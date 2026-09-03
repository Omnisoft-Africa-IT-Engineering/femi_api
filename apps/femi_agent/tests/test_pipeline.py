from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from apps.femi_agent.pipeline import (
    run_ai_extraction,
    _detect_category,
    _detect_payment_method,
    _fallback_parser,
)
from apps.femi_agent.agent.executor import AgentExecutionError
from apps.femi_agent.schemas import ParsedOperationSchema


class RunAiExtractionTestCase(TestCase):
    @patch("apps.femi_agent.pipeline.analyze_accounting_text")
    def test_llm_success_returns_llm_result_without_fallback(self, mock_analyze):
        expected = ParsedOperationSchema(
            transaction_type="RECETTE",
            amount_ttc=Decimal("15000"),
            category="Vente",
            description="Vente test",
            confidence_score=0.95,
        )
        mock_analyze.return_value = expected

        result = run_ai_extraction(text_input="Vente à 15000 FCFA")

        self.assertEqual(result, expected)
        mock_analyze.assert_called_once_with("Vente à 15000 FCFA")

    @patch("apps.femi_agent.pipeline.analyze_accounting_text")
    def test_agent_execution_error_falls_back_to_regex(self, mock_analyze):
        mock_analyze.side_effect = AgentExecutionError("Ollama indisponible")

        result = run_ai_extraction(text_input="Achat de carburant 5000 FCFA")

        self.assertIsInstance(result, ParsedOperationSchema)
        self.assertEqual(result.transaction_type, "DEPENSE")
        self.assertEqual(result.category, "Transport")

    @patch("apps.femi_agent.pipeline.analyze_accounting_text")
    def test_unexpected_exception_falls_back_to_regex(self, mock_analyze):
        mock_analyze.side_effect = ConnectionError("Ollama injoignable")

        result = run_ai_extraction(text_input="Loyer du bureau 50000 FCFA")

        self.assertIsInstance(result, ParsedOperationSchema)
        self.assertEqual(result.category, "Loyer & Charges")

    def test_invalid_image_bytes_raises_ocr_error(self):
        with self.assertRaises(AgentExecutionError) as ctx:
            run_ai_extraction(image_bytes=b"not_a_real_image")
        self.assertIn("Échec de l'extraction OCR", str(ctx.exception))

    def test_invalid_audio_bytes_raises_transcription_error(self):
        with self.assertRaises(AgentExecutionError) as ctx:
            run_ai_extraction(audio_bytes=b"not_a_real_audio")
        self.assertIn("Échec de la transcription audio", str(ctx.exception))

    @patch("apps.femi_agent.pipeline.extract_text_from_image")
    @patch("apps.femi_agent.pipeline.analyze_accounting_text")
    def test_valid_image_bytes_are_transcribed_and_analyzed(self, mock_analyze, mock_ocr):
        mock_ocr.return_value = "Vente de ciment 15000 FCFA"
        expected = ParsedOperationSchema(
            transaction_type="RECETTE",
            amount_ttc=Decimal("15000"),
            category="Vente",
            description="Vente de ciment",
            confidence_score=0.9,
        )
        mock_analyze.return_value = expected

        result = run_ai_extraction(image_bytes=b"fake_but_mocked")

        mock_ocr.assert_called_once_with(b"fake_but_mocked")
        mock_analyze.assert_called_once_with("Vente de ciment 15000 FCFA")
        self.assertEqual(result, expected)

    @patch("apps.femi_agent.pipeline.transcribe_audio")
    @patch("apps.femi_agent.pipeline.analyze_accounting_text")
    def test_valid_audio_bytes_are_transcribed_and_analyzed(self, mock_analyze, mock_audio):
        mock_audio.return_value = "Achat de carburant 5000 FCFA"
        expected = ParsedOperationSchema(
            transaction_type="DEPENSE",
            amount_ttc=Decimal("5000"),
            category="Transport",
            description="Achat de carburant",
            confidence_score=0.9,
        )
        mock_analyze.return_value = expected

        result = run_ai_extraction(audio_bytes=b"fake_but_mocked")

        mock_audio.assert_called_once_with(b"fake_but_mocked")
        mock_analyze.assert_called_once_with("Achat de carburant 5000 FCFA")
        self.assertEqual(result, expected)

    @patch("apps.femi_agent.pipeline.transcribe_audio")
    @patch("apps.femi_agent.pipeline.extract_text_from_image")
    @patch("apps.femi_agent.pipeline.analyze_accounting_text")
    def test_image_and_audio_combined(self, mock_analyze, mock_ocr, mock_audio):
        mock_ocr.return_value = "Facture fournisseur X"
        mock_audio.return_value = "C'est le reçu de ce matin"
        mock_analyze.return_value = ParsedOperationSchema(
            transaction_type="DEPENSE",
            amount_ttc=Decimal("1000"),
            category="Autre",
            description="combiné",
            confidence_score=0.7,
        )

        run_ai_extraction(image_bytes=b"img", audio_bytes=b"audio")

        called_text = mock_analyze.call_args[0][0]
        self.assertIn("Facture fournisseur X", called_text)
        self.assertIn("C'est le reçu de ce matin", called_text)

    def test_empty_text_raises_explicit_error(self):
        with self.assertRaises(AgentExecutionError) as ctx:
            run_ai_extraction(text_input="   ")
        self.assertIn("Aucun texte exploitable", str(ctx.exception))

    def test_no_input_at_all_raises_explicit_error(self):
        with self.assertRaises(AgentExecutionError):
            run_ai_extraction()


