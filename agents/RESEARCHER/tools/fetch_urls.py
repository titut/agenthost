"""Fetch multiple web pages in parallel using Crawl4AI."""

from __future__ import annotations

import asyncio
from typing import Any

DEFAULT_MAX_CHARS = 10_000
MAX_URLS_PER_CALL = 10
BATCH_SIZE = 5


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


async def fetch_urls(
    urls: list[str],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Fetch multiple URLs in parallel and return their readable content.

    Uses a single Crawl4AI browser session. URLs are processed in batches of 5
    to avoid overloading the browser, with up to 10 URLs accepted per call.

    Args:
        urls: A list of full URLs to fetch (http or https). Maximum 10.
        max_chars: Maximum characters of extracted text per page (default 10 000).

    Returns:
        A dict with a "results" list. Each result has keys: url, title, content,
        and optionally error.
    """
    if not urls:
        return {"results": []}

    if len(urls) > MAX_URLS_PER_CALL:
        return {
            "error": (
                f"Too many URLs: {len(urls)}. "
                f"Maximum supported per call is {MAX_URLS_PER_CALL}."
            )
        }

    invalid = [u for u in urls if not u.startswith(("http://", "https://"))]
    if invalid:
        return {"error": f"All URLs must start with http:// or https://. Invalid: {invalid}"}

    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig
    except ImportError as exc:
        return {"error": f"Crawl4AI is not installed: {exc}"}

    browser_config = BrowserConfig(verbose=False)

    async with AsyncWebCrawler(config=browser_config) as crawler:
        results: list[dict[str, Any]] = []
        for i in range(0, len(urls), BATCH_SIZE):
            batch = urls[i : i + BATCH_SIZE]
            results.extend(await _fetch_batch(crawler, batch, max_chars))

    return {"results": results}
