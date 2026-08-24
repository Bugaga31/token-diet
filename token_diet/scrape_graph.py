"""scrape_graph — schema-driven structured extraction (ScrapeGraphAI-style).

Reverse-engineered from ScrapeGraphAI (VinciGit00/Scrapegraph-ai):
    SmartScraperGraph runs a DAG of nodes: FetchNode (get the page) →
    ParseNode (HTML → clean text/markdown) → GenerateAnswerNode (extract
    data matching a user schema) → merge across chunks.

Our take — deterministic, zero LLM calls, zero tokens spent on the
scrape itself:
    1. Fetch      — fetch a URL (or accept HTML/text directly)
    2. Parse      — HTML → clean text (reuses clean_scraper.html_to_text)
    3. Extract    — pull high-confidence facts first: JSON-LD, meta/OG
                     tags, then label:value regex per schema field
    4. Merge      — combine per-chunk extractions, first-wins per field

Why this matters for token-diet:
    ScrapeGraphAI needs an LLM call (and its tokens) to extract data.
    The deterministic pipeline gets the SAME structured facts for free:
    raw HTML (10-50k tokens) → schema JSON (a few hundred tokens).
    The model then only sees the facts it actually asked for.

Pure stdlib. Reuses clean_scraper.html_to_text + core.count_tokens.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .clean_scraper import html_to_text
from .core import count_tokens

# ═══════════════════════════════════════════════════════════════════════════════
# JSON-LD + meta extraction (deterministic high-confidence facts)
# ═══════════════════════════════════════════════════════════════════════════════

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def extract_json_ld(html: str) -> list[dict]:
    """Parse all JSON-LD blocks into dicts (safe against malformed JSON)."""
    out: list[dict] = []
    for m in _JSONLD_RE.finditer(html or ""):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(data)
        elif isinstance(data, list):
            out.extend(d for d in data if isinstance(d, dict))
    return out


_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)


def extract_meta_tags(html: str) -> dict[str, str]:
    """Extract meta + OpenGraph tags: {og:title: value, description: ...}."""
    out: dict[str, str] = {}
    for m in _META_RE.finditer(html or ""):
        key, val = m.group(1).strip(), m.group(2).strip()
        if key and val:
            out.setdefault(key.lower(), val)
    return out


def ld_flatten(data: dict, depth: int = 0) -> dict[str, Any]:
    """Flatten a JSON-LD dict into leaf facts (skip @context, images, urls)."""
    facts: dict[str, Any] = {}
    if depth > 3:
        return facts
    for k, v in (data or {}).items():
        if k.startswith("@"):
            continue
        if isinstance(v, dict):
            facts.update(ld_flatten(v, depth + 1))
        elif isinstance(v, list):
            vals = [i for i in v if isinstance(i, str | int | float)]
            if vals:
                facts[k] = ", ".join(str(x) for x in vals[:5])
        elif isinstance(v, str | int | float) and str(v).strip():
            facts[k] = str(v).strip()
    return facts


# ═══════════════════════════════════════════════════════════════════════════════
# Schema-driven deterministic extraction
# ═══════════════════════════════════════════════════════════════════════════════

# Field-name aliases → regexes for the most useful structured facts.
_ALIASES: dict[str, list[str]] = {
    "title": ["title", "название", "заголовок", "name", "product"],
    "price": ["price", "цена", "стоимость", "cost", "amount"],
    "rating": ["rating", "рейтинг", "оценка", "score"],
    "date": ["date", "дата", "published", "время", "datetime"],
    "author": ["author", "автор"],
    "email": ["email", "почта", "e-mail", "mail"],
    "phone": ["phone", "телефон", "tel", "contact"],
    "address": ["address", "адрес", "location"],
    "url": ["url", "ссылка", "link", "website"],
    "description": ["description", "описание", "summary", "аннотация", "about"],
}

_VALUE_RE: dict[str, str] = {
    "price": r"(?:\d[\d\s.,]{0,12})\s*(?:₽|руб|руб\.|\$|€|р\.)",
    "rating": r"\d(?:[.,]\d)?\s*/\s*5|\d(?:[.,]\d)?\s*(?:из|из 5|of 5)",
    "date": r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "phone": r"(?:\+7|8|7)\s?[\d(]{1,4}[\d\s()-]{6,}\d",
    "url": r"https?://[^\s\"'<>]+",
}

def _label_value_regex(label: str) -> re.Pattern:
    """Build a regex that finds 'Label...: value' where the label is fuzzy.

    Allows up to 25 chars of slack between the label and the colon so
    'Автор обзора: Иван' matches the alias 'автор'.
    """
    esc = re.escape(label)
    # allow optional spaces/hyphens between words in the label
    fuzzy = r"[\s\-_]*".join(esc.split(r"\ ")) if "\\ " in esc else esc
    return re.compile(
        r"(?:^|[\n\r])\s*"
        + fuzzy
        + r"[^:\n]{0,25}?\s*[:—-]\s*(?P<value>[^\n]{1,200})",
        re.IGNORECASE,
    )


def extract_schema_fields(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Deterministic extraction of schema fields from clean text.

    schema format: {"field": "description or hint", ...} — a value may
    also be a dict with keys: hint | pattern | required | type.

    Order of attack per field (cheap → specific):
      1. known-field regex (price/rating/date/email/phone/url)
      2. "Label: value" pattern where label matches the field or aliases
      3. custom regex supplied in the schema
    Returns {field: {"value": ..., "found": bool}}.
    """
    result: dict[str, Any] = {}
    for field_name, spec in (schema or {}).items():
        hint = spec.get("hint", "") if isinstance(spec, dict) else str(spec)
        pattern = spec.get("pattern") if isinstance(spec, dict) else None

        # 1. known-field regex (only for fields that have a value regex)
        value = ""
        base = field_name.lower().split()[0]
        for key, aliases in _ALIASES.items():
            if (field_name.lower() in aliases or base in aliases) and key in _VALUE_RE:
                m = re.search(_VALUE_RE[key], text)
                if m:
                    value = m.group(0).strip()
                break

        # 2. label:value with aliases (fuzzy label matching)
        if not value:
            aliases = _ALIASES.get(field_name.lower(), [field_name])
            for lab in [field_name] + aliases:
                m = _label_value_regex(lab).search(text)
                if m:
                    value = m.group("value").strip().rstrip(",;")
                    break

        # 3. custom pattern
        if not value and pattern:
            m = re.search(pattern, text)
            if m:
                value = m.group(0).strip()

        # 4. keyword proximity (description-style fields): nearest sentence
        if not value and len(field_name) > 2:
            value = _proximity_value(text, field_name, hint)

        result[field_name] = {
            "value": value,
            "found": bool(value),
        }
    return result


