from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Entreprise
from .services import generer_echeances_otr


@receiver(post_save, sender=Entreprise)
def auto_generer_echeances(sender, instance, created, **kwargs):
    if created:
        generer_echeances_otr(entreprise=instance, annee=timezone.now().year)