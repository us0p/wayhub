"""AI ports (D2, D45, D55).

Text generation and embeddings use LangChain's standard interfaces: domain code asks a
`ChatModels` factory for a chat model (or a structured-output model) at a given `Effort` and
gets a LangChain `Runnable`; embeddings are a LangChain `Embeddings`. Retries and the fallback
model are already applied by the factory. Speech keeps our own ports, since LangChain has no
streaming speech abstraction.

Audio contract: STT consumes and TTS produces raw PCM16 little-endian mono audio, at
`STT_SAMPLE_RATE` and `TTS_SAMPLE_RATE` respectively (no WAV header).
"""

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

STT_SAMPLE_RATE = 16_000
TTS_SAMPLE_RATE = 24_000
DEFAULT_LANGUAGE = "pt-BR"


class Effort(StrEnum):
    """How much the model should reason before answering. Lower = faster first token.
    Omitted = the provider's default. Adapters map it to their own knob (Gemini: thinking level).
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChatModels(Protocol):
    def chat(self, *, effort: Effort | None = None) -> Runnable[LanguageModelInput, BaseMessage]:
        """A chat model; use `.astream()` for token streaming."""
        ...

    def structured[M: BaseModel](
        self, schema: type[M], *, effort: Effort | None = None
    ) -> Runnable[LanguageModelInput, M]:
        """A model whose output is validated against `schema` (native JSON schema mode)."""
        ...


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool


class AIError(Exception):
    """Base for AI failures raised by our own adapters and helpers."""


class AIConfigurationError(AIError):
    """A selected provider is missing settings or credentials."""


class AIProviderError(AIError):
    """The provider failed or could not be reached (network, quota, 5xx...)."""


class AIOutputError(AIError):
    """The provider answered, but not with usable output (empty, blocked, invalid)."""


class STT(Protocol):
    def stream(
        self, audio: AsyncIterable[bytes], *, language: str = DEFAULT_LANGUAGE
    ) -> AsyncIterator[Transcript]:
        """Transcribe PCM16 audio at `STT_SAMPLE_RATE`: interim and final transcripts.

        Ends when `audio` ends. Provider stream-length limits are the caller's concern.
        """
        ...


class TTS(Protocol):
    def stream(self, text: AsyncIterable[str], *, voice: str | None = None) -> AsyncIterator[bytes]:
        """Synthesize text chunks (e.g. sentences) into PCM16 audio at `TTS_SAMPLE_RATE`."""
        ...
