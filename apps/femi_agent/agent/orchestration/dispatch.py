"""Dispatch : un intent Router → exécuteur d'agent."""
from __future__ import annotations
import logging
from typing import Optional, Tuple
from apps.femi_agent.agent.accounting_executor import AccountingExecutor
from apps.femi_agent.agent.accounting_modify_executor import AccountingModifyExecutor
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
    AccountingExtractionResult | ToolSelectionOutput | FinalAnswerOutput
    | AccountingModifySearchResult | AccountingModifyResolutionResult
    | AccountingModifyProposeChangeResult | QuoteExtractionResult
]

def dispatch_intent(intent: RouterIntent, entreprise, from_document: bool = False, segment: Optional[str] = None) -> Tuple[DispatchResult, bool]:
    text = segment or intent.raw_segment
    logger.info("[dispatch] intent: agent=%s | segment=%s", intent.agent, intent.raw_segment)
    if intent.agent == "ACCOUNTING":
        result = AccountingExecutor.execute(text, entreprise, from_document=from_document)
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
    logger.warning("[dispatch] Agent '%s' pas encore implémenté (intent_id=%s).", intent.agent, intent.intent_id)
    return None, True

async def adispatch_intent(intent: RouterIntent, entreprise, from_document: bool = False, segment: Optional[str] = None) -> Tuple[DispatchResult, bool]:
    text = segment or intent.raw_segment
    if intent.agent == "ACCOUNTING":
        result = await AccountingExecutor.aexecute(text, entreprise, from_document=from_document)
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
    logger.warning("[dispatch] Agent '%s' pas encore implémenté (intent_id=%s).", intent.agent, intent.intent_id)
    return None, True
