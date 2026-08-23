"""claude_mem — persistent memory that survives sessions (claude-mem style).

Reverse-engineered from claude-mem (thedotmack):
    Hooks into Claude Code's lifecycle (UserPromptSubmit / session
    close), captures transcripts + tool outputs, condenses them into
    distinct "observations", stores them in SQLite with FTS5, and on
    the next prompt auto-injects the most relevant ones.

Our take — deterministic, pure stdlib (sqlite3):
    1. observe(turn)   — split a conversation turn into observations:
       short factual lines (user goal, decision, constraint, fact).
    2. store           — SQLite table observations(content, project,
       created_at, hits) + FTS5 virtual table for keyword search.
    3. retrieve(q)     — hybrid score: FTS5/BMR overlap + recency +
       importance(hits) → top-k.
    4. inject(q)       — "[Memory] top-k observations [/Memory]" block
       that plugs into the system prompt automatically.

Why this matters for token-diet:
    Instead of re-reading whole history (thousands of tokens every
    turn), only the 3-5 relevant observations enter the prompt
    (~200 tokens). Memory survives sessions, so the model "remembers"
    yesterday without paying for yesterday's tokens.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .bm25_reranker import BM25Reranker
except ImportError:  # pragma: no cover
    from bm25_reranker import BM25Reranker  # type: ignore[no-redef]

# Observation extraction: sentences with decision/constraint/fact markers.
_DECISION_RE = re.compile(
    r"\b(?:решили?|decision|выбрали?|chose|приняли?|agreed|must|should|"
    r"обязательно|нельзя|запрещено|важно|important|note|запомни|remember)\b",
    re.IGNORECASE,
)
_FACT_VERBS = re.compile(
    r"\b(?:is|are|uses|uses?|runs|built|установлено|используется|стоит|"
    r"называется|находится|работает|версия|version|команда|command)\b",
    re.IGNORECASE,
)

MAX_OBSERVATION_CHARS = 240


def _split_observations(text: str) -> list[str]:
    """Split a turn into concise factual/decision observations."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    # sentences
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    out: list[str] = []
    for s in sentences:
        if len(s) < 24:          # too short to be a memory
            continue
        if len(s) > MAX_OBSERVATION_CHARS:
            s = s[:MAX_OBSERVATION_CHARS] + "…"
        if _DECISION_RE.search(s) or _FACT_VERBS.search(s):
            out.append(s)
        elif len(out) < 2 and len(s) > 60:   # long factual sentence, keep
            out.append(s)
    return out[:6]


@dataclass
class MemoryObservation:
    id: int
    content: str
    project: str = ""
    created_at: float = 0.0
    hits: int = 0

    def to_line(self) -> str:
        return f"- {self.content}"


