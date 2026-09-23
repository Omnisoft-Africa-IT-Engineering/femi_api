"""Exécuteur de CUSTOMER_PROMPT (nouveau système Router/Tools, sous-flux READ+CREATE).

Le 1er appel LLM utilise CustomerFirstStepOutput (union discriminée sur "step",
voir schemas/customer.py), qui retourne l'un de trois formats :
  - ValidationOutput (step="validation")     : cas bloquant, retourné tel quel.
  - CustomerExtractionOutput (step="extraction") : sous-flux CREATE, terminal,
    comme AccountingExecutor — la persistance/vérification (section 16 du
    prompt) reste au backend, hors scope de cet exécuteur.
  - ToolSelectionOutput (step="tool_selection")  : sous-flux READ. Déjà
    obtenu ici (c'est le 1er appel), donc délégué DIRECTEMENT à
    ToolLoopExecutor.resolve() — jamais .execute()/_select_tools(), pour ne
    pas gaspiller une inférence Ollama (voir docstring de tool_loop_executor.py).

{contacts_correspondants} est injecté via find_matching_contacts(entreprise,
message_text) + format_contacts_correspondants() — recherche floue sur le
texte BRUT du message, calculée avant le 1er appel LLM puisque aucun nom n'a
encore été extrait à ce stade.

Construit le prompt par remplacement de texte (jamais PromptTemplate/.format()),
même raison que AccountingExecutor/RouterExecutor : accolades JSON littérales
dans les exemples du prompt.

COURT-CIRCUIT (ajouté après échec de 2 tentatives de fix côté prompt,
section 6 "IDENTIFICATION DU CONTACT") : le LLM retourne parfois
ValidationOutput(needs_clarification=False) même sans ambiguïté réelle,
au lieu d'enchaîner vers tool_selection comme prévu par l'architecture.
Dans ce cas précis (et uniquement celui-ci — needs_clarification=True
reste un vrai blocage retourné tel quel), on relance un appel
tool_selection propre via ToolLoopExecutor.execute()/aexecute(), qui
force output_schema=ToolSelectionOutput et empêche le LLM de "s'échapper"
à nouveau vers validation.
"""

import functools
import logging

from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.prompts.customer_prompt import CUSTOMER_PROMPT
from apps.femi_agent.agent.tool_loop_executor import ToolLoopExecutor
from apps.femi_agent.agent.tools.contacts import (
    find_matching_contacts,
    format_contacts_correspondants,
    get_contact_info,
    resolve_contact_for_read,
)
from apps.femi_agent.agent.tools.debts import get_all_open_debts, get_contact_open_debts
from apps.femi_agent.schemas.customer import CustomerExtractionOutput, CustomerFirstStepOutput
from apps.femi_agent.schemas.tool_loop import FinalAnswerOutput, ToolSelectionOutput, ValidationOutput

logger = logging.getLogger(__name__)

# Prompt volumineux (23 sections, nombreux exemples JSON) — même précaution de
# contexte que ACCOUNTING_PROMPT/ROUTER_PROMPT.
CUSTOMER_NUM_CTX = 16384

CustomerFirstResult = ValidationOutput | CustomerExtractionOutput | ToolSelectionOutput | FinalAnswerOutput


class CustomerExecutionError(BaseAgentExecutionError):
    """Exception personnalisée encapsulant les échecs d'exécution de l'agent CUSTOMER."""
    pass


