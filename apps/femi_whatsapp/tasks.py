"""
Traitement asynchrone des messages WhatsApp entrants.

Pourquoi une tâche Celery et non un traitement direct dans la vue ?
Meta considère le webhook comme en échec s'il ne répond pas rapidement
(quelques secondes) et RÉ-ENVOIE l'événement. Or FemiAgentManager peut
appeler un LLM, transcrire de l'audio, faire de l'OCR sur une image...
tout ça peut largement dépasser ce délai. En déléguant le traitement à
Celery, la vue répond à Meta en quelques millisecondes (elle a juste
enfilé la tâche), et le vrai travail se fait en arrière-plan.
"""

import logging

from celery import shared_task
from django.core.files.base import ContentFile

from apps.femi_account.models import Utilisateur
from apps.femi_agent.agent.manager import FemiAgentManager
from apps.femi_whatsapp.whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)


def _normalize_phone(raw_number: str) -> str:
    return raw_number if raw_number.startswith("+") else f"+{raw_number}"


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    autoretry_for=(Exception,),
)
def process_whatsapp_message_task(self, message: dict) -> None:
    """
    Résout l'utilisateur à partir du numéro, délègue le contenu du
    message à FemiAgentManager, puis renvoie la réponse sur WhatsApp.
    """
    client = WhatsAppClient()
    from_number = message.get("from")
    message_type = message.get("type")

    if not from_number:
        logger.error("Message WhatsApp sans expéditeur, ignoré : %s", message)
        return

    normalized_number = _normalize_phone(from_number)
    utilisateur = (
        Utilisateur.objects.filter(telephone_whatsapp=normalized_number)
        .select_related("entreprise")
        .first()
    )

    if not utilisateur:
        client.send_text_message(
            from_number,
            "⚠️ Numéro non reconnu. Merci de contacter votre administrateur pour "
            "associer ce numéro à votre compte Femi.",
        )
        return

    agent_kwargs = {
        "source": "WHATSAPP",
        "entreprise_id": utilisateur.entreprise_id,
        "utilisateur_id": utilisateur.id,
    }

    if message_type == "text":
        agent_kwargs["text_input"] = message.get("text", {}).get("body", "")

    elif message_type == "image":
        media_id = message.get("image", {}).get("id")
        media = client.download_media(media_id) if media_id else None
        if media is None:
            client.send_text_message(from_number, "⚠️ Impossible de récupérer l'image envoyée. Réessaie.")
            return
        agent_kwargs["image_bytes"] = media.content
        agent_kwargs["image_file"] = ContentFile(media.content, name=f"whatsapp_{media_id}{media.extension}")

    elif message_type == "audio":
        media_id = message.get("audio", {}).get("id")
        media = client.download_media(media_id) if media_id else None
        if media is None:
            client.send_text_message(from_number, "⚠️ Impossible de récupérer l'audio envoyé. Réessaie.")
            return
        agent_kwargs["audio_bytes"] = media.content

    else:
        logger.info("Type de message WhatsApp non géré : %s", message_type)
        return

    try:
        result = FemiAgentManager.process_transaction_text(**agent_kwargs)
    except Exception:
        logger.exception("Échec du traitement FemiAgentManager pour %s", from_number)
        client.send_text_message(from_number, "⚠️ Une erreur est survenue lors du traitement de votre message.")
        raise  # laisse Celery retenter selon autoretry_for

    client.send_text_message(from_number, result.message)