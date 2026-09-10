"""
Factories factory_boy pour apps/femi_account.

Usage :
    from apps.femi_account.tests.factories import (
        UtilisateurFactory, EntrepriseFactory, OperationFactory, ...
    )

    entreprise = EntrepriseFactory()
    user = UtilisateurFactory(entreprise=entreprise)
    ops = OperationFactory.create_batch(10, entreprise=entreprise)

Installation (si pas déjà fait) :
    pip install factory_boy Faker

Placer ce fichier dans : apps/femi_account/tests/factories.py
(créer apps/femi_account/tests/__init__.py si le dossier n'existe pas)
"""

import random
from datetime import timedelta

import factory
from factory.django import DjangoModelFactory
from django.utils import timezone

from apps.femi_account.models import (
    Utilisateur,
    Secteur,
    Entreprise,
    Operation,
    Niveau,
    Kpi,
    InsightIA,
    Categorie,
    Contact,
    Plan,
    Abonnement,
)


# ---------------------------------------------------------------------------
# Secteur
# ---------------------------------------------------------------------------
class SecteurFactory(DjangoModelFactory):
    class Meta:
        model = Secteur
        django_get_or_create = ("nom",)

    nom = factory.Iterator([
        "Commerce général",
        "Restauration",
        "Coiffure & Esthétique",
        "Couture & Textile",
        "Électronique",
        "Transport",
        "Agroalimentaire",
    ])
    description = factory.Faker("sentence", nb_words=8, locale="fr_FR")


# ---------------------------------------------------------------------------
# Entreprise
# ---------------------------------------------------------------------------
class EntrepriseFactory(DjangoModelFactory):
    class Meta:
        model = Entreprise

    nom = factory.Sequence(lambda n: f"Entreprise Démo {n}")
    secteur = factory.SubFactory(SecteurFactory)
    rccm = factory.Sequence(lambda n: f"TG-LOM-{2024 + n % 3}-B-{10000 + n}")
    ifu = factory.Sequence(lambda n: f"IFU{100000000 + n}")
    regime_fiscal = factory.Iterator(["Réel simplifié", "Réel normal", "Synthétique"])
    devise = "XOF"


# ---------------------------------------------------------------------------
# Utilisateur
# ---------------------------------------------------------------------------
class UtilisateurFactory(DjangoModelFactory):
    class Meta:
        model = Utilisateur
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@femi-demo.tg")
    first_name = factory.Faker("first_name", locale="fr_FR")
    last_name = factory.Faker("last_name", locale="fr_FR")
    entreprise = factory.SubFactory(EntrepriseFactory)
    role = factory.Iterator(["GERANT", "COMPTABLE", "EMPLOYE"])
    telephone_whatsapp = factory.Sequence(lambda n: f"+22890{100000 + n}")
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        raw_password = extracted or "TestPass123!"
        self.set_password(raw_password)
        if create:
            self.save()


# ---------------------------------------------------------------------------
# Categorie
# ---------------------------------------------------------------------------
class CategorieFactory(DjangoModelFactory):
    class Meta:
        model = Categorie
        django_get_or_create = ("entreprise", "nom", "type")

    entreprise = factory.SubFactory(EntrepriseFactory)
    type = factory.Iterator(["RECETTE", "DEPENSE"])

    @factory.lazy_attribute
    def nom(self):
        if self.type == "RECETTE":
            return random.choice(["Ventes marchandises", "Prestations de services", "Autres recettes"])
        return random.choice(["Achat marchandises", "Loyer", "Transport", "Salaires", "Électricité"])


# ---------------------------------------------------------------------------
# Contact (client / fournisseur)
# ---------------------------------------------------------------------------
class ContactFactory(DjangoModelFactory):
    class Meta:
        model = Contact

    entreprise = factory.SubFactory(EntrepriseFactory)
    nom = factory.Faker("company", locale="fr_FR")
    telephone = factory.Sequence(lambda n: f"+22891{200000 + n}")
    type = factory.Iterator(["CLIENT", "FOURNISSEUR"])


# ---------------------------------------------------------------------------
# Operation
# ---------------------------------------------------------------------------
class OperationFactory(DjangoModelFactory):
    class Meta:
        model = Operation

    entreprise = factory.SubFactory(EntrepriseFactory)
    transaction_type = factory.Iterator(["RECETTE", "DEPENSE"])
    amount_ttc = factory.LazyFunction(lambda: round(random.uniform(1000, 500000), 2))
    currency = "XOF"
    category = factory.Faker("word", locale="fr_FR")
    payment_method = factory.Iterator(["ESPECES", "MOBILE_MONEY", "VIREMENT", "CHEQUE"])
    transaction_date = factory.LazyFunction(
        lambda: timezone.now().date() - timedelta(days=random.randint(0, 180))
    )
    description = factory.Faker("sentence", nb_words=6, locale="fr_FR")
    vendor_or_client = factory.Faker("company", locale="fr_FR")
    source = factory.Iterator(["WHATSAPP_TEXT", "WHATSAPP_IMAGE", "WHATSAPP_AUDIO", "MANUEL"])


