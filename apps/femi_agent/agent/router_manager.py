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

from apps.femi_account.models import (
    Entreprise,
    Operation,
    PieceJustificative,
    Utilisateur,
)

from apps.femi_agent.agent.accounting_executor import AccountingExecutor
from apps.femi_agent.agent.customer_executor import CustomerExecutor
from apps.femi_agent.agent.financial_analyst_executor import (
    FinancialAnalystExecutor,
)
from apps.femi_agent.agent.accounting_modify_executor import (
    AccountingModifyExecutor,
)

from apps.femi_agent.agent.manager import FemiAgentManager
from apps.femi_agent.agent.accounting_manager import (
    save_accounting_transactions,
)

from apps.femi_account.integrations.supabase_storage import upload_file

from apps.femi_agent.agent.ocr_executor import (
    OcrExecutor,
    OcrExecutionError,
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
    RouterIntent,
    RouterOutput,
    RouterProcessResult,
    ToolSelectionOutput,
    ValidationOutput,
)


logger = logging.getLogger(__name__)


DispatchResult = Optional[
    AccountingExtractionResult
    | ToolSelectionOutput
    | FinalAnswerOutput
    | AccountingModifySearchResult
    | AccountingModifyResolutionResult
    | AccountingModifyProposeChangeResult
]