def _proximity_value(text: str, field_name: str, hint: str) -> str:
    """Grab the sentence containing the field label (description fallback)."""
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    hay = [field_name]
    if hint:
        hay += [w for w in re.split(r"\W+", hint.lower()) if len(w) > 3][:3]
    for s in sentences:
        low = s.lower()
        if any(h.lower() in low for h in hay):
            return s.strip()[:200]
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# Chunked map-reduce merge (context-overflow defence, like ScrapeGraphAI)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_across_chunks(
    text: str,
    schema: dict[str, Any],
    chunk_size: int = 3000,
) -> dict[str, Any]:
    """Extract per chunk, merge first-wins per field.

    Mirrors ScrapeGraphAI's chunk → extract → merge for long pages,
    without an LLM: values found in earlier chunks win; later chunks
    fill fields that were still empty.
    """
    merged: dict[str, Any] = {}
    for field_name in schema:
        merged[field_name] = {"value": "", "found": False}

    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    for chunk in chunks:
        partial = extract_schema_fields(chunk, schema)
        for field_name, res in partial.items():
            if not merged[field_name]["found"] and res["found"]:
                merged[field_name] = res
    return merged


# ═══════════════════════════════════════════════════════════════════════════════
# Graph pipeline (SmartScraperGraph-style DAG, deterministic)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ScrapeGraphResult:
    """Structured result of a scrape+extract run."""
    url: str = ""
    status: str = "ok"              # ok | error
    error: str = ""
    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    json_ld: list[dict] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)  # from schema
    raw_tokens: int = 0
    structured_tokens: int = 0
    savings_pct: float = 0.0

    def to_prompt_block(self) -> str:
        """Compact [Extracted data] block ready for an LLM prompt."""
        lines = ["[Extracted data]"]
        if self.title:
            lines.append(f"  title: {self.title}")
        for k, v in self.meta.items():
            if v and k in ("og:title", "og:description", "description"):
                lines.append(f"  {k}: {v[:120]}")
        for k, res in self.facts.items():
            if res.get("found"):
                lines.append(f"  {k}: {res['value'][:160]}")
        lines.append("[End data]")
        return "\n".join(lines)


