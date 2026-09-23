"""
apps/femi_account/notifications.py

Création des notifications in-app et envoi des push FCM.

Prérequis push (optionnel : sans lui, seul le centre de notifications
de l'app fonctionne) :
  pip install firebase-admin
  settings.py -> FIREBASE_CREDENTIALS_PATH = config("FIREBASE_CREDENTIALS_PATH", default="")
  (chemin vers le JSON de compte de service Firebase, hors du dépôt git)
"""

import logging

from django.conf import settings

from .models import AppareilNotification, Notification

logger = logging.getLogger(__name__)

# Jours avant l'échéance où un rappel est envoyé (10 jours, 5 jours, la veille et le jour J).
PALIERS_JOURS = (10, 5, 1, 0)

_firebase_pret = None  # None = pas encore tenté, True/False = résultat


def palier_pour(jours_restants: int) -> str:
    """
    Palier courant pour une échéance dans `jours_restants` jours (0 à 10).
    Le plus petit palier >= jours_restants : ex: 6 jours -> J10, 3 jours -> J5.
    Si la tâche n'a pas tourné un jour, on n'envoie que le palier courant
    (pas de rafale de rappels manqués).
    """
    for p in sorted(PALIERS_JOURS):
        if jours_restants <= p:
            return f"J{p}"
    return f"J{max(PALIERS_JOURS)}"


def _init_firebase() -> bool:
    global _firebase_pret
    if _firebase_pret is not None:
        return _firebase_pret

    chemin = getattr(settings, "FIREBASE_CREDENTIALS_PATH", "")
    if not chemin:
        logger.warning("FIREBASE_CREDENTIALS_PATH non défini : push désactivés.")
        _firebase_pret = False
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials

        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app(credentials.Certificate(chemin))
        _firebase_pret = True
    except Exception:
        logger.exception("Initialisation Firebase impossible")
        _firebase_pret = False
    return _firebase_pret


def envoyer_push(utilisateur, titre: str, message: str, data: dict | None = None) -> int:
    """Envoie un push à tous les appareils de l'utilisateur. Retourne le nb de succès."""
    if not _init_firebase():
        return 0

    from firebase_admin import messaging

    tokens = list(utilisateur.appareils.values_list("token", flat=True))
    if not tokens:
        return 0

    msg = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=titre, body=message),
        # FCM exige des valeurs de type chaîne dans `data`.
        data={k: str(v) for k, v in (data or {}).items()},
    )
    reponse = messaging.send_each_for_multicast(msg)

    # Nettoie les tokens morts (app désinstallée, token expiré).
    invalides = [
        token
        for token, r in zip(tokens, reponse.responses)
        if not r.success
        and isinstance(
            r.exception,
            (messaging.UnregisteredError, messaging.SenderIdMismatchError),
        )
    ]
    if invalides:
        AppareilNotification.objects.filter(token__in=invalides).delete()

    return reponse.success_count


def notifier(utilisateur, titre: str, message: str, echeance, palier: str):
    """
    Crée la notification d'échéance (idempotent grâce à la contrainte
    utilisateur + échéance + palier), puis envoie le push seulement si
    elle vient d'être créée. Retourne (notification, cree).
    Réservé aux notifications liées à une échéance.
    """
    notif, cree = Notification.objects.get_or_create(
        utilisateur=utilisateur,
        echeance=echeance,
        palier=palier,
        defaults={"titre": titre, "message": message},
    )
    if cree:
        try:
            envoyer_push(
                utilisateur,
                titre,
                message,
                {
                    "type": "echeance",
                    "notification_id": notif.id,
                    "echeance_id": echeance.id,
                },
            )
        except Exception:
            # Un échec de push ne doit jamais bloquer la tâche ni la
            # notification in-app, déjà enregistrée.
            logger.exception("Échec de l'envoi du push")
    return notif, cree