class RouterInputError(Exception):
    """
    Aucune donnée textuelle ou multimédia exploitable n'a été fournie.
    """

    pass


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

            router_output = await RouterExecutor.aexecute(
                combined_text
            )

            # ----------------------------------------------------
            # 4. Dispatch + persistance
            # ----------------------------------------------------

            return await cls._abuild_result(
                router_output=router_output,
                entreprise=entreprise,
                utilisateur=utilisateur,
                source=source,
                raw_text=combined_text,
                image_bytes=image_bytes,
            )

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
                message=(
                    "Le traitement de l'image ou de l'audio "
                    "a échoué."
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

            router_output = RouterExecutor.execute(
                combined_text
            )

            logger.info(
                "[FemiRouterManager] "
                "Router terminé. Nombre d'intents=%s",
                len(router_output.intents),
            )

            # ----------------------------------------------------
            # 4. Dispatch + persistance
            # ----------------------------------------------------

            return cls._build_result(
                router_output=router_output,
                entreprise=entreprise,
                utilisateur=utilisateur,
                source=source,
                raw_text=combined_text,
                image_bytes=image_bytes,
            )

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
                message=(
                    "Le traitement de l'image ou de l'audio "
                    "a échoué."
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

        parts: list[str] = []

        # --------------------------------------------------------
        # Texte
        # --------------------------------------------------------

        if text_input and text_input.strip():
            parts.append(text_input.strip())

        # --------------------------------------------------------
        # OCR
        # --------------------------------------------------------

        if image_bytes:

            ocr_result = OcrExecutor.execute(
                image_bytes
            )

            if ocr_result.texte_brut_complet:

                parts.append(
                    ocr_result.texte_brut_complet
                )

            else:

                readable_text = (
                    cls._ocr_result_to_readable_text(
                        ocr_result
                    )
                )

                if readable_text:
                    parts.append(readable_text)

        # --------------------------------------------------------
        # AUDIO / STT
        # --------------------------------------------------------

        if audio_bytes:

            raw_transcription = transcribe_audio(
                audio_bytes
            )

            stt_result = SttExecutor.execute(
                raw_transcription
            )

            if stt_result.corrected_text:

                parts.append(
                    stt_result.corrected_text
                )

        # --------------------------------------------------------
        # Résultat final
        # --------------------------------------------------------

        combined = "\n".join(parts).strip()

        if not combined:

            raise RouterInputError(
                "Aucune donnée textuelle ou multimédia "
                "exploitable."
            )

        return combined

    # ============================================================
    # OCR → TEXTE
    # ============================================================

    @staticmethod
    def _ocr_result_to_readable_text(
        ocr_result,
    ) -> str:

        lines: list[str] = []

        en_tete = ocr_result.en_tete

        if en_tete.nom_commercant:
            lines.append(
                f"Commerçant : "
                f"{en_tete.nom_commercant}"
            )

        if en_tete.numero_facture_recu:
            lines.append(
                f"Référence : "
                f"{en_tete.numero_facture_recu}"
            )

        if en_tete.date:
            lines.append(
                f"Date : {en_tete.date}"
            )

        for ligne in ocr_result.lignes_articles:

            if not ligne.designation:
                continue

            detail = ligne.designation

            if ligne.quantite:

                detail += (
                    f" (quantité : "
                    f"{ligne.quantite}"
                )

                if ligne.prix_unitaire:

                    detail += (
                        f", prix unitaire : "
                        f"{ligne.prix_unitaire}"
                    )

                detail += ")"

            if ligne.prix_total:

                detail += (
                    f" — total : "
                    f"{ligne.prix_total}"
                )

            lines.append(detail)

        totaux = ocr_result.totaux

        if totaux.total_ht:

            lines.append(
                f"Total HT : "
                f"{totaux.total_ht}"
            )

        if totaux.tva:

            lines.append(
                f"TVA : {totaux.tva}"
            )

        if totaux.total_ttc:

            lines.append(
                f"Total TTC : "
                f"{totaux.total_ttc}"
            )

        if totaux.moyen_de_paiement:

            lines.append(
                f"Moyen de paiement : "
                f"{totaux.moyen_de_paiement}"
            )

        return "\n".join(lines)

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

        parts: list[str] = []

        # --------------------------------------------------------
        # Texte
        # --------------------------------------------------------

        if text_input and text_input.strip():
            parts.append(text_input.strip())

        # --------------------------------------------------------
        # OCR
        # --------------------------------------------------------

        if image_bytes:

            ocr_result = await OcrExecutor.aexecute(
                image_bytes
            )

            if ocr_result.texte_brut_complet:

                parts.append(
                    ocr_result.texte_brut_complet
                )

            else:

                readable_text = (
                    cls._ocr_result_to_readable_text(
                        ocr_result
                    )
                )

                if readable_text:
                    parts.append(readable_text)

        # --------------------------------------------------------
        # AUDIO / STT
        # --------------------------------------------------------

        if audio_bytes:

            raw_transcription = await sync_to_async(
                transcribe_audio
            )(audio_bytes)

            stt_result = await SttExecutor.aexecute(
                raw_transcription
            )

            if stt_result.corrected_text:

                parts.append(
                    stt_result.corrected_text
                )

        combined = "\n".join(parts).strip()

        if not combined:

            raise RouterInputError(
                "Aucune donnée textuelle ou multimédia "
                "exploitable."
            )

        return combined

    # ============================================================
    # TENANT / UTILISATEUR
    # ============================================================

    @classmethod
    def _get_tenant_context_stricte(
        cls,
        entreprise_id: Optional[str],
        utilisateur_id: Optional[str],
    ) -> Tuple[Entreprise, Utilisateur]:

        entreprise = (
            FemiAgentManager._get_entreprise(
                entreprise_id
            )
        )

        utilisateur = (
            cls._get_utilisateur_stricte(
                utilisateur_id
            )
        )

        return entreprise, utilisateur

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
    # DISPATCH SYNCHRONE
    # ============================================================

    @classmethod
    def _dispatch_intent(
        cls,
        intent: RouterIntent,
        entreprise: Entreprise,
    ) -> Tuple[DispatchResult, bool]:

        logger.info(
            "[FemiRouterManager] "
            "Dispatch intent: agent=%s | segment=%s",
            intent.agent,
            intent.raw_segment,
        )

        if intent.agent == "ACCOUNTING":

            result = AccountingExecutor.execute(
                intent.raw_segment,
                entreprise,
            )

            logger.info(
                "[FemiRouterManager] "
                "AccountingExecutor résultat=%s",
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

            return (
                result,
                result.needs_clarification,
            )

        if intent.agent == "FINANCIAL_ANALYST":

            result = (
                FinancialAnalystExecutor.execute(
                    intent.raw_segment,
                    entreprise,
                )
            )

            return (
                result,
                getattr(
                    result,
                    "needs_clarification",
                    False,
                ),
            )

        if intent.agent == "ACCOUNTING_MODIFY":

            result = (
                AccountingModifyExecutor.execute(
                    intent.raw_segment,
                    entreprise,
                )
            )

            return (
                result,
                result.needs_clarification,
            )

        if intent.agent == "CUSTOMER":

            result = CustomerExecutor.execute(
                intent.raw_segment,
                entreprise,
            )

            return (
                result,
                getattr(
                    result,
                    "needs_clarification",
                    False,
                ),
            )

        logger.warning(
            "[FemiRouterManager] "
            "Agent '%s' pas encore implémenté "
            "(intent_id=%s).",
            intent.agent,
            intent.intent_id,
        )

        return None, True

    # ============================================================
    # DISPATCH ASYNCHRONE
    # ============================================================

    @classmethod
    async def _adispatch_intent(
        cls,
        intent: RouterIntent,
        entreprise: Entreprise,
    ) -> Tuple[DispatchResult, bool]:

        if intent.agent == "ACCOUNTING":

            result = await AccountingExecutor.aexecute(
                intent.raw_segment,
                entreprise,
            )

            return (
                result,
                result.needs_clarification,
            )

        if intent.agent == "FINANCIAL_ANALYST":

            result = (
                await FinancialAnalystExecutor.aexecute(
                    intent.raw_segment,
                    entreprise,
                )
            )

            return (
                result,
                getattr(
                    result,
                    "needs_clarification",
                    False,
                ),
            )

        if intent.agent == "ACCOUNTING_MODIFY":

            result = (
                await AccountingModifyExecutor.aexecute(
                    intent.raw_segment,
                    entreprise,
                )
            )

            return (
                result,
                result.needs_clarification,
            )

        if intent.agent == "CUSTOMER":

            result = await CustomerExecutor.aexecute(
                intent.raw_segment,
                entreprise,
            )

            return (
                result,
                getattr(
                    result,
                    "needs_clarification",
                    False,
                ),
            )

        logger.warning(
            "[FemiRouterManager] "
            "Agent '%s' pas encore implémenté "
            "(intent_id=%s).",
            intent.agent,
            intent.intent_id,
        )

        return None, True

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

            if intent.needs_clarification:
                needs_clarification = True

            for field in intent.missing_fields:

                if field not in missing_fields:
                    missing_fields.append(field)

            # ----------------------------------------------------
            # Dispatch agent
            # ----------------------------------------------------

            try:

                result, dispatch_needs_clarification = (
                    cls._dispatch_intent(
                        intent,
                        entreprise,
                    )
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
                            raw_text=raw_text,
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
            message="Message routé avec succès.",
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

        # --------------------------------------------------------
        # Dispatch
        # --------------------------------------------------------

        for intent in router_output.intents:

            if intent.needs_clarification:
                needs_clarification = True

            for field in intent.missing_fields:

                if field not in missing_fields:
                    missing_fields.append(field)

            result, dispatch_needs_clarification = (
                await cls._adispatch_intent(
                    intent,
                    entreprise,
                )
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
                raw_text=raw_text,
                image_bytes=image_bytes,
            )

            operation_ids = [
                str(operation.id)
                for operation in operations
            ]

        # --------------------------------------------------------
        # RESULTAT FINAL
        # --------------------------------------------------------

        return RouterProcessResult(
            success=True,
            message="Message routé avec succès.",
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

        filename = (
            f"whatsapp_{operation.id}.jpg"
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
                    content_type="image/jpeg",
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
                    type_mime="image/jpeg",
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