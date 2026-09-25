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
  - point 4d (initialement différé, traité dans cette session à la
    demande de l'utilisateur) : quand check_open_debt/check_open_loan
    est True, imputation FIFO du montant à la/les opération(s) ouverte(s)
    existante(s) plutôt que création d'une ligne indépendante
    (_impute_to_open_operation).

Limitation connue sur ce dernier point : voir bloc de commentaire plus bas
(get_cashflow par période peut sous-estimer un mois si un remboursement
d'opération ancienne y est imputé — Operation n'a pas de champ de date par
paiement).
"""

import logging

from django.db import transaction

from apps.femi_account.models import Contact, Operation
from apps.femi_agent.schemas import AccountingExtractionResult, AccountingTransactionSchema
from apps.femi_agent.agent.tools import get_contact_open_debts, get_contact_open_loans
from apps.femi_account.fiscal import creer_ou_mettre_a_jour_echeance_tva

logger = logging.getLogger(__name__)

# LIMITATION CONNUE (schéma existant, pas introduite ici) : quand un
# remboursement est imputé via _impute_to_open_operation(), seul
# montant_paye est incrémenté sur l'Operation d'origine — sa transaction_date
# n'est PAS mise à jour à la date du remboursement (Operation n'a pas de
# champ dédié pour l'historique des paiements successifs). Conséquence :
# get_cashflow(periode) peut sous-estimer les entrées d'une période si des
# remboursements de dettes/prêts anciens arrivent pendant cette période — le
# montant reste rattaché à la période de l'opération d'origine.
# calculate_balance() n'est pas affecté (pas de notion de période).
# Correction propre = un modèle Paiement séparé (date + montant + FK vers
# l'Operation remboursée) — hors scope du point 4, à évaluer séparément.


def _resolve_contact(entreprise, nom_contact: str | None) -> Contact | None:
    """
    Résout un nom de contact (string LLM) en instance Contact.

    - None / chaîne vide → None (transaction sans contrepartie identifiée,
      cas légitime pour une RECETTE/DEPENSE générique).
    - Correspondance exacte insensible à la casse trouvée → cette instance.
    - Aucune correspondance → création automatique (type=CLIENT par défaut),
      avec log pour traçabilité/nettoyage manuel futur.

    Politique validée avec l'utilisateur (voir conversation) : auto-création
    plutôt que blocage, en attendant la recherche floue {contacts_correspondants}
    prévue au point 6 de la roadmap (pas encore construite).
    """
    if not nom_contact:
        return None

    contact = Contact.objects.filter(entreprise=entreprise, nom__iexact=nom_contact).first()
    if contact is not None:
        return contact

    contact = Contact.objects.create(entreprise=entreprise, nom=nom_contact, type="CLIENT")
    logger.info(
        "[AccountingManager] Contact auto-créé : nom=%r entreprise=%s contact_id=%s",
        nom_contact, entreprise.id, contact.id,
    )
    return contact


def _impute_to_open_operation(entreprise, contact, txn: AccountingTransactionSchema) -> Operation | None:
    """
    ⚠️ Doit être appelée à l'intérieur d'un bloc transaction.atomic() — utilise
    select_for_update(), qui lève une TransactionManagementError hors
    transaction. Actuellement garanti par save_accounting_transactions(),
    le seul point d'entrée prévu ; ne pas appeler cette fonction (ni
    _save_single_transaction) directement en dehors de ce contexte.

    Tente d'imputer txn (un remboursement) à l'opération ouverte la plus
    ancienne (FIFO) pour ce contact — créance client si check_open_debt,
    prêt donné si check_open_loan. Incrémente montant_paye sur CETTE
    opération d'origine au lieu de créer une nouvelle ligne.

    Si le montant du remboursement dépasse le solde restant de l'opération
    la plus ancienne, le surplus est imputé à l'opération ouverte suivante
    (toujours FIFO), et ainsi de suite jusqu'à épuisement du montant ou des
    opérations ouvertes. Un reliquat non imputé (aucune opération ouverte ne
    couvre tout) reste dans amount_restant : voir docstring de
    save_accounting_transactions pour ce que l'appelant en fait.

    Returns:
        La DERNIÈRE Operation d'origine mise à jour (pour rattacher un
        éventuel message de confirmation), ou None si aucune opération
        ouverte n'a été trouvée pour ce contact (rien à imputer).
    """
    if txn.check_open_debt:
        resultat = get_contact_open_debts(entreprise, contact)
    else:  # check_open_loan
        resultat = get_contact_open_loans(entreprise, contact)

    operations_ouvertes = resultat["operations"]  # déjà triées par date croissante (FIFO), voir debts.py
    if not operations_ouvertes:
        return None

    montant_restant = txn.amount_ttc
    derniere_operation_maj = None

    for op_info in operations_ouvertes:
        if montant_restant <= 0:
            break

        operation = Operation.objects.select_for_update().get(id=op_info["operation_id"])
        solde = operation.amount_ttc - operation.montant_paye
        imputation = min(montant_restant, solde)

        operation.montant_paye += imputation
        if operation.montant_paye >= operation.amount_ttc:
            operation.statut_paiement = "PAYE"
        operation.save(update_fields=["montant_paye", "statut_paiement"])

        logger.info(
            "[AccountingManager] Imputation : operation_id=%s montant_impute=%s "
            "nouveau_montant_paye=%s statut=%s",
            operation.id, imputation, operation.montant_paye, operation.statut_paiement,
        )

        montant_restant -= imputation
        derniere_operation_maj = operation

    if montant_restant > 0:
        logger.warning(
            "[AccountingManager] Remboursement partiellement imputé : reliquat=%s "
            "non couvert par les opérations ouvertes de contact_id=%s (montant total=%s)",
            montant_restant, contact.id, txn.amount_ttc,
        )

    return derniere_operation_maj


def _save_single_transaction(entreprise, utilisateur, txn: AccountingTransactionSchema, source, raw_text) -> Operation:
    """
    Persiste UNE transaction.

    Si check_open_debt ou check_open_loan est True ET qu'une opération
    ouverte existe pour ce contact, le montant est imputé à cette opération
    (montant_paye incrémenté) plutôt que d'enregistrer une nouvelle ligne
    indépendante — voir _impute_to_open_operation().

    Si check_open_debt/check_open_loan est True mais qu'AUCUNE opération
    ouverte n'existe, on retombe sur le comportement par défaut : créer une
    opération standalone.
    """
    contact = _resolve_contact(entreprise, txn.contact)

    if (txn.check_open_debt or txn.check_open_loan) and contact is not None:
        operation_imputee = _impute_to_open_operation(entreprise, contact, txn)
        if operation_imputee is not None:
            return operation_imputee

    operation = Operation.objects.create(
        entreprise=entreprise,
        transaction_type=txn.transaction_type,
        amount_ttc=txn.amount_ttc,
        amount_ht=getattr(txn, "amount_ht", None),
        tax_amount=getattr(txn, "tax_amount", None),
        currency=txn.currency or "XOF",
        category=txn.category,
        payment_method=txn.payment_method.value if txn.payment_method else None,
        transaction_date=txn.date_operation or timezone_today(),
        description=txn.description,
        contact=contact,
        source=source,
        statut_paiement=txn.statut_paiement,
        montant_paye=0 if txn.statut_paiement == "CREDIT" else txn.amount_ttc,
    )

    if getattr(operation, "tax_amount", None) is not None and operation.tax_amount > 0:
        creer_ou_mettre_a_jour_echeance_tva(operation)

    logger.info(
        "[AccountingManager] Operation créée : id=%s type=%s montant=%s contact=%s",
        operation.id,
        operation.transaction_type,
        operation.amount_ttc,
        contact.nom if contact else None,
    )
    return operation
    

def save_accounting_transactions(entreprise, utilisateur, result: AccountingExtractionResult, source, raw_text) -> list[Operation]:
    """
    Point d'entrée principal : boucle sur result.transactions et persiste
    chaque transaction comme une Operation distincte, dans une transaction
    DB atomique globale (soit toutes les lignes sont créées, soit aucune —
    évite un enregistrement partiel si une ligne échoue au milieu).

    Args:
        entreprise: instance Entreprise.
        utilisateur: instance Utilisateur (ou None).
        result: AccountingExtractionResult déjà validé par Pydantic
                (needs_clarification doit être vérifié par l'appelant AVANT
                d'appeler cette fonction — elle ne le revérifie pas elle-même).
        source: origine du message (ex: "WHATSAPP").
        raw_text: texte brut d'origine, pour traçabilité.

    Returns:
        list[Operation] — une entrée par transaction de result.transactions,
        dans le même ordre.

    Raises:
        ValueError si result.transactions est vide (rien à sauvegarder —
        l'appelant ne devrait normalement jamais arriver ici dans ce cas).
    """
    if not result.transactions:
        raise ValueError("Aucune transaction à sauvegarder (result.transactions est vide).")

    operations = []
    with transaction.atomic():
        for txn in result.transactions:
            operation = _save_single_transaction(entreprise, utilisateur, txn, source, raw_text)
            operations.append(operation)

    return operations


def timezone_today():
    """Import tardif pour éviter une dépendance circulaire éventuelle avec django.utils.timezone au chargement du module."""
    from django.utils import timezone
    return timezone.now().date()