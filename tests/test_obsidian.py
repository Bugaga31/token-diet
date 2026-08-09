"""Tests for obsidian_memory.py — ObsidianMemoryStore."""

import pytest
from token_diet.obsidian_memory import (
    ObsidianMemoryStore,
    ObsidianNote,
    RetrievalResult,
    estimate_memory_savings,
)


class TestObsidianMemoryStore:
    def setup_method(self):
        self.store = ObsidianMemoryStore()

    def test_remember_creates_note(self):
        note = self.store.remember(
            "Refund Policy",
            "Refunds within 14 days. 15% restocking fee. [[Compliance Rules]] apply.",
            kind="fact",
            tags=["policy", "refunds"],
            importance=0.8,
        )
        assert note.title == "Refund Policy"
        assert note.kind == "fact"
        assert "policy" in note.tags
        assert note.importance == 0.8
        assert "Compliance Rules" in note.wikilinks
        assert len(self.store.notes) == 1

    def test_remember_update_existing(self):
        self.store.remember("Test", "Old content", tags=["old"])
        note = self.store.remember("Test", "New content", tags=["new"])
        assert note.content == "New content"
        assert "new" in note.tags
        assert len(self.store.notes) == 1  # Not duplicated

    def test_get_existing_note(self):
        self.store.remember("API Keys", "Store in .env")
        note = self.store.get("API Keys")
        assert note is not None
        assert note.title == "API Keys"

    def test_get_nonexistent(self):
        assert self.store.get("Nonexistent") is None

    def test_link_creates_wikilink(self):
        self.store.remember("Refund", "14 days return policy")
        self.store.remember("Compliance", "T+1 reporting")
        assert self.store.link("Refund", "Compliance")
        note = self.store.get("Refund")
        assert "Compliance" in note.wikilinks

    def test_link_nonexistent_returns_false(self):
        assert not self.store.link("A", "B")

    def test_wikilinks_parsed_from_content(self):
        note = self.store.remember(
            "Architecture",
            "We use [[PostgreSQL]] for data and [[Redis]] for [[Caching Layer]]."
        )
        assert "PostgreSQL" in note.wikilinks
        assert "Redis" in note.wikilinks
        assert "Caching Layer" in note.wikilinks

    def test_retrieve_keyword_match(self):
        self.store.remember("Refund Policy", "14 days, 15% restocking fee. RMA required.",
                           tags=["policy"], importance=0.9)
        self.store.remember("Shipping Info", "Free shipping over $50. Express available.",
                           tags=["shipping"], importance=0.5)
        self.store.remember("Compliance", "T+1 reporting for all trades.",
                           tags=["compliance"], importance=0.8)

        results = self.store.retrieve("refund policy")
        assert len(results) > 0
        assert results[0].note.title == "Refund Policy"
        assert results[0].score > 0.5

    def test_retrieve_tag_match(self):
        self.store.remember("Trading Hours", "MOEX 10:00-19:00 MSK",
                           tags=["trading"], importance=0.6)
        results = self.store.retrieve("trading")
        assert len(results) > 0

    def test_retrieve_empty_store(self):
        results = self.store.retrieve("anything")
        assert len(results) == 0

    def test_graph_traversal(self):
        self.store.remember("A", "Links to [[B]] and [[C]]")
        self.store.remember("B", "Links to [[D]]")
        self.store.remember("C", "Related to B")
        self.store.remember("D", "End of chain")

        results = self.store.graph("A", depth=2)
        titles = {r.note.title for r in results}
        assert "B" in titles
        assert "C" in titles
        assert "D" in titles  # depth 2 from A
        assert "A" not in titles  # Start note excluded

    def test_graph_depth_1(self):
        self.store.remember("Root", "Links to [[L1]] and [[L1b]]")
        self.store.remember("L1", "Links to [[L2]]")
        self.store.remember("L1b", "No links")
        self.store.remember("L2", "Deep")

        results = self.store.graph("Root", depth=1)
        titles = {r.note.title for r in results}
        assert "L1" in titles
        assert "L1b" in titles
        assert "L2" not in titles  # depth 1 only

    def test_backlinks(self):
        self.store.remember("Refund", "14 days, see [[Compliance]]")
        self.store.remember("Compliance", "T+1 reporting")
        self.store.remember("Audit", "Must follow [[Compliance]] rules")

        backlinks = self.store.backlinks("Compliance")
        titles = {n.title for n in backlinks}
        assert "Refund" in titles
        assert "Audit" in titles

    def test_search_hybrid_combines_direct_and_graph(self):
        self.store.remember("Start", "Links to [[Budget]] and [[Timeline]]")
        self.store.remember("Budget", "Q3 budget is $500K. [[Compliance]] must review.")
        self.store.remember("Timeline", "Deadline: Dec 15th")
        self.store.remember("Compliance", "Audit required for all expenses >$10K")

        results = self.store.search_hybrid("budget", start_title="Start", depth=2)
        assert len(results) > 0
        # Should include Budget (direct match) and Compliance (graph traversal)

    def test_context_for_prompt(self):
        self.store.remember(
            "Refund Policy",
            "Refunds within 14 calendar days. 15% restocking fee for opened items. "
            "Shipping non-refundable. RMA number required. See [[Compliance Rules]].",
            kind="fact", tags=["policy", "refunds"], importance=0.9
        )
        self.store.remember(
            "Compliance Rules",
            "T+1 reporting for all trades. 0.25% penalty per day late. "
            "Blocked orders audited within 48 hours.",
            kind="constraint", tags=["compliance"], importance=0.95
        )
        self.store.remember(
            "Shipping Policy",
            "Free shipping over $50. Express $15 flat rate.",
            kind="fact", tags=["shipping"], importance=0.4
        )

        context = self.store.context_for_prompt("refund policy compliance")
        assert "Refund Policy" in context
        assert "[Relevant context from memory]" in context
        assert "[End context]" in context

    def test_context_for_prompt_empty_store(self):
        context = self.store.context_for_prompt("anything")
        assert context == ""

    def test_stats_empty_store(self):
        stats = self.store.stats()
        assert stats["total_notes"] == 0
        assert stats["total_edges"] == 0

    def test_stats_with_notes(self):
        self.store.remember("A", "Content A", tags=["tag1"])
        self.store.remember("B", "Content B with [[A]]", tags=["tag1", "tag2"])
        stats = self.store.stats()
        assert stats["total_notes"] == 2
        assert stats["total_links"] == 1
        assert stats["by_kind"]["fact"] == 2

    def test_persistence(self, tmp_path):
        path = tmp_path / "memory.json"
        store1 = ObsidianMemoryStore(path=path)
        store1.remember("Persistent", "This note survives")

        store2 = ObsidianMemoryStore(path=path)
        note = store2.get("Persistent")
        assert note is not None
        assert note.content == "This note survives"

    def test_different_kinds(self):
        self.store.remember("Fact", "data", kind="fact")
        self.store.remember("Decision", "chose X", kind="decision")
        self.store.remember("Constraint", "must do Y", kind="constraint")
        stats = self.store.stats()
        assert stats["by_kind"]["fact"] == 1
        assert stats["by_kind"]["decision"] == 1
        assert stats["by_kind"]["constraint"] == 1


class TestEstimateMemorySavings:
    def test_typical_savings(self):
        result = estimate_memory_savings(2000, 300)
        assert result["savings_pct"] == 85.0
        assert result["saved_tokens"] == 1700

    def test_no_savings(self):
        result = estimate_memory_savings(100, 100)
        assert result["savings_pct"] == 0.0

    def test_full_replacement(self):
        result = estimate_memory_savings(1000, 0)
        assert result["savings_pct"] == 100.0


class TestObsidianNote:
    def test_snippet(self):
        note = ObsidianNote(
            id="abc", title="Test",
            content="This is a very long note that should be truncated when we call snippet."
        )
        snip = note.snippet(20)
        assert len(snip) <= 23  # 20 + "..." if truncated
