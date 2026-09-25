"""Exécuteur générique pour les prompts spécialisés du nouveau système
multi-agent (ACCOUNTING, FINANCIAL_ANALYST, CUSTOMER, OCR).

Factorise ce qui est commun à RouterExecutor (with_structured_output() en
chemin principal, fallback JSON manuel, gestion d'erreurs) pour éviter de
dupliquer cette logique dans chaque exécuteur d'agent.

Convention : chaque agent construit son propre prompt_text en amont
(injection de ses variables spécifiques via .replace(), jamais via
PromptTemplate/.format() à cause des accolades JSON littérales des
exemples) puis délègue l'appel LLM + parsing à cette classe.

Support multimodal (images) : ajouté pour OcrExecutor (OCR_PROMPT, agent
vision). Quand `images` est fourni (liste de chaînes base64, sans préfixe
data URI), le message "user" est construit au format multimodal attendu par
langchain_ollama (content = liste de blocs {"type": "text"/"image_url"}) —
voir langchain_ollama/chat_models.py, _convert_messages_to_ollama_messages.
Quand `images` est None (comportement historique, tous les autres agents),
le message "user" reste une simple chaîne de texte, inchangé.
"""

import json
import logging
import re
from django.conf import settings
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, ValidationError

from apps.femi_agent.agent.llm import get_llm

logger = logging.getLogger(__name__)

LOG_TEXT_PREVIEW_LEN = 80


def _resolve_vision_provider() -> str:
    """Provider utilisé pour les appels vision (images non vides).

    Isolé de settings.FEMI_LLM_PROVIDER via un réglage dédié
    (FEMI_VISION_PROVIDER), pour pouvoir changer le moteur de l'OCR
    (ex. passer sur Gemini) sans toucher au provider des agents texte
    (Router, Comptabilité, Analyste financier...). Si non défini,
    retombe sur FEMI_LLM_PROVIDER (comportement historique inchangé).
    """
    return getattr(
        settings, "FEMI_VISION_PROVIDER", None
    ) or getattr(settings, "FEMI_LLM_PROVIDER", "ollama")

_FALLBACK_FORMAT_SUFFIX = (
    "\n\n### FORMAT DE SORTIE (STRICT)\n"
    "Réponds uniquement avec un JSON valide, sans texte autour :\n"
    "{format_instructions}\n"
)


class BaseAgentExecutionError(Exception):
    """Exception générique pour un échec d'exécution d'un agent LLM structuré."""
    pass


