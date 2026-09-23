"""
Endpoints appelés uniquement par Supabase (pg_cron + pg_net).
Protégés par un secret partagé (header X-Cron-Secret), pas par l'authentification JWT classique.
"""
import hmac
import logging
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .tasks import (
    envoyer_rappels_echeances_fiscales,
    generer_echeances_annuelles_toutes_entreprises,
)

logger = logging.getLogger(__name__)

def _autorise(request) -> bool:
    secret_recu = request.headers.get("X-Cron-Secret", "")
    secret_attendu = getattr(settings, "CRON_SECRET", "")
    # Vérifie que le secret existe et qu'il correspond exactement (hmac.compare_digest évite les attaques temporelles)
    return bool(secret_attendu) and hmac.compare_digest(secret_recu, secret_attendu)

@csrf_exempt
@require_POST
def cron_rappels_echeances(request):
    if not _autorise(request):
        logger.warning("Tentative d'appel cron non autorisée sur rappels-echeances")
        return JsonResponse({"error": "unauthorized"}, status=401)
    
    # Appel synchrone direct, pas de .delay()
    resultat = envoyer_rappels_echeances_fiscales()  
    return JsonResponse({"result": resultat, "message": "Rappels traités avec succès"})

@csrf_exempt
@require_POST
def cron_generer_echeances(request):
    if not _autorise(request):
        logger.warning("Tentative d'appel cron non autorisée sur generer-echeances")
        return JsonResponse({"error": "unauthorized"}, status=401)
    
    # Appel synchrone direct
    resultat = generer_echeances_annuelles_toutes_entreprises()
    return JsonResponse({"result": resultat, "message": "Génération annuelle traitée avec succès"})