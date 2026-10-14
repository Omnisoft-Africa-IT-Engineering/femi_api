"""
Fonction de persistance pour le nouveau flux Router/ACCOUNTING_PROMPT.

Coexiste avec FemiAgentManager._save_operation() (ancien pipeline GBNF,
apps/femi_agent/agent/manager.py) pendant la transition — voir décision
"Option B" prise avec l'utilisateur : on ne touche pas à l'ancien pipeline
tant que le nouveau n'est pas validé et branché.

Emplacement : apps/femi_agent/agent/accounting_manager.py.

Couvre :
  - point 4 de la roadmap : boucle sur transactions[], une Operation par
    transaction (_save_single_transaction).
  - résolution du contact string → instance Contact, avec auto-création
    si aucune correspondance (_resolve_contact).
  - point 4d : quand check_open_debt/check_open_loan est True, imputation
    FIFO du montant à la/les opération(s) ouverte(s) existante(s)
    plutôt que création d'une ligne indépendante
    (_traiter_remboursement).
  - un remboursement n'est JAMAIS enregistré comme une nouvelle recette /
    dépense : si le contact est introuvable, ambigu, ou s'il n'a aucune
    créance / aucun prêt ouvert, rien n'est créé et l'utilisateur reçoit
    une question claire (txn._statut_enregistrement = "remboursement_refuse").

  - rattachement automatique de chaque nouvelle Operation à son échéance
    fiscale correspondante.

Limitation connue sur le remboursement :
get_cashflow par période peut sous-estimer un mois si un remboursement
d'opération ancienne y est imputé — Operation n'a pas de champ de date
par paiement.
"""

import logging
import re
import unicodedata
from decimal import Decimal
from difflib import get_close_matches

from django.db import transaction
from django.db.models import F

from apps.femi_account.models import Contact, Operation
from apps.femi_agent.schemas import (
    AccountingExtractionResult,
    AccountingTransactionSchema,
)
from apps.femi_account.fiscal import (
    creer_ou_mettre_a_jour_echeance_tva,
)

logger = logging.getLogger(__name__)


# LIMITATION CONNUE :
# quand un remboursement est imputé via _traiter_remboursement(),
# seul montant_paye est incrémenté sur l'Operation d'origine.
# Sa transaction_date n'est PAS mise à jour à la date du remboursement.
#
# Operation n'a pas de champ dédié pour l'historique des paiements
# successifs.
#
# Conséquence :
# get_cashflow(periode) peut sous-estimer les entrées d'une période
# si des remboursements de dettes/prêts anciens arrivent pendant
# cette période.
#
# Correction propre = un modèle Paiement séparé
# (date + montant + FK vers l'Operation remboursée).
# Hors scope du point 4.


def _resolve_contact(
    entreprise,
    nom_contact: str | None,
) -> Contact | None:
    """
    Résout un nom de contact (string LLM) en instance Contact.

    - None / chaîne vide → None.
    - Correspondance exacte insensible à la casse → instance existante.
    - Aucune correspondance → création automatique.
    """

    if not nom_contact:
        return None

    contact = Contact.objects.filter(
        entreprise=entreprise,
        nom__iexact=nom_contact,
    ).first()

    if contact is not None:
        return contact

    contact = Contact.objects.create(
        entreprise=entreprise,
        nom=nom_contact,
        type="CLIENT",
    )

    logger.info(
        "[AccountingManager] Contact auto-créé : "
        "nom=%r entreprise=%s contact_id=%s",
        nom_contact,
        entreprise.id,
        contact.id,
    )

    return contact


