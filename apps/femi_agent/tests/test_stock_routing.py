"""Aiguillage vers l'agent STOCK : une vente, une dépense ou un paiement ne doit
jamais être détourné vers le stock (la recette ou la dépense ne serait alors
enregistrée nulle part)."""

from django.test import SimpleTestCase

from apps.femi_agent.agent.router_manager import (
    _force_stock_intent_if_needed as forcer,
    _message_looks_like_stock as ressemble_stock,
)
from apps.femi_agent.schemas.router import RouterIntent


def intention(agent, action, segment, ident="1"):
    return RouterIntent(
        intent_id=ident, agent=agent, action_type=action, confidence=0.95,
        is_sensitive=False, requires_confirmation=False, requires_tool=False,
        needs_clarification=False, missing_fields=[], merge_context=False,
        context_resolved=True, raw_segment=segment,
    )


# Messages d'argent : le Router les classe ACCOUNTING ou CUSTOMER, ça ne doit pas changer.
MESSAGES_COMPTABLES = [
    "J'ai vendu 3 sacs de ciment à 5000",
    "J'ai vendu 2 cartons de savon à Ama pour 12000",
    "J'ai vendu 4 pièces à 2500 chacune",
    "J'ai acheté 10 bouteilles d'eau pour 3000",
    "J'ai reçu 30 000 de Koffi",
    "J'ai reçu mon salaire",
    "J'ai reçu le paiement de la facture",
    "Ajoute une dépense de 5000 pour l'électricité",
    "J'ai payé 15 000 de transport",
    "Koffi m'a remboursé les 30 000 que je lui avais prêtés",
    "J'ai vendu 3 chemises à 5 000 chacune à Koffi en espèces",
    "Ajout d'une recette de 20 000",
]

# Messages clairement « stock » : le Router peut les classer ACCOUNTING par erreur.
MESSAGES_STOCK = [
    "J'ai reçu 20 sacs de ciment",
    "J'ai reçu 20 sacs de riz à 3000",
    "Entrée stock 20 de riz",
    "Ajout de 20 sacs en stock",
    "Sortie de stock de 3 sacs de ciment",
    "Fais l'inventaire de ciment",
    "Réappro de 50 bouteilles",
]


class AiguillageStockTests(SimpleTestCase):
    def test_les_messages_d_argent_ne_sont_pas_detournes(self):
        for message in MESSAGES_COMPTABLES:
            for agent in ("ACCOUNTING", "CUSTOMER"):
                with self.subTest(message=message, agent=agent):
                    sortie = forcer([intention(agent, "CREATE", message)], message)
                    self.assertEqual(sortie[0].agent, agent)

    def test_les_messages_de_stock_clairs_sont_envoyes_au_stock(self):
        for message in MESSAGES_STOCK:
            with self.subTest(message=message):
                sortie = forcer([intention("ACCOUNTING", "CREATE", message)], message)
                self.assertEqual(sortie[0].agent, "STOCK")
                self.assertEqual(sortie[0].action_type, "CREATE")

    def test_consulter_le_stock_est_une_lecture(self):
        for message in ("Montre mon stock", "Liste mes produits", "État du stock"):
            with self.subTest(message=message):
                sortie = forcer([intention("UNKNOWN", "READ", message)], message)
                self.assertEqual((sortie[0].agent, sortie[0].action_type), ("STOCK", "READ"))

    def test_une_modification_comptable_n_est_jamais_touchee(self):
        for message in ("Modifie ma dépense de 10 000 à 12 000", "Supprime la vente de 3 sacs", "Supprime la dépense de transport de 2 000"):
            with self.subTest(message=message):
                sortie = forcer([intention("ACCOUNTING_MODIFY", "UPDATE", message)], message)
                self.assertEqual(sortie[0].agent, "ACCOUNTING_MODIFY")

    def test_message_mixte_seul_le_segment_stock_est_converti(self):
        texte = "J'ai payé 15 000 de transport et j'ai reçu 20 sacs de riz"
        intentions = [
            intention("ACCOUNTING", "CREATE", "J'ai payé 15 000 de transport", "1"),
            intention("ACCOUNTING", "CREATE", "j'ai reçu 20 sacs de riz", "2"),
        ]
        sortie = forcer(intentions, texte)
        self.assertEqual([i.agent for i in sortie], ["ACCOUNTING", "STOCK"])

    def test_vente_et_stock_dans_le_meme_message_ne_force_rien(self):
        texte = "J'ai vendu 3 sacs à 5000, il m'en reste 10 en stock"
        sortie = forcer([intention("ACCOUNTING", "CREATE", texte)], texte)
        self.assertEqual(sortie[0].agent, "ACCOUNTING")

    def test_detection_directe(self):
        for message in MESSAGES_COMPTABLES:
            with self.subTest(message=message):
                self.assertFalse(ressemble_stock(message))
        for message in MESSAGES_STOCK:
            with self.subTest(message=message):
                self.assertTrue(ressemble_stock(message))
