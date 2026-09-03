from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.femi_account.models import Entreprise, Operation
from apps.femi_agent.agent.manager import FemiAgentManager
from apps.femi_agent.schemas import ParsedOperationSchema


class FemiAgentManagerTestCase(TestCase):
    def setUp(self):
        self.entreprise = Entreprise.objects.create(nom="Test SARL", devise="XOF")

    def test_greeting_returns_welcome_message_without_creating_operation(self):
        result = FemiAgentManager.process_transaction_text(
            text_input="Bonjour",
            entreprise_id=self.entreprise.id,
        )
        self.assertTrue(result.success)
        self.assertIn("Femi", result.message)
        self.assertEqual(Operation.objects.count(), 0)

    def test_analytical_query_returns_correct_balance(self):
        today = timezone.now().date()
        Operation.objects.create(
            entreprise=self.entreprise,
            transaction_type="RECETTE",
            amount_ttc=Decimal("20000"),
            category="Vente",
            description="Vente test",
            transaction_date=today,
        )
        Operation.objects.create(
            entreprise=self.entreprise,
            transaction_type="DEPENSE",
            amount_ttc=Decimal("5000"),
            category="Transport",
            description="Dépense test",
            transaction_date=today,
        )

        result = FemiAgentManager.process_transaction_text(
            text_input="Quel est mon bilan aujourd'hui ?",
            entreprise_id=self.entreprise.id,
        )

        self.assertTrue(result.success)
        self.assertIn("20 000", result.message.replace("\u202f", " ").replace(",", " "))
        self.assertIn("5 000", result.message.replace("\u202f", " ").replace(",", " "))
        self.assertEqual(Operation.objects.count(), 2)

    @patch("apps.femi_agent.agent.manager.GoogleSheetsExporter")
    @patch("apps.femi_agent.agent.manager.run_ai_extraction")
    def test_recette_creates_operation_and_returns_instance(self, mock_extraction, mock_sheets):
        mock_extraction.return_value = ParsedOperationSchema(
            transaction_type="RECETTE",
            amount_ttc=Decimal("15000"),
            category="Vente de marchandises",
            vendor_or_client="Client X",
            payment_method="MOBILE_MONEY",
            description="Vente de 3 sacs de ciment",
            confidence_score=0.95,
        )

        result = FemiAgentManager.process_transaction_text(
            text_input="Vente de 3 sacs de ciment à 15000 FCFA en mobile money",
            entreprise_id=self.entreprise.id,
        )

        self.assertTrue(result.success)
        self.assertEqual(Operation.objects.count(), 1)
        operation = Operation.objects.first()
        self.assertEqual(operation.transaction_type, "RECETTE")
        self.assertEqual(operation.amount_ttc, Decimal("15000"))
        self.assertIsNotNone(result.operation_instance)
        self.assertEqual(result.operation_instance.id, operation.id)

    @patch("apps.femi_agent.agent.manager.GoogleSheetsExporter")
    @patch("apps.femi_agent.agent.manager.run_ai_extraction")
    def test_depense_creates_operation_with_correct_type(self, mock_extraction, mock_sheets):
        mock_extraction.return_value = ParsedOperationSchema(
            transaction_type="DEPENSE",
            amount_ttc=Decimal("3000"),
            category="Transport",
            payment_method="CASH",
            description="Taxi pour livraison",
            confidence_score=0.9,
        )

        result = FemiAgentManager.process_transaction_text(
            text_input="Taxi pour livraison 3000 FCFA",
            entreprise_id=self.entreprise.id,
        )

        self.assertTrue(result.success)
        operation = Operation.objects.first()
        self.assertEqual(operation.transaction_type, "DEPENSE")
        self.assertEqual(operation.category, "Transport")

    def test_invalid_image_bytes_returns_error_without_crashing(self):
        """Vérifie le fix du Bug 1 : pas de TypeError, erreur OCR gérée proprement."""
        result = FemiAgentManager.process_transaction_text(
            image_bytes=b"not_a_real_image",
            entreprise_id=self.entreprise.id,
        )

        self.assertFalse(result.success)
        self.assertIn("Échec de l'extraction OCR", result.message)
        self.assertEqual(Operation.objects.count(), 0)

    @patch("apps.femi_agent.agent.manager.GoogleSheetsExporter")
    @patch("apps.femi_agent.agent.manager.run_ai_extraction")
    def test_sheets_export_failure_does_not_block_operation_creation(
        self, mock_extraction, mock_sheets
    ):
        mock_extraction.return_value = ParsedOperationSchema(
            transaction_type="RECETTE",
            amount_ttc=Decimal("10000"),
            category="Vente",
            description="Vente test export",
            confidence_score=0.9,
        )
        mock_sheets.append_operation.side_effect = Exception("Erreur réseau Google Sheets")

        result = FemiAgentManager.process_transaction_text(
            text_input="Vente à 10000 FCFA",
            entreprise_id=self.entreprise.id,
        )

        self.assertTrue(result.success)
        self.assertEqual(Operation.objects.count(), 1)