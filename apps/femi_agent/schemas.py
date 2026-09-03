from decimal import Decimal
from typing import Optional, Any, Any
from pydantic import BaseModel, Field, ConfigDict, ConfigDict


class LLMExtractionSchema(BaseModel):
    """Schéma de sortie STRICTEMENT pour le LLM (Ollama structured output).
    Utilise float au lieu de Decimal car Ollama ne parse pas correctement
    la grammaire GBNF générée pour Decimal. Converti en ParsedOperationSchema
    juste après l'appel LLM (voir executor.py)."""
    transaction_type: str = Field(description="'RECETTE' ou 'DEPENSE'")
    amount_ttc: float = Field(description="Montant total de la transaction")
    amount_ht: Optional[float] = Field(default=None, description="Montant HT si identifiable")
    tax_amount: Optional[float] = Field(default=None, description="Montant de la TVA/Taxe")
    currency: str = Field(default="XOF", description="Devise (ex: XOF, EUR, USD)")
    category: str = Field(description="Catégorie (ex: Loyer, Transport, Restauration, Vente, etc.)")
    vendor_or_client: Optional[str] = Field(default=None, description="Nom du client ou du fournisseur")
    payment_method: str = Field(default="CASH", description="Mode de paiement: CASH, MOBILE_MONEY, BANK_TRANSFER, CARD, OTHER")
    transaction_date: Optional[str] = Field(default=None, description="Date au format YYYY-MM-DD")
    description: str = Field(description="Description synthétique de l'opération")
    confidence_score: float = Field(default=1.0, description="Score de confiance entre 0.0 et 1.0")


class ParsedOperationSchema(BaseModel):
    """Structure de données standardisée retournée par l'analyse IA."""
    transaction_type: str = Field(description="'RECETTE' ou 'DEPENSE'")
    amount_ttc: Decimal = Field(description="Montant total de la transaction")
    amount_ht: Optional[Decimal] = Field(default=None, description="Montant HT si identifiable")
    tax_amount: Optional[Decimal] = Field(default=None, description="Montant de la TVA/Taxe")
    currency: str = Field(default="XOF", description="Devise (ex: XOF, EUR, USD)")
    category: str = Field(description="Catégorie (ex: Loyer, Transport, Restauration, Vente, etc.)")
    vendor_or_client: Optional[str] = Field(default=None, description="Nom du client ou du fournisseur")
    payment_method: str = Field(default="CASH", description="Mode de paiement: CASH, MOBILE_MONEY, BANK_TRANSFER, CARD, OTHER")
    transaction_date: Optional[str] = Field(default=None, description="Date au format YYYY-MM-DD")
    description: str = Field(description="Description synthétique de l'opération")
    confidence_score: float = Field(default=1.0, description="Score de confiance entre 0.0 et 1.0")


class ProcessResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    """Résultat renvoyé par FemiAgentManager à WhatsApp ou l'App Mobile."""
    success: bool
    operation_id: Optional[str] = None
    message: str
    parsed_data: Optional[ParsedOperationSchema] = None
    operation_instance: Optional[Any] = None
