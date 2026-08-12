"""entity_graph — auto-mine entities and relations from text (Mem0-style).

Reverse-engineered from:
- Mem0: after each conversation turn it extracts the ENTITIES and
  FACTS, stores them in a knowledge graph, and injects the relevant
  ones next time — instead of re-reading whole history.

The missing piece in our memory stack:
    ObsidianMemoryStore needs the user (or the LLM) to explicitly write
    [[wikilinks]]. This module MINES the links automatically: entities
    (proper names, tickers, URLs, known labels) that co-occur in the
    same text become edges with a weight. Memory then answers
    "what is related to X?" without any manual linking.

Pure stdlib. Deterministic heuristics — no LLM calls, no tokens.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

# Entity patterns: proper names, tickers, URLs, quoted labels.
_ENTITY_RE = re.compile(
    r"(?:"
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{2,}(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{1,}){0,2}"  # Proper Name
    r"|[A-Z]{2,6}"                       # ticker / acronym: PLZL, SBER, API
    r"|https?://[^\s]+"                  # URL
    r"|\"([^\"]{3,40})\""                # quoted label
    r")",
)

_SKIP = {
    "the", "and", "for", "with", "from", "that", "this", "your", "our",
    "you", "not", "was", "are", "will", "have", "has", "into", "over",
    "than", "then", "there", "their", "they", "would", "could", "should",
    "when", "what", "which", "while", "about", "after", "before",
    "компания", "также", "теперь", "когда", "который", "чтобы", "будет",
    "может", "очень", "только", "уже", "ещё", "вот", "это", "что", "как",
}


@dataclass
class Entity:
    """An entity with its mention stats."""
    name: str
    kind: str = "entity"          # entity | ticker | url | person
    mentions: int = 0
    last_texts: list[str] = field(default_factory=list)

    def to_line(self) -> str:
        return f"{self.name} ({self.kind}, x{self.mentions})"


@dataclass
class Relation:
    """A weighted co-occurrence edge between two entities."""
    a: str
    b: str
    weight: float = 1.0
    contexts: list[str] = field(default_factory=list)


class EntityGraph:
    """Extracts entities, builds a weighted co-occurrence graph.

    Usage:
        g = EntityGraph()
        g.add("Полюс PLZL подорожал. PLZL зависит от золота.")
        g.related("PLZL")        # → [Relation(...), ...]
        g.prompt_context("PLZL") # compact block for the LLM
    """

    def __init__(self, max_contexts: int = 3):
        self.entities: dict[str, Entity] = {}
        self.edges: dict[tuple[str, str], Relation] = {}
        self.max_contexts = max_contexts

    # ── ingestion ─────────────────────────────────────────────────────────

    def add(self, text: str, source: str = "") -> list[str]:
        """Mine entities from text and link co-occurring pairs.

        Returns the list of entities found in this text.
        """
        found = extract_entities(text)
        for name in found:
            e = self.entities.setdefault(name, Entity(name=name, kind=_kind(name)))
            e.mentions += 1
            if len(e.last_texts) < self.max_contexts:
                e.last_texts.append((text or "").strip()[:200])

        # co-occurrence → edges (complete pairwise within one text)
        for i in range(len(found)):
            for j in range(i + 1, len(found)):
                self._bump(found[i], found[j], text)
        return found

    def _bump(self, a: str, b: str, text: str) -> None:
        key = tuple(sorted((a, b)))
        rel = self.edges.setdefault(key, Relation(a=key[0], b=key[1]))
        rel.weight += 1.0
        if len(rel.contexts) < self.max_contexts:
            rel.contexts.append((text or "").strip()[:160])

    # ── queries ───────────────────────────────────────────────────────────

    def related(self, name: str, top_k: int = 8) -> list[Relation]:
        """Entities co-occurring with `name`, strongest first."""
        out = []
        for (a, b), rel in self.edges.items():
            if a == name or b == name:
                out.append(rel)
        out.sort(key=lambda r: -r.weight)
        return out[:top_k]

    def neighbors(self, name: str, top_k: int = 8) -> list[tuple[str, float]]:
        """(neighbor, weight) pairs for `name`."""
        return [(r.b if r.a == name else r.a, r.weight)
                for r in self.related(name, top_k)]

    def prompt_context(self, name: str, top_k: int = 8, max_chars: int = 1200) -> str:
        """Compact [Knowledge graph] block for an LLM prompt."""
        e = self.entities.get(name)
        if not e:
            return ""
        rels = self.related(name, top_k)
        lines = [f"[Knowledge graph — around {name}]"]
        used = 0
        for r in rels:
            other = r.b if r.a == name else r.a
            line = f"  {name} —{r.weight:.0f}→ {other}"
            lines.append(line)
            used += len(line)
        # include a couple of contexts as evidence
        for ctx in (e.last_texts or [])[:1]:
            if used > max_chars:
                break
            lines.append(f"  ctx: {ctx[:200]}")
        lines.append("[End graph]")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        return {
            "entities": len(self.entities),
            "edges": len(self.edges),
            "top_entities": [e.to_line() for e in
                             sorted(self.entities.values(), key=lambda x: -x.mentions)[:8]],
        }

    # ── bridge to ObsidianMemoryStore ─────────────────────────────────────

    def feed_memory(self, store, title: str, content: str, kind: str = "fact",
                    tags: list[str] | None = None) -> None:
        """Mine a text, remember it, and auto-link co-occurring entities.

        The mined entities become [[wikilink]] targets so the graph
        memory (ObsidianMemoryStore) and the entity graph stay in sync.
        """
        found = self.add(content)
        # remember the note with wikilinks to co-occurring entities
        links = " ".join(f"[[{e}]]" for e in found[:6])
        note_content = content + ("\n\n" + links if links else "")
        store.remember(title, note_content, kind=kind, tags=tags or [])


# ── extraction helpers ─────────────────────────────────────────────────────────

def _kind(name: str) -> str:
    if name.startswith(("http://", "https://")):
        return "url"
    if name.isupper() and len(name) <= 6:
        return "ticker"
    if re.search(r"\b(товарищ|господин|доктор|профессор|mr|dr|prof)\b", name, re.IGNORECASE):
        return "person"
    return "entity"


def extract_entities(text: str) -> list[str]:
    """Deterministic entity candidates: proper names, tickers, URLs, labels.

    Filters stopwords and pure sentence-starters.
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _ENTITY_RE.finditer(text or ""):
        name = (m.group(1) or m.group(0)).strip('"').strip()
        if not name or len(name) < 2:
            continue
        lower = name.lower()
        if lower in _SKIP or lower.split()[0] in _SKIP:
            continue
        # "I" / single letters / sentence-start filler
        if name == "I" or (len(name) == 1):
            continue
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


__all__ = [
    "Entity",
    "EntityGraph",
    "Relation",
    "extract_entities",
]