class CustomerExecutor:
    """
    Exécuteur de CUSTOMER_PROMPT : 1er appel LLM polymorphe (validation /
    tool_selection / extraction), puis délégation à ToolLoopExecutor.resolve()
    pour le sous-flux READ uniquement.
    """

    @staticmethod
    def _build_prompt_text(entreprise, message_text: str) -> str:
        """Injecte {contacts_correspondants} (recherche floue sur le texte
        brut, voir docstring de module)."""
        contacts = find_matching_contacts(entreprise, message_text)
        return CUSTOMER_PROMPT.replace("{contacts_correspondants}", format_contacts_correspondants(contacts))

    @staticmethod
    def _build_tool_registry(entreprise) -> dict:
        """Lie entreprise (et résout contact par nom, jamais d'auto-création)
        via closures/functools.partial, pour que chaque callable soit prêt à
        être appelé avec uniquement les params du LLM (voir
        tool_loop_executor.py)."""

        def _open_debts_by_name(contact: str | None = None):
            return get_contact_open_debts(entreprise, resolve_contact_for_read(entreprise, contact))

        def _contact_info_by_name(contact: str | None = None):
            resolved = resolve_contact_for_read(entreprise, contact)
            if resolved is None:
                return {"success": False, "error": "contact introuvable"}
            return get_contact_info(entreprise, resolved)

        return {
            "get_all_open_debts": functools.partial(get_all_open_debts, entreprise),
            "get_contact_open_debts": _open_debts_by_name,
            "get_contact_info": _contact_info_by_name,
        }

    @classmethod
    def execute(cls, message_text: str, entreprise) -> CustomerFirstResult:
        """Exécution synchrone de l'agent CUSTOMER.

        Args:
            message_text: message utilisateur (segment routé vers CUSTOMER).
            entreprise: instance Entreprise du tenant.

        Returns:
            - ValidationOutput : cas bloquant (wrong_agent, contact_disambiguation, ...).
            - CustomerExtractionOutput : sous-flux CREATE, terminal.
            - ToolSelectionOutput : sous-flux READ avec needs_clarification=True
              (aucun tool exécuté).
            - FinalAnswerOutput : sous-flux READ résolu, réponse finale.
        """
        prompt_text = cls._build_prompt_text(entreprise, message_text)

        first_step: CustomerFirstStepOutput = StructuredLLMExecutor.execute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=CustomerFirstStepOutput,
            num_ctx=CUSTOMER_NUM_CTX,
            error_cls=CustomerExecutionError,
            log_prefix="CustomerExecutor",
        )
        result = first_step.result

        if isinstance(result, CustomerExtractionOutput):
            return result

        if isinstance(result, ValidationOutput):
            if result.needs_clarification:
                return result
            # Court-circuit : cf. docstring de module.
            logger.warning(
                "[CustomerExecutor] ValidationOutput sans needs_clarification, "
                "relance forcée d'un appel tool_selection (court-circuit)."
            )
            return ToolLoopExecutor.execute(
                prompt_text=prompt_text,
                message_text=message_text,
                tool_registry=cls._build_tool_registry(entreprise),
                num_ctx=CUSTOMER_NUM_CTX,
                log_prefix="CustomerExecutor",
            )

        if result.needs_clarification:
            return result

        return ToolLoopExecutor.resolve(
            selection=result,
            prompt_text=prompt_text,
            message_text=message_text,
            tool_registry=cls._build_tool_registry(entreprise),
            num_ctx=CUSTOMER_NUM_CTX,
            log_prefix="CustomerExecutor",
        )

    @classmethod
    async def aexecute(cls, message_text: str, entreprise) -> CustomerFirstResult:
        """Version asynchrone de execute() (mêmes arguments)."""
        prompt_text = cls._build_prompt_text(entreprise, message_text)

        first_step: CustomerFirstStepOutput = await StructuredLLMExecutor.aexecute(
            prompt_text=prompt_text,
            message_text=message_text,
            output_schema=CustomerFirstStepOutput,
            num_ctx=CUSTOMER_NUM_CTX,
            error_cls=CustomerExecutionError,
            log_prefix="CustomerExecutor",
        )
        result = first_step.result

        if isinstance(result, CustomerExtractionOutput):
            return result

        if isinstance(result, ValidationOutput):
            if result.needs_clarification:
                return result
            # Court-circuit : cf. docstring de module.
            logger.warning(
                "[CustomerExecutor] ValidationOutput sans needs_clarification, "
                "relance forcée d'un appel tool_selection (court-circuit)."
            )
            return await ToolLoopExecutor.aexecute(
                prompt_text=prompt_text,
                message_text=message_text,
                tool_registry=cls._build_tool_registry(entreprise),
                num_ctx=CUSTOMER_NUM_CTX,
                log_prefix="CustomerExecutor",
            )

        if result.needs_clarification:
            return result

        return await ToolLoopExecutor.aresolve(
            selection=result,
            prompt_text=prompt_text,
            message_text=message_text,
            tool_registry=cls._build_tool_registry(entreprise),
            num_ctx=CUSTOMER_NUM_CTX,
            log_prefix="CustomerExecutor",
        )