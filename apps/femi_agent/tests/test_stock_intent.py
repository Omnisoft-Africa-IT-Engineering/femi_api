"""Détection stock du routeur : pas de faux positifs sur la compta."""

from django.test import SimpleTestCase

from apps.femi_agent.agent.stock_intent import is_stock_read, looks_like_stock


class StockIntentTests(SimpleTestCase):
    def test_messages_stock_detectes(self):
        for t in (
            "j'ai reçu 20 sacs de riz à 3000",
            "j’ai reçu 20 sacs de riz",
            "Entrée stock 20 de riz",
            "Ajout 20 sacs de riz en stock",
            "ajoute 10 cartons de lait",
            "sortie stock 5 bic",
            "mon stock",
            "est-ce que j'ai encore du riz en stock ?",
        ):
            self.assertTrue(looks_like_stock(t), t)

    def test_la_compta_n_est_pas_detournee(self):
        for t in (
            "j'ai reçu 30000 de Koffi",
            "j'ai reçu le paiement de la facture 12",
            "ajoute une dépense de 5000 pour le transport",
            "ajoute un client Mr Toto",
            "j'ai vendu 5 sacs de riz à 3000 chacun",
            "dépense 100000 achat de 50 kg de riz",
        ):
            self.assertFalse(looks_like_stock(t), t)

    def test_lecture_vs_ecriture(self):
        self.assertTrue(is_stock_read("mon stock"))
        self.assertTrue(is_stock_read("est-ce que j'ai encore du riz en stock ?"))
        self.assertFalse(is_stock_read("j'ai reçu 20 sacs de riz à 3000"))
        self.assertFalse(is_stock_read("Entrée stock 20 de riz"))