from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from apps.femi_account.models import Entreprise, Utilisateur, Operation

class FemiPipelineIntegrationTestCase(APITestCase):
    def setUp(self):
        # Création d'un tenant de test
        self.entreprise = Entreprise.objects.create(nom="Entreprise Test", devise="XOF")
        self.utilisateur = Utilisateur.objects.create(
            nom="Test User",
            telephone_whatsapp="+22800000000",
            entreprise=self.entreprise
        )
        self.url = reverse('process-transaction') # Adaptez le nom de votre URL si besoin

    def test_process_text_transaction(self):
        data = {
            "text": "Vente de 2 sacs à 15000 FCFA",
            "entreprise_id": str(self.entreprise.id),
            "utilisateur_id": str(self.utilisateur.id),
        }
        response = self.client.post(self.url, data, format='multipart')
        
        # Vérifications
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        res_json = response.json()
        self.assertTrue(res_json.get("success"))
        
        # S'assurer que l'opération a bien été créée en base
        self.assertTrue(Operation.objects.filter(entreprise=self.entreprise).exists())