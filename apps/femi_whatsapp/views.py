"""
Webhook Meta Cloud API pour WhatsApp.

Responsabilités volontairement limitées :
1. GET  : valider l'abonnement au webhook (handshake Meta).
2. POST : vérifier la signature, parser le payload, enfiler chaque
          message vers une tâche asynchrone. Toute la logique métier
          (résolution utilisateur, appel à l'agent IA, envoi de la
          réponse) vit dans tasks.py.
"""

import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema

from apps.femi_whatsapp.models import ProcessedMessage
from apps.femi_whatsapp.security import verify_whatsapp_signature
from apps.femi_whatsapp.tasks import process_whatsapp_message_task

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class WhatsAppWebhookView(View):
    """Webhook pour la réception et la réponse aux messages WhatsApp (Meta Cloud API)."""

    @extend_schema(exclude=True)
    def get(self, request):
        """Étape 1 : validation du webhook par Meta (handshake hub.challenge)."""
        mode = request.GET.get("hub.mode")
        received_token = (request.GET.get("hub.verify_token") or "").strip()
        challenge = request.GET.get("hub.challenge", "")

        if mode == "subscribe" and received_token == settings.WHATSAPP_VERIFY_TOKEN:
            return HttpResponse(challenge, status=200)

        logger.warning("Échec de vérification du webhook (mode=%s).", mode)
        return HttpResponse("Jeton de vérification invalide.", status=403)

    @extend_schema(exclude=True)
    def post(self, request):
        """Étape 2 : réception des événements WhatsApp (messages, statuts...)."""
        signature = request.headers.get("X-Hub-Signature-256")
        if not verify_whatsapp_signature(request.body, signature, settings.WHATSAPP_APP_SECRET):
            logger.warning("Signature webhook WhatsApp invalide — requête rejetée.")
            return HttpResponse(status=403)

        try:
            body = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("Payload webhook WhatsApp illisible.")
            return JsonResponse({"status": "invalid_payload"}, status=400)

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
        """
        main
        try:
            self._dispatch(body)
        except Exception:
            # On répond quand même 200 : Meta réessaierait sinon en boucle
            # un événement déjà partiellement traité. L'erreur est loguée
            # pour investigation, pas avalée silencieusement.
            logger.exception("Erreur lors du traitement du webhook WhatsApp.")

        return JsonResponse({"status": "success"}, status=200)

    def _dispatch(self, body: dict) -> None:
        """Parcourt entry/changes/messages — Meta peut en regrouper plusieurs par appel."""
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                if "statuses" in value:
                    # Accusés de livraison/lecture : rien à répondre, à
                    # journaliser/persister séparément si besoin métier.
                    continue

                for message in value.get("messages", []):
                    self._enqueue_message(message)

    def _enqueue_message(self, message: dict) -> None:
        wamid = message.get("id")

        if wamid:
            _, created = ProcessedMessage.objects.get_or_create(
                wamid=wamid,
                defaults={"from_number": message.get("from")},
            )
            if not created:
                logger.info("Retry Meta ignoré pour wamid=%s (déjà traité).", wamid)
                return

        process_whatsapp_message_task.delay(message)