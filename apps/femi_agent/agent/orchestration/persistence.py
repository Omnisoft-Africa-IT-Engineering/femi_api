"""Persistance devis (extrait de router_manager)."""
from __future__ import annotations
import logging
from typing import Tuple
from apps.femi_account.devis_service import DevisInvalideError, creer_devis
from apps.femi_account.models import Entreprise
from apps.femi_agent.schemas import QuoteExtractionResult
logger = logging.getLogger(__name__)

def persist_quote_results(quote_results: list[QuoteExtractionResult], entreprise: Entreprise) -> Tuple[list[dict], list[str]]:
    devis_crees: list[dict] = []
    erreurs: list[str] = []
    for result in quote_results:
        if result.needs_clarification:
            continue
        try:
            devis = creer_devis(
                entreprise,
                lignes=[{"description": l.description, "quantite": l.quantite, "prix_unitaire": l.prix_unitaire} for l in result.lignes],
                client_nom=result.client_nom,
            )
            devise = getattr(entreprise, "devise", None) or "FCFA"
            devis_crees.append({
                "reference": devis.reference,
                "client": devis.client.nom if devis.client else None,
                "nb_lignes": len(result.lignes),
                "total": devis.montant_total,
                "devise": {"XOF": "FCFA", "XAF": "FCFA"}.get(devise.upper(), devise),
            })
        except DevisInvalideError as exc:
            logger.warning("[persistence] Devis refusé : %s", exc)
            erreurs.append(f"Je n'ai pas pu créer le devis : {exc}")
    return devis_crees, erreurs
