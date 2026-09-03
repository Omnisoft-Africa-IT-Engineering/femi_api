import logging
from decimal import Decimal
from langchain_core.prompts import ChatPromptTemplate
from apps.femi_agent.agent.llm import get_llm
from apps.femi_agent.schemas import LLMExtractionSchema, ParsedOperationSchema
from apps.femi_agent.agent.prompts.accounting_prompt import SYSTEM_FINANCIAL_EXTRACTION_PROMPT

logger = logging.getLogger(__name__)


class AgentExecutionError(Exception):
    """Levée quand l'agent ne parvient pas à produire une extraction valide."""
    pass


def _to_decimal(value):
    """Convertit un float en Decimal en évitant les artefacts de précision binaire."""
    if value is None:
        return None
    return Decimal(str(value))

def _clean_optional_str(value):
    """Nettoie les placeholders texte que le LLM peut renvoyer au lieu d'un vrai null
    (limitation connue de la grammaire GBNF d'Ollama sur les champs Optional[str])."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("null", "none", ""):
        return None
    return value

def _llm_result_to_parsed_operation(llm_result: LLMExtractionSchema) -> ParsedOperationSchema:
    """Convertit la sortie LLM (float) en schéma métier (Decimal)."""
    data = llm_result.model_dump()
    data["amount_ttc"] = _to_decimal(data["amount_ttc"])
    data["amount_ht"] = _to_decimal(data["amount_ht"])
    data["tax_amount"] = _to_decimal(data["tax_amount"])
    data["transaction_date"] = _clean_optional_str(data["transaction_date"])
    data["vendor_or_client"] = _clean_optional_str(data["vendor_or_client"])
    return ParsedOperationSchema(**data)


def get_femi_chain(model_name: str = None):
    """
    Construit la chaîne LangChain : prompt -> LLM -> sortie structurée Pydantic.
    Utilise LLMExtractionSchema (float) pour compatibilité Ollama grammar,
    avec retry sur les échecs transitoires.
    """
    llm = get_llm(model_name=model_name)
    structured_llm = llm.with_structured_output(LLMExtractionSchema)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_FINANCIAL_EXTRACTION_PROMPT),
        ("human", "Analyse le texte suivant : \n\n{input_text}"),
    ])
    chain = prompt | structured_llm
    return chain.with_retry(stop_after_attempt=3)


def analyze_accounting_text(text: str, model_name: str = None) -> ParsedOperationSchema:
    """
    Point d'entrée principal : analyse un texte et retourne une ParsedOperationSchema.
    Raises:
        AgentExecutionError: si le texte est vide ou si l'extraction échoue.
    """
    if not text or not text.strip():
        raise AgentExecutionError("Texte vide reçu, impossible d'analyser.")

    chain = get_femi_chain(model_name=model_name)
    try:
        llm_result: LLMExtractionSchema = chain.invoke({"input_text": text})
        result = _llm_result_to_parsed_operation(llm_result)
        logger.info(f"Extraction réussie : {result}")
        return result
    except Exception as e:
        logger.error(f"Échec extraction agent sur texte '{text[:50]}...' : {e}")
        raise AgentExecutionError(f"Impossible d'extraire les données comptables : {e}") from e
