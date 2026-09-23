import os
import django
import firebase_admin
from firebase_admin import credentials, messaging

# Définition du dossier où se trouve test_fcm.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Chemin vers le fichier JSON de ta clé Firebase
CREDENTIALS_PATH = os.path.join(BASE_DIR, 'firebase-credentials.json')

# Token FCM de ton Infinix
FCM_TOKEN = "fc6OcTW9QXi4l7ZDKpVJsc:APA91bG_A0vFppgM0fIKPFbIBghJv3jHMl0F_eDCMzwszubXUQeJQAKESnLXHLrm3GIBTqZ9UveOmJHWn6lMu9y6uCO8ImsOiq_rrTYZL5GcVJwfSlUr0j4"

def send_push_notification():
    # Initialisation du SDK Firebase Admin
    if not firebase_admin._apps:
        cred = credentials.Certificate(CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)

    # Structuration du message de notification
    message = messaging.Message(
        notification=messaging.Notification(
            title="Test Femi App 🚀",
            body="Félicitations ! La notification push depuis Django fonctionne parfaitement sur ton Infinix.",
        ),
        data={
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "test_notification",
        },
        token=FCM_TOKEN,
    )

    # Envoi du message
    try:
        response = messaging.send(message)
        print(f"✅ Notification envoyée avec succès ! Message ID: {response}")
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi de la notification: {e}")

if __name__ == "__main__":
    send_push_notification()