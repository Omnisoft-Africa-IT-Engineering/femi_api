"""Aiguillage vers l'agent STOCK : règles déterministes, volontairement étroites.

Principe : ne détourner vers le stock QUE ce qui est sans ambiguïté, et jamais un
message qui parle d'argent. Une vente ou une dépense envoyée par erreur au stock
n'est enregistrée nulle part dans la comptabilité, ce qui est bien pire qu'un
message de stock traité comme de la compta (que l'agent comptable sait redemander).

Tout le reste est laissé au Router (LLM), dont le prompt contient les règles de
priorité du stock (voir router_prompt.py, règle 5b).
"""

import re

_UNITES = (
    r"(?:sacs?|cartons?|pi[eè]ces?|kg|kilos?|litres?|bouteilles?|bo[iî]tes?|"
    r"paquets?|palettes?|caisses?|bidons?|unit[eé]s?|douzaines?)"
)
_NOMBRE = r"\d[\d\s.,]*"

# « j'ai reçu 20 sacs… » (et non « j'ai reçu 30 000 de Koffi » : pas d'unité de marchandise)
_RECEPTION = re.compile(
    r"\b(?:j['’ ]?ai\s+re[cç]u|r[eé]ception\s+de|livraison\s+de|on\s+m['’ ]?a\s+livr[eé]e?)\s+(?:de\s+)?"
    + _NOMBRE + r"\s*" + _UNITES + r"\b"
)
# « sortie de 3 sacs » (et non « sortie de 5000 » : une sortie d'argent)
_SORTIE = re.compile(r"\bsortie\s+(?:de\s+)?" + _NOMBRE + r"\s*" + _UNITES + r"\b")

_EXPLICITE = (
    "entrée stock", "entree stock", "entrée en stock", "entree en stock",
    "sortie stock", "sortie de stock", "en stock", "mon stock", "le stock",
    "état du stock", "etat du stock", "inventaire", "réappro", "reappro",
    "liste mes produits", "liste de mes produits",
)
_LECTURE = (
    "mon stock", "liste mes produits", "liste de mes produits",
    "état du stock", "etat du stock",
)

# Verbe ou nom d'argent : le message reste à la compta (ou au Router) même avec une quantité.
_ARGENT = re.compile(
    r"\b(?:vendu(?:e|s|es)?|vendre|vends|vente|ventes|ach[eè]t\w*|achat\w*|pay[eéè]\w*|paiement\w*|"
    r"d[eé]pens\w*|recette\w*|encaiss\w*|pr[eê]t[eé]?s?|emprunt\w*|rembours\w*|factur\w*|"
    r"salaire\w*|revenu\w*)\b"
)

# Agents que l'on accepte de remplacer. ACCOUNTING_MODIFY n'en fait pas partie :
# modifier ou supprimer une opération ne doit jamais devenir un mouvement de stock.
_AGENTS_CONVERTIBLES = ("ACCOUNTING", "UNKNOWN", "GENERAL")


def message_looks_like_stock(text: str) -> bool:
    """Vrai seulement si le texte parle sans ambiguïté de stock ET pas d'argent."""
    t = (text or "").casefold().strip()
    if not t or _ARGENT.search(t):
        return False
    return (
        any(m in t for m in _EXPLICITE)
        or bool(_RECEPTION.search(t))
        or bool(_SORTIE.search(t))
    )


def _est_lecture(text: str) -> bool:
    t = (text or "").casefold()
    return any(m in t for m in _LECTURE)


def force_stock_intent_if_needed(intents, message_text: str = ""):
    """Remplace par STOCK les intentions dont LE SEGMENT est clairement du stock.

    On juge chaque intention sur son propre `raw_segment` : dans « j'ai payé 15 000
    de transport et j'ai reçu 20 sacs de riz », seul le second segment devient STOCK.
    Sans `raw_segment`, on ne se rabat sur le message entier que s'il n'y a qu'une intention.
    """
    if not intents:
        return intents
    try:
        from apps.femi_agent.schemas.router import RouterIntent
    except Exception:  # pragma: no cover - import défensif
        return intents

    seule = len(intents) == 1
    resultat = []
    for intent in intents:
        agent = getattr(intent, "agent", None)
        segment = (getattr(intent, "raw_segment", None) or "").strip()
        if not segment and seule:
            segment = message_text or ""

        if agent not in _AGENTS_CONVERTIBLES or not message_looks_like_stock(segment):
            resultat.append(intent)
            continue

        data = intent.model_dump() if hasattr(intent, "model_dump") else dict(intent)
        data["agent"] = "STOCK"
        data["action_type"] = "READ" if _est_lecture(segment) else "CREATE"
        data["merge_context"] = False
        data["needs_clarification"] = False
        data["missing_fields"] = []
        try:
            resultat.append(RouterIntent(**data))
        except Exception:
            resultat.append(intent)
    return resultat
