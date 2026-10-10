import math
from collections.abc import AsyncIterator

import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from mentor.ai.adapters.fake import UNSCRIPTED_REPLY, FakeAI, FakeEmbeddings
from mentor.ai.ports import AIOutputError, Effort, Transcript

USER = [HumanMessage("Oi")]


class Answer(BaseModel):
    name: str
    years: int


async def _aiter[T](*items: T) -> AsyncIterator[T]:
    for item in items:
        yield item


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_chat_replays_script_in_order_and_records_calls(fake_ai: FakeAI) -> None:
    fake_ai.chat.script("primeira", lambda messages: f"eco: {messages[-1].text}")
    model = fake_ai.chat.chat(effort=Effort.LOW)

    assert (await model.ainvoke(USER)).text == "primeira"
    assert (await model.ainvoke(USER)).text == "eco: Oi"
    assert (await model.ainvoke(USER)).text == UNSCRIPTED_REPLY
    assert [(c.method, c.effort) for c in fake_ai.chat.calls] == [("chat", Effort.LOW)] * 3
    assert fake_ai.chat.calls[0].messages == tuple(USER)


async def test_chat_streams_word_chunks(fake_ai: FakeAI) -> None:
    fake_ai.chat.script("Qual é o seu nome completo?")

    chunks = [chunk.text async for chunk in fake_ai.chat.chat().astream(USER)]

    assert len(chunks) > 1
    assert "".join(chunks) == "Qual é o seu nome completo?"


@pytest.mark.parametrize(
    "reply",
    [Answer(name="Ana", years=3), {"name": "Ana", "years": 3}, '{"name": "Ana", "years": 3}'],
)
async def test_structured_accepts_model_dict_or_json(
    fake_ai: FakeAI, reply: Answer | dict[str, object] | str
) -> None:
    fake_ai.chat.script_for(Answer, reply)

    result = await fake_ai.chat.structured(Answer).ainvoke(USER)

    assert result == Answer(name="Ana", years=3)
    assert fake_ai.chat.calls[0].schema is Answer


async def test_structured_replies_are_queued_per_schema(fake_ai: FakeAI) -> None:
    class Other(BaseModel):
        ok: bool

    fake_ai.chat.script_for(Answer, {"name": "Ana", "years": 1})
    fake_ai.chat.script_for(Other, {"ok": True})

    other = await fake_ai.chat.structured(Other).ainvoke(USER)
    answer = await fake_ai.chat.structured(Answer).ainvoke(USER)

    assert (other.ok, answer.name) == (True, "Ana")


async def test_structured_fails_when_unscripted_or_invalid(fake_ai: FakeAI) -> None:
    with pytest.raises(AIOutputError):
        await fake_ai.chat.structured(Answer).ainvoke(USER)

    fake_ai.chat.script_for(Answer, {"name": "Ana"})
    with pytest.raises(AIOutputError):
        await fake_ai.chat.structured(Answer).ainvoke(USER)


async def test_scripted_exceptions_are_raised(fake_ai: FakeAI) -> None:
    fake_ai.chat.script(AIOutputError("bloqueado"))

    with pytest.raises(AIOutputError, match="bloqueado"):
        await fake_ai.chat.chat().ainvoke(USER)


async def test_embeddings_are_deterministic_normalized_and_word_sensitive() -> None:
    embeddings = FakeEmbeddings(dimensions=64)

    python, python_again, django, cooking = await embeddings.aembed_documents(
        ["Python backend", "python  BACKEND", "Python Django backend", "culinária italiana"]
    )

    assert len(python) == 64
    assert math.isclose(math.sqrt(sum(v * v for v in python)), 1.0)
    assert python == python_again
    assert _cosine(python, django) > _cosine(python, cooking)


async def test_embeddings_handle_text_without_words() -> None:
    vector = await FakeEmbeddings(dimensions=8).aembed_query(" ")

    assert math.isclose(sum(v * v for v in vector), 1.0)


async def test_stt_emits_one_scripted_transcript_per_chunk(fake_ai: FakeAI) -> None:
    fake_ai.stt.transcripts = [Transcript("meu", False), Transcript("meu nome", True)]

    out = [t async for t in fake_ai.stt.stream(_aiter(b"ab", b"cd", b"ef"), language="en-US")]

    assert out == [Transcript("meu", False), Transcript("meu nome", True)]
    assert bytes(fake_ai.stt.received) == b"abcdef"
    assert fake_ai.stt.languages == ["en-US"]


async def test_stt_script_carries_over_to_the_next_stream_and_can_fail(fake_ai: FakeAI) -> None:
    fake_ai.stt.transcripts = [Transcript("um", True), Transcript("dois", True), ValueError()]

    first = [t async for t in fake_ai.stt.stream(_aiter(b"a"))]
    second = [t async for t in fake_ai.stt.stream(_aiter(b"b"))]

    assert first == [Transcript("um", True)]
    assert second == [Transcript("dois", True)]
    with pytest.raises(ValueError):
        _ = [t async for t in fake_ai.stt.stream(_aiter(b"c"))]


async def test_tts_can_be_scripted_to_fail(fake_ai: FakeAI) -> None:
    fake_ai.tts.error = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _ = [a async for a in fake_ai.tts.stream(_aiter("Olá."))]


async def test_tts_yields_one_sample_per_character_and_skips_blank_chunks(
    fake_ai: FakeAI,
) -> None:
    audio = [a async for a in fake_ai.tts.stream(_aiter("Olá. ", "  ", "Tudo bem?"), voice="v")]

    assert [len(a) for a in audio] == [10, 18]
    assert fake_ai.tts.texts == ["Olá. ", "Tudo bem?"]
    assert fake_ai.tts.voices == ["v"]
