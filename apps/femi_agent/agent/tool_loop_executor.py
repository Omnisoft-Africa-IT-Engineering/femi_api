"""Exécuteur générique pour le pattern boucle-avec-tools du nouveau système
multi-agent (FINANCIAL_ANALYST, sous-flux READ de CUSTOMER).

Toujours 2 étapes fixes : tool_selection (le LLM choisit un ou plusieurs
tools) → exécution réelle des tools Python → final_answer (le LLM,
réinjecté avec {tool_results}, produit la réponse WhatsApp). Aucun exemple
dans financial_analyst_prompt.py ni customer_prompt.py ne montre plus d'un
tour de tool_selection : la limite à 2 étapes est donc appliquée par la
structure même du code (pas de boucle while, pas de variable de comptage),
volontairement, plutôt que par un garde-fou explicite qui pourrait donner
une fausse impression de protection si le code est un jour transformé en
vraie boucle.

L'étape "validation" propre au sous-flux READ de CUSTOMER n'est PAS gérée
ici : elle est spécifique à un seul agent et reste à la charge de
customer_executor.py, via un appel simple à StructuredLLMExecutor avant
d'appeler ToolLoopExecutor.

Convention reprise de base_executor.py/accounting_executor.py : le prompt
est construit par remplacement de texte (.replace()), jamais
PromptTemplate/.format(), à cause des accolades JSON littérales des
exemples dans les prompts.

Ne connaît ni "entreprise" ni "contact" ni aucun concept métier : chaque
tool du registry fourni par l'appelant doit déjà être prêt à être appelé
avec uniquement les params renvoyés par le LLM (voir financial_analyst_executor.py
et customer_executor.py, qui lient entreprise/contact via functools.partial
avant de construire leur registry).

STRUCTURE (depuis le refactor R4b-iv) : execute()/aexecute() sont
désormais de simples wrappers de deux méthodes séparées :
  - _select_tools()/_aselect_tools() : le premier appel LLM (tool_selection)
    seul, privé — utilisé en interne par execute()/aexecute().
  - resolve()/aresolve() : PUBLIQUES — exécution des tools + appel LLM
    final_answer, à partir d'une ToolSelectionOutput déjà obtenue.
Nécessaire pour CUSTOMER-READ : son "premier appel tool_selection" est en
réalité le premier appel polymorphe de CustomerFirstStepOutput lui-même
(voir customer.py) — customer_executor.py doit donc pouvoir enchaîner
directement sur resolve() sans repasser par _select_tools(), ce qui
gaspillerait une inférence Ollama, précieuse vu la lenteur CPU-only.
resolve()/aresolve() font CONFIANCE à l'appelant sur needs_clarification :
ils n'exécutent jamais cette vérification eux-mêmes (voir docstring de
resolve() plus bas) — ToolLoopExecutor ne connaît aucun concept métier,
donc décider si on doit clarifier avant d'exécuter les tools n'est pas de
son ressort.
"""

import json
import logging
from typing import Callable

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.schemas.tool_loop import FinalAnswerOutput, ToolSelectionOutput

logger = logging.getLogger(__name__)

LOG_TEXT_PREVIEW_LEN = 80


class ToolLoopExecutionError(Exception):
    """Exception générique pour un échec d'exécution du pattern boucle-avec-tools."""
    pass


