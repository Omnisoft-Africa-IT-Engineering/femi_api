"""Tests de l'API d'historique des discussions.

Lancer uniquement ce module (les autres tests du projet ont leurs propres
prérequis) :

    python manage.py test apps.femi_api.tests_conversations
"""

import uuid
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.femi_account.models import (
    Conversation,
    ConversationHistory,
    Entreprise,
    Utilisateur,
)


def _utilisateur(username, entreprise):
    return Utilisateur.objects.create_user(
        username=username,
        password="motdepasse-de-test",
        email=f"{username}@test.tg",
        entreprise=entreprise,
    )


class HistoriqueConversationsTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.entreprise = Entreprise.objects.create(nom="Alpha")
        cls.autre_entreprise = Entreprise.objects.create(nom="Beta")
        cls.moi = _utilisateur("moi", cls.entreprise)
        cls.collegue = _utilisateur("collegue", cls.entreprise)
        cls.autre = _utilisateur("autre", cls.autre_entreprise)
        cls.sans_entreprise = _utilisateur("sans_entreprise", None)

    # --- outils -----------------------------------------------------------

    def _conversation(self, utilisateur, nb_messages, jours=0, canal="MOBILE"):
        conversation = Conversation.objects.create(
            utilisateur=utilisateur,
            entreprise=utilisateur.entreprise,
            canal_origine=canal,
            pending_action={"secret": "etat interne"},
        )
        for i in range(nb_messages):
            ConversationHistory.objects.create(
                conversation=conversation,
                canal=canal,
                expediteur="USER" if i % 2 == 0 else "AGENT",
                contenu_texte=f"message {i}",
            )
        Conversation.objects.filter(pk=conversation.pk).update(
            updated_at=timezone.now() - timedelta(days=jours)
        )
        return conversation

    def _liste(self, **params):
        return self.client.get(reverse("api_conversations_list"), params)

    def _messages(self, conversation_id, **params):
        url = reverse("api_conversation_messages", kwargs={"conversation_id": conversation_id})
        return self.client.get(url, params)

    # --- accès ------------------------------------------------------------

    def test_acces_refuse_sans_authentification(self):
        self.assertIn(self._liste().status_code, (401, 403))
        self.assertIn(self._messages(uuid.uuid4()).status_code, (401, 403))

    def test_compte_sans_entreprise_refuse(self):
        self.client.force_authenticate(self.sans_entreprise)
        self.assertEqual(self._liste().status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._messages(uuid.uuid4()).status_code, status.HTTP_400_BAD_REQUEST)

    # --- liste ------------------------------------------------------------

    def test_liste_isolee_par_utilisateur(self):
        self._conversation(self.moi, 2)
        self._conversation(self.collegue, 3)  # même entreprise, autre personne
        self._conversation(self.autre, 4)
        self.client.force_authenticate(self.moi)
        reponse = self._liste()
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual([c["nb_messages"] for c in reponse.data["results"]], [2])

    def test_liste_ordre_recent_dabord_et_conversation_vide_exclue(self):
        ancienne = self._conversation(self.moi, 1, jours=5)
        recente = self._conversation(self.moi, 2, jours=0)
        Conversation.objects.create(
            utilisateur=self.moi, entreprise=self.entreprise, canal_origine="MOBILE"
        )
        self.client.force_authenticate(self.moi)
        ids = [c["id"] for c in self._liste().data["results"]]
        self.assertEqual(ids, [str(recente.id), str(ancienne.id)])

    def test_apercu_tronque_et_types_sans_texte(self):
        longue = self._conversation(self.moi, 0)
        ConversationHistory.objects.create(
            conversation=longue, canal="MOBILE", expediteur="USER", contenu_texte="x" * 200
        )
        vocale = self._conversation(self.moi, 0, jours=1)
        ConversationHistory.objects.create(
            conversation=vocale, canal="MOBILE", expediteur="USER",
            type_message="AUDIO", contenu_texte=None,
        )
        self.client.force_authenticate(self.moi)
        apercus = {c["id"]: c["apercu"] for c in self._liste().data["results"]}
        self.assertEqual(len(apercus[str(longue.id)]), 80)
        self.assertTrue(apercus[str(longue.id)].endswith("…"))
        self.assertEqual(apercus[str(vocale.id)], "[Message vocal]")

    def test_etat_interne_jamais_expose(self):
        conversation = self._conversation(self.moi, 1)
        self.client.force_authenticate(self.moi)
        self.assertNotIn("pending_action", self._liste().data["results"][0])
        self.assertNotIn("pending_action", str(self._messages(conversation.id).data))

    def test_liste_une_seule_requete_sql(self):
        for jours in range(5):
            self._conversation(self.moi, 3, jours=jours)
        self.client.force_authenticate(self.moi)
        with self.assertNumQueries(1):
            self._liste()

    def test_liste_pagination_par_curseur(self):
        for jours in range(5):
            self._conversation(self.moi, 1, jours=jours)
        self.client.force_authenticate(self.moi)
        vus = []
        curseur = None
        for _ in range(10):  # garde-fou contre une boucle infinie
            params = {"limit": 2}
            if curseur:
                params["before"] = curseur
            reponse = self._liste(**params)
            self.assertEqual(reponse.status_code, 200)
            vus += [c["id"] for c in reponse.data["results"]]
            if not reponse.data["has_more"]:
                self.assertIsNone(reponse.data["next_before"])
                break
            curseur = reponse.data["next_before"]
        self.assertEqual(len(vus), 5)
        self.assertEqual(len(set(vus)), 5)  # aucun doublon

    def test_parametres_invalides(self):
        self.client.force_authenticate(self.moi)
        for params in ({"limit": "abc"}, {"limit": 0}, {"limit": -3}, {"before": "hier"}):
            with self.subTest(params=params):
                self.assertEqual(self._liste(**params).status_code, 400)

    def test_limit_plafonnee(self):
        self._conversation(self.moi, 1)
        self.client.force_authenticate(self.moi)
        self.assertEqual(self._liste(limit=100000).status_code, 200)

    # --- messages ---------------------------------------------------------

    def test_messages_chronologiques_et_pagination_complete(self):
        conversation = self._conversation(self.moi, 120)
        self.client.force_authenticate(self.moi)

        reponse = self._messages(conversation.id, limit=50)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data["conversation_id"], str(conversation.id))
        textes = [m["contenu_texte"] for m in reponse.data["results"]]
        self.assertEqual(textes[0], "message 70")   # les 50 derniers,
        self.assertEqual(textes[-1], "message 119")  # dans l'ordre chronologique
        self.assertTrue(reponse.data["has_more"])

        tous = list(textes)
        while reponse.data["has_more"]:
            reponse = self._messages(
                conversation.id, limit=50, before=reponse.data["next_before"]
            )
            tous = [m["contenu_texte"] for m in reponse.data["results"]] + tous
        self.assertEqual(tous, [f"message {i}" for i in range(120)])  # ni trou ni doublon

    def test_champs_dun_message(self):
        conversation = self._conversation(self.moi, 1)
        self.client.force_authenticate(self.moi)
        message = self._messages(conversation.id).data["results"][0]
        self.assertEqual(
            set(message),
            {"id", "expediteur", "type_message", "contenu_texte",
             "fichier_url", "operation_id", "canal", "created_at"},
        )

    def test_conversation_dun_autre_utilisateur_introuvable(self):
        du_collegue = self._conversation(self.collegue, 2)
        d_une_autre_entreprise = self._conversation(self.autre, 2)
        self.client.force_authenticate(self.moi)
        self.assertEqual(self._messages(du_collegue.id).status_code, 404)
        self.assertEqual(self._messages(d_une_autre_entreprise.id).status_code, 404)
        self.assertEqual(self._messages(uuid.uuid4()).status_code, 404)

    def test_messages_deux_requetes_sql(self):
        conversation = self._conversation(self.moi, 10)
        self.client.force_authenticate(self.moi)
        with self.assertNumQueries(2):
            self._messages(conversation.id)
