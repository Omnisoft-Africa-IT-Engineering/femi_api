"""
Utilitaire d'upload vers Supabase Storage.

Isole l'appel réseau (upload d'un fichier binaire + récupération de
l'URL publique) dans une fonction unique, pour ne pas disperser cette
logique ailleurs. Vit dans femi_account (et non femi_agent) car
PieceJustificative est un modèle central, potentiellement utilisé par
d'autres apps (femi_api, femi_whatsapp) sans passer par l'agent IA.
"""

import logging
import uuid

from django.conf import settings
from supabase import create_client

logger = logging.getLogger(__name__)

_supabase_client = None


def _get_client():
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    return _supabase_client


def upload_file(content: bytes, filename: str, content_type: str) -> str:
    """
    Upload un fichier binaire vers le bucket Supabase Storage configuré.
    Retourne l'URL publique du fichier uploadé.

    Lève une exception en cas d'échec — c'est à l'appelant de décider
    comment réagir.
    """
    client = _get_client()
    bucket = settings.SUPABASE_STORAGE_BUCKET

    storage_path = f"{uuid.uuid4()}_{filename}"

    client.storage.from_(bucket).upload(
        path=storage_path,
        file=content,
        file_options={"content-type": content_type},
    )

    return client.storage.from_(bucket).get_public_url(storage_path)