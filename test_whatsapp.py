import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.test import RequestFactory
from apps.femi_whatsapp.views import WhatsAppWebhookView

factory = RequestFactory()

# Simulation d'un payload JSON reçu de WhatsApp Meta Cloud API
payload = {
    "entry": [{
        "changes": [{
            "value": {
                "messages": [{
                    "from": "22890000000",
                    "type": "text",
                    "text": {"body": "Vente de 5 cartons de savon à 25000 FCFA via TMoney"}
                }]
            }
        }]
    }]
}

request = factory.post(
    '/whatsapp/webhook/',
    data=payload,
    content_type='application/json'
)

view = WhatsAppWebhookView.as_view()
response = view(request)

print(f"Code HTTP Réponse Webhook : {response.status_code}")
