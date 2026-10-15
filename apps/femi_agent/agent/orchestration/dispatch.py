"""Dispatch : un intent Router → exécuteur d'agent.

Extrait de FemiRouterManager._dispatch_intent / _adispatch_intent
(ARCHITECTURE.md Phase 4). Signatures identiques aux appels d'origine,
plus history_text optionnel pour le contexte multi-tour.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from apps.femi_agent.agent.accounting_executor import AccountingExecutor
from apps.femi_agent.agent.accounting_modify_executor import AccountingModifyExecutor
from apps.femi_agent.agent.conversation_manager import build_agent_context
from apps.femi_agent.agent.customer_executor import CustomerExecutor
from apps.femi_agent.agent.financial_analyst_executor import FinancialAnalystExecutor
from apps.femi_agent.agent.quote_executor import QuoteExecutor
from apps.femi_agent.schemas import (
    AccountingExtractionResult,
    AccountingModifyProposeChangeResult,
    AccountingModifyResolutionResult,
    AccountingModifySearchResult,
    FinalAnswerOutput,
    QuoteExtractionResult,
    RouterIntent,
    ToolSelectionOutput,
)

logger = logging.getLogger(__name__)

DispatchResult = Optional[
    AccountingExtractionResult
    | ToolSelectionOutput
    | FinalAnswerOutput
    | AccountingModifySearchResult
    | AccountingModifyResolutionResult
    | AccountingModifyProposeChangeResult
    | QuoteExtractionResult
]

# Agents qui ont besoin de l'historique pour résoudre « cet », « la dernière », etc.
_AGENTS_NEED_HISTORY = {
    "ACCOUNTING_MODIFY",
    "QUOTE",
    "CUSTOMER",
    "ACCOUNTING",
    "FINANCIAL_ANALYST",
}


def _segment_for_agent(
    agent: str,
    segment: str,
    history_text: Optional[str],
) -> str:
    if agent not in _AGENTS_NEED_HISTORY:
        return segment
    # Messages déjà longs (OCR) : ne pas gonfler inutilement
    if segment and len(segment) > 1500:
        return segment
    return build_agent_context(history_text, segment)


def dispatch_intent(
    intent: RouterIntent,
    entreprise,
    from_document: bool = False,
    segment: Optional[str] = None,
    history_text: Optional[str] = None,
) -> Tuple[DispatchResult, bool]:
    """Équivalent sync de FemiRouterManager._dispatch_intent."""
    text = segment or intent.raw_segment
    text = _segment_for_agent(intent.agent, text, history_text)

    logger.info(
        "[dispatch] intent: agent=%s | segment=%s",
        intent.agent,
        (text or "")[:200],
    )

    if intent.agent == "ACCOUNTING":
        result = AccountingExecutor.execute(
            text,
            entreprise,
            from_document=from_document,
        )
        logger.info("[dispatch] AccountingExecutor résultat=%s", result)
        logger.info(
            "[dispatch] Accounting needs_clarification=%s",
            getattr(result, "needs_clarification", None),
        )
        return result, result.needs_clarification

    if intent.agent == "FINANCIAL_ANALYST":
        result = FinancialAnalystExecutor.execute(text, entreprise)
        return result, getattr(result, "needs_clarification", False)

    if intent.agent == "ACCOUNTING_MODIFY":
        result = AccountingModifyExecutor.execute(text, entreprise)
        return result, result.needs_clarification

    if intent.agent == "CUSTOMER":
        result = CustomerExecutor.execute(text, entreprise)
        return result, getattr(result, "needs_clarification", False)

    if intent.agent == "QUOTE":
        result = QuoteExecutor.execute(text, entreprise)
        return result, result.needs_clarification

    logger.warning(
        "[dispatch] Agent '%s' pas encore implémenté (intent_id=%s).",
        intent.agent,
        intent.intent_id,
    )
    return None, True


async def adispatch_intent(
    intent: RouterIntent,
    entreprise,
    from_document: bool = False,
    segment: Optional[str] = None,
    history_text: Optional[str] = None,
) -> Tuple[DispatchResult, bool]:
    """Équivalent async de FemiRouterManager._adispatch_intent."""
    text = segment or intent.raw_segment
    text = _segment_for_agent(intent.agent, text, history_text)

    if intent.agent == "ACCOUNTING":
        result = await AccountingExecutor.aexecute(
            text,
            entreprise,
            from_document=from_document,
        )
        return result, result.needs_clarification

    if intent.agent == "FINANCIAL_ANALYST":
        result = await FinancialAnalystExecutor.aexecute(text, entreprise)
        return result, getattr(result, "needs_clarification", False)

    if intent.agent == "ACCOUNTING_MODIFY":
        result = await AccountingModifyExecutor.aexecute(text, entreprise)
        return result, result.needs_clarification

    if intent.agent == "CUSTOMER":
        result = await CustomerExecutor.aexecute(text, entreprise)
        return result, getattr(result, "needs_clarification", False)

    if intent.agent == "QUOTE":
        result = await QuoteExecutor.aexecute(text, entreprise)
        return result, result.needs_clarification

    logger.warning(
        "[dispatch] Agent '%s' pas encore implémenté (intent_id=%s).",
        intent.agent,
        intent.intent_id,
    )
    return None, True
