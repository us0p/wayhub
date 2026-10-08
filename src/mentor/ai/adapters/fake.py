"""Deterministic in-memory adapters (D24): used by every non-contract test and by offline dev.

`FakeLLM` replays a script of replies in order and records every call, so tests can assert on
what the domain sent. `FakeEmbedder` hashes words into buckets, so texts sharing words get
similar vectors, which is enough to exercise pgvector queries.
"""

import hashlib
import math
import re
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from mentor.ai.bundle import AI
from mentor.ai.ports import (
    DEFAULT_LANGUAGE,
    AIOutputError,
    Completion,
    EmbedPurpose,
    Image,
    Message,
    Structured,
    Transcript,
    Usage,
)

type Reply = str | BaseModel | dict[str, object] | Callable[[Sequence[Message]], Reply]

UNSCRIPTED_REPLY = "Resposta de teste."
_USAGE = Usage(input_tokens=1, output_tokens=1)


@dataclass(frozen=True)
class FakeCall:
    method: str
    messages: tuple[Message, ...]
    images: tuple[Image, ...] = ()
    schema: type[BaseModel] | None = None


class FakeLLM:
    """Scripted LLM (and, through `FakeVision`, Vision). Each call consumes the next reply; a
    callable reply receives the messages. With the script exhausted, text calls answer
    `UNSCRIPTED_REPLY` and structured calls raise `AIOutputError` (a test forgot to script it)."""

    def __init__(self, replies: Iterable[Reply] = ()) -> None:
        self.replies: deque[Reply] = deque(replies)
        self.calls: list[FakeCall] = []

    def script(self, *replies: Reply) -> None:
        self.replies.extend(replies)

    def _next(self, messages: Sequence[Message]) -> Reply | None:
        if not self.replies:
            return None
        reply = self.replies.popleft()
        while callable(reply):
            reply = reply(messages)
        return reply

    def _text(self, messages: Sequence[Message]) -> str:
        reply = self._next(messages)
        if reply is None:
            return UNSCRIPTED_REPLY
        if isinstance(reply, BaseModel):
            return reply.model_dump_json()
        if isinstance(reply, dict):
            raise AIOutputError("FakeLLM: a dict reply was scripted for a text call")
        return str(reply)

    def _structured[M: BaseModel](self, messages: Sequence[Message], schema: type[M]) -> M:
        reply = self._next(messages)
        if reply is None:
            raise AIOutputError(f"FakeLLM: no scripted reply for {schema.__name__}")
        try:
            if isinstance(reply, BaseModel):
                return schema.model_validate(reply.model_dump())
            if isinstance(reply, dict):
                return schema.model_validate(reply)
            return schema.model_validate_json(str(reply))
        except ValidationError as exc:
            raise AIOutputError(f"FakeLLM: reply does not match {schema.__name__}") from exc

    async def generate(self, messages: Sequence[Message]) -> Completion:
        self.calls.append(FakeCall("generate", tuple(messages)))
        return Completion(text=self._text(messages), usage=_USAGE)

    async def generate_structured[M: BaseModel](
        self, messages: Sequence[Message], schema: type[M]
    ) -> Structured[M]:
        self.calls.append(FakeCall("generate_structured", tuple(messages), schema=schema))
        return Structured(value=self._structured(messages, schema), usage=_USAGE)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        self.calls.append(FakeCall("stream", tuple(messages)))
        for chunk in re.findall(r"\S+\s*", self._text(messages)):
            yield chunk

    async def generate_structured_from_images[M: BaseModel](
        self, messages: Sequence[Message], images: Sequence[Image], schema: type[M]
    ) -> Structured[M]:
        self.calls.append(FakeCall("vision", tuple(messages), tuple(images), schema))
        return Structured(value=self._structured(messages, schema), usage=_USAGE)


@dataclass
class FakeVision:
    """Vision port view over a `FakeLLM`, so one script and call log covers both."""

    llm: FakeLLM

    async def generate_structured[M: BaseModel](
        self, messages: Sequence[Message], images: Sequence[Image], schema: type[M]
    ) -> Structured[M]:
        return await self.llm.generate_structured_from_images(messages, images, schema)


@dataclass
class FakeEmbedder:
    """Bag-of-words hashing embedder: same words → same buckets → high cosine similarity."""

    dimensions: int = 768
    calls: list[tuple[tuple[str, ...], EmbedPurpose]] = field(default_factory=list)

    def vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        words = re.findall(r"\w+", text.casefold()) or [""]
        for word in words:
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            vec[int.from_bytes(digest) % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec]

    async def embed(self, texts: Sequence[str], *, purpose: EmbedPurpose) -> list[list[float]]:
        self.calls.append((tuple(texts), purpose))
        return [self.vector(text) for text in texts]


@dataclass
class FakeSTT:
    """Emits the next scripted transcript for each audio chunk received; once the script is
    exhausted, keeps consuming audio silently."""

    transcripts: list[Transcript] = field(default_factory=list)
    received: bytearray = field(default_factory=bytearray)
    languages: list[str] = field(default_factory=list)

    async def stream(
        self, audio: AsyncIterable[bytes], *, language: str = DEFAULT_LANGUAGE
    ) -> AsyncIterator[Transcript]:
        self.languages.append(language)
        pending = deque(self.transcripts)
        async for chunk in audio:
            self.received.extend(chunk)
            if pending:
                yield pending.popleft()


@dataclass
class FakeTTS:
    """Yields silence: 2 bytes (one PCM16 sample) per character of each text chunk."""

    texts: list[str] = field(default_factory=list)
    voices: list[str | None] = field(default_factory=list)

    async def stream(
        self, text: AsyncIterable[str], *, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.voices.append(voice)
        async for chunk in text:
            if chunk.strip():
                self.texts.append(chunk)
                yield b"\x00\x00" * len(chunk)


@dataclass(frozen=True)
class FakeAI(AI):
    """`AI` bundle typed with the fakes, so tests can script and inspect them."""

    llm: FakeLLM
    vision: FakeVision
    embedder: FakeEmbedder
    stt: FakeSTT
    tts: FakeTTS

    @classmethod
    def create(cls, dimensions: int = 768) -> "FakeAI":
        llm = FakeLLM()
        return cls(
            llm=llm,
            vision=FakeVision(llm),
            embedder=FakeEmbedder(dimensions),
            stt=FakeSTT(),
            tts=FakeTTS(),
        )
