"""Photo chat → preview stock (formulaire) sans écrire en base."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MOTS_STOCK = (
    "stock", "inventaire", "produit", "article", "réappro", "reappro",
    "entrée", "entree", "sortie stock", "rayon", "étiquette", "etiquette",
    "bon de livraison", "colis", "carton", "sku", "code-barres", "code barres",
)


def _texte_force_stock(message_text: Optional[str]) -> bool:
    t = (message_text or "").casefold()
    return any(m in t for m in _MOTS_STOCK)


def build_stock_photo_response(
    image_bytes: bytes,
    *,
    entreprise=None,
    message_text: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Si la photo ressemble à du stock, renvoie le payload UI formulaire.

    Sinon None → le pipeline compta / router continue normalement.
    """
    try:
        from apps.femi_agent.agent.stock_ocr_executor import (
            StockOcrExecutionError,
            StockOcrExecutor,
        )
        from apps.femi_agent.parsers.document_loader import UnsupportedDocumentError
    except Exception:
        logger.exception("[stock_chat_photo] import OCR stock impossible")
        return None

    force = _texte_force_stock(message_text)

    try:
        result = StockOcrExecutor.execute(image_bytes)
    except UnsupportedDocumentError:
        return None
    except StockOcrExecutionError:
        logger.info("[stock_chat_photo] OCR stock en échec")
        return None
    except Exception:
        logger.exception("[stock_chat_photo] OCR stock exception")
        return None

    data = result.model_dump()
    lignes = data.get("lignes") or []
    type_s = data.get("type_suggere") or "INCONNU"
    conf = data.get("confidence") or "low"

    if not lignes:
        return None
    if not force and type_s == "INCONNU" and conf == "low":
        return None
    if not force and conf == "low" and type_s == "INCONNU":
        return None

    # Enrichir avec produits existants
    if entreprise is not None:
        try:
            from apps.femi_account.models import Produit

            for ligne in lignes:
                nom = (ligne.get("nom") or "").strip()
                sku = (ligne.get("sku") or "").strip()
                existant = None
                if sku:
                    existant = Produit.objects.filter(
                        entreprise=entreprise, sku__iexact=sku, actif=True
                    ).first()
                if existant is None and nom:
                    existant = Produit.objects.filter(
                        entreprise=entreprise, nom__iexact=nom, actif=True
                    ).first()
                ligne["produit_existant_id"] = str(existant.pk) if existant else None
                ligne["produit_existant_nom"] = existant.nom if existant else None
                ligne["stock_actuel"] = (
                    float(existant.stock_actuel) if existant else None
                )
        except Exception:
            logger.exception("[stock_chat_photo] enrichissement produit")

    premiere = lignes[0]
    nom = premiere.get("nom") or "article"
    message = (
        f"J'ai lu « {nom} » sur la photo. "
        "Complète le formulaire pour enregistrer dans le stock."
    )
    if len(lignes) > 1:
        message = (
            f"J'ai repéré {len(lignes)} articles. "
            "Je préremplis avec le premier — tu pourras ajouter les autres ensuite."
        )

    return {
        "success": True,
        "message": message,
        "ui_action": "open_stock_form",
        "stock_preview": {
            "type_suggere": type_s,
            "confidence": conf,
            "note": data.get("note"),
            "lignes": lignes,
            # Raccourci 1re ligne pour le form mobile
            "form": premiere,
        },
    }
