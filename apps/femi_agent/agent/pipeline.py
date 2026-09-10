import logging
import re
from decimal import Decimal
from typing import Optional, Tuple

from apps.femi_agent.agent.executor import (
    analyze_accounting_text,
    aanalyze_accounting_text,
    AgentExecutionError,
)
from apps.femi_agent.parsers.audio_parser import transcribe_audio
from apps.femi_agent.parsers.ocr_parser import extract_text_from_image
from apps.femi_agent.schemas import ParsedOperationSchema, PaymentMethodEnum

logger = logging.getLogger(__name__)

CATEGORY_KEYWORDS = {
    "Transport/Carburant": ["carburant", "essence", "gazole", "taxi", "transport", "zémidjan", "déplacement"],
    "Restauration": ["repas", "manger", "restaurant", "déjeuner", "dîner", "café"],
    "Loyer": ["loyer", "bureau", "local", "bail"],
    "Communication/Data": ["crédit", "forfait", "internet", "data", "recharge"],
}

PAYMENT_METHOD_KEYWORDS = {
    PaymentMethodEnum.MOBILE_MONEY: ["wave", "tmoney", "flooz", "orange", "om", "momo", "moov"],
    PaymentMethodEnum.BANK_TRANSFER: ["virement", "banque", "chèque", "rib", "transfert"],
    PaymentMethodEnum.CARD: ["carte", "visa", "mastercard"],
}

LOAN_GIVEN_KEYWORDS = ["j'ai prêté", "prêté à", "avancé à", "j'ai avancé"]
LOAN_RECEIVED_KEYWORDS = ["m'a prêté", "j'ai emprunté", "emprunté à", "reçu en prêt"]
FALLBACK_CONFIDENCE_SCORE = 0.50
MIN_PLAUSIBLE_AMOUNT = Decimal("50")
EXCLUDED_YEAR_LIKE_VALUE = Decimal("2026")


def run_ai_extraction(
    text_input: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    audio_bytes: Optional[bytes] = None,
    tenant_name: Optional[str] = None,
    secteur_nom: Optional[str] = None,
    catalogue: Optional[list] = None,
) -> Tuple[ParsedOperationSchema, str]:
    """
    Point d'entrée SYNCHRONE.

    Args:
        tenant_name: Nom de l'entreprise de l'utilisateur (ex: entreprise.nom),
            transmis au LLM pour lever l'ambiguïté RECETTE/DEPENSE sur les
            documents mentionnant plusieurs entreprises (factures fournisseur
            notamment). Optionnel — si absent, l'exécuteur applique sa propre
            valeur de repli.

    Returns:
        Tuple (résultat structuré, texte brut réellement utilisé pour l'extraction
        — texte utilisateur + OCR + transcription audio combinés). Ce texte brut
        doit être stocké tel quel pour la traçabilité (raw_input_text), il ne doit
        JAMAIS être remplacé par `parsed_data.description` qui est une reformulation LLM.
    """
    combined_text = _extract_and_combine_text(text_input, image_bytes, audio_bytes,)

    try:
        parsed_data = analyze_accounting_text(combined_text, tenant_name, secteur_nom, catalogue)
    except AgentExecutionError as e:
        logger.warning("[Pipeline] LLM indisponible, bascule sur fallback Regex : %s", e)
        parsed_data = _fallback_parser(combined_text)
    except Exception:
        logger.exception("[Pipeline] Erreur inattendue LLM")
        parsed_data = _fallback_parser(combined_text)

    return parsed_data, combined_text


