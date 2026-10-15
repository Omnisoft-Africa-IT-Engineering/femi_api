"""Réponses sociales / erreur / hors-sujet + clarifications."""
from __future__ import annotations
from typing import Optional
from django.utils import timezone
from apps.femi_agent.agent.ocr_postprocess import extract_control_anomalies
from apps.femi_agent.agent.response_writer import write_natural_reply
from apps.femi_agent.agent.social_replies import (
    build_off_topic_reply,
    build_social_reply,
    classify_social_message,
    social_reply_context,
)
from apps.femi_agent.agent.orchestration.messages import format_montant
from apps.femi_agent.schemas import RouterProcessResult

_SHORT_REPLY_TIMEOUT = 25

MISSING_FIELD_QUESTIONS = {
    "transaction_type": "Dis-moi, c'est une vente que tu as faite, ou un achat pour ton activité ? (Si c'est un prêt, précise-le-moi.)",
    "amount_ttc": "Quel est le montant total, s'il te plaît ?",
    "nature_creance": "C'est le remboursement d'une vente à crédit, ou d'un prêt que tu avais accordé ?",
    "statut_paiement": "Est-ce que c'est déjà réglé en totalité, ou est-ce qu'il reste une partie à payer ? S'il y a eu une avance, dis-moi combien.",
    "contact_disambiguation": "J'ai plusieurs personnes avec ce nom. C'est laquelle ? Donne-moi son nom complet.",
    "target_data": "Quelle opération veux-tu modifier ou supprimer ? Donne-moi le montant, la date ou le nom du contact.",
    "search_criteria": "Quelle opération cherches-tu ? Donne-moi le montant, la date ou le nom du contact.",
    "operation_not_found": "Je ne retrouve pas cette opération. Peux-tu donner le montant, la date ou le nom du contact ?",
    "new_value": "Quelle est la nouvelle valeur à enregistrer ?",
    "category_not_available": "Cette catégorie n'existe pas dans ton compte. Peux-tu en choisir une existante ?",
    "payment_method_invalid": "Quel est le mode de paiement : espèces, mobile money, virement, chèque ou carte ?",
    "transaction_type_reclassification_not_supported": (
        "Je ne peux pas changer le type d'une opération déjà enregistrée. "
        "Annule-la puis enregistre-la de nouveau avec le bon type."
    ),
    "indicator": "Quel indicateur veux-tu : chiffre d'affaires, dépenses, bénéfice, marge ou trésorerie ?",
    "indicateur": "Quel indicateur veux-tu : chiffre d'affaires, dépenses, bénéfice, marge ou trésorerie ?",
    "date_range_incomplete": "Pour quelle période exactement ? (ex : ce mois-ci, septembre, du 1er au 15)",
    "periode": "Pour quelle période ? (ex : aujourd'hui, ce mois-ci, le mois dernier, cette année)",
    "période": "Pour quelle période ? (ex : aujourd'hui, ce mois-ci, le mois dernier, cette année)",
    "period": "Pour quelle période ? (ex : aujourd'hui, ce mois-ci, le mois dernier, cette année)",
    "annee": "Pour quelle année ?",
    "wrong_agent": "Peux-tu reformuler ta demande en précisant ce que tu veux faire ?",
    "search_error": "Je n'ai pas réussi à retrouver cette information. Peux-tu reformuler ?",
    "client_name": "C'est pour quel client ce devis ?",
    "quote_items": "Quels articles ou services mettre sur le devis (quantités et prix) ?",
    "quote_price": "Quel est le prix unitaire de chaque article du devis ?",
}
CLARIFICATION_SKIP_CODES = {"fonctionnalite_non_disponible", "operation_disambiguation"}


def is_pure_greeting(text: str) -> bool:
    return classify_social_message(text) is not None


def greeting_result(text: str = "") -> RouterProcessResult:
    hour = timezone.localtime().hour
    reply_type, facts = social_reply_context(text, hour)
    return RouterProcessResult(
        success=True,
        message=write_natural_reply(
            facts=facts,
            reply_type=reply_type,
            user_message=text,
            fallback=build_social_reply(text, hour),
            timeout=_SHORT_REPLY_TIMEOUT,
        ),
    )


