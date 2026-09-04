import json
import os
import requests
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from dotenv import load_dotenv

from apps.femi_agent.agent.manager import FemiAgentManager
from apps.femi_account.models import Utilisateur

from apps.femi_whatsapp.models import ProcessedMessage
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
<<<<<<< HEAD
=======
        Gère les messages texte, image et audio.
        """
        try:
            body = json.loads(request.body.decode('utf-8'))

            entries = body.get("entry", [])
            if not entries:
                return JsonResponse({"status": "ignored"}, status=200)

            changes = entries[0].get("changes", [])
            if not changes:
                return JsonResponse({"status": "ignored"}, status=200)

            value = changes[0].get("value", {})
            messages = value.get("messages", [])

            if not messages:
                return JsonResponse({"status": "success"}, status=200)

            message = messages[0]
            from_number = message.get("from")
            message_type = message.get("type")

            # Protection idempotence : ignorer les retries Meta (même wamid déjà traité)
            wamid = message.get("id")
            if wamid:
                _, created = ProcessedMessage.objects.get_or_create(
                    wamid=wamid,
                    defaults={"from_number": from_number}
                )
                if not created:
                    print(f"[RETRY IGNORÉ] wamid={wamid} déjà traité, aucune action.")
                    return JsonResponse({"status": "duplicate_ignored"}, status=200)

            if message_type == "text":
                text_content = message.get("text", {}).get("body", "")
                print(f"\n[MESSAGE TEXTE REÇU DE {from_number}] : {text_content}")
                self._process_and_reply(from_number, text_content=text_content)

            elif message_type == "image":
                media_id = message.get("image", {}).get("id")
                print(f"\n[IMAGE REÇUE DE {from_number}] : media_id={media_id}")
                media_result = self._download_whatsapp_media(media_id) if media_id else None

                if media_result is None:
                    self._send_whatsapp_message(
                        from_number,
                        "⚠️ Impossible de récupérer l'image envoyée. Réessaie."
                    )
                else:
                    image_bytes, mime_type = media_result
                    extension = self._extension_from_mime(mime_type)
                    image_file = ContentFile(image_bytes, name=f"whatsapp_{media_id}{extension}")
                    self._process_and_reply(from_number, image_bytes=image_bytes, image_file=image_file)

            elif message_type == "audio":
                media_id = message.get("audio", {}).get("id")
                print(f"\n[AUDIO REÇU DE {from_number}] : media_id={media_id}")
                media_result = self._download_whatsapp_media(media_id) if media_id else None

                if media_result is None:
                    self._send_whatsapp_message(
                        from_number,
                        "⚠️ Impossible de récupérer l'audio envoyé. Réessaie."
                    )
                else:
                    audio_bytes, _ = media_result
                    self._process_and_reply(from_number, audio_bytes=audio_bytes)

            else:
                print(f"[TYPE NON GÉRÉ] : {message_type}")

            return JsonResponse({"status": "success"}, status=200)

        except Exception as e:
            print(f"[ERREUR WEBHOOK POST] : {str(e)}")
            return JsonResponse({"status": "error", "message": str(e)}, status=200)

    def _process_and_reply(self, from_number, text_content=None, image_bytes=None, audio_bytes=None, image_file=None):
        """
        Logique commune aux 3 types de message :
        résout l'utilisateur, appelle FemiAgentManager, envoie la réponse.
        """
        normalized_number = from_number if from_number.startswith('+') else f"+{from_number}"
        utilisateur = Utilisateur.objects.filter(telephone_whatsapp=normalized_number).first()

        if not utilisateur:
            self._send_whatsapp_message(
                from_number,
                "⚠️ Numéro non reconnu. Merci de contacter votre administrateur pour associer ce numéro à votre compte Femi."
            )
            return

        result = FemiAgentManager.process_transaction_text(
            text_input=text_content,
            image_bytes=image_bytes,
            audio_bytes=audio_bytes,
            image_file=image_file,
            source="WHATSAPP",
            entreprise_id=utilisateur.entreprise.id if utilisateur.entreprise else None,
            utilisateur_id=utilisateur.id
        )

        self._send_whatsapp_message(from_number, result.message)

    def _download_whatsapp_media(self, media_id: str):
        """
        Télécharge un média WhatsApp (image ou audio) en 2 étapes :
        1. Résout l'URL temporaire + mime_type du média via son media_id
        2. Télécharge le contenu binaire depuis cette URL
        Retourne un tuple (bytes, mime_type), ou None en cas d'échec.
        """
        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        if not access_token:
            print("[ERREUR MEDIA] : WHATSAPP_ACCESS_TOKEN non configuré.")
            return None

        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            meta_url = f"https://graph.facebook.com/v19.0/{media_id}"
            response = requests.get(meta_url, headers=headers)
            response.raise_for_status()
            meta_data = response.json()
            media_url = meta_data.get("url")
            mime_type = meta_data.get("mime_type", "")

            if not media_url:
                print(f"[ERREUR MEDIA] : Aucune URL retournée pour media_id={media_id}")
                return None

            media_response = requests.get(media_url, headers=headers)
            media_response.raise_for_status()
            return media_response.content, mime_type

        except requests.exceptions.RequestException as e:
            print(f"[ERREUR TÉLÉCHARGEMENT MEDIA] : {str(e)}")
            return None

    def _extension_from_mime(self, mime_type: str) -> str:
        """Déduit une extension de fichier à partir du mime_type retourné par Meta."""
        mapping = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        return mapping.get(mime_type, ".jpg")

        """
        Télécharge un média WhatsApp (image ou audio) en 2 étapes :
        1. Résout l'URL temporaire du média via son media_id
        2. Télécharge le contenu binaire depuis cette URL
        Retourne les bytes, ou None en cas d'échec.
        """
        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        if not access_token:
            print("[ERREUR MEDIA] : WHATSAPP_ACCESS_TOKEN non configuré.")
            return None

        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            meta_url = f"https://graph.facebook.com/v19.0/{media_id}"
            response = requests.get(meta_url, headers=headers)
            response.raise_for_status()
            media_url = response.json().get("url")

            if not media_url:
                print(f"[ERREUR MEDIA] : Aucune URL retournée pour media_id={media_id}")
                return None

            media_response = requests.get(media_url, headers=headers)
            media_response.raise_for_status()
            return media_response.content

        except requests.exceptions.RequestException as e:
            print(f"[ERREUR TÉLÉCHARGEMENT MEDIA] : {str(e)}")
            return None
        """
        Étape 2 : Réception du message WhatsApp envoyé par l'utilisateur.