async def arun_ai_extraction(
    text_input: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    audio_bytes: Optional[bytes] = None,
    tenant_name: Optional[str] = None,
    secteur_nom: Optional[str] = None,
    catalogue: Optional[list] = None,
) -> Tuple[ParsedOperationSchema, str]:
    """Point d'entrée ASYNCHRONE. Voir `run_ai_extraction` pour le contrat de retour et `tenant_name`."""
    combined_text = _extract_and_combine_text(text_input, image_bytes, audio_bytes)

    try:
        parsed_data = await aanalyze_accounting_text(combined_text, tenant_name, secteur_nom, catalogue)
    except AgentExecutionError as e:
        logger.warning("[Pipeline Async] LLM indisponible, bascule sur fallback Regex : %s", e)
        parsed_data = _fallback_parser(combined_text)
    except Exception:
        logger.exception("[Pipeline Async] Erreur inattendue LLM")
        parsed_data = _fallback_parser(combined_text)

    return parsed_data, combined_text


# --- HELPERS PRIVÉS ---

def _extract_and_combine_text(
    text_input: Optional[str],
    image_bytes: Optional[bytes],
    audio_bytes: Optional[bytes],
) -> str:
    """Centralise la conversion des formats multimédias en une seule chaîne de texte brut."""
    extracted_parts = []

    if text_input and text_input.strip():
        extracted_parts.append(text_input.strip())

    if image_bytes:
        try:
            ocr_text = extract_text_from_image(image_bytes)
            if ocr_text:
                extracted_parts.append(ocr_text)
        except Exception:
            logger.exception("[OCR] Échec du traitement image")

    if audio_bytes:
        try:
            audio_text = transcribe_audio(audio_bytes)
            if audio_text:
                extracted_parts.append(audio_text)
        except Exception:
            logger.exception("[Audio] Échec transcription Whisper")

    combined_text = "\n".join(extracted_parts).strip()

    if not combined_text:
        raise AgentExecutionError("Aucune donnée textuelle ou multimédia exploitable.")

    return combined_text


def _detect_category(text_lower: str, is_recette: bool) -> str:
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "Vente de marchandises" if is_recette else "Divers"


def _detect_payment_method(text_lower: str) -> PaymentMethodEnum:
    for method, keywords in PAYMENT_METHOD_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return method
    return PaymentMethodEnum.CASH


def _detect_transaction_type(text_lower: str) -> str:
    """Détermine le type de transaction par mots-clés, prêts en priorité (plus spécifiques que RECETTE/DEPENSE)."""
    if any(kw in text_lower for kw in LOAN_RECEIVED_KEYWORDS):
        return "PRET_RECU"
    if any(kw in text_lower for kw in LOAN_GIVEN_KEYWORDS):
        return "PRET_DONNE"
    is_recette = any(w in text_lower for w in ["vente", "reçu", "recette", "gain", "encaissement", "client", "vendu"])
    return "RECETTE" if is_recette else "DEPENSE"

def _fallback_parser(text_input: str) -> ParsedOperationSchema:
    """Analyseur dégradé basé sur des expressions régulières (LLM hors-ligne)."""
    text_lower = text_input.lower()

    transaction_type = _detect_transaction_type(text_lower)
    is_recette = transaction_type == "RECETTE"  # conservé : utilisé plus bas par _detect_category

    raw_numbers = re.findall(r"\b\d+(?:[\s.,]\d+)*\b", text_input)
    clean_numbers = []

    for num_str in raw_numbers:
        cleaned = re.sub(r"[\s.]", "", num_str).replace(",", ".")
        try:
            val = Decimal(cleaned)
            if val >= MIN_PLAUSIBLE_AMOUNT and val != EXCLUDED_YEAR_LIKE_VALUE:
                clean_numbers.append(val)
        except Exception:
            continue

    amount = max(clean_numbers) if clean_numbers else Decimal("0.00")

    return ParsedOperationSchema(
        transaction_type=transaction_type,
        amount_ttc=amount,
        amount_ht=amount,
        tax_amount=Decimal("0.00"),
        currency="XOF",
        category=_detect_category(text_lower, is_recette),
        payment_method=_detect_payment_method(text_lower),
        description=text_input[:255],
        confidence_score=FALLBACK_CONFIDENCE_SCORE,
    )