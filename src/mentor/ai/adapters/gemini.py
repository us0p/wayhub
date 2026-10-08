"""Google Gemini adapters (D6, D46): `GeminiLLM` implements LLM and Vision, `GeminiEmbedder`
implements Embedder. Both use the async `google-genai` client, built by `make_client`."""

import math
from collections.abc import AsyncIterator, Sequence

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from mentor.ai.ports import (
    AIConfigurationError,
    AIOutputError,
    AIProviderError,
    Completion,
    EmbedPurpose,
    Image,
    Message,
    Role,
    Structured,
    Usage,
)
from mentor.settings import Settings

TIMEOUT_MS = 60_000
EMBED_BATCH = 100  # max texts per embed_content request

_TASK_TYPES = {
    EmbedPurpose.DOCUMENT: "RETRIEVAL_DOCUMENT",
    EmbedPurpose.QUERY: "RETRIEVAL_QUERY",
    EmbedPurpose.SIMILARITY: "SEMANTIC_SIMILARITY",
}


def make_client(settings: Settings) -> genai.Client:
    options = types.HttpOptions(timeout=TIMEOUT_MS)
    if settings.gemini_backend == "vertex":
        if not settings.google_cloud_project:
            raise AIConfigurationError("GOOGLE_CLOUD_PROJECT is required for the Vertex backend")
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.gemini_location,
            http_options=options,
        )
    if not settings.gemini_api_key:
        raise AIConfigurationError("GEMINI_API_KEY is required for the Gemini API backend")
    return genai.Client(api_key=settings.gemini_api_key.get_secret_value(), http_options=options)


def _contents(
    messages: Sequence[Message], images: Sequence[Image] = ()
) -> tuple[str | None, list[types.Content]]:
    """Split out the system prompt and map turns to Gemini roles; images join the last turn."""
    system = "\n\n".join(m.text for m in messages if m.role is Role.SYSTEM) or None
    contents = [
        types.Content(
            role="model" if m.role is Role.ASSISTANT else "user",
            parts=[types.Part.from_text(text=m.text)],
        )
        for m in messages
        if m.role is not Role.SYSTEM
    ]
    if images:
        if not contents or contents[-1].role != "user":
            contents.append(types.Content(role="user", parts=[]))
        parts = contents[-1].parts or []
        parts.extend(types.Part.from_bytes(data=i.data, mime_type=i.mime_type) for i in images)
        contents[-1].parts = parts
    return system, contents


def _usage(response: types.GenerateContentResponse) -> Usage:
    meta = response.usage_metadata
    if meta is None:
        return Usage()
    return Usage(
        input_tokens=meta.prompt_token_count or 0, output_tokens=meta.candidates_token_count or 0
    )


def _provider_error(exc: errors.APIError) -> AIProviderError:
    # Only the status: messages may echo request content.
    return AIProviderError(f"Gemini API error {exc.code} {exc.status}")


class GeminiLLM:
    def __init__(self, client: genai.Client, model: str) -> None:
        self._client = client
        self._model = model

    async def _generate(
        self,
        messages: Sequence[Message],
        images: Sequence[Image] = (),
        schema: type[BaseModel] | None = None,
    ) -> types.GenerateContentResponse:
        system, contents = _contents(messages, images)
        config = types.GenerateContentConfig(system_instruction=system)
        if schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = schema.model_json_schema()
        try:
            return await self._client.aio.models.generate_content(
                model=self._model, contents=contents, config=config
            )
        except errors.APIError as exc:
            raise _provider_error(exc) from exc

    async def generate_structured_from_images[M: BaseModel](
        self, messages: Sequence[Message], images: Sequence[Image], schema: type[M]
    ) -> Structured[M]:
        response = await self._generate(messages, images, schema)
        if not response.text:
            raise AIOutputError(f"Gemini returned no output for {schema.__name__}")
        try:
            value = schema.model_validate_json(response.text)
        except ValidationError as exc:
            raise AIOutputError(f"Gemini output does not match {schema.__name__}") from exc
        return Structured(value=value, usage=_usage(response))

    async def generate(self, messages: Sequence[Message]) -> Completion:
        response = await self._generate(messages)
        if not response.text:
            raise AIOutputError("Gemini returned no text")
        return Completion(text=response.text, usage=_usage(response))

    async def generate_structured[M: BaseModel](
        self, messages: Sequence[Message], schema: type[M]
    ) -> Structured[M]:
        return await self.generate_structured_from_images(messages, (), schema)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        system, contents = _contents(messages)
        config = types.GenerateContentConfig(system_instruction=system)
        try:
            chunks = await self._client.aio.models.generate_content_stream(
                model=self._model, contents=contents, config=config
            )
            async for chunk in chunks:
                if chunk.text:
                    yield chunk.text
        except errors.APIError as exc:
            raise _provider_error(exc) from exc


class GeminiVision:
    """Vision port over `GeminiLLM` (same client and model; Gemini is natively multimodal)."""

    def __init__(self, llm: GeminiLLM) -> None:
        self._llm = llm

    async def generate_structured[M: BaseModel](
        self, messages: Sequence[Message], images: Sequence[Image], schema: type[M]
    ) -> Structured[M]:
        return await self._llm.generate_structured_from_images(messages, images, schema)


def _normalize(vector: list[float]) -> list[float]:
    # Gemini only normalizes full-size vectors; truncated ones (768) must be normalized here.
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        raise AIOutputError("Gemini returned a zero embedding")
    return [v / norm for v in vector]


class GeminiEmbedder:
    def __init__(self, client: genai.Client, model: str, dimensions: int) -> None:
        self._client = client
        self._model = model
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str], *, purpose: EmbedPurpose) -> list[list[float]]:
        config = types.EmbedContentConfig(
            task_type=_TASK_TYPES[purpose], output_dimensionality=self._dimensions
        )
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = list(texts[start : start + EMBED_BATCH])
            try:
                response = await self._client.aio.models.embed_content(
                    model=self._model,
                    contents=batch,
                    config=config,
                )
            except errors.APIError as exc:
                raise _provider_error(exc) from exc
            embeddings = response.embeddings or []
            if len(embeddings) != len(batch):
                raise AIOutputError("Gemini returned a different number of embeddings")
            for embedding in embeddings:
                values = embedding.values or []
                if len(values) != self._dimensions:
                    raise AIOutputError(f"Gemini returned {len(values)} dimensions")
                vectors.append(_normalize(values))
        return vectors
