"""Fetch and extract readable text from web pages."""
from __future__ import annotations

from html.parser import HTMLParser


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
            "p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
            "li", "tr", "th", "td", "blockquote", "pre",
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
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def fetch_url(url: str, max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    """Fetch a URL and return its readable text content.

    Strips HTML tags, scripts, and styles.  Returns the page title
    (from the <title> tag) and the extracted body text, truncated
    to *max_chars* characters.

    Args:
        url: The full URL to fetch (http or https).
        max_chars: Maximum characters of text to return (default 10 000).

    Returns:
        A dict with keys: url, title, content, and optionally error.
    """
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
        r"<title[^>]*>(.*?)</title>", html, __import__("re").IGNORECASE | __import__("re").DOTALL
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

    # truncate
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated]"

    return {
        "url": url,
        "title": title,
        "content": text,
    }
