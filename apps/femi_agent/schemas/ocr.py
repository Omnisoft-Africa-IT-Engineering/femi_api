"""Schémas Pydantic pour la sortie structurée d'OCR_PROMPT (agent OCR vision).

Reflète exactement le format JSON défini dans ocr_prompt.py (section
"SCHÉMA DE SORTIE"). Aucun champ ajouté ou renommé par rapport au prompt.

Tous les champs numériques (quantite, prix_unitaire, prix_total, total_ht,
tva, total_ttc, remise, acompte, reste_a_payer...) sont typés en
Optional[str] et non en Decimal/float : le prompt exige de préserver la
représentation visuelle exacte du document (ex. "12,50" doit rester
"12,50"), jamais une valeur reconstruite ou recalculée. Même précaution
que pour AccountingExtractionLLMResult (schemas/accounting.py)
vis-à-vis du bug de conversion GBNF côté Ollama.

Évolution (2026-10-06) : le schéma est enrichi pour couvrir une vraie
facture (client, devise, échéance, remises, acompte, reste à payer,
identifiants fiscaux, détail des taxes). Compatibilité : tous les champs
d'origine sont conservés avec le même nom, et tous les nouveaux champs
ont une valeur par défaut (None / liste vide) — une réponse du modèle
qui ne les contient pas reste valide.

Convention : `en_tete` décrit l'ÉMETTEUR du document (celui qui vend /
facture) et les métadonnées du document ; `client` décrit le destinataire
(« Facturé à », « Client », « Destinataire »).
"""

from typing import Optional

from pydantic import BaseModel, Field


class OcrEnTeteSchema(BaseModel):
    # Champs d'origine (émetteur / commerçant)
    nom_commercant: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    date: Optional[str] = None
    numero_facture_recu: Optional[str] = None
    # Ajouts
    email: Optional[str] = None
    identifiant_fiscal: Optional[str] = None  # NIF / IFU / RCCM / n° TVA tel qu'écrit
    type_document: Optional[str] = None  # libellé tel qu'écrit : "Facture", "Pro forma", "Reçu"...
    date_echeance: Optional[str] = None
    devise: Optional[str] = None  # uniquement si visible sur le document


class OcrClientSchema(BaseModel):
    nom: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    email: Optional[str] = None
    identifiant_fiscal: Optional[str] = None


class OcrLigneArticleSchema(BaseModel):
    designation: Optional[str] = None
    quantite: Optional[str] = None
    prix_unitaire: Optional[str] = None
    prix_total: Optional[str] = None
    # Ajouts
    remise: Optional[str] = None
    taux_tva: Optional[str] = None


class OcrTaxeSchema(BaseModel):
    libelle: Optional[str] = None
    taux: Optional[str] = None
    montant: Optional[str] = None


class OcrTotauxSchema(BaseModel):
    total_ht: Optional[str] = None
    tva: Optional[str] = None
    total_ttc: Optional[str] = None
    moyen_de_paiement: Optional[str] = None
    # Ajouts
    remise_totale: Optional[str] = None
    acompte_verse: Optional[str] = None
    reste_a_payer: Optional[str] = None
    taxes_detail: list[OcrTaxeSchema] = Field(default_factory=list)


class OcrExtractionResult(BaseModel):
    en_tete: OcrEnTeteSchema
    client: OcrClientSchema = Field(default_factory=OcrClientSchema)
    lignes_articles: list[OcrLigneArticleSchema] = Field(default_factory=list)
    totaux: OcrTotauxSchema = Field(default_factory=OcrTotauxSchema)
    # Noms des champs vus sur le document mais illisibles / incertains
    # (ex. "totaux.total_ttc", "en_tete.date"). Vide si tout est net.
    champs_illisibles: list[str] = Field(default_factory=list)
    texte_brut_complet: str = ""