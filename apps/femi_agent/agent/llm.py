import logging
from functools import lru_cache
from typing import Optional

from django.conf import settings
from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "mistral"
DEFAULT_BASE_URL = "http://localhost:11434"


@lru_cache(maxsize=8)
def get_llm(
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    timeout: float = 30.0,
    max_retries: int = 2,
) -> ChatOllama:
    """
    Factory sécurisée et mise en cache pour instancier ChatOllama.

    Le cache (lru_cache) évite de recréer une connexion à chaque appel :
    pour des paramètres identiques, la même instance est réutilisée.

    Args:
        model_name: Nom du modèle Ollama. Fallback sur settings.FEMI_LLM_MODEL.
        temperature: Doit être comprise entre 0.0 et 1.0.
        timeout: Timeout réseau en secondes.
        max_retries: Nombre de tentatives en cas d'échec réseau.

    Returns:
        Instance ChatOllama configurée.

    Raises:
        ValueError: si temperature est hors de [0.0, 1.0].
        RuntimeError: si la connexion au service LLM échoue.
    """
    if not 0.0 <= temperature <= 1.0:
        raise ValueError(f"temperature doit être entre 0.0 et 1.0, reçu: {temperature}")

    model_name = model_name or getattr(settings, "FEMI_LLM_MODEL", DEFAULT_MODEL)
    base_url = getattr(settings, "OLLAMA_BASE_URL", DEFAULT_BASE_URL)

    logger.debug(
        "[LLM Factory] Instanciation ChatOllama — Model: %s | URL: %s | Temp: %s | Timeout: %ss",
        model_name, base_url, temperature, timeout,
    )

    try:
        return ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )
    except (ConnectionError, TimeoutError, OSError) as e:
        logger.error("[LLM Factory] Échec réseau vers '%s' (%s): %s", model_name, base_url, e)
        raise RuntimeError(f"Impossible de se connecter au service LLM ({model_name}): {e}") from e
    except Exception as e:
        logger.exception("[LLM Factory] Erreur inattendue à l'initialisation de '%s'", model_name)
        raise RuntimeError(f"Erreur d'initialisation du LLM ({model_name}): {e}") from e