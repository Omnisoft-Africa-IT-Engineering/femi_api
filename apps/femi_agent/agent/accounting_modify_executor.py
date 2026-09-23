"""Exécuteur d'ACCOUNTING_MODIFY_PROMPT (UPDATE/DELETE d'opérations existantes).

Orchestre directement 2 appels à StructuredLLMExecutor.execute() — PAS
ToolLoopExecutor.resolve(), incompatible car câblé en dur sur
ToolSelectionOutput/FinalAnswerOutput (voir tool_loop_executor.py et
schemas/tool_loop.py).

Appel 1 -> AccountingModifySearchResult (étape search). Si
needs_clarification=True, retour direct sans appel 2.

Entre les deux appels, le backend exécute find_matching_operations()
(tools/operations.py, déjà validé) et réinjecte son résultat brut (dict
{count, operations, candidates}) dans {search_results} via .replace(),
même pattern que ToolLoopExecutor.resolve() pour {tool_results}.

Appel 2 : polymorphe. D'après les sections 9/10/11 du prompt (CAS 1/2/3),
c'est le LLM lui-même qui distingue, à partir du contenu de
{search_results}, s'il doit produire AccountingModifyResolutionResult
(aucun résultat ou plusieurs candidats) ou AccountingModifyProposeChangeResult
(un seul résultat) -- le backend ne pré-sélectionne jamais ce schéma.
Ceci est fait via une enveloppe Union discriminée sur "step"
(AccountingModifyStepTwoOutput, définie ici -- "au point d'usage", comme
le recommande le commentaire sur AccountingModifyResult dans
schemas/accounting_modify.py), suivant le pattern confirmé empiriquement
dans schemas/customer.py (CustomerFirstStepOutput) pour
with_structured_output() + Ollama.
"""

import json
import logging
from typing import Union

from pydantic import BaseModel, Field

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.prompts.accounting_modify_prompt import ACCOUNTING_MODIFY_PROMPT
from apps.femi_agent.agent.tools.operations import find_matching_operations
from apps.femi_agent.agent.tools.categories import get_categories_disponibles
from apps.femi_agent.schemas import (
    AccountingModifyProposeChangeResult,
    AccountingModifyResolutionResult,
    OperationCandidate,
    AccountingModifyResult,
    AccountingModifySearchResult,
    SearchCriteria,
)

logger = logging.getLogger(__name__)

# Prompt ~949 lignes, nombreux exemples JSON -> même précaution que
# ACCOUNTING_NUM_CTX (accounting_executor.py) / ROUTER_PROMPT.
ACCOUNTING_MODIFY_NUM_CTX = 16384

# Filet de sécurité final si get_categories_disponibles() ne renvoie rien
# (ex. entreprise sans aucune Categorie déclarée, cf. tools/categories.py).
DEFAULT_CATEGORIES = ""  # fallback si aucune Categorie déclarée pour ce tenant


class AccountingModifyExecutionError(BaseAgentExecutionError):
    """Exception personnalisée encapsulant les échecs d'exécution de l'agent ACCOUNTING_MODIFY."""
    pass


class AccountingModifyStepTwoOutput(BaseModel):
    """
    Enveloppe pour le second appel LLM d'ACCOUNTING_MODIFY : union discriminée
    sur "step", résolvant vers AccountingModifyResolutionResult (CAS 1/2) ou
    AccountingModifyProposeChangeResult (CAS 3). Jamais "search" à cette étape.
    """

    result: Union[AccountingModifyResolutionResult, AccountingModifyProposeChangeResult] = Field(
        discriminator="step"
    )


