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

Asymétrie encaisse/décaisse volontairement conservée à l'identique de
_calc_tresorerie : "encaisse" utilise montant_paye (RECETTE réellement
encaissée), "decaisse" utilise amount_ttc (DEPENSE comptabilisée, pas
nécessairement payée). Pas corrigée ici — à trancher séparément si c'est
un bug côté views.py.
"""

import calendar
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

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
    ops = _ops_periode(entreprise, periode, date_debut, date_fin)
    revenue = float(_sum(ops.filter(transaction_type="RECETTE")))
    return {"periode": periode, "revenue": revenue, "currency": "XOF"}


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
    ops = _ops_periode(entreprise, periode, date_debut, date_fin)
    depenses_qs = ops.filter(transaction_type="DEPENSE")
    expenses = float(_sum(depenses_qs))

    par_categorie = None
    if detail_par_categorie:
        lignes = (
            depenses_qs.exclude(category__isnull=True).exclude(category__exact="")
            .values("category")
            .annotate(total=Sum("amount_ttc"))
            .order_by("-total")
        )
        par_categorie = [{"categorie": l["category"], "montant": float(l["total"])} for l in lignes] or None

    return {"periode": periode, "expenses": expenses, "currency": "XOF", "par_categorie": par_categorie}


def calculate_profit(entreprise, periode, date_debut=None, date_fin=None):
    """
    Bénéfice = revenue - expenses sur la période. Reprend _calc_benefice.

    Returns:
        {"periode": periode, "profit": float, "currency": "XOF"}
    """
    revenue = get_revenue(entreprise, periode, date_debut, date_fin)["revenue"]
    expenses = get_expenses(entreprise, periode, date_debut, date_fin)["expenses"]
    return {"periode": periode, "profit": revenue - expenses, "currency": "XOF"}


def calculate_margin(entreprise, periode, date_debut=None, date_fin=None):
    """
    Marge (%) = (revenue - expenses) / revenue * 100 sur la période.
    Reprend _calc_marge. Retourne 0.0 si revenue <= 0 (évite division par zéro,
    même convention que l'existant).

    Returns:
        {"periode": periode, "margin_percent": float}
    """
    revenue = get_revenue(entreprise, periode, date_debut, date_fin)["revenue"]
    if revenue <= 0:
        return {"periode": periode, "margin_percent": 0.0}
    expenses = get_expenses(entreprise, periode, date_debut, date_fin)["expenses"]
    margin = round(((revenue - expenses) / revenue) * 100, 2)
    return {"periode": periode, "margin_percent": margin}


def get_cashflow(entreprise, periode, date_debut=None, date_fin=None):
    """
    Mouvement net de trésorerie PENDANT la période (entrées - sorties,
    ajusté des prêts). Même formule que _calc_tresorerie, mais restreinte
    à la période au lieu de tout l'historique — voir note en tête de fichier.

    Returns:
        {
            "periode": periode,
            "encaisse": float, "decaisse": float,
            "net": float, "currency": "XOF"
        }
    """
    ops = _ops_periode(entreprise, periode, date_debut, date_fin)

    encaisse = _sum(ops.filter(transaction_type="RECETTE"), field="montant_paye")
    decaisse = _sum(ops.filter(transaction_type="DEPENSE"), field="amount_ttc")

    prets_donnes = ops.filter(transaction_type="PRET_DONNE")
    sorti_prets = _sum(prets_donnes, field="amount_ttc")
    rembourse_recu = _sum(prets_donnes, field="montant_paye")

    prets_recus = ops.filter(transaction_type="PRET_RECU")
    entre_prets = _sum(prets_recus, field="amount_ttc")
    rembourse_verse = _sum(prets_recus, field="montant_paye")

    net = encaisse - decaisse - sorti_prets + rembourse_recu + entre_prets - rembourse_verse

    return {
        "periode": periode,
        "encaisse": float(encaisse),
        "decaisse": float(decaisse),
        "net": float(net),
        "currency": "XOF",
    }


def calculate_balance(entreprise, periode=None, **kwargs):
    """
    Solde de trésorerie CUMULÉ (tout l'historique, sans période).
    Reprise directe de _calc_tresorerie appliquée à toutes les opérations.

    Returns:
        {"balance": float, "currency": "XOF"}
    """
    ops = Operation.objects.filter(entreprise=entreprise)

    encaisse = _sum(ops.filter(transaction_type="RECETTE"), field="montant_paye")
    decaisse = _sum(ops.filter(transaction_type="DEPENSE"), field="amount_ttc")

    prets_donnes = ops.filter(transaction_type="PRET_DONNE")
    sorti_prets = _sum(prets_donnes, field="amount_ttc")
    rembourse_recu = _sum(prets_donnes, field="montant_paye")

    prets_recus = ops.filter(transaction_type="PRET_RECU")
    entre_prets = _sum(prets_recus, field="amount_ttc")
    rembourse_verse = _sum(prets_recus, field="montant_paye")

    balance = encaisse - decaisse - sorti_prets + rembourse_recu + entre_prets - rembourse_verse

    return {"balance": float(balance), "currency": "XOF"}


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
            "variation_absolue": float,
            "variation_percent": float | None,   # None si periode_2 == 0
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

    variation_absolue = valeur_1 - valeur_2
    variation_percent = round((variation_absolue / valeur_2) * 100, 2) if valeur_2 != 0 else None

    return {
        "indicateur": indicateur,
        "periode_1": {"periode": periode_1, "value": valeur_1},
        "periode_2": {"periode": periode_2, "value": valeur_2},
        "variation_absolue": variation_absolue,
        "variation_percent": variation_percent,
    }