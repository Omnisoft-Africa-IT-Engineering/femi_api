import os
import gspread
from google.oauth2.service_account import Credentials
from apps.femi_account.models import Operation

# Scopes requis pour l'accès à Google Sheets et Drive
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def append_operation_to_sheets(operation: Operation, spreadsheet_id_or_url: str = None):
    """
    Synchronise une opération comptable vers un tableau Google Sheets.
    """
    credentials_path = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
    
    # Vérification de la présence du fichier de clés Google
    if not os.path.exists(credentials_path):
        print(f"[GOOGLE SHEETS WARNING] Fichier de clés introuvable : {credentials_path}. Annulation de la synchronisation.")
        return False

    try:
        # Authentification via le Compte de Service
        creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        client = gspread.authorize(creds)

        # ID du Spreadsheet par défaut ou fourni dans les paramètres de l'entreprise
        sheet_id = spreadsheet_id_or_url or os.getenv('GOOGLE_SHEET_ID')
        if not sheet_id:
            print("[GOOGLE SHEETS WARNING] Aucun GOOGLE_SHEET_ID n'est configuré.")
            return False

        # Ouverture de la feuille de calcul (Feuille "Journal")
        spreadsheet = client.open_by_key(sheet_id)
        worksheet = spreadsheet.sheet1

        # Formatage de la ligne à insérer
        row = [
            str(operation.id),
            operation.entreprise.nom,
            operation.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            operation.transaction_date.strftime('%Y-%m-%d') if operation.transaction_date else '',
            operation.transaction_type,
            float(operation.amount_ttc),
            operation.currency,
            operation.category,
            operation.payment_method,
            operation.vendor_or_client or '',
            operation.description,
            operation.source
        ]

        # Ajout de la ligne en bas du tableau
        worksheet.append_row(row)
        return True

    except Exception as e:
        print(f"[GOOGLE SHEETS ERROR] Échec de la synchronisation : {e}")
        return False