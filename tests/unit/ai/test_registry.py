import pytest
from google.auth.exceptions import DefaultCredentialsError

from mentor.ai.adapters.fake import FakeChatModels, FakeEmbeddings, FakeSTT, FakeTTS
from mentor.ai.adapters.gemini import GeminiChatModels
from mentor.ai.adapters.google_speech import GoogleSTT, GoogleTTS
from mentor.ai.embeddings import NormalizedEmbeddings
from mentor.ai.ports import AIConfigurationError
from mentor.ai.registry import build_ai, check_google_credentials, get_ai
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

    assert isinstance(ai.chat, FakeChatModels)
    assert isinstance(ai.stt, FakeSTT)


def test_embedding_dimensions_must_match_the_database() -> None:
    with pytest.raises(AIConfigurationError, match="768"):
        build_ai(_settings(**FAKE, embedding_dimensions=32))


def test_fakes_honor_embedding_dimensions() -> None:
    ai = build_ai(_settings(**FAKE, embedding_dimensions=768))

    assert isinstance(ai.chat, FakeChatModels)
    assert isinstance(ai.embeddings, FakeEmbeddings)
    assert ai.embeddings.dimensions == 768
    assert isinstance(ai.tts, FakeTTS)


def test_real_providers_are_built_without_contacting_them() -> None:
    ai = build_ai(_settings(**REAL, gemini_api_key="k", google_cloud_project="proj"))

    assert isinstance(ai.chat, GeminiChatModels)
    assert isinstance(ai.embeddings, NormalizedEmbeddings)
    assert isinstance(ai.stt, GoogleSTT)
    assert isinstance(ai.tts, GoogleTTS)


def test_each_port_is_selected_independently() -> None:
    ai = build_ai(_settings(**{**FAKE, "ai_embedder": "gemini"}, gemini_api_key="k"))

    assert isinstance(ai.chat, FakeChatModels)
    assert isinstance(ai.embeddings, NormalizedEmbeddings)


def test_vertex_backend_needs_a_project_not_an_api_key() -> None:
    settings = _settings(**{**FAKE, "ai_llm": "gemini"}, gemini_backend="vertex")
    assert settings.missing_ai_settings() == ["GOOGLE_CLOUD_PROJECT"]

    ai = build_ai(
        _settings(
            **{**FAKE, "ai_llm": "gemini"}, gemini_backend="vertex", google_cloud_project="proj"
        )
    )
    assert isinstance(ai.chat, GeminiChatModels)


def test_missing_credentials_fail_with_a_clear_error() -> None:
    with pytest.raises(AIConfigurationError, match="GEMINI_API_KEY, GOOGLE_CLOUD_PROJECT"):
        build_ai(_settings(**REAL))


def test_credentials_are_not_looked_up_for_fakes_or_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def lookup() -> None:
        raise AssertionError("ADC looked up")

    monkeypatch.setattr("google.auth.default", lookup)

    check_google_credentials(_settings(**FAKE))
    check_google_credentials(
        _settings(**{**FAKE, "ai_llm": "gemini", "ai_embedder": "gemini"}, gemini_api_key="k")
    )


def test_missing_speech_credentials_fail_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def lookup() -> None:
        raise DefaultCredentialsError("no credentials")

    monkeypatch.setattr("google.auth.default", lookup)

    with pytest.raises(AIConfigurationError, match="Application Default Credentials"):
        check_google_credentials(_settings(**{**FAKE, "ai_stt": "google"}))
