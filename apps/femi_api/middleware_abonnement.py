"""
Bloque toute l'API applicative (/api/v1/...) quand l'abonnement du client
est expiré ou absent. Réponse : HTTP 402 avec code "abonnement_expire".

Le middleware lit lui-même l'en-tête « Authorization: Token ... » (celui
qu'envoie l'app mobile). Sans token valide il laisse passer : c'est DRF qui
répond 401. Rien n'est bloqué tant que ABONNEMENT_OBLIGATOIRE est faux.
"""

import logging

from django.http import JsonResponse
from rest_framework.authtoken.models import Token

from apps.femi_account.abonnement import abonnement_actif, abonnement_obligatoire

logger = logging.getLogger(__name__)

PREFIXE_API = "/api/v1/"

# Restent accessibles même sans abonnement : connexion, inscription,
# déconnexion, suppression de compte, mot de passe, statut/offres, santé du
# serveur, config, WhatsApp (le webhook gère lui-même le blocage, voir
# femi_whatsapp/tasks.py). Le reste de /auth/ (équipe, liaison WhatsApp)
# est une fonctionnalité : bloquée.
CHEMINS_OUVERTS = (
    "/api/v1/auth/token/",
    "/api/v1/auth/register/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/profile/",
    "/api/v1/auth/deactivate/",
    "/api/v1/auth/activate/",
    "/api/v1/auth/change-password/",
    "/api/v1/abonnement/",
    "/api/v1/health/",
    "/api/v1/config/",
    "/api/v1/whatsapp/",
)

MESSAGE_EXPIRE = (
    "Votre essai gratuit ou votre abonnement Femi a expiré. "
    "Choisissez une offre pour continuer à utiliser Femi."
)


class AbonnementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._doit_bloquer(request):
            return JsonResponse(
                {
                    "code": "abonnement_expire",
                    "error": MESSAGE_EXPIRE,
                    "action": "souscrire",
                },
                status=402,
            )
        return self.get_response(request)

    def _doit_bloquer(self, request):
        if not abonnement_obligatoire():
            return False
        if request.method == "OPTIONS":
            return False
        chemin = request.path
        if not chemin.startswith(PREFIXE_API) or chemin.startswith(CHEMINS_OUVERTS):
            return False

        en_tete = request.META.get("HTTP_AUTHORIZATION", "").split()
        if len(en_tete) != 2 or en_tete[0].lower() != "token":
            return False

        token = (
            Token.objects.select_related("user__entreprise")
            .filter(key=en_tete[1])
            .first()
        )
        if token is None:
            return False

        entreprise = getattr(token.user, "entreprise", None)
        if entreprise is None:
            return False

        return not abonnement_actif(entreprise)
