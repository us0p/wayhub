"""Deterministic in-memory adapters (D24, D55): used by every non-contract test and by offline dev.

`FakeChatModels` is a LangChain-compatible chat model factory driven by a script: text calls
(invoke/stream) consume `script(...)` replies in order; structured calls consume replies
queued per schema with `script_for(Schema, ...)`, so concurrent calls for different schemas
never depend on scheduling order. Every call is recorded in `calls`. `FakeEmbeddings` hashes
words into buckets, so texts sharing words get similar vectors (enough for pgvector tests).
"""

import hashlib
import math
import re
from collections import defaultdict, deque
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, ConfigDict, ValidationError

from mentor.ai.bundle import AI
from mentor.ai.ports import DEFAULT_LANGUAGE, AIOutputError, Effort, Transcript

type Reply = (
    str | BaseModel | dict[str, object] | Exception | Callable[[Sequence[BaseMessage]], "Reply"]
)

UNSCRIPTED_REPLY = "Resposta de teste."


@dataclass(frozen=True)
class FakeCall:
    method: str  # "chat" or "structured"
    messages: tuple[BaseMessage, ...]
    schema: type[BaseModel] | None = None
    effort: Effort | None = None


def _messages(value: LanguageModelInput) -> list[BaseMessage]:
    if isinstance(value, PromptValue):
        return value.to_messages()
    if isinstance(value, str):
        return [HumanMessage(content=value)]
    return [m for m in value if isinstance(m, BaseMessage)]


class FakeScript:
    def __init__(self) -> None:
        self.replies: deque[Reply] = deque()
        self.structured: defaultdict[type[BaseModel], deque[Reply]] = defaultdict(deque)
        self.calls: list[FakeCall] = []

    @staticmethod
    def _resolve(reply: Reply, messages: Sequence[BaseMessage]) -> Reply:
        while callable(reply):
            reply = reply(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def text(self, messages: Sequence[BaseMessage], effort: Effort | None) -> str:
        self.calls.append(FakeCall("chat", tuple(messages), effort=effort))
        if not self.replies:
            return UNSCRIPTED_REPLY
        reply = self._resolve(self.replies.popleft(), messages)
        if isinstance(reply, BaseModel | dict):
            raise AIOutputError("FakeChatModels: a structured reply was scripted for a text call")
        return str(reply)

    def value[M: BaseModel](
        self, messages: Sequence[BaseMessage], schema: type[M], effort: Effort | None
    ) -> M:
        self.calls.append(FakeCall("structured", tuple(messages), schema, effort))
        queue = self.structured[schema]
        if not queue:
            raise AIOutputError(f"FakeChatModels: no scripted reply for {schema.__name__}")
        reply = self._resolve(queue.popleft(), messages)
        try:
            if isinstance(reply, BaseModel):
                return schema.model_validate(reply.model_dump())
            if isinstance(reply, dict):
                return schema.model_validate(reply)
            return schema.model_validate_json(str(reply))
        except ValidationError as exc:
            raise AIOutputError(f"FakeChatModels: reply does not match {schema.__name__}") from exc


class FakeChatModel(BaseChatModel):
    """A LangChain chat model that answers from a `FakeScript`, streaming word by word."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    fake_script: FakeScript
    effort: Effort | None = None

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        text = self.fake_script.text(messages, self.effort)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for word in re.findall(r"\S+\s*", self.fake_script.text(messages, self.effort)):
            yield ChatGenerationChunk(message=AIMessageChunk(content=word))

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._stream(messages, stop, None, **kwargs):
            yield chunk


class FakeChatModels:
    """`ChatModels` backed by one script. Script it with `script()` / `script_for()`."""

    def __init__(self) -> None:
        self.fake_script = FakeScript()

    @property
    def calls(self) -> list[FakeCall]:
        return self.fake_script.calls

    def script(self, *replies: Reply) -> None:
        self.fake_script.replies.extend(replies)

    def script_for(self, schema: type[BaseModel], *replies: Reply) -> None:
        self.fake_script.structured[schema].extend(replies)

    def chat(self, *, effort: Effort | None = None) -> Runnable[LanguageModelInput, BaseMessage]:
        return FakeChatModel(fake_script=self.fake_script, effort=effort)

    def structured[M: BaseModel](
        self, schema: type[M], *, effort: Effort | None = None
    ) -> Runnable[LanguageModelInput, M]:
        async def run(value: LanguageModelInput) -> M:
            return self.fake_script.value(_messages(value), schema, effort)

        return RunnableLambda(run, name=f"fake_structured_{schema.__name__}")


class FakeEmbeddings(Embeddings):
    """Bag-of-words hashing embeddings: same words → same buckets → high cosine similarity."""

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions
        self.calls: list[tuple[str, ...]] = []

    def vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        words = re.findall(r"\w+", text.casefold()) or [""]
        for word in words:
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            vec[int.from_bytes(digest) % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(tuple(texts))
        return [self.vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@dataclass
class FakeSTT:
    """Emits the next scripted transcript for each audio chunk received; once the script is
    exhausted, keeps consuming audio silently. The script is shared by successive streams
    (stream rotation), and a scripted exception is raised in place of a transcript."""

    transcripts: list[Transcript | Exception] = field(default_factory=list)
    received: bytearray = field(default_factory=bytearray)
    languages: list[str] = field(default_factory=list)

    async def stream(
        self, audio: AsyncIterable[bytes], *, language: str = DEFAULT_LANGUAGE
    ) -> AsyncIterator[Transcript]:
        self.languages.append(language)
        async for chunk in audio:
            self.received.extend(chunk)
            if self.transcripts:
                item = self.transcripts.pop(0)
                if isinstance(item, Exception):
                    raise item
                yield item


@dataclass
class FakeTTS:
    """Yields silence: 2 bytes (one PCM16 sample) per character of each text chunk.
    With `error` set, raises it on the first chunk instead."""

    texts: list[str] = field(default_factory=list)
    voices: list[str | None] = field(default_factory=list)
    error: Exception | None = None

    async def stream(
        self, text: AsyncIterable[str], *, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.voices.append(voice)
        async for chunk in text:
            if chunk.strip():
                if self.error is not None:
                    raise self.error
                self.texts.append(chunk)
                yield b"\x00\x00" * len(chunk)


@dataclass(frozen=True)
class FakeAI(AI):
    """`AI` bundle typed with the fakes, so tests can script and inspect them."""

    chat: FakeChatModels
    embeddings: FakeEmbeddings
    stt: FakeSTT
    tts: FakeTTS

    @classmethod
    def create(cls, dimensions: int = 768) -> "FakeAI":
        return cls(
            chat=FakeChatModels(),
            embeddings=FakeEmbeddings(dimensions),
            stt=FakeSTT(),
            tts=FakeTTS(),
        )
