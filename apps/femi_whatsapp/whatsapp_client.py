"""
Client pour l'API Meta Cloud WhatsApp.

Isole tous les appels réseau (envoi de message, téléchargement de média)
dans une seule classe : timeouts explicites, retries sur erreurs serveur,
gestion d'erreurs centralisée, aucune impression de log à droite à gauche.
"""

import logging
import mimetypes
from dataclasses import dataclass
from typing import Optional

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"
REQUEST_TIMEOUT = 10  # secondes — évite qu'un appel Meta ne bloque le worker indéfiniment
MAX_MEDIA_SIZE_BYTES = 16 * 1024 * 1024  # 16 Mo — limite raisonnable, évite un média piégé/énorme

MIME_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@dataclass
class DownloadedMedia:
    content: bytes
    mime_type: str
    extension: str


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


class WhatsAppClient:
    """Enveloppe fine et testable autour de l'API Graph WhatsApp."""

    def __init__(self):
        self.phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.access_token = settings.WHATSAPP_ACCESS_TOKEN
        self.session = _build_session()

    @property
    def _is_configured(self) -> bool:
        return bool(self.phone_number_id and self.access_token)

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def send_text_message(self, to: str, body: str) -> bool:
        """Envoie un message texte. Retourne True si l'envoi a réussi."""
        if not self._is_configured:
            logger.warning("WhatsApp non configuré — simulation d'envoi à %s : %s", to, body)
            return True

        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }

        try:
            response = self.session.post(
                url,
                json=payload,
                headers={**self._headers, "Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException:
            logger.exception("Échec de l'envoi du message WhatsApp à %s", to)
            return False

    def download_media(self, media_id: str) -> Optional[DownloadedMedia]:
        """
        Télécharge un média WhatsApp en 2 étapes (résolution URL temporaire
        puis téléchargement binaire). Retourne None en cas d'échec, de
        média manquant, ou de média dépassant MAX_MEDIA_SIZE_BYTES.
        """
        if not self._is_configured:
            logger.warning("WhatsApp non configuré — téléchargement impossible pour media_id=%s", media_id)
            return None

        try:
            meta_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}"
            meta_response = self.session.get(meta_url, headers=self._headers, timeout=REQUEST_TIMEOUT)
            meta_response.raise_for_status()
            meta_data = meta_response.json()

            media_url = meta_data.get("url")
            mime_type = meta_data.get("mime_type", "")
            file_size = meta_data.get("file_size", 0)

            if not media_url:
                logger.error("Aucune URL retournée par Meta pour media_id=%s", media_id)
                return None

            if file_size and file_size > MAX_MEDIA_SIZE_BYTES:
                logger.error("Média trop volumineux (%s octets) pour media_id=%s", file_size, media_id)
                return None

            media_response = self.session.get(media_url, headers=self._headers, timeout=REQUEST_TIMEOUT)
            media_response.raise_for_status()

            extension = MIME_TO_EXTENSION.get(mime_type) or (mimetypes.guess_extension(mime_type) or ".bin")

            return DownloadedMedia(content=media_response.content, mime_type=mime_type, extension=extension)

        except requests.exceptions.RequestException:
            logger.exception("Échec du téléchargement du média media_id=%s", media_id)
            return None