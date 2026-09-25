import logging
import os
import subprocess
import tempfile
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)


def _load_whisper():
    """Importe Whisper uniquement lorsque la transcription est demandée."""
    try:
        from importlib import import_module

        return import_module("whisper")
    except ImportError as e:
        logger.exception("Le paquet openai-whisper est introuvable.")
        raise ValueError(
            "Le serveur audio n'est pas correctement configuré "
            "(paquet openai-whisper manquant)."
        ) from e


@lru_cache()
def get_whisper_model():
    """Charge (une seule fois, en cache) le modèle Whisper configuré."""
    model_name = getattr(settings, "FEMI_WHISPER_MODEL", "base")
    logger.info("Chargement du modèle Whisper : %s", model_name)
    return _load_whisper().load_model(model_name)


def _load_onnx_asr():
    """Importe onnx_asr uniquement lorsque la transcription Parakeet est demandée."""
    try:
        from importlib import import_module

        return import_module("onnx_asr")
    except ImportError as e:
        logger.exception("Le paquet onnx-asr est introuvable.")
        raise ValueError(
            "Le serveur audio n'est pas correctement configuré "
            "(paquet onnx-asr manquant — voir requirements.txt)."
        ) from e


@lru_cache()
def get_parakeet_model():
    """Charge (une seule fois, en cache) le modèle Parakeet TDT 0.6B v3.

    Téléchargé depuis Hugging Face au premier appel (comme Whisper),
    aucun poids committé sur GitHub. Modèle multilingue (25 langues
    européennes dont le français), tourne entièrement sur CPU."""
    model_name = getattr(
        settings, "FEMI_PARAKEET_MODEL", "nemo-parakeet-tdt-0.6b-v3"
    )
    logger.info("Chargement du modèle Parakeet : %s", model_name)
    return _load_onnx_asr().load_model(model_name)


def _to_wav_16k_mono(audio_bytes: bytes, file_extension: str) -> str:
    """Convertit l'audio brut en WAV 16kHz mono via ffmpeg (déjà requis par
    Whisper — même dépendance système, aucune nouvelle installation) —
    format attendu par onnx_asr, quel que soit le format d'origine
    (ex. .ogg/opus envoyé par WhatsApp)."""
    src_path = wav_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as src:
            src_path = src.name
            src.write(audio_bytes)

        wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(wav_fd)

        subprocess.run(
            [
                "ffmpeg", "-y", "-i", src_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", wav_path,
            ],
            check=True,
            capture_output=True,
        )
        return wav_path
    except Exception:
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)
        raise
    finally:
        if src_path and os.path.exists(src_path):
            os.remove(src_path)


def _transcribe_with_whisper(audio_bytes: bytes, file_extension: str) -> str:
    model = get_whisper_model()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_audio:
            temp_path = temp_audio.name
            temp_audio.write(audio_bytes)

        result = model.transcribe(temp_path, fp16=False)
        return result.get("text", "").strip()

    except FileNotFoundError as e:
        # Cas fréquent : ffmpeg non installé sur le serveur (dépendance système de Whisper)
        logger.exception("ffmpeg introuvable — requis par Whisper pour décoder l'audio.")
        raise ValueError("Le serveur audio n'est pas correctement configuré (ffmpeg manquant).") from e
    except RuntimeError as e:
        logger.warning("Erreur Whisper lors de la transcription : %s", e)
        raise ValueError(f"Erreur lors de la transcription audio : {e}") from e
    except Exception as e:
        logger.exception("Erreur inattendue lors de la transcription audio (Whisper).")
        raise ValueError(f"Erreur inattendue lors de la transcription : {e}") from e
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def _transcribe_with_parakeet(audio_bytes: bytes, file_extension: str) -> str:
    model = get_parakeet_model()
    wav_path = None
    try:
        wav_path = _to_wav_16k_mono(audio_bytes, file_extension)
        result = model.recognize(wav_path)  # str, cf. onnx_asr.adapters.TextResultsAsrAdapter
        return str(result).strip()

    except subprocess.CalledProcessError as e:
        logger.exception("ffmpeg a échoué lors de la conversion audio pour Parakeet.")
        raise ValueError(f"Erreur lors de la conversion audio : {e}") from e
    except FileNotFoundError as e:
        logger.exception("ffmpeg introuvable — requis pour convertir l'audio avant Parakeet.")
        raise ValueError("Le serveur audio n'est pas correctement configuré (ffmpeg manquant).") from e
    except Exception as e:
        logger.exception("Erreur inattendue lors de la transcription audio (Parakeet).")
        raise ValueError(f"Erreur inattendue lors de la transcription : {e}") from e
    finally:
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)


def transcribe_audio(audio_bytes: bytes, file_extension: str = ".ogg") -> str:
    """Convertit un fichier audio brut en texte.

    Moteur choisi via settings.FEMI_STT_ENGINE ("whisper", par défaut,
    ou "parakeet") — isolé de FEMI_LLM_PROVIDER/FEMI_VISION_PROVIDER,
    ceci ne concerne que la transcription brute (avant STT_PROMPT).

    Raises:
        ValueError: si l'audio est vide, corrompu, ou illisible.
    """
    if not audio_bytes:
        raise ValueError("Impossible de transcrire : audio_bytes est vide.")

    engine = getattr(settings, "FEMI_STT_ENGINE", "whisper").lower()

    if engine == "parakeet":
        return _transcribe_with_parakeet(audio_bytes, file_extension)

    if engine != "whisper":
        logger.warning(
            "FEMI_STT_ENGINE inconnu ('%s'), repli sur whisper.", engine
        )

    return _transcribe_with_whisper(audio_bytes, file_extension)