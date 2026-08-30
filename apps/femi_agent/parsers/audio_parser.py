import whisper
import tempfile
import os

# Chargement du modèle de base Whisper en local
_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = whisper.load_model("base")
    return _whisper_model

def transcribe_audio(audio_bytes: bytes, file_extension: str = ".ogg") -> str:
    """Convertit un fichier audio brut en texte via OpenAI Whisper."""
    model = get_whisper_model()
    
    # Création d'un fichier temporaire pour la lecture par Whisper
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_audio:
        temp_audio.write(audio_bytes)
        temp_path = temp_audio.name

    try:
        result = model.transcribe(temp_path, fp16=False)
        return result.get("text", "").strip()
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)