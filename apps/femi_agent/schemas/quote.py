"""Schémas de sortie de l'agent QUOTE (création de devis).

Deux schémas, comme pour ACCOUNTING :
- QuoteExtractionLLMResult : cible du LLM, montants en float (une grammaire
  de sortie structurée ne supporte pas Decimal) ;
- QuoteExtractionResult : schéma interne, montants en Decimal, converti juste
  après réception via .from_llm_result().

Garde-fou backend : needs_clarification et missing_fields sont recalculés
dans from_llm_result() à partir des données réellement extraites. Le LLM ne
peut donc pas déclarer « complet » un devis sans client, sans ligne ou avec
un prix manquant.
"""

from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import ConfidenceEnum

# Codes missing_fields (traduits en questions par FemiRouterManager).
MISSING_CLIENT = "client_name"
MISSING_ITEMS = "quote_items"
MISSING_PRICE = "quote_price"


class LigneDevisLLMSchema(BaseModel):
    """Une ligne de devis telle que renvoyée par le LLM (float)."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., description="Article ou service, tel que dit par l'utilisateur.")
    quantite: float = Field(1.0, description="Quantité ; 1 si l'utilisateur n'en donne pas.")
    prix_unitaire: Optional[float] = Field(
        default=None,
        description="Prix unitaire écrit par l'utilisateur ; null s'il n'est pas donné.",
    )


class QuoteExtractionLLMResult(BaseModel):
    """Sortie brute du LLM pour une demande de devis."""

    model_config = ConfigDict(extra="forbid")

    client_nom: Optional[str] = Field(default=None, description="Nom du client destinataire ; null si absent.")
    lignes: List[LigneDevisLLMSchema] = Field(default_factory=list)
    needs_clarification: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    confidence: ConfidenceEnum = ConfidenceEnum.MEDIUM


class LigneDevisSchema(BaseModel):
    """Une ligne de devis (schéma interne, Decimal)."""

    model_config = ConfigDict(extra="forbid")

    description: str
    quantite: Decimal = Decimal("1")
    prix_unitaire: Optional[Decimal] = None


class QuoteExtractionResult(BaseModel):
    """Résultat interne de l'extraction d'une demande de devis."""

    model_config = ConfigDict(extra="forbid")

    client_nom: Optional[str] = None
    lignes: List[LigneDevisSchema] = Field(default_factory=list)
    needs_clarification: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    confidence: ConfidenceEnum = ConfidenceEnum.MEDIUM

    @classmethod
    def from_llm_result(cls, llm_result: QuoteExtractionLLMResult) -> "QuoteExtractionResult":
        """Convertit float -> Decimal et recalcule les champs manquants."""
        client = (llm_result.client_nom or "").strip() or None

        lignes: list[LigneDevisSchema] = []
        for ligne in llm_result.lignes:
            description = (ligne.description or "").strip()
            if not description:
                continue
            quantite = Decimal(str(ligne.quantite)) if ligne.quantite and ligne.quantite > 0 else Decimal("1")
            prix = (
                Decimal(str(ligne.prix_unitaire))
                if ligne.prix_unitaire is not None and ligne.prix_unitaire > 0
                else None
            )
            lignes.append(LigneDevisSchema(description=description, quantite=quantite, prix_unitaire=prix))

        missing: list[str] = []
        if client is None:
            missing.append(MISSING_CLIENT)
        if not lignes:
            missing.append(MISSING_ITEMS)
        elif any(ligne.prix_unitaire is None for ligne in lignes):
            missing.append(MISSING_PRICE)

        return cls(
            client_nom=client,
            lignes=lignes,
            needs_clarification=bool(missing),
            missing_fields=missing,
            confidence=llm_result.confidence,
        )
