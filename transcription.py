import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")  # adapte "core" si ton dossier settings a un autre nom

import django
django.setup()

from apps.femi_agent.parsers.audio_parser import transcribe_audio  # adapte le chemin exact vers audio_parsey.py

with open("test_vocal.ogg", "rb") as f:
    audio_bytes = f.read()

texte = transcribe_audio(audio_bytes)
print("Transcription :", texte)