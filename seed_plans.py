import os
import django

# Configuration de l'environnement Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings') # Adaptez selon le nom exact de votre dossier settings
django.setup()

from apps.femi_account.models import Plan

def seed_plans():
    plans_data = [
        {
            "nom": "Micro",
            "description": "Pour sortir du carnet et du tableur, sans changer vos habitudes.",
            "prix": 10.99,
            "est_a_partir_de": False,
            "devise": "EUR",
            "duree_jours": 30,
            "populaire": False,
            "fonctionnalites": [
                "Registre journalier numérique",
                "Tableau de bord avec KPIs de pilotage",
                "Enregistrement illimité de vos pièces comptables",
                "Rappels automatiques des échéances fiscales"
            ]
        },
        {
            "nom": "Pro",
            "description": "Pour piloter votre PME comme si vous aviez un comptable à temps plein.",
            "prix": 20.99,
            "est_a_partir_de": False,
            "devise": "EUR",
            "duree_jours": 30,
            "populaire": True, # LE PLUS CHOISI
            "fonctionnalites": [
                "Tout ce qui est inclus dans Micro",
                "Alertes en temps réel sur vos produits les plus performants",
                "Formation personnalisée de votre équipe",
                "Support prioritaire",
                "Documents comptables à tout moment : État, Balance, Grand Livre, Balance auxiliaire",
                "Historique conservé d'une année sur l'autre"
            ]
        },
        {
            "nom": "Business",
            "description": "Pour les structures multi-sites qui veulent un accompagnement sur-mesure.",
            "prix": 30.99,
            "est_a_partir_de": True, # À partir de 30.99€
            "devise": "EUR",
            "duree_jours": 30,
            "populaire": False,
            "fonctionnalites": [
                "Tout ce qui est inclus dans Pro",
                "Accompagnement dédié par un conseiller",
                "Intégration avec vos outils existants",
                "Déclaration de TVA et dépôt de vos documents comptables",
                "Rendez-vous de suivi réguliers"
            ]
        }
    ]

    for data in plans_data:
        plan, created = Plan.objects.update_or_create(
            nom=data["nom"],
            defaults=data
        )
        status = "Créé" if created else "Mis à jour"
        print(f"[{status}] Plan {plan.nom} - {plan.prix} {plan.devise}/mois")

if __name__ == "__main__":
    seed_plans()
    print("Injection des offres terminée avec succès !")