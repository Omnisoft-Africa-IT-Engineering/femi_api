"""
Script AUTONOME (sans Django) pour explorer le sch?ma GraphQL ImmoAsk et
trouver le vrai nom de la requ?te de statut de transaction.
"""

import json
import sys

import requests

IMMOASK_GRAPHQL_URL = "https://immoaskprodapi.omnisoft.africa/api/v2"
IMMOASK_API_KEY = ""

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


def post(query):
    headers = {"Content-Type": "application/json"}
    if IMMOASK_API_KEY:
        headers["Authorization"] = f"Bearer {IMMOASK_API_KEY}"
    return requests.post(IMMOASK_GRAPHQL_URL, json={"query": query}, headers=headers, timeout=15)


def main():
    if len(sys.argv) > 1:
        field_probe = sys.argv[1]
        print(f"Sonde volontairement invalide sur le champ '{field_probe}'...")
        probe_query = f'{{ {field_probe}(transaction_id: "test") {{ __typename }} }}'
        response = post(probe_query)
        print(response.text)
        return

    print("1) Verification que l introspection est activee...")
    response = post(_INTROSPECTION_ENABLED_CHECK)
    body = response.json()
    if response.status_code != 200 or "errors" in body:
        print("Introspection probablement desactivee. Reponse brute :")
        print(response.text)
        print("\nRelance avec un nom suppose, ex : python check_immoask_schema_standalone.py transactionStatus")
        return

    print("Introspection active.\n")

    print("2) Liste des requetes disponibles...")
    response = post(_LIST_QUERY_FIELDS)
    data = response.json()
    fields = [f["name"] for f in data["data"]["__schema"]["queryType"]["fields"]]
    for name in fields:
        marker = "  <-- probablement lie au paiement" if any(
            kw in name.lower() for kw in ("transaction", "payment", "paiement", "feda", "status")
        ) else ""
        print(f"  - {name}{marker}")

    print("\n3) Detail des arguments et types de retour...")
    response = post(_FIELD_DETAIL_QUERY)
    detail = response.json()
    print(json.dumps(detail, indent=2, ensure_ascii=False))

    print("\nRepere le champ qui correspond au statut de transaction ci-dessus.")


if __name__ == "__main__":
    main()