def _fetch(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) token-diet-graph/3.15"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read()
    return raw.decode("utf-8", errors="ignore")


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class ScrapeGraph:
    """Deterministic SmartScraperGraph: Fetch → Parse → Extract → Merge.

    Usage:
        g = ScrapeGraph()
        res = g.run("https://example.com/product", schema={
            "price": "цена товара",
            "title": "название",
            "rating": "рейтинг",
        })
        print(res.to_prompt_block())
    """

    def __init__(self, chunk_size: int = 3000):
        self.chunk_size = chunk_size

    def fetch(self, url: str, timeout: float = 15.0) -> str:
        """FetchNode: URL → raw HTML."""
        return _fetch(url, timeout=timeout)

    def parse(self, html: str) -> str:
        """ParseNode: HTML → clean text (reuses html_to_text)."""
        return html_to_text(html)

    def extract(self, text: str, schema: dict) -> dict[str, Any]:
        """ExtractNode: clean text → schema facts (chunked, merged)."""
        return extract_across_chunks(text, schema, self.chunk_size)

    def run(
        self,
        source: str,
        schema: dict[str, Any],
        timeout: float = 15.0,
        is_html: bool = False,
    ) -> ScrapeGraphResult:
        """Run the whole graph on a URL (or raw HTML/text)."""
        res = ScrapeGraphResult(url=source if not is_html else "(html)")
        try:
            if is_html:
                html = source
            elif source.startswith(("http://", "https://")):
                html = self.fetch(source, timeout=timeout)
            else:
                # treat as plain text already
                text = source
                html = ""
                res.facts = self.extract(text, schema)
                res.raw_tokens = count_tokens(text)
                res.structured_tokens = count_tokens(
                    json.dumps(res.facts, ensure_ascii=False))
                res.savings_pct = _pct(res.raw_tokens, res.structured_tokens)
                return res

            tm = _TITLE_RE.search(html)
            res.title = tm.group(1).strip() if tm else source
            res.json_ld = extract_json_ld(html)
            res.meta = extract_meta_tags(html)

            # high-confidence structured facts straight from JSON-LD
            ld_facts: dict[str, Any] = {}
            for blk in res.json_ld:
                for k, v in ld_flatten(blk).items():
                    ld_facts.setdefault(k, v)

            text = self.parse(html)
            if not text and not ld_facts:
                return ScrapeGraphResult(
                    url=source, status="error", error="не удалось извлечь контент")

            res.facts = self.extract(text, schema) if text else {}

            # fill empty schema fields from JSON-LD facts (name matching)
            for field_name, entry in res.facts.items():
                if entry["found"]:
                    continue
                for ld_key, ld_val in ld_facts.items():
                    if field_name.lower() in ld_key.lower():
                        entry["value"] = str(ld_val)[:200]
                        entry["found"] = True
                        break

            res.raw_tokens = count_tokens(html)
            res.structured_tokens = count_tokens(
                json.dumps({k: v for k, v in res.facts.items()},
                           ensure_ascii=False))
            res.savings_pct = _pct(res.raw_tokens, res.structured_tokens)
            return res
        except Exception as e:  # network/parse errors
            return ScrapeGraphResult(
                url=source, status="error", error=f"{type(e).__name__}: {e}")


def _pct(raw: int, kept: int) -> float:
    return round(100 * (raw - kept) / max(1, raw), 1)


def estimate_scrape_savings(raw_html_tokens: int, structured_tokens: int) -> dict[str, Any]:
    """ScrapeGraphAI's value, quantified: facts instead of raw HTML."""
    saved = raw_html_tokens - structured_tokens
    return {
        "raw_html_tokens": raw_html_tokens,
        "structured_tokens": structured_tokens,
        "saved_tokens": saved,
        "savings_pct": _pct(raw_html_tokens, structured_tokens),
    }


__all__ = [
    "ScrapeGraph",
    "ScrapeGraphResult",
    "estimate_scrape_savings",
    "extract_json_ld",
    "extract_meta_tags",
    "extract_schema_fields",
    "extract_across_chunks",
    "ld_flatten",
]
