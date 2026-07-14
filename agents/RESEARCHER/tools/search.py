"""Search tools for the RESEARCHER agent."""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from typing import Any

DEFAULT_MAX_CHARS = 10_000
FETCH_BATCH_SIZE = 5
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
TOP_K_CHUNKS = 12

# DeepInfra OpenAI-compatible embedding endpoint. The API key is expected to be
# provided by agenthost via the environment (OPENAI_API_KEY or DEEPINFRA_API_KEY).
EMBEDDING_BASE_URL = "https://api.deepinfra.com/v1/openai"
EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"


def _api_key() -> str | None:
    """Return the API key exposed by agenthost."""
    return os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPINFRA_API_KEY")


def _search_ddg(query: str, max_results: int) -> dict[str, Any]:
    """Search DuckDuckGo and return raw result records."""
    try:
        from ddgs import DDGS
    except ImportError as exc:
        return {"error": f"ddgs is not installed: {exc}"}

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return {
            "query": query,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in results
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Search failed: {type(exc).__name__}: {exc}"}


async def _fetch_one(
    crawler,
    url: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Fetch a single URL using a shared crawler instance."""
    from crawl4ai import CrawlerRunConfig
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    run_config = CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(),
        verbose=False,
    )

    try:
        result = await crawler.arun(url=url, config=run_config)
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": f"Crawl failed: {type(exc).__name__}: {exc}"}

    if not result.success:
        return {
            "url": url,
            "error": (
                result.error_message
                or f"Crawl failed for {url} (status: {result.status_code})"
            ),
        }

    content = str(result.markdown) if result.markdown is not None else ""
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... truncated]"

    title = ""
    if result.metadata:
        title = result.metadata.get("title", "") or ""

    return {
        "url": url,
        "title": title,
        "content": content,
    }


async def _fetch_batch(
    crawler,
    urls: list[str],
    max_chars: int,
) -> list[dict[str, Any]]:
    """Fetch a batch of URLs concurrently."""
    tasks = [_fetch_one(crawler, url, max_chars) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed: list[dict[str, Any]] = []
    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            processed.append(
                {"url": url, "error": f"{type(result).__name__}: {result}"}
            )
        else:
            processed.append(result)
    return processed


def _chunk_text(text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Split text into overlapping chunks and tag each with its source."""
    chunks: list[dict[str, Any]] = []
    if not text:
        return chunks
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk = text[start:end]
        chunks.append(
            {
                "text": chunk.strip(),
                "url": source.get("url", ""),
                "title": source.get("title", ""),
            }
        )
        if end >= len(text):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


async def _embed_texts(texts: list[str]) -> dict[str, Any]:
    """Embed a list of texts using the configured DeepInfra embedding model."""
    if not texts:
        return {"embeddings": []}

    key = _api_key()
    if not key:
        return {"error": "No API key found. Set OPENAI_API_KEY or DEEPINFRA_API_KEY."}

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        return {"error": f"openai SDK is not installed: {exc}"}

    client = AsyncOpenAI(base_url=EMBEDDING_BASE_URL, api_key=key)
    try:
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )
        embeddings = [item.embedding for item in response.data]
        return {"embeddings": embeddings}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Embedding failed: {type(exc).__name__}: {exc}"}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _retrieve_top_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int = TOP_K_CHUNKS,
) -> list[dict[str, Any]]:
    """Embed query and chunks, then return the top-k most similar chunks."""
    if not chunks:
        return []

    texts = [query] + [c["text"] for c in chunks]
    embed_result = await _embed_texts(texts)
    if "error" in embed_result:
        raise RuntimeError(embed_result["error"])

    embeddings = embed_result["embeddings"]
    query_embedding = embeddings[0]
    chunk_embeddings = embeddings[1:]

    scored = [
        (_cosine_similarity(query_embedding, emb), chunk)
        for chunk, emb in zip(chunks, chunk_embeddings)
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [chunk for _score, chunk in scored[:top_k]]


async def web_search(
    query: str,
    max_results: int = 5,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Search the web and return the most relevant page chunks.

    Runs a DuckDuckGo search for *query*, fetches the top *max_results* pages,
    splits them into overlapping chunks, embeds the query and chunks via
    DeepInfra, and returns only the top-K most relevant chunks.

    Args:
        query: The search query.
        max_results: Number of search results to fetch (default 5).
        max_chars: Maximum characters of extracted text per page (default 10 000).

    Returns:
        A dict with the original query and a "chunks" list. Each chunk has
        keys: text, title, url.
    """
    search_result = _search_ddg(query, max_results)
    if "error" in search_result:
        return search_result

    results = search_result.get("results") or []
    urls = [
        r["url"]
        for r in results
        if r.get("url", "").startswith(("http://", "https://"))
    ]

    if not urls:
        return {
            "query": query,
            "chunks": [],
            "note": "Search returned no fetchable URLs.",
        }

    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig
    except ImportError as exc:
        return {
            "query": query,
            "chunks": [],
            "error": f"Crawl4AI is not installed: {exc}",
        }

    browser_config = BrowserConfig(verbose=False)

    fetched: list[dict[str, Any]] = []
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            for i in range(0, len(urls), FETCH_BATCH_SIZE):
                batch = urls[i : i + FETCH_BATCH_SIZE]
                fetched.extend(await _fetch_batch(crawler, batch, max_chars))
    except Exception as exc:  # noqa: BLE001
        return {
            "query": query,
            "chunks": [],
            "error": f"Failed to fetch result pages: {type(exc).__name__}: {exc}",
        }

    successful = [
        item for item in fetched if "error" not in item and item.get("content")
    ]
    if not successful:
        return {
            "query": query,
            "chunks": [],
            "error": "Could not extract content from any result page.",
        }

    all_chunks: list[dict[str, Any]] = []
    for item in successful:
        all_chunks.extend(_chunk_text(item.get("content", ""), item))

    if not all_chunks:
        return {
            "query": query,
            "chunks": [],
            "error": "No usable text chunks extracted from pages.",
        }

    try:
        top_chunks = await _retrieve_top_chunks(query, all_chunks, TOP_K_CHUNKS)
    except RuntimeError as exc:
        return {
            "query": query,
            "chunks": [],
            "error": str(exc),
        }

    return {"query": query, "chunks": top_chunks}


def calculate(expression: str) -> dict:
    """Evaluate a simple math expression safely.

    Supports +, -, *, /, parentheses, and decimal numbers.
    """
    import ast
    import operator

    allowed = {
        ast.Expression: None,
        ast.BinOp: None,
        ast.UnaryOp: None,
        ast.Num: None,
        ast.Constant: None,
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.USub: operator.neg,
        ast.Pow: operator.pow,
    }

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in allowed or allowed[op_type] is None:
                raise ValueError(f"Unsupported operator: {op_type.__name__}")
            return allowed[op_type](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in allowed or allowed[op_type] is None:
                raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
            return allowed[op_type](_eval(node.operand))
        raise ValueError(f"Unsupported node: {type(node).__name__}")

    try:
        tree = ast.parse(expression, mode="eval")
        return {"expression": expression, "result": _eval(tree)}
    except Exception as exc:
        return {"error": str(exc)}
