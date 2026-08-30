import json
import requests

WEBHOOK_URL = "http://127.0.0.1:8000/whatsapp/webhook/"

def test_whatsapp_verification():
    print("--- 1. Test de Vérification du Token Webhook (GET) ---")
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": "femi_webhook_secret_token_123",
        "hub.challenge": "99887766"
    }
    response = requests.get(WEBHOOK_URL, params=params)
    print(f"Statut HTTP : {response.status_code}")
    print(f"Réponse Meta : {response.text}\n")


def test_whatsapp_incoming_message():
    print("--- 2. Test d'un Message Texte WhatsApp Entrant (POST) ---")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "22890000000",
                                "phone_number_id": "1234567890"
                            },
                            "messages": [
                                {
                                    "from": "22890112233",
                                    "id": "wamid.HBgLMjI4OTAxMTIyMzM=",
                                    "timestamp": "1724853000",
                                    "text": {
                                        "body": "Paiement de loyer de 75000 XOF en virement bancaire le 10/08/2026 chez Immo Services"
                                    },
                                    "type": "text"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }

    headers = {"Content-Type": "application/json"}
    response = requests.post(WEBHOOK_URL, data=json.dumps(payload), headers=headers)
    print(f"Statut HTTP : {response.status_code}")
    print(f"Réponse Webhook : {response.json()}\n")

if __name__ == "__main__":
    test_whatsapp_verification()
    test_whatsapp_incoming_message()