def natural_notice(facts: str, fallback: str, user_message: str = "") -> str:
    return write_natural_reply(
        facts=facts,
        reply_type="error_user_action",
        user_message=user_message,
        fallback=fallback,
        timeout=_SHORT_REPLY_TIMEOUT,
    )


def natural_off_topic(user_message: str = "") -> str:
    return write_natural_reply(
        facts=(
            "Le message de l'utilisateur n'a pas pu être rattaché à une "
            "action de Femi (hors sujet ou pas compris).\n"
            "Ce que Femi sait faire : enregistrer ventes, dépenses et "
            "prêts ; suivre les clients qui doivent de l'argent ; donner "
            "chiffre d'affaires, dépenses, bénéfice, trésorerie ; "
            "modifier ou annuler une opération."
        ),
        reply_type="off_topic",
        user_message=user_message,
        fallback=build_off_topic_reply(),
        timeout=_SHORT_REPLY_TIMEOUT,
    )


def build_understood_recap(accounting_results) -> Optional[str]:
    for result in accounting_results or []:
        if not getattr(result, "needs_clarification", False):
            continue
        for txn in getattr(result, "transactions", []) or []:
            if not txn.amount_ttc:
                continue
            montant = format_montant(txn.amount_ttc, txn.currency)
            contact = f" ({txn.contact})" if txn.contact else ""
            avance = getattr(txn, "montant_deja_paye", None)
            if avance:
                return (
                    f"J'ai bien lu ta facture de {montant}{contact} "
                    f"et je vois une avance de {format_montant(avance, txn.currency)}."
                )
            return f"J'ai bien lu ton document : {montant}{contact}."
    return None


def build_static_clarification_message(missing_fields, accounting_results=None) -> Optional[str]:
    questions: list[str] = []
    for code in missing_fields or []:
        if code in CLARIFICATION_SKIP_CODES:
            continue
        q = MISSING_FIELD_QUESTIONS.get(code)
        if q and q not in questions:
            questions.append(q)
    if not questions:
        return None
    recap = build_understood_recap(accounting_results)
    liste = "\n".join(f"• {q}" for q in questions[:3])
    intro = (
        f"{recap} Deux ou trois précisions et j'enregistre :"
        if recap
        else "Pour bien l'enregistrer, j'ai juste besoin de quelques précisions :"
    )
    return f"{intro}\n{liste}"


def build_clarification_message(
    missing_fields: list[str],
    accounting_results=None,
    user_message: str = "",
) -> Optional[str]:
    static = build_static_clarification_message(missing_fields, accounting_results)
    if static is None:
        return None
    questions: list[str] = []
    for code in missing_fields:
        if code in CLARIFICATION_SKIP_CODES:
            continue
        q = MISSING_FIELD_QUESTIONS.get(code)
        if q and q not in questions:
            questions.append(q)
    fact_lines = ["Informations à demander à l'utilisateur :"]
    if questions:
        fact_lines.extend(f"- {q}" for q in questions[:3])
    else:
        fact_lines.append("- Une précision générale : demande-lui d'en dire un peu plus.")
    recap = build_understood_recap(accounting_results)
    if recap:
        fact_lines.append(f"Ce que Femi a déjà compris : {recap}")
    anomalies = extract_control_anomalies(user_message)
    if anomalies:
        fact_lines.append(
            "Incohérences lues sur le document scanné (rien n'a été corrigé ni enregistré) :"
        )
        fact_lines.extend(f"- {a}" for a in anomalies)
    codes = [c for c in missing_fields if c not in CLARIFICATION_SKIP_CODES]
    if "quote_items" in codes and "client_name" not in codes:
        name = (user_message or "").strip()
        if name and len(name) <= 80 and not any(
            w in name.lower()
            for w in ("devis", "article", "service", "prix", "quantit", "fcfa", " xof")
        ):
            return (
                f"Client noté : {name}.\n"
                "Quels articles ou services mettre sur le devis "
                "(avec quantités et prix) ?"
            )
    if "quote_items" in codes or "client_name" in codes:
        fact_lines.append(
            "Règle devis : un nom seul (ex. Mr Hola) est le CLIENT du devis, "
            "pas l'interlocuteur. Ne le salue jamais. Dis « Client noté : … » "
            "puis demande les articles."
        )
    return write_natural_reply(
        facts="\n".join(fact_lines),
        reply_type="clarification",
        user_message=user_message,
        fallback=static,
    )
