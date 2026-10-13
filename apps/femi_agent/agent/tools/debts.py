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

from django.db.models import F

from apps.femi_account.models import Operation, Contact  # noqa: F401
from apps.femi_account.soldes_ouverts import (
    lister_creances_par_contact,
    lister_dettes_fournisseurs_par_contact,
    qs_creances_ouvertes,
)


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
    Créances clients ouvertes pour un contact donné (ventes non soldées).

    Aligné sur la KPI « Créances » (kpi_service) :
    RECETTE avec amount_ttc > montant_paye (solde restant > 0).
    On ne filtre plus uniquement sur statut_paiement=CREDIT, car des
    opérations partiellement payées ou mal taguées apparaissaient sur le
    dashboard mais pas dans le chat.
    """
    if contact is None:
        return {
            "success": True,
            "contact": None,
            "operations": [],
            "total_du": 0.0,
            "has_open_debt": False,
            "resume_texte": "Aucun contact précisé.",
        }

    qs = qs_creances_ouvertes(entreprise).filter(contact=contact)

    operations = []
    total_du = 0.0
    for op in qs:
        serialized = _serialize_operation_ouverte(op)
        if serialized["solde_restant"] <= 0:
            continue
        operations.append(serialized)
        total_du += serialized["solde_restant"]

    if not operations:
        resume = f"{contact.nom} ne te doit rien pour le moment (aucune créance ouverte)."
    else:
        lignes = [
            f"- {o['date']}: {o['solde_restant']:,.0f} FCFA restants"
            f"{(' (' + o['description'] + ')') if o.get('description') else ''}"
            for o in operations
        ]
        resume = (
            f"{contact.nom} te doit {total_du:,.0f} FCFA au total :\n"
            + "\n".join(lignes)
        )

    return {
        "success": True,
        "contact": contact.nom,
        "operations": operations,
        "total_du": total_du,
        "has_open_debt": total_du > 0,
        "resume_texte": resume,
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
    Vue d'ensemble des créances clients ouvertes, groupées par contact.

    Délègue à apps.femi_account.soldes_ouverts (même source que la KPI
    « Créances »). total_general == kpi.calc_creances(...).
    """
    devise = getattr(entreprise, "devise", None) or "FCFA"
    devise = {"XOF": "FCFA", "XAF": "FCFA"}.get(str(devise).upper(), devise)
    return lister_creances_par_contact(entreprise, devise=devise)



def get_all_open_payables(entreprise):
    """
    Dettes fournisseurs ouvertes, groupées par contact.

    Même source que kpi.calc_dettes_fournisseurs.
    """
    devise = getattr(entreprise, "devise", None) or "FCFA"
    devise = {"XOF": "FCFA", "XAF": "FCFA"}.get(str(devise).upper(), devise)
    return lister_dettes_fournisseurs_par_contact(entreprise, devise=devise)



def get_all_open_loans(entreprise):
    """
    Vue d'ensemble des prêts encore ouverts :
    - prêts donnés (PRET_DONNE) : argent que l'entreprise a prêté, encore dû
    - prêts reçus (PRET_RECU) : argent emprunté par l'entreprise, encore à rembourser

    Returns:
        {
            "success": True,
            "prets_donnes": {
                "contacts": [{"contact", "contact_id", "operations", "total_restant"}, ...],
                "sans_contact": {"operations", "total_restant"},
                "total_restant": float,
            },
            "prets_recus": {
                "contacts": [...],
                "sans_contact": {...},
                "total_restant": float,
            },
            "total_a_recevoir": float,  # somme prêts donnés restants
            "total_a_rembourser": float,  # somme prêts reçus restants
            "has_open_loan": bool,
        }
    """

    def _group(transaction_type: str) -> dict:
        qs = (
            Operation.objects.filter(
                entreprise=entreprise,
                transaction_type=transaction_type,
                amount_ttc__gt=F("montant_paye"),
            )
            .select_related("contact")
            .order_by("contact__nom", "transaction_date")
        )
        par_contact = {}
        sans_contact = {"operations": [], "total_restant": 0.0}
        for op in qs:
            serialized = _serialize_operation_ouverte(op)
            if serialized["solde_restant"] <= 0:
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
                        "total_restant": 0.0,
                    },
                )
            cible["operations"].append(serialized)
            cible["total_restant"] += serialized["solde_restant"]
        contacts = sorted(
            par_contact.values(), key=lambda e: e["total_restant"], reverse=True
        )
        total = sum((c["total_restant"] for c in contacts), 0.0) + sans_contact[
            "total_restant"
        ]
        return {
            "contacts": contacts,
            "sans_contact": sans_contact,
            "total_restant": total,
        }

    prets_donnes = _group("PRET_DONNE")
    prets_recus = _group("PRET_RECU")
    total_a_recevoir = prets_donnes["total_restant"]
    total_a_rembourser = prets_recus["total_restant"]

    if not (total_a_recevoir or total_a_rembourser):
        resume = "Aucun prêt ouvert pour le moment (ni donné, ni reçu)."
    else:
        lignes = []
        if total_a_recevoir > 0:
            lignes.append(f"Prêts donnés (à te rembourser) : {total_a_recevoir:,.0f} FCFA")
            for c in prets_donnes.get("contacts") or []:
                lignes.append(f"  • {c['contact']} — {c['total_restant']:,.0f} FCFA")
        if total_a_rembourser > 0:
            lignes.append(f"Prêts reçus (à rembourser) : {total_a_rembourser:,.0f} FCFA")
            for c in prets_recus.get("contacts") or []:
                lignes.append(f"  • {c['contact']} — {c['total_restant']:,.0f} FCFA")
        resume = "\n".join(lignes)

    return {
        "success": True,
        "prets_donnes": prets_donnes,
        "prets_recus": prets_recus,
        "total_a_recevoir": total_a_recevoir,
        "total_a_rembourser": total_a_rembourser,
        "has_open_loan": (total_a_recevoir + total_a_rembourser) > 0,
        "resume_texte": resume,
    }


