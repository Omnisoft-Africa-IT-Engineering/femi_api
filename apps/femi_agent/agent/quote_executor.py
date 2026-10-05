"""Exécuteur d'QUOTE_PROMPT (nouveau système Router/Tools).

Construit le prompt de gestion des devis, puis délègue l'appel LLM 
à StructuredLLMExecutor (base_executor.py) pour obtenir une extraction structurée.

Utilise QuoteExtractionLLMResult (montants en float) comme cible de
with_structured_output() — QuoteExtractionResult (Decimal) fait échouer
la conversion en grammaire GBNF côté Ollama (même contrainte technique 
que pour le module comptable). Le résultat LLM est converti vers le
schéma interne (Decimal) juste après réception, via .from_llm_result().
"""
import logging

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.prompts.quote_prompt import QUOTE_SYSTEM_PROMPT
from apps.femi_agent.schemas import QuoteExtractionLLMResult, QuoteExtractionResult
from apps.femi_account.models import Entreprise


logger = logging.getLogger(__name__)

# Taille de contexte allouée pour le modèle LLM (Ollama), 
# identique à l'exécuteur comptable pour supporter de grands prompts structurés.
QUOTE_NUM_CTX = 8192


class QuoteExecutionError(BaseAgentExecutionError):
    """Exception personnalisée encapsulant les échecs d'exécution de l'agent QUOTE."""
    pass


class QuoteExecutor:
    """
    Exécuteur de QUOTE_PROMPT : 
    - Construit le prompt système pour la gestion des devis.
    - Délègue l'appel au LLM avec une sortie structurée contrainte au schéma QuoteExtractionLLMResult (float).
    - Convertit le résultat vers QuoteExtractionResult (Decimal) avant de le retourner.
    """

    @staticmethod
    def _build_prompt_text() -> str:
        """Retourne le texte brut du prompt système pour les devis."""
        return QUOTE_SYSTEM_PROMPT

    @classmethod
    def execute(
        cls,
        message_text: str,
        entreprise: Entreprise,
    ) -> QuoteExtractionResult:
        """Exécution synchrone de l'agent QUOTE.

        Args:
            message_text: Message ou demande de l'utilisateur concernant le devis.
            entreprise: Instance Entreprise du tenant concerné.

        Returns:
            QuoteExtractionResult (schéma interne, montants en Decimal).
        """
        prompt_text = cls._build_prompt_text()
        
        llm_result: QuoteExtractionLLMResult = StructuredLLMExecutor.execute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=QuoteExtractionLLMResult,
            num_ctx=QUOTE_NUM_CTX,
            error_cls=QuoteExecutionError,
            log_prefix="QuoteExecutor",
        )
        return QuoteExtractionResult.from_llm_result(llm_result)

    @classmethod
    async def aexecute(
        cls,
        message_text: str,
        entreprise: Entreprise,
    ) -> QuoteExtractionResult:
        """Exécution asynchrone de l'agent QUOTE (mêmes arguments que execute())."""
        prompt_text = cls._build_prompt_text()
        
        llm_result: QuoteExtractionLLMResult = await StructuredLLMExecutor.aexecute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=QuoteExtractionLLMResult,
            num_ctx=QUOTE_NUM_CTX,
            error_cls=QuoteExecutionError,
            log_prefix="QuoteExecutor",
        )
        return QuoteExtractionResult.from_llm_result(llm_result)