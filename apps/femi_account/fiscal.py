from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import EcheanceFiscale, Operation


def creer_ou_mettre_a_jour_echeance_tva(operation: Operation):
    """
    Crée ou met à jour l'échéance TVA correspondant à la période
    fiscale de l'opération.

    Plusieurs opérations du même mois alimentent la même échéance.
    """

    if not operation.tax_amount or operation.tax_amount <= 0:
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
            # Date provisoire : à remplacer par la règle OTR validée.
            annee = operation.transaction_date.year
            mois = operation.transaction_date.month

            if mois == 12:
                mois_suivant = date(annee + 1, 1, 1)
            else:
                mois_suivant = date(annee, mois + 1, 1)

            # Exemple : échéance fixée au 15 du mois suivant.
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
                montant_taxe=Decimal("0.00"),
                statut="EN_ATTENTE",
            )

        # Évite de compter deux fois la même opération.
        if not echeance.operations.filter(pk=operation.pk).exists():
            echeance.montant_taxe += operation.tax_amount
            echeance.operations.add(operation)
            echeance.save(update_fields=["montant_taxe"])

        return echeance