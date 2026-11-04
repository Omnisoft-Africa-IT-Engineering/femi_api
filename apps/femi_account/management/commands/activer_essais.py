"""
Commande de gestion : activer_essais

Donne l'essai gratuit (offre Micro, 30 jours) aux entreprises DÉJÀ inscrites
qui n'ont encore aucun abonnement. Idempotent : une entreprise qui a déjà un
abonnement (quel que soit son statut) est ignorée.
"""

from django.core.management.base import BaseCommand

from apps.femi_account.models import Entreprise
from apps.femi_account.signals import activer_essai_gratuit


class Command(BaseCommand):
    help = "Active l'essai gratuit de 30 jours pour les entreprises sans abonnement."

    def handle(self, *args, **options):
        actives = 0
        ignorees = 0
        for entreprise in Entreprise.objects.all():
            if activer_essai_gratuit(entreprise):
                actives += 1
                self.stdout.write(f"Essai activé : {entreprise.nom}")
            else:
                ignorees += 1
        self.stdout.write(self.style.SUCCESS(
            f"Terminé : {actives} essai(s) activé(s), {ignorees} entreprise(s) ignorée(s)."
        ))