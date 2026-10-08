import math
from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel

from mentor.ai.adapters.fake import UNSCRIPTED_REPLY, FakeAI, FakeEmbedder
from mentor.ai.ports import AIOutputError, EmbedPurpose, Image, Message, Role, Transcript

USER = [Message(Role.USER, "Oi")]


class Answer(BaseModel):
    name: str
    years: int


async def _aiter[T](*items: T) -> AsyncIterator[T]:
    for item in items:
        yield item


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_llm_replays_script_in_order_and_records_calls(fake_ai: FakeAI) -> None:
    fake_ai.llm.script("primeira", lambda messages: f"eco: {messages[-1].text}")

    assert (await fake_ai.llm.generate(USER)).text == "primeira"
    assert (await fake_ai.llm.generate(USER)).text == "eco: Oi"
    assert (await fake_ai.llm.generate(USER)).text == UNSCRIPTED_REPLY
    assert [c.method for c in fake_ai.llm.calls] == ["generate"] * 3
    assert fake_ai.llm.calls[0].messages == tuple(USER)


async def test_llm_stream_yields_chunks_that_join_to_the_reply(fake_ai: FakeAI) -> None:
    fake_ai.llm.script("Qual é o seu nome completo?")

    chunks = [chunk async for chunk in fake_ai.llm.stream(USER)]

    assert len(chunks) > 1
    assert "".join(chunks) == "Qual é o seu nome completo?"


@pytest.mark.parametrize(
    "reply",
    [Answer(name="Ana", years=3), {"name": "Ana", "years": 3}, '{"name": "Ana", "years": 3}'],
)
async def test_llm_structured_accepts_model_dict_or_json(
    fake_ai: FakeAI, reply: Answer | dict[str, object] | str
) -> None:
    fake_ai.llm.script(reply)

    result = await fake_ai.llm.generate_structured(USER, Answer)

    assert result.value == Answer(name="Ana", years=3)
    assert fake_ai.llm.calls[0].schema is Answer


async def test_llm_structured_fails_when_unscripted_or_invalid(fake_ai: FakeAI) -> None:
    with pytest.raises(AIOutputError):
        await fake_ai.llm.generate_structured(USER, Answer)

    fake_ai.llm.script({"name": "Ana"})
    with pytest.raises(AIOutputError):
        await fake_ai.llm.generate_structured(USER, Answer)


async def test_vision_shares_the_llm_script_and_records_images(fake_ai: FakeAI) -> None:
    image = Image(data=b"png", mime_type="image/png")
    fake_ai.llm.script({"name": "Vaga", "years": 5})

    result = await fake_ai.vision.generate_structured(USER, [image], Answer)

    assert result.value.years == 5
    assert fake_ai.llm.calls[0].images == (image,)


async def test_embedder_is_deterministic_normalized_and_word_sensitive() -> None:
    embedder = FakeEmbedder(dimensions=64)

    python, python_again, django, cooking = await embedder.embed(
        ["Python backend", "python  BACKEND", "Python Django backend", "culinária italiana"],
        purpose=EmbedPurpose.SIMILARITY,
    )

    assert len(python) == 64
    assert math.isclose(math.sqrt(sum(v * v for v in python)), 1.0)
    assert python == python_again
    assert _cosine(python, django) > _cosine(python, cooking)
    assert embedder.calls[0][1] is EmbedPurpose.SIMILARITY


async def test_embedder_handles_text_without_words() -> None:
    [vector] = await FakeEmbedder(dimensions=8).embed([" "], purpose=EmbedPurpose.QUERY)

    assert math.isclose(sum(v * v for v in vector), 1.0)


async def test_stt_emits_one_scripted_transcript_per_chunk(fake_ai: FakeAI) -> None:
    fake_ai.stt.transcripts = [Transcript("meu", False), Transcript("meu nome", True)]

    out = [t async for t in fake_ai.stt.stream(_aiter(b"ab", b"cd", b"ef"), language="en-US")]

    assert out == [Transcript("meu", False), Transcript("meu nome", True)]
    assert bytes(fake_ai.stt.received) == b"abcdef"
    assert fake_ai.stt.languages == ["en-US"]


async def test_tts_yields_one_sample_per_character_and_skips_blank_chunks(
    fake_ai: FakeAI,
) -> None:
    audio = [a async for a in fake_ai.tts.stream(_aiter("Olá. ", "  ", "Tudo bem?"), voice="v")]

    assert [len(a) for a in audio] == [10, 18]
    assert fake_ai.tts.texts == ["Olá. ", "Tudo bem?"]
    assert fake_ai.tts.voices == ["v"]
