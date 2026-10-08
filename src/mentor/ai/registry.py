"""Builds the AI adapters selected in settings (`AI_LLM`, `AI_EMBEDDER`, `AI_STT`, `AI_TTS`).

Domain code receives an `AI` bundle, through the `AIServices` dependency in routes or
`get_ai()` elsewhere (worker tasks). Tests override `get_ai` with fakes.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from mentor.ai.bundle import AI
from mentor.ai.ports import LLM, STT, TTS, AIConfigurationError, Embedder, Vision
from mentor.settings import Settings, get_settings


def build_ai(settings: Settings) -> AI:
    """Build the selected adapters. Raises `AIConfigurationError` if settings are missing.

    Provider SDKs are imported only when selected, so fakes never load them.
    """
    if missing := settings.missing_ai_settings():
        raise AIConfigurationError(f"AI providers need: {', '.join(missing)}")

    from mentor.ai.adapters.fake import FakeAI

    fakes = FakeAI.create(settings.embedding_dimensions)
    llm: LLM = fakes.llm
    vision: Vision = fakes.vision
    embedder: Embedder = fakes.embedder
    stt: STT = fakes.stt
    tts: TTS = fakes.tts

    if settings.uses_gemini:
        from mentor.ai.adapters import gemini

        client = gemini.make_client(settings)
        if settings.ai_llm == "gemini":
            gemini_llm = gemini.GeminiLLM(client, settings.gemini_model)
            llm, vision = gemini_llm, gemini.GeminiVision(gemini_llm)
        if settings.ai_embedder == "gemini":
            embedder = gemini.GeminiEmbedder(
                client, settings.gemini_embedding_model, settings.embedding_dimensions
            )

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

    return AI(llm=llm, vision=vision, embedder=embedder, stt=stt, tts=tts)


@lru_cache
def get_ai() -> AI:
    return build_ai(get_settings())


AIServices = Annotated[AI, Depends(get_ai)]