class StructuredLLMExecutor:
    """
    Exécuteur générique : appelle le LLM avec sortie structurée contrainte
    à un schéma Pydantic donné, avec fallback JSON manuel en secours.
    Ne connaît ni le contenu du prompt ni le domaine métier de l'agent.
    """

    @staticmethod
    def _build_user_content(message_text: str, images: list[str] | None):
        """Construit le contenu du message "user" : texte simple si aucune
        image, sinon liste multimodale (texte + images en base64)."""
        if not images:
            return message_text
        content_parts: list[dict] = [{"type": "text", "text": message_text}]
        for image_b64 in images:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            })
        return content_parts

    @staticmethod
    def _coerce_numbers_to_str(obj):
        """Convertit récursivement les int/float en str — le mode JSON natif
        de Cloudflare renvoie des types numériques natifs, alors que les
        schémas du projet (ex: OcrTotauxSchema) attendent des chaînes."""
        if isinstance(obj, dict):
            return {k: StructuredLLMExecutor._coerce_numbers_to_str(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [StructuredLLMExecutor._coerce_numbers_to_str(v) for v in obj]
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            return str(obj)
        return obj


    @staticmethod
    def _count_filled_fields(obj) -> int:
        """Compte récursivement les valeurs non vides/non None — utilisé
        pour comparer la complétude de plusieurs essais du contournement
        Cloudflare vision (voir _execute_cloudflare_native_vision)."""
        if isinstance(obj, dict):
            return sum(StructuredLLMExecutor._count_filled_fields(v) for v in obj.values())
        if isinstance(obj, list):
            return sum(StructuredLLMExecutor._count_filled_fields(v) for v in obj)
        if obj is None or obj == "" or obj == {}:
            return 0
        return 1
    @staticmethod
    def _execute_cloudflare_native_vision(
        prompt_text: str, message_text: str, output_schema, images: list[str],
        error_cls: type[Exception], log_prefix: str, model_name: str | None,
    ) -> BaseModel:
        """Contournement pour le provider cloudflare + images : la couche
        compatible OpenAI (/ai/v1, utilisée par get_llm()) échoue avec
        l'entrée image_url pour les modèles vision (Internal Server Error
        3030, voir session du 22/09) — on appelle l'API native à la place.
        Généralisé ici (pas seulement OcrExecutor) pour tout futur agent
        vision passant par StructuredLLMExecutor avec provider=cloudflare."""
        from apps.femi_agent.agent.llm import call_cloudflare_native_vision, DEFAULT_CLOUDFLARE_MODEL

        parser = PydanticOutputParser(pydantic_object=output_schema)
        full_prompt = prompt_text + "\n\n" + parser.get_format_instructions()

        MAX_ATTEMPTS = 3
        best_result = None
        best_score = -1

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw_text = call_cloudflare_native_vision(
                    model_name=model_name or getattr(settings, "FEMI_CLOUDFLARE_MODEL", DEFAULT_CLOUDFLARE_MODEL),
                    prompt_text=full_prompt,
                    message_text=message_text,
                    image_b64=images[0],
                )
                json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
                if not json_match:
                    logger.warning(
                        "[%s] Essai %d/%d : aucun JSON trouvé dans la réponse", log_prefix, attempt, MAX_ATTEMPTS
                    )
                    continue

                data = json.loads(json_match.group(0))
                data = StructuredLLMExecutor._coerce_numbers_to_str(data)
                result = output_schema(**data)

                # Score = nombre de champs non vides/non None (récursif) —
                # sert à garder le meilleur essai plutôt que le premier,
                # face à l'instabilité observée du modèle vision Cloudflare
                # (session du 22/09 : 3 essais sur la même image ont donné
                # 3 résultats très différents en complétude).
                score = StructuredLLMExecutor._count_filled_fields(result.model_dump())
                logger.info("[%s] Essai %d/%d : score de complétude = %d", log_prefix, attempt, MAX_ATTEMPTS, score)

                if score > best_score:
                    best_result = result
                    best_score = score

                if score >= 5:  # seuil empirique : résultat jugé suffisamment complet, pas la peine de continuer
                    break

            except Exception:
                logger.warning("[%s] Essai %d/%d échoué", log_prefix, attempt, MAX_ATTEMPTS, exc_info=True)
                continue

        if best_result is not None:
            return best_result

        logger.exception("[%s] Échec critique (API native Cloudflare vision, %d essais)", log_prefix, MAX_ATTEMPTS)
        raise error_cls(f"Erreur d'exécution ({log_prefix}) : aucun essai n'a produit de résultat exploitable après {MAX_ATTEMPTS} tentatives")
    @staticmethod
    def _structured_output_kwargs(llm) -> dict:
        """Certains providers (ex. Groq avec openai/gpt-oss-*) échouent avec
        method="function_calling" (défaut de LangChain) — "Tool choice is
        required, but model did not call a tool". method="json_schema" est
        explicitement supporté pour openai/gpt-oss-*, moonshotai/kimi-k2,
        certains meta-llama/llama-4 (langchain-groq >= 0.3.8). Ollama
        (ChatOllama) n'est pas concerné, comportement inchangé."""
        try:
            from langchain_groq import ChatGroq
            if isinstance(llm, ChatGroq):
                return {"method": "json_schema"}
        except ImportError:
            pass
        return {}
    @staticmethod
    def execute(
        prompt_text: str,
        message_text: str,
        output_schema: type[BaseModel],
        num_ctx: int,
        error_cls: type[Exception] = BaseAgentExecutionError,
        log_prefix: str = "StructuredLLMExecutor",
        images: list[str] | None = None,
        model_name: str | None = None,
    ) -> BaseModel:
        """Exécution synchrone générique.

        Args:
            images: Liste optionnelle d'images en base64 (sans préfixe data
                URI). Si fourni, le message "user" est construit au format
                multimodal. Optionnel — absent pour tous les agents texte.
            model_name: Nom du modèle Ollama à utiliser, transmis à
                get_llm(). Optionnel — si absent, get_llm() applique son
                propre repli (settings.FEMI_LLM_MODEL).
        """

        vision_provider = _resolve_vision_provider().lower() if images else None

        if images and vision_provider == "cloudflare":
            return StructuredLLMExecutor._execute_cloudflare_native_vision(
                prompt_text, message_text, output_schema, images, error_cls, log_prefix, model_name
            )
        try:
            llm = get_llm(
                model_name=model_name,
                num_ctx=num_ctx,
                provider=vision_provider if images else None,
            )
            structured_llm = llm.with_structured_output(output_schema, **StructuredLLMExecutor._structured_output_kwargs(llm))
            logger.debug(
                "[%s] Appel pour : '%s%s'",
                log_prefix,
                message_text[:LOG_TEXT_PREVIEW_LEN],
                "..." if len(message_text) > LOG_TEXT_PREVIEW_LEN else "",
            )
            messages = [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": StructuredLLMExecutor._build_user_content(message_text, images)},
            ]
            return structured_llm.invoke(messages)

        except (OutputParserException, ValidationError) as parse_err:
            logger.warning(
                "[%s] Structured output invalide, passage au fallback JSON manuel : %s",
                log_prefix, parse_err,
            )
            return StructuredLLMExecutor._fallback_parse(
                prompt_text, message_text, output_schema, num_ctx, error_cls, log_prefix, images, model_name
            )
        except Exception as e:
            logger.exception("[%s] Échec critique", log_prefix)
            raise error_cls(f"Erreur d'exécution ({log_prefix}) : {e}") from e

    @staticmethod
    async def aexecute(
        prompt_text: str,
        message_text: str,
        output_schema: type[BaseModel],
        num_ctx: int,
        error_cls: type[Exception] = BaseAgentExecutionError,
        log_prefix: str = "StructuredLLMExecutor",
        images: list[str] | None = None,
        model_name: str | None = None,
    ) -> BaseModel:
        """Exécution asynchrone générique (mêmes arguments que execute())."""
        vision_provider = _resolve_vision_provider().lower() if images else None
        try:
            llm = get_llm(
                model_name=model_name,
                num_ctx=num_ctx,
                provider=vision_provider if images else None,
            )
            structured_llm = llm.with_structured_output(output_schema, **StructuredLLMExecutor._structured_output_kwargs(llm))
            logger.debug(
                "[%s] Appel async pour : '%s%s'",
                log_prefix,
                message_text[:LOG_TEXT_PREVIEW_LEN],
                "..." if len(message_text) > LOG_TEXT_PREVIEW_LEN else "",
            )
            messages = [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": StructuredLLMExecutor._build_user_content(message_text, images)},
            ]
            return await structured_llm.ainvoke(messages)

        except (OutputParserException, ValidationError) as parse_err:
            logger.warning(
                "[%s] Structured output invalide (async), passage au fallback JSON manuel : %s",
                log_prefix, parse_err,
            )
            return StructuredLLMExecutor._fallback_parse(
                prompt_text, message_text, output_schema, num_ctx, error_cls, log_prefix, images, model_name
            )
        except Exception as e:
            logger.exception("[%s] Échec critique asynchrone", log_prefix)
            raise error_cls(f"Erreur d'exécution asynchrone ({log_prefix}) : {e}") from e

    @staticmethod
    def _fallback_parse(
        prompt_text: str,
        message_text: str,
        output_schema: type[BaseModel],
        num_ctx: int,
        error_cls: type[Exception],
        log_prefix: str,
        images: list[str] | None = None,
        model_name: str | None = None,
    ) -> BaseModel:
        """Secours : parsing JSON manuel si with_structured_output() échoue."""
        vision_provider = _resolve_vision_provider().lower() if images else None
        try:
            llm = get_llm(
                model_name=model_name,
                num_ctx=num_ctx,
                provider=vision_provider if images else None,
            )
            parser = PydanticOutputParser(pydantic_object=output_schema)
            full_prompt = prompt_text + _FALLBACK_FORMAT_SUFFIX.format(
                format_instructions=parser.get_format_instructions()
            )
            messages = [
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": StructuredLLMExecutor._build_user_content(message_text, images)},
            ]
            raw_response = llm.invoke(messages)
            content = raw_response.content if hasattr(raw_response, "content") else str(raw_response)

            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                return output_schema(**data)
        except Exception:
            logger.exception("[%s] Le fallback JSON manuel a échoué", log_prefix)

        raise error_cls(
            f"Impossible d'obtenir une sortie valide ({log_prefix}) pour le message "
            f"('{message_text[:LOG_TEXT_PREVIEW_LEN]}...')"
        )