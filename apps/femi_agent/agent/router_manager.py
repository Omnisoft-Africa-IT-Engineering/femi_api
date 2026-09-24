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
            #
            # IMPORTANT :
            # on transmet maintenant toutes les informations
            # nécessaires à _abuild_result().
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

            # ----------------------------------------------------
            # 3. Routage
            # ----------------------------------------------------

            router_output = RouterExecutor.execute(
                combined_text
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

        if intent.agent == "ACCOUNTING":

            result = AccountingExecutor.execute(
                intent.raw_segment,
                entreprise,
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
                cls._dispatch_intent(
                    intent,
                    entreprise,
                )
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

                accounting_results.append(result)

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
            # Champs manquants de l'agent
            # ----------------------------------------------------

            if (
                result is not None
                and hasattr(result, "missing_fields")
            ):

                for field in result.missing_fields:

                    if field not in missing_fields:
                        missing_fields.append(field)

        # --------------------------------------------------------
        # PERSISTANCE ACCOUNTING
        # --------------------------------------------------------

        clean_accounting_results = [
            result
            for result in accounting_results
            if not result.needs_clarification
        ]

        operation_ids: list[str] = []

        if clean_accounting_results:

            operations = cls._persist_accounting_results(
                accounting_results=clean_accounting_results,
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

        if utilisateur is None:

            logger.error(
                "[FemiRouterManager] "
                "Impossible de persister une opération "
                "sans utilisateur."
            )

            return []

        all_operations: list[Operation] = []

        for result in accounting_results:

            try:

                operations = (
                    save_accounting_transactions(
                        entreprise,
                        utilisateur,
                        result,
                        source,
                        raw_text,
                    )
                )

                if operations:
                    all_operations.extend(
                        operations
                    )

            except Exception:

                logger.exception(
                    "[FemiRouterManager] "
                    "Erreur lors de la sauvegarde "
                    "des transactions ACCOUNTING."
                )

                raise

        # --------------------------------------------------------
        # Pièce justificative
        # --------------------------------------------------------

        if image_bytes:

            for operation in all_operations:

                cls._attach_piece_justificative_bytes(
                    operation,
                    image_bytes,
                )

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

                accounting_results.append(result)

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
                and hasattr(result, "missing_fields")
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