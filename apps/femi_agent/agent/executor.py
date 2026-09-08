import json
import logging
import re
from functools import lru_cache

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import ValidationError

from apps.femi_agent.agent.llm import get_llm
from apps.femi_agent.agent.prompts.accounting_prompt import ACCOUNTING_PROMPT
from apps.femi_agent.schemas import LLMExtractionSchema, ParsedOperationSchema

logger = logging.getLogger(__name__)

LOG_TEXT_PREVIEW_LEN = 80

# Valeur de repli utilisée quand aucun nom d'entreprise n'est transmis par
# l'appelant (manager.py) — permet au prompt de rester cohérent même en
# l'absence de contexte tenant (ex: appel direct en test/debug).
DEFAULT_TENANT_NAME = "Non spécifié"

# Suffixe utilisé UNIQUEMENT par le chemin de secours (_fallback_parse), qui
# repose sur un parsing JSON manuel plutôt que sur le structured output natif.
_FALLBACK_FORMAT_SUFFIX = (
    "\n\n### FORMAT DE SORTIE (STRICT)\n"
    "Réponds uniquement avec un JSON valide, sans texte autour :\n"
    "{format_instructions}\n"
)


class AgentExecutionError(Exception):
    """Exception personnalisée encapsulant les échecs d'exécution de l'agent LLM/LangChain."""
    pass


