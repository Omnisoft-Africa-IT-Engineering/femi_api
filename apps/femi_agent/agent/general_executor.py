"""Exécuteur de GENERAL_PROMPT (agent « conseiller entreprise », lecture seule).

Répond aux questions qu'aucun autre agent ne traite : profil et activité de
l'entreprise, meilleurs clients, ce qui se vend le plus, services proposés,
recherche d'opérations, notions générales de gestion, messages hors sujet.

Même pattern boucle-avec-tools que FINANCIAL_ANALYST (voir
tool_loop_executor.py) : tool_selection → exécution réelle des tools Python de
tools/company.py → final_answer. Les schémas de sortie sont ceux de
schemas/tool_loop.py : le résultat (FinalAnswerOutput) est donc classé et
assemblé par router_manager.py exactement comme celui de FINANCIAL_ANALYST,
sans modification de router_manager.py.

Particularités de GENERAL :
1. get_company_profile est TOUJOURS exécuté, quel que soit le choix du LLM :
   la fiche entreprise fait ainsi partie des résultats sur lesquels la
   réponse finale s'appuie (ToolLoopExecutor n'autorise que les résultats
   de tools à l'étape final_answer).
2. GENERAL ne demande jamais de clarification : toute clarification du LLM
   est ignorée, l'agent répond avec ce qu'il a et dit ce qui manque. Le
   résultat est donc toujours un FinalAnswerOutput.
3. ToolLoopExecutor indexe les résultats par nom de tool : un même tool
   appelé deux fois écraserait le premier résultat. Seul le premier appel de
   chaque tool est conservé.

Construit le prompt par remplacement de texte (jamais PromptTemplate/
.format(), accolades JSON littérales dans les exemples). `entreprise` est lié
à chaque tool via functools.partial, jamais choisi par le LLM.
"""

import logging
from functools import partial

from apps.femi_agent.agent.prompts.general_prompt import GENERAL_PROMPT
from apps.femi_agent.agent.tool_loop_executor import ToolLoopExecutionError, ToolLoopExecutor
from apps.femi_agent.agent.tools import company
from apps.femi_agent.schemas.tool_loop import (
    FinalAnswerOutput,
    ToolCallSchema,
    ToolSelectionOutput,
)

logger = logging.getLogger(__name__)

# Même précaution de contexte que les autres agents à gros prompt (Ollama).
GENERAL_NUM_CTX = 16384

PROFILE_TOOL = "get_company_profile"


class GeneralExecutionError(ToolLoopExecutionError):
    """Exception personnalisée encapsulant les échecs d'exécution de l'agent GENERAL."""
    pass


class GeneralExecutor:
    """
    Exécuteur de GENERAL_PROMPT : construit le tool_registry (entreprise déjà
    liée), normalise la sélection de tools, délègue à ToolLoopExecutor.
    """

    @staticmethod
    def _build_prompt_text() -> str:
        """Le prompt n'a pas de variable à injecter : {tool_results} est
        remplacé plus tard par ToolLoopExecutor.resolve()."""
        return GENERAL_PROMPT

    @staticmethod
    def _build_tool_registry(entreprise) -> dict:
        """Lie entreprise à chaque tool de tools/company.py via functools.partial."""
        return {name: partial(getattr(company, name), entreprise) for name in company.TOOL_NAMES}

    @staticmethod
    def _normaliser_selection(
        selection: ToolSelectionOutput,
        tool_registry: dict,
    ) -> ToolSelectionOutput:
        """Garde déterministe appliqué à la sélection du LLM.

        - ignore les tools inconnus du registry ;
        - ne garde que le premier appel de chaque tool (résultats indexés par nom) ;
        - ajoute toujours get_company_profile en tête ;
        - supprime toute demande de clarification (GENERAL ne clarifie jamais).
        """
        appels: list[ToolCallSchema] = []
        vus: set[str] = set()
        for appel in selection.tool_calls or []:
            if appel.tool not in tool_registry:
                logger.warning("[GeneralExecutor] tool inconnu ignoré : %r", appel.tool)
                continue
            if appel.tool in vus:
                logger.warning("[GeneralExecutor] appel en double ignoré : %r", appel.tool)
                continue
            vus.add(appel.tool)
            appels.append(appel)

        if PROFILE_TOOL not in vus:
            appels.insert(0, ToolCallSchema(tool=PROFILE_TOOL, params={}))

        if selection.needs_clarification:
            logger.info(
                "[GeneralExecutor] clarification ignorée (GENERAL répond sans en poser) : %s",
                selection.missing_fields,
            )

        return ToolSelectionOutput(
            step="tool_selection",
            tool_calls=appels,
            needs_clarification=False,
            missing_fields=[],
        )

    @classmethod
    def execute(cls, message_text: str, entreprise) -> FinalAnswerOutput:
        """Exécution synchrone de l'agent GENERAL.

        Args:
            message_text: segment routé vers GENERAL (avec contexte récent,
                voir dispatch._segment_for_agent).
            entreprise: instance Entreprise du tenant, liée à chaque tool.

        Returns:
            FinalAnswerOutput, toujours (jamais de clarification).
        """
        prompt_text = cls._build_prompt_text()
        tool_registry = cls._build_tool_registry(entreprise)
        selection = ToolLoopExecutor._select_tools(
            prompt_text=prompt_text,
            message_text=message_text,
            num_ctx=GENERAL_NUM_CTX,
            log_prefix="GeneralExecutor",
        )
        selection = cls._normaliser_selection(selection, tool_registry)
        return ToolLoopExecutor.resolve(
            selection=selection,
            prompt_text=prompt_text,
            message_text=message_text,
            tool_registry=tool_registry,
            num_ctx=GENERAL_NUM_CTX,
            log_prefix="GeneralExecutor",
        )

    @classmethod
    async def aexecute(cls, message_text: str, entreprise) -> FinalAnswerOutput:
        """Exécution asynchrone de l'agent GENERAL (mêmes arguments que execute())."""
        prompt_text = cls._build_prompt_text()
        tool_registry = cls._build_tool_registry(entreprise)
        selection = await ToolLoopExecutor._aselect_tools(
            prompt_text=prompt_text,
            message_text=message_text,
            num_ctx=GENERAL_NUM_CTX,
            log_prefix="GeneralExecutor",
        )
        selection = cls._normaliser_selection(selection, tool_registry)
        return await ToolLoopExecutor.aresolve(
            selection=selection,
            prompt_text=prompt_text,
            message_text=message_text,
            tool_registry=tool_registry,
            num_ctx=GENERAL_NUM_CTX,
            log_prefix="GeneralExecutor",
        )