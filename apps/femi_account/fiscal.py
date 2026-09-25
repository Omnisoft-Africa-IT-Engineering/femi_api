from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import EcheanceFiscale, Operation


def creer_ou_mettre_a_jour_echeance_tva(operation: Operation):
    """
    Crée ou récupère l'échéance TVA correspondant au mois
    de l'opération et rattache l'opération à cette échéance.

    Les montants fiscaux ne sont pas nécessaires pour cette étape.
    """

    if not operation.transaction_date:
        return None

    periode = operation.transaction_date.strftime("%Y-%m")

    with transaction.atomic():
        echeance = (
            EcheanceFiscale.objects
            .select_for_update()
            .filter(
                entreprise=operation.entreprise,
                type_echeance="TVA",
                periode=periode,
            )
            .first()
        )

        if echeance is None:
            annee = operation.transaction_date.year
            mois = operation.transaction_date.month

            if mois == 12:
                mois_suivant = date(annee + 1, 1, 1)
            else:
                mois_suivant = date(annee, mois + 1, 1)

            # Date provisoire à valider avec les règles OTR.
            date_echeance = date(
                mois_suivant.year,
                mois_suivant.month,
                15,
            )

            echeance = EcheanceFiscale.objects.create(
                entreprise=operation.entreprise,
                type_echeance="TVA",
                libelle=f"Déclaration TVA - {periode}",
                date_echeance=date_echeance,
                periode=periode,
                montant_taxe=0,
                statut="EN_ATTENTE",
            )

        # Rattacher l'opération à l'échéance
        echeance.operations.add(operation)

        return echeance