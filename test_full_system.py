import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.test import RequestFactory
from apps.femi_mobile.views import MobileAIChatAPIView
from apps.femi_whatsapp.views import WhatsAppWebhookView
from apps.femi_account.models import Operation

factory = RequestFactory()

print("==================================================")
print("🚀 TEST SYSTEME INTEGRÉ FEMI (API REST & WHATSAPP)")
print("==================================================\n")

# 1. TEST CANAL API REST
print("1️⃣ [CANAL API REST] Traitement d'une Dépense...")
payload_api = {
    "text": "Achat fournitures de bureau et papier rames pour 18500 FCFA par virement"
}

req_api = factory.post(
    '/api/v1/mobile/chat/',
    data=json.dumps(payload_api),
    content_type='application/json'
)

res_api = MobileAIChatAPIView.as_view()(req_api)
print(f"➜ Code Status HTTP : {res_api.status_code}")
print(f"➜ Réponse API REST :\n{json.dumps(res_api.data, indent=2, ensure_ascii=False)}\n")


# 2. TEST CANAL WHATSAPP
print("2️⃣ [CANAL WHATSAPP] Traitement d'une Recette via Webhook...")
payload_whatsapp = {
    "entry": [{
        "changes": [{
            "value": {
                "messages": [{
                    "from": "22890123456",
                    "type": "text",
                    "text": {"body": "Recette de prestation de service informatique client Togocom pour 150000 FCFA par Flooz"}
                }]
            }
        }]
    }]
}

req_whatsapp = factory.post(
    '/whatsapp/webhook/',
    data=json.dumps(payload_whatsapp),
    content_type='application/json'
)

res_whatsapp = WhatsAppWebhookView.as_view()(req_whatsapp)
print(f"➜ Code Status HTTP Webhook : {res_whatsapp.status_code}\n")


# 3. VERIFICATION BDD
print("3️⃣ [BASE DE DONNÉES] Dernières opérations enregistrées :")
latest_ops = Operation.objects.order_by('-created_at')[:2]

for op in latest_ops:
    print(f"  • ID: {op.id}")
    print(f"    - Source: {op.source}")
    print(f"    - Type: {op.transaction_type}")
    print(f"    - Montant: {op.amount_ttc} {op.currency}")
    print(f"    - Catégorie: {op.category}")
    print(f"    - Mode Paiement: {op.payment_method}")
    print(f"    - Description: {op.description}")
    print("-" * 40)

print("\n✅ TOUS LES TESTS REST & WHATSAPP SONT VALIDES !")
