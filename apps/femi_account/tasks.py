from datetime import date, timedelta
from celery import shared_task
from django.utils import timezone
from .models import EcheanceFiscale


@shared_task
def envoyer_rappels_echeances_fiscales():
  aujourdhui = date.today()
  target_date = aujourdhui + timedelta(days=7)

  echeances = EcheanceFiscale.objects.filter(
      statut='EN_ATTENTE', date_echeance=target_date
  )

  for echeance in echeances:
    # Insérer ici ton helper WhatsApp (ex: send_whatsapp_message)
    print(
        f'Rappel envoyé pour {echeance.entreprise} - {echeance.libelle} (J-7)'
    )
    echeance.statut = 'RAPPELE'
    echeance.dernier_rappel_envoye = timezone.now()
    echeance.save()

  # Passe en 'EN_RETARD' les dates dépassées non payées
  EcheanceFiscale.objects.filter(
      statut__in=['EN_ATTENTE', 'RAPPELE'], date_echeance__lt=aujourdhui
  ).update(statut='EN_RETARD')