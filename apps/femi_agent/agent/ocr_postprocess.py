"""Post-traitement du résultat OCR (OcrExtractionResult).

Deux responsabilités, volontairement séparées de l'appel au modèle vision :

1. CONTRÔLE ARITHMÉTIQUE des montants lus sur le document
   (`check_ocr_result`). Le modèle vision ne calcule rien (règle de
   fidélité de ocr_prompt.py) : c'est ici, en Python pur et déterministe,
   qu'on vérifie que ce qui a été lu est cohérent (lignes, HT, TVA, TTC,
   acompte, reste à payer). Aucune valeur n'est jamais corrigée : on
   détecte seulement, pour que l'agent comptable demande une précision
   au lieu de deviner en silence.

2. MISE EN FORME (`build_document_text`) : transforme le résultat
   structuré en texte lisible pour le routeur et l'agent comptable,
   avec le bloc de contrôle et, en référence, le texte brut complet.

Aucune dépendance Django : ce module est testable seul.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

CONTROL_HEADER = "[CONTRÔLE DU DOCUMENT]"
RAW_TEXT_HEADER = "[TEXTE BRUT DU DOCUMENT — référence]"
DOCUMENT_HEADER = "[DOCUMENT SCANNÉ — informations lues sur l'image]"

_NON_BREAKING_SPACES = ("\u00a0", "\u202f", "\u2009")


# ============================================================
# Lecture des montants
# ============================================================

def parse_amount(value: Optional[str]) -> Optional[Decimal]:
    """Convertit un montant écrit sur un document en Decimal, pour le
    CONTRÔLE uniquement (la valeur d'origine n'est jamais modifiée).

    Gère : "2 100 000", "2.100.000", "2,100,000", "12,50", "12.50",
    "2 100 000 FCFA", "1 250,00 F CFA". Retourne None si aucun nombre
    n'est identifiable ou si la lecture est ambiguë.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for nbsp in _NON_BREAKING_SPACES:
        text = text.replace(nbsp, " ")

    # Garde uniquement chiffres, séparateurs, signe et espaces
    cleaned = re.sub(r"[^\d.,\s-]", "", text).strip()
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned or not re.search(r"\d", cleaned):
        return None

    negative = cleaned.startswith("-")
    cleaned = cleaned.lstrip("-")
    if "-" in cleaned:
        return None

    has_dot = "." in cleaned
    has_comma = "," in cleaned

    if has_dot and has_comma:
        # Le dernier séparateur rencontré est le séparateur décimal
        decimal_sep = "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        cleaned = cleaned.replace(thousands_sep, "")
        cleaned = cleaned.replace(decimal_sep, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = cleaned.split(sep)
        if len(parts) > 2:
            # "2.100.000" : séparateur de milliers répété
            if not all(len(p) == 3 for p in parts[1:]):
                return None
            cleaned = "".join(parts)
        else:
            integer_part, fractional_part = parts
            if len(fractional_part) == 3 and 1 <= len(integer_part) <= 3:
                # "2.100" / "1,250" : milliers (contexte F CFA, sans décimales)
                cleaned = integer_part + fractional_part
            else:
                cleaned = integer_part + "." + fractional_part

    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def _tolerance(terms_count: int, *amounts: Decimal) -> Decimal:
    """Tolérance d'arrondi : 1 unité par terme pour des montants entiers
    (F CFA), 0,02 par terme pour des montants décimaux."""
    terms = max(terms_count, 1)
    all_integers = all(a == a.to_integral_value() for a in amounts)
    unit = Decimal("1") if all_integers else Decimal("0.02")
    return unit * terms


def _fmt(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return f"{int(amount):,}".replace(",", " ")
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


# ============================================================
# Fusion des pages d'un document multi-pages (PDF)
# ============================================================

def _is_filled(value) -> bool:
    return value is not None and str(value).strip() != ""


def _merge_block(blocks: list, from_last: bool):
    """Fusionne des blocs Pydantic de même type champ par champ : on garde
    la première valeur non vide (ou la dernière si from_last=True) ;
    les listes (ex. taxes_detail) viennent du premier/dernier bloc qui en
    contient une."""
    ordered = list(reversed(blocks)) if from_last else list(blocks)
    merged = ordered[0].model_copy(deep=True)
    for field_name in type(merged).model_fields:
        current = getattr(merged, field_name)
        if isinstance(current, list):
            if current:
                continue
            for other in ordered[1:]:
                candidate = getattr(other, field_name)
                if candidate:
                    setattr(merged, field_name, list(candidate))
                    break
        elif not _is_filled(current):
            for other in ordered[1:]:
                candidate = getattr(other, field_name)
                if _is_filled(candidate):
                    setattr(merged, field_name, candidate)
                    break
    return merged


def merge_ocr_results(results: list):
    """Fusionne les OcrExtractionResult des pages d'un même document.

    - en_tete et client : on retient, champ par champ, la première valeur
      lue (l'en-tête est en première page) ;
    - lignes d'articles : concaténées dans l'ordre des pages ;
    - totaux : on retient, champ par champ, la dernière valeur lue (les
      totaux sont en fin de document) ;
    - champs illisibles : préfixés par le numéro de page ;
    - texte brut : concaténé avec un repère de page.
    Une seule page → résultat retourné tel quel.
    """
    if not results:
        raise ValueError("Aucun résultat OCR à fusionner.")
    if len(results) == 1:
        return results[0]

    merged = results[0].model_copy(deep=True)
    merged.en_tete = _merge_block([r.en_tete for r in results], from_last=False)
    merged.client = _merge_block([r.client for r in results], from_last=False)
    merged.totaux = _merge_block([r.totaux for r in results], from_last=True)

    merged.lignes_articles = [
        ligne for r in results for ligne in (r.lignes_articles or [])
    ]

    illisibles: list[str] = []
    for page_number, r in enumerate(results, start=1):
        for champ in r.champs_illisibles or []:
            if champ and champ.strip():
                illisibles.append(f"page {page_number} : {champ.strip()}")
    merged.champs_illisibles = illisibles

    raw_pages = []
    for page_number, r in enumerate(results, start=1):
        text = (r.texte_brut_complet or "").strip()
        if text:
            raw_pages.append(f"--- Page {page_number} ---\n{text}")
    merged.texte_brut_complet = "\n\n".join(raw_pages)
    return merged


# ============================================================
# Contrôle arithmétique
# ============================================================

@dataclass
class OcrCheckReport:
    anomalies: list[str] = field(default_factory=list)
    checks_run: int = 0

    @property
    def is_consistent(self) -> bool:
        return self.checks_run > 0 and not self.anomalies


def check_ocr_result(ocr_result) -> OcrCheckReport:
    """Contrôle la cohérence arithmétique d'un OcrExtractionResult.

    Ne vérifie que ce qui peut l'être avec les valeurs lues : une
    vérification est ignorée si un de ses montants est absent ou
    illisible. Retourne un rapport ; ne modifie jamais le résultat.
    """
    report = OcrCheckReport()
    totaux = ocr_result.totaux
    lignes = ocr_result.lignes_articles or []

    total_ht = parse_amount(totaux.total_ht)
    total_ttc = parse_amount(totaux.total_ttc)
    # Un champ "tva" qui contient un taux ("18 %") n'est pas un montant
    tva = None if (totaux.tva and "%" in totaux.tva) else parse_amount(totaux.tva)
    remise_totale = parse_amount(totaux.remise_totale)
    acompte = parse_amount(totaux.acompte_verse)
    reste = parse_amount(totaux.reste_a_payer)

    # TVA : montant unique, sinon somme des taxes détaillées
    if tva is None and totaux.taxes_detail:
        taxes = [parse_amount(t.montant) for t in totaux.taxes_detail]
        if taxes and all(t is not None for t in taxes):
            tva = sum(taxes, Decimal(0))

    # --- 1. Chaque ligne : quantité × prix unitaire = prix total ---
    line_totals: list[Decimal] = []
    all_lines_have_total = bool(lignes)
    for index, ligne in enumerate(lignes, start=1):
        quantite = parse_amount(ligne.quantite)
        prix_unitaire = parse_amount(ligne.prix_unitaire)
        prix_total = parse_amount(ligne.prix_total)
        remise_ligne = parse_amount(ligne.remise)

        if prix_total is None:
            all_lines_have_total = False
        else:
            line_totals.append(prix_total)

        if quantite is None or prix_unitaire is None or prix_total is None:
            continue
        if ligne.remise and remise_ligne is None:
            continue  # remise écrite mais illisible : on ne conclut pas
        if remise_ligne is not None or ligne.taux_tva:
            continue  # remise/TVA de ligne : le calcul dépend du document

        expected = quantite * prix_unitaire
        report.checks_run += 1
        if abs(expected - prix_total) > _tolerance(1, expected, prix_total):
            libelle = (ligne.designation or f"ligne {index}").strip()
            report.anomalies.append(
                f"Ligne « {libelle} » : quantité {_fmt(quantite)} × prix unitaire "
                f"{_fmt(prix_unitaire)} = {_fmt(expected)}, mais le total lu est "
                f"{_fmt(prix_total)}."
            )

    # --- 2. Somme des lignes = total HT (ou TTC si pas de HT ni de TVA) ---
    if all_lines_have_total and len(line_totals) >= 1:
        lines_sum = sum(line_totals, Decimal(0))
        if remise_totale is not None:
            lines_sum -= remise_totale
        reference_label, reference = None, None
        if total_ht is not None:
            reference_label, reference = "total HT", total_ht
        elif total_ttc is not None and tva is None:
            reference_label, reference = "total TTC", total_ttc
        if reference is not None:
            report.checks_run += 1
            if abs(lines_sum - reference) > _tolerance(len(line_totals), lines_sum, reference):
                report.anomalies.append(
                    f"La somme des lignes ({_fmt(lines_sum)}) ne correspond pas au "
                    f"{reference_label} lu ({_fmt(reference)})."
                )

    # --- 3. HT + TVA = TTC ---
    if total_ht is not None and tva is not None and total_ttc is not None:
        expected_ttc = total_ht + tva
        report.checks_run += 1
        if abs(expected_ttc - total_ttc) > _tolerance(2, expected_ttc, total_ttc):
            report.anomalies.append(
                f"Total HT ({_fmt(total_ht)}) + TVA ({_fmt(tva)}) = {_fmt(expected_ttc)}, "
                f"mais le total TTC lu est {_fmt(total_ttc)}."
            )

    # --- 4. TTC − acompte = reste à payer ---
    if total_ttc is not None and acompte is not None and reste is not None:
        expected_reste = total_ttc - acompte
        report.checks_run += 1
        if abs(expected_reste - reste) > _tolerance(2, expected_reste, reste):
            report.anomalies.append(
                f"Total TTC ({_fmt(total_ttc)}) − acompte ({_fmt(acompte)}) = "
                f"{_fmt(expected_reste)}, mais le reste à payer lu est {_fmt(reste)}."
            )

    # --- 5. Montants écrits mais non interprétables ---
    for label, raw, parsed in (
        ("total HT", totaux.total_ht, total_ht),
        ("TVA", None if (totaux.tva and "%" in totaux.tva) else totaux.tva, tva),
        ("total TTC", totaux.total_ttc, total_ttc),
        ("reste à payer", totaux.reste_a_payer, reste),
    ):
        if raw and parsed is None:
            report.anomalies.append(
                f"Le {label} est présent mais son montant n'est pas interprétable : « {raw} »."
            )

    return report


# ============================================================
# Mise en forme pour le routeur / l'agent comptable
# ============================================================

def _join(parts: list[Optional[str]], sep: str = " | ") -> str:
    return sep.join(p.strip() for p in parts if p and str(p).strip())


def extract_control_anomalies(text: Optional[str]) -> list[str]:
    """Retrouve les incohérences listées dans le bloc [CONTRÔLE DU DOCUMENT]
    d'un texte produit par build_document_text(). Liste vide s'il n'y a ni
    bloc ni incohérence. Sert à citer les vrais chiffres dans la question de
    précision posée à l'utilisateur."""
    if not text or CONTROL_HEADER not in text:
        return []
    block = text.split(CONTROL_HEADER, 1)[1]
    block = block.split("\n\n", 1)[0]
    return [
        line[2:].strip()
        for line in block.splitlines()
        if line.startswith("- ") and line[2:].strip()
    ]


def build_document_text(ocr_result) -> str:
    """Texte unique transmis au routeur et à l'agent comptable :
    informations structurées, bloc de contrôle, puis texte brut complet
    en référence (pour tout ce que le schéma n'a pas capturé)."""
    en_tete = ocr_result.en_tete
    client = ocr_result.client
    totaux = ocr_result.totaux
    lines: list[str] = []

    # --- En-tête / émetteur ---
    if en_tete.type_document:
        lines.append(f"Type de document : {en_tete.type_document}")
    emetteur = _join([
        en_tete.nom_commercant, en_tete.adresse, en_tete.telephone,
        en_tete.email,
        f"identifiant fiscal : {en_tete.identifiant_fiscal}" if en_tete.identifiant_fiscal else None,
    ])
    if emetteur:
        lines.append(f"Émetteur : {emetteur}")

    # --- Client ---
    client_line = _join([
        client.nom, client.adresse, client.telephone, client.email,
        f"identifiant fiscal : {client.identifiant_fiscal}" if client.identifiant_fiscal else None,
    ])
    if client_line:
        lines.append(f"Client : {client_line}")

    if en_tete.numero_facture_recu:
        lines.append(f"Référence : {en_tete.numero_facture_recu}")
    if en_tete.date:
        lines.append(f"Date : {en_tete.date}")
    if en_tete.date_echeance:
        lines.append(f"Échéance : {en_tete.date_echeance}")
    if en_tete.devise:
        lines.append(f"Devise : {en_tete.devise}")

    # --- Articles ---
    articles: list[str] = []
    for ligne in ocr_result.lignes_articles or []:
        if not ligne.designation:
            continue
        detail = ligne.designation
        sub: list[str] = []
        if ligne.quantite:
            sub.append(f"quantité : {ligne.quantite}")
        if ligne.prix_unitaire:
            sub.append(f"prix unitaire : {ligne.prix_unitaire}")
        if ligne.remise:
            sub.append(f"remise : {ligne.remise}")
        if ligne.taux_tva:
            sub.append(f"TVA : {ligne.taux_tva}")
        if sub:
            detail += f" ({', '.join(sub)})"
        if ligne.prix_total:
            detail += f" — total : {ligne.prix_total}"
        articles.append(f"- {detail}")
    if articles:
        lines.append("Articles :")
        lines.extend(articles)

    # --- Totaux ---
    if totaux.total_ht:
        lines.append(f"Total HT : {totaux.total_ht}")
    if totaux.remise_totale:
        lines.append(f"Remise totale : {totaux.remise_totale}")
    if totaux.tva:
        lines.append(f"TVA : {totaux.tva}")
    for taxe in totaux.taxes_detail or []:
        taxe_line = _join([taxe.libelle, taxe.taux, taxe.montant], sep=" ")
        if taxe_line:
            lines.append(f"Taxe : {taxe_line}")
    if totaux.total_ttc:
        lines.append(f"Total TTC : {totaux.total_ttc}")
    if totaux.acompte_verse:
        lines.append(f"Acompte versé : {totaux.acompte_verse}")
    if totaux.reste_a_payer:
        lines.append(f"Reste à payer : {totaux.reste_a_payer}")
    if totaux.moyen_de_paiement:
        lines.append(f"Moyen de paiement : {totaux.moyen_de_paiement}")

    blocks: list[str] = []
    if lines:
        blocks.append("\n".join([DOCUMENT_HEADER, *lines]))

    # --- Contrôle ---
    report = check_ocr_result(ocr_result)
    control_lines: list[str] = []
    if report.anomalies:
        control_lines.append(
            "Incohérences détectées entre les montants lus (rien n'a été corrigé) :"
        )
        control_lines.extend(f"- {a}" for a in report.anomalies)
    elif report.is_consistent:
        control_lines.append("Contrôle des montants : cohérent.")
    illisibles = [c for c in (ocr_result.champs_illisibles or []) if c and c.strip()]
    if illisibles:
        control_lines.append(
            "Champs présents sur le document mais illisibles : " + ", ".join(illisibles) + "."
        )
    if control_lines:
        blocks.append("\n".join([CONTROL_HEADER, *control_lines]))

    # --- Texte brut en référence ---
    raw = (ocr_result.texte_brut_complet or "").strip()
    if raw:
        blocks.append(f"{RAW_TEXT_HEADER}\n{raw}")

    return "\n\n".join(blocks)