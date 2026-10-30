"""Garde montant : plus d'IntegrityError sur une vente sans montant."""

from decimal import Decimal

from django.test import TestCase

from apps.femi_account.models import Entreprise, Produit
from apps.femi_agent.agent.accounting_guard import completer_ou_demander_montant
from apps.femi_agent.schemas import AccountingExtractionResult
from apps.femi_agent.schemas.accounting import AccountingTransactionSchema


def _resultat(**kwargs):
    txn = AccountingTransactionSchema(
        transaction_type="RECETTE", description="Vente de 2 sacs de riz", **kwargs
    )
    return AccountingExtractionResult(transactions=[txn])


class GardeMontantTests(TestCase):
    def setUp(self):
        self.entreprise = Entreprise.objects.create(nom="Boutique Test", devise="XOF")

    def test_sans_montant_et_sans_prix_on_demande_le_montant(self):
        Produit.objects.create(entreprise=self.entreprise, nom="Riz", unite="sac")
        r = completer_ou_demander_montant(_resultat(), self.entreprise, "j'ai vendu 2 sacs de riz")
        self.assertTrue(r.needs_clarification)
        self.assertIn("amount_ttc", r.missing_fields)

    def test_prix_de_vente_du_catalogue_complete_le_montant(self):
        Produit.objects.create(
            entreprise=self.entreprise, nom="Riz", unite="sac", prix_vente=Decimal("2500")
        )
        r = completer_ou_demander_montant(_resultat(), self.entreprise, "j'ai vendu 2 sacs de riz")
        self.assertFalse(r.needs_clarification)
        self.assertEqual(r.transactions[0].amount_ttc, Decimal("5000"))
        self.assertIn("prix catalogue", r.transactions[0].description)

    def test_montant_present_rien_ne_change(self):
        r = completer_ou_demander_montant(
            _resultat(amount_ttc=Decimal("6000")), self.entreprise, "j'ai vendu 2 sacs de riz à 3000"
        )
        self.assertFalse(r.needs_clarification)
        self.assertEqual(r.transactions[0].amount_ttc, Decimal("6000"))
        self.assertEqual(r.transactions[0].description, "Vente de 2 sacs de riz")

    def test_produit_inconnu_sans_montant_on_demande(self):
        r = completer_ou_demander_montant(_resultat(), self.entreprise, "j'ai vendu 2 sacs de ciment")
        self.assertTrue(r.needs_clarification)
        self.assertIn("amount_ttc", r.missing_fields)