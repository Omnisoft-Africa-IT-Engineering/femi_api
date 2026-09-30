"""
Gestion de la mémoire conversationnelle multi-canal de Femi.

Objectif : donner au ROUTER_PROMPT un historique récent de la conversation
(utilisateur + agent), tous canaux confondus (WhatsApp, Mobile), afin que
Femi conserve le contexte d'un message à l'autre au lieu de traiter chaque
message de façon totalement isolée.

Règle de regroupement (validée avec l'utilisateur, jamais codée jusqu'ici) :

- Une "Conversation" regroupe les messages d'un même utilisateur (pour une
  entreprise donnée) tant que le dernier message date de moins de
  CONVERSATION_GROUPING_HOURS heures. Passé ce délai, un nouveau message
  ouvre une nouvelle Conversation.
- Le contexte transmis au LLM (Router) se limite aux CONVERSATION_HISTORY_
  WINDOW derniers messages de la Conversation active (USER + AGENT confondus).

Ce module est volontairement isolé de router_manager.py : toute erreur ici
(DB indisponible, etc.) ne doit JAMAIS faire échouer le traitement principal
d'un message — voir l'usage de safe_* dans router_manager.py.
"""

import logging
from datetime import timedelta
from typing import Optional

from django.utils import timezone

from apps.femi_account.models import (
    Conversation,
    ConversationHistory,
    Entreprise,
    Operation,
    Utilisateur,
)

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------
# Paramètres de la règle (validés en discussion avec l'utilisateur)
# ----------------------------------------------------------------

CONVERSATION_GROUPING_HOURS = 24
CONVERSATION_HISTORY_WINDOW = 6

# Normalisation des valeurs de "source"/"canal" utilisées ailleurs dans le
# code (router_manager.py reçoit "MOBILE", "whatsapp", etc.) vers les
# choices réels du modèle Conversation ('WHATSAPP', 'MOBILE').
_CANAL_ALIASES = {
    "WHATSAPP": "WHATSAPP",
    "MOBILE": "MOBILE",
    "API": "MOBILE",
}


def normalize_canal(source: Optional[str]) -> str:
    """Convertit un `source` libre (ex: 'whatsapp', 'MOBILE', 'API')
    vers une valeur valide de Conversation.CANAL_CHOICES.

    Par défaut, retombe sur 'MOBILE' si la valeur est inconnue plutôt que
    de lever une exception : la conversation ne doit jamais bloquer le
    traitement principal du message.
    """

    if not source:
        return "MOBILE"

    return _CANAL_ALIASES.get(source.strip().upper(), "MOBILE")


def get_or_create_active_conversation(
    utilisateur: Utilisateur,
    entreprise: Entreprise,
    canal: str,
) -> Conversation:
    """Retourne la Conversation active (< 24h) de l'utilisateur pour cette
    entreprise, ou en crée une nouvelle si aucune n'est active.

    Une Conversation est "globale, tous canaux confondus" (voir docstring
    du modèle) : on ne groupe donc PAS par canal, uniquement par
    (utilisateur, entreprise). `canal` ne sert qu'à renseigner
    `canal_origine` lors de la création.
    """

    seuil = timezone.now() - timedelta(
        hours=CONVERSATION_GROUPING_HOURS
    )

    conversation = (
        Conversation.objects
        .filter(
            utilisateur=utilisateur,
            entreprise=entreprise,
            updated_at__gte=seuil,
        )
        .order_by("-updated_at")
        .first()
    )

    if conversation is not None:
        return conversation

    return Conversation.objects.create(
        utilisateur=utilisateur,
        entreprise=entreprise,
        canal_origine=canal,
    )


def build_history_text(
    conversation: Conversation,
    limit: int = CONVERSATION_HISTORY_WINDOW,
) -> Optional[str]:
    """Construit le texte d'historique attendu par RouterExecutor
    (paramètre `history`), à partir des derniers messages de la
    Conversation, dans l'ordre chronologique.

    Retourne None si la conversation est vide (RouterExecutor utilisera
    alors son DEFAULT_HISTORY "(aucun historique)").
    """

    derniers = list(
        conversation.messages
        .order_by("-created_at")[:limit]
    )

    if not derniers:
        return None

    derniers.reverse()

    lignes = []

    for msg in derniers:

        role = (
            "UTILISATEUR"
            if msg.expediteur == "USER"
            else "FEMI"
        )

        contenu = (
            msg.contenu_texte
            or f"[{msg.get_type_message_display()} sans texte exploitable]"
        )

        lignes.append(f"{role}: {contenu}")

    return "\n".join(lignes)


def _touch_conversation(conversation: Conversation) -> None:
    """Met à jour Conversation.updated_at au moment d'un nouveau message.

    Sans cela, la fenêtre de regroupement de 24 h se calcule depuis la
    création de la Conversation et non depuis son dernier message.
    Utilise .update() (pas .save()) pour éviter tout effet de bord.
    """

    try:
        Conversation.objects.filter(pk=conversation.pk).update(
            updated_at=timezone.now()
        )
    except Exception:
        logger.exception(
            "[conversation_manager] Échec de la mise à jour de "
            "updated_at (conversation_id=%s) — ignoré.",
            getattr(conversation, "id", None),
        )


def record_message(
    conversation: Conversation,
    canal: str,
    expediteur: str,
    contenu_texte: Optional[str],
    type_message: str = "TEXTE",
    fichier_url: Optional[str] = None,
    operation: Optional[Operation] = None,
) -> Optional[ConversationHistory]:
    """Enregistre un message (utilisateur ou agent) dans l'historique.

    Ne lève jamais d'exception : une conversation est un "plus", jamais un
    pré-requis pour qu'un message métier soit traité et persisté.
    """

    try:
        message = ConversationHistory.objects.create(
            conversation=conversation,
            canal=canal,
            expediteur=expediteur,
            type_message=type_message,
            contenu_texte=contenu_texte,
            fichier_url=fichier_url,
            operation=operation,
        )
        _touch_conversation(conversation)
        return message
    except Exception:
        logger.exception(
            "[conversation_manager] "
            "Échec de l'enregistrement d'un message "
            "(conversation_id=%s, expediteur=%s) — ignoré, "
            "n'affecte pas le traitement principal.",
            getattr(conversation, "id", None),
            expediteur,
        )
        return None


def resolve_type_message(
    image_bytes: Optional[bytes],
    audio_bytes: Optional[bytes],
) -> str:
    """Détermine le type_message ('IMAGE'/'AUDIO'/'TEXTE') à partir des
    entrées reçues par route_message()/aroute_message()."""

    if image_bytes:
        return "IMAGE"

    if audio_bytes:
        return "AUDIO"

    return "TEXTE"