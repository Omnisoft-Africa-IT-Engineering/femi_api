#!/usr/bin/env bash
# Arrêter le script dès qu'une commande échoue
set -o errexit

# 1. Dépendance système requise par la transcription audio (Whisper et
#    Parakeet en ont tous les deux besoin pour décoder/convertir l'audio).
#    Sans ça, tout message audio échoue avec "ffmpeg manquant".
apt-get update -y && apt-get install -y --no-install-recommends ffmpeg

# 2. Installer les dépendances Python
pip install -r requirements.txt

# 3. Collecter les fichiers statiques (pour l'admin Django)
python manage.py collectstatic --no-input

# 4. Appliquer les migrations de base de données directement sur Supabase !
python manage.py migrate