class RecetteFactory(OperationFactory):
    """Raccourci : force une opération de type RECETTE."""
    transaction_type = "RECETTE"


class DepenseFactory(OperationFactory):
    """Raccourci : force une opération de type DEPENSE."""
    transaction_type = "DEPENSE"


# ---------------------------------------------------------------------------
# Niveau (les 5 niveaux du dashboard)
# ---------------------------------------------------------------------------
class NiveauFactory(DjangoModelFactory):
    class Meta:
        model = Niveau
        django_get_or_create = ("numero",)

    numero = factory.Sequence(lambda n: n + 1)
    nom = factory.LazyAttribute(lambda o: {
        1: "Vue d'ensemble",
        2: "Trésorerie",
        3: "Performance",
        4: "Stock & Fournisseurs",
        5: "IA Prédictive",
    }.get(o.numero, f"Niveau {o.numero}"))


# ---------------------------------------------------------------------------
# Kpi
# ---------------------------------------------------------------------------
class KpiFactory(DjangoModelFactory):
    class Meta:
        model = Kpi

    niveau = factory.SubFactory(NiveauFactory)
    secteur = None  # KPI commun par défaut
    nom = factory.Faker("sentence", nb_words=3, locale="fr_FR")
    icone = factory.Iterator(["📊", "💰", "📈", "⚠️", "🔮"])
    formule_description = factory.Faker("sentence", nb_words=10, locale="fr_FR")
    unite = factory.Iterator(["XOF", "%", "jours", ""])


# ---------------------------------------------------------------------------
# InsightIA
# ---------------------------------------------------------------------------
class InsightIAFactory(DjangoModelFactory):
    class Meta:
        model = InsightIA

    entreprise = factory.SubFactory(EntrepriseFactory)
    type = factory.Iterator(
        ["ANOMALIE", "TENDANCE", "PREVISION", "ALERTE", "RECOMMANDATION", "OPPORTUNITE"]
    )
    message = factory.Faker("sentence", nb_words=12, locale="fr_FR")
    action_recommandee = factory.Faker("sentence", nb_words=8, locale="fr_FR")
    niveau_gravite = factory.Iterator(["INFO", "ATTENTION", "CRITIQUE"])


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
class PlanFactory(DjangoModelFactory):
    class Meta:
        model = Plan
        django_get_or_create = ("nom",)

    nom = factory.Iterator(["Micro", "Pro", "Business"])
    description = factory.Faker("sentence", nb_words=10, locale="fr_FR")
    prix = factory.LazyAttribute(lambda o: {
        "Micro": 10.99, "Pro": 20.99, "Business": 30.99
    }.get(o.nom, 15.99))
    est_a_partir_de = factory.LazyAttribute(lambda o: o.nom == "Business")
    devise = "EUR"
    duree_jours = 30
    populaire = factory.LazyAttribute(lambda o: o.nom == "Pro")
    fonctionnalites = factory.LazyAttribute(lambda o: {
        "Micro": ["Suivi recettes/dépenses", "1 utilisateur"],
        "Pro": ["Tout Micro", "Dashboard avancé", "3 utilisateurs"],
        "Business": ["Tout Pro", "IA prédictive", "Utilisateurs illimités"],
    }.get(o.nom, []))
    limite_operations_mensuelles = factory.LazyAttribute(lambda o: {
        "Micro": 100, "Pro": 500, "Business": None
    }.get(o.nom))
    limite_utilisateurs = factory.LazyAttribute(lambda o: {
        "Micro": 1, "Pro": 3, "Business": None
    }.get(o.nom))


# ---------------------------------------------------------------------------
# Abonnement
# ---------------------------------------------------------------------------
class AbonnementFactory(DjangoModelFactory):
    class Meta:
        model = Abonnement

    entreprise = factory.SubFactory(EntrepriseFactory)
    plan = factory.SubFactory(PlanFactory)
    prix_paye = factory.LazyAttribute(lambda o: o.plan.prix)
    date_debut = factory.LazyFunction(lambda: timezone.now().date())
    date_fin = factory.LazyAttribute(lambda o: o.date_debut + timedelta(days=o.plan.duree_jours))
    statut = "ACTIF"