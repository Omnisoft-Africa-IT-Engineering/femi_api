"""Tests d'uniformité des KPI : un chiffre, une seule définition.

Jeu de données de référence (toutes les opérations datées d'aujourd'hui) :

    vente payée                    100 000
    vente à crédit, 50 000 payés   200 000
    achat payé                      60 000
    achat à crédit, rien payé       80 000
    emprunt reçu, 100 000 remboursés 500 000
    prêt donné, 10 000 récupérés     30 000

Valeurs attendues (calculées à la main) :
    trésorerie = 150 000 - 60 000 + 500 000 - 100 000 - 30 000 + 10 000 = 470 000
    créances 150 000 | dettes fournisseurs 80 000 | emprunts 400 000
    prêts accordés 20 000 | chiffre d'affaires 300 000 | dépenses 140 000
    bénéfice 160 000 | marge 53,33 %

Lancer :
    python manage.py test apps.femi_account.tests_kpi_uniformite
"""

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.femi_account import kpi_service as kpi
from apps.femi_account.kpi_views import avec_variation
from apps.femi_account.models import Entreprise, Operation, Utilisateur
from apps.femi_agent.agent.tools import financials as chat
from apps.femi_agent.agent.tools.debts import get_all_open_debts, get_all_open_payables
from apps.femi_account.soldes_ouverts import (
    total_creances_ouvertes,
    total_dettes_fournisseurs_ouvertes,
)


def _operation(entreprise, type_, ttc, statut="PAYE", paye=None, date=None):
    operation = Operation(
        entreprise=entreprise,
        transaction_type=type_,
        amount_ttc=Decimal(ttc),
        transaction_date=date or timezone.localdate(),
        statut_paiement=statut,
    )
    if paye is not None:
        operation.montant_paye = Decimal(paye)
    operation.save()
    return operation


def _utilisateur(nom, entreprise):
    return Utilisateur.objects.create_user(
        username=nom, password="motdepasse-de-test", email=f"{nom}@test.tg", entreprise=entreprise
    )


class KpiUniformiteTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.entreprise = Entreprise.objects.create(nom="Jeu de reference")
        cls.utilisateur = _utilisateur("ref", cls.entreprise)
        _operation(cls.entreprise, "RECETTE", "100000")
        _operation(cls.entreprise, "RECETTE", "200000", "CREDIT", "50000")
        _operation(cls.entreprise, "DEPENSE", "60000")
        _operation(cls.entreprise, "DEPENSE", "80000", "CREDIT", "0")
        _operation(cls.entreprise, "PRET_RECU", "500000", "CREDIT", "100000")
        _operation(cls.entreprise, "PRET_DONNE", "30000", "CREDIT", "10000")

    def _contexte(self, periode="this_month"):
        debut, fin = kpi.resoudre_periode(periode)
        return kpi.KpiContexte(self.entreprise, debut, fin)

    def _get(self, nom_url, **params):
        self.client.force_authenticate(self.utilisateur)
        reponse = self.client.get(reverse(nom_url), params)
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.data

    # --- valeurs de référence ---------------------------------------------

    def test_valeurs_de_reference(self):
        ctx = self._contexte()
        self.assertEqual(kpi.calc_chiffre_affaires(ctx), 300000)
        self.assertEqual(kpi.calc_depenses(ctx), 140000)
        self.assertEqual(kpi.calc_benefice(ctx), 160000)
        self.assertEqual(kpi.calc_marge(ctx), 53.33)
        self.assertEqual(kpi.calc_tresorerie(ctx), 470000)
        self.assertEqual(kpi.calc_creances(ctx), 150000)
        self.assertEqual(kpi.calc_dettes_fournisseurs(ctx), 80000)
        self.assertEqual(kpi.calc_emprunts(ctx), 400000)
        self.assertEqual(kpi.calc_dettes(ctx), 480000)
        self.assertEqual(kpi.calc_prets_accordes(ctx), 20000)

    # --- uniformité entre écrans ------------------------------------------

    def test_tresorerie_identique_partout(self):
        dashboard = self._get("api_dashboard_kpis")["treasury"]["balance"]
        bilan = self._get("api_bilan_synthese")["tresorerie"]["valeur"]
        etat = self._get("api_etat_financier")["actif"]["tresorerie_actif"]
        assistant = chat.calculate_balance(self.entreprise)["balance"]
        self.assertEqual({dashboard, bilan, etat, assistant}, {470000.0})

    def test_flux_identiques_dashboard_bilan_assistant(self):
        dashboard = self._get("api_dashboard_kpis")["kpis"]
        bilan = self._get("api_bilan_synthese")
        self.assertEqual(dashboard["total_revenue"], bilan["chiffre_affaires"]["valeur"])
        self.assertEqual(dashboard["net_profit"], bilan["resultat_net"]["valeur"])
        self.assertEqual(dashboard["profit_margin_percentage"], bilan["marge_pct"]["valeur"])
        self.assertEqual(chat.get_revenue(self.entreprise, "ce_mois")["revenue"], dashboard["total_revenue"])
        self.assertEqual(chat.get_expenses(self.entreprise, "ce_mois")["expenses"], dashboard["total_expenses"])
        self.assertEqual(chat.calculate_profit(self.entreprise, "ce_mois")["profit"], dashboard["net_profit"])
        self.assertEqual(
            chat.calculate_margin(self.entreprise, "ce_mois")["margin_percent"],
            dashboard["profit_margin_percentage"],
        )

    def test_cashflow_du_chat_coherent_avec_la_tresorerie(self):
        flux = chat.get_cashflow(self.entreprise, "ce_mois")
        self.assertEqual(flux["net"], 470000)      # tout est daté de ce mois
        self.assertEqual(flux["encaisse"], 150000)
        self.assertEqual(flux["decaisse"], 60000)  # l'achat à crédit n'est pas payé

    def test_creances_et_dettes_identiques(self):
        dashboard = self._get("api_dashboard_kpis")["kpis"]
        self.assertEqual(dashboard["total_receivables"], 150000)
        self.assertEqual(dashboard["total_debts"], 480000)

    # --- état financier ---------------------------------------------------

    def test_etat_financier_equilibre(self):
        etat = self._get("api_etat_financier")
        self.assertTrue(etat["equilibre"])
        self.assertEqual(etat["actif"]["total_actif"], 640000)
        self.assertEqual(etat["passif"]["total_passif"], 640000)
        self.assertEqual(etat["actif"]["actif_circulant"], 150000)
        self.assertEqual(etat["actif"]["prets_accordes"], 20000)
        self.assertEqual(etat["passif"]["passif_circulant"], 80000)
        self.assertEqual(etat["passif"]["dettes_financieres"], 400000)
        self.assertEqual(etat["postes_non_calcules"], ["actif_immobilise"])

    def test_bilan_annee_hors_limites(self):
        self.client.force_authenticate(self.utilisateur)
        self.assertEqual(self.client.get(reverse("api_bilan_synthese"), {"annee": 99999}).status_code, 400)

    # --- tendances --------------------------------------------------------

    def test_variation_cas_limites(self):
        self.assertEqual(kpi.variation(50000, -100000), 150.0)    # amélioration
        self.assertEqual(kpi.variation(-50000, 100000), -150.0)   # dégradation
        self.assertEqual(kpi.variation(120, 100), 20.0)
        self.assertIsNone(kpi.variation(50000, 0))                # aucune base
        self.assertIsNone(kpi.variation(None, 100))               # valeur absente

    def test_tendance_dashboard_apres_une_perte(self):
        """Mois précédent à -100 000, ce mois à +50 000 : +150 % (amélioration)."""
        entreprise = Entreprise.objects.create(nom="Redressement")
        utilisateur = _utilisateur("redressement", entreprise)
        mois_precedent, _ = kpi.resoudre_periode("last_month")
        _operation(entreprise, "DEPENSE", "100000", date=mois_precedent)
        _operation(entreprise, "RECETTE", "80000")
        _operation(entreprise, "DEPENSE", "30000")
        self.client.force_authenticate(utilisateur)
        tendances = self.client.get(reverse("api_dashboard_kpis")).data["trends"]
        self.assertEqual(tendances["profit_change_percentage"], 150.0)
        self.assertIsNone(tendances["revenue_change_percentage"])   # rien le mois d'avant

    # --- marge et valeurs absentes ----------------------------------------

    def test_marge_absente_sans_chiffre_daffaires(self):
        entreprise = Entreprise.objects.create(nom="Sans ventes")
        utilisateur = _utilisateur("sansventes", entreprise)
        _operation(entreprise, "DEPENSE", "40000")
        ctx = kpi.KpiContexte(entreprise, *kpi.resoudre_periode("this_month"))
        self.assertIsNone(kpi.calc_marge(ctx))
        self.assertEqual(kpi.calc_benefice(ctx), -40000)          # la perte reste visible
        self.client.force_authenticate(utilisateur)
        dashboard = self.client.get(reverse("api_dashboard_kpis")).data
        self.assertIsNone(dashboard["kpis"]["profit_margin_percentage"])
        bilan = self.client.get(reverse("api_bilan_synthese")).data
        self.assertFalse(bilan["marge_pct"]["disponible"])
        self.assertIsNone(chat.calculate_margin(entreprise, "ce_mois")["margin_percent"])

    def test_marge_negative_quand_les_depenses_depassent_les_ventes(self):
        entreprise = Entreprise.objects.create(nom="Deficit")
        _operation(entreprise, "RECETTE", "100000")
        _operation(entreprise, "DEPENSE", "150000")
        ctx = kpi.KpiContexte(entreprise, *kpi.resoudre_periode("this_month"))
        self.assertEqual(kpi.calc_marge(ctx), -50.0)

    def test_endpoint_kpi_ne_plante_pas_sans_chiffre_daffaires(self):
        entreprise = Entreprise.objects.create(nom="Vide")
        debut, fin = kpi.resoudre_periode("this_month")
        resultat = avec_variation(kpi.calculer_kpi_finance, entreprise, debut, fin)
        self.assertIsNone(resultat["marge"]["valeur"])
        self.assertIsNone(resultat["marge"]["variation"])

    def test_compare_periods_avec_marge_absente(self):
        comparaison = chat.compare_periods(self.entreprise, "margin", "ce_mois", "mois_dernier")
        self.assertIsNone(comparaison["variation_absolue"])
        self.assertIsNone(comparaison["variation_percent"])

    def test_champs_non_suivis_sont_null(self):
        dashboard = self._get("api_dashboard_kpis")
        self.assertIsNone(dashboard["kpis"]["pending_orders_count"])
        self.assertIsNone(dashboard["operations"]["staff_count"])
        self.assertIsNone(dashboard["ai_insights"]["alerts_count"])
        self.assertEqual(dashboard["kpis"]["services_count"], 0)

    def test_creances_kpi_egal_chat(self):
        """KPI Créances == total chat get_all_open_debts (source unique)."""
        ctx = self._contexte()
        kpi_val = kpi.calc_creances(ctx)
        chat_payload = get_all_open_debts(self.entreprise)
        self.assertEqual(kpi_val, 150000)
        self.assertEqual(chat_payload["total_general"], kpi_val)
        self.assertEqual(total_creances_ouvertes(self.entreprise), kpi_val)
        self.assertTrue(chat_payload["has_open_debt"])
        self.assertIn("150", chat_payload["resume_texte"].replace("\u202f", "").replace(" ", "").replace(",", ""))

    def test_dettes_fournisseurs_kpi_egal_chat(self):
        """KPI dettes fournisseurs == total chat get_all_open_payables."""
        ctx = self._contexte()
        kpi_val = kpi.calc_dettes_fournisseurs(ctx)
        chat_payload = get_all_open_payables(self.entreprise)
        self.assertEqual(kpi_val, 80000)
        self.assertEqual(chat_payload["total_general"], kpi_val)
        self.assertEqual(
            total_dettes_fournisseurs_ouvertes(self.entreprise), kpi_val
        )
        self.assertTrue(chat_payload["has_open_payable"])


