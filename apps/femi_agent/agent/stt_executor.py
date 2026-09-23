"""Exécuteur de STT_PROMPT (agent de post-traitement textuel après Whisper).

Construit le prompt par remplacement de texte, comme les autres exécuteurs du
nouveau système (même raison que RouterExecutor/AccountingExecutor : le
prompt contient de nombreux blocs JSON d'exemple avec accolades littérales,
incompatibles avec PromptTemplate/.format()) — ici STT_PROMPT ne contient
cependant aucune variable à injecter, il est donc utilisé tel quel comme
prompt_text.

Ne fait AUCUNE interprétation métier : reçoit le texte brut transcrit par
Whisper (transcribe_audio, apps/femi_agent/parsers/audio_parser.py) et
retourne uniquement une version nettoyée/corrigée (corrected_text), destinée
à être ensuite transmise telle quelle au Router (RouterExecutor).
"""

import logging

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.prompts.stt_prompt import STT_PROMPT
from apps.femi_agent.schemas import SttPostProcessingResult

logger = logging.getLogger(__name__)

# Le prompt système STT (nombreuses règles et exemples) dépasse largement le
# contexte par défaut d'Ollama (2048) — même précaution que ROUTER_PROMPT et
# ACCOUNTING_PROMPT (voir router_executor.py, accounting_executor.py).
STT_NUM_CTX = 16384


class SttExecutionError(BaseAgentExecutionError):
    """Exception personnalisée encapsulant les échecs d'exécution de l'agent STT."""
    pass


class SttExecutor:
    """
    Exécuteur de STT_PROMPT : appelle le LLM avec sortie structurée
    contrainte au schéma SttPostProcessingResult, sur le texte brut produit
    par transcribe_audio(). Aucune variable à injecter dans le prompt.
    """

    @classmethod
    def execute(cls, transcribed_text: str) -> SttPostProcessingResult:
        """Exécution synchrone de l'agent STT.

        Args:
            transcribed_text: Texte brut produit par Whisper (transcribe_audio),
                avant tout nettoyage.

        Returns:
            SttPostProcessingResult (corrected_text, uncertain_segments, confidence).
        """
        return StructuredLLMExecutor.execute(
            prompt_text=STT_PROMPT,
            message_text=transcribed_text,
            output_schema=SttPostProcessingResult,
            num_ctx=STT_NUM_CTX,
            error_cls=SttExecutionError,
            log_prefix="SttExecutor",
        )

    @classmethod
    async def aexecute(cls, transcribed_text: str) -> SttPostProcessingResult:
        """Exécution asynchrone de l'agent STT (mêmes arguments que execute())."""
        return await StructuredLLMExecutor.aexecute(
            prompt_text=STT_PROMPT,
            message_text=transcribed_text,
            output_schema=SttPostProcessingResult,
            num_ctx=STT_NUM_CTX,
            error_cls=SttExecutionError,
            log_prefix="SttExecutor",
        )
