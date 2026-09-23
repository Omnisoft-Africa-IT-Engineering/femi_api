"""Schémas Pydantic pour le pattern boucle-avec-tools (Point tool_loop_executor).

Utilisés par FINANCIAL_ANALYST (2 étapes : tool_selection → final_answer)
et par le sous-flux READ de CUSTOMER (3 étapes : validation → tool_selection
→ final_answer). Le sous-flux CREATE de CUSTOMER n'utilise PAS ces schémas :
il suit le pattern simple d'extraction en un seul appel, comme ACCOUNTING
(voir schemas/accounting.py).

Un schéma distinct par étape plutôt qu'un schéma unique discriminé : chaque
appel à with_structured_output() se lie à un seul schéma, et l'exécuteur
sait toujours quelle étape il exécute (voir tool_loop_executor.py) — pas
besoin d'union discriminée entre tool_selection/final_answer/validation.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCallSchema(BaseModel):
    """Un appel de tool demandé par le LLM à l'étape tool_selection."""

    tool: str = Field(description="Nom du tool à appeler (doit exister dans le registry de l'agent).")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Paramètres à passer au tool, tels que renvoyés par le LLM."
    )


class ToolSelectionOutput(BaseModel):
    """
    Sortie attendue à l'étape "tool_selection" (financial_analyst_prompt.py
    section 13, customer_prompt.py section 7 pour le sous-flux READ).
    """

    step: Literal["tool_selection"]
    tool_calls: list[ToolCallSchema] = Field(
        default_factory=list, description="Un ou plusieurs tools à exécuter avant de produire la réponse finale."
    )
    needs_clarification: bool = False
    missing_fields: list[str] = Field(default_factory=list)


class FinalAnswerOutput(BaseModel):
    """
    Sortie attendue à l'étape "final_answer" (financial_analyst_prompt.py
    section 14, customer_prompt.py section 8), une fois {tool_results}
    injecté dans le prompt.
    """

    step: Literal["final_answer"]
    answer: str = Field(description="Réponse claire et concise destinée à WhatsApp.")
    key_figures: dict[str, Any] = Field(
        default_factory=dict, description="Chiffres clés cités dans la réponse, tous traçables à {tool_results}."
    )


class ValidationOutput(BaseModel):
    """
    Sortie attendue à l'étape "validation", propre au sous-flux READ de
    CUSTOMER (customer_prompt.py section 6) : désambiguïsation de contact,
    ou signalement qu'il s'agit en fait d'un autre agent (wrong_agent).
    N'existe pas côté FINANCIAL_ANALYST, qui démarre directement à
    tool_selection.
    """

    action_type: Literal["READ", "CREATE"]
    step: Literal["validation"]
    needs_clarification: bool = False
    missing_fields: list[str] = Field(default_factory=list)