"""Fetch and extract readable text from web pages using Crawl4AI."""

from __future__ import annotations

from agenthost.tools import current_thread_id

# Per-thread set of URLs already fetched by the RESEARCHER agent. This enforces
# the 20-website research limit regardless of which model is running the agent.
_RESEARCHER_VISITED_URLS: dict[str, set[str]] = {}
_RESEARCHER_WEBSITE_LIMIT = 20

DEFAULT_MAX_CHARS = 10_000


def _get_thread_id() -> str | None:
    return current_thread_id.get()


def _count_fetched_urls(thread_id: str) -> int:
    return len(_RESEARCHER_VISITED_URLS.get(thread_id, set()))


def _record_fetched_url(thread_id: str, url: str) -> int:
    urls = _RESEARCHER_VISITED_URLS.setdefault(thread_id, set())
    urls.add(url)
    return len(urls)


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
        A dict with keys: url, title, content, and optionally error,
        urls_fetched, limit.
    """
    thread_id = _get_thread_id()
    if thread_id is not None:
        current_count = _count_fetched_urls(thread_id)
        if current_count >= _RESEARCHER_WEBSITE_LIMIT:
            return {
                "error": (
                    f"Research website limit reached ({_RESEARCHER_WEBSITE_LIMIT} unique URLs). "
                    "Stop and tell the user you have looked through 20 websites. "
                    "Only continue if the user explicitly asks you to."
                ),
                "urls_fetched": current_count,
                "limit": _RESEARCHER_WEBSITE_LIMIT,
            }
        _record_fetched_url(thread_id, url)

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
        "urls_fetched": (
            _count_fetched_urls(thread_id) if thread_id is not None else None
        ),
        "limit": _RESEARCHER_WEBSITE_LIMIT if thread_id is not None else None,
    }
