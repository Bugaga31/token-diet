"""live_docs — live, versioned documentation injector (Context7 reverse-engineered).

Problem Context7 solves:
    LLMs hallucinate framework APIs because training data is stale.
    Context7 pulls the CURRENT docs for the requested version into context.

Our zero-dependency take (stdlib only, no API keys):
    1. fetch latest README / docs for a package (GitHub raw or docs site)
    2. cache on disk with TTL (don't re-fetch every call)
    3. compress the fetched docs with token-diet compression
    4. return a compact context block sized to your token budget

Token economics:
    Instead of a 4k-token hallucinated answer that needs fixing, inject
    300-800 tokens of REAL current docs once. Fewer retries, fewer
    correction loops, smarter code. For the people. For the planet.

Sources are plain-text friendly endpoints:
    - GitHub raw README:      https://raw.githubusercontent.com/<owner>/<repo>/HEAD/README.md
    - GitHub API contents:    https://api.github.com/repos/<owner>/<repo>/readme (base64)
    - PyPI description:       https://pypi.org/pypi/<pkg>/json
    - npm registry:           https://registry.npmjs.org/<pkg>/latest
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .core import count_tokens
    from .loss_router import compress_prose_aggressive
except ImportError:  # pragma: no cover
    from core import count_tokens  # type: ignore[no-redef]
    from loss_router import compress_prose_aggressive  # type: ignore[no-redef]

DEFAULT_CACHE_DIR = Path(os.environ.get("TOKEN_DIET_DOCS_CACHE", "~/.cache/token-diet-docs")).expanduser()
DEFAULT_TTL_HOURS = 24  # re-fetch docs at most once a day


# ── registry of known doc sources ────────────────────────────────────────────

# package name → (kind, url)
# kind: "github_readme" | "github_api_readme" | "pypi" | "npm" | "raw_url"
KNOWN_PACKAGES: dict[str, tuple[str, str]] = {
    # Python data / web
    "requests": ("pypi", "https://pypi.org/pypi/requests/json"),
    "fastapi": ("pypi", "https://pypi.org/pypi/fastapi/json"),
    "pydantic": ("pypi", "https://pypi.org/pypi/pydantic/json"),
    "sqlalchemy": ("pypi", "https://pypi.org/pypi/SQLAlchemy/json"),
    "pandas": ("pypi", "https://pypi.org/pypi/pandas/json"),
    "numpy": ("pypi", "https://pypi.org/pypi/numpy/json"),
    "django": ("pypi", "https://pypi.org/pypi/Django/json"),
    "flask": ("pypi", "https://pypi.org/pypi/Flask/json"),
    "httpx": ("pypi", "https://pypi.org/pypi/httpx/json"),
    "aiohttp": ("pypi", "https://pypi.org/pypi/aiohttp/json"),
    "asyncio": ("pypi", "https://pypi.org/pypi/aioconsole/json"),
    "celery": ("pypi", "https://pypi.org/pypi/celery/json"),
    "psycopg": ("pypi", "https://pypi.org/pypi/psycopg/json"),
    "redis": ("pypi", "https://pypi.org/pypi/redis/json"),
    "click": ("pypi", "https://pypi.org/pypi/click/json"),
    "typer": ("pypi", "https://pypi.org/pypi/typer/json"),
    "pytest": ("pypi", "https://pypi.org/pypi/pytest/json"),
    "beautifulsoup4": ("pypi", "https://pypi.org/pypi/beautifulsoup4/json"),
    # Frontend / JS
    "react": ("npm", "https://registry.npmjs.org/react/latest"),
    "next": ("npm", "https://registry.npmjs.org/next/latest"),
    "vue": ("npm", "https://registry.npmjs.org/vue/latest"),
    "typescript": ("npm", "https://registry.npmjs.org/typescript/latest"),
    "express": ("npm", "https://registry.npmjs.org/express/latest"),
    "node": ("npm", "https://registry.npmjs.org/node/latest"),
    # Infra
    "docker": ("github_api_readme", "https://api.github.com/repos/moby/moby/readme"),
    "kubernetes": ("github_api_readme", "https://api.github.com/repos/kubernetes/kubernetes/readme"),
    "terraform": ("github_api_readme", "https://api.github.com/repos/hashicorp/terraform/readme"),
    "ansible": ("github_api_readme", "https://api.github.com/repos/ansible/ansible/readme"),
    "postgres": ("github_api_readme", "https://api.github.com/repos/postgres/postgres/readme"),
    "sqlite": ("github_api_readme", "https://api.github.com/repos/sqlite/sqlite/readme"),
    # Our stack
    "telethon": ("pypi", "https://pypi.org/pypi/Telethon/json"),
    "tinkoff": ("github_api_readme", "https://api.github.com/repos/Tinkoff/invest-python/readme"),
}

# Fallback: search GitHub for a repo when not in the known list
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories?q={q}&per_page=1"


# ── data structures ──────────────────────────────────────────────────────────


@dataclass
class LiveDocsResult:
    """Fetched + compressed docs for one package."""
    package: str
    source_url: str
    fetched_at: float
    version: str = ""
    raw_chars: int = 0
    raw_tokens: int = 0
    compressed_tokens: int = 0
    savings_pct: float = 0.0
    context_block: str = ""
    error: str = ""


@dataclass
class DocsCache:
    """Disk cache for fetched docs with TTL."""
    cache_dir: Path = DEFAULT_CACHE_DIR
    ttl_hours: float = DEFAULT_TTL_HOURS

    def _path(self, package: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", package.lower())
        return self.cache_dir / f"{safe}.json"

    def get(self, package: str) -> dict[str, Any] | None:
        p = self._path(package)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        age_h = (time.time() - data.get("fetched_at", 0)) / 3600
        if age_h > self.ttl_hours:
            return None
        return data

    def put(self, package: str, data: dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._path(package).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )


# ── fetching ────────────────────────────────────────────────────────────────


def _fetch(url: str, timeout: float = 15.0, retries: int = 2) -> bytes:
    """Fetch a URL with a timeout, neutral user-agent, and retries.

    Transient SSL/EOF errors are common on flaky networks; retry before
    giving up (same lesson as telegram_monitor: reconnect, don't crash).
    """
    last_err: Exception | None = None
    for attempt in range(1 + retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent":
                        "token-diet-live-docs/3.6 (educational, for the people)"
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    if last_err:
        raise last_err
    raise RuntimeError("unreachable")


def _github_search(owner_repo: str) -> str:
    """Resolve 'owner/repo' to a raw README URL (fallback for unknown packages)."""
    if "/" in owner_repo:
        owner, repo = owner_repo.split("/", 1)
        return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
    # bare name → GitHub search
    try:
        raw = _fetch(GITHUB_SEARCH_URL.format(q=urllib.parse.quote(owner_repo)))
        data = json.loads(raw)
        if data.get("items"):
            full = data["items"][0]["full_name"]
            owner, repo = full.split("/", 1)
            return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
    except Exception:
        pass
    raise ValueError(f"Не удалось найти репозиторий для «{owner_repo}»")


def _extract_text_from_markdown(md: str) -> str:
    """Strip markdown syntax: keep the meat, drop the ceremony."""
    # drop code fences' backticks but keep code blocks content
    text = re.sub(r"```[a-zA-Z0-9_+-]*\n?", "", md)
    text = re.sub(r"```", "", text)
    # links: [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # images: ![alt](url) → drop
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    # headers: ### → keep words
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    # bold/italic
    text = re.sub(r"(\*\*|__|\*|_|~~|`)(?=[^\s])", "", text)
    # html comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pypi(description: str) -> str:
    """Extract plain text from PyPI description (markdown or reST)."""
    if not description:
        return ""
    # strip HTML if present
    if "<" in description and ">" in description:
        description = re.sub(r"<[^>]+>", " ", description)
    return _extract_text_from_markdown(description)


def fetch_package_docs(package: str) -> tuple[str, str, str, str]:
    """Fetch current docs for a package. Returns (source_url, version, full_text, error).

    Raises ValueError on unresolvable package; returns ("","","",err) on fetch failure.
    """
    package = package.strip().lower()
    if not package:
        return "", "", "", "пустое имя пакета"

    # not in known list → try GitHub
    kind, url = KNOWN_PACKAGES.get(package, ("github", package))

    try:
        if kind == "pypi":
            data = json.loads(_fetch(url))
            version = data.get("info", {}).get("version", "")
            desc = data.get("info", {}).get("description", "")
            text = _extract_pypi(desc)
            return url, version, text, ""

        if kind == "npm":
            data = json.loads(_fetch(url))
            version = data.get("version", "")
            desc = data.get("description", "")
            readme = data.get("readme", "")
            text = _extract_text_from_markdown(readme or desc)
            return url, version, text, ""

        if kind == "github_api_readme":
            data = json.loads(_fetch(url))
            version = data.get("default_branch", "HEAD")
            b64 = data.get("content", "")
            try:
                md = base64.b64decode(b64).decode("utf-8", errors="ignore")
            except Exception:
                md = ""
            return url, version, _extract_text_from_markdown(md), ""

        # raw github or search fallback
        if kind == "github":
            try:
                url = _github_search(package)
            except ValueError as e:
                return "", "", "", str(e)
        raw = _fetch(url)
        md = raw.decode("utf-8", errors="ignore")
        return url, "HEAD", _extract_text_from_markdown(md), ""
    except Exception as e:  # network / json errors
        return "", "", "", f"{type(e).__name__}: {e}"


# ── main API ────────────────────────────────────────────────────────────────


def get_live_docs(
    package: str,
    max_tokens: int = 600,
    max_chars: int = 8000,
    use_cache: bool = True,
    ttl_hours: float = DEFAULT_TTL_HOURS,
) -> LiveDocsResult:
    """Get compressed, current docs for a package, ready for prompt injection.

    Uses disk cache to avoid re-fetching. If the fetch fails but a stale
    cache exists, gracefully falls back to the stale copy (better than nothing).
    """
    cache = DocsCache(ttl_hours=ttl_hours)

    if use_cache:
        cached = cache.get(package)
        if cached and cached.get("text"):
            cached["cached"] = True
            return _build_result(package, cached, max_tokens, max_chars)

    url, version, text, error = fetch_package_docs(package)

    if not text and error:
        # fallback to stale cache
        if use_cache:
            stale = cache.get(package)
            if stale and stale.get("text"):
                stale["cached"] = True
                stale["stale_fallback"] = True
                return _build_result(package, stale, max_tokens, max_chars)
        return LiveDocsResult(package=package, source_url="", fetched_at=0,
                              error=error)

    if not text:
        return LiveDocsResult(package=package, source_url="", fetched_at=0,
                              error="документация пуста или не найдена")

    text = text[:max_chars]
    cache.put(package, {
        "package": package,
        "source_url": url,
        "version": version,
        "text": text,
        "fetched_at": time.time(),
    })
    return _build_result(package, {
        "package": package,
        "source_url": url,
        "version": version,
        "text": text,
        "fetched_at": time.time(),
    }, max_tokens, max_chars)


def _build_result(package: str, data: dict[str, Any], max_tokens: int,
                  max_chars: int) -> LiveDocsResult:
    """Compress fetched docs into a context block."""
    text = data.get("text", "")
    raw_tokens = count_tokens(text)
    raw_chars = len(text)

    compressed = text
    # compress long prose, but only if it saves meaningfully
    if raw_tokens > max_tokens * 1.2:
        try:
            compressed, _, _ = compress_prose_aggressive(text, budget_tokens=max_tokens)
        except Exception:
            compressed = text[: max_chars]

    if count_tokens(compressed) > max_tokens:
        # hard trim to budget, keeping the beginning (API surface first)
        compressed = _trim_to_tokens(compressed, max_tokens)

    compressed_tokens = count_tokens(compressed)
    savings = 100 * (raw_tokens - compressed_tokens) / max(1, raw_tokens)

    block = (
        f"[Live docs: {package} {data.get('version', '')} — "
        f"{data.get('source_url', '')}]\n"
        f"{compressed}\n"
        f"[End live docs — {compressed_tokens} tokens]"
    )

    return LiveDocsResult(
        package=package,
        source_url=data.get("source_url", ""),
        fetched_at=data.get("fetched_at", 0.0),
        version=data.get("version", ""),
        raw_chars=raw_chars,
        raw_tokens=raw_tokens,
        compressed_tokens=compressed_tokens,
        savings_pct=round(savings, 1),
        context_block=block,
        error=data.get("error", ""),
    )


def _trim_to_tokens(text: str, max_tokens: int) -> str:
    """Trim text to fit a token budget (rough estimate: 4 chars/token for RU+EN)."""
    if count_tokens(text) <= max_tokens:
        return text
    budget_chars = max(200, int(max_tokens * 3.6))
    head = text[: int(budget_chars * 0.75)]
    tail = text[-int(budget_chars * 0.25):]
    return f"{head}\n\n[... обрезано до {max_tokens} токенов ...]\n\n{tail}"


def docs_bundle(
    packages: list[str],
    max_tokens_per_doc: int = 400,
    max_docs: int = 3,
    use_cache: bool = True,
) -> tuple[str, int]:
    """Bundle several packages into one context block (for multi-doc prompts).

    Returns (context_block, total_tokens).
    """
    parts: list[str] = []
    total = 0
    for pkg in packages[:max_docs]:
        res = get_live_docs(pkg, max_tokens=max_tokens_per_doc, use_cache=use_cache)
        if res.error:
            parts.append(f"[{pkg}: {res.error}]")
        else:
            parts.append(res.context_block)
            total += res.compressed_tokens
    bundle = "\n\n".join(parts)
    return bundle, total


def flush_cache(package: str | None = None) -> int:
    """Delete cached docs. Returns number of removed files."""
    cache = DocsCache()
    if not cache.cache_dir.exists():
        return 0
    removed = 0
    for f in cache.cache_dir.glob("*.json"):
        if package is None or package.lower() in f.name:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


__all__ = [
    "DEFAULT_CACHE_DIR",
    "DEFAULT_TTL_HOURS",
    "DocsCache",
    "KNOWN_PACKAGES",
    "LiveDocsResult",
    "docs_bundle",
    "fetch_package_docs",
    "flush_cache",
    "get_live_docs",
]
