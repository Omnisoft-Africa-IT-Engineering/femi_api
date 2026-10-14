"""Règles déterministes autour de FINANCIAL_ANALYST (sans LLM, sans Django).

Pourquoi : sur « quelle est mon chiffre d'affaire », le LLM redemandait la
période ou, après la réponse « ce mois », calculait un autre indicateur
(bénéfice). Pour les indicateurs simples, le message suffit : on lit
l'indicateur et la période dans le texte, et la période par défaut est
« ce_mois ».

Module volontairement pur (que des chaînes) pour rester testable sans base
ni modèle. Les valeurs de période sont celles de tools/periodes.py
(PERIODES_VALIDES + année YYYY).
"""

import re

PERIODE_PAR_DEFAUT = "ce_mois"

# tool -> mots-clés (texte mis en minuscules, accents conservés)
_INDICATEURS = (
    (
        "get_revenue",
        (
            "chiffre d'affaire",
            "chiffre d’affaire",
            "chiffre d affaire",
            "chiffres d'affaire",
            "chiffres d’affaire",
        ),
    ),
    ("get_expenses", ("dépense", "depense")),
    ("calculate_profit", ("bénéfice", "benefice")),
    ("calculate_margin", ("marge",)),
)

# « ça » ne doit pas être pris pour « ca » : on n'enlève jamais les accents.
_RE_CA = re.compile(r"(?<![\wç])ca(?![\wç])")

# Si le message parle de bilan, de comparaison... on laisse le flux normal.
_MOTS_HORS_REGLE = (
    "bilan",
    "synthèse",
    "synthese",
    "compar",
    "par rapport",
    " vs ",
    "versus",
)

TOOLS_INDICATEURS = frozenset(t for t, _ in _INDICATEURS)

_MOIS = (
    "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    "septembre|octobre|novembre|décembre|decembre"
)
_RE_PERIODE_NON_GEREE = re.compile(
    rf"\b(?:{_MOIS})\b|\btrimestre\b|\bsemestre\b|\bdu\b.+\bau\b|\bentre\b.+\bet\b"
)


def indicateur_unique(texte: str):
    """Nom du tool si UN SEUL indicateur simple est demandé, sinon None."""
    t = (texte or "").lower()
    if any(m in t for m in _MOTS_HORS_REGLE):
        return None

    trouves = []
    for tool, mots in _INDICATEURS:
        if any(m in t for m in mots):
            trouves.append(tool)
    if "get_revenue" not in trouves and _RE_CA.search(t):
        trouves.append("get_revenue")

    return trouves[0] if len(trouves) == 1 else None


def analyser_periode(texte: str):
    """Retourne (periode, non_geree).

    periode : valeur du vocabulaire de tools/periodes.py, ou None si aucune
    période n'est dite. non_geree=True si une période est dite mais qu'on ne
    sait pas la traduire (un mois nommé, « du 1er au 15 »...) : dans ce cas
    on ne devine rien et on laisse le LLM décider.
    """
    t = (texte or "").lower()

    if _RE_PERIODE_NON_GEREE.search(t):
        return None, True
    if "aujourd" in t:
        return "aujourd'hui", False
    if re.search(r"\bhier\b", t):
        return "hier", False
    if re.search(r"semaine\s+(?:derni|pass|préc|prec)", t):
        return "semaine_derniere", False
    if "semaine" in t:
        return "cette_semaine", False
    if re.search(r"mois\s+(?:derni|pass|préc|prec)", t):
        return "mois_dernier", False
    if re.search(r"ann[ée]e\s+(?:derni|pass|préc|prec)", t):
        return "annee_derniere", False
    annee = re.search(r"\b(20\d{2})\b", t)
    if annee:
        return annee.group(1), False
    if re.search(r"cette\s+ann[ée]e|ann[ée]e\s+en\s+cours", t):
        return "cette_annee", False
    if re.search(r"depuis\s+(?:le\s+)?d[ée]but|tout\s+l['’]historique", t):
        return "depuis_debut", False
    if re.search(r"\bce\s+mois?\b|\bmois[-\s]ci\b|\bdu\s+mois\b", t):
        return "ce_mois", False
    return None, False