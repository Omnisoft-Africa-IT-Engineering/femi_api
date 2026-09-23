from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import EcheanceFiscale, Entreprise
from .notifications import PALIERS_JOURS, notifier, palier_pour
from .services import generer_echeances_otr

STATUTS_OUVERTS = ["EN_ATTENTE", "RAPPELE"]

# Paliers configurés à 10 jours, 5 jours et 1 jour (la veille)
PALIERS_JOURS = [10, 5, 1]

# Une échéance dépassée depuis plus longtemps que ça n'envoie pas de
# notification "en retard" (évite une rafale sur les données anciennes
# la première fois que la tâche tourne).
FENETRE_RETARD_JOURS = 7


def _utilisateurs_actifs(echeance):
    return [u for u in echeance.entreprise.utilisateurs.all() if u.is_active]


def _textes_rappel(echeance, jours: int):
    if jours == 1:
        titre = "⚠️ Urgent : Demain dernière limite !"
    elif jours == 5:
        titre = "Échéance proche (J-5)"
    elif jours == 10:
        titre = "Rappel fiscal (J-10)"
    else:
        titre = f"Rappel d'échéance ({jours} jours)"
        
    message = f"{echeance.libelle} — date limite : {echeance.date_echeance:%d/%m/%Y}."
    return titre, message


@shared_task
def envoyer_rappels_echeances_fiscales():
    """
    Tâche quotidienne (7h) :
    - Rappels à J-10, J-5 et J-1 pour les échéances non payées :
      notification dans l'app + push. Chaque palier n'est envoyé qu'une
      fois par utilisateur et par échéance (contrainte d'unicité).
    - Passe en RAPPELE les échéances EN_ATTENTE entrées dans la fenêtre
      de 10 jours (le <= rattrape les jours où la tâche n'a pas tourné).
    - Notifie puis passe en EN_RETARD les échéances dépassées.
    """
    aujourdhui = timezone.localdate()
    maintenant = timezone.now()
    limite = aujourdhui + timedelta(days=max(PALIERS_JOURS))

    # --- Rappels avant échéance ---
    a_rappeler = (
        EcheanceFiscale.objects.filter(
            statut__in=STATUTS_OUVERTS,
            date_echeance__gte=aujourdhui,
            date_echeance__lte=limite,
        )
        .select_related("entreprise")
        .prefetch_related("entreprise__utilisateurs")
    )

    ids_fenetre, ids_notifies = [], []
    nb_notifs = 0
    for echeance in a_rappeler:
        jours = (echeance.date_echeance - aujourdhui).days
        palier = palier_pour(jours)
        
        # On ne traite que si le nombre de jours correspond exactement à un de nos paliers
        if jours not in PALIERS_JOURS:
            continue

        titre, message = _textes_rappel(echeance, jours)

        nouvelle = False
        for utilisateur in _utilisateurs_actifs(echeance):
            _, cree = notifier(utilisateur, titre, message, echeance, palier)
            if cree:
                nb_notifs += 1
                nouvelle = True

        ids_fenetre.append(echeance.id)
        if nouvelle:
            ids_notifies.append(echeance.id)

    EcheanceFiscale.objects.filter(id__in=ids_fenetre, statut="EN_ATTENTE").update(
        statut="RAPPELE"
    )
    EcheanceFiscale.objects.filter(
        id__in=ids_notifies, statut__in=STATUTS_OUVERTS
    ).update(dernier_rappel_envoye=maintenant)

    # --- Échéances dépassées ---
    en_retard = EcheanceFiscale.objects.filter(
        statut__in=STATUTS_OUVERTS,
        date_echeance__lt=aujourdhui,
    )

    recentes = (
        en_retard.filter(
            date_echeance__gte=aujourdhui - timedelta(days=FENETRE_RETARD_JOURS)
        )
        .select_related("entreprise")
        .prefetch_related("entreprise__utilisateurs")
    )
    for echeance in recentes:
        message = (
            f"{echeance.libelle} était due le {echeance.date_echeance:%d/%m/%Y}. "
            "Pensez à la régulariser."
        )
        for utilisateur in _utilisateurs_actifs(echeance):
            notifier(utilisateur, "Échéance dépassée", message, echeance, "RETARD")

    nb_retard = en_retard.update(statut="EN_RETARD")

    return f"Traité : {nb_notifs} notification(s) de rappel, {nb_retard} en retard"


@shared_task
def generer_echeances_annuelles_toutes_entreprises():
    """Génère les échéances de l'année en cours pour toutes les entreprises."""
    annee = timezone.localdate().year
    count = 0

    for entreprise in Entreprise.objects.all():
        generer_echeances_otr(entreprise=entreprise, annee=annee)
        count += 1

    return f"Échéances {annee} générées pour {count} entreprise(s)."