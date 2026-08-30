from typing import Optional
from django.utils import timezone
from django.db.models import Sum
from decimal import Decimal

from apps.femi_account.models import Operation, Entreprise
from apps.femi_agent.pipeline import run_ai_extraction
from apps.femi_agent.schemas import ProcessResult
from apps.femi_agent.sheets_exporter import GoogleSheetsExporter


class FemiAgentManager:
    """
    Gestionnaire central unique de l'agent Femi.
    Gère l'enregistrement des transactions, les salutations et les bilans financiers.
    """

    @classmethod
    def process_transaction_text(
        cls,
        text_input: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        audio_bytes: Optional[bytes] = None,
        image_file=None,
        source: str = "API",
        entreprise_id: Optional[int] = None
    ) -> ProcessResult:
        """
        Analyse la requête utilisateur et choisit le traitement adapté :
        - Salutation / Politesse
        - Question analytique (bilan, ventes, dépenses)
        - Enregistrement d'une nouvelle transaction
        """
        try:
            # 1. Récupération de l'entreprise
            if entreprise_id:
                entreprise = Entreprise.objects.get(id=entreprise_id)
            else:
                entreprise = Entreprise.objects.first()
                if not entreprise:
                    entreprise = Entreprise.objects.create(nom="Entreprise Principale", devise="XOF")

            # 2. Détection des salutations
            if text_input and cls._is_greeting(text_input):
                return ProcessResult(
                    success=True,
                    operation_id=None,
                    message="👋 *Bonjour !* Je suis Femi, ton assistant financier.\n\n• Envoie-moi une transaction (ex: *Vente de 2 sacs à 15000 FCFA*).\n• Ou pose-moi une question (ex: *Combien j'ai vendu aujourd'hui ?*).",
                    parsed_data=None
                )

            # 3. Détection des questions sur le Chiffre d'Affaires ou le Bilan
            if text_input and cls._is_analytical_query(text_input):
                return cls._handle_analytical_query(text_input, entreprise)

            # 4. Enregistrement d'une transaction
            if image_bytes or audio_bytes:
                parsed_data = run_ai_extraction(
                    text_input=text_input,
                    image_bytes=image_bytes,
                    audio_bytes=audio_bytes
                )
            else:
                parsed_data = run_ai_extraction(text_input)

            operation = Operation.objects.create(
                entreprise=entreprise,
                transaction_type=parsed_data.transaction_type,
                amount_ht=parsed_data.amount_ht or parsed_data.amount_ttc,
                tax_amount=parsed_data.tax_amount or 0,
                amount_ttc=parsed_data.amount_ttc,
                currency=parsed_data.currency,
                category=parsed_data.category,
                vendor_or_client=parsed_data.vendor_or_client,
                payment_method=parsed_data.payment_method,
                transaction_date=parsed_data.transaction_date,
                description=parsed_data.description,
                confidence_score=parsed_data.confidence_score,
                source=source,
                raw_input_text=text_input or parsed_data.description,
                receipt_image=image_file
            )

            # Exportation Google Sheets
            try:
                GoogleSheetsExporter.append_operation(operation)
            except Exception as sheet_err:
                print(f"[EXPORTEUR SHEETS ERREUR] : {str(sheet_err)}")

            # Message de confirmation
            icon = "📥" if operation.transaction_type == "RECETTE" else "📤"
            formatted_message = (
                f"{icon} *{operation.transaction_type.capitalize()} enregistrée !*\n\n"
                f"• *Montant* : {operation.amount_ttc} {operation.currency}\n"
                f"• *Catégorie* : {operation.category}\n"
                f"• *Moyen de paiement* : {operation.payment_method}\n"
                f"• *Description* : {operation.description}"
            )

            return ProcessResult(
                success=True,
                operation_id=str(operation.id),
                message=formatted_message,
                parsed_data=parsed_data,
                operation_instance=operation
            )

        except Exception as e:
            return ProcessResult(
                success=False,
                operation_id=None,
                message=f"Erreur lors du traitement : {str(e)}",
                parsed_data=None
            )

    @classmethod
    def _is_greeting(cls, text: str) -> bool:
        """Détecte les salutations et politesses simples."""
        text_lower = text.lower().strip()
        greetings = [
            "salut", "bonjour", "hello", "coucou", "bonsoir", 
            "comment vas tu", "comment vas-tu", "comment tu vas", 
            "ca va", "ça va", "sava"
        ]
        return any(g in text_lower for g in greetings)

    @classmethod
    def _is_analytical_query(cls, text: str) -> bool:
        """Détecte si l'utilisateur pose une question de consultation/bilan."""
        text_clean = " ".join(text.lower().replace("'", "' ").split())
        
        # Mots-clés explicites de consultation (évite les mots isolés comme 'vendu')
        keywords = [
            "chiffre d'affaire", "chiffre d'affaires", "combien j'ai", 
            "combien ai-je", "combien de", "comment j'ai", "comment ai-je",
            "bilan", "résumé", "rapport", "solde", "statistique", 
            "total des", "total de"
        ]
        return any(kw in text_clean for kw in keywords)

    @classmethod
    def _handle_analytical_query(cls, text_input: str, entreprise: Entreprise) -> ProcessResult:
        """Calcule et formule la réponse aux questions financières."""
        today = timezone.now().date()
        
        ops_today = Operation.objects.filter(entreprise=entreprise, transaction_date=today)

        total_recettes = ops_today.filter(transaction_type="RECETTE").aggregate(
            total=Sum('amount_ttc')
        )['total'] or Decimal('0.00')

        total_depenses = ops_today.filter(transaction_type="DEPENSE").aggregate(
            total=Sum('amount_ttc')
        )['total'] or Decimal('0.00')

        solde_net = total_recettes - total_depenses
        devise = entreprise.devise or "XOF"

        reply_message = (
            f"📊 *Bilan du jour ({today.strftime('%d/%m/%Y')})*\n\n"
            f"• 💰 *Chiffre d'affaires (Ventes)* : {total_recettes:,.0f} {devise}\n"
            f"• 💸 *Total des dépenses* : {total_depenses:,.0f} {devise}\n"
            f"• ⚖️ *Solde net* : {solde_net:,.0f} {devise}"
        )

        return ProcessResult(
            success=True,
            operation_id=None,
            message=reply_message,
            parsed_data=None,
            operation_instance=None
        )