class DetectCategoryTestCase(TestCase):
    def test_transport_keywords(self):
        self.assertEqual(_detect_category("achat de carburant", is_recette=False), "Transport")
        self.assertEqual(_detect_category("course en taxi", is_recette=False), "Transport")

    def test_restauration_keywords(self):
        self.assertEqual(_detect_category("repas au restaurant", is_recette=False), "Restauration")

    def test_loyer_keywords(self):
        self.assertEqual(_detect_category("paiement du loyer", is_recette=False), "Loyer & Charges")

    def test_recette_without_keyword_defaults_to_vente(self):
        self.assertEqual(
            _detect_category("encaissement client", is_recette=True),
            "Vente de services / produits",
        )

    def test_depense_without_keyword_defaults_to_autre(self):
        self.assertEqual(_detect_category("achat divers", is_recette=False), "Autre")


class DetectPaymentMethodTestCase(TestCase):
    def test_mobile_money_keywords(self):
        self.assertEqual(_detect_payment_method("payé via wave"), "MOBILE_MONEY")
        self.assertEqual(_detect_payment_method("reçu par momo"), "MOBILE_MONEY")

    def test_bank_transfer_keywords(self):
        self.assertEqual(_detect_payment_method("paiement par virement"), "BANK_TRANSFER")

    def test_default_is_cash(self):
        self.assertEqual(_detect_payment_method("payé en liquide"), "CASH")


class FallbackParserTestCase(TestCase):
    def test_recette_detection(self):
        result = _fallback_parser("Vente de 3 sacs de ciment à 15000 FCFA")
        self.assertEqual(result.transaction_type, "RECETTE")

    def test_depense_detection(self):
        result = _fallback_parser("Achat de fournitures pour 8000 FCFA")
        self.assertEqual(result.transaction_type, "DEPENSE")

    def test_amount_takes_max_number_to_avoid_quantity_confusion(self):
        result = _fallback_parser("Vente de 2 sacs de ciment à 45000 FCFA")
        self.assertEqual(result.amount_ttc, Decimal("45000"))

    def test_no_number_defaults_to_zero(self):
        result = _fallback_parser("Vente de marchandises")
        self.assertEqual(result.amount_ttc, Decimal("0"))

    def test_confidence_score_is_lower_than_llm(self):
        result = _fallback_parser("Vente test")
        self.assertEqual(result.confidence_score, 0.80)