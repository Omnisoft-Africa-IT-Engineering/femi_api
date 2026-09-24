import logging
import os
import json
from functools import lru_cache
from typing import Optional

from django.conf import settings

from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "mistral"
DEFAULT_BASE_URL = "http://localhost:11434"

# Providers disponibles :
# - ollama
# - groq
# - mistral
# - cloudflare
# - gemini

DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
DEFAULT_MISTRAL_MODEL = "mistral-small-latest"
DEFAULT_CLOUDFLARE_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"


@lru_cache(maxsize=8)
def get_llm(
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    timeout: float = 30.0,
    max_retries: int = 2,
    num_ctx: Optional[int] = None,
):
    """
    Factory sécurisée et mise en cache pour instancier le LLM.

    Provider choisi via settings.FEMI_LLM_PROVIDER.

    Providers supportés :
        - ollama
        - groq
        - mistral
        - cloudflare
        - gemini

    num_ctx est utilisé uniquement par Ollama.

    Args:
        model_name: Nom du modèle.
        temperature: Doit être comprise entre 0.0 et 1.0.
        timeout: Timeout réseau en secondes.
        max_retries: Nombre de tentatives en cas d'échec réseau.
        num_ctx: Taille du contexte pour Ollama.

    Returns:
        Instance du LLM correspondant au provider sélectionné.

    Raises:
        ValueError: si temperature est hors de [0.0, 1.0],
            ou si FEMI_LLM_PROVIDER est inconnu.
        RuntimeError: si une clé API requise est absente
            ou si l'initialisation du LLM échoue.
    """

    if not 0.0 <= temperature <= 1.0:
        raise ValueError(
            f"temperature doit être entre 0.0 et 1.0, reçu: {temperature}"
        )

    provider = getattr(
        settings,
        "FEMI_LLM_PROVIDER",
        "ollama",
    ).lower()

    if provider == "groq":
        return _get_groq_llm(
            model_name,
            temperature,
            timeout,
            max_retries,
        )

    if provider == "mistral":
        return _get_mistral_llm(
            model_name,
            temperature,
            timeout,
            max_retries,
        )

    if provider == "cloudflare":
        return _get_cloudflare_llm(
            model_name,
            temperature,
            timeout,
            max_retries,
        )

    if provider == "gemini":
        return _get_gemini_llm(
            model_name,
            temperature,
            timeout,
            max_retries,
        )

    if provider != "ollama":
        raise ValueError(
            f"FEMI_LLM_PROVIDER inconnu : '{provider}' "
            "(valeurs valides : "
            "'ollama', 'groq', 'mistral', 'cloudflare', 'gemini')."
        )

    return _get_ollama_llm(
        model_name,
        temperature,
        timeout,
        max_retries,
        num_ctx,
    )


def _get_ollama_llm(
    model_name,
    temperature,
    timeout,
    max_retries,
    num_ctx,
):
    """Instancie ChatOllama (comportement historique)."""

    model_name = model_name or getattr(
        settings,
        "FEMI_LLM_MODEL",
        DEFAULT_MODEL,
    )

    base_url = getattr(
        settings,
        "OLLAMA_BASE_URL",
        DEFAULT_BASE_URL,
    )

    logger.debug(
        "[LLM Factory] Instanciation ChatOllama — "
        "Model: %s | URL: %s | Temp: %s | Timeout: %ss",
        model_name,
        base_url,
        temperature,
        timeout,
    )

    try:
        return ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
            num_ctx=num_ctx,
        )

    except (ConnectionError, TimeoutError, OSError) as e:
        logger.error(
            "[LLM Factory] Échec réseau vers '%s' (%s): %s",
            model_name,
            base_url,
            e,
        )
        raise RuntimeError(
            f"Impossible de se connecter au service LLM "
            f"({model_name}): {e}"
        ) from e

    except Exception as e:
        logger.exception(
            "[LLM Factory] Erreur inattendue à l'initialisation "
            "de '%s'",
            model_name,
        )
        raise RuntimeError(
            f"Erreur d'initialisation du LLM "
            f"({model_name}): {e}"
        ) from e


def _get_groq_llm(
    model_name,
    temperature,
    timeout,
    max_retries,
):
    """Instancie ChatGroq."""

    from langchain_groq import ChatGroq

    model_name = model_name or getattr(
        settings,
        "FEMI_GROQ_MODEL",
        DEFAULT_GROQ_MODEL,
    )

    api_key = (
        getattr(settings, "GROQ_API_KEY", None)
        or os.environ.get("GROQ_API_KEY")
    )

    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY manquant — définir la variable "
            "d'environnement ou settings.GROQ_API_KEY "
            "pour utiliser FEMI_LLM_PROVIDER='groq'."
        )

    logger.debug(
        "[LLM Factory] Instanciation ChatGroq — "
        "Model: %s | Temp: %s | Timeout: %ss",
        model_name,
        temperature,
        timeout,
    )

    try:
        return ChatGroq(
            model=model_name,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )

    except Exception as e:
        logger.exception(
            "[LLM Factory] Erreur inattendue à "
            "l'initialisation Groq de '%s'",
            model_name,
        )
        raise RuntimeError(
            f"Erreur d'initialisation du LLM Groq "
            f"({model_name}): {e}"
        ) from e


