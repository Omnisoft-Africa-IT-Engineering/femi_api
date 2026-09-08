import logging
import os
import tempfile
from functools import lru_cache

import whisper
from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache()
def get_whisper_model():
    """Charge (une seule fois, en cache) le modèle Whisper configuré."""
    model_name = getattr(settings, "FEMI_WHISPER_MODEL", "base")
    logger.info("Chargement du modèle Whisper : %s", model_name)
    return whisper.load_model(model_name)


def transcribe_audio(audio_bytes: bytes, file_extension: str = ".ogg") -> str:
    """Convertit un fichier audio brut en texte via OpenAI Whisper.

    Raises:
        ValueError: si l'audio est vide, corrompu, ou illisible par Whisper.
    """
    if not audio_bytes:
        raise ValueError("Impossible de transcrire : audio_bytes est vide.")

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
        logger.exception("Erreur inattendue lors de la transcription audio.")
        raise ValueError(f"Erreur inattendue lors de la transcription : {e}") from e
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)