class ClaudeMem:
    """SQLite-backed memory with FTS5 keyword search + hybrid scoring.

    Usage:
        m = ClaudeMem("memories.db", project="token-diet")
        m.observe("Пользователь решил хранить память в Obsidian.")
        block = m.inject("как мы храним память?")   # auto-inject
    """

    def __init__(self, path: str | Path | None = None, project: str = "default"):
        self.path = Path(path) if path else Path.home() / ".claude-mem" / "memories.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.project = project
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        c = self._conn
        c.execute(
            "CREATE TABLE IF NOT EXISTS observations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "content TEXT NOT NULL, project TEXT DEFAULT '',"
            "created_at REAL, hits INTEGER DEFAULT 0)")
        try:
            c.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts "
                "USING fts5(content, project)")
        except sqlite3.OperationalError:
            # FTS5 unavailable (rare) — keyword search falls back to LIKE
            pass
        c.commit()

    # ── write ────────────────────────────────────────────────────────────

    def observe(self, text: str, project: str | None = None) -> list[str]:
        """Split a turn into observations and store new ones (dedup)."""
        proj = project or self.project
        stored: list[str] = []
        for obs in _split_observations(text):
            if self._exists(obs, proj):
                continue
            now = time.time()
            cur = self._conn.execute(
                "INSERT INTO observations(content, project, created_at, hits) "
                "VALUES (?, ?, ?, 0)", (obs, proj, now))
            oid = cur.lastrowid
            try:
                self._conn.execute(
                    "INSERT INTO observations_fts(rowid, content, project) "
                    "VALUES (?, ?, ?)", (oid, obs, proj))
            except sqlite3.OperationalError:
                pass
            stored.append(obs)
        self._conn.commit()
        return stored

    def _exists(self, content: str, project: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM observations WHERE content = ? AND project = ? "
            "LIMIT 1", (content, project))
        return cur.fetchone() is not None

    # ── read ─────────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 5, project: str | None = None) -> list[MemoryObservation]:
        """Hybrid retrieval: FTS/keyword overlap + recency + importance."""
        proj = project or self.project
        rows = self._conn.execute(
            "SELECT id, content, project, created_at, hits FROM observations "
            "WHERE project = ?", (proj,)).fetchall()
        if not rows:
            return []

        # 1. keyword/FTS score via BM25Reranker (deterministic, no vectors)
        texts = [r["content"] for r in rows]
        bm = BM25Reranker(texts)
        hits = bm.rerank(query, top_k=min(top_k * 2, len(texts)))
        bm_scores = {h.index: h.score for h in hits}

        # 2. combine: BM25 + recency + hits(importance)
        now = time.time()
        scored: list[tuple[float, MemoryObservation]] = []
        for i, r in enumerate(rows):
            obs = MemoryObservation(
                id=r["id"], content=r["content"], project=r["project"],
                created_at=r["created_at"], hits=r["hits"])
            s = bm_scores.get(i, 0.0)
            age_days = (now - r["created_at"]) / 86400
            recency = 0.3 / (1 + age_days / 7)      # recent memories weigh more
            importance = min(0.3, r["hits"] * 0.05)  # re-accessed = important
            scored.append((s * 1.0 + recency + importance, obs))

        scored.sort(key=lambda x: -x[0])
        return [o for _, o in scored[:top_k]]

    def inject(self, query: str, top_k: int = 5,
               project: str | None = None) -> str:
        """Build the auto-inject [Memory] block for a prompt.

        Also bumps `hits` on the returned observations (they proved
        useful → importance grows, claude-mem's feedback loop).
        """
        obs = self.retrieve(query, top_k=top_k, project=project)
        if not obs:
            return ""
        ids = [o.id for o in obs]
        self._conn.executemany(
            "UPDATE observations SET hits = hits + 1 WHERE id = ?",
            [(i,) for i in ids])
        self._conn.commit()
        lines = ["[Memory — from previous sessions]"]
        lines += [o.to_line() for o in obs]
        lines.append("[/Memory]")
        return "\n".join(lines)

    # ── maintenance ──────────────────────────────────────────────────────

    def forget_older_than(self, days: float) -> int:
        """Delete observations older than N days. Returns count removed."""
        cutoff = time.time() - days * 86400
        rows = self._conn.execute(
            "SELECT id FROM observations WHERE created_at < ?", (cutoff,)).fetchall()
        if not rows:
            return 0
        self._conn.execute("DELETE FROM observations WHERE created_at < ?", (cutoff,))
        try:
            self._conn.execute("DELETE FROM observations_fts WHERE rowid IN "
                               "(SELECT id FROM observations WHERE created_at < ?)",
                               (cutoff,))
        except sqlite3.OperationalError:
            pass
        self._conn.commit()
        return len(rows)

    def stats(self, project: str | None = None) -> dict[str, Any]:
        proj = project or self.project
        n = self._conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(hits),0) h FROM observations "
            "WHERE project = ?", (proj,)).fetchone()
        return {
            "project": proj,
            "observations": n["c"] if n else 0,
            "total_hits": n["h"] if n else 0,
            "db": str(self.path),
        }

    def close(self) -> None:
        self._conn.close()


def estimate_memory_injection_savings(
    full_history_tokens: int, injected_tokens: int,
) -> dict[str, Any]:
    """claude-mem's value: relevant observations instead of whole history."""
    saved = full_history_tokens - injected_tokens
    return {
        "full_history_tokens": full_history_tokens,
        "injected_tokens": injected_tokens,
        "saved_tokens": saved,
        "savings_pct": round(100 * saved / max(1, full_history_tokens), 1),
    }


__all__ = [
    "ClaudeMem",
    "MemoryObservation",
    "estimate_memory_injection_savings",
    "_split_observations",
]
