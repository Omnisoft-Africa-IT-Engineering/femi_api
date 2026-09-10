"""
Commande de seed pour un test WhatsApp réel avec l'entreprise ZOLA.

Usage :
    python manage.py seed_whatsapp_test

Placer ce fichier dans :
    apps/femi_account/management/commands/seed_whatsapp_test.py

(créer les dossiers manquants avec des __init__.py vides :
 apps/femi_account/management/__init__.py
 apps/femi_account/management/commands/__init__.py
 si le dossier `management` n'existe pas encore dans femi_account)

⚠️ IMPORTANT — format du numéro WhatsApp :
Le numéro donné est "96401502". Meta envoie généralement le `wa_id` au format
international COMPLET sans "+" (ex: indicatif Togo 228 -> "22896401502").
Avant de tester, vérifie dans apps/femi_whatsapp/views.py comment le wa_id
entrant est comparé à Utilisateur.telephone_whatsapp (recherche exacte ?
normalisation ?). Ce script crée l'utilisateur avec DEUX numéros possibles
en variable en haut du fichier : ajuste TELEPHONE_WHATSAPP si besoin après
avoir vérifié le format réel reçu par ton webhook (tu peux logger le wa_id
brut lors du premier message de test pour confirmer le format exact).
"""

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from django.db import transaction

from apps.femi_account.models import (
    Secteur,
    Entreprise,
    Utilisateur,
    Categorie,
    Contact,
    Operation,
)

# --- Ajuste ici si le format attendu par le webhook diffère ---
TELEPHONE_WHATSAPP = "22896401502"  # indicatif Togo (228) + numéro fourni
# Si ton webhook compare le numéro brut sans indicatif, utilise plutôt :
# TELEPHONE_WHATSAPP = "96401502"

ENTREPRISE_NOM = "ZOLA"


class Command(BaseCommand):
    help = "Crée une entreprise ZOLA + utilisateur + données réelles pour un test WhatsApp"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Supprime l'entreprise ZOLA existante avant de la recréer",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            deleted, _ = Entreprise.objects.filter(nom=ENTREPRISE_NOM).delete()
            if deleted:
                self.stdout.write(self.style.WARNING(f"Anciennes données ZOLA supprimées ({deleted} objets liés)."))

        # 1. Secteur
        secteur, _ = Secteur.objects.get_or_create(
            nom="Commerce général",
            defaults={"description": "Vente de produits divers au détail"},
        )

        # 2. Entreprise
        entreprise, created = Entreprise.objects.get_or_create(
            nom=ENTREPRISE_NOM,
            defaults={
                "secteur": secteur,
                "rccm": "TG-LOM-2024-B-00001",
                "ifu": "IFU100000001",
                "regime_fiscal": "Réel simplifié",
                "devise": "XOF",
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Entreprise créée : {entreprise.nom} ({entreprise.id})"))
        else:
            self.stdout.write(self.style.WARNING(f"Entreprise déjà existante : {entreprise.nom} ({entreprise.id})"))

        # 3. Utilisateur lié au numéro WhatsApp réel
        user, created = Utilisateur.objects.get_or_create(
            telephone_whatsapp=TELEPHONE_WHATSAPP,
            defaults={
                "username": "zola_gerant",
                "email": "zola@femi-test.tg",
                "first_name": "Gérant",
                "last_name": "ZOLA",
                "entreprise": entreprise,
                "role": "GERANT",
                "is_active": True,
                "password": make_password("TestPass123!"),
            },
        )
        if not created and user.entreprise_id != entreprise.id:
            user.entreprise = entreprise
            user.save(update_fields=["entreprise"])
        self.stdout.write(self.style.SUCCESS(
            f"Utilisateur {'créé' if created else 'déjà existant'} : {user.username} — WhatsApp {user.telephone_whatsapp}"
        ))

        # 4. Catégories de base
        categories_recette = ["Ventes marchandises", "Prestations de services"]
        categories_depense = ["Achat marchandises", "Loyer", "Transport", "Électricité"]

        for nom in categories_recette:
            Categorie.objects.get_or_create(entreprise=entreprise, nom=nom, type="RECETTE")
        for nom in categories_depense:
            Categorie.objects.get_or_create(entreprise=entreprise, nom=nom, type="DEPENSE")
        self.stdout.write(self.style.SUCCESS("Catégories de base créées."))

        # 5. Quelques contacts
        contacts_data = [
            ("Client Boutique Centre-Ville", "22890111111", "CLIENT"),
            ("Fournisseur Togo Distribution", "22890222222", "FOURNISSEUR"),
        ]
        for nom, tel, type_ in contacts_data:
            Contact.objects.get_or_create(entreprise=entreprise, nom=nom, telephone=tel, type=type_)
        self.stdout.write(self.style.SUCCESS("Contacts créés."))

        # 6. Opérations existantes (pour tester "Combien j'ai vendu ce mois ?" etc.)
        if not Operation.objects.filter(entreprise=entreprise).exists():
            today = timezone.now().date()
            operations = []
            # Recettes réparties sur les 30 derniers jours
            for i in range(8):
                operations.append(Operation(
                    entreprise=entreprise,
                    transaction_type="RECETTE",
                    amount_ttc=random.choice([5000, 12000, 25000, 45000, 8000]),
                    currency="XOF",
                    category="Ventes marchandises",
                    payment_method=random.choice(["ESPECES", "MOBILE_MONEY"]),
                    transaction_date=today - timedelta(days=random.randint(0, 30)),
                    description="Vente au comptoir",
                    vendor_or_client="Client Boutique Centre-Ville",
                    source="MANUEL",
                ))
            # Dépenses
            for i in range(4):
                operations.append(Operation(
                    entreprise=entreprise,
                    transaction_type="DEPENSE",
                    amount_ttc=random.choice([3000, 15000, 20000]),
                    currency="XOF",
                    category=random.choice(["Achat marchandises", "Transport", "Loyer"]),
                    payment_method="ESPECES",
                    transaction_date=today - timedelta(days=random.randint(0, 30)),
                    description="Dépense courante",
                    vendor_or_client="Fournisseur Togo Distribution",
                    source="MANUEL",
                ))
            Operation.objects.bulk_create(operations)
            self.stdout.write(self.style.SUCCESS(f"{len(operations)} opérations historiques créées."))
        else:
            self.stdout.write(self.style.WARNING("Des opérations existent déjà pour ZOLA, aucune ajoutée."))

        self.stdout.write(self.style.SUCCESS(
            f"\n✅ Prêt pour le test WhatsApp.\n"
            f"   Entreprise : {entreprise.nom}\n"
            f"   Numéro attendu par le webhook : {TELEPHONE_WHATSAPP}\n"
            f"   Envoie un message WhatsApp depuis ce numéro pour déclencher le webhook."
        ))