"""
Commande de gestion : definir_seuils_alerte

Donne un seuil d'alerte aux produits actifs qui n'en ont pas (seuil à 0).
Sans seuil, un produit n'est jamais signalé avant la rupture.

Exemples :
    python manage.py definir_seuils_alerte --dry-run
    python manage.py definir_seuils_alerte --seuil 5
    python manage.py definir_seuils_alerte --seuil 10 --entreprise "Zolashop"
"""

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from apps.femi_account.models import Produit


class Command(BaseCommand):
    help = "Définit un seuil d'alerte sur les produits actifs qui n'en ont pas."

    def add_arguments(self, parser):
        parser.add_argument("--seuil", default="5", help="Seuil à appliquer (défaut : 5).")
        parser.add_argument("--entreprise", default=None, help="Nom exact d'une entreprise (sinon toutes).")
        parser.add_argument("--dry-run", action="store_true", help="Affiche le nombre de produits sans rien modifier.")

    def handle(self, *args, **options):
        try:
            seuil = Decimal(str(options["seuil"]).replace(",", "."))
        except InvalidOperation:
            raise CommandError("--seuil doit être un nombre.")
        if seuil <= 0:
            raise CommandError("--seuil doit être supérieur à 0.")

        produits = Produit.objects.filter(actif=True, seuil_alerte=0)
        if options["entreprise"]:
            produits = produits.filter(entreprise__nom=options["entreprise"])

        nombre = produits.count()
        if options["dry_run"]:
            self.stdout.write(f"{nombre} produit(s) seraient mis à jour (seuil {seuil}).")
            return

        produits.update(seuil_alerte=seuil)
        self.stdout.write(self.style.SUCCESS(f"{nombre} produit(s) mis à jour (seuil {seuil})."))
