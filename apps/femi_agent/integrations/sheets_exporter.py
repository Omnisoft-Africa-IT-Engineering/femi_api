import os
import gspread
from google.oauth2.service_account import Credentials

# Scopes requis pour l'accès à Google Sheets et Drive
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

class GoogleSheetsExporter:
    """Exportation des transactions enregistrées vers Google Sheets."""

    @classmethod
    def append_operation(cls, operation):
        """
        Ajoute une ligne représentant l'opération dans le Google Sheet configuré.
        """
        json_creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE", "credentials.json")
        spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")

        # Si les identifiants ou l'ID de la feuille ne sont pas configurés, on annule proprement
        if not os.path.exists(json_creds_path) or not spreadsheet_id:
            print("[SIMULATION GOOGLE SHEETS] : Identifiants non configurés, enregistrement ignoré.")
            return False

        try:
            creds = Credentials.from_service_account_file(json_creds_path, scopes=SCOPES)
            client = gspread.authorize(creds)
            sheet = client.open_by_key(spreadsheet_id).sheet1

            # Ligne de données à ajouter
            row = [
                str(operation.id),
                operation.transaction_date.strftime("%Y-%m-%d") if operation.transaction_date else "",
                operation.transaction_type,
                float(operation.amount_ttc),
                operation.currency,
                operation.category,
                operation.payment_method,
                operation.vendor_or_client or "",
                operation.description,
                operation.source,
                operation.created_at.strftime("%Y-%m-%d %H:%M:%S")
            ]

            sheet.append_row(row)
            return True
        except Exception as e:
            print(f"[ERREUR GOOGLE SHEETS] : {str(e)}")
            return False