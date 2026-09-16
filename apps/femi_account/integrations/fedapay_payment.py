"""
Client d appel a la mutation GraphQL payWithFedaPay, exposee par le
backend ImmoAsk (endpoint mutualise, decision d equipe).

IMPORTANT - point non confirme a date :
Qui notifie FEMI_PAYMENT_CALLBACK_URL une fois le paiement confirme ?
C est le backend ImmoAsk qui recoit le webhook FedaPay natif et le
relaie vers nous (confirme). La verification de signature dans
apps/femi_account/views_payment.py reste un stub a completer tant que
le secret partage n est pas confirme avec ImmoAsk.
"""

import logging
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_MUTATION = """
mutation PayWithFedaPay($input: FedaPayInput!) {
  payWithFedaPay(input: $input) {
    transaction_id
    success
    message
    payment_url
    raw_response
  }
}
"""

# TODO : nom de champ/requete a confirmer avec l equipe ImmoAsk.
_STATUS_QUERY = """
query TransactionStatus($transactionId: String!) {
  transactionStatus(transaction_id: $transactionId) {
    transaction_id
    status
    amount
    raw_response
  }
}
"""


class FedaPayError(Exception):
    """Levee quand ImmoAsk repond mais signale un echec (success=false ou erreurs GraphQL)."""


def initiate_payment(
    *,
    amount,
    firstname,
    lastname,
    phone,
    email,
    description,
    country_code="TG",
    currency="XOF",
    reference=None,
):
    """
    Cree une transaction de paiement mobile via FedaPay (par l API ImmoAsk).
    Retourne un dict {transaction_id, payment_url, message, raw_response}.
    """
    reference = reference or str(uuid.uuid4())

    variables = {
        "input": {
            "description": description,
            "country_code": country_code,
            "amount": int(amount),
            "firstname": firstname,
            "lastname": lastname,
            "phone": phone,
            "callback_url": settings.FEMI_PAYMENT_CALLBACK_URL,
            "currency": currency,
            "email": email,
        }
    }

    headers = {"Content-Type": "application/json"}
    if settings.IMMOASK_API_KEY:
        headers["Authorization"] = f"Bearer {settings.IMMOASK_API_KEY}"

    response = requests.post(
        settings.IMMOASK_GRAPHQL_URL,
        json={"query": _MUTATION, "variables": variables},
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("errors"):
        logger.error("Erreur GraphQL payWithFedaPay (ref=%s): %s", reference, payload["errors"])
        raise FedaPayError(str(payload["errors"]))

    result = payload["data"]["payWithFedaPay"]
    if not result.get("success"):
        logger.warning("FedaPay a refuse la transaction (ref=%s): %s", reference, result.get("message"))
        raise FedaPayError(result.get("message") or "Echec de la creation du paiement FedaPay.")

    logger.info("Transaction FedaPay creee (ref=%s, transaction_id=%s)", reference, result.get("transaction_id"))
    return result


def get_transaction_status(transaction_id):
    """
    Interroge ImmoAsk pour connaitre le statut reel d une transaction.
    Retourne un statut normalise en minuscules, ou None si la requete
    echoue / le champ n existe pas encore cote ImmoAsk.
    """
    headers = {"Content-Type": "application/json"}
    if settings.IMMOASK_API_KEY:
        headers["Authorization"] = f"Bearer {settings.IMMOASK_API_KEY}"

    try:
        response = requests.post(
            settings.IMMOASK_GRAPHQL_URL,
            json={"query": _STATUS_QUERY, "variables": {"transactionId": transaction_id}},
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        logger.exception("Echec reseau lors de la verification du statut (transaction_id=%s)", transaction_id)
        return None

    if payload.get("errors"):
        logger.error(
            "Requete transactionStatus rejetee par ImmoAsk (transaction_id=%s) : %s.",
            transaction_id, payload["errors"],
        )
        return None

    data = (payload.get("data") or {}).get("transactionStatus")
    if not data:
        logger.warning("Aucune donnee transactionStatus renvoyee pour %s", transaction_id)
        return None

    return (data.get("status") or "").lower()