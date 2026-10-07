"""
Traitement asynchrone des messages WhatsApp entrants.

Pourquoi une tâche Celery et non un traitement direct dans la vue ?
Meta considère le webhook comme en échec s'il ne répond pas rapidement
(quelques secondes) et RÉ-ENVOIE l'événement. Or FemiRouterManager peut
appeler un LLM, transcrire de l'audio, faire de l'OCR sur une image...
tout ça peut largement dépasser ce délai. En déléguant le traitement à
Celery, la vue répond à Meta en quelques millisecondes (elle a juste
enfilé la tâche), et le vrai travail se fait en arrière-plan.
"""

import logging

from celery import shared_task
from django.core.files.base import ContentFile

from apps.femi_account.models import Utilisateur
from apps.femi_agent.agent.router_manager import FemiRouterManager
from apps.femi_agent.agent.response_writer import write_natural_reply
from apps.femi_agent.parsers.document_loader import detect_mime
from apps.femi_whatsapp.whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)


def _normalize_phone(raw_number: str) -> str:
    return raw_number if raw_number.startswith("+") else f"+{raw_number}"


def _send_notice(client, to: str, facts: str, fallback: str) -> None:
    """Avis court à l'utilisateur, rédigé naturellement (texte fixe
    `fallback` si le rédacteur échoue ou dépasse 8 s)."""
    client.send_text_message(
        to,
        write_natural_reply(
            facts=facts,
            reply_type="error_user_action",
            fallback=fallback,
            timeout=8.0,
        ),
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    autoretry_for=(Exception,),
)
def process_whatsapp_message_task(self, message: dict) -> None:
    """
    Résout l'utilisateur à partir du numéro, délègue le contenu du
    message à FemiRouterManager, puis renvoie la réponse sur WhatsApp.
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

    router_kwargs = {
        "entreprise_id": utilisateur.entreprise_id,
        "utilisateur_id": utilisateur.id,
        "source": "whatsapp",
    }

    if message_type == "text":
        router_kwargs["message_text"] = message.get("text", {}).get("body", "")

    elif message_type == "image":
        media_id = message.get("image", {}).get("id")
        media = client.download_media(media_id) if media_id else None
        if media is None:
            _send_notice(
                client, from_number,
                "L'image envoyée n'a pas pu être récupérée. L'utilisateur peut la renvoyer.",
                "⚠️ Impossible de récupérer l'image envoyée. Réessaie.",
            )
            return
        router_kwargs["image_bytes"] = media.content

    elif message_type == "document":
        # Facture envoyée comme fichier : PDF, ou photo envoyée "en tant que
        # document" (qualité d'origine). Le format réel est détecté sur les
        # octets ; le pipeline OCR (image_bytes) accepte images et PDF.
        media_id = message.get("document", {}).get("id")
        media = client.download_media(media_id) if media_id else None
        if media is None:
            _send_notice(
                client, from_number,
                "Le document envoyé n'a pas pu être récupéré. L'utilisateur peut le renvoyer.",
                "⚠️ Impossible de récupérer le document envoyé. Réessaie.",
            )
            return
        if detect_mime(media.content) is None:
            _send_notice(
                client, from_number,
                "Le fichier envoyé n'est pas dans un format que Femi sait lire. "
                "Formats acceptés : images (JPEG, PNG, WebP) et PDF. "
                "L'utilisateur peut renvoyer sa facture dans l'un de ces formats.",
                "⚠️ Je ne sais lire que les images (JPEG, PNG, WebP) et les PDF. "
                "Envoie-moi la facture dans l'un de ces formats.",
            )
            return
        router_kwargs["image_bytes"] = media.content

    elif message_type == "audio":
        media_id = message.get("audio", {}).get("id")
        media = client.download_media(media_id) if media_id else None
        if media is None:
            _send_notice(
                client, from_number,
                "Le message vocal n'a pas pu être récupéré. L'utilisateur peut le renvoyer.",
                "⚠️ Impossible de récupérer l'audio envoyé. Réessaie.",
            )
            return
        router_kwargs["audio_bytes"] = media.content

    else:
        logger.info("Type de message WhatsApp non géré : %s", message_type)
        return

    try:
        result = FemiRouterManager.route_message(**router_kwargs)
    except Exception:
        logger.exception("Échec du traitement FemiRouterManager pour %s", from_number)
        client.send_text_message(from_number, "⚠️ Une erreur est survenue lors du traitement de votre message.")
        raise  # laisse Celery retenter selon autoretry_for

    client.send_text_message(from_number, result.message)