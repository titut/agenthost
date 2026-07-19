"""Shared embedding client for agenthost memory and tools."""
from __future__ import annotations

import os
from typing import Any

from openai import AsyncOpenAI

from agenthost.config import EmbeddingConfig


def _default_api_key() -> str | None:
    """Return the API key exposed by agenthost."""
    return os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPINFRA_API_KEY")


class EmbeddingClient:
    """Async OpenAI-compatible embedding client.

    Uses the model and endpoint configured in ``EmbeddingConfig``. The API key
    is read from the environment (``OPENAI_API_KEY`` or ``DEEPINFRA_API_KEY``)
    unless provided explicitly.
    """

    def __init__(self, config: EmbeddingConfig, api_key: str | None = None) -> None:
        self.model = config.model
        self.base_url = config.base_url
        self.api_key = api_key or _default_api_key()
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(api_key=self.api_key, **kwargs)
        return self._client

    async def embed(self, texts: list[str]) -> dict[str, Any]:
        """Embed a list of texts.

        Returns
        -------
        dict
            ``{"embeddings": [[...], ...]}`` on success, **or**
            ``{"error": "..."}`` on failure.
        """
        if not texts:
            return {"embeddings": []}
        if not self.api_key:
            return {
                "error": "No API key found. Set OPENAI_API_KEY or DEEPINFRA_API_KEY."
            }

        try:
            response = await self._get_client().embeddings.create(
                model=self.model,
                input=texts,
            )
            return {"embeddings": [item.embedding for item in response.data]}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Embedding failed: {type(exc).__name__}: {exc}"}

    async def embed_query(self, text: str) -> dict[str, Any]:
        """Embed a single query text.

        Returns
        -------
        dict
            ``{"embedding": [...]}`` on success, **or**
            ``{"error": "..."}`` on failure.
        """
        result = await self.embed([text])
        if "error" in result:
            return result
        embeddings = result.get("embeddings", [])
        if not embeddings:
            return {"error": "No embedding returned for query"}
        return {"embedding": embeddings[0]}


async def embed_texts(
    texts: list[str],
    model: str = "BAAI/bge-m3",
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Convenience standalone embedding function.

    Useful for tools that don't have an ``EmbeddingConfig`` instance.
    """
    client = EmbeddingClient(
        EmbeddingConfig(model=model, base_url=base_url),
        api_key=api_key,
    )
    return await client.embed(texts)
