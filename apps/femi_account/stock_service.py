"""Gestion de stock : toute variation passe par enregistrer_mouvement().

Règles :
- le stock n'est jamais modifié à la main : un MouvementStock est écrit et
  Produit.stock_actuel / cout_moyen sont mis à jour dans la même transaction,
  produit verrouillé (select_for_update) pour éviter les écarts en cas
  d'accès simultanés ;
- valorisation au coût moyen pondéré (CUMP), recalculé à chaque entrée dont le
  coût est connu ;
- un stock négatif est refusé par défaut ;
- une clé d'idempotence évite de compter deux fois un mouvement renvoyé par
  l'app après une coupure réseau.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum

from apps.femi_account.models import MouvementStock, Produit

logger = logging.getLogger(__name__)

_DEUX = Decimal("0.01")
_TROIS = Decimal("0.001")

MOTIFS_PAR_TYPE = {
    "ENTREE": {"STOCK_INITIAL", "ACHAT", "RETOUR_CLIENT", "AUTRE"},
    "SORTIE": {"VENTE", "RETOUR_FOURNISSEUR", "PERTE", "CASSE", "USAGE_INTERNE", "AUTRE"},
    "AJUSTEMENT": {"INVENTAIRE", "AUTRE"},
}
MOTIF_PAR_DEFAUT = {"ENTREE": "ACHAT", "SORTIE": "VENTE", "AJUSTEMENT": "INVENTAIRE"}


class StockError(ValueError):
    """Erreur métier de stock (message affichable à l'utilisateur)."""


class ProduitIntrouvableError(StockError):
    """Le produit n'existe pas dans cette entreprise."""


class StockInsuffisantError(StockError):
    """La sortie demandée dépasse le stock disponible."""


class MouvementInvalideError(StockError):
    """Données de mouvement inexploitables."""


def _decimal(valeur, champ: str) -> Decimal:
    try:
        d = Decimal(str(valeur))
    except (InvalidOperation, ValueError):
        raise MouvementInvalideError(f"{champ} invalide.")
    if not d.is_finite():
        raise MouvementInvalideError(f"{champ} invalide.")
    return d


def _fmt(d: Decimal) -> str:
    """Affiche 12.000 sous la forme 12."""
    texte = format(d.normalize(), "f")
    return texte


def calculer_cump(stock: Decimal, cump: Decimal, quantite: Decimal, cout: Decimal) -> Decimal:
    """Nouveau coût moyen pondéré après une entrée de `quantite` à `cout`."""
    if stock <= 0:
        return cout.quantize(_DEUX, ROUND_HALF_UP)
    total = stock * cump + quantite * cout
    return (total / (stock + quantite)).quantize(_DEUX, ROUND_HALF_UP)


@transaction.atomic
def enregistrer_mouvement(
    *,
    entreprise,
    produit_id,
    type_mouvement: str,
    quantite,
    motif: str | None = None,
    cout_unitaire=None,
    note: str = "",
    utilisateur=None,
    operation=None,
    cle_idempotence: str | None = None,
    autoriser_negatif: bool = False,
    date_mouvement=None,
):
    """Enregistre un mouvement et met le stock à jour. Retourne (mouvement, cree).

    - ENTREE / SORTIE : `quantite` est la quantité (> 0) à ajouter / retirer.
    - AJUSTEMENT : `quantite` est le stock CONSTATÉ après comptage (>= 0) ;
      la variation est calculée (constaté - stock actuel).
    - `cree` vaut False quand la clé d'idempotence correspond à un mouvement
      déjà enregistré : celui-ci est renvoyé tel quel, sans rien modifier.
    """
    if type_mouvement not in MOTIFS_PAR_TYPE:
        raise MouvementInvalideError("Type de mouvement inconnu.")
    motif = motif or MOTIF_PAR_DEFAUT[type_mouvement]
    if motif not in MOTIFS_PAR_TYPE[type_mouvement]:
        raise MouvementInvalideError("Ce motif ne correspond pas au type de mouvement.")

    qte = _decimal(quantite, "Quantité").quantize(_TROIS, ROUND_HALF_UP)
    cout = None
    if cout_unitaire is not None:
        cout = _decimal(cout_unitaire, "Coût unitaire")
        if cout < 0:
            raise MouvementInvalideError("Le coût unitaire ne peut pas être négatif.")
        cout = cout.quantize(_DEUX, ROUND_HALF_UP)

    try:
        produit = Produit.objects.select_for_update().get(pk=produit_id, entreprise=entreprise)
    except (Produit.DoesNotExist, ValueError, TypeError):
        raise ProduitIntrouvableError("Produit introuvable.")

    # Après le verrou du produit : deux envois simultanés de la même clé
    # passent l'un après l'autre, le second retrouve donc le premier.
    if cle_idempotence:
        existant = (
            MouvementStock.objects.filter(entreprise=entreprise, cle_idempotence=cle_idempotence)
            .select_related("produit")
            .first()
        )
        if existant is not None:
            return existant, False

    if not produit.actif:
        raise MouvementInvalideError("Ce produit est archivé : réactivez-le avant tout mouvement.")

    stock_avant = produit.stock_actuel
    cump_avant = produit.cout_moyen
    nouveau_cump = cump_avant

    if type_mouvement == "ENTREE":
        if qte <= 0:
            raise MouvementInvalideError("La quantité doit être supérieure à zéro.")
        variation = qte
        if cout is not None:
            nouveau_cump = calculer_cump(stock_avant, cump_avant, qte, cout)
        cout_mouvement = cout if cout is not None else cump_avant
    elif type_mouvement == "SORTIE":
        if qte <= 0:
            raise MouvementInvalideError("La quantité doit être supérieure à zéro.")
        variation = -qte
        cout_mouvement = cump_avant
    else:  # AJUSTEMENT
        if qte < 0:
            raise MouvementInvalideError("Le stock constaté ne peut pas être négatif.")
        variation = qte - stock_avant
        if variation == 0:
            raise MouvementInvalideError("Le stock constaté est identique au stock actuel.")
        cout_mouvement = cump_avant

    stock_apres = stock_avant + variation
    if stock_apres < 0 and not autoriser_negatif:
        raise StockInsuffisantError(
            f"Stock insuffisant pour « {produit.nom} » : "
            f"{_fmt(stock_avant)} {produit.unite} disponible(s)."
        )

    kwargs = {}
    if date_mouvement is not None:
        kwargs["date_mouvement"] = date_mouvement
    mouvement = MouvementStock.objects.create(
        entreprise=entreprise,
        produit=produit,
        type_mouvement=type_mouvement,
        motif=motif,
        variation=variation,
        stock_apres=stock_apres,
        cout_unitaire=cout_mouvement,
        operation=operation,
        utilisateur=utilisateur,
        note=(note or "").strip()[:255],
        cle_idempotence=cle_idempotence or None,
        **kwargs,
    )

    produit.stock_actuel = stock_apres
    produit.cout_moyen = nouveau_cump
    produit.save(update_fields=["stock_actuel", "cout_moyen", "updated_at"])

    logger.info(
        "[Stock] %s %s produit=%s variation=%s stock_apres=%s",
        type_mouvement,
        motif,
        produit.pk,
        variation,
        stock_apres,
    )
    return mouvement, True


def resume_stock(entreprise) -> dict:
    """Chiffres clés du stock (produits actifs uniquement)."""
    actifs = Produit.objects.filter(entreprise=entreprise, actif=True)
    valeur = ExpressionWrapper(
        F("stock_actuel") * F("cout_moyen"),
        output_field=DecimalField(max_digits=24, decimal_places=5),
    )
    agg = actifs.aggregate(
        valeur_stock=Sum(valeur, filter=Q(stock_actuel__gt=0)),
    )
    return {
        "nombre_produits": actifs.count(),
        "valeur_stock": (agg["valeur_stock"] or Decimal("0")).quantize(_DEUX, ROUND_HALF_UP),
        "nb_ruptures": actifs.filter(stock_actuel__lte=0).count(),
        "nb_alertes": actifs.filter(
            stock_actuel__gt=0, seuil_alerte__gt=0, stock_actuel__lte=F("seuil_alerte")
        ).count(),
    }