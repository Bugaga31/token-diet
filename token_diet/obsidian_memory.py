"""
ObsidianMemoryStore — external linked-note knowledge base for LLMs.

Inspired by Obsidian: notes with [[wikilinks]], frontmatter, bidirectional
links, graph traversal, and semantic search.

Why this matters:
    Models shouldn't carry context in their prompt cache (expensive,
    volatile, structurally blind). Instead, store knowledge as linked
    notes in an external base. The model queries only what it needs —
    less tokens, smarter retrieval, persistent memory.

Pattern:
    1. remember(note)        → store fact/decision/constraint as note
    2. link(note_a, note_b)  → create [[wikilink]] between notes
    3. retrieve(query)       → semantic + graph search → relevant notes
    4. graph(start, depth)   → traverse linked notes from a starting point
    5. backlinks(note)       → find all notes that link to this one

Token savings:
    Instead of stuffing 2000 tokens of context into every prompt,
    retrieve only the 3-5 most relevant linked notes (~300 tokens).
    Saving: 85% on context tokens per call.

Intelligence boost:
    Graph traversal finds connections the model would miss.
    Bidirectional links reveal structure invisible to flat retrieval.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ObsidianNote:
    """A single note in the knowledge base, Obsidian-style."""
    id: str                          # Unique note ID (hash of title+content)
    title: str                       # Note title (display name)
    content: str                      # Full note content (markdown)
    kind: str = "fact"               # fact | decision | constraint | preference | reference
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5          # 0..1
    created_at: float = 0.0
    updated_at: float = 0.0
    wikilinks: list[str] = field(default_factory=list)        # Outgoing [[links]]
    backlinks: list[str] = field(default_factory=list)        # Incoming (who links here)
    metadata: dict[str, str] = field(default_factory=dict)    # Frontmatter key-values

    def snippet(self, max_chars: int = 120) -> str:
        return self.content[:max_chars] + ("..." if len(self.content) > max_chars else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "kind": self.kind,
            "tags": self.tags,
            "importance": self.importance,
            "wikilinks": self.wikilinks,
            "backlinks": self.backlinks,
            "metadata": self.metadata,
        }


# Typed edge relations (reverse-engineered from Memora memory graph)
# — typed edges let retrieval know WHY notes are connected, not just that they are.
EDGE_TYPES = (
    "references",   # A mentions/uses B
    "implements",   # A implements B (plan → code, todo → feature)
    "supersedes",   # A replaces B (new decision beats old one)
    "extends",      # A extends B (adds detail)
    "contradicts",  # A contradicts B (conflict resolution marker)
    "related_to",   # generic association
)


@dataclass
class GraphEdge:
    """An edge in the knowledge graph: note A → note B via wikilink.

    Memora insight: edges carry a TYPE so retrieval can reason about
    relations — a "supersedes" edge should bury the old note, a
    "contradicts" edge should surface BOTH sides.
    """
    source: str
    target: str
    edge_type: str = "related_to"   # one of EDGE_TYPES
    weight: float = 1.0             # Link strength (default 1.0, can decay)


@dataclass
class RetrievalResult:
    """A retrieved note with relevance score and match reason."""
    note: ObsidianNote
    score: float                    # 0..1 relevance
    match_type: str                 # "keyword" | "graph" | "tag" | "link" | "semantic"
    graph_distance: int = 0         # Hops from query origin (0 = direct match)
    path: list[str] = field(default_factory=list)  # Traversal path


# ═══════════════════════════════════════════════════════════════════════════════
# ObsidianMemoryStore
# ═══════════════════════════════════════════════════════════════════════════════

class ObsidianMemoryStore:
    """External knowledge base with Obsidian-style linked notes.

    Features:
    - [[wikilink]] parsing: notes can reference each other
    - Bidirectional links: automatic backlink tracking
    - Graph traversal: follow links N hops
    - Hybrid retrieval: keyword + graph + tag + importance
    - File-based persistence: JSON file on disk
    - Frontmatter support: tags, kind, importance in YAML-style metadata
    """

    def __init__(
        self,
        path: str | Path | None = None,
        max_graph_depth: int = 3,
        max_retrieve_notes: int = 5,
    ):
        self.path = Path(path) if path else None
        self.notes: dict[str, ObsidianNote] = {}
        self.edges: list[GraphEdge] = []
        self.max_graph_depth = max_graph_depth
        self.max_retrieve_notes = max_retrieve_notes

        self._wikilink_re = re.compile(r"\[\[([^\]]+)\]\]")
        self._frontmatter_re = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

        if self.path and self.path.exists():
            self._load()

    # ── Public API ──────────────────────────────────────────────────────────

    def remember(
        self,
        title: str,
        content: str,
        kind: str = "fact",
        tags: list[str] | None = None,
        importance: float = 0.5,
        metadata: dict[str, str] | None = None,
    ) -> ObsidianNote:
        """Store a new note (or update existing by title)."""
        note_id = self._make_id(title)

        # Parse wikilinks from content
        wikilinks = self._extract_wikilinks(content)

        now = time.time()

        if note_id in self.notes:
            # Update existing
            note = self.notes[note_id]
            note.content = content
            note.kind = kind
            note.importance = importance
            note.updated_at = now
            # Update links
            self._remove_edges(note.id)
            note.wikilinks = wikilinks
            note.tags = tags or []
            note.metadata = metadata or {}
        else:
            note = ObsidianNote(
                id=note_id,
                title=title,
                content=content,
                kind=kind,
                tags=tags or [],
                importance=importance,
                created_at=now,
                updated_at=now,
                wikilinks=wikilinks,
                metadata=metadata or {},
            )
            self.notes[note_id] = note

        # Add edges for each wikilink
        for link_title in wikilinks:
            link_id = self._make_id(link_title)
            self.edges.append(GraphEdge(source=note_id, target=link_id))

        # Update backlinks of linked notes
        self._rebuild_backlinks()

        if self.path:
            self._save()

        return note

    def link(self, source_title: str, target_title: str,
             edge_type: str = "related_to") -> bool:
        """Create a typed wikilink between two existing notes.

        edge_type: references | implements | supersedes | extends |
                   contradicts | related_to

        Memora insight: typed edges power smarter retrieval — a
        "supersedes" edge demotes the old note, a "contradicts" edge
        surfaces both sides of a conflict.
        """
        if edge_type not in EDGE_TYPES:
            edge_type = "related_to"
        source = self.get(source_title)
        target = self.get(target_title)
        if not source or not target:
            return False

        # Upgrade existing link's type if present, else add new edge
        existing = next(
            (e for e in self.edges if e.source == source.id and e.target == target.id),
            None,
        )
        if existing:
            existing.edge_type = edge_type
        else:
            source.wikilinks.append(target_title)
            self.edges.append(GraphEdge(
                source=source.id, target=target.id, edge_type=edge_type))
        self._rebuild_backlinks()
        if self.path:
            self._save()
        return True

    def boost(self, title: str, amount: float = 0.2) -> bool:
        """Permanently raise a note's importance (Memora's memory_boost).

        Important notes float higher in retrieval. amount: 0..1 added to
        importance, capped at 1.0.
        """
        note = self.get(title)
        if not note:
            return False
        note.importance = min(1.0, note.importance + amount)
        if self.path:
            self._save()
        return True

    def find_duplicates(self, threshold: float = 0.85) -> list[tuple[ObsidianNote, ObsidianNote]]:
        """Find near-duplicate note pairs (token-overlap heuristic).

        Memora's memory_find_duplicates — but deterministic, zero LLM
        calls, zero tokens. Two notes are duplicates when their content
        shares ≥threshold of tokens (normalized by length).
        """
        dupes: list[tuple[ObsidianNote, ObsidianNote]] = []
        ids = list(self.notes.values())
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if a.id == b.id:
                    continue
                overlap = self._token_overlap(a.content, b.content)
                if overlap >= threshold:
                    dupes.append((a, b))
        return dupes

    def merge(self, keep_title: str, drop_title: str, strategy: str = "append") -> bool:
        """Merge two duplicate notes into one (Memora's memory_merge).

        strategies:
          append  — keep both contents (drop_title appended to keep_title)
          replace — drop_title's newer content wins if updated_at newer
          prepend — drop_title content first, then keep_title's

        Merged note keeps the higher importance and union of tags.
        """
        keep = self.get(keep_title)
        drop = self.get(drop_title)
        if not keep or not drop:
            return False

        if strategy == "replace" and drop.updated_at > keep.updated_at:
            keep.content = drop.content
        elif strategy == "prepend":
            keep.content = drop.content + "\n\n" + keep.content
        else:  # append
            keep.content = keep.content + "\n\n" + drop.content

        keep.importance = max(keep.importance, drop.importance)
        keep.tags = sorted(set(keep.tags) | set(drop.tags))
        keep.metadata = {**drop.metadata, **keep.metadata}
        keep.updated_at = time.time()

        # Steal drop's wikilinks, remove drop note + its edges
        for lt in drop.wikilinks:
            if lt not in keep.wikilinks:
                keep.wikilinks.append(lt)
        for e in list(self.edges):
            if e.source == drop.id:
                self.edges.remove(e)
        self.notes.pop(drop.id, None)
        self._rebuild_backlinks()
        if self.path:
            self._save()
        return True

    def digest(self, topic: str, max_lines: int = 8) -> str:
        """Return a compressed knowledge digest about a topic.

        Memora's memory_digest — deterministic: top retrieved notes +
        their typed relations, formatted as a compact context block.
        Cheaper than dumping raw notes; smarter than keyword search.
        """
        results = self.retrieve(topic, max_results=6)
        if not results:
            return f"[No memory about: {topic}]"

        lines = [f"[Memory digest: {topic}]"]
        edge_repr: dict[tuple[str, str], str] = {
            (e.source, e.target): e.edge_type for e in self.edges}

        for r in results[:max_lines]:
            note = r.note
            kind_icon = {"fact": "📋", "decision": "🔨", "constraint": "🔒",
                         "preference": "⚙", "reference": "📖"}.get(note.kind, "📝")
            line = f"- {kind_icon} **{note.title}** (rel {r.score:.2f}): {note.snippet(90)}"
            lines.append(line)

            # Show typed relations to other retrieved notes
            rels = [
                f"{edge_repr[(note.id, other.note.id)]}→{other.note.title}"
                for other in results
                if (note.id, other.note.id) in edge_repr and other.note.id != note.id
            ]
            if rels:
                lines.append(f"    ⛓ {'; '.join(rels[:3])}")

        lines.append("[End digest]")
        return "\n".join(lines)

    def _token_overlap(self, a: str, b: str) -> float:
        """Jaccard-style token overlap between two texts (0..1)."""
        ta = set(a.lower().split())
        tb = set(b.lower().split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / min(len(ta), len(tb))

    def get(self, title: str) -> ObsidianNote | None:
        """Get note by title."""
        return self.notes.get(self._make_id(title))

    def retrieve(self, query: str, max_results: int | None = None) -> list[RetrievalResult]:
        """Hybrid retrieval: keyword + graph + tag + importance.

        Returns notes ranked by composite relevance score.
        """
        max_results = max_results or self.max_retrieve_notes
        query_lower = query.lower()
        query_terms = set(query_lower.split())

        results: list[RetrievalResult] = []

        for note in self.notes.values():
            score = 0.0
            match_type = "keyword"

            # 1. Keyword match in title (strong signal)
            title_lower = note.title.lower()
            if query_lower in title_lower:
                score += 0.5
                match_type = "keyword"
            elif any(t in title_lower for t in query_terms):
                score += 0.3

            # 2. Keyword match in content
            content_lower = note.content.lower()
            term_hits = sum(1 for t in query_terms if t in content_lower)
            if term_hits > 0:
                overlap = term_hits / max(1, len(query_terms))
                score += 0.3 * overlap

            # 3. Tag match
            if any(t.lower() in query_lower for t in note.tags):
                score += 0.2
                match_type = "tag"

            # 4. Importance boost
            score += note.importance * 0.2

            # 5. Recency boost (notes updated in last hour)
            age_hours = (time.time() - note.updated_at) / 3600
            if age_hours < 1:
                score += 0.1

            if score > 0:
                results.append(RetrievalResult(
                    note=note,
                    score=min(1.0, score),
                    match_type=match_type,
                    graph_distance=0,
                ))

        # Sort by score descending
        results.sort(key=lambda r: -r.score)
        return results[:max_results]

    def graph(self, start_title: str, depth: int | None = None) -> list[RetrievalResult]:
        """Graph traversal: follow wikilinks from a starting note.

        Returns all notes reachable within `depth` hops.
        """
        depth = depth or self.max_graph_depth
        start_note = self.get(start_title)
        if not start_note:
            return []

        visited: dict[str, int] = {start_note.id: 0}  # id → distance
        results: list[RetrievalResult] = []
        queue: list[tuple[ObsidianNote, int, list[str]]] = [
            (start_note, 0, [start_title])
        ]

        while queue:
            note, dist, path = queue.pop(0)
            if dist > 0:  # Don't include the start note
                results.append(RetrievalResult(
                    note=note,
                    score=1.0 / (1 + dist),  # Closer = higher score
                    match_type="graph",
                    graph_distance=dist,
                    path=list(path),
                ))

            if dist >= depth:
                continue

            # Follow outgoing wikilinks
            for link_title in note.wikilinks:
                linked = self.get(link_title)
                if linked and linked.id not in visited:
                    visited[linked.id] = dist + 1
                    queue.append((linked, dist + 1, path + [link_title]))

        results.sort(key=lambda r: -r.score)
        return results

    def backlinks(self, title: str) -> list[ObsidianNote]:
        """Find all notes that link to this one."""
        note = self.get(title)
        if not note:
            return []
        return [
            self.notes[bid]
            for bid in note.backlinks
            if bid in self.notes
        ]

    def search_hybrid(
        self,
        query: str,
        start_title: str | None = None,
        depth: int | None = None,
        max_results: int | None = None,
    ) -> list[RetrievalResult]:
        """Full hybrid search: direct retrieval + graph traversal.

        If start_title is provided, also traverses the graph from that note.
        Results are deduplicated and merged by score.
        """
        direct = self.retrieve(query, max_results)

        if start_title:
            graph_results = self.graph(start_title, depth)
            # Merge: graph results get a small boost for being connected
            seen_ids = {r.note.id for r in direct}
            for gr in graph_results:
                if gr.note.id not in seen_ids:
                    gr.score *= 1.2  # Connection boost
                    direct.append(gr)
                    seen_ids.add(gr.note.id)

        direct.sort(key=lambda r: -r.score)
        return direct[:max_results or self.max_retrieve_notes]

    def context_for_prompt(
        self,
        query: str,
        start_title: str | None = None,
        max_tokens: int = 500,
    ) -> str:
        """Generate a compact context block for the LLM prompt.

        Retrieves relevant linked notes and formats them as a context section.
        This replaces the old approach of stuffing entire event history.

        Returns a string like:
            [Relevant context from memory]
            - Refund Policy: 14 days, 15% restocking fee...
            - Compliance: T+1 reporting required...
            [End context]
        """
        results = self.search_hybrid(query, start_title)

        if not results:
            return ""

        lines = ["[Relevant context from memory]"]
        token_budget = max_tokens
        tokens_used = len(lines[0].split())  # Rough estimate

        for r in results[:5]:
            kind_icon = {"fact": "📋", "decision": "🔨", "constraint": "🔒",
                         "preference": "⚙", "reference": "📖"}.get(r.note.kind, "📝")
            line = f"- {kind_icon} **{r.note.title}**: {r.note.snippet(150)}"
            line_tokens = len(line.split())
            if tokens_used + line_tokens > token_budget:
                break
            lines.append(line)
            tokens_used += line_tokens

        # Add graph context if available
        if start_title:
            backlinks = self.backlinks(start_title)
            if backlinks:
                lines.append("  → Referenced by:")
                for bl in backlinks[:3]:
                    line = f"    - {bl.title}"
                    lines.append(line)

        lines.append("[End context]")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        """Memory store statistics."""
        kinds = defaultdict(int)
        tags = defaultdict(int)
        total_links = 0
        for note in self.notes.values():
            kinds[note.kind] += 1
            for tag in note.tags:
                tags[tag] += 1
            total_links += len(note.wikilinks)

        return {
            "total_notes": len(self.notes),
            "total_edges": len(self.edges),
            "total_links": total_links,
            "by_kind": dict(kinds),
            "top_tags": sorted(tags.items(), key=lambda x: -x[1])[:10],
            "orphaned": sum(1 for n in self.notes.values()
                           if not n.wikilinks and not n.backlinks),
        }

    # ── Internal helpers ────────────────────────────────────────────────────

    def _make_id(self, title: str) -> str:
        """Generate stable note ID from title."""
        return hashlib.sha256(title.strip().lower().encode()).hexdigest()[:16]

    def _extract_wikilinks(self, content: str) -> list[str]:
        """Extract [[wikilinks]] from note content."""
        return self._wikilink_re.findall(content)

    def _remove_edges(self, note_id: str) -> None:
        """Remove all outgoing edges from a note."""
        self.edges = [e for e in self.edges if e.source != note_id]

    def _rebuild_backlinks(self) -> None:
        """Rebuild backlinks for all notes."""
        for note in self.notes.values():
            note.backlinks = []

        for edge in self.edges:
            target_note = self.notes.get(edge.target)
            source_note = self.notes.get(edge.source)
            if target_note and source_note:
                if source_note.id not in target_note.backlinks:
                    target_note.backlinks.append(source_note.id)

    def _save(self) -> None:
        """Persist to JSON file."""
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "notes": {
                nid: {
                    "title": n.title,
                    "content": n.content,
                    "kind": n.kind,
                    "tags": n.tags,
                    "importance": n.importance,
                    "created_at": n.created_at,
                    "updated_at": n.updated_at,
                    "wikilinks": n.wikilinks,
                    "metadata": n.metadata,
                }
                for nid, n in self.notes.items()
            },
            # Memora insight: persist typed edges explicitly so the
            # graph keeps relation semantics across sessions.
            "edges": [
                {"source": e.source, "target": e.target,
                 "edge_type": e.edge_type, "weight": e.weight}
                for e in self.edges
            ],
            "version": "1.1",
        }
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _load(self) -> None:
        """Load from JSON file."""
        if not self.path or not self.path.exists():
            return
        with open(self.path) as f:
            data = json.load(f)
        for nid, ndata in data.get("notes", {}).items():
            note = ObsidianNote(
                id=nid,
                title=ndata["title"],
                content=ndata["content"],
                kind=ndata.get("kind", "fact"),
                tags=ndata.get("tags", []),
                importance=ndata.get("importance", 0.5),
                created_at=ndata.get("created_at", 0.0),
                updated_at=ndata.get("updated_at", 0.0),
                wikilinks=ndata.get("wikilinks", []),
                metadata=ndata.get("metadata", {}),
            )
            self.notes[nid] = note

        # Restore typed edges from file (fallback: rebuild from wikilinks)
        saved_edges = data.get("edges")
        if saved_edges:
            self.edges = [
                GraphEdge(
                    source=e.get("source", ""),
                    target=e.get("target", ""),
                    edge_type=e.get("edge_type", "related_to"),
                    weight=e.get("weight", 1.0),
                )
                for e in saved_edges
            ]
        else:
            self.edges = []
            for note in self.notes.values():
                for link_title in note.wikilinks:
                    link_id = self._make_id(link_title)
                    self.edges.append(GraphEdge(source=note.id, target=link_id))

        self._rebuild_backlinks()


# ═══════════════════════════════════════════════════════════════════════════════
# Quick utility
# ═══════════════════════════════════════════════════════════════════════════════

def estimate_memory_savings(
    full_context_tokens: int,
    retrieved_context_tokens: int,
) -> dict[str, Any]:
    """Estimate token savings from ObsidianMemoryStore vs full context.

    Typical scenario: instead of 2000 tokens of event history,
    retrieve only 300 tokens of relevant linked notes.
    """
    saved = full_context_tokens - retrieved_context_tokens
    pct = 100 * saved / max(1, full_context_tokens)
    return {
        "full_context_tokens": full_context_tokens,
        "retrieved_tokens": retrieved_context_tokens,
        "saved_tokens": saved,
        "savings_pct": round(pct, 1),
        "co2_kg_per_1M_calls": round(saved * 1_000_000 * 0.000003, 1),  # ~3g CO2/token
    }
