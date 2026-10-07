"""Lecture des documents reçus (image ou PDF) avant l'OCR vision.

Les canaux (WhatsApp, API mobile) transmettent des bytes bruts. Ce module
identifie leur vrai format à partir de leurs premiers octets — jamais à
partir du nom de fichier ou du type MIME déclaré, qui peuvent être faux —
et transforme un PDF en une image JPEG par page, que le pipeline OCR vision
existant sait déjà traiter (quel que soit le fournisseur : Gemini,
Cloudflare, Ollama...).

Aucune dépendance Django : testable seul.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

MIME_PDF = "application/pdf"
MIME_JPEG = "image/jpeg"
MIME_PNG = "image/png"
MIME_WEBP = "image/webp"

SUPPORTED_MIME_TYPES = (MIME_PDF, MIME_JPEG, MIME_PNG, MIME_WEBP)

MIME_TO_EXTENSION = {
    MIME_PDF: ".pdf",
    MIME_JPEG: ".jpg",
    MIME_PNG: ".png",
    MIME_WEBP: ".webp",
}

# Un PDF plus long n'est pas tronqué en silence : il est refusé avec un
# message clair (une facture tronquée donnerait des totaux faux).
MAX_PDF_PAGES = 5

# Zoom de rendu (72 dpi × zoom) et plafond de la plus grande dimension,
# pour rester lisible sans produire d'images énormes.
_PDF_RENDER_ZOOM = 2.0
_MAX_IMAGE_SIDE_PX = 2400
_JPEG_QUALITY = 85


class UnsupportedDocumentError(ValueError):
    """Document illisible ou non pris en charge. `str(exc)` est un message
    en français destiné à être montré à l'utilisateur."""


def detect_mime(content: bytes) -> Optional[str]:
    """Identifie le format réel à partir des premiers octets.
    Retourne None si le format n'est pas pris en charge."""
    if not content:
        return None
    head = content[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return MIME_JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return MIME_PNG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return MIME_WEBP
    # La spec PDF tolère quelques octets avant l'en-tête "%PDF-"
    if b"%PDF-" in content[:1024]:
        return MIME_PDF
    return None


def extension_for(content: bytes) -> str:
    return MIME_TO_EXTENSION.get(detect_mime(content) or "", ".jpg")


def _import_pymupdf():
    try:
        import pymupdf  # PyMuPDF >= 1.24.3
        return pymupdf
    except ImportError:
        import fitz  # ancien nom du même paquet
        return fitz


def pdf_to_page_images(pdf_bytes: bytes, max_pages: int = MAX_PDF_PAGES) -> list[bytes]:
    """Rend chaque page d'un PDF en image JPEG.

    Lève UnsupportedDocumentError (message prêt à afficher) si le PDF est
    corrompu, protégé par mot de passe, vide ou trop long.
    """
    try:
        pymupdf = _import_pymupdf()
    except ImportError as exc:  # dépendance absente de l'environnement
        logger.error("PyMuPDF non installé : lecture des PDF impossible (%s)", exc)
        raise UnsupportedDocumentError(
            "La lecture des PDF n'est pas disponible pour le moment."
        ) from exc

    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        logger.warning("PDF illisible : %s", exc)
        raise UnsupportedDocumentError(
            "Ce PDF est illisible ou corrompu."
        ) from exc

    try:
        if document.needs_pass:
            raise UnsupportedDocumentError(
                "Ce PDF est protégé par un mot de passe, je ne peux pas l'ouvrir."
            )
        page_count = document.page_count
        if page_count == 0:
            raise UnsupportedDocumentError("Ce PDF ne contient aucune page.")
        if page_count > max_pages:
            raise UnsupportedDocumentError(
                f"Ce PDF contient {page_count} pages, le maximum que je lis est "
                f"{max_pages}. Envoie-moi seulement les pages utiles."
            )

        images: list[bytes] = []
        for page in document:
            zoom = _PDF_RENDER_ZOOM
            longest_side = max(page.rect.width, page.rect.height) * zoom
            if longest_side > _MAX_IMAGE_SIDE_PX:
                zoom *= _MAX_IMAGE_SIDE_PX / longest_side
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(zoom, zoom),
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            images.append(pixmap.tobytes("jpeg", jpg_quality=_JPEG_QUALITY))
        return images
    except UnsupportedDocumentError:
        raise
    except Exception as exc:
        logger.exception("Échec du rendu du PDF")
        raise UnsupportedDocumentError(
            "Je n'ai pas réussi à lire ce PDF."
        ) from exc
    finally:
        document.close()


def to_page_images(content: bytes) -> list[bytes]:
    """Point d'entrée unique : retourne la liste des images à analyser.

    - image (JPEG/PNG/WebP) → [content] tel quel ;
    - PDF → une image JPEG par page ;
    - autre chose → UnsupportedDocumentError.
    """
    mime = detect_mime(content)
    if mime == MIME_PDF:
        return pdf_to_page_images(content)
    if mime in (MIME_JPEG, MIME_PNG, MIME_WEBP):
        return [content]
    raise UnsupportedDocumentError(
        "Je ne sais lire que les images (JPEG, PNG, WebP) et les PDF."
    )