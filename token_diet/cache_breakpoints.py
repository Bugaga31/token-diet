"""Automatic cache-breakpoint search (review priority #3).

Provider prompt caches are keyed on a stable prefix. One volatile value near
the top of that prefix — a timestamp, a UUID, an unsorted JSON object, a
personal greeting or a request id — invalidates the cache for every request
and silently removes the cheapest token tier (cache_read). Hunting those
values down by hand is unreliable; this module scans a candidate "static"
block and reports every breakpoint with a concrete fix.

The analyzer is deliberately deterministic and dependency-free: it runs on
the same prompt builder output that will actually be sent to the provider.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

try:
    from .core import count_tokens
except ImportError:  # standalone use (tests, scripts with token-diet-lib on path)
    from core import count_tokens  # type: ignore[no-redef]


@dataclass
class Breakpoint:
    """One volatile value found inside a cache-stable prompt region."""

    kind: str  # date | time | epoch | uuid | request_id | greeting | json_key_order | tool_schema | changed_region
    severity: str  # high | medium | low
    snippet: str
    start: int
    end: int
    suggestion: str

    def render(self) -> str:
        return (
            f"[{self.severity}] {self.kind}: "
            f"{self.snippet[:60]!r} -> {self.suggestion}"
        )


@dataclass
class CacheReport:
    static_tokens: int = 0
    volatile_tokens: int = 0
    breakpoints: list[Breakpoint] = field(default_factory=list)

    @property
    def cache_safe(self) -> bool:
        return not self.breakpoints

    @property
    def wasted_read_tokens(self) -> int:
        """Tokens billed at the expensive cache_write/input rate per request
        because a breakpoint forces the whole prefix to be re-sent fresh."""
        if not self.breakpoints:
            return 0
        return self.static_tokens

    def summary(self) -> str:
        if self.cache_safe:
            return (
                f"cache-safe: {self.static_tokens} static tokens stable; "
                f"{self.volatile_tokens} volatile tokens isolated"
            )
        return (
            f"cache-broken: {len(self.breakpoints)} breakpoint(s) in "
            f"{self.static_tokens} static tokens (worth ~{self.wasted_read_tokens} "
            f"re-read tokens/request); {self.volatile_tokens} volatile tokens isolated"
        )


_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_EPOCH_RE = re.compile(r"\b\d{10,13}\b")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_REQUEST_ID_RE = re.compile(
    r"\b(?:req(?:uest)?|rid|trace|span|corr(?:elation)?|session|run|job)[_-]?"
    r"id\s*[:=]\s*[A-Za-z0-9_\-]{6,}\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(
    r"\b(?:hello|hi|hey|good\s+(?:morning|afternoon|evening)|"
    r"привет|здравствуйте?|добрый\s+день)\b[^\n,;]{0,12}"
    r"[,:]?\s+[A-ZА-ЯЁ][\wа-яёА-ЯЁ-]{1,24}\b",
    re.IGNORECASE,
)
_TOOL_SCHEMA_RE = re.compile(r'"type"\s*:\s*"function"')
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")


def _is_sorted_keys(key_values: list[tuple[str, Any]]) -> bool:
    keys = [key for key, _value in key_values]
    return keys == sorted(keys)


class CacheBreakpointAnalyzer:
    """Scan prompt text for values that silently invalidate provider caches."""

    def analyze(self, text: str) -> list[Breakpoint]:
        if not text:
            return []

        found: list[Breakpoint] = []

        def add(kind: str, severity: str, match: re.Match[str], suggestion: str) -> None:
            found.append(
                Breakpoint(
                    kind=kind,
                    severity=severity,
                    snippet=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    suggestion=suggestion,
                )
            )

        for pattern, kind, severity, suggestion in (
            (_DATE_RE, "date", "high", "move to volatile or use a relative day"),
            (_TIME_RE, "time", "high", "move to volatile; clock values change every request"),
            (_EPOCH_RE, "epoch", "high", "move to volatile or drop the timestamp"),
            (_UUID_RE, "uuid", "high", "move to volatile; UUIDs are unique per request"),
            (_REQUEST_ID_RE, "request_id", "high", "move to volatile; changes every request"),
            (_GREETING_RE, "greeting", "high", "drop the personal greeting from the stable block"),
        ):
            for match in pattern.finditer(text):
                add(kind, severity, match, suggestion)

        for match in _JSON_OBJECT_RE.finditer(text):
            body = match.group(0)
            try:
                parsed = json.loads(body)
            except Exception:
                continue
            if not isinstance(parsed, dict) or len(parsed) < 2:
                continue
            if not _is_sorted_keys(list(parsed.items())):
                add(
                    "json_key_order",
                    "medium",
                    match,
                    "serialize with canonical_json (sort_keys=True) so key order "
                    "cannot churn the cache prefix",
                )
            # A static tool schema with "type": "function" is a normal cacheable
            # block, NOT a breakpoint. Only flag it when the schema itself
            # carries volatile values -> it is generated per request.
            if _TOOL_SCHEMA_RE.search(body) and (
                _DATE_RE.search(body)
                or _TIME_RE.search(body)
                or _EPOCH_RE.search(body)
                or _UUID_RE.search(body)
                or _REQUEST_ID_RE.search(body)
            ):
                add(
                    "tool_schema",
                    "medium",
                    match,
                    "tool schema contains volatile values; it is generated per "
                    "request and should not live in the static cache block",
                )

        # Deduplicate overlapping matches (e.g. a UUID inside a request_id).
        unique: list[Breakpoint] = []
        for item in sorted(found, key=lambda b: (b.start, b.end)):
            if unique and item.start < unique[-1].end:
                continue
            unique.append(item)

        order = {"high": 0, "medium": 1, "low": 2}
        return sorted(unique, key=lambda b: (order[b.severity], b.start))

    def analyze_prompt(
        self,
        static: list[str] | None = None,
        volatile: list[str] | None = None,
        counter=count_tokens,
    ) -> CacheReport:
        static_text = "\n\n".join(static or [])
        volatile_text = "\n\n".join(volatile or [])
        return CacheReport(
            static_tokens=counter(static_text),
            volatile_tokens=counter(volatile_text),
            breakpoints=self.analyze(static_text),
        )

    def diff(self, before: str, after: str) -> list[Breakpoint]:
        """Report what changed between two snapshots of the same prompt.

        The changed regions are themselves treated as cache breakpoints: if
        the stable block differs between requests, something is generating
        it per-request.
        """
        if before == after:
            return []

        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        sm = difflib.SequenceMatcher(a=before_lines, b=after_lines)
        changed: list[Breakpoint] = []
        offset = 0

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                offset += sum(len(line) for line in before_lines[i1:i2])
                continue
            old_text = "".join(before_lines[i1:i2])
            new_text = "".join(after_lines[j1:j2])
            start = offset
            offset += sum(len(line) for line in after_lines[j1:j2])
            snippet = new_text.strip().splitlines()[0] if new_text.strip() else old_text.strip()
            changed.append(
                Breakpoint(
                    kind="changed_region",
                    severity="high",
                    snippet=snippet[:120],
                    start=start,
                    end=start + max(len(old_text), len(new_text)),
                    suggestion=(
                        f"region changed between requests "
                        f"({len(new_text)} new chars); move it out of the static block"
                    ),
                )
            )

        return changed


def fingerprint(text: str) -> str:
    """Deterministic fingerprint of a cache-stable block (sorted, normalized).

    Two requests whose static block fingerprints differ WILL miss the cache,
    so the runner compares fingerprints across requests to prove stability.
    """
    return json.dumps(
        {"tokens": count_tokens(text), "hash": hash_text(text), "length": len(text)},
        sort_keys=True,
    )


def hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
