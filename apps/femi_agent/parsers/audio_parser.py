import logging
import os
import subprocess
import tempfile
from functools import lru_cache
from io import BytesIO

from django.conf import settings

logger = logging.getLogger(__name__)


def _load_elevenlabs():
    """Importe le SDK ElevenLabs uniquement lorsque la transcription est demandée."""
    try:
        from importlib import import_module

        return import_module("elevenlabs.client")
    except ImportError as e:
        logger.exception("Le paquet elevenlabs est introuvable.")
        raise ValueError(
            "Le serveur audio n'est pas correctement configuré "
            "(paquet elevenlabs manquant — voir requirements.txt)."
        ) from e


@lru_cache()
def get_elevenlabs_client():
    """Crée (une seule fois, en cache) le client ElevenLabs.

    Ne télécharge et ne charge aucun modèle en mémoire : la transcription
    se fait via un appel HTTP à l'API ElevenLabs (modèle Scribe), ce qui
    élimine le cold start / le risque OOM des modèles locaux (Whisper,
    Parakeet)."""
    api_key = getattr(settings, "FEMI_ELEVENLABS_API_KEY", None)
    if not api_key:
        raise ValueError(
            "Le serveur audio n'est pas correctement configuré "
            "(FEMI_ELEVENLABS_API_KEY manquante dans les settings)."
        )
    elevenlabs_client_module = _load_elevenlabs()
    return elevenlabs_client_module.ElevenLabs(api_key=api_key)


def _to_wav_16k_mono(audio_bytes: bytes, file_extension: str) -> str:
    """Convertit l'audio brut en WAV 16kHz mono via ffmpeg.

    Conservé par précaution même avec l'API ElevenLabs : garantit un format
    d'entrée standard quel que soit le format d'origine envoyé par WhatsApp
    (ex. .ogg/opus), sans dépendre des formats acceptés en interne par
    l'API du fournisseur."""
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
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="ignore") if e.stderr else ""
        logger.error("ffmpeg a échoué : %s", stderr)
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)
        raise
    except Exception:
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)
        raise
    finally:
        if src_path and os.path.exists(src_path):
            os.remove(src_path)


def transcribe_audio(audio_bytes: bytes, file_extension: str = ".ogg") -> str:
    """Convertit un fichier audio brut en texte via l'API ElevenLabs (Scribe).

    Remplace les moteurs locaux Whisper/Parakeet : plus de modèle à
    télécharger ni à charger en mémoire côté serveur.

    Raises:
        ValueError: si l'audio est vide, corrompu, ou si l'appel à l'API échoue.
    """
    if not audio_bytes:
        raise ValueError("Impossible de transcrire : audio_bytes est vide.")

    client = get_elevenlabs_client()
    model_id = getattr(settings, "FEMI_ELEVENLABS_MODEL", "scribe_v2")
    # None = détection automatique de la langue (utile si les utilisateurs
    # parlent parfois une langue locale plutôt que le français)
    language_code = getattr(settings, "FEMI_STT_LANGUAGE_CODE", None)

    wav_path = None
    try:
        wav_path = _to_wav_16k_mono(audio_bytes, file_extension)
        with open(wav_path, "rb") as f:
            result = client.speech_to_text.convert(
                file=BytesIO(f.read()),
                model_id=model_id,
                language_code=language_code,
                tag_audio_events=False,
                diarize=False,
            )
        return (getattr(result, "text", "") or "").strip()

    except subprocess.CalledProcessError as e:
        raise ValueError(f"Erreur lors de la conversion audio : {e}") from e
    except FileNotFoundError as e:
        logger.exception("ffmpeg introuvable — requis pour convertir l'audio avant transcription.")
        raise ValueError("Le serveur audio n'est pas correctement configuré (ffmpeg manquant).") from e
    except Exception as e:
        logger.exception("Erreur inattendue lors de la transcription audio (ElevenLabs).")
        raise ValueError(f"Erreur lors de la transcription audio : {e}") from e
    finally:
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)