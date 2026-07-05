"""Fetch and extract readable text from web pages."""

from __future__ import annotations

import os
from html.parser import HTMLParser

from agenthost.tools import current_agent_config, current_thread_id

# Per-thread set of URLs already fetched by the RESEARCHER agent. This enforces
# the 50-website research limit regardless of which model is running the agent.
_RESEARCHER_VISITED_URLS: dict[str, set[str]] = {}
_RESEARCHER_WEBSITE_LIMIT = 50


def _get_thread_id() -> str | None:
    return current_thread_id.get()


def _count_fetched_urls(thread_id: str) -> int:
    return len(_RESEARCHER_VISITED_URLS.get(thread_id, set()))


def _record_fetched_url(thread_id: str, url: str) -> int:
    urls = _RESEARCHER_VISITED_URLS.setdefault(thread_id, set())
    urls.add(url)
    return len(urls)


class _TextExtractor(HTMLParser):
    """Strips HTML tags, resolving entities into readable text."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Skip script and style blocks entirely.
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False
        # Block-level tags get a newline to separate text.
        if tag in (
            "p",
            "br",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "tr",
            "th",
            "td",
            "blockquote",
            "pre",
        ):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        char = self.unescape(f"&{name};")
        self._parts.append(char)

    def handle_charref(self, name: str) -> None:
        char = self.unescape(f"&#{name};")
        self._parts.append(char)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        # Collapse multiple blank lines into one.
        import re

        return re.sub(r"\n{3,}", "\n\n", raw).strip()


DEFAULT_MAX_CHARS = 10_000
SUMMARIZE_MAX_TEXT = 8_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def _summarize(
    text: str, url: str, title: str, query: str, model: str, base_url: str | None
) -> str:
    """Summarize extracted page text using the configured summarizer model."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        return f"[Summarization unavailable: {exc}]\n\n{text[:SUMMARIZE_MAX_TEXT]}"

    client_kwargs: dict[str, object] = {}
    if base_url:
        client_kwargs["base_url"] = base_url
    # The api_key is read from OPENAI_API_KEY by default; allow override.
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        client_kwargs["api_key"] = api_key

    client = OpenAI(**client_kwargs)

    focus = (
        f" Focus on information relevant to this research question: {query}"
        if query
        else ""
    )
    prompt = (
        f"Shorten the content of this webpage to 5000 words or less. Remember to keep key events including their dates and times.{focus}\n"
        f"URL: {url}\n"
        f"Title: {title}\n\n"
        f"{text[:SUMMARIZE_MAX_TEXT]}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise research summarizer. Extract only factual claims and concrete details. Do not add commentary or speculation.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=512,
        )
        summary = response.choices[0].message.content or ""
        return summary.strip()
    except Exception as exc:  # noqa: BLE001
        return f"[Summarization failed ({type(exc).__name__}): {exc}]\n\n{text[:SUMMARIZE_MAX_TEXT]}"


def fetch_url(
    url: str,
    query: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict:
    """Fetch a URL and return a readable summary of its content.

    Strips HTML tags, scripts, and styles. If the agent has a
    ``summarizer_model`` configured, the extracted text is summarized by that
    model before being returned; otherwise the raw text is returned (truncated
    to *max_chars*).

    Args:
        url: The full URL to fetch (http or https).
        query: The research question. Helps the summarizer focus on relevant
            facts. Optional but recommended.
        max_chars: Maximum characters of raw text to extract before
            summarization (default 10 000).

    Returns:
        A dict with keys: url, title, content (summary or raw text), and
        optionally error, urls_fetched, limit.
    """
    thread_id = _get_thread_id()
    if thread_id is not None:
        current_count = _count_fetched_urls(thread_id)
        if current_count >= _RESEARCHER_WEBSITE_LIMIT:
            return {
                "error": (
                    f"Research website limit reached ({_RESEARCHER_WEBSITE_LIMIT} unique URLs). "
                    "Stop and tell the user you have looked through 50 websites. "
                    "Only continue if the user explicitly asks you to."
                ),
                "urls_fetched": current_count,
                "limit": _RESEARCHER_WEBSITE_LIMIT,
            }
        _record_fetched_url(thread_id, url)

    try:
        import httpx
    except ImportError as exc:
        return {"error": f"httpx is not installed: {exc}"}

    # --- minimal validation ---
    if not url.startswith(("http://", "https://")):
        return {"error": "URL must start with http:// or https://"}

    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(
                url,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            html = response.text
    except httpx.TimeoutException:
        return {"error": f"Request timed out after 15 seconds: {url}"}
    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code} for {url}"}
    except httpx.RequestError as exc:
        return {"error": f"Request failed: {type(exc).__name__}: {exc}"}

    # --- extract title ---
    title = ""
    title_match = __import__("re").search(
        r"<title[^>]*>(.*?)</title>",
        html,
        __import__("re").IGNORECASE | __import__("re").DOTALL,
    )
    if title_match:
        title = title_match.group(1).strip()

    # --- strip HTML to text ---
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"HTML parsing failed: {type(exc).__name__}: {exc}"}

    text = extractor.get_text()

    # truncate before summarization
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated]"

    # --- summarize if configured ---
    config = current_agent_config.get()
    summarizer_model = (
        getattr(config, "summarizer_model", None) if config is not None else None
    )
    base_url = getattr(config, "base_url", None) if config is not None else None

    if summarizer_model:
        content = _summarize(text, url, title, query, summarizer_model, base_url)
    else:
        content = text

    return {
        "url": url,
        "title": title,
        "content": content,
        "urls_fetched": (
            _count_fetched_urls(thread_id) if thread_id is not None else None
        ),
        "limit": _RESEARCHER_WEBSITE_LIMIT if thread_id is not None else None,
    }
