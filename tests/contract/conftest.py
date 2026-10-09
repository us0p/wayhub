"""Contract tests hit the real Google APIs (`uv run pytest -m contract`). They need, in the
environment or `.env`: GEMINI_API_KEY (or GEMINI_BACKEND=vertex) and, for Speech/TTS and
Vertex, GOOGLE_CLOUD_PROJECT plus Application Default Credentials. Missing credentials skip
the tests instead of failing them."""

import pytest

from mentor.ai.adapters.gemini import GeminiChatModels, make_embeddings
from mentor.ai.adapters.google_speech import GoogleSTT, GoogleTTS
from mentor.ai.embeddings import NormalizedEmbeddings
from mentor.settings import Settings, get_settings

pytestmark = pytest.mark.contract


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


def _require_gemini(settings: Settings) -> None:
    if settings.gemini_backend == "api_key" and not settings.gemini_api_key:
        pytest.skip("GEMINI_API_KEY not set")
    if settings.gemini_backend == "vertex" and not settings.google_cloud_project:
        pytest.skip("GOOGLE_CLOUD_PROJECT not set")


@pytest.fixture(scope="session")
def chat_models(settings: Settings) -> GeminiChatModels:
    _require_gemini(settings)
    return GeminiChatModels(settings)


@pytest.fixture(scope="session")
def embeddings(settings: Settings) -> NormalizedEmbeddings:
    _require_gemini(settings)
    return make_embeddings(settings)


@pytest.fixture(scope="session")
def cloud_project(settings: Settings) -> str:
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    if not settings.google_cloud_project:
        pytest.skip("GOOGLE_CLOUD_PROJECT not set")
    try:
        google.auth.default()
    except DefaultCredentialsError:
        pytest.skip("no Application Default Credentials")
    return settings.google_cloud_project


@pytest.fixture
def stt(settings: Settings, cloud_project: str) -> GoogleSTT:
    return GoogleSTT(cloud_project, settings.google_stt_location, settings.google_stt_model)


@pytest.fixture
def tts(settings: Settings, cloud_project: str) -> GoogleTTS:
    return GoogleTTS(settings.google_tts_voice)
