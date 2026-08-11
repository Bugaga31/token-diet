"""library — full-text storage with chunk indexing and retrieval.

The honest answer to "a model can't memorize a whole book": it doesn't
need to. The FULL text lives on disk (external Kingston — effectively
unlimited); we build a chunk index (chapters/blocks of ~1500 chars each
with keyword fingerprints); and on a query we retrieve only the 3-5
relevant chunks (~600 tokens). The model then answers like an expert
who opens the book at the right page.

Storage layout:
    <LIBRARY_ROOT>/
      raw/              full plain text, one file per source
        <slug>.txt
      index.json        chunk -> source, char-range, keywords
      meta.json         title, source url/path, length, date

Zero dependencies. Pure stdlib. For the people. For the planet.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 150


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё _-]+", "", text).strip().replace(" ", "_")


def _words(text: str) -> set[str]:
    """Lowercased words with length >= 4 for keyword fingerprints."""
    return {
        w for w in re.findall(r"[0-9A-Za-zА-Яа-яЁё]{4,}", text.lower())
    }


def chunk_text(text: str, size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks on word boundaries."""
    words = text.split()
    chunks: list[str] = []
    if not words:
        return chunks
    step = max(1, size - overlap)
    i = 0
    while i < len(words):
        part = " ".join(words[i:i + size])
        if part.strip():
            chunks.append(part)
        if i + size >= len(words):
            break
        i += step
    return chunks


class Library:
    """Full-text knowledge library with chunk retrieval."""

    def __init__(self, root: str | Path = "/media/ro/KINGSTON1/token-diet-library"):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.meta_path = self.root / "meta.json"
        self._index: list[dict[str, Any]] = self._load_index()

    # ── persistence ─────────────────────────────────────────────────────
    def _load_index(self) -> list[dict[str, Any]]:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save_index(self) -> None:
        self.index_path.write_text(
            json.dumps(self._index, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    def _load_meta(self) -> dict[str, Any]:
        if self.meta_path.exists():
            try:
                return json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_meta(self, meta: dict[str, Any]) -> None:
        all_meta = self._load_meta()
        all_meta[_slug(meta["title"])] = meta
        self.meta_path.write_text(
            json.dumps(all_meta, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    # ── add a source ────────────────────────────────────────────────────
    def add(
        self,
        title: str,
        full_text: str,
        source: str = "",
        kind: str = "book",
    ) -> dict[str, Any]:
        """Store full text + build chunk index. Replaces if title exists."""
        slug = _slug(title) or "source"
        raw_file = self.raw_dir / f"{slug}.txt"
        raw_file.write_text(full_text, encoding="utf-8")

        chunks = chunk_text(full_text)
        # drop old chunks for this source
        self._index = [c for c in self._index if c.get("source") != slug]
        for n, ch in enumerate(chunks):
            self._index.append({
                "source": slug,
                "chunk": n,
                "chars": len(ch),
                "words": sorted(_words(ch)),
            })
        self._save_index()

        meta = {
            "title": title,
            "source": source,
            "kind": kind,
            "chars": len(full_text),
            "chunks": len(chunks),
            "raw_file": str(raw_file),
            "added": date.today().isoformat(),
        }
        self._save_meta(meta)
        return meta

    # ── retrieval ───────────────────────────────────────────────────────
    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        """Return the most relevant chunks for a query.

        Scores by keyword overlap with the chunk fingerprint, then
        reads the raw file and slices the chunk back out. Returns:
        [{"source": slug, "title": ..., "chunk": n, "score": float,
          "text": str, "chars": int}]
        """
        q_words = _words(query)
        if not q_words:
            return []
        scored: list[tuple[float, dict]] = []
        for c in self._index:
            cw = set(c.get("words", []))
            hit = len(q_words & cw)
            if hit == 0:
                continue
            score = hit / max(1, len(q_words)) * min(1.0, c["chars"] / 800)
            scored.append((score, c))
        scored.sort(key=lambda x: -x[0])

        meta = self._load_meta()
        out: list[dict[str, Any]] = []
        for score, c in scored[:limit]:
            slug = c["source"]
            raw_file = self.raw_dir / f"{slug}.txt"
            text = ""
            if raw_file.exists():
                full = raw_file.read_text(encoding="utf-8", errors="ignore")
                # reconstruct the chunk by position: walk chunks again
                chunks = chunk_text(full)
                if c["chunk"] < len(chunks):
                    text = chunks[c["chunk"]]
            out.append({
                "source": slug,
                "title": meta.get(slug, {}).get("title", slug),
                "kind": meta.get(slug, {}).get("kind", "book"),
                "chunk": c["chunk"],
                "score": round(score, 3),
                "chars": len(text),
                "text": text,
            })
        return out

    def context_for_prompt(self, query: str, max_chars: int = 1500) -> str:
        """Compact retrieval block for an LLM prompt."""
        hits = self.search(query, limit=3)
        if not hits:
            return ""
        parts = ["[Library context — retrieved from full texts]"]
        budget = max_chars
        for h in hits:
            block = f"### {h['title']} (глава {h['chunk'] + 1})\n{h['text'][:budget // 3]}"
            parts.append(block)
        parts.append("[End library context]")
        return "\n\n".join(parts)

    def stats(self) -> dict[str, Any]:
        meta = self._load_meta()
        total_chars = sum(m.get("chars", 0) for m in meta.values())
        return {
            "sources": len(meta),
            "chunks": len(self._index),
            "total_chars": total_chars,
            "root": str(self.root),
            "sources_detail": [
                {"title": m["title"], "chars": m["chars"], "chunks": m["chunks"]}
                for m in meta.values()
            ],
        }


__all__ = ["Library", "chunk_text", "_words"]
