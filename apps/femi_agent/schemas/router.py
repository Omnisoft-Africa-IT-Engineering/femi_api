"""Schémas Pydantic pour la sortie structurée du ROUTER_PROMPT (Point 7).

Reflète exactement le format JSON défini dans router_prompt.py (section 22).
Aucun champ ajouté ou renommé par rapport au prompt.
"""

from typing import Literal,Optional

from pydantic import BaseModel, Field

from apps.femi_agent.schemas.accounting_modify import AccountingModifyResult
from apps.femi_agent.schemas.tool_loop import FinalAnswerOutput, ToolSelectionOutput
from apps.femi_agent.schemas.customer import CustomerExtractionOutput
from apps.femi_agent.schemas.tool_loop import ValidationOutput

from apps.femi_agent.schemas.accounting import AccountingExtractionResult

# Valeurs autorisées, copiées telles quelles depuis router_prompt.py (sections 2 et 3)
RouterAgent = Literal[
    "ACCOUNTING",
    "ACCOUNTING_MODIFY",
    "FINANCIAL_ANALYST",
    "CUSTOMER",
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

    Équivalent de ProcessResult (apps/femi_agent/schemas/common.py), mais
    dédié au nouveau système : ne pas fusionner avec ProcessResult, pour ne
    pas coupler les deux pipelines (Option B).

        Portée actuelle : couvre le résultat du routage (RouterExecutor) ET le
    dispatch vers les agents spécialisés déjà branchés — ACCOUNTING (R4b-i)
    et FINANCIAL_ANALYST (R4b-iii), via accounting_results et
    financial_analyst_results respectivement. CUSTOMER (R4b-iv) reste à
    brancher. operation_ids reste vide tant qu'aucun agent ne le peuple.
    """

    success: bool = Field(description="Statut du succès du routage.")
    message: str = Field(description="Message destiné aux logs ou à l'utilisateur en cas d'échec.")
    router_output: Optional[RouterOutput] = Field(
        default=None, description="Sortie brute du Router (liste des intents détectés)."
    )
    operation_ids: list[str] = Field(
        default_factory=list, description="UUID des opérations créées/modifiées (vide tant que R4 n'est pas branché)."
    )
    needs_clarification: bool = Field(
        default=False, description="True si au moins un intent nécessite une clarification utilisateur."
    )
    missing_fields: list[str] = Field(
        default_factory=list, description="Champs manquants agrégés, si needs_clarification=True."
    )
    
    accounting_results: list[AccountingExtractionResult] = Field(
        default_factory=list,
        description="Résultats d'extraction accounting, un par intent ACCOUNTING traité, dans le même ordre que router_output.intents.",
    )

    financial_analyst_results: list[ToolSelectionOutput | FinalAnswerOutput] = Field(
        default_factory=list,
        description=(
            "Résultats FINANCIAL_ANALYST, un par intent traité, dans le même ordre que "
            "router_output.intents. ToolSelectionOutput si needs_clarification=True (pas "
            "de tools exécutés), sinon FinalAnswerOutput."
        ),
    )
    
    accounting_modify_results: list[AccountingModifyResult] = Field(
        default_factory=list,
        description=(
            "Résultats ACCOUNTING_MODIFY, un par intent traité, dans le même ordre que "
            "router_output.intents. AccountingModifySearchResult si clarification requise "
            "dès l'appel 1, sinon AccountingModifyResolutionResult ou "
            "AccountingModifyProposeChangeResult (jamais d'écriture réelle en base)."
        ),
    )

    customer_results: list[ValidationOutput | CustomerExtractionOutput | ToolSelectionOutput | FinalAnswerOutput] = Field(
        default_factory=list,
        description=(
            "Résultats CUSTOMER, un par intent traité, dans le même ordre que "
            "router_output.intents. ValidationOutput si cas bloquant (wrong_agent, "
            "contact_disambiguation), CustomerExtractionOutput pour le sous-flux CREATE, "
            "ToolSelectionOutput si needs_clarification=True côté READ, sinon FinalAnswerOutput."
        ),
    )
