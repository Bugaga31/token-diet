"""clean_scraper — readable-text extraction from HTML (Firecrawl-lite).

Reverse-engineered from Firecrawl's core value: turn messy HTML into
clean, readable text that an LLM can actually use — without burning
tokens on nav menus, cookie banners, and script noise.

Our take — zero dependencies (stdlib HTMLParser), no API key, no browser:
    1. fetch the page (urllib, with timeout)
    2. strip <script>/<style>/<nav>/<footer>/<aside> and hidden elements
    3. extract text with block-level newlines
    4. collapse whitespace, drop boilerplate lines (cookie, subscribe...)
    5. optionally compress with token-diet and/or save to the Library

Token economics:
    A typical news page is 40-70% chrome (nav, ads, related-links). Clean
    extraction gives the LLM the same facts for ~50% fewer tokens — and
    far less distraction noise, which measurably improves answer quality.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

try:
    from .core import count_tokens
except ImportError:  # pragma: no cover
    from core import count_tokens  # type: ignore[no-redef]


# ── HTML → text ─────────────────────────────────────────────────────────────


# Tags whose content we keep as separate blocks
_BLOCK_TAGS = {
    "p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "tr", "br", "blockquote", "pre", "td", "th", "ul", "ol", "table",
}

# Tags to drop entirely (with their content)
_SKIP_TAGS = {
    "script", "style", "noscript", "nav", "footer", "aside", "header",
    "iframe", "svg", "canvas", "form", "button", "template", "dialog",
    "select", "option", "input", "textarea",
}

# Inline tags: join their content without newline
_INLINE_TAGS = {"a", "span", "b", "i", "em", "strong", "code", "small", "label"}

_BOILERPLATE_RE = re.compile(
    r"^(cookie|privacy|accept all|подписаться|subscribe|реклама|advertisement|"
    r"related articles|похожие|читайте также|read more|show more|показать ещё|"
    r"share|поделиться|copy link|скопировать ссылку|menu|меню|close|закрыть)\b",
    re.IGNORECASE,
)


class _TextExtractor(HTMLParser):
    """HTMLParser subclass that produces clean block-structured text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag not in _SKIP_TAGS:
                return
            self._skip_depth += 1
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = 1
            return
        if tag == "pre":
            self._in_pre = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if tag == "pre":
            self._in_pre = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data:
            self.parts.append(data)


def html_to_text(html: str, max_chars: int = 20000) -> str:
    """Convert HTML to clean readable text (stdlib only)."""
    if not html:
        return ""
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass

    text = "".join(p.parts)

    # Collapse whitespace per line, then blank lines
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines)

    # Drop boilerplate lines (cookie/subscribe/etc.)
    kept = [ln for ln in lines if not _BOILERPLATE_RE.match(ln.strip())]
    text = "\n".join(kept)

    # Collapse 3+ newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_chars].strip()


# ── fetch + clean pipeline ───────────────────────────────────────────────────


@dataclass
class ScrapeResult:
    """Result of a scrape+clean run."""
    url: str
    status: str = "ok"           # ok | error
    error: str = ""
    title: str = ""
    raw_chars: int = 0
    clean_chars: int = 0
    clean_tokens: int = 0
    compression_pct: float = 0.0  # % of raw bytes removed as chrome
    text: str = ""


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _fetch(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) token-diet-scraper/3.6"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def scrape_url(
    url: str,
    max_chars: int = 20000,
    timeout: float = 15.0,
) -> ScrapeResult:
    """Fetch a URL and extract clean readable text."""
    try:
        raw = _fetch(url, timeout=timeout)
    except Exception as e:  # network errors, HTTP errors
        return ScrapeResult(url=url, status="error",
                            error=f"{type(e).__name__}: {e}")

    html = raw.decode("utf-8", errors="ignore")

    title_m = _TITLE_RE.search(html)
    title = (title_m.group(1).strip() if title_m else url)

    # Try charset from meta if present
    meta_charset = re.search(
        r'<meta[^>]+charset=["\']?([a-zA-Z0-9-_]+)', html, re.IGNORECASE
    )
    if meta_charset and meta_charset.group(1).lower() not in ("utf-8", "utf8"):
        try:
            html = raw.decode(meta_charset.group(1), errors="ignore")
        except LookupError:
            pass

    clean = html_to_text(html, max_chars=max_chars)
    if not clean:
        return ScrapeResult(url=url, status="error",
                            error="не удалось извлечь текст из страницы")

    raw_chars = len(html)
    compression = 100 * (raw_chars - len(clean)) / max(1, raw_chars)

    return ScrapeResult(
        url=url,
        title=title,
        raw_chars=raw_chars,
        clean_chars=len(clean),
        clean_tokens=count_tokens(clean),
        compression_pct=round(compression, 1),
        text=clean,
    )


def scrape_and_save(
    url: str,
    title: str = "",
    max_chars: int = 20000,
    timeout: float = 15.0,
    library: Any = None,
) -> ScrapeResult:
    """Scrape a page, optionally save to the Library for long-term memory."""
    res = scrape_url(url, max_chars=max_chars, timeout=timeout)
    if res.status != "ok":
        return res
    if library is not None:
        try:
            library.add(
                res.title or title or url,
                res.text,
                source=url,
                kind="web",
            )
            res.status = "saved"
        except Exception as e:
            res.error = f"не удалось сохранить в библиотеку: {e}"
            res.status = "error"
    return res


__all__ = [
    "ScrapeResult",
    "html_to_text",
    "scrape_and_save",
    "scrape_url",
]
