"""Schémas Pydantic pour la sortie structurée du ROUTER_PROMPT (Point 7).

Reflète exactement le format JSON défini dans router_prompt.py (section 22).
Aucun champ ajouté ou renommé par rapport au prompt.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from apps.femi_agent.schemas.accounting_modify import AccountingModifyResult
from apps.femi_agent.schemas.tool_loop import FinalAnswerOutput, ToolSelectionOutput, ValidationOutput
from apps.femi_agent.schemas.customer import CustomerExtractionOutput
from apps.femi_agent.schemas.accounting import AccountingExtractionResult
from apps.femi_agent.schemas.quote import QuoteExtractionResult  # Import du schéma de devis

# Valeurs autorisées, mises à jour avec QUOTE
RouterAgent = Literal[
    "ACCOUNTING",
    "ACCOUNTING_MODIFY",
    "FINANCIAL_ANALYST",
    "CUSTOMER",
    "QUOTE",  # Ajout de l'agent devis
    "SETTINGS",
    "UNKNOWN",
]
RouterActionType = Literal["READ", "CREATE", "UPDATE", "DELETE"]


class RouterIntent(BaseModel):
    intent_id: str
    agent: RouterAgent
    action_type: RouterActionType
    confidence: float = Field(ge=0.0, le=1.0)
    is_sensitive: bool
    requires_confirmation: bool
    requires_tool: bool
    needs_clarification: bool
    missing_fields: list[str] = Field(default_factory=list)
    merge_context: bool
    context_resolved: bool
    raw_segment: str


class RouterOutput(BaseModel):
    intents: list[RouterIntent]


class RouterProcessResult(BaseModel):
    """
    Contrat de réponse standardisé pour le nouveau pipeline (Router + agents).
    """

    success: bool = Field(description="Statut du succès du routage.")
    message: str = Field(description="Message destiné aux logs ou à l'utilisateur en cas d'échec.")
    router_output: Optional[RouterOutput] = Field(
        default=None, description="Sortie brute du Router (liste des intents détectés)."
    )
    operation_ids: list[str] = Field(
        default_factory=list, description="UUID des opérations créées/modifiées."
    )
    needs_clarification: bool = Field(
        default=False, description="True si au moins un intent nécessite une clarification utilisateur."
    )
    missing_fields: list[str] = Field(
        default_factory=list, description="Champs manquants agrégés, si needs_clarification=True."
    )
    
    accounting_results: list[AccountingExtractionResult] = Field(
        default_factory=list,
        description="Résultats d'extraction accounting.",
    )

    financial_analyst_results: list[ToolSelectionOutput | FinalAnswerOutput] = Field(
        default_factory=list,
        description="Résultats FINANCIAL_ANALYST.",
    )
    
    accounting_modify_results: list[AccountingModifyResult] = Field(
        default_factory=list,
        description="Résultats ACCOUNTING_MODIFY.",
    )

    customer_results: list[ValidationOutput | CustomerExtractionOutput | ToolSelectionOutput | FinalAnswerOutput] = Field(
        default_factory=list,
        description="Résultats CUSTOMER.",
    )

    quote_results: list[QuoteExtractionResult] = Field(
        default_factory=list,
        description="Résultats QUOTE, un par intent QUOTE traité, dans le même ordre que router_output.intents.",
    )