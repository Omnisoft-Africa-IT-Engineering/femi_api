"""
Tools de recherche d'opérations existantes pour ACCOUNTING_MODIFY
(UPDATE/DELETE — jamais de création ici).

Import confirmé : Operation vit dans apps.femi_account.models
(voir apps/femi_account/models.py, ligne 69).

Convention confirmée (accounting_modify_prompt.py, section CONTACT) :
`contact` reste un nom texte brut, JAMAIS résolu en instance Contact par
ce module (contrairement à debts.py/customer, qui reçoivent un objet
Contact déjà résolu) — l'ambiguïté sur un nom approximatif est un cas
d'usage légitime du CAS 2 (plusieurs candidats), pas une erreur à
corriger en amont. Filtrage donc par correspondance textuelle
(`contact__nom__icontains`).

Filtre `date_range`/`date_debut`/`date_fin` : `date_range` attend une
valeur de PERIODES_VALIDES (tools/periodes.py), résolue via
resolve_periode() — même vocabulaire que financials.py (règle "un seul
vocabulaire de périodes"). `date_debut`/`date_fin` ne sont utilisés que
si date_range == "personnalisee" (voir resolve_periode()).
"""

from apps.femi_account.models import Operation  # noqa: F401 (Entreprise non utilisé directement ici)
from apps.femi_agent.agent.tools.periodes import resolve_periode


def _serialize_operation_candidate(operation):
    """
    Résumé factuel d'une opération, destiné au champ `candidates[].summary`
    du FORMAT RÉSOLUTION — PLUSIEURS CANDIDATS (section 10 du prompt).

    Le prompt exige un résumé "strictement factuel [...] fondé uniquement
    sur les données retournées par le BACKEND" — construit ici, pas par
    le LLM, qui ne fait que le recopier tel quel dans sa sortie.
    """
    return {
        "operation_id": str(operation.id),
        "summary": (
            f"{operation.transaction_type} de {float(operation.amount_ttc):.0f} "
            f"{operation.currency} le {operation.transaction_date.isoformat()}"
            + (f" — {operation.description}" if operation.description else "")
            + (f" ({operation.contact.nom})" if operation.contact_id else "")
        ),
    }


def _serialize_operation_full(operation):
    """
    Représentation complète d'une opération trouvée — utilisée quand une
    seule opération correspond (CAS 3, section 11), pour construire
    `current_values` côté FORMAT PROPOSITION UPDATE/DELETE.

    Toujours `float()` explicite sur les Decimal, jamais de Decimal brut
    exposé au LLM (cohérent avec debts.py).
    """
    return {
        "operation_id": str(operation.id),
        "transaction_type": operation.transaction_type,
        "amount_ttc": float(operation.amount_ttc),
        "currency": operation.currency,
        "category": operation.category,
        "payment_method": operation.payment_method,
        "transaction_date": operation.transaction_date.isoformat(),
        "description": operation.description,
        "contact": operation.contact.nom if operation.contact_id else None,
        "statut_paiement": operation.statut_paiement,
        "montant_paye": float(operation.montant_paye),
    }


def find_matching_operations(
    entreprise,
    operation_id=None,
    contact_nom=None,
    amount_ttc=None,
    category=None,
    date_range=None,
    date_debut=None,
    date_fin=None,
    description_keywords=None,
    transaction_type=None,
):
    """
    Recherche des opérations existantes selon les critères de
    search_criteria (accounting_modify_prompt.py, section 4).

    Tous les critères sont optionnels et combinés en ET logique — un
    critère non fourni (None) n'est simplement pas appliqué au filtre.

    Args:
        entreprise: instance Entreprise (jamais résolue en interne).
        operation_id: str|None — UUID exact, prioritaire si fourni.
        contact_nom: str|None — nom texte brut, JAMAIS résolu en instance
            Contact ici (voir docstring du module) ; filtré par
            correspondance textuelle insensible à la casse.
        amount_ttc: float|None — montant ACTUEL recherché (pas le nouveau
            montant demandé pour un UPDATE, voir section 4 du prompt).
        category: str|None.
        date_range, date_debut, date_fin: date_range est une valeur de
            PERIODES_VALIDES (voir tools/periodes.py) ; date_debut/date_fin
            ne sont utilisés que si date_range == "personnalisee". Lève
            ValueError si date_range est invalide ou si "personnalisee"
            est fourni sans date_debut/date_fin (voir resolve_periode()).
        description_keywords: str|None — recherche insensible à la casse
            dans le champ description.
        transaction_type: str|None — une des valeurs RECETTE/DEPENSE/
            PRET_DONNE/PRET_RECU.

    Returns:
        {
            "count": int,
            "operations": [ {...}, ... ],  # _serialize_operation_full,
                                            # triées par date décroissante
                                            # (résultat le plus récent
                                            # en premier)
            "candidates": [ {operation_id, summary}, ... ] | None,
                # rempli uniquement si count > 1 — voir CAS 2 (section 10)
        }

        Le code appelant (futur accounting_modify_executor.py) est
        responsable de choisir le FORMAT RÉSOLUTION adapté selon `count`
        (0 → operation_not_found, 1 → CAS 3 direct, >1 → candidates).
    """
    qs = Operation.objects.filter(entreprise=entreprise)

    if operation_id:
        qs = qs.filter(id=operation_id)
    if contact_nom:
        qs = qs.filter(contact__nom__icontains=contact_nom)
    if amount_ttc is not None:
        qs = qs.filter(amount_ttc=amount_ttc)
    if category:
        qs = qs.filter(category__iexact=category)
    if description_keywords:
        qs = qs.filter(description__icontains=description_keywords)
    if transaction_type:
        qs = qs.filter(transaction_type=transaction_type)

    if date_range:
        start, end = resolve_periode(date_range, date_debut, date_fin)
        if start is not None:
            qs = qs.filter(transaction_date__gte=start, transaction_date__lte=end)

    qs = qs.select_related("contact").order_by("-transaction_date")
    operations = list(qs)

    result = {
        "count": len(operations),
        "operations": [_serialize_operation_full(op) for op in operations],
        "candidates": None,
    }
    if len(operations) > 1:
        result["candidates"] = [_serialize_operation_candidate(op) for op in operations]

    return result