def get_impayes(entreprise, jours_retard: int = 30):
    """
    Impayés clients = créances ouvertes (RECETTE non soldées), avec aging.

    Args:
        entreprise: instance Entreprise.
        jours_retard: seuil en jours depuis transaction_date pour marquer
            « en retard » (défaut 30). Les opérations plus récentes restent
            des créances ouvertes mais pas encore « en retard ».

    Returns:
        {
            "success": True,
            "seuil_jours": int,
            "contacts": [
                {
                    "contact": str,
                    "contact_id": str,
                    "operations": [
                        {
                            ...,
                            "jours": int,
                            "en_retard": bool,
                            "tranche": "0-30"|"31-60"|"61-90"|"90+",
                        }
                    ],
                    "total_du": float,
                    "total_en_retard": float,
                },
                ...
            ],
            "sans_contact": {...},
            "total_general": float,
            "total_en_retard": float,
            "repartition": {"0-30": float, "31-60": float, "61-90": float, "90+": float},
            "has_impayes": bool,
            "has_retard": bool,
        }
    """
    from datetime import date

    today = date.today()
    try:
        seuil = max(0, int(jours_retard))
    except (TypeError, ValueError):
        seuil = 30

    def _tranche(jours: int) -> str:
        if jours <= 30:
            return "0-30"
        if jours <= 60:
            return "31-60"
        if jours <= 90:
            return "61-90"
        return "90+"

    qs = (
        Operation.objects.filter(
            entreprise=entreprise,
            transaction_type="RECETTE",
            amount_ttc__gt=F("montant_paye"),
        )
        .select_related("contact")
        .order_by("transaction_date")
    )

    par_contact = {}
    sans_contact = {
        "operations": [],
        "total_du": 0.0,
        "total_en_retard": 0.0,
    }
    repartition = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    total_general = 0.0
    total_en_retard = 0.0

    for op in qs:
        serialized = _serialize_operation_ouverte(op)
        solde = serialized["solde_restant"]
        if solde <= 0:
            continue
        jours = (today - op.transaction_date).days
        if jours < 0:
            jours = 0
        tranche = _tranche(jours)
        en_retard = jours >= seuil
        serialized["jours"] = jours
        serialized["en_retard"] = en_retard
        serialized["tranche"] = tranche
        if op.contact_id is None:
            serialized["client_ou_fournisseur"] = op.vendor_or_client
            cible = sans_contact
        else:
            cible = par_contact.setdefault(
                op.contact_id,
                {
                    "contact": op.contact.nom,
                    "contact_id": str(op.contact_id),
                    "operations": [],
                    "total_du": 0.0,
                    "total_en_retard": 0.0,
                },
            )
        cible["operations"].append(serialized)
        cible["total_du"] += solde
        total_general += solde
        repartition[tranche] += solde
        if en_retard:
            cible["total_en_retard"] += solde
            total_en_retard += solde

    contacts = sorted(
        par_contact.values(), key=lambda e: e["total_en_retard"], reverse=True
    )

    if total_general <= 0:
        resume = "Aucun impayé : aucune créance client ouverte."
    else:
        lignes = [
            f"Impayés clients : {total_general:,.0f} FCFA au total "
            f"(dont {total_en_retard:,.0f} FCFA en retard ≥ {seuil} j)."
        ]
        for c in contacts:
            flag = " — en retard" if c.get("total_en_retard", 0) > 0 else ""
            lignes.append(f"• {c['contact']} — {c['total_du']:,.0f} FCFA{flag}")
        if sans_contact.get("total_du", 0) > 0:
            lignes.append(
                f"• Sans client — {sans_contact['total_du']:,.0f} FCFA"
            )
        resume = "\n".join(lignes)

    return {
        "success": True,
        "seuil_jours": seuil,
        "contacts": contacts,
        "sans_contact": sans_contact,
        "total_general": total_general,
        "total_en_retard": total_en_retard,
        "repartition": repartition,
        "has_impayes": total_general > 0,
        "has_retard": total_en_retard > 0,
        "resume_texte": resume,
    }
