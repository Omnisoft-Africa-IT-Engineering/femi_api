"""
Gestion des actions en attente de clarification (pending_action).

Problème résolu : quand Femi demande une précision (ex. « facture achetée
ou vendue ? ») et que l'utilisateur répond en plusieurs messages courts
(« c'est un achat et c'est déjà payé », puis « 566400 »), chaque message
était envoyé seul à l'agent spécialisé, qui perdait donc le contexte.

Principe :

- Quand un intent reste en clarification, on stocke en base (sur
  Conversation.pending_action) : agent, action_type, texte d'origine,
  champs manquants.
- Au message suivant, ces entrées sont fournies au Router (pending_action /
  missing_fields du prompt). Si le Router répond merge_context=true pour un
  intent, le texte d'origine est fusionné avec le nouveau fragment avant
  l'appel à l'agent.
- Une entrée est retirée dès que l'agent n'a plus besoin de clarification,
  ou remplacée si une nouvelle clarification survient. Elle expire après
  PENDING_ACTION_TTL_MINUTES.

Ce module est best-effort, comme conversation_manager.py : aucune erreur
ici ne doit faire échouer le traitement principal d'un message.

Limite connue : l'image d'origine n'est pas conservée. Le texte lu par l'OCR
l'est (via le texte d'origine), mais la pièce justificative n'est pas
rattachée à l'opération enregistrée lors d'un message de suite.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from django.utils import timezone

from apps.femi_account.models import Conversation

logger = logging.getLogger(__name__)

PENDING_ACTION_TTL_MINUTES = 30
PENDING_MAX_ENTRIES = 3
PENDING_MAX_TEXT_CHARS = 6000
PENDING_PREVIEW_CHARS = 300


def _now_iso() -> str:
    return timezone.now().isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)

    return parsed


def _is_live(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False

    if not entry.get("agent") or not entry.get("text"):
        return False

    updated_at = _parse_iso(entry.get("updated_at"))

    if updated_at is None:
        return False

    limit = timezone.now() - timedelta(minutes=PENDING_ACTION_TTL_MINUTES)

    return updated_at >= limit


class PendingTurn:
    """État des actions en attente pour UN tour de conversation.

    Cycle d'utilisation dans route_message()/aroute_message() :

        pending = PendingTurn.load(conversation)
        RouterExecutor.execute(..., **pending.router_kwargs())
        ... pour chaque intent :
            segment, from_document = pending.resolve(intent, from_document)
            ... appel de l'agent avec `segment` ...
            pending.observe(intent, segment, from_document, needs_clarification, missing)
        pending.commit()
    """

    def __init__(
        self,
        conversation: Optional[Conversation],
        entries: list[dict],
        turn_text: str = "",
        intents_count: int = 0,
    ) -> None:
        self.conversation = conversation
        self.entries = entries
        self.turn_text = turn_text or ""
        self.intents_count = intents_count

        self._consumed: set[int] = set()
        self._observed: list[dict] = []
        self._merged_single_text: Optional[str] = None

    # ------------------------------------------------------------
    # Chargement
    # ------------------------------------------------------------

    @classmethod
    def load(cls, conversation: Optional[Conversation]) -> "PendingTurn":
        """Charge les entrées encore valides. Ne lève jamais d'exception."""

        if conversation is None:
            return cls(None, [])

        try:
            stored = (
                Conversation.objects
                .filter(pk=conversation.pk)
                .values_list("pending_action", flat=True)
                .first()
            )

            raw_entries = []

            if isinstance(stored, dict):
                raw_entries = stored.get("entries") or []

            entries = [e for e in raw_entries if _is_live(e)]

            return cls(conversation, entries)

        except Exception:
            logger.exception(
                "[pending_action] Échec du chargement — poursuite sans "
                "action en attente."
            )
            return cls(conversation, [])

    def start_turn(self, turn_text: str, intents_count: int) -> None:
        """Renseigne le texte complet du tour (message + OCR/STT) et le
        nombre d'intents détectés par le Router."""

        self.turn_text = turn_text or ""
        self.intents_count = intents_count

    # ------------------------------------------------------------
    # Fourniture au Router
    # ------------------------------------------------------------

    def router_kwargs(self) -> dict:
        """Arguments pending_action / missing_fields pour RouterExecutor.

        Vide s'il n'y a aucune entrée : les valeurs par défaut du
        RouterExecutor (« null » / « [] ») s'appliquent alors.
        """

        if not self.entries:
            return {}

        try:
            pending_payload = [
                {
                    "agent": e["agent"],
                    "action_type": e.get("action_type"),
                    "texte_precedent": (e.get("text") or "")[
                        :PENDING_PREVIEW_CHARS
                    ],
                    "missing_fields": e.get("missing_fields") or [],
                }
                for e in self.entries
            ]

            missing: list[str] = []

            for e in self.entries:
                for field in e.get("missing_fields") or []:
                    if field not in missing:
                        missing.append(field)

            return {
                "pending_action": json.dumps(
                    pending_payload, ensure_ascii=False
                ),
                "missing_fields": json.dumps(missing, ensure_ascii=False),
            }

        except Exception:
            logger.exception(
                "[pending_action] Échec de sérialisation pour le Router."
            )
            return {}

    # ------------------------------------------------------------
    # Résolution avant l'appel à l'agent
    # ------------------------------------------------------------

    def _find_entry(self, intent) -> Optional[int]:
        candidates = [
            i
            for i, e in enumerate(self.entries)
            if i not in self._consumed and e.get("agent") == intent.agent
        ]

        if not candidates:
            return None

        for i in candidates:
            if self.entries[i].get("action_type") == intent.action_type:
                return i

        return candidates[0]

    def resolve(self, intent, from_document: bool) -> tuple[str, bool]:
        """Retourne (segment à envoyer à l'agent, from_document).

        Si le Router a mis merge_context=true et qu'une entrée correspond
        (même agent), le texte d'origine est fusionné avec le nouveau
        fragment. Sinon, le segment du Router est utilisé tel quel.
        """

        segment = intent.raw_segment

        try:
            if not getattr(intent, "merge_context", False):
                return segment, from_document

            index = self._find_entry(intent)

            if index is None:
                return segment, from_document

            entry = self.entries[index]
            self._consumed.add(index)

            merged = f"{entry['text']}\n{intent.raw_segment}".strip()

            if len(merged) > PENDING_MAX_TEXT_CHARS:
                merged = merged[-PENDING_MAX_TEXT_CHARS:]

            if self.intents_count == 1:
                self._merged_single_text = merged

            logger.info(
                "[pending_action] Fusion appliquée: agent=%s | "
                "ancien=%r | nouveau=%r",
                intent.agent,
                entry["text"][:120],
                intent.raw_segment[:120],
            )

            return merged, bool(from_document or entry.get("from_document"))

        except Exception:
            logger.exception(
                "[pending_action] Échec de fusion — segment brut utilisé."
            )
            return segment, from_document

    def effective_raw_text(self, raw_text: str) -> str:
        """Texte brut à conserver sur l'opération enregistrée.

        Quand un seul intent a été fusionné, c'est le texte fusionné (sinon
        l'opération n'aurait que « 566400 » comme texte source).
        """

        return self._merged_single_text or raw_text

    # ------------------------------------------------------------
    # Observation après l'appel à l'agent
    # ------------------------------------------------------------

    def observe(
        self,
        intent,
        segment: str,
        from_document: bool,
        needs_clarification: bool,
        missing_fields: Optional[list[str]] = None,
    ) -> None:
        """Enregistre les intents qui restent en clarification."""

        try:
            if not needs_clarification:
                return

            merged = segment != intent.raw_segment

            if merged or self.intents_count != 1:
                text = segment
            else:
                # Intent unique non fusionné : on garde le texte complet du
                # tour (message + OCR/STT), plus fidèle que le résumé du
                # Router.
                text = self.turn_text or segment

            if len(text) > PENDING_MAX_TEXT_CHARS:
                text = text[-PENDING_MAX_TEXT_CHARS:]

            fields: list[str] = []

            for field in list(intent.missing_fields or []) + list(
                missing_fields or []
            ):
                if field not in fields:
                    fields.append(field)

            self._observed.append(
                {
                    "agent": intent.agent,
                    "action_type": intent.action_type,
                    "text": text,
                    "missing_fields": fields,
                    "from_document": bool(from_document),
                    "updated_at": _now_iso(),
                }
            )

        except Exception:
            logger.exception(
                "[pending_action] Échec de l'observation d'un intent."
            )

    # ------------------------------------------------------------
    # Sauvegarde
    # ------------------------------------------------------------

    def commit(self) -> None:
        """Écrit l'état final en base. Ne lève jamais d'exception.

        Nouvel état = intents encore en clarification à ce tour
        + anciennes entrées non reprises et non remplacées
        (encore valides selon le TTL).
        """

        if self.conversation is None:
            return

        if not self._observed and not self._consumed:
            return  # rien n'a changé : pas d'écriture inutile

        try:
            superseded = {
                (o["agent"], o["action_type"]) for o in self._observed
            }

            kept = [
                e
                for i, e in enumerate(self.entries)
                if i not in self._consumed
                and (e.get("agent"), e.get("action_type")) not in superseded
            ]

            final = (self._observed + kept)[:PENDING_MAX_ENTRIES]

            payload = {"entries": final} if final else None

            Conversation.objects.filter(pk=self.conversation.pk).update(
                pending_action=payload
            )

        except Exception:
            logger.exception(
                "[pending_action] Échec de la sauvegarde — ignoré."
            )