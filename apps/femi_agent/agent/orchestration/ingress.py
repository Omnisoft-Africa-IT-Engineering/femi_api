"""Ingress multimédia : texte + OCR + STT → texte combiné pour le Router.

Extrait de router_manager (ARCHITECTURE.md Phase 3) pour réduire le god-object.
Comportement identique : aucune règle métier ici.
"""

from __future__ import annotations

import logging
from typing import Optional

from asgiref.sync import sync_to_async

from apps.femi_agent.agent.ocr_executor import OcrExecutor
from apps.femi_agent.agent.ocr_postprocess import build_document_text
from apps.femi_agent.agent.stt_executor import SttExecutor
from apps.femi_agent.parsers.audio_parser import transcribe_audio

logger = logging.getLogger(__name__)


class IngressError(ValueError):
    """Aucune donnée textuelle ou multimédia exploitable."""


def build_combined_text(
    text_input: Optional[str],
    image_bytes: Optional[bytes],
    audio_bytes: Optional[bytes],
) -> str:
    """Assemble le message final (sync) : texte utilisateur + OCR + STT."""
    parts: list[str] = []

    if text_input and text_input.strip():
        parts.append(text_input.strip())

    if image_bytes:
        ocr_result = OcrExecutor.execute(image_bytes)
        readable_text = build_document_text(ocr_result)
        if readable_text:
            parts.append(readable_text)

    if audio_bytes:
        raw_transcription = transcribe_audio(audio_bytes)
        stt_result = SttExecutor.execute(raw_transcription)
        if stt_result.corrected_text:
            parts.append(stt_result.corrected_text)

    combined = "\n".join(parts).strip()
    if not combined:
        raise IngressError(
            "Aucune donnée textuelle ou multimédia exploitable."
        )
    return combined


async def abuild_combined_text(
    text_input: Optional[str],
    image_bytes: Optional[bytes],
    audio_bytes: Optional[bytes],
) -> str:
    """Version async de build_combined_text."""
    parts: list[str] = []

    if text_input and text_input.strip():
        parts.append(text_input.strip())

    if image_bytes:
        ocr_result = await OcrExecutor.aexecute(image_bytes)
        readable_text = build_document_text(ocr_result)
        if readable_text:
            parts.append(readable_text)

    if audio_bytes:
        raw_transcription = await sync_to_async(transcribe_audio)(audio_bytes)
        stt_result = await SttExecutor.aexecute(raw_transcription)
        if stt_result.corrected_text:
            parts.append(stt_result.corrected_text)

    combined = "\n".join(parts).strip()
    if not combined:
        raise IngressError(
            "Aucune donnée textuelle ou multimédia exploitable."
        )
    return combined
