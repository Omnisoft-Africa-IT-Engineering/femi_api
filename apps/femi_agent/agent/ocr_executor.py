"""Exécuteur d'OCR_PROMPT (agent OCR vision, remplace à terme ocr_parser.py
/ Tesseract — voir résumé de session, migration décidée le 11/09).

Contrairement aux autres exécuteurs du nouveau système, celui-ci appelle un
modèle Ollama multimodal (vision), configuré séparément via
settings.FEMI_VISION_MODEL — jamais le modèle texte par défaut
(settings.FEMI_LLM_MODEL) utilisé par les autres agents.

OCR_PROMPT ne contient aucune variable à injecter (pas d'accolade {...}),
il est donc utilisé tel quel comme prompt_text, comme STT_PROMPT
(voir stt_executor.py).

Reçoit les bytes bruts de l'image (mêmes bytes que ceux transmis par
tasks.py / WhatsAppClient.download_media(), voir apps/femi_whatsapp/tasks.py)
et gère lui-même l'encodage base64 — aucune autre couche ne doit dupliquer
cet encodage.
"""

import base64
import logging

from django.conf import settings

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.prompts.ocr_prompt import OCR_PROMPT
from apps.femi_agent.schemas import OcrExtractionResult

logger = logging.getLogger(__name__)

# Le prompt système OCR (nombreuses règles et exemples) dépasse largement le
# contexte par défaut d'Ollama (2048) — même précaution que les autres
# prompts du nouveau système (voir router_executor.py, accounting_executor.py,
# stt_executor.py).
OCR_NUM_CTX = 16384

DEFAULT_VISION_MODEL = "llava:7b"

# Instruction minimale accompagnant l'image : OCR_PROMPT (system) contient
# déjà l'intégralité des règles et du schéma de sortie attendu — le message
# "user" n'a donc besoin que de pointer vers l'image jointe, sans répéter
# d'instructions.
_USER_INSTRUCTION = "Analyse l'image jointe selon les règles et le schéma définis."


class OcrExecutionError(BaseAgentExecutionError):
    """Exception personnalisée encapsulant les échecs d'exécution de l'agent OCR."""
    pass


class OcrExecutor:
    """
    Exécuteur d'OCR_PROMPT : encode l'image en base64, appelle le LLM
    vision (settings.FEMI_VISION_MODEL) avec sortie structurée contrainte
    au schéma OcrExtractionResult. Aucune variable à injecter dans le prompt.
    """

    @staticmethod
    def _get_vision_model_name() -> str:
        return getattr(settings, "FEMI_VISION_MODEL", DEFAULT_VISION_MODEL)

    @classmethod
    def execute(cls, image_bytes: bytes) -> OcrExtractionResult:
        """Exécution synchrone de l'agent OCR.

        Args:
            image_bytes: Bytes bruts de l'image (JPEG/PNG), non encodés.

        Returns:
            OcrExtractionResult (schéma structuré, voir schemas/ocr.py).
        """
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        return StructuredLLMExecutor.execute(
            prompt_text=OCR_PROMPT,
            message_text=_USER_INSTRUCTION,
            output_schema=OcrExtractionResult,
            num_ctx=OCR_NUM_CTX,
            error_cls=OcrExecutionError,
            log_prefix="OcrExecutor",
            images=[image_b64],
            model_name=cls._get_vision_model_name(),
        )

    @classmethod
    async def aexecute(cls, image_bytes: bytes) -> OcrExtractionResult:
        """Exécution asynchrone de l'agent OCR (mêmes arguments que execute())."""
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        return await StructuredLLMExecutor.aexecute(
            prompt_text=OCR_PROMPT,
            message_text=_USER_INSTRUCTION,
            output_schema=OcrExtractionResult,
            num_ctx=OCR_NUM_CTX,
            error_cls=OcrExecutionError,
            log_prefix="OcrExecutor",
            images=[image_b64],
            model_name=cls._get_vision_model_name(),
        )
