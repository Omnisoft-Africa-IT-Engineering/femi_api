from datetime import date
from .models import EcheanceFiscale


def generer_echeances_otr(entreprise, regime="TPU", annee=2026):
    """Génère le calendrier fiscal adaptatif OTR Togo dans femi_account.

    regime: 'TPU' (4 acomptes) ou 'REEL' (TVA mensuelle + liasse avril)
    """
    echeances = []

    # 1. Acomptes trimestriels TPU/IS/Patente (31 Jan, 31 Mai, 31 Jul, 31 Oct)
    dates_acomptes = [
        (date(annee, 1, 31), f"1er Acompte TPU/Patente {annee}"),
        (date(annee, 5, 31), f"2ème Acompte TPU/Patente {annee}"),
        (date(annee, 7, 31), f"3ème Acompte TPU/Patente {annee}"),
        (date(annee, 10, 31), f"4ème Acompte TPU/Patente {annee}"),
    ]
    for d, lib in dates_acomptes:
        echeances.append(
            EcheanceFiscale(
                entreprise=entreprise,
                type_echeance="TPU_ACOMPTE",
                libelle=lib,
                date_echeance=d,
            )
        )

    # 2. Régime Réel : TVA mensuelle (le 15) + Liasse SYSCOHADA (30 Avril)
    if regime.upper() == "REEL":
        for mois in range(1, 13):
            echeances.append(
                EcheanceFiscale(
                    entreprise=entreprise,
                    type_echeance="TVA",
                    libelle=f"Déclaration TVA - Mois {mois:02d}/{annee}",
                    date_echeance=date(annee, mois, 15),
                )
            )
        echeances.append(
            EcheanceFiscale(
                entreprise=entreprise,
                type_echeance="LIASSE_ANNUELLE",
                libelle=f"Liasse Fiscale SYSCOHADA {annee-1}",
                date_echeance=date(annee, 4, 30),
            )
        )
    else:
        # Régime TPU : Déclaration simplifiée de résultat
        echeances.append(
            EcheanceFiscale(
                entreprise=entreprise,
                type_echeance="LIASSE_ANNUELLE",
                libelle=f"Déclaration Annuelle TPU {annee-1}",
                date_echeance=date(annee, 3, 31),
            )
        )

    return EcheanceFiscale.objects.bulk_create(echeances)