"""
Source unique des soldes ouverts (créances clients, dettes fournisseurs).

Utilisé par :
- kpi_service (totaux dashboard)
- apps.femi_agent.agent.tools.debts (listes chat WhatsApp)

Règle unique (SYSCOHADA / état actuel de Operation.montant_paye) :
    solde = amount_ttc - montant_paye
    ouvert ⇔ solde > 0  (équivalent SQL : amount_ttc > montant_paye)

Ne pas recalculer ces totaux ailleurs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from django.db.models import F, QuerySet, Sum

from apps.femi_account.models import Operation


def _f(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def qs_creances_ouvertes(
    entreprise,
    jusqua: Optional[date] = None,
) -> QuerySet:
    """RECETTE encore dues (clients qui doivent de l'argent)."""
    qs = Operation.objects.filter(
        entreprise=entreprise,
        transaction_type="RECETTE",
        amount_ttc__gt=F("montant_paye"),
    ).select_related("contact")
    if jusqua is not None:
        qs = qs.filter(transaction_date__lte=jusqua)
    return qs.order_by("contact__nom", "transaction_date")


def qs_dettes_fournisseurs_ouvertes(
    entreprise,
    jusqua: Optional[date] = None,
) -> QuerySet:
    """DEPENSE encore dues (fournisseurs / créanciers à payer)."""
    qs = Operation.objects.filter(
        entreprise=entreprise,
        transaction_type="DEPENSE",
        amount_ttc__gt=F("montant_paye"),
    ).select_related("contact")
    if jusqua is not None:
        qs = qs.filter(transaction_date__lte=jusqua)
    return qs.order_by("contact__nom", "transaction_date")


def total_creances_ouvertes(
    entreprise,
    jusqua: Optional[date] = None,
) -> float:
    """Total créances clients — même chiffre que la KPI « Créances »."""
    agg = qs_creances_ouvertes(entreprise, jusqua).aggregate(
        total=Sum(F("amount_ttc") - F("montant_paye"))
    )
    return round(_f(agg["total"]), 2)


def total_dettes_fournisseurs_ouvertes(
    entreprise,
    jusqua: Optional[date] = None,
) -> float:
    """Total dettes fournisseurs — même chiffre que la KPI dettes fournisseurs."""
    agg = qs_dettes_fournisseurs_ouvertes(entreprise, jusqua).aggregate(
        total=Sum(F("amount_ttc") - F("montant_paye"))
    )
    return round(_f(agg["total"]), 2)


def _serialize_op(operation: Operation) -> dict[str, Any]:
    solde = _f(operation.amount_ttc) - _f(operation.montant_paye)
    return {
        "operation_id": str(operation.id),
        "transaction_type": operation.transaction_type,
        "date": operation.transaction_date.isoformat(),
        "amount_ttc": _f(operation.amount_ttc),
        "montant_paye": _f(operation.montant_paye),
        "solde_restant": solde,
        "description": operation.description or "",
    }


def lister_creances_par_contact(
    entreprise,
    jusqua: Optional[date] = None,
    devise: str = "FCFA",
) -> dict[str, Any]:
    """
    Détail des créances groupées par contact + resume_texte WhatsApp.
    total_general == total_creances_ouvertes(...).
    """
    par_contact: dict = {}
    sans_contact: dict = {"operations": [], "total_du": 0.0}

    for op in qs_creances_ouvertes(entreprise, jusqua):
        row = _serialize_op(op)
        if row["solde_restant"] <= 0:
            continue
        if op.contact_id is None:
            row["client_ou_fournisseur"] = getattr(op, "vendor_or_client", None)
            cible = sans_contact
        else:
            cible = par_contact.setdefault(
                op.contact_id,
                {
                    "contact": op.contact.nom,
                    "contact_id": str(op.contact_id),
                    "operations": [],
                    "total_du": 0.0,
                },
            )
        cible["operations"].append(row)
        cible["total_du"] += row["solde_restant"]

    contacts = sorted(par_contact.values(), key=lambda e: e["total_du"], reverse=True)
    total_general = round(
        sum((c["total_du"] for c in contacts), 0.0) + sans_contact["total_du"],
        2,
    )

    if total_general <= 0:
        resume = (
            "Aucune créance client ouverte pour le moment. "
            "Personne ne te doit d'argent."
        )
    else:
        lignes = [f"• {c['contact']} — {c['total_du']:,.0f} {devise}" for c in contacts]
        if sans_contact["total_du"] > 0:
            n_ops = len(sans_contact["operations"])
            lignes.append(
                f"• Sans client enregistré ({n_ops} op.) — "
                f"{sans_contact['total_du']:,.0f} {devise}"
            )
        resume = (
            f"Créances clients ouvertes : {total_general:,.0f} {devise} au total.\n"
            + "\n".join(lignes)
        )

    return {
        "success": True,
        "contacts": contacts,
        "sans_contact": sans_contact,
        "total_general": total_general,
        "has_open_debt": total_general > 0,
        "resume_texte": resume,
    }


def lister_dettes_fournisseurs_par_contact(
    entreprise,
    jusqua: Optional[date] = None,
    devise: str = "FCFA",
) -> dict[str, Any]:
    """Détail des dettes fournisseurs groupées + resume_texte."""
    par_contact: dict = {}
    sans_contact: dict = {"operations": [], "total_a_payer": 0.0}

    for op in qs_dettes_fournisseurs_ouvertes(entreprise, jusqua):
        row = _serialize_op(op)
        if row["solde_restant"] <= 0:
            continue
        if op.contact_id is None:
            cible = sans_contact
        else:
            cible = par_contact.setdefault(
                op.contact_id,
                {
                    "contact": op.contact.nom,
                    "contact_id": str(op.contact_id),
                    "operations": [],
                    "total_a_payer": 0.0,
                },
            )
        cible["operations"].append(row)
        cible["total_a_payer"] += row["solde_restant"]

    contacts = sorted(
        par_contact.values(), key=lambda e: e["total_a_payer"], reverse=True
    )
    total_general = round(
        sum((c["total_a_payer"] for c in contacts), 0.0)
        + sans_contact["total_a_payer"],
        2,
    )

    if total_general <= 0:
        resume = "Aucune dette fournisseur ouverte. Tu ne dois rien pour le moment."
    else:
        lignes = [
            f"• {c['contact']} — {c['total_a_payer']:,.0f} {devise}" for c in contacts
        ]
        if sans_contact["total_a_payer"] > 0:
            n_ops = len(sans_contact["operations"])
            lignes.append(
                f"• Sans fournisseur enregistré ({n_ops} op.) — "
                f"{sans_contact['total_a_payer']:,.0f} {devise}"
            )
        resume = (
            f"Dettes fournisseurs (créanciers) : {total_general:,.0f} {devise} au total.\n"
            + "\n".join(lignes)
        )

    return {
        "success": True,
        "contacts": contacts,
        "sans_contact": sans_contact,
        "total_general": total_general,
        "has_open_payable": total_general > 0,
        "resume_texte": resume,
    }
