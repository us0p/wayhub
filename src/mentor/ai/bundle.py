from dataclasses import dataclass

from mentor.ai.ports import LLM, STT, TTS, Embedder, Vision


@dataclass(frozen=True)
class AI:
    """The adapters for every AI port, as selected by `mentor.ai.registry`."""

    llm: LLM
    vision: Vision
    embedder: Embedder
    stt: STT
    tts: TTS
