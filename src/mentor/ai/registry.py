"""Builds the AI adapters selected in settings (`AI_LLM`, `AI_EMBEDDER`, `AI_STT`, `AI_TTS`).

Domain code receives an `AI` bundle, through the `AIServices` dependency in routes or
`get_ai()` elsewhere (worker tasks). Tests override `get_ai` with fakes.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from langchain_core.embeddings import Embeddings

from mentor.ai.bundle import AI
from mentor.ai.ports import STT, TTS, AIConfigurationError, ChatModels
from mentor.settings import Settings, get_settings


def build_ai(settings: Settings) -> AI:
    """Build the selected services. Raises `AIConfigurationError` if settings are missing.

    Provider SDKs are imported only when selected, so fakes never load them.
    """
    if missing := settings.missing_ai_settings():
        raise AIConfigurationError(f"AI providers need: {', '.join(missing)}")
    from mentor.profile.models import EMBEDDING_DIMENSIONS

    if settings.embedding_dimensions != EMBEDDING_DIMENSIONS:
        raise AIConfigurationError(
            f"EMBEDDING_DIMENSIONS must be {EMBEDDING_DIMENSIONS} (the database vector size)"
        )

    from mentor.ai.adapters.fake import FakeAI

    fakes = FakeAI.create(settings.embedding_dimensions)
    chat: ChatModels = fakes.chat
    embeddings: Embeddings = fakes.embeddings
    stt: STT = fakes.stt
    tts: TTS = fakes.tts

    if settings.uses_gemini:
        from mentor.ai.adapters import gemini

        if settings.ai_llm == "gemini":
            chat = gemini.GeminiChatModels(settings)
        if settings.ai_embedder == "gemini":
            embeddings = gemini.make_embeddings(settings)

    if "google" in (settings.ai_stt, settings.ai_tts):
        from mentor.ai.adapters import google_speech

        assert settings.google_cloud_project  # checked by missing_ai_settings()
        if settings.ai_stt == "google":
            stt = google_speech.GoogleSTT(
                settings.google_cloud_project,
                settings.google_stt_location,
                settings.google_stt_model,
            )
        if settings.ai_tts == "google":
            tts = google_speech.GoogleTTS(settings.google_tts_voice)

    return AI(chat=chat, embeddings=embeddings, stt=stt, tts=tts)


@lru_cache
def get_ai() -> AI:
    return build_ai(get_settings())


AIServices = Annotated[AI, Depends(get_ai)]
