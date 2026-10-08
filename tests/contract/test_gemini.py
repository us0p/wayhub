import math
import struct
import zlib

import pytest
from google.genai import Client
from pydantic import BaseModel

from mentor.ai.adapters.gemini import GeminiEmbedder, GeminiLLM, GeminiVision
from mentor.ai.ports import EmbedPurpose, Image, Message, Role
from mentor.settings import Settings

pytestmark = pytest.mark.contract


def _white_png(size: int = 8) -> bytes:
    """A blank PNG: enough to prove images reach the model; the text carries the content."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)  # 8-bit grayscale
    pixels = zlib.compress(b"".join(b"\x00" + b"\xff" * size for _ in range(size)))
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


class Person(BaseModel):
    name: str
    years_of_python: int


@pytest.fixture
def llm(gemini_client: Client, settings: Settings) -> GeminiLLM:
    return GeminiLLM(gemini_client, settings.gemini_model)


async def test_generate_answers_in_portuguese(llm: GeminiLLM) -> None:
    result = await llm.generate(
        [
            Message(Role.SYSTEM, "Responda em português do Brasil, em uma frase curta."),
            Message(Role.USER, "Diga olá."),
        ]
    )

    assert result.text.strip()
    assert result.usage.input_tokens > 0


async def test_generate_structured_follows_the_schema(llm: GeminiLLM) -> None:
    result = await llm.generate_structured(
        [Message(Role.USER, "Ana Souza programa em Python há 4 anos. Extraia os dados.")], Person
    )

    assert result.value.years_of_python == 4
    assert "Ana" in result.value.name


async def test_stream_yields_several_chunks(llm: GeminiLLM) -> None:
    chunks = [c async for c in llm.stream([Message(Role.USER, "Conte de 1 a 30 por extenso.")])]

    assert len(chunks) > 1
    assert "trinta" in "".join(chunks).lower()


async def test_vision_accepts_images(llm: GeminiLLM) -> None:
    result = await GeminiVision(llm).generate_structured(
        [Message(Role.USER, "Ignore a imagem. Pessoa: Bruno, 2 anos de Python.")],
        [Image(_white_png(), "image/png")],
        Person,
    )

    assert result.value.years_of_python == 2


async def test_embeddings_have_the_configured_size_and_rank_related_texts(
    gemini_client: Client, settings: Settings
) -> None:
    embedder = GeminiEmbedder(
        gemini_client, settings.gemini_embedding_model, settings.embedding_dimensions
    )

    python, py3, cooking = await embedder.embed(
        ["Python", "Python 3 programming language", "receita de bolo de cenoura"],
        purpose=EmbedPurpose.SIMILARITY,
    )

    assert len(python) == settings.embedding_dimensions
    assert math.isclose(sum(v * v for v in python), 1.0, rel_tol=1e-6)

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cos(python, py3) > cos(python, cooking)