>>>>>>> b302b58d6a21a860e9330a836cc90e0bde835d43
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

                      # Protection idempotence : ignorer les retries Meta (même wamid déjà traité)
                      wamid = message.get("id")
                      if wamid:
                          _, created = ProcessedMessage.objects.get_or_create(
                              wamid=wamid,
                              defaults={"from_number": from_number}
                          )
                          if not created:
                              print(f"[RETRY IGNORÉ] wamid={wamid} déjà traité, aucune action.")
                              return JsonResponse({"status": "duplicate_ignored"}, status=200)

                      # Résolution de l'utilisateur via son numéro WhatsApp                

                      # Résolution de l'utilisateur via son numéro WhatsApp
                      # Meta envoie le numéro sans '+' (ex: "22890000000")
                      normalized_number = from_number if from_number.startswith('+') else f"+{from_number}"
                      utilisateur = Utilisateur.objects.filter(telephone_whatsapp=normalized_number).first()

                      if not utilisateur:
                          self._send_whatsapp_message(
                              from_number,
                              "⚠️ Numéro non reconnu. Merci de contacter votre administrateur pour associer ce numéro à votre compte Femi."
                          )
                          return JsonResponse({"status": "unknown_number"}, status=200)

                      # 💥 APPEL DU CERVEAU CENTRAL (FemiAgentManager)
                      result = FemiAgentManager.process_transaction_text(
                          text_input=text_content,
                          source="WHATSAPP",
                          entreprise_id=utilisateur.entreprise.id if utilisateur.entreprise else None,
                          utilisateur_id=utilisateur.id
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