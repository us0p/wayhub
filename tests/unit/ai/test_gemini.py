"""How the Gemini models are configured through LangChain (no network). Real API behavior is
covered by tests/contract."""

import math

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.runnables import RunnableWithFallbacks
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from pydantic import BaseModel

from mentor.ai.adapters.gemini import ATTEMPTS, TIMEOUT_SECONDS, GeminiChatModels, make_embeddings
from mentor.ai.embeddings import NormalizedEmbeddings, normalize
from mentor.ai.ports import AIConfigurationError, AIOutputError, Effort
from mentor.settings import Settings


@pytest.fixture(autouse=True)
def _no_ai_credentials_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GEMINI_BACKEND", "GEMINI_API_KEY", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)


def _settings(**overrides: object) -> Settings:
    values = {
        "database_url": "postgresql+asyncpg://u@h/db",
        "secret_key": "x" * 32,
        "app_env": "test",
        "gemini_api_key": "k",
        "gemini_model": "main",
        "gemini_fallback_model": "backup",
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


class Answer(BaseModel):
    name: str


def test_chat_uses_the_main_model_with_retries_and_falls_back() -> None:
    chat = GeminiChatModels(_settings()).chat(effort=Effort.LOW)

    assert isinstance(chat, RunnableWithFallbacks)
    main, backup = chat.runnable, chat.fallbacks[0]
    assert isinstance(main, ChatGoogleGenerativeAI)
    assert isinstance(backup, ChatGoogleGenerativeAI)
    assert (main.model, backup.model) == ("main", "backup")
    assert main.reasoning_effort == backup.reasoning_effort == "low"  # Gemini thinking_level
    assert (main.max_retries, main.timeout) == (ATTEMPTS, TIMEOUT_SECONDS)


def test_default_effort_leaves_the_thinking_level_to_the_model() -> None:
    chat = GeminiChatModels(_settings(gemini_fallback_model="")).chat()

    assert isinstance(chat, ChatGoogleGenerativeAI)  # no fallback configured
    assert chat.reasoning_effort is None


def test_models_are_reused_per_name_and_effort() -> None:
    models = GeminiChatModels(_settings(gemini_fallback_model=""))

    assert models.chat(effort=Effort.LOW) is models.chat(effort=Effort.LOW)
    assert models.chat(effort=Effort.LOW) is not models.chat()


def test_structured_output_has_a_fallback_too() -> None:
    structured = GeminiChatModels(_settings()).structured(Answer)

    assert isinstance(structured, RunnableWithFallbacks)
    assert len(structured.fallbacks) == 1


def test_missing_credentials_are_reported() -> None:
    with pytest.raises(AIConfigurationError, match="GEMINI_API_KEY"):
        GeminiChatModels(_settings(gemini_api_key=None))
    with pytest.raises(AIConfigurationError, match="GOOGLE_CLOUD_PROJECT"):
        GeminiChatModels(_settings(gemini_backend="vertex"))


def test_embeddings_are_truncated_to_the_database_size_and_normalized() -> None:
    embeddings = make_embeddings(_settings())

    assert isinstance(embeddings, NormalizedEmbeddings)
    assert isinstance(embeddings.inner, GoogleGenerativeAIEmbeddings)
    assert embeddings.inner.output_dimensionality == 768


class _Raw(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[3.0, 4.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 2.0]


async def test_normalized_embeddings_scale_to_unit_length_and_check_size() -> None:
    embeddings = NormalizedEmbeddings(_Raw(), dimensions=2)

    assert await embeddings.aembed_documents(["a"]) == [[0.6, 0.8]]
    assert await embeddings.aembed_query("a") == [0.0, 1.0]
    with pytest.raises(AIOutputError):
        await NormalizedEmbeddings(_Raw(), dimensions=3).aembed_query("a")
    with pytest.raises(AIOutputError):
        normalize([0.0, 0.0], 2)
    assert math.isclose(sum(v * v for v in normalize([1.0, 1.0], 2)), 1.0)
