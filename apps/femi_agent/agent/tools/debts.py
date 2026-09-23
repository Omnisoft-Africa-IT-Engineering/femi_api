"""
Tools de gestion des créances (dettes clients) et des prêts en cours.

Convention de solde, reprise de _calc_prets_accordes / _calc_dettes
(apps/femi_api/views.py) :

    - Une opération est "ouverte" tant que statut_paiement == "CREDIT".
    - Le solde restant dû d'une opération = amount_ttc - montant_paye.
    - Il n'existe PAS de ligne Operation séparée par remboursement :
      c'est le champ montant_paye de la ligne d'origine qui s'incrémente
      côté backend (hors scope de ce fichier).

Import confirmé : Operation et Contact vivent dans apps.femi_account.models
(voir apps/femi_account/models.py, lignes 69 et 230).

HYPOTHÈSE ASSUMÉE : une seule devise (XOF) par entreprise en pratique.
Les totaux (total_du, total_restant, total_general) additionnent les
montants sans regrouper par devise. Si le multi-devises devient réel un
jour, ces totaux doivent être recalculés par devise avant d'être sommés.
"""

from apps.femi_account.models import Operation, Contact  # noqa: F401  (Entreprise non utilisé directement ici)


def _solde_ouvert(operation):
    """Solde restant dû sur une opération à crédit (toujours >= 0 en théorie)."""
    return operation.amount_ttc - operation.montant_paye


def _serialize_operation_ouverte(operation):
    return {
        "operation_id": str(operation.id),
        "transaction_type": operation.transaction_type,
        "date": operation.transaction_date.isoformat(),
        "amount_ttc": float(operation.amount_ttc),
        "montant_paye": float(operation.montant_paye),
        "solde_restant": float(_solde_ouvert(operation)),
        "description": operation.description,
    }


def get_contact_open_debts(entreprise, contact):
    """
    Créances clients ouvertes pour un contact donné (ventes à crédit non soldées).

    Une "dette" ici = de l'argent que le CONTACT doit à l'ENTREPRISE.
    → Operation.transaction_type == "RECETTE" ET statut_paiement == "CREDIT".

    Args:
        entreprise: instance Entreprise.
        contact: instance Contact (ou None — retourne alors une liste vide).

    Returns:
        {
            "contact": "<nom>" | None,
            "operations": [ {operation_id, transaction_type, date,
                              amount_ttc, montant_paye, solde_restant,
                              description}, ... ],   # triées par date croissante (FIFO)
            "total_du": float,
            "has_open_debt": bool,
        }
    """
    if contact is None:
        return {"contact": None, "operations": [], "total_du": 0.0, "has_open_debt": False}

    qs = (
        Operation.objects.filter(
            entreprise=entreprise,
            contact=contact,
            transaction_type="RECETTE",
            statut_paiement="CREDIT",
        )
        .order_by("transaction_date")
    )

    operations = [_serialize_operation_ouverte(op) for op in qs]
    total_du = sum((op["solde_restant"] for op in operations), 0.0)

    return {
        "contact": contact.nom,
        "operations": operations,
        "total_du": total_du,
        "has_open_debt": len(operations) > 0,
    }


def get_contact_open_loans(entreprise, contact):
    """
    Prêts donnés (PRET_DONNE) encore en cours pour un contact donné.

    Utilisé quand check_open_loan=true côté ACCOUNTING_PROMPT, c-à-d quand
    un message décrit un remboursement reçu ("Koffi m'a remboursé...").

    Args:
        entreprise: instance Entreprise.
        contact: instance Contact (ou None — retourne alors une liste vide).

    Returns:
        {
            "contact": "<nom>" | None,
            "operations": [ {...}, ... ],   # triées par date croissante (FIFO)
            "total_restant": float,
            "has_open_loan": bool,
        }
    """
    if contact is None:
        return {"contact": None, "operations": [], "total_restant": 0.0, "has_open_loan": False}

    qs = (
        Operation.objects.filter(
            entreprise=entreprise,
            contact=contact,
            transaction_type="PRET_DONNE",
            statut_paiement="CREDIT",
        )
        .order_by("transaction_date")
    )

    operations = [_serialize_operation_ouverte(op) for op in qs]
    total_restant = sum((op["solde_restant"] for op in operations), 0.0)

    return {
        "contact": contact.nom,
        "operations": operations,
        "total_restant": total_restant,
        "has_open_loan": len(operations) > 0,
    }


def get_all_open_debts(entreprise):
    """
    Vue d'ensemble des créances clients ouvertes, groupées par contact
    (rapport de type "aging report").

    Ne couvre QUE les créances clients (RECETTE/CREDIT), pas les prêts
    donnés — utiliser get_contact_open_loans / une variante dédiée pour
    une vue globale des prêts si besoin plus tard.

    Args:
        entreprise: instance Entreprise.

    Returns:
        {
            "contacts": [
                {
                    "contact": "<nom>",
                    "contact_id": "<uuid>",
                    "operations": [ {...}, ... ],
                    "total_du": float,
                },
                ...
            ],   # triés par total_du décroissant (plus gros débiteurs en premier)
            "total_general": float,
        }
    """
    qs = (
        Operation.objects.filter(
            entreprise=entreprise,
            transaction_type="RECETTE",
            statut_paiement="CREDIT",
            contact__isnull=False,
        )
        .select_related("contact")
        .order_by("contact__nom", "transaction_date")
    )

    par_contact = {}
    for op in qs:
        entry = par_contact.setdefault(
            op.contact_id,
            {"contact": op.contact.nom, "contact_id": str(op.contact_id), "operations": [], "total_du": 0.0},
        )
        serialized = _serialize_operation_ouverte(op)
        entry["operations"].append(serialized)
        entry["total_du"] += serialized["solde_restant"]

    contacts = sorted(par_contact.values(), key=lambda e: e["total_du"], reverse=True)
    total_general = sum((c["total_du"] for c in contacts), 0.0)

    return {"contacts": contacts, "total_general": total_general}