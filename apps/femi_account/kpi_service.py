"""
Source unique de calcul des KPI de Femi.

Trois écrans les affichent (`/kpi/`, `/kpi/<niveau>/`, `dashboard/kpis/`) :
tous appellent ce module, donc le même nom de KPI donne le même chiffre
partout.

Deux familles, selon les règles comptables (SYSCOHADA) :

* FLUX, calculés sur la période [debut, fin] (compte de résultat) :
  chiffre d'affaires, dépenses, bénéfice, marge, nombre de ventes,
  panier moyen, clients, prestations.

* SOLDES, cumulés depuis le début de l'historique jusqu'à la date `fin`
  (bilan) : trésorerie, créances, dettes, prêts accordés. Ils ne dépendent
  pas de `debut` : une vente à crédit de septembre reste une créance en
  novembre tant qu'elle n'est pas encaissée.

Limite connue : il n'existe pas d'historique des paiements, seulement
`Operation.montant_paye` (état actuel). Un solde « à une date passée »
est donc une approximation ; pour la période en cours il est exact.

Les montants de devises différentes ne sont pas convertis (additionnés tels
quels), comme avant.
"""

from calendar import monthrange
from datetime import date, timedelta

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from .models import Operation, PrestationRealisee


# ---------------------------------------------------------------------------
# Périodes
# ---------------------------------------------------------------------------

def _bornes_mois(annee, mois):
    return date(annee, mois, 1), date(annee, mois, monthrange(annee, mois)[1])


def _decaler_mois(annee, mois, delta):
    indice = annee * 12 + (mois - 1) + delta
    return indice // 12, indice % 12 + 1


def resoudre_periode(period, aujourdhui=None):
    """
    Convertit `period` (today, this_month, last_month, this_year) en
    (debut, fin). Toute autre valeur : (None, None) = tout l'historique.
    """
    auj = aujourdhui or timezone.localdate()
    if period == "today":
        return auj, auj
    if period == "this_month":
        return _bornes_mois(auj.year, auj.month)
    if period == "last_month":
        return _bornes_mois(*_decaler_mois(auj.year, auj.month, -1))
    if period == "this_year":
        return date(auj.year, 1, 1), date(auj.year, 12, 31)
    return None, None


def resoudre_periode_precedente(period, aujourdhui=None):
    """
    Période servant de comparaison pour les tendances du dashboard :
    hier, mois précédent, mois d'avant, année précédente.
    Renvoie None s'il n'y a pas de période de comparaison.
    """
    auj = aujourdhui or timezone.localdate()
    if period == "today":
        hier = auj - timedelta(days=1)
        return hier, hier
    if period == "this_month":
        return _bornes_mois(*_decaler_mois(auj.year, auj.month, -1))
    if period == "last_month":
        return _bornes_mois(*_decaler_mois(auj.year, auj.month, -2))
    if period == "this_year":
        return date(auj.year - 1, 1, 1), date(auj.year - 1, 12, 31)
    return None


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


# ---------------------------------------------------------------------------
# Contexte de calcul (agrégats calculés une seule fois par requête)
# ---------------------------------------------------------------------------

def _f(valeur):
    return float(valeur or 0)


class KpiContexte:
    """
    Regroupe entreprise + période et calcule les agrégats à la demande
    (une requête pour les flux, une pour les soldes, une pour les prestations).
    """

    def __init__(self, entreprise, debut=None, fin=None):
        self.entreprise = entreprise
        self.debut = debut
        self.fin = fin
        self._flux = None
        self._soldes = None

    @property
    def flux(self):
        if self._flux is None:
            qs = Operation.objects.filter(entreprise=self.entreprise)
            if self.debut:
                qs = qs.filter(transaction_date__gte=self.debut)
            if self.fin:
                qs = qs.filter(transaction_date__lte=self.fin)
            vente = Q(transaction_type="RECETTE")
            self._flux = qs.aggregate(
                chiffre_affaires=Sum("amount_ttc", filter=vente),
                depenses=Sum("amount_ttc", filter=Q(transaction_type="DEPENSE")),
                nb_ventes=Count("id", filter=vente),
                nb_transactions=Count("id"),
                nb_clients=Count(
                    "contact",
                    filter=vente & Q(contact__isnull=False),
                    distinct=True,
                ),
            )
        return self._flux

    @property
    def soldes(self):
        if self._soldes is None:
            qs = Operation.objects.filter(entreprise=self.entreprise)
            if self.fin:
                qs = qs.filter(transaction_date__lte=self.fin)
            reste = F("amount_ttc") - F("montant_paye")

            def type_(nom):
                return Q(transaction_type=nom)

            self._soldes = qs.aggregate(
                encaisse=Sum("montant_paye", filter=type_("RECETTE")),
                decaisse=Sum("montant_paye", filter=type_("DEPENSE")),
                creances=Sum(reste, filter=type_("RECETTE")),
                dettes_fournisseurs=Sum(reste, filter=type_("DEPENSE")),
                emprunts=Sum(reste, filter=type_("PRET_RECU")),
                pret_recu_total=Sum("amount_ttc", filter=type_("PRET_RECU")),
                pret_recu_rembourse=Sum("montant_paye", filter=type_("PRET_RECU")),
                prets_accordes=Sum(reste, filter=type_("PRET_DONNE")),
                pret_donne_total=Sum("amount_ttc", filter=type_("PRET_DONNE")),
                pret_donne_rembourse=Sum("montant_paye", filter=type_("PRET_DONNE")),
            )
        return self._soldes

    def prestations(self):
        qs = PrestationRealisee.objects.filter(
            operation__entreprise=self.entreprise,
            operation__transaction_type="RECETTE",
        )
        if self.debut:
            qs = qs.filter(operation__transaction_date__gte=self.debut)
        if self.fin:
            qs = qs.filter(operation__transaction_date__lte=self.fin)
        return qs


