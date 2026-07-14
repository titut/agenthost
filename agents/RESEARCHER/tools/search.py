"""Search tools for the RESEARCHER agent."""
from __future__ import annotations

import asyncio
from typing import Any

DEFAULT_MAX_CHARS = 10_000
FETCH_BATCH_SIZE = 5


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
            processed.append({"url": url, "error": f"{type(result).__name__}: {result}"})
        else:
            processed.append(result)
    return processed


async def web_search(
    query: str,
    max_results: int = 5,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Search the web and fetch the resulting pages.

    Runs a DuckDuckGo search for *query*, then fetches the top *max_results*
    pages in parallel using Crawl4AI. Each returned item includes the page
    title, URL, search snippet, and full extracted content (truncated to
    *max_chars*).

    Args:
        query: The search query.
        max_results: Number of search results to fetch (default 5).
        max_chars: Maximum characters of extracted text per page (default 10 000).

    Returns:
        A dict with the original query and a "results" list. Each result has
        keys: title, url, snippet, content, and optionally error.
    """
    search_result = _search_ddg(query, max_results)
    if "error" in search_result:
        return search_result

    results = search_result.get("results") or []
    urls = [r["url"] for r in results if r.get("url", "").startswith(("http://", "https://"))]

    if not urls:
        return {
            "query": query,
            "results": results,
            "note": "Search returned no fetchable URLs.",
        }

    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig
    except ImportError as exc:
        return {
            "query": query,
            "results": results,
            "error": f"Crawl4AI is not installed: {exc}",
        }

    browser_config = BrowserConfig(verbose=False)

    fetched_by_url: dict[str, dict[str, Any]] = {}
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            for i in range(0, len(urls), FETCH_BATCH_SIZE):
                batch = urls[i : i + FETCH_BATCH_SIZE]
                batch_results = await _fetch_batch(crawler, batch, max_chars)
                for item in batch_results:
                    fetched_by_url[item["url"]] = item
    except Exception as exc:  # noqa: BLE001
        return {
            "query": query,
            "results": results,
            "error": f"Failed to fetch result pages: {type(exc).__name__}: {exc}",
        }

    merged = []
    for r in results:
        url = r.get("url", "")
        fetched = fetched_by_url.get(url, {})
        merged.append(
            {
                "title": r.get("title", ""),
                "url": url,
                "snippet": r.get("snippet", ""),
                "content": fetched.get("content", ""),
                "error": fetched.get("error"),
            }
        )

    return {"query": query, "results": merged}


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
