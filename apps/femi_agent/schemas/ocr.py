"""Schémas Pydantic pour la sortie structurée d'OCR_PROMPT (agent OCR vision).

Reflète exactement le format JSON défini dans ocr_prompt.py (section
"SCHÉMA DE SORTIE"). Aucun champ ajouté ou renommé par rapport au prompt.

Tous les champs numériques (quantite, prix_unitaire, prix_total, total_ht,
tva, total_ttc) sont typés en Optional[str] et non en Decimal/float : le
prompt exige de préserver la représentation visuelle exacte du document
(ex. "12,50" doit rester "12,50"), jamais une valeur reconstruite ou
recalculée. Même précaution que pour AccountingExtractionLLMResult
(schemas/accounting.py) vis-à-vis du bug de conversion GBNF côté Ollama.
"""

from typing import Optional

from pydantic import BaseModel


class OcrEnTeteSchema(BaseModel):
    nom_commercant: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    date: Optional[str] = None
    numero_facture_recu: Optional[str] = None


class OcrLigneArticleSchema(BaseModel):
    designation: Optional[str] = None
    quantite: Optional[str] = None
    prix_unitaire: Optional[str] = None
    prix_total: Optional[str] = None


class OcrTotauxSchema(BaseModel):
    total_ht: Optional[str] = None
    tva: Optional[str] = None
    total_ttc: Optional[str] = None
    moyen_de_paiement: Optional[str] = None


class OcrExtractionResult(BaseModel):
    en_tete: OcrEnTeteSchema
    lignes_articles: list[OcrLigneArticleSchema]
    totaux: OcrTotauxSchema
    texte_brut_complet: str
