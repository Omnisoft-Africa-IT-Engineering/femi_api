"""
Inspecte les champs d un TYPE GraphQL precis (pas une requete) sur
l API ImmoAsk.

Usage :
    python check_type_fields.py Payment_transaction
"""

import json
import sys

import requests

IMMOASK_GRAPHQL_URL = "https://immoaskprodapi.omnisoft.africa/api/v2"

QUERY = """
query TypeFields($typeName: String!) {
  __type(name: $typeName) {
    name
    fields {
      name
      type {
        name
        kind
        ofType { name kind }
      }
    }
  }
}
"""


def main():
    if len(sys.argv) < 2:
        print("Usage : python check_type_fields.py <NomDuType>")
        return

    type_name = sys.argv[1]
    response = requests.post(
        IMMOASK_GRAPHQL_URL,
        json={"query": QUERY, "variables": {"typeName": type_name}},
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()