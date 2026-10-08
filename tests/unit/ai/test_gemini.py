"""GeminiLLM/GeminiEmbedder against a stub client: request mapping and error handling. Real
API behavior is covered by tests/contract."""

import math
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from mentor.ai.adapters.gemini import EMBED_BATCH, GeminiEmbedder, GeminiLLM, GeminiVision
from mentor.ai.ports import (
    AIOutputError,
    AIProviderError,
    EmbedPurpose,
    Image,
    Message,
    Role,
    Usage,
)

CONVERSATION = [
    Message(Role.SYSTEM, "Você é um entrevistador."),
    Message(Role.ASSISTANT, "Qual é o seu nome?"),
    Message(Role.USER, "Ana"),
]


class Answer(BaseModel):
    name: str


def _response(text: str | None) -> types.GenerateContentResponse:
    parts = [types.Part.from_text(text=text)] if text is not None else []
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12, candidates_token_count=3
        ),
    )


class StubModels:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: list[Any] = []

    def _next(self, **request: Any) -> Any:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def generate_content(self, **request: Any) -> Any:
        return self._next(**request)

    async def generate_content_stream(self, **request: Any) -> AsyncIterator[Any]:
        chunks = self._next(**request)

        async def iterate() -> AsyncIterator[Any]:
            for chunk in chunks:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        return iterate()

    async def embed_content(self, **request: Any) -> Any:
        return self._next(**request)


@pytest.fixture
def models() -> StubModels:
    return StubModels()


@pytest.fixture
def client(models: StubModels) -> genai.Client:
    return cast(genai.Client, SimpleNamespace(aio=SimpleNamespace(models=models)))


def _api_error(code: int) -> errors.APIError:
    return errors.APIError(code, {"error": {"message": "segredo do usuário", "status": "X"}})


async def test_generate_maps_roles_and_system_prompt(
    client: genai.Client, models: StubModels
) -> None:
    models.responses = [_response("Prazer, Ana!")]

    result = await GeminiLLM(client, "m").generate(CONVERSATION)

    assert result.text == "Prazer, Ana!"
    assert result.usage == Usage(input_tokens=12, output_tokens=3)
    request = models.requests[0]
    assert request["model"] == "m"
    assert request["config"].system_instruction == "Você é um entrevistador."
    assert [(c.role, c.parts[0].text) for c in request["contents"]] == [
        ("model", "Qual é o seu nome?"),
        ("user", "Ana"),
    ]


async def test_generate_structured_requests_json_schema_and_validates(
    client: genai.Client, models: StubModels
) -> None:
    models.responses = [_response('{"name": "Ana"}')]

    result = await GeminiLLM(client, "m").generate_structured(CONVERSATION, Answer)

    assert result.value == Answer(name="Ana")
    config = models.requests[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == Answer.model_json_schema()


@pytest.mark.parametrize("text", ['{"nome": "Ana"}', "not json", None])
async def test_invalid_or_empty_output_raises_output_error(
    client: genai.Client, models: StubModels, text: str | None
) -> None:
    models.responses = [_response(text)]

    with pytest.raises(AIOutputError):
        await GeminiLLM(client, "m").generate_structured(CONVERSATION, Answer)


async def test_api_errors_become_provider_errors_without_echoing_content(
    client: genai.Client, models: StubModels
) -> None:
    models.responses = [_api_error(429)]

    with pytest.raises(AIProviderError) as exc:
        await GeminiLLM(client, "m").generate(CONVERSATION)

    assert "429" in str(exc.value)
    assert "segredo" not in str(exc.value)


async def test_stream_yields_text_chunks_and_maps_errors(
    client: genai.Client, models: StubModels
) -> None:
    llm = GeminiLLM(client, "m")
    models.responses = [[_response("Olá, "), _response(None), _response("Ana!")]]
    assert [c async for c in llm.stream(CONVERSATION)] == ["Olá, ", "Ana!"]

    models.responses = [[_response("Olá"), _api_error(503)]]
    with pytest.raises(AIProviderError):
        [c async for c in llm.stream(CONVERSATION)]


async def test_vision_attaches_images_to_the_last_user_turn(
    client: genai.Client, models: StubModels
) -> None:
    models.responses = [_response('{"name": "Vaga"}')]
    images = [Image(b"a", "image/png"), Image(b"b", "application/pdf")]

    await GeminiVision(GeminiLLM(client, "m")).generate_structured(
        [Message(Role.USER, "Extraia a vaga.")], images, Answer
    )

    [content] = models.requests[0]["contents"]
    assert content.parts[0].text == "Extraia a vaga."
    assert [(p.inline_data.data, p.inline_data.mime_type) for p in content.parts[1:]] == [
        (b"a", "image/png"),
        (b"b", "application/pdf"),
    ]


def _embeddings(*vectors: list[float]) -> types.EmbedContentResponse:
    return types.EmbedContentResponse(
        embeddings=[types.ContentEmbedding(values=v) for v in vectors]
    )


async def test_embed_batches_normalizes_and_sets_task_type(
    client: genai.Client, models: StubModels
) -> None:
    texts = [f"t{i}" for i in range(EMBED_BATCH + 1)]
    models.responses = [
        _embeddings(*([[3.0, 4.0]] * EMBED_BATCH)),
        _embeddings([0.0, 2.0]),
    ]

    vectors = await GeminiEmbedder(client, "e", 2).embed(texts, purpose=EmbedPurpose.QUERY)

    assert len(vectors) == EMBED_BATCH + 1
    assert vectors[0] == [0.6, 0.8]
    assert vectors[-1] == [0.0, 1.0]
    assert all(math.isclose(sum(v * v for v in vec), 1.0) for vec in vectors)
    assert [len(r["contents"]) for r in models.requests] == [EMBED_BATCH, 1]
    config = models.requests[0]["config"]
    assert (config.task_type, config.output_dimensionality) == ("RETRIEVAL_QUERY", 2)


async def test_embed_rejects_wrong_dimensions(client: genai.Client, models: StubModels) -> None:
    models.responses = [_embeddings([1.0, 0.0, 0.0])]

    with pytest.raises(AIOutputError):
        await GeminiEmbedder(client, "e", 2).embed(["x"], purpose=EmbedPurpose.DOCUMENT)
