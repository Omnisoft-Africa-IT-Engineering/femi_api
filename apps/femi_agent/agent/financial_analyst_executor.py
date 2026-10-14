"""Exécuteur de FINANCIAL_ANALYST_PROMPT (nouveau système Router/Tools).

Contrairement à ACCOUNTING (un seul appel LLM → un objet Pydantic), suit le
pattern boucle-avec-tools : tool_selection → exécution réelle des tools
Python de apps/femi_agent/agent/tools/financials.py → final_answer. Voir
tool_loop_executor.py pour le détail du mécanisme générique (2 étapes,
{tool_results} injecté par .replace()).

Construit le prompt par remplacement de texte (jamais PromptTemplate/
.format(), même raison que ACCOUNTING/Router : accolades JSON littérales
dans les exemples du prompt).

"entreprise" n'est jamais un paramètre choisi par le LLM (voir en-tête de
tools/financials.py) : il est lié à chaque tool via functools.partial avant
de construire le registry, pour que ToolLoopExecutor puisse appeler chaque
tool avec uniquement les params renvoyés par le LLM.
"""

import logging
from functools import partial

from apps.femi_agent.agent.financial_analyst_rules import (
    PERIODE_PAR_DEFAUT,
    TOOLS_INDICATEURS,
    analyser_periode,
    indicateur_unique,
)
from apps.femi_agent.agent.prompts.financial_analyst_prompt import FINANCIAL_ANALYST_PROMPT
from apps.femi_agent.agent.tool_loop_executor import ToolLoopExecutionError, ToolLoopExecutor
from apps.femi_agent.agent.tools import financials
from apps.femi_agent.schemas.tool_loop import (
    FinalAnswerOutput,
    ToolCallSchema,
    ToolSelectionOutput,
)

logger = logging.getLogger(__name__)

# Même précaution que pour ACCOUNTING/Router : le prompt système (nombreux
# exemples JSON) dépasse largement le contexte par défaut d'Ollama (2048).
FINANCIAL_ANALYST_NUM_CTX = 16384

# TODO (Point 6, pas encore fait) : remplacer par la vraie liste des années
# disponibles du tenant (Entreprise -> Operation.transaction_date). Valeur
# de repli statique pour l'instant, pour ne pas bloquer le test de
# FINANCIAL_ANALYST sur Point 6 — même raisonnement que DEFAULT_CATEGORIES
# dans accounting_executor.py.
DEFAULT_ANNEES_DISPONIBLES = "(années non disponibles)"

# Noms de tools exposés au LLM, repris tels quels de l'en-tête de
# tools/financials.py — ne jamais en ajouter un qui n'y est pas documenté.
_TOOL_NAMES = (
    "get_revenue",
    "get_expenses",
    "calculate_profit",
    "calculate_margin",
    "get_cashflow",
    "calculate_balance",
    "compare_periods",
    "get_bilan",
)


# Codes de clarification que le LLM a raison de lever : on ne les écrase jamais.
_CODES_A_RESPECTER = {"wrong_agent", "fonctionnalite_non_disponible"}


class FinancialAnalystExecutionError(ToolLoopExecutionError):
    """Exception personnalisée encapsulant les échecs d'exécution de l'agent FINANCIAL_ANALYST."""
    pass


