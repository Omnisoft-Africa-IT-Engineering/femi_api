import logging
from functools import lru_cache
from langchain_ollama import ChatOllama
from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def get_llm(model_name: str = None, temperature: float = 0.0) -> ChatOllama:
    """
    Retourne une instance ChatOllama, mise en cache par (model_name, temperature).
    Le cache évite de recréer une connexion à chaque appel.
    """
    model_name = model_name or getattr(settings, "FEMI_LLM_MODEL", "mistral")
    base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    logger.info(f"Initialisation ChatOllama — model={model_name}, temp={temperature}")

    return ChatOllama(
        model=model_name,
        base_url=base_url,
        temperature=temperature,
    )
