"""Provider-agnostic AI ports (D2, D45).

Domain code depends only on these protocols and DTOs. Adapters live in `mentor.ai.adapters`
and are chosen by `mentor.ai.registry` from settings; adding a provider never touches domain
code.

Audio contract: STT consumes and TTS produces raw PCM16 little-endian mono audio, at
`STT_SAMPLE_RATE` and `TTS_SAMPLE_RATE` respectively (no WAV header).
"""

from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

STT_SAMPLE_RATE = 16_000
TTS_SAMPLE_RATE = 24_000
DEFAULT_LANGUAGE = "pt-BR"


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class Message:
    role: Role
    text: str


@dataclass(frozen=True)
class Image:
    data: bytes
    mime_type: str  # e.g. image/png, image/jpeg, application/pdf


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage


@dataclass(frozen=True)
class Structured[M: BaseModel]:
    value: M
    usage: Usage


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool


class EmbedPurpose(StrEnum):
    DOCUMENT = "document"  # stored texts (bullets, requirements)
    QUERY = "query"  # texts searched against stored documents
    SIMILARITY = "similarity"  # symmetric comparison (skill normalization)


class AIError(Exception):
    """Base for every AI failure the domain may handle."""


class AIConfigurationError(AIError):
    """A selected provider is missing settings or credentials."""


class AIProviderError(AIError):
    """The provider failed or could not be reached (network, quota, 5xx...)."""


class AIOutputError(AIError):
    """The provider answered, but not with usable output (empty, blocked, invalid schema)."""


class LLM(Protocol):
    async def generate(self, messages: Sequence[Message]) -> Completion: ...

    async def generate_structured[M: BaseModel](
        self, messages: Sequence[Message], schema: type[M]
    ) -> Structured[M]:
        """Return output validated against `schema`, or raise `AIOutputError`."""
        ...

    def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Yield the reply as text chunks as soon as they are generated."""
        ...


class Vision(Protocol):
    async def generate_structured[M: BaseModel](
        self, messages: Sequence[Message], images: Sequence[Image], schema: type[M]
    ) -> Structured[M]:
        """Like `LLM.generate_structured`, with images (or PDFs) attached to the last turn."""
        ...


class Embedder(Protocol):
    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str], *, purpose: EmbedPurpose) -> list[list[float]]:
        """One L2-normalized vector of `dimensions` floats per text, in order."""
        ...


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
