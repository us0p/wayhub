import pytest

from mentor.ai.adapters.fake import FakeEmbedder, FakeLLM, FakeSTT, FakeTTS
from mentor.ai.adapters.gemini import GeminiEmbedder, GeminiLLM, GeminiVision
from mentor.ai.adapters.google_speech import GoogleSTT, GoogleTTS
from mentor.ai.ports import AIConfigurationError
from mentor.ai.registry import build_ai, get_ai
from mentor.settings import Settings

BASE = {"database_url": "postgresql+asyncpg://u@h/db", "secret_key": "x" * 32, "app_env": "test"}
REAL = {"ai_llm": "gemini", "ai_embedder": "gemini", "ai_stt": "google", "ai_tts": "google"}
FAKE = dict.fromkeys(REAL, "fake")


@pytest.fixture(autouse=True)
def _no_ai_credentials_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's real credentials (from `.env` via compose) must not change these tests."""
    for var in ("GEMINI_BACKEND", "GEMINI_API_KEY", "GOOGLE_CLOUD_PROJECT", "EMBEDDING_DIMENSIONS"):
        monkeypatch.delenv(var, raising=False)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**BASE, **overrides})  # type: ignore[arg-type]


def test_tests_run_with_fake_adapters() -> None:
    get_ai.cache_clear()
    ai = get_ai()

    assert isinstance(ai.llm, FakeLLM)
    assert isinstance(ai.stt, FakeSTT)


def test_fakes_share_one_llm_and_honor_embedding_dimensions() -> None:
    ai = build_ai(_settings(**FAKE, embedding_dimensions=32))

    assert isinstance(ai.llm, FakeLLM)
    assert ai.vision.llm is ai.llm  # type: ignore[attr-defined]
    assert isinstance(ai.embedder, FakeEmbedder)
    assert ai.embedder.dimensions == 32
    assert isinstance(ai.tts, FakeTTS)


def test_real_providers_are_built_without_contacting_them() -> None:
    ai = build_ai(_settings(**REAL, gemini_api_key="k", google_cloud_project="proj"))

    assert isinstance(ai.llm, GeminiLLM)
    assert isinstance(ai.vision, GeminiVision)
    assert isinstance(ai.embedder, GeminiEmbedder)
    assert isinstance(ai.stt, GoogleSTT)
    assert isinstance(ai.tts, GoogleTTS)


def test_each_port_is_selected_independently() -> None:
    ai = build_ai(_settings(**{**FAKE, "ai_embedder": "gemini"}, gemini_api_key="k"))

    assert isinstance(ai.llm, FakeLLM)
    assert isinstance(ai.embedder, GeminiEmbedder)


def test_vertex_backend_needs_a_project_not_an_api_key() -> None:
    settings = _settings(**{**FAKE, "ai_llm": "gemini"}, gemini_backend="vertex")
    assert settings.missing_ai_settings() == ["GOOGLE_CLOUD_PROJECT"]

    ai = build_ai(
        _settings(
            **{**FAKE, "ai_llm": "gemini"}, gemini_backend="vertex", google_cloud_project="proj"
        )
    )
    assert isinstance(ai.llm, GeminiLLM)


def test_missing_credentials_fail_with_a_clear_error() -> None:
    with pytest.raises(AIConfigurationError, match="GEMINI_API_KEY, GOOGLE_CLOUD_PROJECT"):
        build_ai(_settings(**REAL))
