"""
Point d'entrée du nouveau pipeline (Router + agents spécialisés).

Pipeline :

Utilisateur
    ↓
FemiRouterManager
    ↓
OCR / STT si nécessaire
    ↓
RouterExecutor
    ↓
Agent spécialisé
    ↓
Validation / extraction
    ↓
Persistance éventuelle
    ↓
RouterProcessResult
"""

import logging
from typing import Optional, Tuple

from asgiref.sync import sync_to_async

from apps.femi_agent.agent.orchestration.persistence import persist_quote_results
from apps.femi_account.devis_service import (
    MESSAGE_DEVIS_INTERDIT_EMPLOYE,
    DevisInvalideError,
    creer_devis,
    utilisateur_est_employe,
)
from apps.femi_account.models import (
    Conversation,
    Entreprise,
    Operation,
    PieceJustificative,
    Utilisateur,
)
from apps.femi_agent.agent.response_writer import write_natural_reply


from apps.femi_agent.agent.accounting_executor import AccountingExecutor
from apps.femi_agent.agent.customer_executor import CustomerExecutor
from apps.femi_agent.agent.quote_executor import QuoteExecutor
from apps.femi_agent.agent.financial_analyst_executor import (
    FinancialAnalystExecutor,
)
from apps.femi_agent.agent.accounting_modify_executor import (
    AccountingModifyExecutor,
)

from apps.femi_agent.agent.accounting_manager import (
    save_accounting_transactions,
)
from apps.femi_agent.agent.conversation_manager import (
    build_history_text,
    get_or_create_active_conversation,
    normalize_canal,
    record_message,
    resolve_type_message,
)
from apps.femi_agent.agent.pending_action import PendingTurn
from apps.femi_agent.agent.tools.operations import apply_operation_change, list_recent_operations
from apps.femi_agent.agent.social_replies import (
    build_off_topic_reply,
    build_social_reply,
    classify_social_message,
    social_reply_context,
)
from django.utils import timezone

from apps.femi_account.integrations.supabase_storage import upload_file

from apps.femi_agent.agent.ocr_executor import (
    OcrExecutor,
    OcrExecutionError,
)

from apps.femi_agent.agent.ocr_postprocess import (
    build_document_text,
    extract_control_anomalies,
)
from apps.femi_agent.parsers.document_loader import (
    UnsupportedDocumentError,
    extension_for,
    detect_mime,
)

from apps.femi_agent.agent.stt_executor import (
    SttExecutor,
    SttExecutionError,
)

from apps.femi_agent.agent.router_executor import (
    RouterExecutor,
    RouterExecutionError,
)

from apps.femi_agent.parsers.audio_parser import transcribe_audio

from apps.femi_agent.schemas import (
    AccountingExtractionResult,
    AccountingModifyResolutionResult,
    AccountingModifyProposeChangeResult,
    AccountingModifySearchResult,
    CustomerExtractionOutput,
    FinalAnswerOutput,
    QuoteExtractionResult,
    RouterIntent,
    RouterOutput,
    RouterProcessResult,
    ToolSelectionOutput,
    ValidationOutput,
)

from apps.femi_agent.agent.orchestration.ingress import (
    IngressError,
    abuild_combined_text,
    build_combined_text,
)

from apps.femi_agent.agent.orchestration.dispatch import (
    adispatch_intent,
    dispatch_intent,
)

from apps.femi_agent.agent.orchestration.replies import (
    build_clarification_message,
    build_static_clarification_message,
    build_understood_recap,
    greeting_result,
    is_pure_greeting,
    natural_notice,
    natural_off_topic,
)

from apps.femi_agent.agent.orchestration.messages import (
    format_montant,
    format_values_for_facts,
    summarize_candidates,
    summarize_customer_payment,
    summarize_devis,
    summarize_propose_change,
)

logger = logging.getLogger(__name__)

# Délais maximaux du rédacteur pour les messages courts (salutations,
# avis d'erreur) : au-delà, on envoie le texte de secours plutôt que de
# faire attendre l'utilisateur.
_SHORT_REPLY_TIMEOUT =25


DispatchResult = Optional[
    AccountingExtractionResult
    | ToolSelectionOutput
    | FinalAnswerOutput
    | AccountingModifySearchResult
    | AccountingModifyResolutionResult
    | AccountingModifyProposeChangeResult
    | QuoteExtractionResult
]


class RouterInputError(Exception):
    """
    Aucune donnée textuelle ou multimédia exploitable n'a été fournie.
    """

    pass




def _message_looks_like_stock(text: str) -> bool:
    """Heuristique : vocabulaire stock / quantité produit → pas la compta."""
    import re
    t = (text or "").casefold()
    if not t.strip():
        return False
    mots = (
        "entrée stock", "entree stock", "sortie stock", "en stock",
        "mon stock", "inventaire", "réappro", "reappro",
        "ajout ", "ajoute ", "j'ai reçu", "j ai reçu", "j'ai recu", "j ai recu",
    )
    if any(m in t for m in mots):
        return True
    if re.search(
        r"\b\d+[\s.,]*\d*\s*(sacs?|cartons?|pi[eè]ces?|kg|litres?|bouteilles?|bo[iî]tes?)\b",
        t,
    ):
        return True
    return False


def _force_stock_intent_if_needed(intents, message_text: str):
    """Si le message est clairement stock, force agent STOCK."""
    if not intents or not _message_looks_like_stock(message_text):
        return intents
    try:
        from apps.femi_agent.schemas.router import RouterIntent
    except Exception:
        return intents
    fixed = []
    t = (message_text or "").casefold()
    for intent in intents:
        if getattr(intent, "agent", None) in (
            "ACCOUNTING", "ACCOUNTING_MODIFY", "UNKNOWN", "GENERAL",
        ):
            data = intent.model_dump() if hasattr(intent, "model_dump") else dict(intent)
            data["agent"] = "STOCK"
            if any(x in t for x in ("mon stock", "liste mes produits", "état du stock", "etat du stock")):
                data["action_type"] = "READ"
            else:
                data["action_type"] = "CREATE"
            data["merge_context"] = False
            data["needs_clarification"] = False
            data["missing_fields"] = []
            try:
                fixed.append(RouterIntent(**data))
            except Exception:
                fixed.append(intent)
        else:
            fixed.append(intent)
    return fixed