class FinancialAnalystExecutor:
    """
    Exécuteur de FINANCIAL_ANALYST_PROMPT : construit le prompt et le
    tool_registry (entreprise déjà liée), délègue la boucle tool_selection/
    final_answer à ToolLoopExecutor.
    """

    @staticmethod
    def _build_prompt_text(annees_disponibles: str) -> str:
        """Injecte les variables de FINANCIAL_ANALYST_PROMPT dans le texte brut du prompt."""
        return FINANCIAL_ANALYST_PROMPT.replace("{annees_disponibles}", annees_disponibles)

    @staticmethod
    def _build_tool_registry(entreprise) -> dict:
        """Lie entreprise à chaque tool de tools/financials.py via functools.partial."""
        return {name: partial(getattr(financials, name), entreprise) for name in _TOOL_NAMES}

    @staticmethod
    def _corriger_selection(
        selection: ToolSelectionOutput,
        message_text: str,
        tool_registry: dict,
    ) -> ToolSelectionOutput:
        """Garde déterministe appliqué à la sélection de tools du LLM.

        1. Un seul indicateur simple (CA, dépenses, bénéfice, marge) nommé
           dans le message : on appelle directement le bon tool, avec la
           période lue dans le texte ou « ce_mois » par défaut. Plus de
           question de période, plus de bénéfice à la place du CA.
        2. Aucun tool et aucune clarification : on ne laisse pas le LLM
           écrire une question en « réponse finale » (elle ne serait pas
           mémorisée pour le message suivant). On force le tool évident
           (bilan) ou on lève une vraie clarification, mémorisée.
        """
        codes = {str(c).lower() for c in (selection.missing_fields or [])}
        if codes & _CODES_A_RESPECTER:
            return selection

        tool = indicateur_unique(message_text)
        periode, periode_non_geree = analyser_periode(message_text)

        if tool and tool in tool_registry and not periode_non_geree:
            choisis = {c.tool for c in selection.tool_calls}
            incoherent = bool(choisis) and choisis <= TOOLS_INDICATEURS and tool not in choisis
            if not choisis or incoherent or selection.needs_clarification:
                logger.warning(
                    "[FinancialAnalystExecutor] garde: tool=%s periode=%s "
                    "(LLM: tools=%s clarification=%s missing=%s)",
                    tool,
                    periode or PERIODE_PAR_DEFAUT,
                    sorted(choisis),
                    selection.needs_clarification,
                    selection.missing_fields,
                )
                return ToolSelectionOutput(
                    step="tool_selection",
                    tool_calls=[
                        ToolCallSchema(
                            tool=tool,
                            params={"periode": periode or PERIODE_PAR_DEFAUT},
                        )
                    ],
                    needs_clarification=False,
                    missing_fields=[],
                )
            return selection

        if selection.tool_calls or selection.needs_clarification:
            return selection

        forced = ToolLoopExecutor._force_customer_tools_if_empty(
            selection, message_text, tool_registry, "FinancialAnalystExecutor"
        )
        if forced.tool_calls:
            return forced

        logger.warning(
            "[FinancialAnalystExecutor] garde: aucun tool ni clarification "
            "-> clarification mémorisable (message=%r)",
            (message_text or "")[:80],
        )
        return ToolSelectionOutput(
            step="tool_selection",
            tool_calls=[],
            needs_clarification=True,
            missing_fields=["date_range_incomplete" if tool else "indicateur"],
        )

    @classmethod
    def execute(
        cls,
        message_text: str,
        entreprise,
        annees_disponibles: str | None = None,
    ) -> ToolSelectionOutput | FinalAnswerOutput:
        """Exécution synchrone de l'agent FINANCIAL_ANALYST.

        Args:
            message_text: Message utilisateur (segment routé vers FINANCIAL_ANALYST par le Router).
            entreprise: instance Entreprise, liée à chaque tool avant exécution.
            annees_disponibles: années disponibles pour ce tenant, déjà formatées en texte.
                Optionnel — TODO Point 6 : injection dynamique réelle pas encore branchée.

        Returns:
            ToolSelectionOutput si needs_clarification=True, sinon FinalAnswerOutput.
        """
        prompt_text = cls._build_prompt_text(annees_disponibles or DEFAULT_ANNEES_DISPONIBLES)
        tool_registry = cls._build_tool_registry(entreprise)
        selection = ToolLoopExecutor._select_tools(
            prompt_text=prompt_text,
            message_text=message_text,
            num_ctx=FINANCIAL_ANALYST_NUM_CTX,
            log_prefix="FinancialAnalystExecutor",
        )
        selection = cls._corriger_selection(selection, message_text, tool_registry)
        if selection.needs_clarification:
            return selection
        return ToolLoopExecutor.resolve(
            selection=selection,
            prompt_text=prompt_text,
            message_text=message_text,
            tool_registry=tool_registry,
            num_ctx=FINANCIAL_ANALYST_NUM_CTX,
            log_prefix="FinancialAnalystExecutor",
        )

    @classmethod
    async def aexecute(
        cls,
        message_text: str,
        entreprise,
        annees_disponibles: str | None = None,
    ) -> ToolSelectionOutput | FinalAnswerOutput:
        """Exécution asynchrone de l'agent FINANCIAL_ANALYST (mêmes arguments que execute())."""
        prompt_text = cls._build_prompt_text(annees_disponibles or DEFAULT_ANNEES_DISPONIBLES)
        tool_registry = cls._build_tool_registry(entreprise)
        selection = await ToolLoopExecutor._aselect_tools(
            prompt_text=prompt_text,
            message_text=message_text,
            num_ctx=FINANCIAL_ANALYST_NUM_CTX,
            log_prefix="FinancialAnalystExecutor",
        )
        selection = cls._corriger_selection(selection, message_text, tool_registry)
        if selection.needs_clarification:
            return selection
        return await ToolLoopExecutor.aresolve(
            selection=selection,
            prompt_text=prompt_text,
            message_text=message_text,
            tool_registry=tool_registry,
            num_ctx=FINANCIAL_ANALYST_NUM_CTX,
            log_prefix="FinancialAnalystExecutor",
        )