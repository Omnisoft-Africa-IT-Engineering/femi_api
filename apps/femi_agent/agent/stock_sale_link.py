"""Lien vente -> stock (mode hybride, sans appel LLM).

Après une vente (RECETTE) créée par l'agent comptable :
1. on cherche dans le message un produit DU CATALOGUE précédé d'une quantité
   (« 5 sacs de riz ») ; sans produit connu on ne dit rien ;
2. cas SÛR (une seule vente, quantités claires, stock suffisant) : le stock
   est décompté tout de suite (SORTIE motif VENTE rattachée à l'opération) et
   Femi le dit, avec « annule le décompte » pour revenir en arrière ;
3. cas DOUTEUX (quantité qui ressemble au montant, grosse quantité sans unité) :
   Femi demande « oui / non » avant d'écrire quoi que ce soit ;
4. stock insuffisant : rien n'est écrit, Femi prévient.

Toutes les écritures passent par enregistrer_mouvement() avec une clé
d'idempotence : un message rejoué ne décompte jamais deux fois.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.femi_account.models import MouvementStock, Operation, Produit
from apps.femi_account.stock_service import StockError, enregistrer_mouvement

logger = logging.getLogger(__name__)

AGENT = "STOCK"
ACTION_TYPE = "CONFIRM_SORTIE"
_FENETRE_NOUVELLE_VENTE = timedelta(seconds=120)
_FENETRE_ANNULATION = timedelta(hours=1)
_SEUIL_GROSSE_QUANTITE_SANS_UNITE = Decimal("1000")

_UNITES = (
    r"(?:sacs?|cartons?|pieces?|kg|kilos?|litres?|bouteilles?|boites?|"
    r"paquets?|palettes?|bidons?|caisses?|unites?)"
)
_DEMANDE_ANNULATION = re.compile(
    r"\bannul\w*\s+(?:le\s+|la\s+|ce\s+|mon\s+)?(?:decompte|sortie)\b"
)


def _norm(texte: str) -> str:
    t = unicodedata.normalize("NFD", (texte or "").replace("\u2019", "'"))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return t.casefold()


def _fmt(n) -> str:
    try:
        x = float(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(x - round(x)) < 0.001:
        return f"{int(round(x)):,}".replace(",", " ")
    return f"{x:,.3f}".replace(",", " ").rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Détection
# ---------------------------------------------------------------------------
def detecter_lignes(entreprise, texte: str) -> list[dict]:
    """Produits du catalogue cités avec une quantité juste avant leur nom."""
    t = _norm(texte)
    if not t.strip():
        return []
    produits = list(Produit.objects.filter(entreprise=entreprise, actif=True)[:500])
    # Noms longs d'abord : « riz parfume » est consommé avant « riz ».
    produits.sort(key=lambda p: len(p.nom), reverse=True)

    lignes: list[dict] = []
    for p in produits:
        nom = _norm(p.nom).strip()
        if not nom:
            continue
        motif = re.compile(
            r"(?<![\w.,])(?P<qte>\d+(?:[.,]\d+)?)\s*(?P<unite>" + _UNITES + r")?\s*"
            r"(?:(?:de|du|des)\s+|d')?" + re.escape(nom) + r"s?(?![\w])"
        )
        m = motif.search(t)
        if not m:
            continue
        try:
            qte = Decimal(m.group("qte").replace(",", "."))
        except InvalidOperation:
            continue
        if qte <= 0:
            continue
        lignes.append(
            {
                "produit_id": str(p.pk),
                "nom": p.nom,
                "unite": p.unite,
                "quantite": qte,
                "stock_actuel": p.stock_actuel,
                "prix_vente": p.prix_vente,
                "avec_unite": m.group("unite") is not None,
            }
        )
        t = t[: m.start()] + " " * (m.end() - m.start()) + t[m.end():]
    return lignes


def _nouvelles_ventes(operations) -> list:
    maintenant = timezone.now()
    return [
        op
        for op in operations or []
        if op.transaction_type == "RECETTE"
        and getattr(op, "_femi_outcome", None) is None
        and op.created_at is not None
        and maintenant - op.created_at < _FENETRE_NOUVELLE_VENTE
    ]


def _est_douteuse(ligne: dict, vente) -> bool:
    """Quantité qui ressemble à un montant, ou énorme sans unité."""
    qte = ligne["quantite"]
    try:
        if qte == Decimal(str(vente.amount_ttc)):
            return True
    except (InvalidOperation, TypeError):
        pass
    return (not ligne["avec_unite"]) and qte >= _SEUIL_GROSSE_QUANTITE_SANS_UNITE


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------
def _cle_vente(operation_id: str, produit_id: str) -> str:
    return "vte:" + hashlib.sha1(f"{operation_id}:{produit_id}".encode()).hexdigest()[:40]


def _cle_annulation(mouvement_id) -> str:
    return "ann:" + hashlib.sha1(str(mouvement_id).encode()).hexdigest()[:40]


def _ecrire_sorties(entreprise, operation, operation_id: str, lignes: list[dict], utilisateur):
    """SORTIE VENTE pour chaque ligne, tout ou rien. -> (résumés, nb déjà faits)."""
    resumes: list[str] = []
    deja = 0
    with transaction.atomic():
        for l in lignes:
            mouvement, cree = enregistrer_mouvement(
                entreprise=entreprise,
                produit_id=l["produit_id"],
                type_mouvement="SORTIE",
                quantite=l["quantite"],
                motif="VENTE",
                note="Vente enregistrée dans le chat",
                utilisateur=utilisateur,
                operation=operation,
                cle_idempotence=_cle_vente(operation_id, l["produit_id"]),
            )
            if not cree:
                deja += 1
            produit = mouvement.produit
            resumes.append(
                f"{produit.nom} : {_fmt(mouvement.stock_apres + abs(mouvement.variation))}"
                f" → {_fmt(mouvement.stock_apres)} {produit.unite}"
            )
    return resumes, deja


# ---------------------------------------------------------------------------
# Point d'entrée après une vente
# ---------------------------------------------------------------------------
def traiter_vente_stock(entreprise, operations, texte: str, pending, utilisateur=None) -> str | None:
    """Décompte auto si sûr, question si douteux, avertissement si insuffisant.

    Retourne la phrase à ajouter à la réponse, ou None (rien à dire).
    """
    if not (texte or "").strip():
        return None
    ventes = _nouvelles_ventes(operations)
    if len(ventes) != 1:  # plusieurs ventes : on ne devine pas laquelle
        return None
    vente = ventes[0]

    lignes = detecter_lignes(entreprise, texte)
    if not lignes:
        return None

    insuffisantes = [l for l in lignes if l["quantite"] > l["stock_actuel"]]
    valides = [l for l in lignes if l["quantite"] <= l["stock_actuel"]]
    douteuses = [l for l in valides if _est_douteuse(l, vente)]

    avertissements = [
        f"⚠️ Pour {l['nom']}, tu as {_fmt(l['stock_actuel'])} {l['unite']} en stock "
        f"pour {_fmt(l['quantite'])} vendu(s) : je ne peux pas le décompter. Vérifie ton stock."
        for l in insuffisantes
    ]
    parties: list[str] = []

    if valides and not douteuses and not insuffisantes:
        # Cas sûr : on décompte tout de suite.
        try:
            resumes, deja = _ecrire_sorties(
                entreprise, vente, str(vente.pk), valides, utilisateur
            )
        except StockError as exc:
            return f"Je n'ai rien décompté du stock : {exc}"
        except Exception:
            logger.exception("[stock_sale_link] échec du décompte automatique")
            return (
                "Je n'ai pas pu décompter le stock. "
                "Tu peux le faire avec « sortie stock » ou le formulaire."
            )
        if deja == len(valides):
            return None
        detail = " ; ".join(resumes)
        return (
            f"J'ai décompté du stock — {detail}. "
            "Si ce n'est pas bon, écris « annule le décompte »."
        )

    if valides and pending is not None:
        # Cas douteux : on demande avant d'écrire.
        if len(valides) == 1:
            l = valides[0]
            parties.append(
                f"Je décompte aussi {_fmt(l['quantite'])} {l['unite']} de {l['nom']} "
                f"du stock ({_fmt(l['stock_actuel'])} → "
                f"{_fmt(l['stock_actuel'] - l['quantite'])}) ? "
                "Réponds « oui » pour confirmer, « non » pour laisser le stock tel quel."
            )
        else:
            detail = "\n".join(
                f"• {l['nom']} : {_fmt(l['quantite'])} {l['unite']} "
                f"({_fmt(l['stock_actuel'])} → {_fmt(l['stock_actuel'] - l['quantite'])})"
                for l in valides
            )
            parties.append(
                "Je décompte aussi ces produits du stock ?\n"
                f"{detail}\nRéponds « oui » pour confirmer, « non » pour laisser le stock tel quel."
            )
        pending.store_confirmation(
            agent=AGENT,
            action_type=ACTION_TYPE,
            operation_id=str(vente.pk),
            proposed_values={
                "lignes": [
                    {"produit_id": l["produit_id"], "quantite": str(l["quantite"])}
                    for l in valides
                ]
            },
            text=texte,
        )

    parties.extend(avertissements)
    return "\n\n".join(parties) if parties else None


# ---------------------------------------------------------------------------
# Confirmation « oui » (cas douteux)
# ---------------------------------------------------------------------------
def est_confirmation_sortie(entry: dict | None) -> bool:
    return bool(entry) and entry.get("agent") == AGENT and entry.get("action_type") == ACTION_TYPE


def appliquer_sortie_vente(entreprise, entry: dict, utilisateur=None) -> str:
    """Écrit les SORTIE confirmées (tout ou rien) et résume le résultat."""
    lignes = (entry.get("proposed_values") or {}).get("lignes") or []
    if not lignes:
        return "Je n'ai rien à décompter du stock."
    operation_id = str(entry.get("operation_id") or "")
    operation = Operation.objects.filter(pk=operation_id, entreprise=entreprise).first()
    try:
        resumes, deja = _ecrire_sorties(entreprise, operation, operation_id, lignes, utilisateur)
    except StockError as exc:
        return f"Je n'ai rien décompté du stock : {exc}"
    except Exception:
        logger.exception("[stock_sale_link] échec du décompte de stock")
        return "Je n'ai pas pu décompter le stock. Tu peux le faire avec « sortie stock » ou le formulaire."
    if deja == len(lignes):
        return "Ce décompte de stock était déjà fait."
    return "C'est décompté ✅ " + " ; ".join(resumes) + ". Si ce n'est pas bon, écris « annule le décompte »."


# ---------------------------------------------------------------------------
# Annulation « annule le décompte » (sans état en attente)
# ---------------------------------------------------------------------------
def est_demande_annulation_decompte(texte: str) -> bool:
    return bool(_DEMANDE_ANNULATION.search(_norm(texte)))


def annuler_dernier_decompte(entreprise, utilisateur=None) -> str:
    """Remet en stock le dernier décompte de vente (< 1 h) : ENTREE RETOUR_CLIENT."""
    qs = MouvementStock.objects.filter(
        entreprise=entreprise,
        type_mouvement="SORTIE",
        motif="VENTE",
        operation__isnull=False,
        created_at__gte=timezone.now() - _FENETRE_ANNULATION,
    ).select_related("produit", "operation")
    if utilisateur is not None:
        qs = qs.filter(utilisateur=utilisateur)

    restants = [
        m
        for m in qs.order_by("-created_at")
        if not MouvementStock.objects.filter(
            entreprise=entreprise, cle_idempotence=_cle_annulation(m.pk)
        ).exists()
    ]
    if not restants:
        return "Je ne vois aucun décompte de vente récent (moins d'une heure) à annuler."

    operation_id = restants[0].operation_id
    a_annuler = [m for m in restants if m.operation_id == operation_id]
    resumes: list[str] = []
    try:
        with transaction.atomic():
            for m in a_annuler:
                retour, _ = enregistrer_mouvement(
                    entreprise=entreprise,
                    produit_id=m.produit_id,
                    type_mouvement="ENTREE",
                    quantite=abs(m.variation),
                    motif="RETOUR_CLIENT",
                    cout_unitaire=m.cout_unitaire,
                    note="Annulation du décompte de vente",
                    utilisateur=utilisateur,
                    operation=m.operation,
                    cle_idempotence=_cle_annulation(m.pk),
                )
                resumes.append(
                    f"{m.produit.nom} : {_fmt(retour.stock_apres)} {m.produit.unite} en stock"
                )
    except StockError as exc:
        return f"Je n'ai pas pu annuler le décompte : {exc}"
    except Exception:
        logger.exception("[stock_sale_link] échec de l'annulation du décompte")
        return "Je n'ai pas pu annuler le décompte. Tu peux remettre le stock avec « entrée stock »."
    return "C'est annulé ✅ Stock remis — " + " ; ".join(resumes) + "."