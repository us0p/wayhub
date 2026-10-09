from dataclasses import dataclass

from langchain_core.embeddings import Embeddings

from mentor.ai.ports import STT, TTS, ChatModels


@dataclass(frozen=True)
class AI:
    """The AI services selected by `mentor.ai.registry` (D55)."""

    chat: ChatModels
    embeddings: Embeddings  # unit-length vectors of the database's dimension
    stt: STT
    tts: TTS
