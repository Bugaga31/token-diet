"""headroom — reversible context compression with on-demand retrieval.

Reverse-engineered from Headroom (headroomlabs-ai/headroom, Tejas Chopra):
    A local compression layer that cuts 60-95% of tokens on tool
    outputs, logs, files and RAG chunks. Three mechanisms:

    1. ContentRouter — inspects the payload, detects its type
       (JSON / logs / code / markdown / prose), and routes it to the
       right compressor instead of blind truncation.
    2. SmartCrusher  — parses the data, drops redundant rows/keys,
       and leaves a deterministic marker: <<ccr:HASH N offloaded>>
    3. CCR (Cache-aligned Reversible Retrieval) — the dropped content
       is stored locally keyed by that short hash; if the model later
       decides it NEEDS the full block, it calls headroom_retrieve(hash)
       and gets the original bytes back. Compression is REVERSIBLE.

Why this matters for token-diet:
    Normal compression is lossy and final. Headroom's trick: compress
    aggressively, but keep a local escape hatch — the cost of a wrong
    truncation is one extra call, not a permanently lost fact.

Pure stdlib.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .core import count_tokens


class CCRStore:
    """Cache-aligned reversible store: short hash → original content."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    @staticmethod
    def key(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]

    def put(self, content: str) -> str:
        k = self.key(content)
        self._store.setdefault(k, content)
        return k

    def get(self, key: str) -> str:
        """Retrieve the original content for a ccr marker (empty if gone)."""
        return self._store.get(key, "")

    def pop(self, key: str) -> str:
        return self._store.pop(key, "")

    def __len__(self) -> int:
        return len(self._store)


def _marker(kind: str, key: str, count: int, unit: str) -> str:
    return (f"<<ccr:{key} {count} {unit} offloaded. "
            f"Use headroom_retrieve('{key}') to expand.>>")


# ═══════════════════════════════════════════════════════════════════════════════
# SmartCrusher — lossy compressors that leave reversible markers
# ═══════════════════════════════════════════════════════════════════════════════

def crush_json(data: Any, store: CCRStore, max_rows: int = 8,
               max_keys: int = 12) -> dict[str, Any]:
    """JSON compression: keep sample rows + key set, offload the rest.

    Returns a dict that renders far smaller than the original:
      {"_sample": [...], "_ccr": "<<ccr:HASH N rows offloaded>>", ...}
    """
    if isinstance(data, list):
        if len(data) <= max_rows:
            return {"_rows": len(data), "_sample": data}
        kept = data[:max_rows]
        offloaded = data[max_rows:]
        key = store.put(json.dumps(offloaded, ensure_ascii=False))
        return {
            "_rows": len(data),
            "_sample": kept,
            "_ccr": _marker("rows", key, len(offloaded), "rows"),
        }

    if isinstance(data, dict):
        if len(data) <= max_keys:
            return data
        items = list(data.items())
        kept_keys = [k for k, _ in items[:max_keys]]
        kept = {k: v for k, v in items[:max_keys]}
        offloaded = {k: v for k, v in items[max_keys:]}
        key = store.put(json.dumps(offloaded, ensure_ascii=False))
        kept["_ccr"] = _marker("keys", key, len(offloaded), "keys")
        kept["_offloaded_keys"] = ", ".join(kept_keys)  # names stay visible
        return kept

    return data


_JSON_DICT_RE = re.compile(r"^\s*\{", re.DOTALL)
_JSON_LIST_RE = re.compile(r"^\s*\[", re.DOTALL)
_LOG_LINE_RE = re.compile(
    r"^(?:\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?\s+)?"  # timestamp
    r"(?:INFO|DEBUG|WARN|ERROR|TRACE|WARNING)\b", re.IGNORECASE)


