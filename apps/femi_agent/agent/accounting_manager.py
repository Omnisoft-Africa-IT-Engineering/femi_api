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
    (_impute_to_open_operation).

  - rattachement automatique de chaque nouvelle Operation à son échéance
    fiscale correspondante.

Limitation connue sur le remboursement :
get_cashflow par période peut sous-estimer un mois si un remboursement
d'opération ancienne y est imputé — Operation n'a pas de champ de date
par paiement.
"""

import logging
import re

from django.db import transaction

from apps.femi_account.models import Contact, Operation
from apps.femi_agent.schemas import (
    AccountingExtractionResult,
    AccountingTransactionSchema,
)
from apps.femi_agent.agent.tools import (
    get_contact_open_debts,
    get_contact_open_loans,
)
from apps.femi_account.fiscal import (
    creer_ou_mettre_a_jour_echeance_tva,
)

logger = logging.getLogger(__name__)


# LIMITATION CONNUE :
# quand un remboursement est imputé via _impute_to_open_operation(),
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


def _impute_to_open_operation(
    entreprise,
    contact,
    txn: AccountingTransactionSchema,
) -> Operation | None:
    """
    Tente d'imputer txn à l'opération ouverte la plus ancienne (FIFO).

    Doit être appelée à l'intérieur d'un bloc transaction.atomic().
    """

    if txn.check_open_debt:
        resultat = get_contact_open_debts(
            entreprise,
            contact,
        )
    else:
        resultat = get_contact_open_loans(
            entreprise,
            contact,
        )

    operations_ouvertes = resultat["operations"]

    if not operations_ouvertes:
        return None

    montant_restant = txn.amount_ttc
    derniere_operation_maj = None

    for op_info in operations_ouvertes:

        if montant_restant <= 0:
            break

        operation = (
            Operation.objects
            .select_for_update()
            .get(id=op_info["operation_id"])
        )

        solde = (
            operation.amount_ttc
            - operation.montant_paye
        )

        imputation = min(
            montant_restant,
            solde,
        )

        operation.montant_paye += imputation

        if operation.montant_paye >= operation.amount_ttc:
            operation.statut_paiement = "PAYE"

        operation.save(
            update_fields=[
                "montant_paye",
                "statut_paiement",
            ]
        )

        logger.info(
            "[AccountingManager] Imputation : "
            "operation_id=%s montant_impute=%s "
            "nouveau_montant_paye=%s statut=%s",
            operation.id,
            imputation,
            operation.montant_paye,
            operation.statut_paiement,
        )

        montant_restant -= imputation
        derniere_operation_maj = operation

    if montant_restant > 0:
        logger.warning(
            "[AccountingManager] Remboursement partiellement imputé : "
            "reliquat=%s non couvert par les opérations ouvertes "
            "de contact_id=%s (montant total=%s)",
            montant_restant,
            contact.id,
            txn.amount_ttc,
        )

    return derniere_operation_maj


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
) -> Operation:
    """
    Persiste UNE transaction.

    Si check_open_debt ou check_open_loan est True ET qu'une opération
    ouverte existe pour ce contact, le montant est imputé à cette
    opération plutôt que de créer une nouvelle ligne.

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

    contact = _resolve_contact(
        entreprise,
        txn.contact,
    )

    # ---------------------------------------------------------
    # REMBOURSEMENT / IMPUTATION D'UNE OPÉRATION EXISTANTE
    # ---------------------------------------------------------

    if (
        txn.check_open_debt
        or txn.check_open_loan
    ) and contact is not None:

        operation_imputee = _impute_to_open_operation(
            entreprise,
            contact,
            txn,
        )

        if operation_imputee is not None:
            return operation_imputee

    # ---------------------------------------------------------
    # CRÉATION DE L'OPÉRATION
    # ---------------------------------------------------------

    statut_paiement, montant_paye = _resolve_paiement(txn)

    operation = Operation.objects.create(
        entreprise=entreprise,
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

            operations.append(operation)

    return operations


def timezone_today():
    """
    Import tardif pour éviter une dépendance circulaire éventuelle
    avec django.utils.timezone au chargement du module.
    """

    from django.utils import timezone

    return timezone.now().date()