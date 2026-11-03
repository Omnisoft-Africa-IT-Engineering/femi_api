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

# 5. Peupler le catalogue KPI (niveaux + KPI par secteur). Idempotent :
#    peut tourner à chaque déploiement sans créer de doublons.
python manage.py seed_kpi_catalogue


# 6. Peupler les offres (Micro, Pro, Business). Idempotent (update_or_create) :
#    NÉCESSAIRE pour que l'essai gratuit trouve le plan Micro.
python seed_plans.py