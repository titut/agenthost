"""Fetch and extract readable text from web pages using Crawl4AI."""

from __future__ import annotations

DEFAULT_MAX_CHARS = 10_000


async def fetch_url(
    url: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict:
    """Fetch a URL and return readable markdown content.

    Uses Crawl4AI to render the page and extract clean markdown text. The full
    page content is returned (truncated to *max_chars*); no LLM summarization is
    performed.

    Args:
        url: The full URL to fetch (http or https).
        max_chars: Maximum characters of extracted text to return
            (default 10 000).

    Returns:
        A dict with keys: url, title, content, and optionally error.
    """
    if not url.startswith(("http://", "https://")):
        return {"error": "URL must start with http:// or https://"}

    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError as exc:
        return {"error": f"Crawl4AI is not installed: {exc}"}

    browser_config = BrowserConfig(verbose=False)
    run_config = CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(),
        verbose=False,
    )

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Crawl failed: {type(exc).__name__}: {exc}"}

    if not result.success:
        return {
            "error": (
                result.error_message
                or f"Crawl failed for {url} (status: {result.status_code})"
            )
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
