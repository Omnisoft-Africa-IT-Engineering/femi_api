"""
Vérification de l'authenticité des requêtes entrantes du webhook WhatsApp.

Meta signe chaque requête POST avec l'App Secret de l'application, via le
header `X-Hub-Signature-256`. Sans cette vérification, n'importe qui
connaissant l'URL du webhook peut y injecter de faux messages.

Doc Meta : https://developers.facebook.com/docs/graph-api/webhooks/getting-started#validate-payloads
"""

import hashlib
import hmac
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def verify_whatsapp_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    app_secret: Optional[str],
) -> bool:
    """
    Recalcule la signature HMAC-SHA256 du corps brut de la requête avec
    l'App Secret Meta, et la compare (en temps constant) à celle fournie
    dans le header X-Hub-Signature-256.
    """
    if not app_secret:
        logger.error("WHATSAPP_APP_SECRET non configuré : signature non vérifiable.")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_signature = signature_header.split("sha256=", 1)[1]
    computed_signature = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_signature, computed_signature)