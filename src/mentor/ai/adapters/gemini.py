"""Gemini through LangChain (`langchain-google-genai`, D55).

Resilience (D51): each model retries transient failures inside the SDK (3 attempts, 30 s per
attempt); if the main model still fails, the runnable falls back to `GEMINI_FALLBACK_MODEL`
once. A stream only falls back before its first chunk (LangChain's `with_fallbacks`).
Embeddings never fall back: another model's vectors are not comparable.
"""

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from pydantic import BaseModel

from mentor.ai.embeddings import NormalizedEmbeddings
from mentor.ai.ports import AIConfigurationError, Effort
from mentor.settings import Settings

TIMEOUT_SECONDS = 30.0  # per attempt
ATTEMPTS = 3  # including the first; the SDK reads 1 as "no retries"


def _credentials(settings: Settings) -> dict[str, Any]:
    if settings.gemini_backend == "vertex":
        if not settings.google_cloud_project:
            raise AIConfigurationError("GOOGLE_CLOUD_PROJECT is required for the Vertex backend")
        return {
            "vertexai": True,
            "project": settings.google_cloud_project,
            "location": settings.gemini_location,
        }
    if not settings.gemini_api_key:
        raise AIConfigurationError("GEMINI_API_KEY is required for the Gemini API backend")
    return {"google_api_key": settings.gemini_api_key}


class GeminiChatModels:
    """Builds (and reuses) one LangChain chat model per model name and effort."""

    def __init__(self, settings: Settings) -> None:
        self._credentials = _credentials(settings)
        self._names = [settings.gemini_model]
        if settings.gemini_fallback_model:
            self._names.append(settings.gemini_fallback_model)
        self._cache: dict[tuple[str, Effort | None], ChatGoogleGenerativeAI] = {}

    def _model(self, name: str, effort: Effort | None) -> ChatGoogleGenerativeAI:
        key = (name, effort)
        if key not in self._cache:
            self._cache[key] = ChatGoogleGenerativeAI(
                model=name,
                reasoning_effort=effort.value if effort else None,  # Gemini's thinking_level
                max_retries=ATTEMPTS,
                timeout=TIMEOUT_SECONDS,
                **self._credentials,
            )
        return self._cache[key]

    def chat(self, *, effort: Effort | None = None) -> Runnable[LanguageModelInput, BaseMessage]:
        main, *fallbacks = (self._model(name, effort) for name in self._names)
        return main.with_fallbacks(fallbacks) if fallbacks else main

    def structured[M: BaseModel](
        self, schema: type[M], *, effort: Effort | None = None
    ) -> Runnable[LanguageModelInput, M]:
        main, *fallbacks = (
            self._model(name, effort).with_structured_output(schema, method="json_schema")
            for name in self._names
        )
        runnable = main.with_fallbacks(fallbacks) if fallbacks else main
        return runnable  # type: ignore[return-value]  # LangChain types it as dict | BaseModel


def make_embeddings(settings: Settings) -> NormalizedEmbeddings:
    inner = GoogleGenerativeAIEmbeddings(
        model=settings.gemini_embedding_model,
        output_dimensionality=settings.embedding_dimensions,
        **_credentials(settings),
    )
    return NormalizedEmbeddings(inner, settings.embedding_dimensions)
