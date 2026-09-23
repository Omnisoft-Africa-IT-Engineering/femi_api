#!/usr/bin/env bash
# Arrêter le script dès qu'une commande échoue
set -o errexit

# 1. Installer les dépendances Python
pip install -r requirements.txt

# 2. Collecter les fichiers statiques (pour l'admin Django)
python manage.py collectstatic --no-input

# 3. Appliquer les migrations de base de données directement sur Supabase !
python manage.py migrate