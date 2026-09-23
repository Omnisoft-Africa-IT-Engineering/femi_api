"""Schémas Pydantic pour la sortie structurée de CUSTOMER_PROMPT (Point R4b-iv).

Contrairement à ACCOUNTING (un seul appel LLM → un objet Pydantic) et à
FINANCIAL_ANALYST (toujours 2 étapes fixes tool_selection → final_answer),
le PREMIER appel LLM de CUSTOMER est polymorphe : selon le message, le LLM
retourne l'un de trois formats, discriminés par le champ "step" :

    - step="validation"     → cas bloquant (wrong_agent, contact_disambiguation,
                               nature_creance) — customer_prompt.py sections 2, 4, 18, 22.
    - step="tool_selection" → démarre le sous-flux READ (section 7) — mêmes
                               schémas que FINANCIAL_ANALYST (tool_loop.py),
                               réutilisés tels quels, PUIS délégué à
                               ToolLoopExecutor.resolve() pour l'étape
                               final_answer (section 11).
    - step="extraction"     → sous-flux CREATE, terminal en un seul appel,
                               comme ACCOUNTING (sections 12-17).

CustomerFirstStepOutput est une union discriminée sur "step", pour que
Pydantic valide et route automatiquement vers le bon sous-schéma plutôt que
de renvoyer un schéma générique trié manuellement après coup.

CONFIRMÉ EMPIRIQUEMENT : la compatibilité de with_structured_output()
(LangChain + Ollama) avec une union Pydantic discriminée a été testée avec
succès (script isolé, schéma A/B minimal discriminé sur "step") — le LLM
retourne bien le sous-schéma correct, correctement typé. Ce pattern peut
être branché dans customer_executor.py sans réserve sur ce point précis.
"""

from typing import Literal, Union

from pydantic import BaseModel, Field

from apps.femi_agent.schemas.accounting import AccountingConfidenceEnum, AccountingPaymentMethodEnum
from apps.femi_agent.schemas.tool_loop import ToolSelectionOutput, ValidationOutput


class CustomerExtractionOutput(BaseModel):
    """
    Sortie attendue pour le sous-flux CREATE (customer_prompt.py section 13,
    17) : extraction d'un paiement client en un seul appel, terminal —
    aucun tool, aucun deuxième appel LLM, comme AccountingExtractionResult.
    """

    action_type: Literal["CREATE"]
    step: Literal["extraction"]
    contact: str | None = Field(default=None, description="Nom du contact, jamais inventé.")
    amount_ttc: float | None = Field(
        default=None, description="Montant du paiement, jamais calculé (voir section 14)."
    )
    currency: str | None = Field(default=None)
    payment_method: AccountingPaymentMethodEnum | None = Field(default=None)
    date: str | None = Field(default=None, description="Date explicitement indiquée, sinon null.")
    description: str = Field(description="Résumé factuel et court.")
    confidence: AccountingConfidenceEnum
    needs_clarification: bool = False
    missing_fields: list[str] = Field(default_factory=list)


class CustomerFirstStepOutput(BaseModel):
    """
    Enveloppe pour le premier appel LLM de CUSTOMER : union discriminée sur
    "step", résolvant vers l'un des trois formats possibles (voir docstring
    de module). ToolSelectionOutput est réutilisé tel quel depuis
    tool_loop.py (customer_prompt.py section 7 reprend exactement le même
    format que financial_analyst_prompt.py section 13).
    """

    result: Union[ValidationOutput, ToolSelectionOutput, CustomerExtractionOutput] = Field(
        discriminator="step"
    )
    