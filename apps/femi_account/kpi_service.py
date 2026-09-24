from datetime import timedelta

from django.db.models import Sum

from .models import Operation, PrestationRealisee


def _operations(entreprise, debut=None, fin=None):
    qs = Operation.objects.filter(entreprise=entreprise)
    if debut:
        qs = qs.filter(transaction_date__gte=debut)
    if fin:
        qs = qs.filter(transaction_date__lte=fin)
    return qs


def _somme(qs, champ="amount_ttc"):
    return float(qs.aggregate(t=Sum(champ))["t"] or 0)


def _nb_clients(recettes):
    return (
        recettes.exclude(contact__isnull=True)
        .values("contact").distinct().count()
    )


def periode_precedente(debut, fin):
    """Période de même durée, juste avant [debut, fin]."""
    if not debut or not fin:
        return None, None
    duree = (fin - debut) + timedelta(days=1)
    return debut - duree, debut - timedelta(days=1)


def variation(actuel, precedent):
    """Variation en % (None si la période précédente vaut 0)."""
    if not precedent:
        return None
    return round((actuel - precedent) / abs(precedent) * 100, 1)


def calculer_kpi_sante(entreprise, debut=None, fin=None):
    """Niveau 1."""
    ops = _operations(entreprise, debut, fin)
    recettes = ops.filter(transaction_type="RECETTE")
    depenses = ops.filter(transaction_type="DEPENSE")

    ca = _somme(recettes)
    dep = _somme(depenses)
    encaisse = _somme(recettes, "montant_paye")
    decaisse = _somme(depenses, "montant_paye")

    return {
        "chiffre_affaires": ca,
        "benefice": round(ca - dep, 2),
        "depenses": dep,
        "tresorerie": round(encaisse - decaisse, 2),
        "clients": _nb_clients(recettes),
        "creances": round(ca - encaisse, 2),
    }


def calculer_kpi_activite(entreprise, debut=None, fin=None):
    """Niveau 2."""
    ops = _operations(entreprise, debut, fin)
    recettes = ops.filter(transaction_type="RECETTE")
    nb_ventes = recettes.count()
    revenus = _somme(recettes)
    nb_prestations = (
        PrestationRealisee.objects.filter(operation__in=recettes)
        .aggregate(t=Sum("quantite"))["t"] or 0
    )

    return {
        "transactions": ops.count(),
        "ventes": nb_ventes,
        "commandes": nb_ventes,  # provisoire
        "panier_moyen": round(revenus / nb_ventes, 2) if nb_ventes else 0,
        "clients": _nb_clients(recettes),
        "prestations": nb_prestations,
    }


def calculer_kpi_finance(entreprise, debut=None, fin=None):
    """Niveau 3."""
    ops = _operations(entreprise, debut, fin)
    recettes = ops.filter(transaction_type="RECETTE")
    depenses = ops.filter(transaction_type="DEPENSE")

    revenus = _somme(recettes)
    dep = _somme(depenses)
    encaisse = _somme(recettes, "montant_paye")
    decaisse = _somme(depenses, "montant_paye")
    benefice = revenus - dep

    return {
        "revenus": revenus,
        "depenses": dep,
        "marge": round(benefice / revenus * 100, 2) if revenus else 0,
        "benefice": round(benefice, 2),
        "tresorerie": round(encaisse - decaisse, 2),
        "dettes": round(dep - decaisse, 2),
        "creances": round(revenus - encaisse, 2),
    }