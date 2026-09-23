from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.femi_account.models import Entreprise, Utilisateur, Operation
from apps.femi_agent.agent.router_manager import FemiRouterManager


class FemiRouterManagerTestCase(TestCase):
    def setUp(self):
        self.entreprise = Entreprise.objects.create(nom="Test SARL", devise="XOF")
        self.utilisateur = Utilisateur.objects.create(
            username="testuser",
            full_name="Test User",
            telephone_whatsapp="+22890000000",
            entreprise=self.entreprise,
        )

    def test_greeting_returns_welcome_message_without_creating_operation(self):
        result = FemiRouterManager.route_message(
            message_text="Bonjour",
            entreprise_id=str(self.entreprise.id),
            utilisateur_id=str(self.utilisateur.id),
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

        result = FemiRouterManager.route_message(
            message_text="Quel est mon bilan aujourd'hui ?",
            entreprise_id=str(self.entreprise.id),
            utilisateur_id=str(self.utilisateur.id),
        )

        self.assertTrue(result.success)
        self.assertIn("20 000", result.message.replace("\u202f", " ").replace(",", " "))