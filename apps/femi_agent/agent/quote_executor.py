"""Exécuteur de QUOTE_PROMPT (création de devis).

Même mécanique que AccountingExecutor : appel LLM à sortie structurée via
StructuredLLMExecutor (cible float), puis conversion vers le schéma interne
(Decimal) avec recalcul des champs manquants côté backend.

L'exécuteur n'écrit rien en base : l'enregistrement du devis est fait par
FemiRouterManager, via apps.femi_account.devis_service.creer_devis().
"""

import logging

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.prompts.quote_prompt import QUOTE_PROMPT
from apps.femi_agent.schemas import QuoteExtractionLLMResult, QuoteExtractionResult

logger = logging.getLogger(__name__)

QUOTE_NUM_CTX = 4096


class QuoteExecutionError(BaseAgentExecutionError):
    """Échec d'exécution de l'agent QUOTE."""


class QuoteExecutor:
    @classmethod
    def execute(cls, message_text: str, entreprise=None) -> QuoteExtractionResult:
        llm_result: QuoteExtractionLLMResult = StructuredLLMExecutor.execute(
            prompt_text=QUOTE_PROMPT,
            message_text=message_text,
            output_schema=QuoteExtractionLLMResult,
            num_ctx=QUOTE_NUM_CTX,
            error_cls=QuoteExecutionError,
            log_prefix="QuoteExecutor",
        )
        return QuoteExtractionResult.from_llm_result(llm_result)

    @classmethod
    async def aexecute(cls, message_text: str, entreprise=None) -> QuoteExtractionResult:
        llm_result: QuoteExtractionLLMResult = await StructuredLLMExecutor.aexecute(
            prompt_text=QUOTE_PROMPT,
            message_text=message_text,
            output_schema=QuoteExtractionLLMResult,
            num_ctx=QUOTE_NUM_CTX,
            error_cls=QuoteExecutionError,
            log_prefix="QuoteExecutor",
        )
        return QuoteExtractionResult.from_llm_result(llm_result)
