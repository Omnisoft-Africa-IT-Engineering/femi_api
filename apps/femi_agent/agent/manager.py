import logging
from decimal import Decimal
from typing import Optional, Tuple

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.femi_account.models import Entreprise, Operation, Utilisateur
from apps.femi_agent.integrations.sheets_exporter import GoogleSheetsExporter
from apps.femi_agent.agent.pipeline import run_ai_extraction, arun_ai_extraction
from apps.femi_agent.schemas import ProcessResult
from apps.femi_agent.constants import GREETING_PATTERN, ANALYTICAL_PATTERN

logger = logging.getLogger(__name__)


class FemiAgentManager:
    """
    Gestionnaire central de l'agent Femi.
    Orchestre la détection d'intention, l'extraction IA et la persistance BDD.
    """

    # --- POINTS D'ENTRÉE PUBLICS ---

    @classmethod
    async def aprocess_transaction_text(
        cls,
        text_input: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        audio_bytes: Optional[bytes] = None,
        image_file=None,
        source: str = "API",
        entreprise_id: Optional[str] = None,
        utilisateur_id: Optional[str] = None,
    ) -> ProcessResult:
        """Point d'entrée ASYNCHRONE pour les vues Django Async / ASGI."""
        try:
            entreprise, utilisateur = await sync_to_async(cls._get_tenant_context)(
                entreprise_id, utilisateur_id
            )

            quick_reply = await sync_to_async(cls._quick_intent_response)(
                text_input, image_bytes, audio_bytes, entreprise
            )
            if quick_reply is not None:
                return quick_reply

            # raw_combined_text = texte utilisateur + OCR + transcription audio,
            # à conserver tel quel pour la traçabilité (NE PAS remplacer par
            # parsed_data.description, qui est une reformulation LLM).
            #
            # entreprise.nom est transmis au LLM (via pipeline.py -> executor.py)
            # pour lever l'ambiguïté RECETTE/DEPENSE sur les documents qui
            # mentionnent plusieurs entreprises (ex: facture fournisseur où le
            # nom du tenant apparaît comme acheteur, pas comme vendeur).
            parsed_data, raw_combined_text = await arun_ai_extraction(
                text_input=text_input,
                image_bytes=image_bytes,
                audio_bytes=audio_bytes,
                tenant_name=entreprise.nom,
            )

            return await sync_to_async(cls._finalize_transaction)(
                parsed_data, entreprise, utilisateur, source, raw_combined_text, image_file
            )

        except Entreprise.DoesNotExist:
            logger.error("[FemiAgent] Profil entreprise non trouvé : ID=%s", entreprise_id)
            return ProcessResult(success=False, message="Erreur : Profil entreprise introuvable.", parsed_data=None)
        except Exception:
            logger.exception("[FemiAgent] Erreur inattendue (async)")
            return ProcessResult(success=False, message="Une erreur technique est survenue.", parsed_data=None)

    @classmethod
    def process_transaction_text(
        cls,
        text_input: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        audio_bytes: Optional[bytes] = None,
        image_file=None,
        source: str = "API",
        entreprise_id: Optional[str] = None,
        utilisateur_id: Optional[str] = None,
    ) -> ProcessResult:
        """Point d'entrée SYNCHRONE (WSGI / Celery)."""
        try:
            entreprise, utilisateur = cls._get_tenant_context(entreprise_id, utilisateur_id)

            quick_reply = cls._quick_intent_response(text_input, image_bytes, audio_bytes, entreprise)
            if quick_reply is not None:
                return quick_reply

            parsed_data, raw_combined_text = run_ai_extraction(
                text_input=text_input,
                image_bytes=image_bytes,
                audio_bytes=audio_bytes,
                tenant_name=entreprise.nom,
            )

            return cls._finalize_transaction(
                parsed_data, entreprise, utilisateur, source, raw_combined_text, image_file
            )

        except Entreprise.DoesNotExist:
            logger.error("[FemiAgent] Entreprise introuvable ID=%s", entreprise_id)
            return ProcessResult(success=False, message="Profil entreprise introuvable.", parsed_data=None)
        except Exception:
            logger.exception("[FemiAgent] Erreur inattendue (sync)")
            return ProcessResult(success=False, message="Une erreur technique est survenue.", parsed_data=None)

    # --- LOGIQUE PARTAGÉE (mutualisée entre sync et async) ---

    @classmethod
    def _get_tenant_context(
        cls, entreprise_id: Optional[str], utilisateur_id: Optional[str]
    ) -> Tuple[Entreprise, Optional[Utilisateur]]:
        """Récupère entreprise + utilisateur en un seul aller-retour thread-pool."""
        entreprise = cls._get_entreprise(entreprise_id)
        utilisateur = cls._get_utilisateur(utilisateur_id)
        return entreprise, utilisateur

    @classmethod
    def _quick_intent_response(
        cls,
        text_input: Optional[str],
        image_bytes: Optional[bytes],
        audio_bytes: Optional[bytes],
        entreprise: Entreprise,
    ) -> Optional[ProcessResult]:
        """Court-circuite l'appel LLM pour les salutations et les requêtes analytiques."""
        if text_input and not image_bytes and not audio_bytes:
            clean_text = text_input.strip()

            if cls._is_pure_greeting(clean_text):
                return cls._get_greeting_response()

            if ANALYTICAL_PATTERN.search(clean_text):
                return cls._handle_analytical_query(entreprise)

        return None

    @classmethod
    def _finalize_transaction(
        cls,
        parsed_data,
        entreprise: Entreprise,
        utilisateur: Optional[Utilisateur],
        source: str,
        raw_text: str,
        image_file,
    ) -> ProcessResult:
        """Valide, persiste et exporte une transaction extraite par le LLM."""
        if not parsed_data or parsed_data.amount_ttc <= 0:
            return cls._get_invalid_transaction_response()

        operation = cls._save_operation(
            entreprise=entreprise,
            utilisateur=utilisateur,
            parsed_data=parsed_data,
            source=source,
            raw_text=raw_text,
            image_file=image_file,
        )

        cls._export_sheets_safe(operation)

        return ProcessResult(
            success=True,
            operation_id=str(operation.id),
            message=cls._format_success_message(operation),
            parsed_data=parsed_data,
            operation_instance=operation,
        )

    # --- HELPERS BDD ---

    @classmethod
    def _get_entreprise(cls, entreprise_id: Optional[str]) -> Entreprise:
        if entreprise_id:
            return Entreprise.objects.get(id=entreprise_id)
        raise Entreprise.DoesNotExist("Aucun ID d'entreprise fourni.")

    @classmethod
    def _get_utilisateur(cls, utilisateur_id: Optional[str]) -> Optional[Utilisateur]:
        if utilisateur_id:
            return Utilisateur.objects.filter(id=utilisateur_id).first()
        return None

    @classmethod
    def _save_operation(cls, entreprise, utilisateur, parsed_data, source, raw_text, image_file) -> Operation:
        """Centralise la création atomique d'une opération en base de données."""
        with transaction.atomic():
            return Operation.objects.create(
                entreprise=entreprise,
                cree_par=utilisateur,
                transaction_type=parsed_data.transaction_type,
                amount_ht=parsed_data.amount_ht or parsed_data.amount_ttc,
                tax_amount=parsed_data.tax_amount or Decimal("0.00"),
                amount_ttc=parsed_data.amount_ttc,
                currency=parsed_data.currency,
                category=parsed_data.category,
                vendor_or_client=parsed_data.vendor_or_client,
                payment_method=(
                    parsed_data.payment_method.value
                    if hasattr(parsed_data.payment_method, "value")
                    else parsed_data.payment_method
                ),
                transaction_date=parsed_data.transaction_date,
                description=parsed_data.description,
                confidence_score=parsed_data.confidence_score,
                source=source,
                # raw_text est désormais garanti non-vide (voir pipeline._extract_and_combine_text,
                # qui lève AgentExecutionError si rien n'est exploitable) — c'est le texte brut réel
                # (saisie utilisateur + OCR + transcription audio), jamais une reformulation LLM.
                raw_input_text=raw_text,
                receipt_image=image_file,
            )

    @classmethod
    def _is_pure_greeting(cls, text: str) -> bool:
        words = text.split()
        return len(words) <= 3 and bool(GREETING_PATTERN.search(text))

    @classmethod
    def _get_greeting_response(cls) -> ProcessResult:
        return ProcessResult(
            success=True,
            operation_id=None,
            message=(
                "👋 *Bonjour !* Je suis Femi, ton assistant financier.\n\n"
                "• Envoie-moi une transaction (ex: *Vente de 2 sacs à 15000 FCFA*).\n"
                "• Ou pose-moi une question (ex: *Combien j'ai vendu aujourd'hui ?*)."
            ),
            parsed_data=None,
        )

    @classmethod
    def _get_invalid_transaction_response(cls) -> ProcessResult:
        return ProcessResult(
            success=True,
            operation_id=None,
            message=(
                "🤔 Je n'ai pas identifié de transaction dans ce message.\n"
                "Envoie-moi une vente ou une dépense (ex: *Vente de 2 sacs à 15000 FCFA*)."
            ),
            parsed_data=None,
        )

    @classmethod
    def _handle_analytical_query(cls, entreprise: Entreprise) -> ProcessResult:
        today = timezone.now().date()

        totals = (
            Operation.objects.filter(entreprise=entreprise, transaction_date=today)
            .values("transaction_type")
            .annotate(total=Sum("amount_ttc"))
        )

        totals_map = {
            entry["transaction_type"]: entry["total"] or Decimal("0.00") for entry in totals
        }
        recettes = totals_map.get("RECETTE", Decimal("0.00"))
        depenses = totals_map.get("DEPENSE", Decimal("0.00"))
        solde_net = recettes - depenses
        devise = entreprise.devise or "XOF"

        reply_message = (
            f"📊 *Bilan du jour ({today.strftime('%d/%m/%Y')})*\n\n"
            f"• 💰 *Chiffre d'affaires* : {recettes:,.0f} {devise}\n"
            f"• 💸 *Total des dépenses* : {depenses:,.0f} {devise}\n"
            f"• ⚖️ *Solde net* : {solde_net:,.0f} {devise}"
        )

        return ProcessResult(success=True, operation_id=None, message=reply_message, parsed_data=None)

    @classmethod
    def _export_sheets_safe(cls, operation: Operation) -> None:
        try:
            GoogleSheetsExporter.append_operation(operation)
        except Exception:
            logger.error("[GoogleSheets] Échec de la synchronisation pour l'opération %s", operation.id)

    @staticmethod
    def _format_success_message(operation: Operation) -> str:
        icon = "📥" if operation.transaction_type == "RECETTE" else "📤"
        return (
            f"{icon} *{operation.transaction_type.capitalize()} enregistrée !*\n\n"
            f"• *Montant* : {operation.amount_ttc} {operation.currency}\n"
            f"• *Catégorie* : {operation.category}\n"
            f"• *Moyen de paiement* : {operation.payment_method}\n"
            f"• *Description* : {operation.description}"
        )