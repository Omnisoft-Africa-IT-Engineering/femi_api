import json
import os
import re
from decimal import Decimal
from apps.femi_agent.schemas import ParsedOperationSchema
from apps.femi_agent.prompts import SYSTEM_FINANCIAL_EXTRACTION_PROMPT


def run_ai_extraction(text_input: str) -> ParsedOperationSchema:
    """
    Analyse le texte utilisateur via l'IA (Gemini) et retourne un objet ParsedOperationSchema.
    Bascule automatiquement sur un analyseur local de secours si la clé API n'est pas présente.
    """
    api_key = os.getenv("GEMINI_API_KEY")

    if api_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")

            prompt = f"{SYSTEM_FINANCIAL_EXTRACTION_PROMPT}\n\nTexte utilisateur :\n\"{text_input}\""
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            data = json.loads(response.text)
            return ParsedOperationSchema(**data)
        except Exception:
            return _fallback_parser(text_input)
    else:
        return _fallback_parser(text_input)


def _fallback_parser(text_input: str) -> ParsedOperationSchema:
    """Analyseur local de secours amélioré."""
    text_lower = text_input.lower()

    is_recette = any(w in text_lower for w in ["vente", "reçu", "recette", "gain", "encaissement", "client"])
    transaction_type = "RECETTE" if is_recette else "DEPENSE"

    # Extraction améliorée du montant (cherche les nombres avec FCFA / F ou prend le plus grand chiffre)
    amount_matches = re.findall(r'(\d+(?:[\.,]\d+)?)\s*(?:fcfa|cfa|f)?', text_input, re.IGNORECASE)
    numbers = [Decimal(n.replace(",", ".")) for n in re.findall(r'\d+', text_input.replace(" ", ""))]
    
    if numbers:
        # Si plusieurs nombres, on prend la valeur maximale pour éviter de confondre les quantités (ex: 2 sacs) et le montant (45000)
        amount = max(numbers)
    else:
        amount = Decimal("0")

    payment_method = "CASH"
    if any(w in text_lower for w in ["wave", "tmoney", "flooz", "orange", "om", "momo"]):
        payment_method = "MOBILE_MONEY"
    elif any(w in text_lower for w in ["virement", "banque", "chèque", "rib"]):
        payment_method = "BANK_TRANSFER"

    category = "Autre"
    if any(w in text_lower for w in ["carburant", "essence", "taxi", "transport"]):
        category = "Transport"
    elif any(w in text_lower for w in ["repas", "manger", "restaurant", "déjeuner"]):
        category = "Restauration"
    elif any(w in text_lower for w in ["loyer", "bureau", "local"]):
        category = "Loyer & Charges"
    elif is_recette:
        category = "Vente de services / produits"

    return ParsedOperationSchema(
        transaction_type=transaction_type,
        amount_ttc=amount,
        amount_ht=amount,
        tax_amount=Decimal("0"),
        currency="XOF",
        category=category,
        payment_method=payment_method,
        description=text_input,
        confidence_score=0.80
    )