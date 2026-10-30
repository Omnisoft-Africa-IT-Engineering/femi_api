"""Garde déterministe : une opération ne s'enregistre jamais sans montant.

Avant, seul le LLM comptable était censé demander le montant manquant ; quand
il oubliait (« j'ai vendu 2 sacs de riz »), l'insertion échouait en base
(NOT NULL sur amount_ttc) et l'utilisateur recevait une erreur 400.

Ici, pour toute transaction à créer sans montant valide :
- vente (RECETTE) de produits dont le prix de vente est configuré dans le
  catalogue : montant = quantité × prix de vente, indiqué dans la description ;
- sinon : needs_clarification + « amount_ttc » → Femi demande le montant.
Les remboursements (check_open_debt / check_open_loan) ont déjà leur propre
refus et ne sont pas touchés.
"""

from __future__ import annotations

import logging

from apps.femi_agent.schemas import AccountingExtractionResult

logger = logging.getLogger(__name__)


def _sans_montant(txn) -> bool:
    if getattr(txn, "check_open_debt", False) or getattr(txn, "check_open_loan", False):
        return False
    return txn.amount_ttc is None or txn.amount_ttc <= 0


def _montant_depuis_catalogue(txn, entreprise, message_text: str) -> bool:
    """Renseigne txn.amount_ttc depuis les prix de vente du catalogue si possible."""
    if txn.transaction_type != "RECETTE":
        return False
    from apps.femi_agent.agent.stock_sale_link import _fmt, detecter_lignes

    lignes = detecter_lignes(entreprise, message_text)
    if not lignes or any((l["prix_vente"] or 0) <= 0 for l in lignes):
        return False
    txn.amount_ttc = sum(l["quantite"] * l["prix_vente"] for l in lignes)
    detail = " + ".join(
        f"{_fmt(l['quantite'])} {l['nom']} × {_fmt(l['prix_vente'])}" for l in lignes
    )
    base = (txn.description or "Vente").strip()
    txn.description = f"{base} (prix catalogue : {detail})"
    return True


def completer_ou_demander_montant(
    result: AccountingExtractionResult, entreprise, message_text: str
) -> AccountingExtractionResult:
    manquantes = [t for t in result.transactions if _sans_montant(t)]
    if not manquantes:
        return result

    restantes = []
    for txn in manquantes:
        try:
            if len(result.transactions) == 1 and _montant_depuis_catalogue(
                txn, entreprise, message_text
            ):
                continue
        except Exception:
            logger.exception("[accounting_guard] prix catalogue indisponible")
        restantes.append(txn)

    if restantes:
        result.needs_clarification = True
        if "amount_ttc" not in result.missing_fields:
            result.missing_fields.append("amount_ttc")
    return result