def crush_logs(text: str, store: CCRStore, max_lines: int = 12) -> str:
    """Log compression: keep head + unique error lines, offload repeats.

    Repeated lines collapse to a count; the tail is offloaded to CCR.
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) <= max_lines:
        return text

    head = lines[:max_lines]
    offloaded = lines[max_lines:]

    # collapse consecutive repeats in the head (keep first + count)
    collapsed: list[str] = []
    i = 0
    while i < len(head):
        j = i
        while j + 1 < len(head) and head[j + 1] == head[i]:
            j += 1
        count = j - i + 1
        collapsed.append(head[i] if count == 1 else f"{head[i]} (x{count})")
        i = j + 1

    key = store.put("\n".join(offloaded))
    result = "\n".join(collapsed)
    if len(offloaded) > 0:
        result += "\n" + _marker("log", key, len(offloaded), "lines")
    return result


def crush_markdown(text: str, store: CCRStore, max_chars: int = 4000) -> str:
    """Markdown/docs compression: strip boilerplate, offload the tail."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    # keep headings + first max_chars, offload the rest
    kept = text[:max_chars]
    offloaded = text[max_chars:]
    key = store.put(offloaded)
    tail_marker = _marker("text", key, len(offloaded), "chars")
    # keep the last heading as an anchor so the model knows what was cut
    headings = re.findall(r"^#{1,6} .*$", text[max_chars // 2:], re.MULTILINE)
    anchor = f"\n\n… (cut {len(offloaded)} chars; remaining headings: {headings[-3:]})"
    return kept + anchor + "\n" + tail_marker


# ═══════════════════════════════════════════════════════════════════════════════
# ContentRouter — detect type, route to the right crusher
# ═══════════════════════════════════════════════════════════════════════════════

def detect_content_type(text: str) -> str:
    """Headroom ContentRouter: json | logs | markdown | code | prose."""
    t = (text or "").lstrip()
    if not t:
        return "empty"
    if _JSON_DICT_RE.match(t) or _JSON_LIST_RE.match(t):
        try:
            json.loads(t)
            return "json"
        except json.JSONDecodeError:
            pass
    log_lines = sum(1 for ln in t.splitlines()[:20] if _LOG_LINE_RE.match(ln))
    if len(t.splitlines()) >= 3 and log_lines >= max(2, len(t.splitlines()) // 2):
        return "logs"
    if re.search(r"^#{1,6}\s", t, re.MULTILINE) and len(t) > 500:
        return "markdown"
    if re.search(r"(?:def |class |function |import |#include|\bif\s*\(|=>)", t):
        return "code"
    return "prose"


def headroom_compress(
    text: str,
    store: CCRStore,
    max_rows: int = 8,
    max_chars: int = 4000,
) -> tuple[str, str]:
    """Route + crush. Returns (compressed_text, content_type).

    The compressed text contains <<ccr:HASH ...>> markers; the store
    holds the originals for headroom_retrieve().
    """
    kind = detect_content_type(text)
    if kind == "json":
        try:
            data = json.loads(text)
            crushed = crush_json(data, store, max_rows=max_rows)
            return json.dumps(crushed, ensure_ascii=False), kind
        except json.JSONDecodeError:
            pass
    if kind == "logs":
        return crush_logs(text, store, max_lines=max_rows), kind
    if kind == "markdown":
        return crush_markdown(text, store, max_chars=max_chars), kind
    # code/prose: smart truncate with CCR tail
    if len(text) > max_chars:
        key = store.put(text[max_chars:])
        return text[:max_chars] + "\n" + _marker("text", key, len(text) - max_chars, "chars"), kind
    return text, kind


def headroom_retrieve(compressed: str, store: CCRStore) -> str:
    """Expand all <<ccr:HASH>> markers in a compressed text (recursively)."""
    def _expand(m: re.Match) -> str:
        key = m.group(1)
        orig = store.get(key)
        if not orig:
            return f"[ccr:{key} not found]"
        # the retrieved block may itself contain markers — expand again
        return re.sub(_CCR_RE, _expand, orig)

    return _CCR_RE.sub(_expand, compressed)


_CCR_RE = re.compile(r"<<ccr:([0-9a-f]{12})\s[^>]*>>")


def estimate_headroom_savings(before: str, after: str) -> dict[str, Any]:
    b, a = count_tokens(before), count_tokens(after)
    saved = b - a
    return {
        "before_tokens": b,
        "after_tokens": a,
        "saved_tokens": saved,
        "savings_pct": round(100 * saved / max(1, b), 1),
        "reversible": True,
    }


__all__ = [
    "CCRStore",
    "crush_json",
    "crush_logs",
    "crush_markdown",
    "detect_content_type",
    "estimate_headroom_savings",
    "headroom_compress",
    "headroom_retrieve",
]
