"""A LangChain `Embeddings` wrapper that enforces our vector contract (D46): every vector has the
database's dimension and unit length, so pgvector cosine distances are comparable.

Gemini only normalizes full-size vectors; ours are truncated to 768 dimensions.
"""

import math

from langchain_core.embeddings import Embeddings

from mentor.ai.ports import AIOutputError


def normalize(vector: list[float], dimensions: int) -> list[float]:
    if len(vector) != dimensions:
        raise AIOutputError(f"embedding has {len(vector)} dimensions, expected {dimensions}")
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        raise AIOutputError("zero embedding")
    return [v / norm for v in vector]


class NormalizedEmbeddings(Embeddings):
    def __init__(self, inner: Embeddings, dimensions: int) -> None:
        self.inner = inner
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [normalize(v, self.dimensions) for v in self.inner.embed_documents(texts)]

    def embed_query(self, text: str) -> list[float]:
        return normalize(self.inner.embed_query(text), self.dimensions)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = await self.inner.aembed_documents(texts)
        return [normalize(v, self.dimensions) for v in vectors]

    async def aembed_query(self, text: str) -> list[float]:
        return normalize(await self.inner.aembed_query(text), self.dimensions)
