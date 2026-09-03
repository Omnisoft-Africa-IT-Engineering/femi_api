from django.test import TestCase


from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.femi_account.models import Entreprise, Operation, Utilisateur


class FemiAPITestCase(APITestCase):
    """Classe de base : crée une entreprise + un utilisateur authentifié par token."""

    def setUp(self):
        self.entreprise = Entreprise.objects.create(nom="Entreprise Test", devise="XOF")
        self.user = Utilisateur.objects.create_user(
            username="test_user",
            password="test1234",
            entreprise=self.entreprise,
            role="DIRIGEANT",
        )
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")


class HealthCheckTests(APITestCase):
    """/health/ reste public et doit répondre 200 avec la BDD accessible."""

    def test_health_check_ok(self):
        response = self.client.get(reverse("api_health"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")
        self.assertEqual(response.data["database"], "ok")


class LoginAPITests(APITestCase):
    """/auth/token/ : login public, renvoie un token si les identifiants sont valides."""

    def setUp(self):
        self.entreprise = Entreprise.objects.create(nom="Entreprise Login", devise="XOF")
        self.user = Utilisateur.objects.create_user(
            username="login_user",
            password="secret123",
            entreprise=self.entreprise,
            role="EMPLOYE",
        )

    def test_login_success(self):
        response = self.client.post(reverse("api_auth_token"), {
            "username": "login_user",
            "password": "secret123",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("token", response.data)
        self.assertEqual(response.data["entreprise_nom"], "Entreprise Login")

    def test_login_wrong_password(self):
        response = self.client.post(reverse("api_auth_token"), {
            "username": "login_user",
            "password": "wrong",
        })
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_missing_fields(self):
        response = self.client.post(reverse("api_auth_token"), {"username": "login_user"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ProcessTransactionTests(FemiAPITestCase):

    def test_process_requires_auth(self):
        self.client.credentials()
        response = self.client.post(reverse("api_process_transaction"), {"text": "Vente 5000"})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("apps.femi_api.views.FemiAgentManager.process_transaction_text")
    def test_process_creates_operation(self, mock_process):
        operation = Operation.objects.create(
            entreprise=self.entreprise,
            transaction_type="RECETTE",
            amount_ttc=Decimal("5000.00"),
            category="Vente",
            description="Vente test",
            source="API",
        )
        mock_process.return_value = SimpleNamespace(
            success=True, message="Transaction enregistrée.", operation_instance=operation,
        )
        response = self.client.post(reverse("api_process_transaction"), {"text": "Vente 5000 FCFA"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @patch("apps.femi_api.views.FemiAgentManager.process_transaction_text")
    def test_process_question_no_save(self, mock_process):
        mock_process.return_value = SimpleNamespace(
            success=True, message="Votre bénéfice est de 10000 FCFA.", operation_instance=None,
        )
        response = self.client.post(reverse("api_process_transaction"), {"text": "Quel est mon bénéfice ?"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch("apps.femi_api.views.FemiAgentManager.process_transaction_text")
    def test_process_agent_failure(self, mock_process):
        mock_process.return_value = SimpleNamespace(success=False, message="Erreur IA", operation_instance=None)
        response = self.client.post(reverse("api_process_transaction"), {"text": "???"})
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_process_missing_input(self):
        response = self.client.post(reverse("api_process_transaction"), {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class BatchTransactionTests(FemiAPITestCase):

    def test_batch_requires_auth(self):
        self.client.credentials()
        response = self.client.post(reverse("api_transaction_batch"), {"transactions": [{"text": "x"}]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("apps.femi_api.views.FemiAgentManager.process_transaction_text")
    def test_batch_creates_multiple_operations(self, mock_process):
        operation = Operation.objects.create(
            entreprise=self.entreprise,
            transaction_type="DEPENSE",
            amount_ttc=Decimal("5000.00"),
            category="Fournitures",
            description="Achat test",
            source="MOBILE",
        )
        mock_process.return_value = SimpleNamespace(success=True, message="OK", operation_instance=operation)
        payload = {"transactions": [
            {"text": "Achat fournitures 5000", "client_ref": "ref1"},
            {"text": "Vente produit 15000", "client_ref": "ref2"},
        ]}
        response = self.client.post(reverse("api_transaction_batch"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["results"][0]["client_ref"], "ref1")

    def test_batch_empty_list_rejected(self):
        response = self.client.post(reverse("api_transaction_batch"), {"transactions": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_batch_over_50_rejected(self):
        payload = {"transactions": [{"text": f"item {i}"} for i in range(51)]}
        response = self.client.post(reverse("api_transaction_batch"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class DashboardKPITests(FemiAPITestCase):

    def test_kpis_requires_auth(self):
        self.client.credentials()
        response = self.client.get(reverse("api_dashboard_kpis"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_kpis_empty(self):
        response = self.client.get(reverse("api_dashboard_kpis"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["kpis"]["total_revenue"], 0.0)

    def test_kpis_with_operations(self):
        today = timezone.now().date()
        Operation.objects.create(
            entreprise=self.entreprise, transaction_type="RECETTE", amount_ttc=Decimal("10000.00"),
            category="Vente", description="Vente", transaction_date=today,
        )
        Operation.objects.create(
            entreprise=self.entreprise, transaction_type="DEPENSE", amount_ttc=Decimal("3000.00"),
            category="Fournitures", description="Achat", transaction_date=today,
        )
        response = self.client.get(reverse("api_dashboard_kpis") + "?period=today")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["kpis"]["total_revenue"], 10000.0)
        self.assertEqual(response.data["kpis"]["total_expenses"], 3000.0)
        self.assertEqual(response.data["kpis"]["net_profit"], 7000.0)


class ExportTransactionTests(FemiAPITestCase):

    def setUp(self):
        super().setUp()
        Operation.objects.create(
            entreprise=self.entreprise, transaction_type="RECETTE", amount_ttc=Decimal("15000.00"),
            category="Vente", description="Vente export test", transaction_date=timezone.now().date(),
        )

    def test_export_requires_auth(self):
        self.client.credentials()
        response = self.client.get(reverse("api_transaction_export"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_export_xlsx(self):
        response = self.client.get(reverse("api_transaction_export") + "?export_format=xlsx")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_export_pdf(self):
        response = self.client.get(reverse("api_transaction_export") + "?export_format=pdf")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_export_invalid_format(self):
        response = self.client.get(reverse("api_transaction_export") + "?export_format=doc")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)