import logging
from datetime import timedelta

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Entreprise, Plan, Abonnement
from .services import generer_echeances_otr

logger = logging.getLogger(__name__)

# Offre offerte à chaque nouvelle entreprise, et durée de l'essai.
PLAN_ESSAI = "Micro"
ESSAI_JOURS = 30
MODE_PAIEMENT_ESSAI = "ESSAI_GRATUIT"


@receiver(post_save, sender=Entreprise)
def auto_generer_echeances(sender, instance, created, **kwargs):
    if created:
        generer_echeances_otr(entreprise=instance, annee=timezone.now().year)


def activer_essai_gratuit(entreprise):
    """
    Crée un abonnement d'essai gratuit (offre Micro, 30 jours, statut ACTIF)
    pour une entreprise qui n'a encore aucun abonnement.

    Retourne l'Abonnement créé, ou None si rien n'a été fait (déjà un
    abonnement, ou plan Micro absent de la base).
    """
    if entreprise.abonnements.exists():
        return None

    plan = Plan.objects.filter(nom__iexact=PLAN_ESSAI).first()
    if plan is None:
        logger.warning(
            "Essai gratuit non activé pour %s : plan '%s' introuvable "
            "(lancer seed_plans.py).", entreprise.id, PLAN_ESSAI,
        )
        return None

    debut = timezone.localdate()
    return Abonnement.objects.create(
        entreprise=entreprise,
        plan=plan,
        prix_paye=0,
        mode_paiement=MODE_PAIEMENT_ESSAI,
        date_debut=debut,
        date_fin=debut + timedelta(days=ESSAI_JOURS),
        statut="ACTIF",
    )


@receiver(post_save, sender=Entreprise)
def auto_essai_gratuit(sender, instance, created, **kwargs):
    """Chaque nouvelle entreprise reçoit 30 jours d'offre Micro gratuite."""
    if not created:
        return
    try:
        # Savepoint : un échec ici ne doit jamais annuler l'inscription.
        with transaction.atomic():
            activer_essai_gratuit(instance)
    except Exception:
        logger.exception(
            "Activation de l'essai gratuit impossible pour %s", instance.id
        )