# ---------------------------------------------------------------------------
# Calculateurs : une fonction par KPI, signature (ctx) -> valeur
# ---------------------------------------------------------------------------

def calc_chiffre_affaires(ctx):
    return _f(ctx.flux["chiffre_affaires"])


def calc_depenses(ctx):
    return _f(ctx.flux["depenses"])


def calc_benefice(ctx):
    return round(calc_chiffre_affaires(ctx) - calc_depenses(ctx), 2)


def calc_marge(ctx):
    ca = calc_chiffre_affaires(ctx)
    if ca <= 0:
        return 0.0
    return round((ca - calc_depenses(ctx)) / ca * 100, 2)


def calc_nombre_ventes(ctx):
    return ctx.flux["nb_ventes"] or 0


def calc_panier_moyen(ctx):
    nb = calc_nombre_ventes(ctx)
    return round(calc_chiffre_affaires(ctx) / nb, 2) if nb else 0.0


def calc_clients(ctx):
    return ctx.flux["nb_clients"] or 0


def calc_nombre_transactions(ctx):
    return ctx.flux["nb_transactions"] or 0


def calc_tresorerie(ctx):
    """Argent réellement encaissé - réellement décaissé, prêts inclus."""
    s = ctx.soldes
    return round(
        _f(s["encaisse"]) - _f(s["decaisse"])
        + _f(s["pret_recu_total"]) - _f(s["pret_recu_rembourse"])
        - _f(s["pret_donne_total"]) + _f(s["pret_donne_rembourse"]),
        2,
    )


def calc_creances(ctx):
    """Ventes à crédit non encaissées (solde cumulé)."""
    return round(_f(ctx.soldes["creances"]), 2)


def calc_dettes(ctx):
    """Achats à crédit non payés + emprunts non remboursés (solde cumulé)."""
    s = ctx.soldes
    return round(_f(s["dettes_fournisseurs"]) + _f(s["emprunts"]), 2)


def calc_prets_accordes(ctx):
    return round(_f(ctx.soldes["prets_accordes"]), 2)


def calc_nombre_prestations(ctx):
    return ctx.prestations().aggregate(t=Sum("quantite"))["t"] or 0


def calc_heures_facturees(ctx):
    minutes = ctx.prestations().aggregate(t=Sum("duree_minutes"))["t"] or 0
    return round(minutes / 60, 2)


def calc_marge_par_prestation(ctx):
    lignes = ctx.prestations().filter(
        prestation__cout_unitaire__isnull=False
    ).select_related("prestation")
    if not lignes.exists():
        return None
    return [
        {
            "prestation": ligne.prestation.nom,
            "marge_unitaire": float(
                ligne.prix_unitaire_facture - ligne.prestation.cout_unitaire
            ),
        }
        for ligne in lignes
    ]


# Nom du catalogue (`Kpi.nom`) -> calculateur.
KPI_CALCULATEURS = {
    "Chiffre d'affaires": calc_chiffre_affaires,
    "Revenus": calc_chiffre_affaires,
    "Dépenses": calc_depenses,
    "Bénéfice": calc_benefice,
    "Marge": calc_marge,
    "Trésorerie": calc_tresorerie,
    "Clients": calc_clients,
    "Créances": calc_creances,
    "Dettes": calc_dettes,
    "Prêts accordés": calc_prets_accordes,
    "Ventes": calc_nombre_ventes,
    "Panier moyen": calc_panier_moyen,
    "Nombre de prestations": calc_nombre_prestations,
    "Prestations": calc_nombre_prestations,
    "Heures facturées": calc_heures_facturees,
    "Marge par prestation": calc_marge_par_prestation,
}


def calculer_kpi(nom, ctx):
    """Valeur du KPI `nom`, ou None s'il n'a pas de calculateur."""
    calculateur = KPI_CALCULATEURS.get(nom)
    return calculateur(ctx) if calculateur else None


# ---------------------------------------------------------------------------
# Niveaux 1 à 3 de `/kpi/` (mêmes clés qu'avant)
# ---------------------------------------------------------------------------

def calculer_kpi_sante(entreprise, debut=None, fin=None):
    """Niveau 1."""
    ctx = KpiContexte(entreprise, debut, fin)
    return {
        "chiffre_affaires": calc_chiffre_affaires(ctx),
        "benefice": calc_benefice(ctx),
        "depenses": calc_depenses(ctx),
        "tresorerie": calc_tresorerie(ctx),
        "clients": calc_clients(ctx),
        "creances": calc_creances(ctx),
    }


def calculer_kpi_activite(entreprise, debut=None, fin=None):
    """Niveau 2."""
    ctx = KpiContexte(entreprise, debut, fin)
    nb_ventes = calc_nombre_ventes(ctx)
    return {
        "transactions": calc_nombre_transactions(ctx),
        "ventes": nb_ventes,
        "commandes": nb_ventes,  # provisoire
        "panier_moyen": calc_panier_moyen(ctx),
        "clients": calc_clients(ctx),
        "prestations": calc_nombre_prestations(ctx),
    }


def calculer_kpi_finance(entreprise, debut=None, fin=None):
    """Niveau 3."""
    ctx = KpiContexte(entreprise, debut, fin)
    return {
        "revenus": calc_chiffre_affaires(ctx),
        "depenses": calc_depenses(ctx),
        "marge": calc_marge(ctx),
        "benefice": calc_benefice(ctx),
        "tresorerie": calc_tresorerie(ctx),
        "dettes": calc_dettes(ctx),
        "creances": calc_creances(ctx),
    }