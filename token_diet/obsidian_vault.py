"""obsidian_vault: persistent memory as Obsidian-compatible markdown notes.

Why: models shouldn't re-derive facts from scratch every call. Put durable
knowledge (goals, lessons, rules, reference data) into markdown notes that
Obsidian — or any other tool — can read. The model then *retrieves* facts
instead of holding a private cache, which is cheaper AND more consistent.

This module is a thin, dependency-free wrapper around a vault folder:

    Vault/
      ├── Note A.md          (with --- frontmatter + [[wikilinks]])
      ├── Note B.md
      └── ...

    ObsidianVault("~/Documents/Obsidian")
        .write("Цели", body, tags=["goals"])   # create/update a note
        .search("рынок дивиденды")              # keyword search over notes
        .context_for_prompt("о рынке РФ")       # compact block for a prompt

Security: only ever writes markdown. Never touches sessions, keys or secrets.
For the people. For the planet.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё _-]+", "", text).strip()


class ObsidianVault:
    """Persistent markdown memory in an Obsidian-compatible folder."""

    def __init__(self, path: str | Path = "~/Documents/Obsidian"):
        self.path = Path(path).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)

    # ── write ───────────────────────────────────────────────────────────────
    def write(
        self,
        title: str,
        body: str,
        tags: list[str] | None = None,
        kind: str = "note",
        metadata: dict[str, str] | None = None,
    ) -> Path:
        """Create or replace a note. Filename is derived from the title."""
        safe = _slug(title).replace(" ", "_") or "note"
        front = (
            "---\n"
            f"title: {title}\n"
            f"tags: [{', '.join(tags or [])}]\n"
            f"kind: {kind}\n"
            f"created: {date.today().isoformat()}\n"
        )
        for k, v in (metadata or {}).items():
            front += f"{k}: {v}\n"
        front += "---\n\n"
        file = self.path / f"{safe}.md"
        file.write_text(front + body.strip() + "\n", encoding="utf-8")
        return file

    # ── read / search ───────────────────────────────────────────────────────
    def notes(self) -> list[Path]:
        return sorted(self.path.glob("*.md"))

    def read(self, name: str) -> str | None:
        """Read a note's body by title or filename (with or without .md)."""
        candidates = []
        if name.endswith(".md"):
            candidates.append(self.path / name)
            candidates.append(self.path / f"{_slug(name[:-3]).replace(' ', '_')}.md")
        else:
            candidates.append(self.path / f"{name}.md")
            candidates.append(self.path / f"{_slug(name).replace(' ', '_')}.md")
        for f in candidates:
            if f.exists():
                return f.read_text(encoding="utf-8")
        return None

    def search(self, query: str, limit: int = 5) -> list[tuple[str, float]]:
        """Keyword search over note bodies. Returns [(title, score)]."""
        q = query.lower()
        terms = [t for t in re.split(r"[^0-9A-Za-zА-Яа-яЁё]+", q) if len(t) >= 3]
        if not terms:
            return []
        scored: list[tuple[str, float]] = []
        for f in self.notes():
            text = f.read_text(encoding="utf-8").lower()
            score = sum(text.count(t) for t in terms)
            if score:
                scored.append((f.stem, float(score)))
        return sorted(scored, key=lambda x: -x[1])[:limit]

    # ── prompt integration ──────────────────────────────────────────────────
    def context_for_prompt(
        self,
        query: str,
        max_chars: int = 1200,
        fallback_notes: list[str] | None = None,
    ) -> str:
        """Compact memory block for an LLM prompt (token-cheap by design).

        Prefers notes that match the query; falls back to a curated list.
        """
        found = self.search(query, limit=3)
        titles = [t for t, _ in found]
        for t in (fallback_notes or []):
            if t not in titles:
                titles.append(t)

        # If the query found nothing, fall back to the most recent notes so the
        # model still gets *some* durable memory (better than an empty block).
        if not titles:
            files = sorted(self.notes(), key=lambda p: p.stat().st_mtime, reverse=True)
            titles = [f.stem for f in files[:3]]

        parts: list[str] = []
        budget = max_chars
        slice_titles = titles[:4]
        for t in slice_titles:
            body = self.read(t)
            if not body:
                continue
            # strip frontmatter
            body = _FRONTMATTER_RE.sub("", body)
            body = " ".join(body.split())[: budget // len(slice_titles)]
            parts.append(f"### {t}\n{body}")
        return "\n\n".join(parts) if parts else ""

    def stats(self) -> dict:
        return {"notes": len(self.notes()), "path": str(self.path)}


__all__ = ["ObsidianVault"]