class AgentExecutor:
    """
    Exécuteur LCEL pour l'extraction de transactions comptables via LangChain.

    Chemin principal : `with_structured_output()` (function calling / JSON mode
    natif du modèle) — norme actuelle recommandée par LangChain.

    Chemin de secours (`_fallback_parse`) : ancien mécanisme par
    PydanticOutputParser + extraction JSON regex, conservé pour les cas où le
    modèle ne respecte pas correctement le structured output natif.

    Le nom de l'entreprise (`tenant_name`) est transmis à chaque invocation
    (et non figé via `.partial()`) car il varie selon l'entreprise qui envoie
    le message — contrairement à `format_instructions`, qui est statique.
    """

    @staticmethod
    @lru_cache(maxsize=1)
    def _get_base_prompt():
        """
        Prompt de base, mis en cache. Contient {input} et {tenant_name} —
        c'est `with_structured_output()` qui impose le schéma au modèle, plus
        besoin d'instructions de formatage dans le prompt principal.
        """
        if isinstance(ACCOUNTING_PROMPT, str):
            return PromptTemplate.from_template(ACCOUNTING_PROMPT)
        return ACCOUNTING_PROMPT

    @staticmethod
    @lru_cache(maxsize=1)
    def _get_structured_llm():
        """
        LLM lié au schéma Pydantic via l'API structured output native de LangChain.

        ⚠️ Nécessite que le modèle Ollama utilisé supporte le tool-calling / JSON
        mode. Validé empiriquement avec `mistral` (voir tests de session).
        """
        llm = get_llm()
        return llm.with_structured_output(LLMExtractionSchema)

    @classmethod
    def _build_chain(cls):
        """Construit la chaîne LCEL principale (prompt | llm structuré)."""
        return cls._get_base_prompt() | cls._get_structured_llm()

    @classmethod
    def execute(cls, text_input: str, tenant_name: str | None = None) -> ParsedOperationSchema:
        """Exécution synchrone via structured output natif.

        Args:
            text_input: Texte brut à analyser (message utilisateur, OCR, ou transcription).
            tenant_name: Nom de l'entreprise de l'utilisateur, utilisé pour lever
                l'ambiguïté RECETTE/DEPENSE (ex: distinguer un achat d'une vente
                sur une facture qui mentionne deux entreprises). Optionnel.
        """
        resolved_tenant_name = tenant_name or DEFAULT_TENANT_NAME
        try:
            chain = cls._build_chain()

            logger.debug(
                "[AgentExecutor] Analyse synchrone LLM pour : '%s%s'",
                text_input[:LOG_TEXT_PREVIEW_LEN],
                "..." if len(text_input) > LOG_TEXT_PREVIEW_LEN else "",
            )
            raw_result: LLMExtractionSchema = chain.invoke({
                "input": text_input,
                "tenant_name": resolved_tenant_name,
            })

            return cls._map_to_processed_schema(raw_result)

        except (OutputParserException, ValidationError) as parse_err:
            logger.warning(
                "[AgentExecutor] Structured output invalide, passage au fallback JSON manuel : %s", parse_err
            )
            return cls._fallback_parse(text_input, resolved_tenant_name)
        except Exception as e:
            logger.exception("[AgentExecutor] Échec critique de l'agent")
            raise AgentExecutionError(f"Erreur d'exécution de l'agent : {e}") from e

    @classmethod
    async def aexecute(cls, text_input: str, tenant_name: str | None = None) -> ParsedOperationSchema:
        """Exécution asynchrone via structured output natif.

        Args:
            text_input: Texte brut à analyser (message utilisateur, OCR, ou transcription).
            tenant_name: Nom de l'entreprise de l'utilisateur, utilisé pour lever
                l'ambiguïté RECETTE/DEPENSE. Optionnel.
        """
        resolved_tenant_name = tenant_name or DEFAULT_TENANT_NAME
        try:
            chain = cls._build_chain()

            logger.debug(
                "[AgentExecutor] Analyse asynchrone LLM pour : '%s%s'",
                text_input[:LOG_TEXT_PREVIEW_LEN],
                "..." if len(text_input) > LOG_TEXT_PREVIEW_LEN else "",
            )
            raw_result: LLMExtractionSchema = await chain.ainvoke({
                "input": text_input,
                "tenant_name": resolved_tenant_name,
            })

            return cls._map_to_processed_schema(raw_result)

        except (OutputParserException, ValidationError) as parse_err:
            logger.warning(
                "[AgentExecutor] Structured output invalide (async), passage au fallback JSON manuel : %s", parse_err
            )
            return cls._fallback_parse(text_input, resolved_tenant_name)
        except Exception as e:
            logger.exception("[AgentExecutor] Échec critique asynchrone de l'agent")
            raise AgentExecutionError(f"Erreur d'exécution asynchrone de l'agent : {e}") from e

    @classmethod
    def _fallback_parse(cls, text_input: str, tenant_name: str | None = None) -> ParsedOperationSchema:
        """
        Ancien mécanisme de secours : injecte manuellement les instructions de
        format Pydantic dans le prompt et parse la réponse texte du LLM par
        extraction JSON regex. Déclenché uniquement si `with_structured_output()`
        échoue à produire une sortie valide.
        """
        resolved_tenant_name = tenant_name or DEFAULT_TENANT_NAME
        try:
            llm = get_llm()
            parser = PydanticOutputParser(pydantic_object=LLMExtractionSchema)

            base_text = ACCOUNTING_PROMPT if isinstance(ACCOUNTING_PROMPT, str) else ACCOUNTING_PROMPT.template
            fallback_prompt = PromptTemplate.from_template(base_text + _FALLBACK_FORMAT_SUFFIX)
            fallback_prompt = fallback_prompt.partial(format_instructions=parser.get_format_instructions())

            raw_response = (fallback_prompt | llm).invoke({
                "input": text_input,
                "tenant_name": resolved_tenant_name,
            })
            content = raw_response.content if hasattr(raw_response, "content") else str(raw_response)

            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                extracted = LLMExtractionSchema(**data)
                return cls._map_to_processed_schema(extracted)
        except Exception:
            logger.exception("[AgentExecutor] Le fallback JSON manuel a échoué")

        raise AgentExecutionError(
            f"Impossible d'extraire des données comptables valides du texte "
            f"('{text_input[:LOG_TEXT_PREVIEW_LEN]}...')"
        )

    @staticmethod
    def _map_to_processed_schema(raw_schema: LLMExtractionSchema) -> ParsedOperationSchema:
        """Convertit l'extraction LLM (float) vers le schéma comptable strict (Decimal)."""
        if hasattr(raw_schema, "to_parsed_schema"):
            return raw_schema.to_parsed_schema()

        return ParsedOperationSchema(
            transaction_type=getattr(raw_schema, "transaction_type", "RECETTE"),
            amount_ttc=getattr(raw_schema, "amount_ttc", None),
            currency=getattr(raw_schema, "currency", "XOF") or "XOF",
            category=getattr(raw_schema, "category", "Autre") or "Autre",
            vendor_or_client=getattr(raw_schema, "vendor_or_client", None),
            payment_method=getattr(raw_schema, "payment_method", "CASH") or "CASH",
            transaction_date=getattr(raw_schema, "transaction_date", None),
            description=getattr(raw_schema, "description", "Transaction non décrite"),
            confidence_score=getattr(raw_schema, "confidence_score", 0.85),
        )


# --- Alias de compatibilité requis par pipeline.py ---

def analyze_accounting_text(text_input: str, tenant_name: str | None = None) -> ParsedOperationSchema:
    return AgentExecutor.execute(text_input, tenant_name)


async def aanalyze_accounting_text(text_input: str, tenant_name: str | None = None) -> ParsedOperationSchema:
    return await AgentExecutor.aexecute(text_input, tenant_name)