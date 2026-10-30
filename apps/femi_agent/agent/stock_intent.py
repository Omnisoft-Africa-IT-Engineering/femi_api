"""Détection déterministe des messages de stock (partagée routeur / pending).

Règle : on ne force l'agent STOCK que sur un vocabulaire stock explicite ou sur
« j'ai reçu / ajoute » suivi d'une quantité AVEC une unité de produit.
« j'ai reçu 30 000 de Koffi » ou « ajoute une dépense » restent de la compta.
"""

from __future__ import annotations

import re

_UNITES = (
    r"(?:sacs?|cartons?|pi[eè]ces?|kg|kilos?|litres?|bouteilles?|bo[iî]tes?|"
    r"paquets?|palettes?|bidons?|caisses?|unit[eé]s?)"
)

_MOTS_STOCK = (
    "entrée stock", "entree stock", "sortie stock", "en stock", "mon stock",
    "mes stocks", "le stock", "inventaire", "réappro", "reappro",
    "liste mes produits", "mes produits",
)

_ENTREE_AVEC_UNITE = re.compile(
    r"\b(?:j'ai\s+re[cç]u|reception|r[eé]ception|ajoute|ajouter|ajout)\s+"
    r"(?:de\s+|du\s+|des\s+)?\d[\d\s.,]*\s*" + _UNITES + r"\b"
)

_QUESTION = re.compile(
    r"\b(?:combien|il\s+me\s+reste|reste-t-il|ai-je\s+encore|"
    r"j'ai\s+encore|est-ce\s+que|dispo(?:nible)?s?|quel\s+est\s+le\s+stock|"
    r"montre|affiche|liste|etat|état)\b|\?"
)


def _norm(text: str) -> str:
    return (text or "").casefold().replace("\u2019", "'").strip()


def looks_like_stock(text: str) -> bool:
    t = _norm(text)
    if not t:
        return False
    if any(m in t for m in _MOTS_STOCK):
        return True
    return bool(_ENTREE_AVEC_UNITE.search(t))


def _is_write(t: str) -> bool:
    return (
        any(m in t for m in ("entrée stock", "entree stock", "sortie stock", "réappro", "reappro"))
        or bool(_ENTREE_AVEC_UNITE.search(t))
    )


def is_stock_read(text: str) -> bool:
    """Consultation (« mon stock », « il me reste du riz en stock ? ») et non écriture."""
    t = _norm(text)
    if not looks_like_stock(t):
        return False
    if _QUESTION.search(t):
        return True
    return not _is_write(t)


_VENTE = re.compile(r"\b(?:j'?ai\s+)?vendu(?:e|s|es)?\b|\bvente\s+(?:de|d')\s*\d")


def looks_like_sale(text: str) -> bool:
    """« j'ai vendu 5 sacs de riz », « vente de 10 bic » : une VENTE (compta),
    pas un mouvement de stock. Exige un chiffre pour éviter « mes ventes ? »."""
    t = _norm(text)
    return bool(_VENTE.search(t)) and any(c.isdigit() for c in t)