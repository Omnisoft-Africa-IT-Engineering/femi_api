"""Messages utilisateur autonomes (devis, modify, client).

Extrait de FemiRouterManager — les résumés ACCOUNTING restent dans router_manager.
"""

from __future__ import annotations

from typing import Optional

from apps.femi_agent.agent.response_writer import write_natural_reply
from apps.femi_agent.schemas import (
    AccountingModifyProposeChangeResult,
    AccountingModifyResolutionResult,
    CustomerExtractionOutput,
)


def format_montant(amount, currency: Optional[str] = None) -> str:
    devise = currency or "FCFA"
    if amount is None:
        return f"montant non précisé ({devise})"
    formatted = f"{amount:,.0f}".replace(",", " ")
    return f"{formatted} {devise}"


def format_values_for_facts(values) -> str:
    if not values:
        return "n/a"
    return "; ".join(f"{k}: {v}" for k, v in values.items() if v is not None)


def summarize_devis(infos: dict, user_message: str = "") -> str:
    total = infos["total"]
    total_txt = (
        f"{total:,.0f}" if total == total.to_integral_value() else f"{total:,.2f}"
    ).replace(",", " ")
    montant = f"{total_txt} {infos['devise']}"
    client = infos["client"] or "le client"
    nb = infos["nb_lignes"]
    articles = "1 ligne" if nb == 1 else f"{nb} lignes"

    fallback = (
        f"✅ Devis {infos['reference']} créé pour {client} : {articles}, "
        f"total {montant}. Il est en brouillon ; tu peux le télécharger "
        f"en PDF depuis l'application."
    )
    facts = (
        "Devis créé avec succès (statut brouillon).\n"
        f"- référence: {infos['reference']}\n"
        f"- client: {client}\n"
        f"- nombre de lignes: {nb}\n"
        f"- total: {montant}\n"
        "- le PDF se télécharge depuis l'application, section Devis"
    )
    texte = write_natural_reply(
        facts=facts,
        reply_type="quote_success",
        user_message=user_message,
        fallback=fallback,
    )
    # L'application détecte la référence pour afficher le bouton « Ouvrir le
    # devis » : on garantit qu'elle figure toujours dans la réponse, même si
    # la reformulation du modèle l'a omise.
    if infos["reference"] not in texte:
        texte = f"{texte}\nRéférence : {infos['reference']}"
    return texte


def summarize_customer_payment(result: CustomerExtractionOutput) -> str:
    montant = format_montant(result.amount_ttc, result.currency)
    contact = result.contact or "ce contact"
    return (
        f"Je n'ai rien enregistré pour ce paiement de {montant}, car je "
        f"ne sais pas dans quel sens il va. Dis-moi par exemple « {contact} "
        f"m'a payé {montant} » (un client te règle) ou « j'ai réglé "
        f"{montant} de ma dette chez {contact} » (tu règles un fournisseur)."
    )


def summarize_propose_change(
    result: AccountingModifyProposeChangeResult,
    user_message: str = "",
) -> str:
    if result.action_type == "DELETE":
        fallback = (
            f"🗑️ Je propose de supprimer cette opération : {result.current_values}. "
            "Confirmes-tu ?"
        )
        facts = (
            "Proposition de SUPPRESSION d'une opération, en attente de "
            "confirmation de l'utilisateur (rien n'est encore supprimé).\n"
            f"Opération concernée : {format_values_for_facts(result.current_values)}"
        )
    else:
        fallback = (
            f"✏️ Je propose de modifier cette opération : {result.proposed_values}. "
            "Confirmes-tu ?"
        )
        facts = (
            "Proposition de MODIFICATION d'une opération, en attente de "
            "confirmation de l'utilisateur (rien n'est encore modifié).\n"
            f"Valeurs actuelles : {format_values_for_facts(result.current_values)}\n"
            f"Nouvelles valeurs proposées : {format_values_for_facts(result.proposed_values)}"
        )
    return write_natural_reply(
        facts=facts,
        reply_type="modify_propose",
        user_message=user_message,
        fallback=fallback,
    )


def summarize_candidates(
    result: AccountingModifyResolutionResult,
    user_message: str = "",
) -> str:
    if not result.candidates:
        return "Je n'ai trouvé aucune opération correspondante."
    candidats = "\n".join(f"- {c.summary}" for c in result.candidates)
    fallback = (
        "Plusieurs opérations correspondent, laquelle veux-tu "
        f"modifier/supprimer ?\n{candidats}"
    )
    facts = (
        "Plusieurs opérations correspondent à la demande de "
        f"{'suppression' if result.action_type == 'DELETE' else 'modification'}. "
        "L'utilisateur doit dire laquelle.\nCandidates :\n" + candidats
    )
    return write_natural_reply(
        facts=facts,
        reply_type="modify_candidates",
        user_message=user_message,
        fallback=fallback,
    )