class ToolLoopExecutor:
    """
    Exécuteur générique à 2 étapes fixes : tool_selection → final_answer.
    Ne connaît ni le contenu du prompt ni le domaine métier de l'agent.
    """

    @staticmethod
    def execute(
        prompt_text: str,
        message_text: str,
        tool_registry: dict[str, Callable],
        num_ctx: int,
        log_prefix: str = "ToolLoopExecutor",
    ) -> ToolSelectionOutput | FinalAnswerOutput:
        """
        Exécution synchrone complète : tool_selection puis, si possible,
        exécution des tools + final_answer. Simple wrapper de
        _select_tools() + resolve(), pour les appelants (FinancialAnalystExecutor)
        qui n'ont pas déjà fait le premier appel ailleurs.

        Args:
            prompt_text: prompt initial de l'agent (sans {tool_results}
                encore résolu — le placeholder est remplacé en interne
                après l'étape tool_selection).
            message_text: message utilisateur original.
            tool_registry: dict {nom_du_tool: callable}, chaque callable
                déjà prêt à être appelé avec uniquement les params du LLM
                (entreprise/contact déjà liés en amont par l'appelant).
            num_ctx: taille de contexte à passer au LLM.
            log_prefix: préfixe de log, pour distinguer FINANCIAL_ANALYST
                de CUSTOMER dans les logs.

        Returns:
            ToolSelectionOutput si needs_clarification=True (pas de tools
            exécutés, pas de deuxième appel), sinon FinalAnswerOutput.
        """
        selection = ToolLoopExecutor._select_tools(
            prompt_text=prompt_text,
            message_text=message_text,
            num_ctx=num_ctx,
            log_prefix=log_prefix,
        )

        if selection.needs_clarification:
            logger.debug(
                "[%s] needs_clarification=True à l'étape tool_selection, arrêt sans exécuter de tools.",
                log_prefix,
            )
            return selection

        return ToolLoopExecutor.resolve(
            selection=selection,
            prompt_text=prompt_text,
            message_text=message_text,
            tool_registry=tool_registry,
            num_ctx=num_ctx,
            log_prefix=log_prefix,
        )

    @staticmethod
    async def aexecute(
        prompt_text: str,
        message_text: str,
        tool_registry: dict[str, Callable],
        num_ctx: int,
        log_prefix: str = "ToolLoopExecutor",
    ) -> ToolSelectionOutput | FinalAnswerOutput:
        """Exécution asynchrone (mêmes arguments que execute())."""
        selection = await ToolLoopExecutor._aselect_tools(
            prompt_text=prompt_text,
            message_text=message_text,
            num_ctx=num_ctx,
            log_prefix=log_prefix,
        )

        if selection.needs_clarification:
            logger.debug(
                "[%s] needs_clarification=True à l'étape tool_selection, arrêt sans exécuter de tools.",
                log_prefix,
            )
            return selection

        return await ToolLoopExecutor.aresolve(
            selection=selection,
            prompt_text=prompt_text,
            message_text=message_text,
            tool_registry=tool_registry,
            num_ctx=num_ctx,
            log_prefix=log_prefix,
        )

    @staticmethod
    def _select_tools(
        prompt_text: str,
        message_text: str,
        num_ctx: int,
        log_prefix: str = "ToolLoopExecutor",
    ) -> ToolSelectionOutput:
        """
        Premier appel LLM (tool_selection) seul, sans exécution des tools
        ni deuxième appel. Privé : utilisé en interne par execute(). Les
        appelants dont le premier appel LLM est fait ailleurs (ex:
        CUSTOMER, via CustomerFirstStepOutput) n'utilisent jamais cette
        méthode — ils obtiennent déjà leur ToolSelectionOutput autrement
        et appellent directement resolve().
        """
        return StructuredLLMExecutor.execute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=ToolSelectionOutput,
            num_ctx=num_ctx,
            error_cls=ToolLoopExecutionError,
            log_prefix=f"{log_prefix}/tool_selection",
        )

    @staticmethod
    async def _aselect_tools(
        prompt_text: str,
        message_text: str,
        num_ctx: int,
        log_prefix: str = "ToolLoopExecutor",
    ) -> ToolSelectionOutput:
        """Version asynchrone de _select_tools()."""
        return await StructuredLLMExecutor.aexecute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=ToolSelectionOutput,
            num_ctx=num_ctx,
            error_cls=ToolLoopExecutionError,
            log_prefix=f"{log_prefix}/tool_selection",
        )

    @staticmethod
    def resolve(
        selection: ToolSelectionOutput,
        prompt_text: str,
        message_text: str,
        tool_registry: dict[str, Callable],
        num_ctx: int,
        log_prefix: str = "ToolLoopExecutor",
    ) -> FinalAnswerOutput:
        """
        Exécute les tools d'une ToolSelectionOutput déjà obtenue, puis fait
        l'appel LLM final_answer. PUBLIQUE : point d'entrée pour
        CUSTOMER-READ, dont le "premier appel tool_selection" est en
        réalité le premier appel polymorphe de CustomerFirstStepOutput
        (voir schemas/customer.py) — customer_executor.py appelle resolve()
        directement, sans repasser par _select_tools(), pour ne pas
        gaspiller une inférence Ollama.

        IMPORTANT : cette méthode NE VÉRIFIE PAS selection.needs_clarification.
        C'est la responsabilité de l'appelant de s'assurer que
        needs_clarification=False avant d'appeler resolve() — ToolLoopExecutor
        ne connaît aucun concept métier et ne doit pas décider si une
        clarification est nécessaire.

        Args:
            selection: ToolSelectionOutput déjà obtenue (par
                _select_tools() ou par un autre appel LLM polymorphe),
                needs_clarification déjà vérifié False par l'appelant.
            prompt_text: même prompt initial que pour l'appel qui a produit
                `selection` (contient encore {tool_results} non résolu).
            message_text: message utilisateur original.
            tool_registry: dict {nom_du_tool: callable}, voir execute().
            num_ctx: taille de contexte à passer au LLM.
            log_prefix: préfixe de log.

        Returns:
            FinalAnswerOutput.
        """
        tool_results = ToolLoopExecutor._run_tools(selection, tool_registry, log_prefix)

        final_prompt = prompt_text.replace("{tool_results}", json.dumps(tool_results, ensure_ascii=False))

        return StructuredLLMExecutor.execute(
            prompt_text=final_prompt,
            message_text=message_text,
            output_schema=FinalAnswerOutput,
            num_ctx=num_ctx,
            error_cls=ToolLoopExecutionError,
            log_prefix=f"{log_prefix}/final_answer",
        )

    @staticmethod
    async def aresolve(
        selection: ToolSelectionOutput,
        prompt_text: str,
        message_text: str,
        tool_registry: dict[str, Callable],
        num_ctx: int,
        log_prefix: str = "ToolLoopExecutor",
    ) -> FinalAnswerOutput:
        """Version asynchrone de resolve()."""
        tool_results = ToolLoopExecutor._run_tools(selection, tool_registry, log_prefix)

        final_prompt = prompt_text.replace("{tool_results}", json.dumps(tool_results, ensure_ascii=False))

        return await StructuredLLMExecutor.aexecute(
            prompt_text=final_prompt,
            message_text=message_text,
            output_schema=FinalAnswerOutput,
            num_ctx=num_ctx,
            error_cls=ToolLoopExecutionError,
            log_prefix=f"{log_prefix}/final_answer",
        )

    @staticmethod
    def _run_tools(
        selection: ToolSelectionOutput,
        tool_registry: dict[str, Callable],
        log_prefix: str,
    ) -> dict:
        """
        Exécute chaque tool_call demandé par le LLM, indexé par nom de tool
        (voir schemas/tool_loop.py pour la justification du format
        {nom_du_tool: résultat}). Un tool absent du registry ou qui lève
        une exception ne bloque pas les autres : son résultat est remplacé
        par {"success": False, "error": "..."} (cf. customer_prompt.py
        section 9, "absence de créance vs échec technique" — le LLM doit
        pouvoir distinguer les deux cas).

        Le détail brut d'une exception (str(e)) n'est JAMAIS placé dans
        tool_results : ce contenu est réinjecté dans le prompt du deuxième
        appel LLM, qui peut le citer dans sa réponse WhatsApp finale — un
        message d'exception Django/ORM pourrait exposer des détails
        techniques internes à l'utilisateur final. L'exception complète est
        loguée côté serveur (logger.exception), mais seul un message
        générique et sûr est exposé au LLM.
        """
        results = {}
        for call in selection.tool_calls:
            if call.tool not in tool_registry:
                logger.warning(
                    "[%s] Tool inconnu demandé par le LLM : '%s' (absent du registry).",
                    log_prefix, call.tool,
                )
                results[call.tool] = {"success": False, "error": f"tool inconnu : {call.tool}"}
                continue
            try:
                results[call.tool] = tool_registry[call.tool](**call.params)
            except Exception:
                logger.exception("[%s] Échec d'exécution du tool '%s'", log_prefix, call.tool)
                results[call.tool] = {"success": False, "error": "erreur technique lors de l'exécution du tool"}
        return results