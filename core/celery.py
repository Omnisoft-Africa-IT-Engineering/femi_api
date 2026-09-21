import os
from celery import Celery

# Définit le module de paramètres par défaut pour 'celery'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings') # Remplace 'monprojet' par le nom de ton dossier

app = Celery('monprojet')

# Charge les configurations depuis settings.py avec le préfixe 'CELERY_'
app.config_from_object('django.conf:settings', namespace='CELERY')

# Découvre automatiquement les tâches (tasks.py) dans toutes tes apps installées
app.autodiscover_tasks()