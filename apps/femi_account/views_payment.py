"""
Webhook de confirmation de paiement - cible de callback_url envoyee a
payWithFedaPay (voir integrations/fedapay_payment.py).

Flux confirme avec l equipe ImmoAsk : c est le compte FedaPay d ImmoAsk
qui recoit le webhook natif FedaPay, puis ImmoAsk le relaie vers cette
URL. femi_api ne parle donc jamais directement a FedaPay.

Principe de securite applique ici : le payload relaye par ImmoAsk ne sert
QUE de declencheur. On ne fait jamais confiance a son contenu (status)
pour activer un abonnement - on re-interroge systematiquement ImmoAsk
via get_transaction_status() avant de trancher.

RESTE A CONFIRMER avec l equipe ImmoAsk :
- Le nom exact du champ/requete GraphQL de statut (voir TODO dans
  fedapay_payment._STATUS_QUERY).
- Si ImmoAsk signe ses appels de relais (secret partage, en-tete HMAC) -
  _verify_signature() reste un stub tant que ce n est pas confirme.
"""

import json
import logging

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

from .models import Abonnement
from .integrations.fedapay_payment import get_transaction_status

logger = logging.getLogger(__name__)


def _verify_signature(request) -> bool:
    """
    TODO : verifier que l appel provient bien d ImmoAsk (secret partage,
    en-tete HMAC - a confirmer avec leur equipe). Retourne False par
    defaut : on prefere rejeter explicitement plutot que de faire
    confiance a un relais non authentifie.
    """
    if not settings.FEDAPAY_WEBHOOK_SECRET:
        logger.error(
            "FEDAPAY_WEBHOOK_SECRET absent : verification de signature impossible, "
            "callback rejete par securite."
        )
        return False
    return False


@method_decorator(csrf_exempt, name="dispatch")
class PaymentCallbackView(View):
    """Recoit le relais de confirmation FedaPay via ImmoAsk."""

    def post(self, request):
        if not _verify_signature(request):
            return JsonResponse({"status": "rejected"}, status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("Payload callback paiement illisible.")
            return JsonResponse({"status": "invalid_payload"}, status=400)

        logger.info("Callback paiement recu (declencheur, non fiable en soi) : %s", payload)
        transaction_id = payload.get("transaction_id") or payload.get("id")
        if not transaction_id:
            return JsonResponse({"status": "missing_transaction_id"}, status=400)

        try:
            abonnement = Abonnement.objects.get(transaction_id=transaction_id)
        except Abonnement.DoesNotExist:
            logger.warning("Callback paiement pour transaction_id inconnu : %s", transaction_id)
            return JsonResponse({"status": "unknown_transaction"}, status=404)

        if abonnement.statut in ("ACTIF", "ANNULE"):
            return JsonResponse({"status": "already_processed"}, status=200)

        real_status = get_transaction_status(transaction_id)

        if real_status in ("success", "approved", "completed", "paye"):
            abonnement.statut = "ACTIF"
        elif real_status in ("failed", "declined", "cancelled", "canceled"):
            abonnement.statut = "ANNULE"
        else:
            logger.warning(
                "Statut ImmoAsk non concluant (%s) pour transaction %s - abonnement laisse EN_ATTENTE",
                real_status, transaction_id,
            )
            return JsonResponse({"status": "pending_verification"}, status=200)

        abonnement.save(update_fields=["statut"])
        return JsonResponse({"status": "ok"}, status=200)