# ----------------------------------------------------------------------
# REMBOURSEMENT : IMPUTATION SUR LES OPÉRATIONS OUVERTES
# ----------------------------------------------------------------------
#
# Cibles d'un remboursement (même définition de « ouvert » que
# apps.femi_account.soldes_ouverts : amount_ttc > montant_paye) :
#   - check_open_debt=true + txn RECETTE        -> RECETTE   (créance client)
#   - check_open_debt=true + txn DEPENSE        -> DEPENSE   (dette fournisseur)
#   - check_open_loan=true + txn RECETTE        -> PRET_DONNE (prêt qu'on a accordé)
#   - check_open_loan=true + txn DEPENSE        -> PRET_RECU  (prêt qu'on a reçu)
#
# Un remboursement ne crée jamais de nouvelle Operation et jamais de contact.
# Si on ne peut pas l'imputer, on le dit à l'utilisateur (voir
# FemiRouterManager._message_remboursement_refuse) au lieu de compter le
# paiement une deuxième fois.

_LIMITE_CANDIDATS = 5
_SEUIL_NOM_PROCHE = 0.75


def _normaliser_nom(nom):
    """Minuscules, sans accents, espaces simplifiés (« Kôffi  » == « koffi »)."""
    texte = unicodedata.normalize("NFKD", nom or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return " ".join(texte.lower().split())


def _type_operation_cible(txn):
    if txn.check_open_debt:
        return "DEPENSE" if txn.transaction_type == "DEPENSE" else "RECETTE"
    if txn.transaction_type == "DEPENSE":
        return "PRET_RECU"
    return "PRET_DONNE"


def _qs_operations_ouvertes(entreprise, type_cible):
    return Operation.objects.filter(
        entreprise=entreprise,
        transaction_type=type_cible,
        amount_ttc__gt=F("montant_paye"),
    )


def _contact_a_solde_ouvert(entreprise, contact, type_cible):
    return (
        _qs_operations_ouvertes(entreprise, type_cible)
        .filter(contact=contact)
        .exists()
    )


def _noms_avec_solde_ouvert(entreprise, type_cible):
    """Noms (au plus 5) des contacts qui ont encore un solde ouvert."""
    noms = (
        _qs_operations_ouvertes(entreprise, type_cible)
        .filter(contact__isnull=False)
        .order_by("contact__nom")
        .values_list("contact__nom", flat=True)
        .distinct()
    )
    return list(noms[:_LIMITE_CANDIDATS])


def _libelle_contact(contact):
    """Nom + 4 derniers chiffres du téléphone, pour départager des homonymes."""
    telephone = (contact.telephone or "").strip()
    if len(telephone) >= 4:
        return f"{contact.nom} (tél. …{telephone[-4:]})"
    return contact.nom


def _departager(entreprise, candidats, type_cible):
    """(contact, propositions) parmi des contacts de même nom.

    Un seul candidat, ou un seul candidat avec un solde ouvert : on le prend.
    Aucun candidat avec un solde ouvert : (None, []), il n'y a rien à imputer.
    Plusieurs avec un solde : (None, libellés) pour demander lequel.
    """
    if len(candidats) == 1:
        return candidats[0], []
    avec_solde = [
        c for c in candidats
        if _contact_a_solde_ouvert(entreprise, c, type_cible)
    ]
    if len(avec_solde) == 1:
        return avec_solde[0], []
    return None, [_libelle_contact(c) for c in avec_solde[:_LIMITE_CANDIDATS]]


def _resoudre_contact_remboursement(entreprise, nom, type_cible):
    """Retourne (contact, raison, propositions). Ne crée JAMAIS de contact.

    raison : None (trouvé), "sans_nom", "ambigu", "aucune_dette" (homonymes
    sans aucun solde ouvert), "proche" ou "inconnu".
    Seules les correspondances exactes (casse, accents et espaces ignorés)
    sont acceptées automatiquement : un nom simplement ressemblant
    (« Kofi » pour « Koffi ») est proposé, jamais imputé d'office, car
    imputer un paiement sur la mauvaise personne fausse ses comptes.
    """
    nom = (nom or "").strip()
    if not nom:
        return None, "sans_nom", []

    contacts = list(
        Contact.objects.filter(entreprise=entreprise).order_by("nom")
    )
    cible = _normaliser_nom(nom)

    exacts = [c for c in contacts if c.nom.strip().lower() == nom.lower()]
    memes = exacts or [
        c for c in contacts if _normaliser_nom(c.nom) == cible
    ]
    if memes:
        contact, propositions = _departager(entreprise, memes, type_cible)
        if contact is not None:
            return contact, None, []
        if not propositions:
            return None, "aucune_dette", []
        return None, "ambigu", propositions

    mots_cible = set(cible.split())
    noms_normalises = [_normaliser_nom(c.nom) for c in contacts]
    proches_normalises = set(
        get_close_matches(
            cible, noms_normalises, n=3, cutoff=_SEUIL_NOM_PROCHE
        )
    )
    propositions = []
    for contact, normalise in zip(contacts, noms_normalises):
        mots = set(normalise.split())
        ressemble = normalise in proches_normalises or (
            bool(mots_cible)
            and bool(mots)
            and (mots_cible <= mots or mots <= mots_cible)
        )
        if ressemble and contact.nom not in propositions:
            propositions.append(contact.nom)
    if propositions:
        return None, "proche", propositions[:3]

    return None, "inconnu", []


def _imputer_remboursement(entreprise, contact, txn, type_cible):
    """Impute txn.amount_ttc sur les opérations ouvertes du contact (FIFO).

    Doit être appelée dans un bloc transaction.atomic().
    Retourne None s'il n'y a aucune opération ouverte, sinon un dict :
    derniere (Operation), montant_impute, surplus, reste_du, soldees,
    nb_operations.
    """
    operations = list(
        _qs_operations_ouvertes(entreprise, type_cible)
        .filter(contact=contact)
        .select_for_update()
        .order_by("transaction_date", "created_at")
    )
    if not operations:
        return None

    restant = txn.amount_ttc
    montant_impute = Decimal("0")
    soldees = 0
    nb_operations = 0
    derniere = None

    for operation in operations:
        if restant <= 0:
            break

        solde = operation.amount_ttc - operation.montant_paye
        imputation = min(restant, solde)

        operation.montant_paye += imputation
        if operation.montant_paye >= operation.amount_ttc:
            operation.statut_paiement = "PAYE"
            soldees += 1

        operation.save(update_fields=["montant_paye", "statut_paiement"])

        logger.info(
            "[AccountingManager] Imputation : "
            "operation_id=%s montant_impute=%s "
            "nouveau_montant_paye=%s statut=%s",
            operation.id,
            imputation,
            operation.montant_paye,
            operation.statut_paiement,
        )

        restant -= imputation
        montant_impute += imputation
        nb_operations += 1
        derniere = operation

    reste_du = sum(
        (o.amount_ttc - o.montant_paye for o in operations),
        Decimal("0"),
    )

    return {
        "derniere": derniere,
        "montant_impute": montant_impute,
        "surplus": restant if restant > 0 else Decimal("0"),
        "reste_du": reste_du,
        "soldees": soldees,
        "nb_operations": nb_operations,
    }


def _traiter_remboursement(entreprise, txn):
    """Impute un remboursement ou le refuse proprement. Operation | None.

    Succès : retourne la dernière Operation mise à jour et renseigne
    txn._statut_enregistrement = "remboursement".
    Refus : ne crée ni opération ni contact, retourne None et renseigne
    txn._statut_enregistrement = "remboursement_refuse" avec
    detail["raison"] parmi : montant, sans_nom, inconnu, proche, ambigu,
    aucune_dette. Le message utilisateur est écrit par FemiRouterManager.
    """
    type_cible = _type_operation_cible(txn)
    detail = {
        "type_cible": type_cible,
        "currency": txn.currency,
        "montant_recu": txn.amount_ttc,
        "nom_saisi": txn.contact,
    }

    def refuser(raison, **extra):
        detail["raison"] = raison
        detail.update(extra)
        txn._statut_enregistrement = "remboursement_refuse"
        txn._detail_enregistrement = detail
        logger.warning(
            "[AccountingManager] Remboursement refusé (aucune opération "
            "créée) : raison=%s type_cible=%s nom=%r montant=%s",
            raison,
            type_cible,
            txn.contact,
            txn.amount_ttc,
        )
        return None

    if not txn.amount_ttc or txn.amount_ttc <= 0:
        return refuser("montant")

    contact, raison, propositions = _resoudre_contact_remboursement(
        entreprise, txn.contact, type_cible
    )
    if contact is None:
        extra = {"propositions": propositions}
        if raison in ("sans_nom", "inconnu"):
            extra["candidats"] = _noms_avec_solde_ouvert(
                entreprise, type_cible
            )
        return refuser(raison, **extra)

    resultat = _imputer_remboursement(entreprise, contact, txn, type_cible)
    if resultat is None:
        return refuser("aucune_dette", contact=contact.nom)

    detail.update(
        contact=contact.nom,
        montant_impute=resultat["montant_impute"],
        surplus=resultat["surplus"],
        reste_du=resultat["reste_du"],
        soldees=resultat["soldees"],
        nb_operations=resultat["nb_operations"],
    )
    txn._statut_enregistrement = "remboursement"
    txn._detail_enregistrement = detail

    if resultat["surplus"] > 0:
        logger.warning(
            "[AccountingManager] Remboursement supérieur au solde : "
            "surplus=%s non enregistré (contact_id=%s, montant total=%s)",
            resultat["surplus"],
            contact.id,
            txn.amount_ttc,
        )

    return resultat["derniere"]


def _resolve_paiement(txn):
    """Retourne (statut_paiement, montant_paye) pour une nouvelle opération.

    Une avance (montant_deja_paye) est prise en compte pour une opération
    à crédit ; si l'avance couvre déjà le total, l'opération est PAYE.
    """

    total = txn.amount_ttc or 0

    if txn.statut_paiement != "CREDIT":
        return "PAYE", total

    avance = txn.montant_deja_paye or 0

    if avance <= 0:
        return "CREDIT", 0

    if total > 0 and avance >= total:
        return "PAYE", total

    return "CREDIT", avance


# ----------------------------------------------------------------------
# FACTURE DÉJÀ ENREGISTRÉE : RÈGLEMENT ET ANTI-DOUBLON
# ----------------------------------------------------------------------
#
# Operation n'a pas de champ « référence » : le numéro de facture n'existe
# que dans la description (« Facture n°12345 - … »). On le retrouve par
# expression régulière. Deux factures de fournisseurs différents peuvent
# porter le même numéro : si les deux contacts sont connus et différents,
# ce n'est PAS la même facture.

_REFERENCE_FACTURE_RE = re.compile(
    r"(?:facture|invoice|re[cç]u|ticket)\s*"
    r"(?:num[eé]ro|n[°ºo]?\.?|#)?\s*[:#]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-/_.]*)",
    re.IGNORECASE,
)


def extraire_reference_facture(texte):
    """Numéro de facture trouvé dans un texte (en minuscules), ou None.

    Le numéro doit contenir au moins un chiffre ; « facture de mars » ne
    donne donc rien.
    """
    if not texte:
        return None
    for match in _REFERENCE_FACTURE_RE.finditer(texte):
        reference = match.group(1).strip(".-/_")
        if any(c.isdigit() for c in reference):
            return reference.lower()
    return None


def _trouver_operation_par_reference(entreprise, txn, reference):
    candidats = (
        Operation.objects
        .filter(
            entreprise=entreprise,
            transaction_type=txn.transaction_type,
            description__icontains=reference,
        )
        .select_related("contact")
        .order_by("-created_at")[:20]
    )
    for operation in candidats:
        if extraire_reference_facture(operation.description) != reference:
            continue
        if (
            txn.contact
            and operation.contact is not None
            and operation.contact.nom.strip().lower()
            != txn.contact.strip().lower()
        ):
            continue
        return operation
    return None


def _traiter_facture_existante(entreprise, txn):
    """
    Si la transaction concerne une facture DÉJÀ enregistrée (même numéro,
    même type, même contact quand il est connu), évite d'enregistrer une
    deuxième fois la même opération.

    - facture ouverte (reste > 0) et paiement (PAYE)  -> « reglement » :
      le montant est imputé sur l'opération existante ;
    - facture déjà soldée et paiement (PAYE)          -> « deja_soldee » ;
    - facture à crédit, même montant                  -> « doublon » ;
    - sinon (montant différent...) : None, création normale.

    Retourne l'Operation existante (avec txn._statut_enregistrement
    renseigné), ou None si la transaction doit être créée normalement.
    Doit être appelée dans un bloc transaction.atomic().
    """

    if txn.transaction_type not in ("RECETTE", "DEPENSE") or not txn.amount_ttc:
        return None

    reference = extraire_reference_facture(txn.description)
    if not reference:
        return None

    trouvee = _trouver_operation_par_reference(entreprise, txn, reference)
    if trouvee is None:
        return None

    operation = Operation.objects.select_for_update().get(id=trouvee.id)
    reste = operation.amount_ttc - operation.montant_paye

    detail = {
        "reference": reference,
        "date": operation.transaction_date,
        "currency": operation.currency,
        "contact": operation.contact.nom if operation.contact else None,
        "montant_total": operation.amount_ttc,
    }

    if txn.statut_paiement == "PAYE":
        if reste <= 0:
            statut = "deja_soldee"
        else:
            imputation = min(txn.amount_ttc, reste)
            operation.montant_paye += imputation
            if operation.montant_paye >= operation.amount_ttc:
                operation.statut_paiement = "PAYE"
            operation.save(update_fields=["montant_paye", "statut_paiement"])
            detail["montant_impute"] = imputation
            detail["reste"] = operation.amount_ttc - operation.montant_paye
            detail["surplus"] = txn.amount_ttc - imputation
            statut = "reglement"
    elif txn.amount_ttc == operation.amount_ttc:
        statut = "doublon"
    else:
        return None

    txn._statut_enregistrement = statut
    txn._detail_enregistrement = detail
    operation._femi_outcome = statut

    logger.info(
        "[AccountingManager] Facture déjà enregistrée : statut=%s "
        "reference=%s operation_id=%s detail=%s",
        statut,
        reference,
        operation.id,
        detail,
    )

    return operation


def _save_single_transaction(
    entreprise,
    utilisateur,
    txn: AccountingTransactionSchema,
    source,
    raw_text,
) -> Operation | None:
    """
    Persiste UNE transaction.

    Si check_open_debt ou check_open_loan est True, le montant est imputé
    sur les opérations ouvertes du contact (FIFO). Si ce n'est pas possible,
    RIEN n'est créé et None est retourné (txn._statut_enregistrement =
    "remboursement_refuse", raison dans txn._detail_enregistrement).

    Sinon, une nouvelle Operation est créée.

    Après création de l'Operation, celle-ci est automatiquement
    rattachée à l'échéance fiscale correspondant à sa période.
    """

    # ---------------------------------------------------------
    # FACTURE DÉJÀ ENREGISTRÉE (règlement ou doublon)
    # ---------------------------------------------------------
    # Avant _resolve_contact : un doublon ne doit pas auto-créer de contact.

    operation_existante = _traiter_facture_existante(entreprise, txn)
    if operation_existante is not None:
        return operation_existante

    # ---------------------------------------------------------
    # REMBOURSEMENT : imputation sur l'opération ouverte, sinon refus
    # ---------------------------------------------------------
    # Un remboursement ne crée JAMAIS de nouvelle ligne (sinon le chiffre
    # d'affaires ou les dépenses seraient comptés deux fois et la dette
    # d'origine resterait ouverte). Il ne crée pas non plus de contact.

    if txn.check_open_debt or txn.check_open_loan:
        return _traiter_remboursement(entreprise, txn)

    contact = _resolve_contact(
        entreprise,
        txn.contact,
    )

    # ---------------------------------------------------------
    # CRÉATION DE L'OPÉRATION
    # ---------------------------------------------------------

    statut_paiement, montant_paye = _resolve_paiement(txn)

    operation = Operation.objects.create(
        entreprise=entreprise,
        utilisateur=utilisateur,
        transaction_type=txn.transaction_type,

        # Montants comptables
        amount_ttc=txn.amount_ttc,
        amount_ht=txn.amount_ht,
        tax_amount=txn.tax_amount,

        # Informations générales
        currency=txn.currency or "XOF",
        category=txn.category,

        payment_method=(
            txn.payment_method.value
            if txn.payment_method
            else None
        ),

        transaction_date=(
            txn.date_operation
            or timezone_today()
        ),

        description=txn.description,
        contact=contact,
        source=source,

        # Paiement
        statut_paiement=statut_paiement,
        montant_paye=montant_paye,
    )

    # ---------------------------------------------------------
    # ÉCHÉANCE FISCALE
    # ---------------------------------------------------------
    #
    # IMPORTANT :
    # On ne dépend plus de tax_amount.
    #
    # L'Operation est systématiquement rattachée à l'échéance
    # fiscale correspondant à sa période.
    #
    # Les montants fiscaux peuvent donc être NULL pour le moment.
    # ---------------------------------------------------------

    try:
        echeance = creer_ou_mettre_a_jour_echeance_tva(
            operation
        )

        logger.info(
            "[AccountingManager] Échéance fiscale liée : "
            "operation_id=%s echeance_id=%s",
            operation.id,
            echeance.id if echeance else None,
        )

    except Exception:
        logger.exception(
            "[AccountingManager] Impossible de créer/rattacher "
            "l'échéance fiscale pour operation_id=%s",
            operation.id,
        )

        # On laisse remonter l'erreur pour que le transaction.atomic()
        # puisse annuler l'enregistrement si nécessaire.
        raise

    # ---------------------------------------------------------
    # LOG
    # ---------------------------------------------------------

    logger.info(
        "[AccountingManager] Operation créée : "
        "id=%s type=%s TTC=%s HT=%s TVA=%s contact=%s",
        operation.id,
        operation.transaction_type,
        operation.amount_ttc,
        operation.amount_ht,
        operation.tax_amount,
        contact.nom if contact else None,
    )

    return operation


def save_accounting_transactions(
    entreprise,
    utilisateur,
    result: AccountingExtractionResult,
    source,
    raw_text,
) -> list[Operation]:
    """
    Point d'entrée principal.

    Boucle sur result.transactions et persiste chaque transaction
    comme une Operation distincte, dans une transaction DB atomique
    globale.

    Une Operation créée est automatiquement rattachée à son
    échéance fiscale.
    """

    if not result.transactions:
        raise ValueError(
            "Aucune transaction à sauvegarder "
            "(result.transactions est vide)."
        )

    operations = []

    with transaction.atomic():

        for txn in result.transactions:

            operation = _save_single_transaction(
                entreprise,
                utilisateur,
                txn,
                source,
                raw_text,
            )

            # None = remboursement refusé (rien à créer) : le message à
            # l'utilisateur vient de txn._statut_enregistrement.
            if operation is not None:
                operations.append(operation)

    return operations


def timezone_today():
    """
    Import tardif pour éviter une dépendance circulaire éventuelle
    avec django.utils.timezone au chargement du module.
    """

    from django.utils import timezone

    return timezone.now().date()