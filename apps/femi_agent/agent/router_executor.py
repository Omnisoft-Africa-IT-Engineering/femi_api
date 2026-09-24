"""Exécuteur du ROUTER_PROMPT (Point 7 — branchement du Router).

Même logique générale que apps/femi_agent/agent/executor.py (AgentExecutor) :
chemin principal via with_structured_output(), fallback JSON manuel en secours.

Différence assumée : ROUTER_PROMPT contient de nombreux blocs JSON d'exemple
avec des accolades littérales (sections 22-23), incompatibles avec
PromptTemplate/str.format(). L'injection des variables du prompt se fait donc
ici par remplacement de texte simple (.replace()), jamais par formatage.
"""

import json
import logging
import re

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import ValidationError

from apps.femi_agent.agent.llm import get_llm
from apps.femi_agent.agent.prompts.router_prompt import ROUTER_PROMPT
from apps.femi_agent.schemas import RouterOutput

logger = logging.getLogger(__name__)

LOG_TEXT_PREVIEW_LEN = 80

# Valeurs de repli si l'appelant ne fournit pas de contexte conversationnel.
DEFAULT_HISTORY = "(aucun historique)"
DEFAULT_PENDING_ACTION = "null"
DEFAULT_MISSING_FIELDS = "[]"
DEFAULT_STT_FLAGS = "[]"

_FALLBACK_FORMAT_SUFFIX = (
    "\n\n### FORMAT DE SORTIE (STRICT)\n"
    "Réponds uniquement avec un JSON valide, sans texte autour :\n"
    "{format_instructions}\n"
)


class RouterExecutionError(Exception):
    """Exception personnalisée encapsulant les échecs d'exécution du Router LLM."""
    pass


class RouterExecutor:
    """
    Exécuteur du Router : construit le prompt par remplacement de texte
    (jamais par PromptTemplate/.format(), à cause des accolades JSON
    littérales du prompt), puis appelle le LLM avec sortie structurée
    contrainte au schéma RouterOutput.
    """

    @staticmethod
    def _build_prompt_text(
        history: str,
        pending_action: str,
        missing_fields: str,
        stt_flags: str,
    ) -> str:
        """Injecte les 4 variables du Router dans le texte brut du prompt."""
        return (
            ROUTER_PROMPT
            .replace("{history}", history)
            .replace("{pending_action}", pending_action)
            .replace("{missing_fields}", missing_fields)
            .replace("{stt_flags}", stt_flags)
        )

       # Le prompt système du Router (~9500 tokens) dépasse largement le
    # contexte par défaut d'Ollama (2048) — d'où num_ctx explicite ici,
    # sans toucher au défaut partagé par l'ancien pipeline (voir llm.py).
    ROUTER_NUM_CTX = 8192

    @classmethod
    def _get_structured_llm(cls):
        llm = get_llm(num_ctx=cls.ROUTER_NUM_CTX)
        return llm.with_structured_output(RouterOutput)

    @classmethod
    def execute(
        cls,
        message_text: str,
        history: str | None = None,
        pending_action: str | None = None,
        missing_fields: str | None = None,
        stt_flags: str | None = None,
    ) -> RouterOutput:
        """Exécution synchrone du Router.

        Args:
            message_text: Message utilisateur (ou texte corrigé par STT_PROMPT) à router.
            history: Historique conversationnel récent, déjà formaté en texte. Optionnel.
            pending_action: État de l'action en attente (JSON en texte, ou "null"). Optionnel.
            missing_fields: Champs manquants de l'action en attente (JSON en texte). Optionnel.
            stt_flags: Segments incertains signalés par STT_PROMPT (JSON en texte). Optionnel.
        """
        prompt_text = cls._build_prompt_text(
            history or DEFAULT_HISTORY,
            pending_action or DEFAULT_PENDING_ACTION,
            missing_fields or DEFAULT_MISSING_FIELDS,
            stt_flags or DEFAULT_STT_FLAGS,
        )
        try:
            structured_llm = cls._get_structured_llm()
            logger.debug(
                "[RouterExecutor] Routage synchrone pour : '%s%s'",
                message_text[:LOG_TEXT_PREVIEW_LEN],
                "..." if len(message_text) > LOG_TEXT_PREVIEW_LEN else "",
            )
            messages = [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": message_text},
            ]
            result: RouterOutput = structured_llm.invoke(messages)
            return result

        except (OutputParserException, ValidationError) as parse_err:
            logger.warning(
                "[RouterExecutor] Structured output invalide, passage au fallback JSON manuel : %s", parse_err
            )
            return cls._fallback_parse(prompt_text, message_text)
        except Exception as e:
            logger.exception("[RouterExecutor] Échec critique du Router")
            raise RouterExecutionError(f"Erreur d'exécution du Router : {e}") from e

    @classmethod
    async def aexecute(
        cls,
        message_text: str,
        history: str | None = None,
        pending_action: str | None = None,
        missing_fields: str | None = None,
        stt_flags: str | None = None,
    ) -> RouterOutput:
        """Exécution asynchrone du Router (mêmes arguments que execute())."""
        prompt_text = cls._build_prompt_text(
            history or DEFAULT_HISTORY,
            pending_action or DEFAULT_PENDING_ACTION,
            missing_fields or DEFAULT_MISSING_FIELDS,
            stt_flags or DEFAULT_STT_FLAGS,
        )
        try:
            structured_llm = cls._get_structured_llm()
            logger.debug(
                "[RouterExecutor] Routage asynchrone pour : '%s%s'",
                message_text[:LOG_TEXT_PREVIEW_LEN],
                "..." if len(message_text) > LOG_TEXT_PREVIEW_LEN else "",
            )
            messages = [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": message_text},
            ]
            result: RouterOutput = await structured_llm.ainvoke(messages)
            return result

        except (OutputParserException, ValidationError) as parse_err:
            logger.warning(
                "[RouterExecutor] Structured output invalide (async), passage au fallback JSON manuel : %s", parse_err
            )
            return cls._fallback_parse(prompt_text, message_text)
        except Exception as e:
            logger.exception("[RouterExecutor] Échec critique asynchrone du Router")
            raise RouterExecutionError(f"Erreur d'exécution asynchrone du Router : {e}") from e

    @classmethod
    def _fallback_parse(cls, prompt_text: str, message_text: str) -> RouterOutput:
        """
        Secours : injecte des instructions de format Pydantic supplémentaires
        et parse la réponse texte du LLM par extraction JSON regex. Déclenché
        uniquement si with_structured_output() échoue à produire une sortie
        valide.
        """
        try:
            llm = get_llm(num_ctx=cls.ROUTER_NUM_CTX)
            parser = PydanticOutputParser(pydantic_object=RouterOutput)
            full_prompt = prompt_text + _FALLBACK_FORMAT_SUFFIX.format(
                format_instructions=parser.get_format_instructions()
            )
            messages = [
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": message_text},
            ]
            raw_response = llm.invoke(messages)
            content = raw_response.content if hasattr(raw_response, "content") else str(raw_response)

            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                return RouterOutput(**data)
        except Exception:
            logger.exception("[RouterExecutor] Le fallback JSON manuel a échoué")

        raise RouterExecutionError(
            f"Impossible d'obtenir un routage valide pour le message "
            f"('{message_text[:LOG_TEXT_PREVIEW_LEN]}...')"
        )