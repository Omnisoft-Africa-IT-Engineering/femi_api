"""
Tools financiers agrégés (CA, dépenses, bénéfice, marge, trésorerie).

Destinés à être appelés par FINANCIAL_ANALYST_AGENT (financial_analyst_prompt.py)
via function-calling. Les paramètres et noms de tools ci-dessous respectent
EXACTEMENT ce que le prompt annonce au LLM :

    get_revenue(periode)
    get_expenses(periode)
    calculate_profit(periode)
    calculate_margin(periode)
    get_cashflow(periode)
    calculate_balance()                       # sans paramètre
    compare_periods(indicateur, periode_1, periode_2)

`entreprise` n'est jamais un paramètre choisi par le LLM : il est résolu par
le backend/orchestrateur à partir du contexte de session, puis injecté en
premier argument de chaque fonction Python ci-dessous.

--------------------------------------------------------------------------
RÉSOLUTION DES PÉRIODES — écart identifié avec l'existant
--------------------------------------------------------------------------
apps/femi_api/views.py::_filtrer_periode() ne gère que 4 valeurs
('today', 'this_month', 'last_month', 'this_year', en anglais) et retombe
silencieusement sur "aucun filtre" pour tout le reste.

financial_analyst_prompt.py attend 10 valeurs, en français :
aujourd'hui, hier, cette_semaine, semaine_derniere, ce_mois, mois_dernier,
cette_annee, annee_derniere, depuis_debut, personnalisee (+ date_debut/date_fin).

_resolve_periode() ci-dessous est donc une NOUVELLE fonction, pas un simple
port de _filtrer_periode — elle a été conçue pour couvrir le vocabulaire du
prompt. Hypothèses posées (à corriger si besoin, un seul endroit à changer) :

    - Semaine ISO : lundi → dimanche.
    - depuis_debut = tout l'historique, aucune borne de date.

--------------------------------------------------------------------------
CALCULS — écart identifié avec l'existant (_calc_tresorerie)
--------------------------------------------------------------------------
_calc_tresorerie(entreprise, ops_periode, toutes_ops) dans views.py calcule
TOUJOURS sur toutes_ops (jamais sur ops_periode) : c'est donc en réalité un
calcul de SOLDE CUMULÉ, pas un flux de période. Il correspond à
calculate_balance() ci-dessous, pas à get_cashflow(periode).

get_cashflow(periode) est une variante NOUVELLE : même formule que
_calc_tresorerie, mais restreinte aux opérations de la période (mouvement
net PENDANT la période), pour distinguer "trésorerie actuelle" (balance)
de "comment ma trésorerie a évolué" (cashflow), comme l'exige le prompt
section BALANCE / CASHFLOW.

Tous les montants viennent désormais de apps.femi_account.kpi_service, la
source unique des KPI : le chat donne les mêmes chiffres que le tableau de
bord. L'ancienne asymétrie (encaisse = montant_paye, décaisse = amount_ttc)
est supprimée : une dépense à crédit non payée ne sort pas de la trésorerie.
"""

import calendar
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.femi_account.kpi_service import (
    KpiContexte,
    calc_benefice,
    calc_chiffre_affaires,
    calc_depenses,
    calc_marge,
    calc_tresorerie,
    calc_variation_tresorerie,
    variation,
)
from apps.femi_account.models import Operation

from apps.femi_agent.agent.tools.periodes import PERIODES_VALIDES, resolve_periode  # noqa: F401 (PERIODES_VALIDES ré-exporté pour compat)


def _ops_periode(entreprise, periode, date_debut=None, date_fin=None):
    """QuerySet Operation de l'entreprise, filtré sur la période résolue."""
    start, end = resolve_periode(periode, date_debut, date_fin)
    qs = Operation.objects.filter(entreprise=entreprise)
    if start is not None:
        qs = qs.filter(transaction_date__gte=start, transaction_date__lte=end)
    return qs


