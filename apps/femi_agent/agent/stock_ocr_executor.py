"""OCR vision dédié au préremplissage formulaire stock (aucune écriture DB)."""

from __future__ import annotations

import base64
import logging

from django.conf import settings

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.prompts.stock_ocr_prompt import STOCK_OCR_PROMPT
from apps.femi_agent.parsers.document_loader import to_page_images
from apps.femi_agent.schemas.stock_ocr import StockOcrPreviewResult

logger = logging.getLogger(__name__)

OCR_NUM_CTX = 8192
DEFAULT_VISION_MODEL = "llava:7b"
_USER_INSTRUCTION = "Analyse l'image pour le stock et remplis le schéma JSON."


class StockOcrExecutionError(BaseAgentExecutionError):
    pass


class StockOcrExecutor:
    @staticmethod
    def _vision_model() -> str:
        return getattr(settings, "FEMI_VISION_MODEL", DEFAULT_VISION_MODEL)

    @classmethod
    def execute(cls, image_bytes: bytes) -> StockOcrPreviewResult:
        pages = to_page_images(image_bytes)
        if not pages:
            raise StockOcrExecutionError("Image ou document illisible.")
        # Une seule page suffit pour un preview formulaire
        page = pages[0]
        image_b64 = base64.b64encode(page).decode("utf-8")
        return StructuredLLMExecutor.execute(
            prompt_text=STOCK_OCR_PROMPT,
            message_text=_USER_INSTRUCTION,
            output_schema=StockOcrPreviewResult,
            num_ctx=OCR_NUM_CTX,
            error_cls=StockOcrExecutionError,
            log_prefix="StockOcrExecutor",
            images=[image_b64],
            model_name=cls._vision_model(),
        )