def list_recent_operations(entreprise, limit: int = 15, date_range: str | None = None):
    """Liste les opérations récentes (lecture seule) pour WhatsApp."""
    try:
        lim = max(1, min(int(limit or 15), 30))
    except (TypeError, ValueError):
        lim = 15

    qs = Operation.objects.filter(entreprise=entreprise).select_related("contact")
    if date_range:
        try:
            start, end = resolve_periode(date_range, None, None)
            if start is not None:
                qs = qs.filter(transaction_date__gte=start, transaction_date__lte=end)
        except Exception:
            pass
    qs = qs.order_by("-transaction_date", "-id")[:lim]
    operations = list(qs)

    if not operations:
        resume = "Aucune opération trouvée pour le moment."
    else:
        lignes = []
        for op in operations:
            contact = f" ({op.contact.nom})" if op.contact_id else ""
            desc = f" — {op.description}" if op.description else ""
            amt = f"{float(op.amount_ttc):,.0f}".replace(",", " ")
            lignes.append(
                f"• {op.transaction_type} {amt} "
                f"{op.currency or 'FCFA'} le {op.transaction_date.isoformat()}"
                f"{contact}{desc}"
            )
        resume = f"Voici tes {len(operations)} dernière(s) opération(s) :\n" + "\n".join(lignes)

    return {
        "success": True,
        "count": len(operations),
        "operations": [_serialize_operation_full(op) for op in operations],
        "resume_texte": resume,
    }


def apply_operation_change(
    entreprise,
    operation_id: str,
    action_type: str,
    proposed_values: dict | None = None,
):
    """Applique une proposition UPDATE/DELETE après confirmation utilisateur."""
    from django.db import transaction

    try:
        op = Operation.objects.select_related("contact").get(
            id=operation_id, entreprise=entreprise
        )
    except Operation.DoesNotExist:
        return {
            "success": False,
            "error": "operation_introuvable",
            "resume_texte": "Je ne retrouve plus cette opération. Elle a peut-être déjà été modifiée.",
        }

    action = (action_type or "UPDATE").upper()
    if action == "DELETE":
        if hasattr(op, "annulee"):
            op.annulee = True
        elif hasattr(op, "is_cancelled"):
            op.is_cancelled = True
        else:
            prefix = "[ANNULÉ] "
            if op.description and not str(op.description).startswith(prefix):
                op.description = prefix + str(op.description)
            elif not op.description:
                op.description = "[ANNULÉ]"
        op.save()
        amt = f"{float(op.amount_ttc):,.0f}".replace(",", " ")
        resume = (
            f"Opération annulée : {op.transaction_type} {amt} "
            f"le {op.transaction_date.isoformat()}."
        )
        return {"success": True, "action": "DELETE", "operation_id": str(op.id), "resume_texte": resume}

    values = proposed_values or {}
    changes = []
    if "amount_ttc" in values and values["amount_ttc"] is not None:
        old = float(op.amount_ttc)
        op.amount_ttc = values["amount_ttc"]
        changes.append(
            f"montant {old:,.0f} → {float(op.amount_ttc):,.0f}".replace(",", " ")
        )
    if "description" in values and values["description"] is not None:
        op.description = values["description"]
        changes.append("description mise à jour")
    if "category" in values and values["category"] is not None:
        op.category = values["category"]
        changes.append(f"catégorie → {op.category}")
    if "payment_method" in values and values["payment_method"] is not None:
        op.payment_method = values["payment_method"]
        changes.append(f"paiement → {op.payment_method}")
    if "transaction_date" in values and values["transaction_date"]:
        from datetime import date as date_cls
        d = values["transaction_date"]
        if isinstance(d, str):
            op.transaction_date = date_cls.fromisoformat(d[:10])
        else:
            op.transaction_date = d
        changes.append(f"date → {op.transaction_date.isoformat()}")
    if "contact" in values and values["contact"]:
        from apps.femi_account.models import Contact
        nom = str(values["contact"]).strip()
        c = Contact.objects.filter(entreprise=entreprise, nom__iexact=nom).first()
        if c is None:
            c = Contact.objects.create(entreprise=entreprise, nom=nom)
        op.contact = c
        changes.append(f"contact → {c.nom}")

    with transaction.atomic():
        op.save()

    resume = (
        "Aucune modification à appliquer (valeurs identiques)."
        if not changes
        else "Modification enregistrée : " + " ; ".join(changes) + "."
    )
    return {
        "success": True,
        "action": "UPDATE",
        "operation_id": str(op.id),
        "resume_texte": resume,
    }
