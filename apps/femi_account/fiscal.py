from datetime import date

from django.db import transaction

from .models import EcheanceFiscale, Operation


def creer_ou_mettre_a_jour_echeance_tva(operation: Operation):
    """
    Crée une NOUVELLE échéance TVA pour chaque opération/facture.

    Même si une échéance TVA existe déjà pour cette entreprise
    et cette période, une nouvelle échéance est créée.
    """

    if not operation.transaction_date:
        return None

    periode = operation.transaction_date.strftime("%Y-%m")

    with transaction.atomic():

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

        # TOUJOURS créer une nouvelle échéance
        echeance = EcheanceFiscale.objects.create(
            entreprise=operation.entreprise,
            type_echeance="TVA",
            libelle=f"Déclaration TVA - {periode}",
            date_echeance=date_echeance,
            periode=periode,
            montant_taxe=0,
            statut="EN_ATTENTE",
        )

        # Rattacher cette facture/opération à la nouvelle échéance
        echeance.operations.add(operation)

        return echeance