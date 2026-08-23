"""docs_retriever — Context7-style documentation retrieval, offline.

Reverse-engineered from:
- Context7 MCP: keeps per-library documentation indexed; on a query it
  injects ONLY the relevant doc fragments instead of the whole page.
  The result: current, correct API answers without a giant docs dump
  in the prompt.

How we replicate it (pure stdlib, reusing our own pieces):
    docs → chunk_text() (from library.py) → keyword index
         → BM25Reranker (from bm25_reranker.py) on query
         → compact "[Docs context]" block, token-limited.

Why this matters for token-diet:
    A library README+docs can be 20-50k tokens. The 2-3 relevant
    fragments are usually <800 tokens. Injecting only those saves
    ~95% of docs tokens per call AND improves answer correctness
    (less noise to contradict the model).

Works offline. Feed it markdown/HTML/text you already fetched.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bm25_reranker import BM25Reranker
from .library import chunk_text

_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]{3,}")


@dataclass
class DocEntry:
    """One indexed documentation source."""
    slug: str
    title: str
    url: str = ""
    kind: str = "docs"           # docs | readme | reference | changelog
    chars: int = 0
    chunks: list[str] = field(default_factory=list)


@dataclass
class DocsHit:
    """One retrieved docs fragment."""
    source: str
    title: str
    chunk: int
    score: float
    text: str
    tokens: int = 0


class DocsRetriever:
    """Context7-style index: docs → chunks → BM25 → compact context.

    Usage:
        dr = DocsRetriever()
        dr.add("fastapi", "FastAPI is a modern web framework ...", url="...")
        block = dr.context_for_prompt("path parameters", max_tokens=600)
    """

    def __init__(self, index_path: str | Path | None = None):
        self.entries: dict[str, DocEntry] = {}
        self.index_path = Path(index_path) if index_path else None
        if self.index_path and self.index_path.exists():
            self._load()

    # ── indexing ─────────────────────────────────────────────────────────

    def add(
        self,
        title: str,
        text: str,
        url: str = "",
        kind: str = "docs",
        chunk_size: int = 900,
        chunk_overlap: int = 100,
    ) -> str:
        """Index a documentation text (replaces same title)."""
        slug = re.sub(r"[^0-9A-Za-zА-Яа-яЁё _-]+", "", title).strip().lower().replace(" ", "-")
        slug = slug or f"doc{len(self.entries)}"
        chunks = chunk_text(text or "", size=chunk_size, overlap=chunk_overlap)
        self.entries[slug] = DocEntry(
            slug=slug, title=title, url=url, kind=kind,
            chars=len(text or ""), chunks=chunks,
        )
        if self.index_path:
            self._save()
        return slug

    def remove(self, slug: str) -> bool:
        if slug in self.entries:
            del self.entries[slug]
            if self.index_path:
                self._save()
            return True
        return False

    # ── retrieval ────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 3, min_score: float = 0.0) -> list[DocsHit]:
        """Return the most relevant doc fragments (BM25 over all chunks)."""
        if not query.strip():
            return []
        chunks: list[str] = []
        owners: list[tuple[str, int]] = []  # (slug, chunk_idx)
        for slug, entry in self.entries.items():
            for i, ch in enumerate(entry.chunks):
                chunks.append(ch)
                owners.append((slug, i))
        if not chunks:
            return []

        r = BM25Reranker(chunks)
        hits = r.rerank(query, top_k=min(top_k, len(chunks)))
        out: list[DocsHit] = []
        for h in hits:
            if min_score and h.score < min_score:
                continue
            slug, chunk_idx = owners[h.index]
            entry = self.entries[slug]
            out.append(DocsHit(
                source=slug, title=entry.title, chunk=chunk_idx,
                score=h.score, text=h.text, tokens=h.tokens,
            ))
        return out

    def context_for_prompt(self, query: str, max_tokens: int = 600,
                           max_hits: int = 3) -> str:
        """Compact [Docs context] block for an LLM prompt."""
        hits = self.retrieve(query, top_k=max_hits)
        if not hits:
            return ""
        lines = ["[Docs context — retrieved fragments]"]
        budget = max_tokens
        used = 0
        for h in hits:
            header = f"### {h.title}"
            body = h.text.strip()
            snippet_tokens = len((header + " " + body).split())
            if used + snippet_tokens > budget and used > 0:
                break
            lines.append(header)
            lines.append(body[: int(budget * 4)])  # ~4 chars/token cap
            used += snippet_tokens
        lines.append("[End docs context]")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        total_chunks = sum(len(e.chunks) for e in self.entries.values())
        total_chars = sum(e.chars for e in self.entries.values())
        return {
            "sources": len(self.entries),
            "chunks": total_chunks,
            "total_chars": total_chars,
            "detail": [
                {"title": e.title, "kind": e.kind, "chars": e.chars,
                 "chunks": len(e.chunks)}
                for e in self.entries.values()
            ],
        }

    # ── persistence ──────────────────────────────────────────────────────

    def _save(self) -> None:
        if not self.index_path:
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            slug: {"title": e.title, "url": e.url, "kind": e.kind,
                   "chars": e.chars, "chunks": e.chunks}
            for slug, e in self.entries.items()
        }
        self.index_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _load(self) -> None:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for slug, d in data.items():
            self.entries[slug] = DocEntry(
                slug=slug, title=d.get("title", slug), url=d.get("url", ""),
                kind=d.get("kind", "docs"), chars=d.get("chars", 0),
                chunks=d.get("chunks", []),
            )


# ── honest token math ─────────────────────────────────────────────────────────

def estimate_docs_savings(full_docs_tokens: int, retrieved_tokens: int) -> dict[str, Any]:
    """Context7's value proposition, quantified.

    Typical: 30k tokens of docs vs 600 tokens of relevant fragments.
    """
    saved = full_docs_tokens - retrieved_tokens
    return {
        "full_docs_tokens": full_docs_tokens,
        "retrieved_tokens": retrieved_tokens,
        "saved_tokens": saved,
        "savings_pct": round(100 * saved / max(1, full_docs_tokens), 1),
        "calls_until_breakeven": 1,  # every single call saves
    }


__all__ = [
    "DocEntry",
    "DocsHit",
    "DocsRetriever",
    "estimate_docs_savings",
]
