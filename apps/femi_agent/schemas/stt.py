"""Schémas Pydantic pour la sortie structurée de STT_PROMPT (agent de
post-traitement textuel après transcription Whisper).

Reflète exactement le format JSON défini dans stt_prompt.py (section 22).
Aucun champ ajouté ou renommé par rapport au prompt.
"""

from typing import Literal

from pydantic import BaseModel

# Valeurs autorisées, copiées telles quelles depuis stt_prompt.py (section 18)
UncertainSegmentType = Literal["montant", "contact", "quantite", "autre"]

# Valeurs autorisées, copiées telles quelles depuis stt_prompt.py (section 19)
SttConfidence = Literal["high", "medium", "low"]


class UncertainSegmentSchema(BaseModel):
    text: str
    type: UncertainSegmentType
    reason: str


class SttPostProcessingResult(BaseModel):
    corrected_text: str
    uncertain_segments: list[UncertainSegmentSchema]
    confidence: SttConfidence
