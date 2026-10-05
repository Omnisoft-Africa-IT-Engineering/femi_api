"""
Schémas de sortie pour le module de gestion des devis (QuoteExecutor).
Gère la structure des lignes d'articles, du client et l'état de clarification.

Utilise ConfidenceEnum (common.py) pour la cohérence avec les autres agents.
"""

from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import ConfidenceEnum


class LigneDevisSchema(BaseModel):
    """Représentation d'une ligne d'article ou de service dans un devis (Decimal)."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., description="Description de l'article ou du service")
    quantite: float = Field(1.0, description="Quantité commandée")
    prix_unitaire: Decimal = Field(Decimal("0.0"), description="Prix unitaire de l'article en Decimal")


class LigneDevisLLMSchema(BaseModel):
    """Représentation d'une ligne d'article pour le LLM (montants en float pour Ollama/GBNF)."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., description="Description de l'article ou du service")
    quantite: float = Field(1.0, description="Quantité commandée")
    prix_unitaire: float = Field(0.0, description="Prix unitaire de l'article en float")


class QuoteExtractionLLMResult(BaseModel):
    """Résultat brut de l'extraction LLM avec des floats pour la compatibilité Ollama/GBNF."""

    model_config = ConfigDict(extra="forbid")

    client_nom: Optional[str] = Field(
        default=None,
        description="Nom texte brut du client ou de l'entreprise destinataire du devis.",
    )
    lignes: List[LigneDevisLLMSchema] = Field(
        default_factory=list, description="Liste des articles ou services demandés."
    )
    needs_clarification: bool = Field(
        default=False,
        description="True si des informations obligatoires manquent (ex: client ou prix).",
    )
    missing_fields: List[str] = Field(
        default_factory=list, description="Liste des champs manquants nécessitant une relance."
    )
    confidence: ConfidenceEnum = ConfidenceEnum.MEDIUM


class QuoteExtractionResult(BaseModel):
    """Résultat interne de l'extraction pour la création ou la modification d'un devis (Decimal)."""

    model_config = ConfigDict(extra="forbid")

    client_nom: Optional[str] = Field(
        default=None,
        description="Nom texte brut du client ou de l'entreprise destinataire du devis.",
    )
    lignes: List[LigneDevisSchema] = Field(
        default_factory=list, description="Liste des articles ou services demandés."
    )
    needs_clarification: bool = Field(
        default=False,
        description="True si des informations obligatoires manquent (ex: client ou prix).",
    )
    missing_fields: List[str] = Field(
        default_factory=list, description="Liste des champs manquants nécessitant une relance."
    )
    confidence: ConfidenceEnum = ConfidenceEnum.MEDIUM

    @classmethod
    def from_llm_result(cls, llm_result: QuoteExtractionLLMResult) -> "QuoteExtractionResult":
        """Convertit le résultat LLM (float) vers le schéma interne (Decimal)."""
        return cls(
            client_nom=llm_result.client_nom,
            lignes=[
                LigneDevisSchema(
                    description=l.description,
                    quantite=l.quantite,
                    prix_unitaire=Decimal(str(l.prix_unitaire)),
                )
                for l in llm_result.lignes
            ],
            needs_clarification=llm_result.needs_clarification,
            missing_fields=llm_result.missing_fields,
            confidence=llm_result.confidence,
        )