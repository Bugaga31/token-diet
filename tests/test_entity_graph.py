"""Tests for entity_graph — Mem0-style entity/relation auto-mining."""
from token_diet.entity_graph import EntityGraph, extract_entities


def test_extract_proper_names():
    found = extract_entities("Товарищ Верховный Главнокомандующий купил акции Полюса.")
    assert any("Полюс" in e for e in found)


def test_extract_tickers():
    found = extract_entities("PLZL вырос, SBER и GAZP тоже подросли")
    assert "PLZL" in found
    assert "SBER" in found


def test_extract_urls():
    found = extract_entities("docs at https://example.com/api/guide here")
    assert any(u.startswith("https://") for u in found)


def test_graph_edges_on_cooccurrence():
    g = EntityGraph()
    g.add("PLZL зависит от золота. Золото растёт.")
    rels = g.related("PLZL")
    assert rels
    other = rels[0].b if rels[0].a == "PLZL" else rels[0].a
    assert other.lower() == "золото"


def test_weight_grows_with_repetition():
    g = EntityGraph()
    g.add("Сбер и Яндекс растут.")
    g.add("Сбер и Яндекс объявили о слиянии.")
    rel = g.related("Сбер")
    assert rel and rel[0].weight >= 2.0


def test_prompt_context():
    g = EntityGraph()
    g.add("PLZL связан с золотом. Полюс добывает золото.")
    block = g.prompt_context("PLZL")
    assert block.startswith("[Knowledge graph")
    assert "золото" in block.lower()


def test_stats():
    g = EntityGraph()
    g.add("SBER отчитался. SBER и T-Технологии растут.")
    st = g.stats()
    assert st["entities"] >= 2
    assert st["edges"] >= 1


def test_feed_memory():
    from token_diet.obsidian_memory import ObsidianMemoryStore

    g = EntityGraph()
    store = ObsidianMemoryStore()
    g.feed_memory(store, "Полюс и золото", "PLZL растёт вместе с золотом.")
    assert store.get("Полюс и золото") is not None
    assert any("PLZL" in n or "золот" in n for n in store.get("Полюс и золото").wikilinks)
