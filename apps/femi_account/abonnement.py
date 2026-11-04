"""
Règles d'accès par abonnement.

Un client a accès à Femi tant qu'il possède un abonnement au statut ACTIF
dont la date de fin n'est pas dépassée (l'essai gratuit de 30 jours est un
abonnement comme un autre, créé par signals.activer_essai_gratuit).

Le blocage n'est appliqué que si ABONNEMENT_OBLIGATOIRE est vrai (voir
core/settings.py) : on l'active une fois que le paiement sait réactiver un
abonnement.
"""

from django.conf import settings
from django.utils import timezone

from .models import Abonnement
from .signals import MODE_PAIEMENT_ESSAI


def abonnement_obligatoire():
    return bool(getattr(settings, "ABONNEMENT_OBLIGATOIRE", False))


def abonnement_courant(entreprise):
    """Abonnement ACTIF non échu (le plus long), ou None."""
    if entreprise is None:
        return None
    return (
        Abonnement.objects.filter(
            entreprise=entreprise,
            statut="ACTIF",
            date_fin__gte=timezone.localdate(),
        )
        .select_related("plan")
        .order_by("-date_fin")
        .first()
    )


def abonnement_actif(entreprise):
    return abonnement_courant(entreprise) is not None


def resume_abonnement(entreprise):
    abonnement = abonnement_courant(entreprise)
    if abonnement is None:
        return {
            "actif": False,
            "plan": None,
            "date_fin": None,
            "jours_restants": 0,
            "essai": False,
        }
    return {
        "actif": True,
        "plan": abonnement.plan.nom,
        "date_fin": abonnement.date_fin.isoformat(),
        "jours_restants": (abonnement.date_fin - timezone.localdate()).days,
        "essai": abonnement.mode_paiement == MODE_PAIEMENT_ESSAI,
    }
