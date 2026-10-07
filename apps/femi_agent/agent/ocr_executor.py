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

import asyncio
import base64
import logging

from django.conf import settings

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.ocr_postprocess import merge_ocr_results
from apps.femi_agent.agent.prompts.ocr_prompt import OCR_PROMPT
from apps.femi_agent.parsers.document_loader import to_page_images
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
    def _execute_page(cls, page_bytes: bytes) -> OcrExtractionResult:
        """Analyse UNE image (une page) avec le modèle vision."""
        image_b64 = base64.b64encode(page_bytes).decode("utf-8")
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
    async def _aexecute_page(cls, page_bytes: bytes) -> OcrExtractionResult:
        image_b64 = base64.b64encode(page_bytes).decode("utf-8")
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

    @classmethod
    def execute(cls, image_bytes: bytes) -> OcrExtractionResult:
        """Exécution synchrone de l'agent OCR.

        Args:
            image_bytes: Bytes bruts d'une image (JPEG/PNG/WebP) OU d'un PDF,
                non encodés. Le format est détecté sur les octets eux-mêmes
                (voir parsers/document_loader.py). Un PDF est lu page par
                page puis fusionné : chaque page passe par le même chemin
                vision qu'une image seule.

        Returns:
            OcrExtractionResult (schéma structuré, voir schemas/ocr.py).

        Raises:
            UnsupportedDocumentError: format non pris en charge, PDF protégé,
                corrompu ou trop long (message en français pour l'utilisateur).
            OcrExecutionError: échec du modèle vision sur une page. Aucun
                résultat partiel n'est retourné : un document à moitié lu
                donnerait des totaux faux.
        """
        pages = to_page_images(image_bytes)
        if len(pages) > 1:
            logger.info("[OcrExecutor] Document de %d pages.", len(pages))
        results = [cls._execute_page(page) for page in pages]
        return merge_ocr_results(results)

    @classmethod
    async def aexecute(cls, image_bytes: bytes) -> OcrExtractionResult:
        """Exécution asynchrone de l'agent OCR (mêmes arguments que execute()).
        Les pages sont analysées l'une après l'autre (pas en parallèle) pour
        ne pas saturer les limites de débit du fournisseur vision."""
        pages = await asyncio.to_thread(to_page_images, image_bytes)
        if len(pages) > 1:
            logger.info("[OcrExecutor] Document de %d pages.", len(pages))
        results = []
        for page in pages:
            results.append(await cls._aexecute_page(page))
        return merge_ocr_results(results)