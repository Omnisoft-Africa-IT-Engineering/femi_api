"""Exécuteur d'ACCOUNTING_PROMPT (nouveau système Router/Tools).

Construit le prompt par remplacement de texte (jamais PromptTemplate/.format(),
même raison que RouterExecutor : accolades JSON littérales dans les exemples
du prompt), puis délègue l'appel LLM à StructuredLLMExecutor (base_executor.py).

Utilise AccountingExtractionLLMResult (amount_ttc en float) comme cible de
with_structured_output() — AccountingExtractionResult (Decimal) fait échouer
la conversion en grammaire GBNF côté Ollama (voir schemas/accounting.py pour
le détail du bug et sa correction). Le résultat LLM est converti vers le
schéma interne (Decimal) juste après réception, via .from_llm_result().
"""

import logging

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.prompts.accounting_prompt import ACCOUNTING_PROMPT
from apps.femi_agent.schemas import AccountingExtractionLLMResult, AccountingExtractionResult
from apps.femi_agent.agent.tools.categories import get_categories_disponibles

logger = logging.getLogger(__name__)

# Le prompt système ACCOUNTING (~855 lignes, nombreux exemples JSON) dépasse
# largement le contexte par défaut d'Ollama (2048) — même précaution que pour
# ROUTER_PROMPT (voir router_executor.py), sans toucher au défaut partagé par
# l'ancien pipeline (voir llm.py).
ACCOUNTING_NUM_CTX = 8192

# Point 6 : injection dynamique des catégories du tenant via
# get_categories_disponibles(entreprise), avec repli statique si le tenant
# n'a aucune Categorie déclarée ou si aucun categories_disponibles fourni.
DEFAULT_CATEGORIES = "(catégories non disponibles)"


class AccountingExecutionError(BaseAgentExecutionError):
    """Exception personnalisée encapsulant les échecs d'exécution de l'agent ACCOUNTING."""
    pass


class AccountingExecutor:
    """
    Exécuteur d'ACCOUNTING_PROMPT : construit le prompt par remplacement de
    texte, appelle le LLM avec sortie structurée contrainte au schéma
    AccountingExtractionLLMResult (float), puis convertit le résultat vers
    AccountingExtractionResult (Decimal) avant de le retourner.
    """

    @staticmethod
    def _build_prompt_text(categories_disponibles: str) -> str:
        """Injecte les variables d'ACCOUNTING_PROMPT dans le texte brut du prompt."""
        return ACCOUNTING_PROMPT.replace("{categories_disponibles}", categories_disponibles)

    @classmethod
    def execute(
        cls,
        message_text: str,
        entreprise,
        categories_disponibles: str | None = None,
    ) -> AccountingExtractionResult:
        """Exécution synchrone de l'agent ACCOUNTING.

        Args:
            message_text: Message utilisateur (segment routé vers ACCOUNTING par le Router).
            entreprise: Instance Entreprise du tenant (utilisée pour résoudre les catégories réelles).
            categories_disponibles: Liste des catégories du tenant, déjà formatée en texte.
                Optionnel — si absent, résolu dynamiquement via get_categories_disponibles(entreprise).

        Returns:
            AccountingExtractionResult (schéma interne, amount_ttc en Decimal).
        """
        prompt_text = cls._build_prompt_text(categories_disponibles or get_categories_disponibles(entreprise) or DEFAULT_CATEGORIES)
        llm_result: AccountingExtractionLLMResult = StructuredLLMExecutor.execute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=AccountingExtractionLLMResult,
            num_ctx=ACCOUNTING_NUM_CTX,
            error_cls=AccountingExecutionError,
            log_prefix="AccountingExecutor",
        )
        return AccountingExtractionResult.from_llm_result(llm_result)

    @classmethod
    async def aexecute(
        cls,
        message_text: str,
        entreprise,
        categories_disponibles: str | None = None,
    ) -> AccountingExtractionResult:
        """Exécution asynchrone de l'agent ACCOUNTING (mêmes arguments que execute())."""
        prompt_text = cls._build_prompt_text(categories_disponibles or get_categories_disponibles(entreprise) or DEFAULT_CATEGORIES)
        llm_result: AccountingExtractionLLMResult = await StructuredLLMExecutor.aexecute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=AccountingExtractionLLMResult,
            num_ctx=ACCOUNTING_NUM_CTX,
            error_cls=AccountingExecutionError,
            log_prefix="AccountingExecutor",
        )
        return AccountingExtractionResult.from_llm_result(llm_result)