class AccountingModifyExecutor:
    """
    Exécuteur d'ACCOUNTING_MODIFY_PROMPT : orchestre recherche (appel 1),
    exécution du tool find_matching_operations, puis résolution/proposition
    (appel 2, format choisi par le LLM lui-même).
    """

    @staticmethod
    def _build_prompt_text(categories_disponibles: str, search_results: dict | None = None) -> str:
        """Injecte {categories_disponibles} et {search_results} dans le texte brut du prompt.

        search_results=None (appel 1) injecte une chaîne vide, cohérent avec
        la section 3 du prompt ("Si {search_results} est vide, absent ou null").
        """
        text = ACCOUNTING_MODIFY_PROMPT.replace("{categories_disponibles}", categories_disponibles)
        results_text = "" if search_results is None else json.dumps(search_results, ensure_ascii=False)
        return text.replace("{search_results}", results_text)

    @staticmethod
    def _map_search_criteria(search_criteria: SearchCriteria) -> dict:
        """Mappe SearchCriteria (schéma LLM) vers les kwargs de
        find_matching_operations() (tools/operations.py).
        Seul renommage : contact -> contact_nom."""
        return {
            "operation_id": search_criteria.operation_id,
            "contact_nom": search_criteria.contact,
            "amount_ttc": search_criteria.amount_ttc,
            "category": search_criteria.category,
            "date_range": search_criteria.date_range,
            "date_debut": search_criteria.date_debut,
            "date_fin": search_criteria.date_fin,
            "description_keywords": search_criteria.description_keywords,
            "transaction_type": search_criteria.transaction_type,
        }

    @classmethod
    def execute(
        cls,
        message_text: str,
        entreprise,
        categories_disponibles: str | None = None,
    ) -> AccountingModifyResult:
        """Exécution synchrone de l'agent ACCOUNTING_MODIFY.

        Args:
            message_text: message utilisateur (segment routé vers
                ACCOUNTING_MODIFY par le Router).
            entreprise: instance Entreprise (jamais résolue en interne),
                transmise telle quelle à find_matching_operations().
            categories_disponibles: liste des catégories du tenant, déjà
                formatée en texte. Optionnel — si absent, résolu dynamiquement via get_categories_disponibles(entreprise).

        Returns:
            AccountingModifySearchResult (si clarification nécessaire dès
            l'appel 1) | AccountingModifyResolutionResult |
            AccountingModifyProposeChangeResult.
        """
        categories = categories_disponibles or get_categories_disponibles(entreprise) or DEFAULT_CATEGORIES

        prompt_text = cls._build_prompt_text(categories, search_results=None)
        search_result: AccountingModifySearchResult = StructuredLLMExecutor.execute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=AccountingModifySearchResult,
            num_ctx=ACCOUNTING_MODIFY_NUM_CTX,
            error_cls=AccountingModifyExecutionError,
            log_prefix="AccountingModifyExecutor/search",
        )

        if search_result.needs_clarification:
            return search_result

        kwargs = cls._map_search_criteria(search_result.search_criteria)
        search_results = find_matching_operations(entreprise, **kwargs)

        if search_results["count"] > 1:
            return AccountingModifyResolutionResult(
                step="resolution",
                action_type=search_result.action_type,
                needs_clarification=True,
                missing_fields=["operation_disambiguation"],
                candidates=[OperationCandidate(**c) for c in search_results["candidates"]],
            )

        prompt_text_2 = cls._build_prompt_text(categories, search_results=search_results)
        step_two: AccountingModifyStepTwoOutput = StructuredLLMExecutor.execute(
            prompt_text=prompt_text_2,
            message_text=message_text,
            output_schema=AccountingModifyStepTwoOutput,
            num_ctx=ACCOUNTING_MODIFY_NUM_CTX,
            error_cls=AccountingModifyExecutionError,
            log_prefix="AccountingModifyExecutor/resolve",
        )
        return step_two.result

    @classmethod
    async def aexecute(
        cls,
        message_text: str,
        entreprise,
        categories_disponibles: str | None = None,
    ) -> AccountingModifyResult:
        """Version asynchrone de execute() (mêmes arguments)."""
        categories = categories_disponibles or get_categories_disponibles(entreprise) or DEFAULT_CATEGORIES

        prompt_text = cls._build_prompt_text(categories, search_results=None)
        search_result: AccountingModifySearchResult = await StructuredLLMExecutor.aexecute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=AccountingModifySearchResult,
            num_ctx=ACCOUNTING_MODIFY_NUM_CTX,
            error_cls=AccountingModifyExecutionError,
            log_prefix="AccountingModifyExecutor/search",
        )

        if search_result.needs_clarification:
            return search_result

        kwargs = cls._map_search_criteria(search_result.search_criteria)
        search_results = find_matching_operations(entreprise, **kwargs)

        if search_results["count"] > 1:
            return AccountingModifyResolutionResult(
                step="resolution",
                action_type=search_result.action_type,
                needs_clarification=True,
                missing_fields=["operation_disambiguation"],
                candidates=[OperationCandidate(**c) for c in search_results["candidates"]],
            )

        prompt_text_2 = cls._build_prompt_text(categories, search_results=search_results)
        step_two: AccountingModifyStepTwoOutput = await StructuredLLMExecutor.aexecute(
            prompt_text=prompt_text_2,
            message_text=message_text,
            output_schema=AccountingModifyStepTwoOutput,
            num_ctx=ACCOUNTING_MODIFY_NUM_CTX,
            error_cls=AccountingModifyExecutionError,
            log_prefix="AccountingModifyExecutor/resolve",
        )
        return step_two.result
