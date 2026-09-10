"""
Commande de gestion : seed_kpi_catalogue

Peuple le catalogue Kpi/Niveau (idempotent, get_or_create) avec les 5 niveaux
génériques universels + les listes spécifiques par secteur qui REMPLACENT
entièrement les génériques pour ce niveau (règle de priorité confirmée
en session : pas d'addition générique + spécifique).
"""

from django.core.management.base import BaseCommand

from apps.femi_account.models import Niveau, Kpi, Secteur


NIVEAUX_KPI = {
    1: {
        "nom": "Santé de l'entreprise",
        "kpis": [
            ("Chiffre d'affaires", "💰"),
            ("Bénéfice", "📈"),
            ("Dépenses", "💸"),
            ("Trésorerie", "💵"),
            ("Clients", "👥"),
            ("Créances", "🧾"),
        ],
    },
    2: {
        "nom": "Activité",
        "kpis": [
            ("Ventes", "🛒"),
            ("Commandes", None),
            ("Panier moyen", "🧺"),
            ("Produits", None),
            ("Clients", "👥"),
            ("Prestations", None),
        ],
    },
    3: {
        "nom": "Finance",
        "kpis": [
            ("Revenus", None),
            ("Dépenses", None),
            ("Marge", None),
            ("Bénéfice", None),
            ("Trésorerie", None),
            ("Dettes", None),
            ("Créances", None),
        ],
    },
    4: {
        "nom": "Opérations",
        "kpis": [
            ("Stocks", None),
            ("Fournisseurs", None),
            ("Production", None),
            ("Achats", None),
            ("Livraisons", None),
            ("Personnel", None),
        ],
    },
    5: {
        "nom": "Intelligence IA",
        "kpis": [
            ("Anomalies", None),
            ("Tendances", None),
            ("Prévisions", None),
            ("Alertes", None),
            ("Recommandations", None),
            ("Opportunités", None),
        ],
    },
}

KPI_PAR_SECTEUR = {
    "Personnel": {
        1: [
            ("Revenus", "💰"), ("Dépenses totales", "💸"), ("Dépenses par catégorie", "📊"),
            ("Épargne", "🏦"), ("Taux d'épargne", "📈"), ("Budget restant vs objectif", "🎯"),
        ],
    },
    "Commerce général": {
        1: [
            ("Chiffre d'affaires", "💰"), ("Bénéfice", "📈"), ("Marge", "📊"), ("Dépenses", "💸"),
            ("Trésorerie", "💵"), ("Clients", "👥"), ("Créances", "🧾"),
        ],
        2: [
            ("Ventes", "🛒"), ("Panier moyen", "🧺"), ("Clients", "👥"),
            ("Produits vendus", "📦"), ("Produits les plus vendus", "🏆"), ("Ruptures de stock", "⚠️"),
        ],
    },
    "Services": {
        2: [
            ("Nombre de prestations", "🧾"), ("Heures facturées", "⏱️"),
            ("Marge par prestation", "📊"), ("Clients", "👥"),
        ],
    },
}


class Command(BaseCommand):
    help = "Peuple le catalogue Kpi/Niveau (générique + spécifique par secteur), idempotent."

    def handle(self, *args, **options):
        for numero, data in NIVEAUX_KPI.items():
            niveau, created = Niveau.objects.get_or_create(numero=numero, defaults={"nom": data["nom"]})
            if not created and niveau.nom != data["nom"]:
                niveau.nom = data["nom"]
                niveau.save(update_fields=["nom"])

            for nom_kpi, icone in data["kpis"]:
                Kpi.objects.get_or_create(
                    niveau=niveau, nom=nom_kpi, secteur=None,
                    defaults={"icone": icone},
                )
            self.stdout.write(self.style.SUCCESS(f"Niveau {numero} ({data['nom']}) : {len(data['kpis'])} KPI génériques."))

        for secteur_nom, niveaux in KPI_PAR_SECTEUR.items():
            secteur, _ = Secteur.objects.get_or_create(nom=secteur_nom)
            for numero, kpis in niveaux.items():
                niveau = Niveau.objects.get(numero=numero)
                for nom_kpi, icone in kpis:
                    Kpi.objects.get_or_create(
                        niveau=niveau, nom=nom_kpi, secteur=secteur,
                        defaults={"icone": icone},
                    )
                self.stdout.write(self.style.SUCCESS(f"Secteur {secteur_nom}, niveau {numero} : {len(kpis)} KPI spécifiques."))

        self.stdout.write(self.style.SUCCESS("Catalogue KPI peuplé avec succès."))