def _sum(qs, field="amount_ttc"):
    return qs.aggregate(t=Sum(field))["t"] or Decimal("0.00")


def _devise(entreprise):
    return getattr(entreprise, "devise", None) or "XOF"


def _contexte(entreprise, periode, date_debut=None, date_fin=None):
    """Contexte de calcul kpi_service pour la période demandée au chat."""
    start, end = resolve_periode(periode, date_debut, date_fin)
    return KpiContexte(entreprise, start, end)


# --------------------------------------------------------------------------
# Tools exposés au LLM
# --------------------------------------------------------------------------

def get_revenue(entreprise, periode, date_debut=None, date_fin=None):
    """
    Chiffre d'affaires (somme des RECETTE, amount_ttc) sur la période.
    Reprend _calc_chiffre_affaires.

    Returns:
        {"periode": periode, "revenue": float, "currency": "XOF"}
    """
    ctx = _contexte(entreprise, periode, date_debut, date_fin)
    return {
        "periode": periode,
        "revenue": calc_chiffre_affaires(ctx),
        "currency": _devise(entreprise),
    }


def get_expenses(entreprise, periode, date_debut=None, date_fin=None, detail_par_categorie=False):
    """
    Dépenses (somme des DEPENSE, amount_ttc) sur la période.
    Reprend _calc_depenses, avec détail optionnel via _calc_depenses_par_categorie.

    Returns:
        {
            "periode": periode,
            "expenses": float,
            "currency": "XOF",
            "par_categorie": [{"categorie": str, "montant": float}, ...] | None
        }
    """
    ctx = _contexte(entreprise, periode, date_debut, date_fin)
    expenses = calc_depenses(ctx)

    par_categorie = None
    if detail_par_categorie:
        ops = _ops_periode(entreprise, periode, date_debut, date_fin)
        depenses_qs = ops.filter(transaction_type="DEPENSE")
        avec_categorie = depenses_qs.exclude(category__isnull=True).exclude(category__exact="")
        lignes = avec_categorie.values("category").annotate(total=Sum("amount_ttc")).order_by("-total")
        par_categorie = [{"categorie": l["category"], "montant": float(l["total"])} for l in lignes]
        # Les dépenses sans catégorie restent visibles dans la répartition
        # (sinon « où va l'argent » ne montrait que le total, sans détail).
        sans_categorie = float(_sum(depenses_qs.filter(Q(category__isnull=True) | Q(category__exact=""))))
        if sans_categorie > 0:
            par_categorie.append({"categorie": "Sans catégorie", "montant": sans_categorie})
        par_categorie = par_categorie or None

    return {
        "periode": periode,
        "expenses": expenses,
        "currency": _devise(entreprise),
        "par_categorie": par_categorie,
    }


def calculate_profit(entreprise, periode, date_debut=None, date_fin=None):
    """
    Bénéfice = revenue - expenses sur la période. Reprend _calc_benefice.

    Returns:
        {"periode": periode, "profit": float, "currency": "XOF"}
    """
    ctx = _contexte(entreprise, periode, date_debut, date_fin)
    return {
        "periode": periode,
        "profit": calc_benefice(ctx),
        "currency": _devise(entreprise),
    }


def calculate_margin(entreprise, periode, date_debut=None, date_fin=None):
    """
    Marge (%) = (revenue - expenses) / revenue * 100 sur la période.
    Même calcul que le tableau de bord (kpi_service.calc_marge). Retourne
    margin_percent = None (avec une note) s'il n'y a aucun chiffre d'affaires :
    une marge de 0 % laisserait croire à une activité à l'équilibre.

    Returns:
        {"periode": periode, "margin_percent": float | None}
    """
    ctx = _contexte(entreprise, periode, date_debut, date_fin)
    marge = calc_marge(ctx)
    if marge is None:
        return {
            "periode": periode,
            "margin_percent": None,
            "note": "Aucun chiffre d'affaires sur cette période : la marge n'est pas calculable.",
        }
    return {"periode": periode, "margin_percent": marge}


