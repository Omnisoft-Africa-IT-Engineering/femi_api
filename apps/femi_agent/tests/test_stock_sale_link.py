"""Lien vente -> stock en mode hybride : automatique / confirmation / annulation."""

from decimal import Decimal

from django.test import TestCase

from apps.femi_account.models import Entreprise, MouvementStock, Produit
from apps.femi_account.stock_service import enregistrer_mouvement
from apps.femi_account.tests.factories import RecetteFactory
from apps.femi_agent.agent.stock_sale_link import (
    ACTION_TYPE,
    annuler_dernier_decompte,
    appliquer_sortie_vente,
    detecter_lignes,
    est_confirmation_sortie,
    est_demande_annulation_decompte,
    traiter_vente_stock,
)


class FauxPending:
    """Capture store_confirmation sans passer par la conversation."""

    def __init__(self):
        self.stockees = []

    def store_confirmation(self, **kwargs):
        self.stockees.append(kwargs)


class LienVenteStockTests(TestCase):
    def setUp(self):
        self.entreprise = Entreprise.objects.create(nom="Boutique Test", devise="XOF")
        self.riz = Produit.objects.create(entreprise=self.entreprise, nom="Riz", unite="sac")
        self.riz_p = Produit.objects.create(entreprise=self.entreprise, nom="Riz parfumé", unite="sac")
        for produit, qte in ((self.riz, 12), (self.riz_p, 4)):
            enregistrer_mouvement(
                entreprise=self.entreprise, produit_id=produit.pk,
                type_mouvement="ENTREE", quantite=qte, motif="STOCK_INITIAL", cout_unitaire=2000,
            )
        self.vente = RecetteFactory(entreprise=self.entreprise, amount_ttc=Decimal("15000"))
        self.pending = FauxPending()

    def _stock(self, produit):
        produit.refresh_from_db()
        return produit.stock_actuel

    def _traiter(self, texte, operations=None):
        return traiter_vente_stock(
            self.entreprise, operations or [self.vente], texte, self.pending
        )

    # -- détection ---------------------------------------------------
    def test_detection_prefere_le_nom_le_plus_long(self):
        lignes = detecter_lignes(self.entreprise, "j'ai vendu 2 sacs de riz parfumé à 4000")
        self.assertEqual([l["nom"] for l in lignes], ["Riz parfumé"])
        self.assertTrue(lignes[0]["avec_unite"])

    def test_pas_de_produit_connu_rien_a_dire(self):
        self.assertIsNone(self._traiter("j'ai vendu du savon pour 5000"))
        self.assertEqual(MouvementStock.objects.filter(motif="VENTE").count(), 0)

    # -- automatique -------------------------------------------------
    def test_cas_sur_decompte_automatique_et_rattache_a_la_vente(self):
        msg = self._traiter("j'ai vendu 5 sacs de riz à 3000")
        self.assertIn("J'ai décompté", msg)
        self.assertIn("12 → 7", msg)
        self.assertIn("annule le décompte", msg)
        self.assertEqual(self._stock(self.riz), Decimal("7"))
        m = MouvementStock.objects.get(produit=self.riz, motif="VENTE")
        self.assertEqual(m.operation_id, self.vente.pk)
        self.assertEqual(self.pending.stockees, [])  # aucune question

    def test_message_rejoue_ne_decompte_pas_deux_fois(self):
        self._traiter("j'ai vendu 5 sacs de riz")
        self._traiter("j'ai vendu 5 sacs de riz")
        self.assertEqual(self._stock(self.riz), Decimal("7"))

    def test_plusieurs_ventes_dans_le_tour_rien(self):
        autre = RecetteFactory(entreprise=self.entreprise)
        self.assertIsNone(self._traiter("vendu 5 sacs de riz", [self.vente, autre]))
        self.assertEqual(self._stock(self.riz), Decimal("12"))

    # -- douteux : confirmation -------------------------------------
    def test_quantite_egale_au_montant_demande_confirmation(self):
        vente = RecetteFactory(entreprise=self.entreprise, amount_ttc=Decimal("10"))
        msg = traiter_vente_stock(
            self.entreprise, [vente], "vendu 10 sacs de riz", self.pending
        )
        self.assertIn("« oui »", msg)
        self.assertEqual(self._stock(self.riz), Decimal("12"))  # rien d'écrit
        c = self.pending.stockees[0]
        self.assertEqual((c["agent"], c["action_type"]), ("STOCK", ACTION_TYPE))
        self.assertTrue(c["text"])
        entry = {k: c[k] for k in ("agent", "action_type", "operation_id", "proposed_values")}
        self.assertTrue(est_confirmation_sortie(entry))
        self.assertIn("décompté", appliquer_sortie_vente(self.entreprise, entry))
        self.assertEqual(self._stock(self.riz), Decimal("2"))
        self.assertIn("déjà fait", appliquer_sortie_vente(self.entreprise, entry))
        self.assertEqual(self._stock(self.riz), Decimal("2"))

    # -- insuffisant -------------------------------------------------
    def test_stock_insuffisant_avertit_sans_rien_ecrire(self):
        msg = self._traiter("j'ai vendu 50 sacs de riz")
        self.assertIn("Vérifie ton stock", msg)
        self.assertEqual(self._stock(self.riz), Decimal("12"))
        self.assertEqual(self.pending.stockees, [])

    # -- annulation --------------------------------------------------
    def test_annule_le_decompte_remet_le_stock_une_seule_fois(self):
        self._traiter("j'ai vendu 5 sacs de riz")
        self.assertTrue(est_demande_annulation_decompte("Annule le décompte"))
        self.assertFalse(est_demande_annulation_decompte("annule"))
        msg = annuler_dernier_decompte(self.entreprise)
        self.assertIn("annulé", msg)
        self.assertEqual(self._stock(self.riz), Decimal("12"))
        retour = MouvementStock.objects.get(produit=self.riz, motif="RETOUR_CLIENT")
        self.assertEqual(retour.operation_id, self.vente.pk)
        self.assertEqual(self._stock(self.riz), Decimal("12"))
        self.assertIn("aucun décompte", annuler_dernier_decompte(self.entreprise))
        self.assertEqual(self._stock(self.riz), Decimal("12"))