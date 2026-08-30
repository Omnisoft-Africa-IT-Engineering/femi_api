import json
import os
import requests
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from dotenv import load_dotenv

from apps.femi_agent.agent_manager import FemiAgentManager

# Charger les variables du fichier .env
load_dotenv()


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppWebhookView(View):
    """
    Webhook pour la réception et la réponse aux messages WhatsApp (Meta Cloud API).
    """

    @extend_schema(exclude=True)
    def get(self, request):
        """
        Étape 1 : Validation du Webhook par Meta/WhatsApp.
        """
        expected_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "femi_secret_token_2026").strip()
        mode = request.GET.get("hub.mode")
        received_token = (request.GET.get("hub.verify_token") or "").strip()
        challenge = request.GET.get("hub.challenge")

        if mode == "subscribe" and received_token == expected_token:
            return HttpResponse(challenge, status=200)

        print(f"[WEBHOOK GET FAIL] Token attendu: '{expected_token}', Reçu: '{received_token}'")
        return HttpResponse("Jeton de vérification invalide.", status=403)

    @extend_schema(exclude=True)
    def post(self, request):
        """
        Étape 2 : Réception du message WhatsApp envoyé par l'utilisateur.
        """
        try:
            body = json.loads(request.body.decode('utf-8'))

            # Vérification de la structure du payload Meta
            entries = body.get("entry", [])
            if not entries:
                return JsonResponse({"status": "ignored"}, status=200)

            changes = entries[0].get("changes", [])
            if not changes:
                return JsonResponse({"status": "ignored"}, status=200)

            value = changes[0].get("value", {})
            messages = value.get("messages", [])

            if messages:
                message = messages[0]
                from_number = message.get("from")  # Numéro de l'expéditeur
                message_type = message.get("type")

                # Traitement si c'est un message texte
                if message_type == "text":
                    text_content = message.get("text", {}).get("body", "")
                    print(f"\n[MESSAGE REÇU DE {from_number}] : {text_content}")

                    # 💥 APPEL DU CERVEAU CENTRAL (FemiAgentManager)
                    result = FemiAgentManager.process_transaction_text(
                        text_input=text_content,
                        source="WHATSAPP"
                    )

                    # Envoi de la réponse sur WhatsApp
                    self._send_whatsapp_message(from_number, result.message)

            return JsonResponse({"status": "success"}, status=200)

        except Exception as e:
            print(f"[ERREUR WEBHOOK POST] : {str(e)}")
            # On renvoie 200 à Meta pour éviter les re-tentatives en boucle en cas de bug
            return JsonResponse({"status": "error", "message": str(e)}, status=200)

    def _send_whatsapp_message(self, phone_number: str, message_text: str):
        """
        Envoie un message texte via l'API Graph Meta Cloud WhatsApp.
        """
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()

        # Si les clés Meta ne sont pas configurées, fallback en mode simulation log
        if not phone_number_id or not access_token:
            print(f"\n[SIMULATION WHATSAPP -> {phone_number}] :\n{message_text}\n")
            return

        url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": message_text}
        }

        response = requests.post(url, json=data, headers=headers)

        print(f"\n[RÉPONSE META API] Statut: {response.status_code}")
        if response.status_code != 200:
            print(f"Détails Erreur Meta: {response.text}")