def get_cashflow(entreprise, periode, date_debut=None, date_fin=None):
    """
    Mouvement net de trésorerie PENDANT la période (entrées - sorties,
    prêts compris). C'est la différence entre la trésorerie à la fin de la
    période et à sa veille : même formule que calculate_balance.

    encaisse / decaisse = argent réellement reçu / payé sur les opérations
    de la période (montant_paye) ; net inclut en plus les prêts.

    Returns:
        {
            "periode": periode,
            "encaisse": float, "decaisse": float,
            "net": float, "currency": str
        }
    """
    ops = _ops_periode(entreprise, periode, date_debut, date_fin)
    encaisse = _sum(ops.filter(transaction_type="RECETTE"), field="montant_paye")
    decaisse = _sum(ops.filter(transaction_type="DEPENSE"), field="montant_paye")
    ctx = _contexte(entreprise, periode, date_debut, date_fin)

    return {
        "periode": periode,
        "encaisse": float(encaisse),
        "decaisse": float(decaisse),
        "net": calc_variation_tresorerie(ctx),
        "currency": _devise(entreprise),
    }


def calculate_balance(entreprise, periode=None, **kwargs):
    """
    Solde de trésorerie CUMULÉ (tout l'historique, sans période).
    Même calcul que le tableau de bord (kpi_service.calc_tresorerie).

    Returns:
        {"balance": float, "currency": str}
    """
    ctx = KpiContexte(entreprise, None, None)
    return {"balance": calc_tresorerie(ctx), "currency": _devise(entreprise)}


_INDICATEUR_TOOLS = {
    "revenue": get_revenue,
    "expenses": get_expenses,
    "profit": calculate_profit,
    "margin": calculate_margin,
    "cashflow": get_cashflow,
}


def compare_periods(entreprise, indicateur, periode_1, periode_2,
                     date_debut_1=None, date_fin_1=None,
                     date_debut_2=None, date_fin_2=None):
    """
    Compare un indicateur (revenue|expenses|profit|margin|cashflow) entre
    deux périodes. "balance" n'est volontairement pas comparable ici : c'est
    un solde cumulé sans notion de période (voir calculate_balance).

    Returns:
        {
            "indicateur": indicateur,
            "periode_1": {"periode": ..., "value": float},
            "periode_2": {"periode": ..., "value": float},
            "variation_absolue": float | None,
            "variation_percent": float | None,   # None si periode_2 == 0 ou valeur absente
        }

    Raises:
        ValueError si indicateur n'est pas comparable.
    """
    if indicateur not in _INDICATEUR_TOOLS:
        raise ValueError(
            f"indicateur non comparable : {indicateur!r} "
            f"(valeurs autorisées : {sorted(_INDICATEUR_TOOLS)})"
        )

    tool = _INDICATEUR_TOOLS[indicateur]
    result_1 = tool(entreprise, periode_1, date_debut_1, date_fin_1)
    result_2 = tool(entreprise, periode_2, date_debut_2, date_fin_2)

    # Clé de valeur principale selon l'indicateur (chaque tool a un nom de champ différent)
    cle_valeur = {
        "revenue": "revenue",
        "expenses": "expenses",
        "profit": "profit",
        "margin": "margin_percent",
        "cashflow": "net",
    }[indicateur]

    valeur_1 = result_1[cle_valeur]
    valeur_2 = result_2[cle_valeur]

    if valeur_1 is None or valeur_2 is None:
        # Ex. marge sans chiffre d'affaires sur l'une des périodes.
        variation_absolue = None
        variation_percent = None
    else:
        variation_absolue = valeur_1 - valeur_2
        variation_percent = variation(valeur_1, valeur_2)

    return {
        "indicateur": indicateur,
        "periode_1": {"periode": periode_1, "value": valeur_1},
        "periode_2": {"periode": periode_2, "value": valeur_2},
        "variation_absolue": variation_absolue,
        "variation_percent": variation_percent,
    }