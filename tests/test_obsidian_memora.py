"""Tests for Memora-inspired features in ObsidianMemoryStore:

typed edges (supersedes/contradicts/implements), boost, find_duplicates,
merge, digest, and typed-edge persistence across save/load.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.obsidian_memory import (
    EDGE_TYPES,
    ObsidianMemoryStore,
)


class TestTypedEdges:
    def test_edge_types_defined(self):
        assert "supersedes" in EDGE_TYPES
        assert "contradicts" in EDGE_TYPES
        assert "implements" in EDGE_TYPES
        assert "references" in EDGE_TYPES
        assert "extends" in EDGE_TYPES
        assert "related_to" in EDGE_TYPES

    def test_link_with_type(self):
        store = ObsidianMemoryStore()
        store.remember("Старый план", "Делать X по-старому")
        store.remember("Новый план", "Делать X по-новому")
        ok = store.link("Новый план", "Старый план", edge_type="supersedes")
        assert ok
        edge = next(e for e in store.edges
                    if e.edge_type == "supersedes")
        assert edge.edge_type == "supersedes"

    def test_link_upgrades_existing_type(self):
        store = ObsidianMemoryStore()
        store.remember("A", "note A")
        store.remember("B", "note B")
        store.link("A", "B", edge_type="references")
        store.link("A", "B", edge_type="contradicts")
        supers = [e for e in store.edges if e.edge_type == "contradicts"]
        refs = [e for e in store.edges if e.edge_type == "references"]
        assert len(supers) == 1
        assert len(refs) == 0  # upgraded, not duplicated

    def test_invalid_type_falls_back(self):
        store = ObsidianMemoryStore()
        store.remember("A", "note A")
        store.remember("B", "note B")
        store.link("A", "B", edge_type="bogus_type")
        edge = store.edges[0]
        assert edge.edge_type == "related_to"


class TestBoost:
    def test_boost_raises_importance(self):
        store = ObsidianMemoryStore()
        store.remember("Важное", "критичный факт для сделки", importance=0.3)
        assert store.boost("Важное", amount=0.4)
        note = store.get("Важное")
        assert note.importance == 0.7

    def test_boost_capped_at_one(self):
        store = ObsidianMemoryStore()
        store.remember("Топ", "важно", importance=0.9)
        store.boost("Топ", amount=0.5)
        assert store.get("Топ").importance == 1.0

    def test_boost_missing_note(self):
        store = ObsidianMemoryStore()
        assert not store.boost("Нет такой", amount=0.1)


class TestFindDuplicates:
    def test_finds_duplicates(self):
        store = ObsidianMemoryStore()
        store.remember("Один", "правило: всегда проверять факты перед ответом")
        store.remember("Два", "правило: всегда проверять факты перед ответом")
        dupes = store.find_duplicates()
        assert len(dupes) >= 1

    def test_no_duplicates_for_different(self):
        store = ObsidianMemoryStore()
        store.remember("Про кофе", "кофе растёт в горах")
        store.remember("Про чай", "чай растёт в горах тоже")
        dupes = store.find_duplicates()
        assert len(dupes) == 0


class TestMerge:
    def test_merge_append(self):
        store = ObsidianMemoryStore()
        store.remember("Keep", "первая часть правила")
        store.remember("Drop", "вторая часть правила")
        assert store.merge("Keep", "Drop", strategy="append")
        assert store.get("Drop") is None
        merged = store.get("Keep")
        assert "первая часть" in merged.content
        assert "вторая часть" in merged.content

    def test_merge_keeps_union_tags_and_importance(self):
        store = ObsidianMemoryStore()
        store.remember("Keep", "a", tags=["x"], importance=0.2)
        store.remember("Drop", "b", tags=["y"], importance=0.9)
        store.merge("Keep", "Drop")
        merged = store.get("Keep")
        assert "x" in merged.tags and "y" in merged.tags
        assert merged.importance == 0.9


class TestDigest:
    def test_digest_returns_block(self):
        store = ObsidianMemoryStore()
        store.remember("Правило рефандов", "Возврат в течение 14 дней")
        store.remember("Правило доставки", "Доставка бесплатная от 3000")
        store.link("Правило рефандов", "Правило доставки", edge_type="related_to")
        d = store.digest("рефанды")
        assert "[Memory digest:" in d
        assert "Правило рефандов" in d

    def test_digest_empty(self):
        store = ObsidianMemoryStore()
        assert "No memory" in store.digest("чего-то неизвестного")


class TestTypedEdgePersistence:
    def test_edge_type_survives_save_load(self, tmp_path: Path):
        path = tmp_path / "mem.json"
        store = ObsidianMemoryStore(path=path)
        store.remember("Старый", "старая версия решения")
        store.remember("Новый", "новая версия решения")
        store.link("Новый", "Старый", edge_type="supersedes")

        store2 = ObsidianMemoryStore(path=path)
        assert len(store2.edges) >= 1
        assert any(e.edge_type == "supersedes" for e in store2.edges)

    def test_boost_survives_save_load(self, tmp_path: Path):
        path = tmp_path / "mem2.json"
        store = ObsidianMemoryStore(path=path)
        store.remember("Важно", "данные", importance=0.2)
        store.boost("Важно", amount=0.5)

        store2 = ObsidianMemoryStore(path=path)
        assert store2.get("Важно").importance == 0.7
