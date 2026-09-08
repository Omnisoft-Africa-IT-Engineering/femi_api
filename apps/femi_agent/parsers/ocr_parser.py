import io
import logging

import pytesseract
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)


def extract_text_from_image(image_bytes: bytes) -> str:
    """Extrait le texte brut d'une image (JPEG, PNG) via double passe OCR.

    Combine deux modes de segmentation Tesseract :
    - PSM 4 : optimisé pour les lignes de tableau (désignation/quantité/prix)
    - PSM 6 : optimisé pour les blocs de texte uniformes (en-têtes, totaux)

    Le texte combiné (avec doublons éventuels sur l'en-tête) est transmis
    tel quel au LLM en aval, qui se charge d'en extraire les champs utiles.

    Raises:
        ValueError: si l'image est invalide, illisible, ou vide.
    """
    if not image_bytes:
        raise ValueError("Impossible d'extraire le texte : image_bytes est vide.")

    try:
        image = Image.open(io.BytesIO(image_bytes))
        # PSM 6 : bloc de texte uniforme — fiable pour en-têtes et totaux
        text_blocks = pytesseract.image_to_string(
            image, lang="fra+eng", config="--psm 6"
        )
        # PSM 4 : colonnes de tailles variables — fiable pour les lignes produits
        text_table = pytesseract.image_to_string(
            image, lang="fra+eng", config="--psm 4"
        )
    except UnidentifiedImageError as e:
        logger.warning("Image illisible ou format non supporté : %s", e)
        raise ValueError("Le fichier fourni n'est pas une image valide.") from e
    except pytesseract.TesseractNotFoundError as e:
        logger.exception("Tesseract OCR introuvable sur le système.")
        raise ValueError("Le moteur OCR n'est pas disponible sur le serveur.") from e
    except pytesseract.TesseractError as e:
        logger.warning("Erreur du moteur Tesseract : %s", e)
        raise ValueError(f"Erreur lors de l'analyse OCR : {e}") from e
    except Exception as e:
        logger.exception("Erreur inattendue lors de l'extraction OCR.")
        raise ValueError(f"Erreur inattendue lors de la lecture OCR : {e}") from e

    combined = f"{text_blocks.strip()}\n\n{text_table.strip()}"
    stripped_text = combined.strip()

    if not stripped_text:
        logger.info("OCR n'a détecté aucun texte dans l'image fournie.")

    return stripped_text