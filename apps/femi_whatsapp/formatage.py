"""Adapte le texte de l'agent au format WhatsApp (gras = *texte*, pas **texte**)."""

import html
import re

_GRAS_MARKDOWN = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_TITRE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_PUCE = re.compile(r"^[ \t]*[-*][ \t]+", re.MULTILINE)


def vers_whatsapp(texte):
    """**gras** -> *gras*, titres Markdown -> gras, puces -> « • », entités HTML décodées."""
    if not isinstance(texte, str):
        return texte
    texte = html.unescape(texte)
    texte = _GRAS_MARKDOWN.sub(r"*\1*", texte)
    texte = _TITRE.sub(r"*\1*", texte)
    texte = _PUCE.sub("• ", texte)
    return texte
