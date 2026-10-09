import math

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from mentor.ai.adapters.gemini import GeminiChatModels
from mentor.ai.embeddings import NormalizedEmbeddings
from mentor.ai.ports import Effort
from mentor.interview.schemas import ProfilePatch, TurnPlan
from mentor.settings import Settings

pytestmark = pytest.mark.contract


class Person(BaseModel):
    name: str
    years_of_python: int


async def test_low_effort_stream_answers_in_portuguese(chat_models: GeminiChatModels) -> None:
    chunks = [
        chunk.text
        async for chunk in chat_models.chat(effort=Effort.LOW).astream(
            [
                SystemMessage("Responda em português do Brasil, em uma frase curta."),
                HumanMessage("Conte de 1 a 20 por extenso."),
            ]
        )
    ]

    assert len(chunks) >= 1
    assert "vinte" in "".join(chunks).lower()


async def test_structured_output_follows_the_schema(chat_models: GeminiChatModels) -> None:
    result = await chat_models.structured(Person).ainvoke(
        [HumanMessage("Ana Souza programa em Python há 4 anos. Extraia os dados.")]
    )

    assert result.years_of_python == 4
    assert "Ana" in result.name


@pytest.mark.parametrize("schema", [ProfilePatch, TurnPlan])
async def test_the_interview_schemas_are_accepted_by_gemini(
    chat_models: GeminiChatModels, schema: type[BaseModel]
) -> None:
    """Gemini's JSON-schema support has limits; our real schemas must pass."""
    result = await chat_models.structured(schema, effort=Effort.LOW).ainvoke(
        [HumanMessage("Moro em Campinas e trabalho como dev Python na Acme desde 2021.")]
    )

    assert isinstance(result, schema)


async def test_the_fallback_model_works_on_its_own(settings: Settings) -> None:
    if not settings.gemini_fallback_model:
        pytest.skip("no fallback model configured")
    only_fallback = GeminiChatModels(
        settings.model_copy(
            update={"gemini_model": settings.gemini_fallback_model, "gemini_fallback_model": None}
        )
    )

    reply = await only_fallback.chat(effort=Effort.LOW).ainvoke([HumanMessage("Diga olá.")])

    assert reply.text.strip()


async def test_embeddings_have_the_configured_size_and_rank_related_texts(
    embeddings: NormalizedEmbeddings, settings: Settings
) -> None:
    python, py3, cooking = await embeddings.aembed_documents(
        ["Python", "Python 3 programming language", "receita de bolo de cenoura"]
    )

    assert len(python) == settings.embedding_dimensions
    assert math.isclose(sum(v * v for v in python), 1.0, rel_tol=1e-6)

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cos(python, py3) > cos(python, cooking)
