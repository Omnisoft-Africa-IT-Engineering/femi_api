import pytesseract
from PIL import Image
import io

def extract_text_from_image(image_bytes: bytes) -> str:
    """Extrait le texte brut d'un fichier image (JPEG, PNG)."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        # Utilisation de Tesseract en français et anglais
        extracted_text = pytesseract.image_to_string(image, lang='fra+eng')
        return extracted_text.strip()
    except Exception as e:
        raise ValueError(f"Erreur lors de la lecture OCR de l'image : {str(e)}")