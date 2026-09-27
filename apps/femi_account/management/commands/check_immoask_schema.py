"""
Commande de diagnostic pour explorer le schéma GraphQL ImmoAsk et trouver
le vrai nom de la requête de statut de transaction (voir TODO dans
apps/femi_account/integrations/fedapay_payment.py — _STATUS_QUERY).

Usage :
    python manage.py check_immoask_schema
    python manage.py check_immoask_schema --field transactionStatus

Ne modifie rien en base — lecture seule, purement diagnostic.
"""

import json

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

_INTROSPECTION_ENABLED_CHECK = "{ __schema { queryType { name } } }"

_LIST_QUERY_FIELDS = "{ __schema { queryType { fields { name } } } }"

_FIELD_DETAIL_QUERY = """
{
  __type(name: "Query") {
    fields {
      name
      args { name type { name kind ofType { name } } }
      type { name kind ofType { name } }
    }
  }
}
"""


class Command(BaseCommand):
    help = "Interroge l'introspection GraphQL d'ImmoAsk pour trouver la requête de statut de transaction."

    def add_arguments(self, parser):
        parser.add_argument(
            "--field",
            help="Si fourni, tente une requête volontairement invalide sur ce nom de champ "
                 "pour lire le message d'erreur (utile si l'introspection est désactivée).",
        )

    def _post(self, query):
        headers = {"Content-Type": "application/json"}
        if settings.IMMOASK_API_KEY:
            headers["Authorization"] = f"Bearer {settings.IMMOASK_API_KEY}"
        response = requests.post(
            settings.IMMOASK_GRAPHQL_URL,
            json={"query": query},
            headers=headers,
            timeout=15,
        )
        return response

    def handle(self, *args, **options):
        field_probe = options.get("field")

        if field_probe:
            self.stdout.write(f"Sonde volontairement invalide sur le champ '{field_probe}'...")
            probe_query = f'{{ {field_probe}(transaction_id: "test") {{ __typename }} }}'
            response = self._post(probe_query)
            self.stdout.write(response.text)
            return

        self.stdout.write("1) Vérification que l'introspection est activée...")
        response = self._post(_INTROSPECTION_ENABLED_CHECK)
        if response.status_code != 200 or "errors" in response.json():
            self.stdout.write(self.style.WARNING(
                "Introspection probablement désactivée. Réponse brute :"
            ))
            self.stdout.write(response.text)
            self.stdout.write(self.style.WARNING(
                "\nRelance avec --field <nom_suppose> pour sonder via un message d'erreur, "
                "ex : python manage.py check_immoask_schema --field transactionStatus"
            ))
            return

        self.stdout.write(self.style.SUCCESS("Introspection active.\n"))

        self.stdout.write("2) Liste des requêtes disponibles...")
        response = self._post(_LIST_QUERY_FIELDS)
        data = response.json()
        fields = [f["name"] for f in data["data"]["__schema"]["queryType"]["fields"]]
        for name in fields:
            marker = "  <-- probablement lié au paiement" if any(
                kw in name.lower() for kw in ("transaction", "payment", "paiement", "feda", "status")
            ) else ""
            self.stdout.write(f"  - {name}{marker}")

        self.stdout.write("\n3) Détail des arguments et types de retour...")
        response = self._post(_FIELD_DETAIL_QUERY)
        detail = response.json()
        self.stdout.write(json.dumps(detail, indent=2, ensure_ascii=False))

        self.stdout.write(self.style.SUCCESS(
            "\nRepère le champ qui correspond au statut de transaction ci-dessus, "
            "puis mets à jour _STATUS_QUERY dans "
            "apps/femi_account/integrations/fedapay_payment.py en conséquence."
        ))