def _get_mistral_llm(
    model_name,
    temperature,
    timeout,
    max_retries,
):
    """Instancie ChatMistralAI."""

    from langchain_mistralai import ChatMistralAI

    model_name = model_name or getattr(
        settings,
        "FEMI_MISTRAL_MODEL",
        DEFAULT_MISTRAL_MODEL,
    )

    api_key = (
        getattr(settings, "MISTRAL_API_KEY", None)
        or os.environ.get("MISTRAL_API_KEY")
    )

    if not api_key:
        raise RuntimeError(
            "MISTRAL_API_KEY manquant — définir la variable "
            "d'environnement ou settings.MISTRAL_API_KEY "
            "pour utiliser FEMI_LLM_PROVIDER='mistral'."
        )

    logger.debug(
        "[LLM Factory] Instanciation ChatMistralAI — "
        "Model: %s | Temp: %s | Timeout: %ss",
        model_name,
        temperature,
        timeout,
    )

    try:
        return ChatMistralAI(
            model=model_name,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )

    except Exception as e:
        logger.exception(
            "[LLM Factory] Erreur inattendue à "
            "l'initialisation Mistral de '%s'",
            model_name,
        )
        raise RuntimeError(
            f"Erreur d'initialisation du LLM Mistral "
            f"({model_name}): {e}"
        ) from e


def _get_gemini_llm(
    model_name,
    temperature,
    timeout,
    max_retries,
):
    """Instancie ChatGoogleGenerativeAI pour Gemini."""

    from langchain_google_genai import ChatGoogleGenerativeAI

    model_name = model_name or getattr(
        settings,
        "FEMI_GEMINI_MODEL",
        DEFAULT_GEMINI_MODEL,
    )

    api_key = (
        getattr(settings, "GEMINI_API_KEY", None)
        or os.environ.get("GEMINI_API_KEY")
    )

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY manquant — définir la variable "
            "d'environnement ou settings.GEMINI_API_KEY "
            "pour utiliser FEMI_LLM_PROVIDER='gemini'."
        )

    logger.debug(
        "[LLM Factory] Instanciation "
        "ChatGoogleGenerativeAI — "
        "Model: %s | Temp: %s | Timeout: %ss",
        model_name,
        temperature,
        timeout,
    )

    try:
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )

    except Exception as e:
        logger.exception(
            "[LLM Factory] Erreur inattendue à "
            "l'initialisation Gemini de '%s'",
            model_name,
        )
        raise RuntimeError(
            f"Erreur d'initialisation du LLM Gemini "
            f"({model_name}): {e}"
        ) from e


def _get_cloudflare_llm(
    model_name,
    temperature,
    timeout,
    max_retries,
):
    """Instancie ChatOpenAI pointé sur Cloudflare Workers AI."""

    from langchain_openai import ChatOpenAI

    model_name = model_name or getattr(
        settings,
        "FEMI_CLOUDFLARE_MODEL",
        DEFAULT_CLOUDFLARE_MODEL,
    )

    account_id = (
        getattr(settings, "CLOUDFLARE_ACCOUNT_ID", None)
        or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    )

    api_key = (
        getattr(settings, "CLOUDFLARE_API_KEY", None)
        or os.environ.get("CLOUDFLARE_API_KEY")
    )

    if not account_id or not api_key:
        raise RuntimeError(
            "CLOUDFLARE_ACCOUNT_ID et/ou "
            "CLOUDFLARE_API_KEY manquant(s) — "
            "définir ces variables d'environnement "
            "ou les settings correspondants pour utiliser "
            "FEMI_LLM_PROVIDER='cloudflare'."
        )

    base_url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/v1"
    )

    logger.debug(
        "[LLM Factory] Instanciation "
        "ChatOpenAI/Cloudflare — "
        "Model: %s | Temp: %s | Timeout: %ss",
        model_name,
        temperature,
        timeout,
    )

    try:
        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
            max_tokens=4096,
        )

    except Exception as e:
        logger.exception(
            "[LLM Factory] Erreur inattendue à "
            "l'initialisation Cloudflare de '%s'",
            model_name,
        )
        raise RuntimeError(
            f"Erreur d'initialisation du LLM Cloudflare "
            f"({model_name}): {e}"
        ) from e


def call_cloudflare_native_vision(
    model_name: str,
    prompt_text: str,
    message_text: str,
    image_b64: str,
) -> str:
    """
    Appelle l'API native Cloudflare Workers AI
    (/ai/run/..., PAS la couche compatible OpenAI)
    pour les modèles vision.

    Retourne le texte brut de result.response.
    """

    import requests

    account_id = (
        getattr(settings, "CLOUDFLARE_ACCOUNT_ID", None)
        or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    )

    api_key = (
        getattr(settings, "CLOUDFLARE_API_KEY", None)
        or os.environ.get("CLOUDFLARE_API_KEY")
    )

    if not account_id or not api_key:
        raise RuntimeError(
            "CLOUDFLARE_ACCOUNT_ID et/ou "
            "CLOUDFLARE_API_KEY manquant(s)."
        )

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/run/{model_name}"
    )

    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "messages": [
            {
                "role": "system",
                "content": prompt_text,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            message_text
                            + "\n\nRéponds UNIQUEMENT avec "
                            "l'objet JSON demandé, sans aucun "
                            "texte avant ou après, sans "
                            "description de l'image."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:image/png;base64,"
                                f"{image_b64}"
                            )
                        },
                    },
                ],
            },
        ],
        "max_tokens": 2048,
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=60,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("success"):
        raise RuntimeError(
            f"Échec API native Cloudflare : "
            f"{data.get('errors')}"
        )

    response = data["result"]["response"]

    if isinstance(response, dict):
        return json.dumps(
            response,
            ensure_ascii=False,
        )

    return response