class FemiRouterManager:
    """
    Gestionnaire central du nouveau pipeline :

        Router
            ↓
        Agent spécialisé
            ↓
        Persistance éventuelle
    """

    # ============================================================
    # POINT D'ENTRÉE ASYNCHRONE
    # ============================================================

    @classmethod
    async def aroute_message(
        cls,
        message_text: Optional[str] = None,
        entreprise_id: Optional[str] = None,
        utilisateur_id: Optional[str] = None,
        source: str = "API",
        image_bytes: Optional[bytes] = None,
        audio_bytes: Optional[bytes] = None,
    ) -> RouterProcessResult:

        try:

            # ----------------------------------------------------
            # 1. Résolution du tenant et de l'utilisateur
            # ----------------------------------------------------

            entreprise, utilisateur = await sync_to_async(
                cls._get_tenant_context_stricte
            )(
                entreprise_id,
                utilisateur_id,
            )

            # ----------------------------------------------------
            # 1bis. Contexte conversationnel (historique multi-canal,
            # best-effort — voir conversation_manager.py)
            # ----------------------------------------------------

            conversation, canal, type_message, history_text = (
                await sync_to_async(
                    cls._setup_conversation_context
                )(
                    utilisateur,
                    entreprise,
                    source,
                    image_bytes,
                    audio_bytes,
                )
            )

            # ----------------------------------------------------
            # 1ter. Court-circuit salutation (voir route_message)
            # ----------------------------------------------------

            if (
                message_text
                and not image_bytes
                and not audio_bytes
                and cls._is_pure_greeting(message_text.strip())
            ):
                greeting_result = cls._greeting_result(message_text.strip())

                await cls._arecord_turn(
                    conversation,
                    canal,
                    message_text.strip(),
                    type_message,
                    greeting_result.message,
                )

                return greeting_result

            # ----------------------------------------------------
            # 2. Construction du texte final
            # ----------------------------------------------------

            combined_text = await cls._abuild_combined_text(
                message_text,
                image_bytes,
                audio_bytes,
            )

            # ----------------------------------------------------
            # 3. Routage
            # ----------------------------------------------------

            # Actions en attente de clarification (best-effort)
            pending = await sync_to_async(PendingTurn.load)(
                conversation
            )
            pending._history_text = history_text

            router_output = await RouterExecutor.aexecute(
                combined_text,
                history=history_text,
                **pending.router_kwargs(),
            )
            try:
                forced = _force_stock_intent_if_needed(
                    list(router_output.intents), combined_text
                )
                if forced is not router_output.intents:
                    router_output = router_output.model_copy(update={"intents": forced})
            except Exception:
                logger.exception("[FemiRouterManager] force STOCK ignoré")


            # Force merge si réponse courte à une action en attente
            # (ex. "Mr Toto" après "pour quel client ce devis ?")
            pending.force_merge_on_short_reply(
                router_output.intents,
                combined_text,
            )

            early_msg = await sync_to_async(cls._try_handle_modify_confirmation)(
                pending, combined_text, entreprise
            )
            if early_msg is None:
                early_msg = await sync_to_async(cls._try_handle_list_operations)(
                    combined_text, entreprise
                )
            if early_msg is not None:
                await sync_to_async(pending.commit)()
                await cls._arecord_turn(
                    conversation,
                    canal,
                    combined_text,
                    type_message,
                    early_msg,
                )
                return RouterProcessResult(
                    success=True,
                    message=early_msg,
                    needs_clarification=False,
                    missing_fields=[],
                )

            pending.start_turn(
                combined_text,
                len(router_output.intents),
            )

            # ----------------------------------------------------
            # 4. Dispatch + persistance
            # ----------------------------------------------------

            result = await cls._abuild_result(
                router_output=router_output,
                entreprise=entreprise,
                utilisateur=utilisateur,
                source=source,
                raw_text=combined_text,
                image_bytes=image_bytes,
                pending=pending,
            )

            await sync_to_async(pending.commit)()

            await cls._arecord_turn(
                conversation,
                canal,
                combined_text,
                type_message,
                result.message,
            )

            return result

        except (
            Entreprise.DoesNotExist,
            Utilisateur.DoesNotExist,
        ) as exc:

            logger.error(
                "[FemiRouterManager] "
                "Contexte tenant introuvable (async): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message=str(exc),
            )

        except RouterInputError as exc:

            logger.warning(
                "[FemiRouterManager] "
                "Aucune donnée exploitable (async): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message=str(exc),
            )

        except UnsupportedDocumentError as exc:

            logger.warning(
                "[FemiRouterManager] "
                "Document non pris en charge (async): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message=cls._natural_notice(
                    facts=(
                        "Le fichier envoyé n'a pas pu être lu : "
                        f"{exc}"
                    ),
                    fallback=str(exc),
                ),
            )

        except (
            OcrExecutionError,
            SttExecutionError,
            ValueError,
        ) as exc:

            logger.error(
                "[FemiRouterManager] "
                "Échec OCR/audio (async): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message=cls._natural_notice(
                    facts=(
                        "Le traitement de l'image, du PDF ou de l'audio "
                        "envoyé a échoué (lecture impossible). L'utilisateur "
                        "peut réessayer avec une photo plus nette, un autre "
                        "fichier ou un nouveau vocal."
                    ),
                    fallback=(
                        "Le traitement de l'image ou de l'audio "
                        "a échoué."
                    ),
                ),
            )

        except RouterExecutionError as exc:

            logger.error(
                "[FemiRouterManager] "
                "Échec du Router (async): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message="Le routage du message a échoué.",
            )

        except Exception:

            logger.exception(
                "[FemiRouterManager] "
                "Erreur inattendue (async)"
            )

            return RouterProcessResult(
                success=False,
                message="Une erreur technique est survenue.",
            )

    # ============================================================
    # POINT D'ENTRÉE SYNCHRONE
    # ============================================================

    @classmethod
    def route_message(
        cls,
        message_text: Optional[str] = None,
        entreprise_id: Optional[str] = None,
        utilisateur_id: Optional[str] = None,
        source: str = "API",
        image_bytes: Optional[bytes] = None,
        audio_bytes: Optional[bytes] = None,
    ) -> RouterProcessResult:

        try:

            # ----------------------------------------------------
            # 1. Résolution tenant + utilisateur
            # ----------------------------------------------------

            entreprise, utilisateur = (
                cls._get_tenant_context_stricte(
                    entreprise_id,
                    utilisateur_id,
                )
            )

            # ----------------------------------------------------
            # 1bis. Contexte conversationnel (historique multi-canal,
            # best-effort — voir conversation_manager.py)
            # ----------------------------------------------------

            conversation, canal, type_message, history_text = (
                cls._setup_conversation_context(
                    utilisateur,
                    entreprise,
                    source,
                    image_bytes,
                    audio_bytes,
                )
            )

            # ----------------------------------------------------
            # 1ter. Court-circuit salutation (pas d'image/audio,
            # message très court type "Salut Femi") — évite un
            # appel LLM inutile et répond immédiatement.
            # ----------------------------------------------------

            if (
                message_text
                and not image_bytes
                and not audio_bytes
                and cls._is_pure_greeting(message_text.strip())
            ):
                greeting_result = cls._greeting_result(message_text.strip())

                cls._record_turn(
                    conversation,
                    canal,
                    message_text.strip(),
                    type_message,
                    greeting_result.message,
                )

                return greeting_result

            # ----------------------------------------------------
            # 2. Texte final
            # ----------------------------------------------------

            combined_text = cls._build_combined_text(
                message_text,
                image_bytes,
                audio_bytes,
            )

            logger.info(
                "[FemiRouterManager] "
                "Message final envoyé au Router: %s",
                combined_text,
            )

            # ----------------------------------------------------
            # 3. Routage
            # ----------------------------------------------------

            # Actions en attente de clarification (best-effort)
            pending = PendingTurn.load(conversation)
            pending._history_text = history_text

            router_output = RouterExecutor.execute(
                combined_text,
                history=history_text,
                **pending.router_kwargs(),
            )
            # Messages stock → agent STOCK (évite dépense compta fantôme)
            try:
                forced = _force_stock_intent_if_needed(
                    list(router_output.intents), combined_text
                )
                if forced is not router_output.intents:
                    router_output = router_output.model_copy(update={"intents": forced})
            except Exception:
                logger.exception("[FemiRouterManager] force STOCK ignoré")

            # Force merge si réponse courte à une action en attente
            # (ex. "Mr Toto" après "pour quel client ce devis ?")
            pending.force_merge_on_short_reply(
                router_output.intents,
                combined_text,
            )

            early_msg = cls._try_handle_modify_confirmation(
                pending, combined_text, entreprise
            )
            if early_msg is None:
                early_msg = cls._try_handle_list_operations(
                    combined_text, entreprise
                )
            if early_msg is not None:
                pending.commit()
                cls._record_turn(
                    conversation,
                    canal,
                    combined_text,
                    type_message,
                    early_msg,
                )
                return RouterProcessResult(
                    success=True,
                    message=early_msg,
                    needs_clarification=False,
                    missing_fields=[],
                )

            pending.start_turn(
                combined_text,
                len(router_output.intents),
            )

            logger.info(
                "[FemiRouterManager] "
                "Router terminé. Nombre d'intents=%s",
                len(router_output.intents),
            )

            # ----------------------------------------------------
            # 4. Dispatch + persistance
            # ----------------------------------------------------

            result = cls._build_result(
                router_output=router_output,
                entreprise=entreprise,
                utilisateur=utilisateur,
                source=source,
                raw_text=combined_text,
                image_bytes=image_bytes,
                pending=pending,
            )

            pending.commit()

            cls._record_turn(
                conversation,
                canal,
                combined_text,
                type_message,
                result.message,
            )

            return result

        except (
            Entreprise.DoesNotExist,
            Utilisateur.DoesNotExist,
        ) as exc:

            logger.error(
                "[FemiRouterManager] "
                "Contexte tenant introuvable (sync): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message=str(exc),
            )

        except RouterInputError as exc:

            logger.warning(
                "[FemiRouterManager] "
                "Aucune donnée exploitable (sync): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message=str(exc),
            )

        except UnsupportedDocumentError as exc:

            logger.warning(
                "[FemiRouterManager] "
                "Document non pris en charge (sync): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message=cls._natural_notice(
                    facts=(
                        "Le fichier envoyé n'a pas pu être lu : "
                        f"{exc}"
                    ),
                    fallback=str(exc),
                ),
            )

        except (
            OcrExecutionError,
            SttExecutionError,
            ValueError,
        ) as exc:

            logger.error(
                "[FemiRouterManager] "
                "Échec OCR/audio (sync): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message=cls._natural_notice(
                    facts=(
                        "Le traitement de l'image, du PDF ou de l'audio "
                        "envoyé a échoué (lecture impossible). L'utilisateur "
                        "peut réessayer avec une photo plus nette, un autre "
                        "fichier ou un nouveau vocal."
                    ),
                    fallback=(
                        "Le traitement de l'image ou de l'audio "
                        "a échoué."
                    ),
                ),
            )

        except RouterExecutionError as exc:

            logger.error(
                "[FemiRouterManager] "
                "Échec du Router (sync): %s",
                exc,
            )

            return RouterProcessResult(
                success=False,
                message="Le routage du message a échoué.",
            )

        except Exception:

            logger.exception(
                "[FemiRouterManager] "
                "Erreur inattendue (sync)"
            )

            return RouterProcessResult(
                success=False,
                message="Une erreur technique est survenue.",
            )

    # ============================================================
    # CONSTRUCTION DU TEXTE
    # ============================================================

    @classmethod
    def _build_combined_text(
        cls,
        text_input: Optional[str],
        image_bytes: Optional[bytes],
        audio_bytes: Optional[bytes],
    ) -> str:
        try:
            return build_combined_text(text_input, image_bytes, audio_bytes)
        except IngressError as exc:
            raise RouterInputError(str(exc)) from exc
    
    # ============================================================
    # OCR → TEXTE
    # ============================================================

    @staticmethod
    def _ocr_result_to_readable_text(
        ocr_result,
    ) -> str:

        return build_document_text(ocr_result)

    # ============================================================
    # VERSION ASYNCHRONE DU TEXTE
    # ============================================================

    @classmethod
    async def _abuild_combined_text(
        cls,
        text_input: Optional[str],
        image_bytes: Optional[bytes],
        audio_bytes: Optional[bytes],
    ) -> str:
        try:
            return await abuild_combined_text(text_input, image_bytes, audio_bytes)
        except IngressError as exc:
            raise RouterInputError(str(exc)) from exc

    # ============================================================
    # TENANT / UTILISATEUR
    # ============================================================

    @classmethod
    def _get_tenant_context_stricte(
        cls,
        entreprise_id: Optional[str],
        utilisateur_id: Optional[str],
    ) -> Tuple[Entreprise, Utilisateur]:

        entreprise = cls._get_entreprise(entreprise_id)

        utilisateur = (
            cls._get_utilisateur_stricte(
                utilisateur_id
            )
        )

        return entreprise, utilisateur

    @staticmethod
    def _get_entreprise(entreprise_id: Optional[str]) -> Entreprise:
        """Entreprise du tenant, ou Entreprise.DoesNotExist si absente."""
        if entreprise_id:
            return Entreprise.objects.get(id=entreprise_id)
        raise Entreprise.DoesNotExist("Aucun ID d'entreprise fourni.")

    @classmethod
    def _get_utilisateur_stricte(
        cls,
        utilisateur_id: Optional[str],
    ) -> Utilisateur:

        if utilisateur_id:

            utilisateur = (
                Utilisateur.objects
                .filter(id=utilisateur_id)
                .first()
            )

            if utilisateur is not None:
                return utilisateur

        raise Utilisateur.DoesNotExist(
            "Aucun utilisateur résolu pour "
            f"utilisateur_id={utilisateur_id!r}."
        )

    # ============================================================
    # CONTEXTE CONVERSATIONNEL (historique multi-canal)
    # ============================================================
    #
    # Best-effort par construction : une panne ici ne doit jamais faire
    # échouer le traitement principal d'un message (voir
    # conversation_manager.py). Utilisé par route_message() ET
    # aroute_message() (ce dernier passe par sync_to_async).

    @classmethod
    def _setup_conversation_context(
        cls,
        utilisateur: Utilisateur,
        entreprise: Entreprise,
        source: str,
        image_bytes: Optional[bytes],
        audio_bytes: Optional[bytes],
    ) -> Tuple[
        Optional[Conversation],
        str,
        str,
        Optional[str],
    ]:
        """Résout/crée la Conversation active et l'historique à donner
        au Router.

        Retourne (conversation, canal, type_message, history_text).
        `conversation` vaut None en cas d'échec (aucun blocage du flux
        principal), auquel cas les enregistrements de messages sont
        simplement ignorés en aval.
        """

        canal = normalize_canal(source)
        type_message = resolve_type_message(
            image_bytes,
            audio_bytes,
        )

        try:
            conversation = get_or_create_active_conversation(
                utilisateur,
                entreprise,
                canal,
            )

            history_text = build_history_text(conversation)

            return conversation, canal, type_message, history_text

        except Exception:

            logger.exception(
                "[FemiRouterManager] "
                "Échec de préparation du contexte conversationnel "
                "— poursuite sans historique."
            )

            return None, canal, type_message, None

    @classmethod
    def _record_turn(
        cls,
        conversation: Optional[Conversation],
        canal: str,
        user_text: str,
        user_type_message: str,
        agent_text: Optional[str],
    ) -> None:
        """Enregistre le tour utilisateur + agent dans l'historique
        (version synchrone). Aucune exception ne remonte au-delà de
        record_message(), qui est déjà défensif."""

        if conversation is None:
            return

        record_message(
            conversation,
            canal,
            "USER",
            user_text,
            user_type_message,
        )

        record_message(
            conversation,
            canal,
            "AGENT",
            agent_text,
            "TEXTE",
        )

    @classmethod
    async def _arecord_turn(
        cls,
        conversation: Optional[Conversation],
        canal: str,
        user_text: str,
        user_type_message: str,
        agent_text: Optional[str],
    ) -> None:
        """Équivalent asynchrone de _record_turn()."""

        if conversation is None:
            return

        await sync_to_async(cls._record_turn)(
            conversation,
            canal,
            user_text,
            user_type_message,
            agent_text,
        )

    # ============================================================
    # DISPATCH SYNCHRONE
    # ============================================================

    @classmethod
    def _dispatch_intent(
        cls,
        intent: RouterIntent,
        entreprise: Entreprise,
        from_document: bool = False,
        segment: Optional[str] = None,
        history_text: Optional[str] = None,
    ) -> Tuple[DispatchResult, bool]:
        return dispatch_intent(
            intent,
            entreprise,
            from_document=from_document,
            segment=segment,
            history_text=history_text,
        )
    # ============================================================
    # DISPATCH ASYNCHRONE
    # ============================================================

    @classmethod
    async def _adispatch_intent(
        cls,
        intent: RouterIntent,
        entreprise: Entreprise,
        from_document: bool = False,
        segment: Optional[str] = None,
        history_text: Optional[str] = None,
    ) -> Tuple[DispatchResult, bool]:
        return await adispatch_intent(
            intent,
            entreprise,
            from_document=from_document,
            segment=segment,
            history_text=history_text,
        )

    # ============================================================
    # SALUTATIONS (court-circuit, avant tout appel LLM)
    # ============================================================

    @classmethod
    def _is_pure_greeting(cls, text: str) -> bool:
        return is_pure_greeting(text)

    @staticmethod
    def _greeting_result(text: str = "") -> RouterProcessResult:
        return greeting_result(text)

    @staticmethod
    def _natural_notice(facts: str, fallback: str, user_message: str = "") -> str:
        return natural_notice(facts, fallback, user_message)

    @staticmethod
    def _natural_off_topic(user_message: str = "") -> str:
        return natural_off_topic(user_message)
    # ============================================================
    # CONSTRUCTION DU MESSAGE FINAL EN LANGAGE NATUREL
    # ============================================================
    #
    # Sans ceci, RouterProcessResult.message était une chaîne fixe
    # ("Message routé avec succès.") quel que soit le résultat réel —
    # l'utilisateur recevait la même phrase pour une transaction
    # enregistrée, une vraie réponse d'analyse financière (pourtant déjà
    # rédigée par le LLM dans FinalAnswerOutput.answer), ou une question
    # hors-sujet. Les méthodes ci-dessous assemblent un message réellement
    # représentatif, à partir des résultats déjà collectés par
    # _build_result()/_abuild_result().

    _TRANSACTION_TYPE_LABELS = {
        "RECETTE": "Recette",
        "DEPENSE": "Dépense",
        "PRET_DONNE": "Prêt donné",
        "PRET_RECU": "Prêt reçu",
    }

    @staticmethod
    def _format_montant(amount, currency: Optional[str] = None) -> str:
        return format_montant(amount, currency)

    @classmethod
    def _summarize_accounting_transactions(
            cls, result: AccountingExtractionResult
        ) -> list[str]:
            if not result.transactions:
                return []

            # Transactions qui visaient une facture déjà enregistrée
            # (règlement / doublon) : message déterministe, jamais
            # « enregistrée » à tort.
            messages_speciaux = [
                cls._message_facture_existante(txn)
                for txn in result.transactions
                if getattr(txn, "_statut_enregistrement", None)
            ]
            transactions_normales = [
                txn
                for txn in result.transactions
                if not getattr(txn, "_statut_enregistrement", None)
            ]
            if not transactions_normales:
                return messages_speciaux

            fact_lines: list[str] = []
            fallback_lines: list[str] = []

            for txn in transactions_normales:
                label = cls._TRANSACTION_TYPE_LABELS.get(
                    txn.transaction_type, txn.transaction_type
                )
                montant = cls._format_montant(txn.amount_ttc, txn.currency)
                extra = f" ({txn.category})" if txn.category else ""
                contact = f" — {txn.contact}" if txn.contact else ""

                # Description métier (ex: "2 sacs de riz", "carburant Zem")
                raw_desc = (getattr(txn, "description", None) or "").strip()
                desc_fact = raw_desc if raw_desc else "n/a"
                desc_fallback = f" — {raw_desc}" if raw_desc else ""

                statut = getattr(txn, "statut_paiement", "PAYE")
                avance = getattr(txn, "montant_deja_paye", None)
                payment_method = getattr(txn, "payment_method", None)
                payment_method_str = (
                    payment_method.value
                    if hasattr(payment_method, "value")
                    else (str(payment_method) if payment_method else "n/a")
                )
                date_op = getattr(txn, "date_operation", None)

                # ---- faits structurés pour le LLM ----
                fact = (
                    f"- type: {label}; montant_ttc: {montant}; "
                    f"description: {desc_fact}; "
                    f"categorie: {txn.category or 'n/a'}; "
                    f"contact: {txn.contact or 'n/a'}; "
                    f"mode_paiement: {payment_method_str}; "
                    f"date: {date_op or 'n/a'}; "
                    f"statut_paiement: {statut}"
                )
                if avance is not None:
                    fact += (
                        f"; montant_deja_paye: "
                        f"{cls._format_montant(avance, txn.currency)}"
                    )
                    if (
                        txn.amount_ttc is not None
                        and avance is not None
                        and 0 < float(avance) < float(txn.amount_ttc)
                    ):
                        reste = cls._format_montant(
                            txn.amount_ttc - avance, txn.currency
                        )
                        fact += f"; reste_a_payer_ou_encaisser: {reste}"
                elif statut == "CREDIT" and txn.amount_ttc is not None:
                    fact += f"; reste_a_payer_ou_encaisser: {montant}"
                fact_lines.append(fact)

                # ---- fallback template (si LLM down) ----
                # Ordre lisible : type + description + montant + contact
                if (
                    statut == "CREDIT"
                    and avance is not None
                    and txn.amount_ttc is not None
                    and 0 < float(avance) < float(txn.amount_ttc)
                    and txn.transaction_type in ("RECETTE", "DEPENSE")
                ):
                    reste = cls._format_montant(
                        txn.amount_ttc - avance, txn.currency
                    )
                    deja = cls._format_montant(avance, txn.currency)
                    if txn.transaction_type == "RECETTE":
                        fallback_lines.append(
                            f"✅ Vente{desc_fallback} de {montant}{extra}{contact} enregistrée.\n"
                            f"Avance reçue : {deja}. Il reste {reste} à encaisser."
                        )
                    else:
                        fallback_lines.append(
                            f"✅ Achat{desc_fallback} de {montant}{extra}{contact} enregistré.\n"
                            f"Avance versée : {deja}. Il reste {reste} à payer."
                        )
                elif statut == "CREDIT" and txn.transaction_type == "RECETTE":
                    fallback_lines.append(
                        f"✅ Vente à crédit{desc_fallback} de {montant}{extra}{contact} enregistrée.\n"
                        f"Créance client : {montant} restent à encaisser."
                    )
                elif statut == "CREDIT" and txn.transaction_type == "DEPENSE":
                    fallback_lines.append(
                        f"✅ Achat à crédit{desc_fallback} de {montant}{extra}{contact} enregistré.\n"
                        f"Dette fournisseur : {montant} restent à payer."
                    )
                else:
                    # Evite "Recette de 15 000 — 2 sacs — enregistrée"
                    # → "Recette — 2 sacs de riz — 15 000 FCFA enregistrée."
                    if raw_desc:
                        fallback_lines.append(
                            f"✅ {label}{desc_fallback} : {montant}{extra}{contact} enregistrée."
                        )
                    else:
                        fallback_lines.append(
                            f"✅ {label} de {montant}{extra}{contact} enregistrée."
                        )

            facts = (
                "Opération(s) comptable(s) enregistrée(s) avec succès :\n"
                + "\n".join(fact_lines)
                + "\n\nIntègre la description dans la phrase de confirmation "
                "quand elle est pertinente (pas 'n/a')."
            )
            fallback = "\n".join(fallback_lines)

            natural = write_natural_reply(
                facts=facts,
                reply_type="accounting_success",
                fallback=fallback,
                temperature=0.7,
            )
            return [natural] + messages_speciaux

    @classmethod
    def _montant_remboursement(cls, montant, currency) -> str:
        devise = {"XOF": "FCFA", "XAF": "FCFA"}.get(
            str(currency or "").upper(), currency
        )
        return cls._format_montant(montant, devise)

    @classmethod
    def _message_remboursement(cls, detail: dict) -> str:
        """Message déterministe après imputation d'un remboursement sur les
        opérations ouvertes d'un contact (voir accounting_manager)."""
        devise = detail.get("currency")
        contact = detail.get("contact") or "ce contact"
        type_cible = detail.get("type_cible")
        impute = cls._montant_remboursement(detail.get("montant_impute"), devise)
        reste = detail.get("reste_du") or 0
        reste_txt = cls._montant_remboursement(reste, devise)

        if type_cible == "PRET_RECU":
            debut = f"✅ Remboursement de {impute} enregistré pour ton prêt auprès de {contact}."
            fin = (
                f"Il te reste {reste_txt} à rembourser."
                if reste > 0
                else "Ce prêt est entièrement remboursé."
            )
        elif type_cible == "PRET_DONNE":
            debut = f"✅ Remboursement de {impute} reçu de {contact}."
            fin = (
                f"{contact} te doit encore {reste_txt} sur ce prêt."
                if reste > 0
                else "Ce prêt est entièrement remboursé."
            )
        elif type_cible == "DEPENSE":
            debut = f"✅ Paiement de {impute} enregistré pour ta dette envers {contact}."
            fin = (
                f"Il te reste {reste_txt} à lui payer."
                if reste > 0
                else f"Ta dette envers {contact} est entièrement réglée."
            )
        else:
            debut = f"✅ Paiement de {impute} reçu de {contact}, imputé sur sa dette."
            fin = (
                f"{contact} te doit encore {reste_txt}."
                if reste > 0
                else f"{contact} ne te doit plus rien."
            )

        message = f"{debut} {fin}"
        surplus = detail.get("surplus") or 0
        if surplus > 0:
            message += (
                f" Tu as indiqué {cls._montant_remboursement(surplus, devise)} "
                f"de plus que le solde : ce surplus n'a pas été enregistré. "
                f"Dis-moi si c'est une avance ou une erreur de saisie."
            )
        return message

    @classmethod
    def _message_remboursement_refuse(cls, detail: dict) -> str:
        """Message quand un remboursement n'a pas pu être imputé : RIEN n'a
        été enregistré (ni opération, ni contact), pour ne pas compter le
        paiement deux fois."""
        raison = detail.get("raison")
        devise = detail.get("currency")
        nom = detail.get("nom_saisi") or ""
        montant = cls._montant_remboursement(detail.get("montant_recu"), devise)
        type_cible = detail.get("type_cible")
        propositions = detail.get("propositions") or []
        candidats = detail.get("candidats") or []
        est_pret = type_cible in ("PRET_DONNE", "PRET_RECU")
        objet = "prêt" if est_pret else "dette"

        if raison == "montant":
            return (
                "Je n'ai rien enregistré : je n'ai pas compris le montant "
                "payé. Quel est le montant ?"
            )

        if raison == "ambigu":
            return (
                f"J'ai plusieurs contacts qui s'appellent « {nom} » "
                f"({', '.join(propositions)}). Je n'ai rien enregistré : "
                f"redis-moi le paiement en précisant lequel."
            )

        if raison == "proche":
            return (
                f"Je ne trouve pas « {nom} » dans tes contacts. Tu veux dire "
                f"{' ou '.join(propositions)} ? Je n'ai rien enregistré : "
                f"redis-moi le paiement avec le bon nom."
            )

        if raison == "aucune_dette":
            contact = detail.get("contact") or nom
            if type_cible == "RECETTE":
                conseil = (
                    f"Si c'est une nouvelle vente déjà payée, dis-le-moi "
                    f"(ex. « vente de {montant} à {contact}, payée »)."
                )
            elif type_cible == "DEPENSE":
                conseil = (
                    f"Si c'est un nouvel achat déjà payé, dis-le-moi "
                    f"(ex. « achat de {montant} chez {contact}, payé »)."
                )
            else:
                conseil = "Si c'est une autre opération, précise-moi laquelle."
            return (
                f"{contact} n'a aucun{'' if est_pret else 'e'} {objet} "
                f"ouvert{'' if est_pret else 'e'}, donc je n'ai pas enregistré "
                f"ce paiement de {montant} (pour ne pas le compter deux fois). "
                f"{conseil}"
            )

        # sans_nom / inconnu
        if raison == "inconnu":
            debut = (
                f"Je ne trouve aucun contact « {nom} » dans tes comptes, donc "
                f"je n'ai pas enregistré ce paiement de {montant}."
            )
        else:
            debut = (
                f"Je n'ai pas enregistré ce paiement de {montant} : je ne sais "
                f"pas de qui il vient."
            )
        suite = (
            f" Ceux qui ont encore un solde : {', '.join(candidats)}."
            if candidats
            else ""
        )
        return (
            f"{debut}{suite} Redis-moi le paiement avec le nom du contact "
            f"(ex. « Koffi m'a payé 10 000 »)."
        )

    @classmethod
    def _message_facture_existante(cls, txn) -> str:
        """Message pour une facture déjà enregistrée : règlement imputé,
        facture déjà soldée ou doublon évité."""
        statut = getattr(txn, "_statut_enregistrement", None)
        detail = getattr(txn, "_detail_enregistrement", None) or {}

        if statut == "remboursement":
            return cls._message_remboursement(detail)
        if statut == "remboursement_refuse":
            return cls._message_remboursement_refuse(detail)

        reference = detail.get("reference") or "?"
        devise = detail.get("currency")
        contact = f" ({detail['contact']})" if detail.get("contact") else ""

        if statut == "reglement":
            impute = cls._format_montant(detail.get("montant_impute"), devise)
            reste = detail.get("reste")
            if reste is not None and reste > 0:
                fin = f"Il reste {cls._format_montant(reste, devise)} à régler sur cette facture."
            else:
                fin = "La facture est maintenant entièrement réglée."
            message = (
                f"✅ Règlement de {impute} enregistré sur la facture "
                f"n°{reference}{contact}. {fin}"
            )
            surplus = detail.get("surplus")
            if surplus is not None and surplus > 0:
                message += (
                    f" Tu as indiqué {cls._format_montant(surplus, devise)} "
                    f"de plus que le reste dû : ce surplus n'a pas été enregistré."
                )
            return message

        if statut == "deja_soldee":
            return (
                f"La facture n°{reference}{contact} est déjà enregistrée et "
                f"entièrement réglée. Je n'ai rien enregistré de plus."
            )

        total = cls._format_montant(detail.get("montant_total"), devise)
        date_op = detail.get("date")
        quand = f" le {date_op.strftime('%d/%m/%Y')}" if date_op else ""
        return (
            f"Cette facture n°{reference}{contact} de {total} est déjà "
            f"enregistrée{quand}. Je ne l'ai pas enregistrée une deuxième fois."
        )

    @staticmethod
    def _persist_quote_results(
        quote_results: list[QuoteExtractionResult],
        entreprise: Entreprise,
    ) -> Tuple[list[dict], list[str]]:
        return persist_quote_results(quote_results, entreprise)


    @staticmethod
    def _summarize_devis(infos: dict, user_message: str = "") -> str:
        return summarize_devis(infos, user_message)

    @classmethod
    def _summarize_customer_payment(cls, result: CustomerExtractionOutput) -> str:
        return summarize_customer_payment(result)
    
    @staticmethod
    def _format_values_for_facts(values) -> str:
        return format_values_for_facts(values)

    @classmethod
    def _summarize_propose_change(cls, result, user_message: str = "") -> str:
        return summarize_propose_change(result, user_message)

    
    
    @staticmethod
    def _summarize_candidates(
        result: AccountingModifyResolutionResult,
        user_message: str = "",
    ) -> str:
        return summarize_candidates(result, user_message)


    # ------------------------------------------------------------
    # QUESTIONS DE CLARIFICATION (codes missing_fields → français clair)
    # ------------------------------------------------------------
    # Les codes viennent des prompts (ROUTER, ACCOUNTING, ACCOUNTING_MODIFY,
    # CUSTOMER, FINANCIAL_ANALYST). L'utilisateur ne doit JAMAIS voir un nom
    # de champ technique. Ordre = priorité : ce qui bloque l'enregistrement
    # d'abord. Ces textes servent (1) de FAITS « à demander » donnés au
    # rédacteur naturel (response_writer.py) et (2) de réponse de secours
    # si le rédacteur échoue.
    _MISSING_FIELD_QUESTIONS = {
        "transaction_type": "Dis-moi, c'est une vente que tu as faite, ou un achat pour ton activité ? (Si c'est un prêt, précise-le-moi.)",
        "amount_ttc": "Quel est le montant total, s'il te plaît ?",
        "nature_creance": "C'est le remboursement d'une vente à crédit, ou d'un prêt que tu avais accordé ?",
        "statut_paiement": "Est-ce que c'est déjà réglé en totalité, ou est-ce qu'il reste une partie à payer ? S'il y a eu une avance, dis-moi combien.",
        "contact_disambiguation": "J'ai plusieurs personnes avec ce nom. C'est laquelle ? Donne-moi son nom complet.",
        "target_data": "Quelle opération veux-tu modifier ou supprimer ? Donne-moi le montant, la date ou le nom du contact.",
        "search_criteria": "Quelle opération cherches-tu ? Donne-moi le montant, la date ou le nom du contact.",
        "operation_not_found": "Je ne retrouve pas cette opération. Peux-tu donner le montant, la date ou le nom du contact ?",
        "new_value": "Quelle est la nouvelle valeur à enregistrer ?",
        "category_not_available": "Cette catégorie n'existe pas dans ton compte. Peux-tu en choisir une existante ?",
        "payment_method_invalid": "Quel est le mode de paiement : espèces, mobile money, virement, chèque ou carte ?",
        "transaction_type_reclassification_not_supported": (
            "Je ne peux pas changer le type d'une opération déjà enregistrée. "
            "Annule-la puis enregistre-la de nouveau avec le bon type."
        ),
        "indicator": "Quel indicateur veux-tu : chiffre d'affaires, dépenses, bénéfice, marge ou trésorerie ?",
        "indicateur": "Quel indicateur veux-tu : chiffre d'affaires, dépenses, bénéfice, marge ou trésorerie ?",
        "date_range_incomplete": "Pour quelle période exactement ? (ex : ce mois-ci, septembre, du 1er au 15)",
        "periode": "Pour quelle période ? (ex : aujourd'hui, ce mois-ci, le mois dernier, cette année)",
        "période": "Pour quelle période ? (ex : aujourd'hui, ce mois-ci, le mois dernier, cette année)",
        "period": "Pour quelle période ? (ex : aujourd'hui, ce mois-ci, le mois dernier, cette année)",
        "annee": "Pour quelle année ?",
        "wrong_agent": "Peux-tu reformuler ta demande en précisant ce que tu veux faire ?",
        "search_error": "Je n'ai pas réussi à retrouver cette information. Peux-tu reformuler ?",
        "client_name": "C'est pour quel client ce devis ?",
        "quote_items": "Quels articles ou services mettre sur le devis (quantités et prix) ?",
        "quote_price": "Quel est le prix unitaire de chaque article du devis ?",
    }

    # Codes déjà rendus ailleurs (liste de candidats) ou propres à un autre
    # message : jamais transformés en question ici.
    _CLARIFICATION_SKIP_CODES = {"fonctionnalite_non_disponible", "operation_disambiguation"}

    @classmethod
    def _build_understood_recap(cls, accounting_results) -> str | None:
        return build_understood_recap(accounting_results)



    @staticmethod
    def _is_affirmative(text: str) -> bool:
        t = (text or "").strip().lower()
        return t in {
            "oui", "yes", "ok", "okay", "daccord", "d'accord", "confirme",
            "je confirme", "valide", "valider", "oui valide", "oui je confirme",
            "c'est bon", "cest bon", "go", "vas-y", "vas y",
        }

    @staticmethod
    def _is_negative(text: str) -> bool:
        t = (text or "").strip().lower()
        return t in {
            "non", "no", "annule", "annuler", "laisse", "laisse tomber",
            "stop", "cancel", "non merci",
        }

    @classmethod
    def _try_handle_modify_confirmation(
        cls,
        pending: Optional[PendingTurn],
        raw_text: str,
        entreprise,
    ) -> str | None:
        """Si pending = proposition UPDATE/DELETE et message = oui/non, applique."""
        if pending is None or not hasattr(pending, "has_confirmation"):
            return None
        if not pending.has_confirmation():
            return None
        text = (raw_text or "").strip()
        if not (cls._is_affirmative(text) or cls._is_negative(text)):
            return None
        entry = pending.pop_confirmation()
        if not entry:
            return None
        if cls._is_negative(text):
            return "D'accord, je n'ai rien modifié."
        result = apply_operation_change(
            entreprise,
            operation_id=entry.get("operation_id"),
            action_type=entry.get("action_type") or "UPDATE",
            proposed_values=entry.get("proposed_values") or {},
        )
        return result.get("resume_texte") or (
            "Modification enregistrée."
            if result.get("success")
            else "Je n'ai pas pu appliquer la modification."
        )

    @classmethod
    def _try_handle_list_operations(cls, raw_text: str, entreprise) -> str | None:
        """Réponse déterministe pour « liste les opérations »."""
        import unicodedata

        def _n(s: str) -> str:
            return "".join(
                c for c in unicodedata.normalize("NFD", s.lower())
                if unicodedata.category(c) != "Mn"
            )

        msg_n = _n(raw_text or "")
        if not (
            ("liste" in msg_n and "operation" in msg_n)
            or ("affiche" in msg_n and "operation" in msg_n)
            or ("montre" in msg_n and "operation" in msg_n)
            or msg_n.strip() in {"mes operations", "mes opérations"}
        ):
            return None
        payload = list_recent_operations(entreprise, limit=15)
        return payload.get("resume_texte")


    @classmethod
    def _build_clarification_message(
        cls,
        missing_fields: list[str],
        accounting_results=None,
        user_message: str = "",
    ) -> str | None:
        return build_clarification_message(
            missing_fields, accounting_results, user_message
        )


    @classmethod
    def _build_static_clarification_message(
        cls, missing_fields, accounting_results=None
    ) -> str | None:
        return build_static_clarification_message(
            missing_fields, accounting_results
        )


    @classmethod
    def _build_final_message(
        cls,
        accounting_results: list[AccountingExtractionResult],
        financial_analyst_results: list,
        accounting_modify_results: list,
        customer_results: list,
        needs_clarification: bool,
        missing_fields: list[str],
        user_message: str = "",
        devis_crees: Optional[list] = None,
        quote_erreurs: Optional[list] = None,
    ) -> str:
        """Assemble le message réellement envoyé à l'utilisateur (WhatsApp et
        application mobile) à partir des résultats structurés des agents."""
        lines: list[str] = []

        # 1. Réponses conversationnelles déjà rédigées par un agent
        #    (FinancialAnalystExecutor ou sous-flux READ de CustomerExecutor).
        for result in list(financial_analyst_results) + list(customer_results):
            if isinstance(result, FinalAnswerOutput):
                lines.append(result.answer)

        # 2. Transactions comptables effectivement enregistrées (ACCOUNTING).
        for result in accounting_results:
            if not result.needs_clarification:
                lines.extend(cls._summarize_accounting_transactions(result))

        # 3. Paiement client/fournisseur enregistré (CUSTOMER, sous-flux CREATE).
        for result in customer_results:
            if isinstance(result, CustomerExtractionOutput) and not result.needs_clarification:
                lines.append(cls._summarize_customer_payment(result))

        # 3 bis. Devis créés (QUOTE) ou refusés par la validation.
        for devis in devis_crees or []:
            lines.append(cls._summarize_devis(devis, user_message))
        lines.extend(quote_erreurs or [])

        # 4. Propositions de modification/suppression (ACCOUNTING_MODIFY),
        #    ou liste de candidats en cas d'ambiguïté.
        for result in accounting_modify_results:
            if isinstance(result, AccountingModifyProposeChangeResult):
                lines.append(cls._summarize_propose_change(result, user_message))
            elif isinstance(result, AccountingModifyResolutionResult) and result.candidates:
                lines.append(cls._summarize_candidates(result, user_message))

        if lines:
            # Une opération du lot peut être enregistrée pendant qu'une autre
            # attend une précision : ne pas perdre la question.
            if needs_clarification:
                clarification = cls._build_clarification_message(
                    missing_fields, accounting_results, user_message
                )
                if clarification:
                    lines.append(clarification)
            return "\n\n".join(lines)

        # 5. Rien de concret à annoncer : soit une clarification est
        #    nécessaire, soit la demande n'a pas pu être rattachée à un agent
        #    connu (question hors-sujet, intent SETTINGS/UNKNOWN...).
        if needs_clarification:
            if "fonctionnalite_non_disponible" in missing_fields:
                return write_natural_reply(
                    facts=(
                        "La fonction demandée par l'utilisateur n'est pas "
                        "encore disponible chez Femi.\n"
                        "Ce que Femi peut faire à la place : donner chiffre "
                        "d'affaires, dépenses (détail par catégorie), "
                        "bénéfice, marge ou trésorerie ; comparer deux "
                        "périodes ; suivre les créances clients et dire qui "
                        "relancer ; enregistrer, modifier ou supprimer une "
                        "transaction."
                    ),
                    reply_type="feature_unavailable",
                    user_message=user_message,
                    fallback=(
                        "Cette fonctionnalité n'est pas encore disponible chez Femi 🙂\n\n"
                        "Je peux en revanche t'aider avec :\n"
                        "• Ton chiffre d'affaires, tes dépenses (avec détail par catégorie), "
                        "ton bénéfice, ta marge ou ta trésorerie ;\n"
                        "• Une comparaison entre deux périodes ;\n"
                        "• Tes créances clients et qui relancer ;\n"
                        "• Enregistrer, modifier ou supprimer une transaction."
                    ),
                    timeout=_SHORT_REPLY_TIMEOUT,
                )
            clarification = cls._build_clarification_message(
                    missing_fields, accounting_results, user_message
                )
            if clarification:
                return clarification
            return write_natural_reply(
                facts=(
                    "Il manque des informations pour traiter la demande, "
                    "sans savoir lesquelles précisément. Demande à "
                    "l'utilisateur de préciser sa demande."
                ),
                reply_type="clarification",
                user_message=user_message,
                fallback="Je n'ai pas toutes les informations nécessaires. Peux-tu préciser ta demande ?",
                timeout=_SHORT_REPLY_TIMEOUT,
            )

        return cls._natural_off_topic(user_message)

    # ============================================================
    # CONSTRUCTION RESULTAT SYNCHRONE
    # ============================================================

    @classmethod
    def _build_result(
        cls,
        router_output: RouterOutput,
        entreprise: Entreprise,
        utilisateur: Optional[Utilisateur] = None,
        source: str = "API",
        raw_text: str = "",
        image_bytes: Optional[bytes] = None,
        pending: Optional[PendingTurn] = None,
    ) -> RouterProcessResult:

        logger.info("=" * 80)
        logger.info(
            "[FemiRouterManager] >>> DEBUT _build_result"
        )

        logger.info(
            "[FemiRouterManager] entreprise_id=%s",
            getattr(entreprise, "id", None),
        )

        logger.info(
            "[FemiRouterManager] utilisateur_id=%s",
            getattr(utilisateur, "id", None),
        )

        logger.info(
            "[FemiRouterManager] source=%s",
            source,
        )

        logger.info(
            "[FemiRouterManager] raw_text=%s",
            raw_text,
        )

        needs_clarification = False
        missing_fields: list[str] = []

        accounting_results: list[
            AccountingExtractionResult
        ] = []

        financial_analyst_results: list[
            ToolSelectionOutput | FinalAnswerOutput
        ] = []

        accounting_modify_results: list[
            AccountingModifySearchResult
            | AccountingModifyResolutionResult
            | AccountingModifyProposeChangeResult
        ] = []

        customer_results: list[
            ValidationOutput
            | CustomerExtractionOutput
            | ToolSelectionOutput
            | FinalAnswerOutput
        ] = []

        quote_results: list[QuoteExtractionResult] = []
        quote_interdit = False

        logger.info(
            "[FemiRouterManager] "
            "Nombre d'intents=%s",
            len(router_output.intents),
        )

        # --------------------------------------------------------
        # Dispatch
        # --------------------------------------------------------

        for index, intent in enumerate(
            router_output.intents,
            start=1,
        ):

            logger.info("-" * 70)

            logger.info(
                "[FemiRouterManager] "
                ">>> INTENT #%s",
                index,
            )

            logger.info(
                "[FemiRouterManager] agent=%s",
                intent.agent,
            )

            logger.info(
                "[FemiRouterManager] action=%s",
                getattr(
                    intent,
                    "action",
                    None,
                ),
            )

            logger.info(
                "[FemiRouterManager] raw_segment=%s",
                intent.raw_segment,
            )

            logger.info(
                "[FemiRouterManager] "
                "intent.needs_clarification=%s",
                intent.needs_clarification,
            )

            logger.info(
                "[FemiRouterManager] "
                "intent.missing_fields=%s",
                intent.missing_fields,
            )

            # Un employé n'a pas accès aux devis : on ne lance pas l'agent
            # QUOTE (donc aucune question de précision) et rien n'est créé.
            if intent.agent == "QUOTE" and utilisateur_est_employe(utilisateur):
                logger.info(
                    "[FemiRouterManager] Intent QUOTE ignoré : "
                    "utilisateur au rôle EMPLOYE."
                )
                quote_interdit = True
                continue

            # FINANCIAL_ANALYST décide seul s'il doit clarifier (son propre
            # résultat remonte plus bas) : le drapeau du routeur ne doit pas
            # s'ajouter à une réponse déjà donnée.
            if intent.agent != "FINANCIAL_ANALYST":
                if intent.needs_clarification:
                    needs_clarification = True

                for field in intent.missing_fields:

                    if field not in missing_fields:
                        missing_fields.append(field)

            # ----------------------------------------------------
            # Dispatch agent
            # ----------------------------------------------------

            try:

                segment, doc_flag = (
                    pending.resolve(intent, bool(image_bytes))
                    if pending is not None
                    else (intent.raw_segment, bool(image_bytes))
                )

                result, dispatch_needs_clarification = (
                    cls._dispatch_intent(
                    intent,
                    entreprise,
                    from_document=doc_flag,
                    segment=segment,
                    history_text=getattr(pending, "_history_text", None),
                )
                )

                if pending is not None:
                    pending.observe(
                        intent,
                        segment,
                        doc_flag,
                        bool(dispatch_needs_clarification)
                        and result is not None,
                        getattr(result, "missing_fields", None),
                    )

            except Exception:

                logger.exception(
                    "[FemiRouterManager] "
                    "ERREUR pendant _dispatch_intent "
                    "pour intent #%s",
                    index,
                )

                raise

            logger.info(
                "[FemiRouterManager] "
                "Résultat dispatch=%s",
                result,
            )

            logger.info(
                "[FemiRouterManager] "
                "Type résultat=%s",
                (
                    type(result).__name__
                    if result is not None
                    else "None"
                ),
            )

            logger.info(
                "[FemiRouterManager] "
                "dispatch_needs_clarification=%s",
                dispatch_needs_clarification,
            )

            if dispatch_needs_clarification:
                needs_clarification = True

            # ----------------------------------------------------
            # Classification du résultat
            # ----------------------------------------------------

            if isinstance(
                result,
                AccountingExtractionResult,
            ):

                logger.info(
                    "[FemiRouterManager] "
                    ">>> AccountingExtractionResult détecté"
                )

                logger.info(
                    "[FemiRouterManager] "
                    "Accounting result=%s",
                    result,
                )

                logger.info(
                    "[FemiRouterManager] "
                    "Accounting needs_clarification=%s",
                    getattr(
                        result,
                        "needs_clarification",
                        None,
                    ),
                )

                accounting_results.append(
                    result
                )

            elif isinstance(
                result,
                (
                    AccountingModifySearchResult,
                    AccountingModifyResolutionResult,
                    AccountingModifyProposeChangeResult,
                ),
            ):

                logger.info(
                    "[FemiRouterManager] "
                    ">>> AccountingModifyResult détecté"
                )

                accounting_modify_results.append(
                    result
                )
                if (
                    pending is not None
                    and isinstance(result, AccountingModifyProposeChangeResult)
                    and getattr(result, "operation_id", None)
                ):
                    pending.store_confirmation(
                        agent="ACCOUNTING_MODIFY",
                        action_type=getattr(result, "action_type", "UPDATE") or "UPDATE",
                        operation_id=str(result.operation_id),
                        proposed_values=getattr(result, "proposed_values", None),
                        current_values=getattr(result, "current_values", None),
                        text="",
                    )

            elif isinstance(result, QuoteExtractionResult):

                quote_results.append(result)

            elif intent.agent == "CUSTOMER":

                logger.info(
                    "[FemiRouterManager] "
                    ">>> Customer result détecté"
                )

                if result is not None:
                    customer_results.append(result)

            elif isinstance(
                result,
                (
                    ToolSelectionOutput,
                    FinalAnswerOutput,
                ),
            ):

                logger.info(
                    "[FemiRouterManager] "
                    ">>> Financial Analyst result détecté"
                )

                financial_analyst_results.append(
                    result
                )

            # ----------------------------------------------------
            # Champs manquants de l'agent
            # ----------------------------------------------------

            if (
                result is not None
                and hasattr(
                    result,
                    "missing_fields",
                )
            ):

                result_missing_fields = (
                    getattr(
                        result,
                        "missing_fields",
                        [],
                    )
                )

                logger.info(
                    "[FemiRouterManager] "
                    "result.missing_fields=%s",
                    result_missing_fields,
                )

                for field in result_missing_fields:

                    if field not in missing_fields:
                        missing_fields.append(field)

        # ========================================================
        # FIN DISPATCH
        # ========================================================

        logger.info("=" * 80)

        logger.info(
            "[FemiRouterManager] "
            ">>> accounting_results FINAL=%s",
            accounting_results,
        )

        logger.info(
            "[FemiRouterManager] "
            ">>> nombre accounting_results=%s",
            len(accounting_results),
        )

        # --------------------------------------------------------
        # Filtrage des résultats valides
        # --------------------------------------------------------

        clean_accounting_results = [
            result
            for result in accounting_results
            if not result.needs_clarification
        ]

        logger.info(
            "[FemiRouterManager] "
            ">>> clean_accounting_results=%s",
            clean_accounting_results,
        )

        logger.info(
            "[FemiRouterManager] "
            ">>> nombre clean_accounting_results=%s",
            len(clean_accounting_results),
        )

        # --------------------------------------------------------
        # Log détaillé de chaque résultat
        # --------------------------------------------------------

        if accounting_results:

            for index, result in enumerate(
                accounting_results,
                start=1,
            ):

                logger.info(
                    "[FemiRouterManager] "
                    "Accounting #%s | "
                    "needs_clarification=%s | "
                    "result=%s",
                    index,
                    getattr(
                        result,
                        "needs_clarification",
                        None,
                    ),
                    result,
                )

        else:

            logger.warning(
                "[FemiRouterManager] "
                "!!! Aucun AccountingExtractionResult "
                "n'a été produit."
            )

        # ========================================================
        # PERSISTANCE ACCOUNTING
        # ========================================================

        operation_ids: list[str] = []

        if clean_accounting_results:

            logger.info("=" * 80)

            logger.info(
                "[FemiRouterManager] "
                ">>> SAUVEGARDE DES OPERATIONS"
            )

            logger.info(
                "[FemiRouterManager] "
                "Nombre de résultats à sauvegarder=%s",
                len(clean_accounting_results),
            )

            logger.info(
                "[FemiRouterManager] "
                "Utilisateur présent=%s",
                utilisateur is not None,
            )

            logger.info(
                "[FemiRouterManager] "
                "Utilisateur ID=%s",
                getattr(
                    utilisateur,
                    "id",
                    None,
                ),
            )

            logger.info(
                "[FemiRouterManager] "
                "Entreprise ID=%s",
                getattr(
                    entreprise,
                    "id",
                    None,
                ),
            )

            if utilisateur is None:

                logger.error(
                    "[FemiRouterManager] "
                    "!!! IMPOSSIBLE DE SAUVEGARDER : "
                    "utilisateur=None"
                )

            else:

                try:

                    logger.info(
                        "[FemiRouterManager] "
                        ">>> appel de "
                        "_persist_accounting_results()"
                    )

                    operations = (
                        cls._persist_accounting_results(
                            accounting_results=(
                                clean_accounting_results
                            ),
                            entreprise=entreprise,
                            utilisateur=utilisateur,
                            source=source,
                            raw_text=(
                                pending.effective_raw_text(raw_text)
                                if pending is not None
                                else raw_text
                            ),
                            image_bytes=image_bytes,
                        )
                    )

                    logger.info(
                        "[FemiRouterManager] "
                        ">>> _persist_accounting_results terminé"
                    )

                    logger.info(
                        "[FemiRouterManager] "
                        "operations retournées=%s",
                        operations,
                    )

                    logger.info(
                        "[FemiRouterManager] "
                        "nombre operations créées=%s",
                        len(operations),
                    )

                    operation_ids = [
                        str(operation.id)
                        for operation in operations
                    ]

                    logger.info(
                        "[FemiRouterManager] "
                        ">>> OPERATION IDS=%s",
                        operation_ids,
                    )

                except Exception:

                    logger.exception(
                        "[FemiRouterManager] "
                        "!!! ERREUR LORS DE LA "
                        "SAUVEGARDE DES OPERATIONS"
                    )

                    raise

        else:

            logger.warning("=" * 80)

            logger.warning(
                "[FemiRouterManager] "
                "!!! AUCUNE OPERATION À SAUVEGARDER"
            )

            logger.warning(
                "[FemiRouterManager] "
                "accounting_results=%s",
                accounting_results,
            )

            logger.warning(
                "[FemiRouterManager] "
                "missing_fields=%s",
                missing_fields,
            )

            logger.warning(
                "[FemiRouterManager] "
                "needs_clarification=%s",
                needs_clarification,
            )

        # --------------------------------------------------------
        # Création des devis (QUOTE)
        # --------------------------------------------------------

        devis_crees, quote_erreurs = persist_quote_results(
            quote_results, entreprise
        )
        if quote_interdit:
            quote_erreurs = [MESSAGE_DEVIS_INTERDIT_EMPLOYE] + list(quote_erreurs)

        # ========================================================
        # RESULTAT FINAL
        # ========================================================

        logger.info("=" * 80)

        logger.info(
            "[FemiRouterManager] "
            ">>> FIN _build_result"
        )

        logger.info(
            "[FemiRouterManager] "
            "operation_ids=%s",
            operation_ids,
        )

        logger.info(
            "[FemiRouterManager] "
            "needs_clarification=%s",
            needs_clarification,
        )

        logger.info(
            "[FemiRouterManager] "
            "missing_fields=%s",
            missing_fields,
        )

        logger.info("=" * 80)

        return RouterProcessResult(
            success=True,
            message=cls._build_final_message(
                accounting_results=accounting_results,
                financial_analyst_results=financial_analyst_results,
                accounting_modify_results=accounting_modify_results,
                customer_results=customer_results,
                devis_crees=devis_crees,
                quote_erreurs=quote_erreurs,
                needs_clarification=needs_clarification,
                missing_fields=missing_fields,
                user_message=raw_text,
            ),
            router_output=router_output,
            operation_ids=operation_ids,
            needs_clarification=needs_clarification,
            missing_fields=missing_fields,
            accounting_results=accounting_results,
            financial_analyst_results=(
                financial_analyst_results
            ),
            accounting_modify_results=(
                accounting_modify_results
            ),
            customer_results=customer_results,
            quote_results=quote_results,
        )

    # ============================================================
    # PERSISTANCE ACCOUNTING
    # ============================================================

    @classmethod
    def _persist_accounting_results(
        cls,
        accounting_results,
        entreprise,
        utilisateur,
        source,
        raw_text,
        image_bytes,
    ) -> list[Operation]:

        logger.info("=" * 80)

        logger.info(
            "[FemiRouterManager] "
            ">>> _persist_accounting_results"
        )

        logger.info(
            "[FemiRouterManager] "
            "utilisateur_id=%s",
            getattr(
                utilisateur,
                "id",
                None,
            ),
        )

        logger.info(
            "[FemiRouterManager] "
            "entreprise_id=%s",
            getattr(
                entreprise,
                "id",
                None,
            ),
        )

        logger.info(
            "[FemiRouterManager] "
            "source=%s",
            source,
        )

        logger.info(
            "[FemiRouterManager] "
            "raw_text=%s",
            raw_text,
        )

        logger.info(
            "[FemiRouterManager] "
            "nombre accounting_results=%s",
            len(accounting_results),
        )

        logger.info(
            "[FemiRouterManager] "
            "accounting_results=%s",
            accounting_results,
        )

        # --------------------------------------------------------
        # Vérification utilisateur
        # --------------------------------------------------------

        if utilisateur is None:

            logger.error(
                "[FemiRouterManager] "
                "Impossible de persister une opération "
                "sans utilisateur."
            )

            return []

        all_operations: list[Operation] = []

        # --------------------------------------------------------
        # Sauvegarde de chaque résultat comptable
        # --------------------------------------------------------

        for index, result in enumerate(
            accounting_results,
            start=1,
        ):

            logger.info("-" * 70)

            logger.info(
                "[FemiRouterManager] "
                ">>> Sauvegarde accounting #%s",
                index,
            )

            logger.info(
                "[FemiRouterManager] "
                "result=%s",
                result,
            )

            try:

                logger.info(
                    "[FemiRouterManager] "
                    ">>> appel save_accounting_transactions()"
                )

                operations = (
                    save_accounting_transactions(
                        entreprise,
                        utilisateur,
                        result,
                        source,
                        raw_text,
                    )
                )

                logger.info(
                    "[FemiRouterManager] "
                    "<<< retour save_accounting_transactions()=%s",
                    operations,
                )

                logger.info(
                    "[FemiRouterManager] "
                    "type retour=%s",
                    (
                        type(operations).__name__
                        if operations is not None
                        else "None"
                    ),
                )

                if operations:

                    logger.info(
                        "[FemiRouterManager] "
                        "Nombre d'operations retournées=%s",
                        len(operations),
                    )

                    all_operations.extend(
                        operations
                    )

                else:

                    logger.warning(
                        "[FemiRouterManager] "
                        "!!! save_accounting_transactions "
                        "n'a retourné aucune opération."
                    )

            except Exception:

                logger.exception(
                    "[FemiRouterManager] "
                    "!!! Erreur lors de la sauvegarde "
                    "des transactions ACCOUNTING."
                )

                raise

        # --------------------------------------------------------
        # Résultat de la sauvegarde
        # --------------------------------------------------------

        logger.info("=" * 80)

        logger.info(
            "[FemiRouterManager] "
            ">>> OPERATIONS CRÉÉES=%s",
            all_operations,
        )

        logger.info(
            "[FemiRouterManager] "
            ">>> NOMBRE OPERATIONS=%s",
            len(all_operations),
        )

        logger.info(
            "[FemiRouterManager] "
            ">>> IDS OPERATIONS=%s",
            [
                str(operation.id)
                for operation in all_operations
            ],
        )

        # --------------------------------------------------------
        # Pièce justificative
        # --------------------------------------------------------

        if image_bytes:

            logger.info(
                "[FemiRouterManager] "
                "Image présente : ajout des pièces justificatives."
            )

            for operation in all_operations:

                # Doublon / facture déjà soldée : l'opération existe déjà
                # avec sa pièce, on n'en rattache pas une deuxième.
                if getattr(operation, "_femi_outcome", None) in (
                    "doublon",
                    "deja_soldee",
                ):
                    continue

                cls._attach_piece_justificative_bytes(
                    operation,
                    image_bytes,
                )

        else:

            logger.info(
                "[FemiRouterManager] "
                "Aucune image à attacher."
            )

        logger.info("=" * 80)

        return all_operations

    # ============================================================
    # RESULTAT ASYNCHRONE
    # ============================================================

    @classmethod
    async def _abuild_result(
        cls,
        router_output: RouterOutput,
        entreprise: Entreprise,
        utilisateur: Optional[Utilisateur] = None,
        source: str = "API",
        raw_text: str = "",
        image_bytes: Optional[bytes] = None,
        pending: Optional[PendingTurn] = None,
    ) -> RouterProcessResult:

        needs_clarification = False
        missing_fields: list[str] = []

        accounting_results: list[
            AccountingExtractionResult
        ] = []

        financial_analyst_results: list[
            ToolSelectionOutput | FinalAnswerOutput
        ] = []

        accounting_modify_results: list[
            AccountingModifySearchResult
            | AccountingModifyResolutionResult
            | AccountingModifyProposeChangeResult
        ] = []

        customer_results: list[
            ValidationOutput
            | CustomerExtractionOutput
            | ToolSelectionOutput
            | FinalAnswerOutput
        ] = []

        quote_results: list[QuoteExtractionResult] = []
        quote_interdit = False

        # --------------------------------------------------------
        # Dispatch
        # --------------------------------------------------------

        for intent in router_output.intents:

            # Un employé n'a pas accès aux devis : agent QUOTE non lancé.
            if intent.agent == "QUOTE" and utilisateur_est_employe(utilisateur):
                quote_interdit = True
                continue

            # FINANCIAL_ANALYST décide seul s'il doit clarifier (voir sync).
            if intent.agent != "FINANCIAL_ANALYST":
                if intent.needs_clarification:
                    needs_clarification = True

                for field in intent.missing_fields:

                    if field not in missing_fields:
                        missing_fields.append(field)

            segment, doc_flag = (
                pending.resolve(intent, bool(image_bytes))
                if pending is not None
                else (intent.raw_segment, bool(image_bytes))
            )

            result, dispatch_needs_clarification = (
                await cls._adispatch_intent(
                    intent,
                    entreprise,
                    from_document=doc_flag,
                    segment=segment,
                    history_text=getattr(pending, "_history_text", None),
                )
            )

            if pending is not None:
                pending.observe(
                    intent,
                    segment,
                    doc_flag,
                    bool(dispatch_needs_clarification)
                    and result is not None,
                    getattr(result, "missing_fields", None),
                )

            if dispatch_needs_clarification:
                needs_clarification = True

            # ----------------------------------------------------
            # Classification
            # ----------------------------------------------------

            if isinstance(
                result,
                AccountingExtractionResult,
            ):

                accounting_results.append(
                    result
                )

            elif isinstance(
                result,
                (
                    AccountingModifySearchResult,
                    AccountingModifyResolutionResult,
                    AccountingModifyProposeChangeResult,
                ),
            ):

                accounting_modify_results.append(
                    result
                )
                if (
                    pending is not None
                    and isinstance(result, AccountingModifyProposeChangeResult)
                    and getattr(result, "operation_id", None)
                ):
                    pending.store_confirmation(
                        agent="ACCOUNTING_MODIFY",
                        action_type=getattr(result, "action_type", "UPDATE") or "UPDATE",
                        operation_id=str(result.operation_id),
                        proposed_values=getattr(result, "proposed_values", None),
                        current_values=getattr(result, "current_values", None),
                        text="",
                    )

            elif isinstance(result, QuoteExtractionResult):

                quote_results.append(result)

            elif intent.agent == "CUSTOMER":

                if result is not None:
                    customer_results.append(result)

            elif isinstance(
                result,
                (
                    ToolSelectionOutput,
                    FinalAnswerOutput,
                ),
            ):

                financial_analyst_results.append(
                    result
                )

            # ----------------------------------------------------
            # Champs manquants
            # ----------------------------------------------------

            if (
                result is not None
                and hasattr(
                    result,
                    "missing_fields",
                )
            ):

                for field in result.missing_fields:

                    if field not in missing_fields:
                        missing_fields.append(field)

        # --------------------------------------------------------
        # PERSISTANCE ASYNCHRONE
        # --------------------------------------------------------

        valid_accounting_results = [
            result
            for result in accounting_results
            if not result.needs_clarification
        ]

        operation_ids: list[str] = []

        if (
            valid_accounting_results
            and utilisateur is not None
        ):

            operations = await sync_to_async(
                cls._persist_accounting_results
            )(
                accounting_results=(
                    valid_accounting_results
                ),
                entreprise=entreprise,
                utilisateur=utilisateur,
                source=source,
                raw_text=(
                    pending.effective_raw_text(raw_text)
                    if pending is not None
                    else raw_text
                ),
                image_bytes=image_bytes,
            )

            operation_ids = [
                str(operation.id)
                for operation in operations
            ]

        # --------------------------------------------------------
        # Création des devis (QUOTE)
        # --------------------------------------------------------

        devis_crees, quote_erreurs = await sync_to_async(
            persist_quote_results
        )(quote_results, entreprise)
        if quote_interdit:
            quote_erreurs = [MESSAGE_DEVIS_INTERDIT_EMPLOYE] + list(quote_erreurs)

        # --------------------------------------------------------
        # RESULTAT FINAL
        # --------------------------------------------------------

        return RouterProcessResult(
            success=True,
            message=cls._build_final_message(
                accounting_results=accounting_results,
                financial_analyst_results=financial_analyst_results,
                accounting_modify_results=accounting_modify_results,
                customer_results=customer_results,
                devis_crees=devis_crees,
                quote_erreurs=quote_erreurs,
                needs_clarification=needs_clarification,
                missing_fields=missing_fields,
                user_message=raw_text,
            ),
            router_output=router_output,
            operation_ids=operation_ids,
            needs_clarification=needs_clarification,
            missing_fields=missing_fields,
            accounting_results=accounting_results,
            financial_analyst_results=(
                financial_analyst_results
            ),
            accounting_modify_results=(
                accounting_modify_results
            ),
            customer_results=customer_results,
            quote_results=quote_results,
        )

    # ============================================================
    # PIECE JUSTIFICATIVE
    # ============================================================

    @staticmethod
    def _attach_piece_justificative_bytes(
        operation: Operation,
        image_bytes: bytes,
    ) -> None:

        import time

        # Format réel détecté sur les octets (image ou PDF)
        content_type = detect_mime(image_bytes) or "image/jpeg"
        filename = (
            f"whatsapp_{operation.id}"
            f"{extension_for(image_bytes)}"
        )

        max_retries = 3
        public_url = None

        for attempt in range(
            1,
            max_retries + 1,
        ):

            try:

                public_url = upload_file(
                    content=image_bytes,
                    filename=filename,
                    content_type=content_type,
                )

                break

            except Exception as exc:

                if attempt == max_retries:

                    logger.exception(
                        "[FemiRouterManager] "
                        "Échec définitif de l'upload "
                        "Supabase Storage après %d "
                        "tentatives pour l'opération %s",
                        max_retries,
                        operation.id,
                    )

                    return

                logger.warning(
                    "[FemiRouterManager] "
                    "Tentative %d/%d échouée pour "
                    "l'upload Supabase "
                    "(opération %s): %s. "
                    "Nouvelle tentative...",
                    attempt,
                    max_retries,
                    operation.id,
                    exc,
                )

                time.sleep(1)

        # --------------------------------------------------------
        # Création PieceJustificative
        # --------------------------------------------------------

        if public_url:

            try:

                PieceJustificative.objects.create(
                    operation=operation,
                    nom_fichier=filename,
                    url_fichier=public_url,
                    type_mime=content_type,
                    taille_octets=len(image_bytes),
                )

            except Exception:

                logger.exception(
                    "[FemiRouterManager] "
                    "Échec de la création de "
                    "PieceJustificative pour "
                    "l'opération %s",
                    operation.id,
                )