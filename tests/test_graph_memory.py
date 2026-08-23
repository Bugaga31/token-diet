"""Тесты graph_memory: файловый бэкенд, 0 внешних зависимостей."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from token_diet.graph_memory import FileGraphStore, GraphMemory, extract_entities


# ── извлечение сущностей ────────────────────────────────────────────────
def test_extract_tickers_and_assets():
    ents = dict(extract_entities("PLZL вырос, SBER тоже, золото дорожает, нефть дешевеет"))
    assert "PLZL" in ents and "SBER" in ents
    assert "Золото" in ents and "Нефть" in ents


def test_extract_proper_names():
    ents = dict(extract_entities("Полюс отчитался. Сбер объявил дивиденды."))
    assert "Полюс" in ents and "Сбер" in ents


def test_extract_numbers():
    from token_diet.graph_memory import extract_numbers
    nums = extract_numbers("Цена 1261.6 руб, рост 3%, золото $4400")
    joined = " ".join(nums)
    assert "1261.6" in joined and "3%" in joined and "$4400" in joined


# ── эпизоды и инкрементальная индексация ────────────────────────────────
def test_add_episode_builds_graph(tmp_path):
    g = FileGraphStore(tmp_path / "g.json")
    g.add_episode("Эп1", "Полюс вырос на 3%. Золото $4400. Сбер упал на 1%.")
    st = g.stats()
    assert st["episodes"] == 1
    assert st["entities"] >= 3          # Полюс, Золото, Сбер
    assert st["active_facts"] >= 2      # рост/падение
    assert st["edges"] >= 1             # co-occurrence рёбра


# ── би-темпоральность ───────────────────────────────────────────────────
def test_bi_temporal_invalidation(tmp_path):
    g = FileGraphStore(tmp_path / "g.json")
    t1 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 8, tzinfo=timezone.utc)
    g.add_episode("Цена авг1", "Золото стоит 4000 долларов.", ref_time=t1)
    g.add_episode("Цена авг8", "Золото стоит 4400 долларов.", ref_time=t2)

    # на 5 августа было верно старое значение
    facts_old = g.query_as_of("Золото", datetime(2026, 8, 5, tzinfo=timezone.utc).isoformat())
    # сейчас верно новое
    facts_new = g.query_as_of("Золото", datetime.now(timezone.utc).isoformat())

    values_old = {f["obj"] for f in facts_old}
    values_new = {f["obj"] for f in facts_new}
    assert "4000" in values_old
    assert "4400" in values_new
    # старый факт инвалидирован, но не удалён
    closed = [f for f in g.facts if f.subject == "Золото" and f.valid_until is not None]
    assert len(closed) >= 1


# ── гибридный поиск ─────────────────────────────────────────────────────
def test_search_hybrid(tmp_path):
    g = FileGraphStore(tmp_path / "g.json")
    g.add_episode("Аналитика", "Полюс вырос на 3%. Золото на максимумах $4400.")
    g.add_episode("Другое", "Погода сегодня солнечная, температура 25 градусов.")

    hits = g.search("Полюс золото цена")
    assert hits, "запрос по сущностям должен дать результат"
    top = hits[0]
    assert "Полюс" in top["entities"] or "Золото" in top["entities"]
    assert top["facts"], "у эпизода должны быть извлечённые факты"


def test_neighbors(tmp_path):
    g = FileGraphStore(tmp_path / "g.json")
    g.add_episode("Эп", "Полюс связан с золотом. Сбер связан с золотом.")
    nb = g.neighbors("Полюс")
    assert "Золото" in nb or "Сбер" in nb


# ── экспорт в Obsidian ──────────────────────────────────────────────────
def test_to_obsidian_wiki_links(tmp_path):
    g = FileGraphStore(tmp_path / "g.json")
    g.add_episode("Эп", "Полюс вырос на 3%. Золото $4400.")
    vault = tmp_path / "vault"
    written = g.to_obsidian(vault)
    assert len(written) >= 2
    for f in written:
        content = (vault / f).read_text(encoding="utf-8")
        assert content.startswith("# ")
    # wiki-ссылки на связи
    any_links = any("[[Полюс]]" in (vault / f).read_text(encoding="utf-8")
                    for f in written)
    assert any_links, "должны быть [[wiki-links]] между связанными сущностями"


# ── фасад ───────────────────────────────────────────────────────────────
def test_facade_file_backend(tmp_path):
    gm = GraphMemory(backend="file", path=str(tmp_path / "gm.json"))
    gm.add_episode("Память", "Отец дал задание установить Graphiti. Полюс держим до 1400.")
    hits = gm.search("Полюс Graphiti")
    assert hits
    assert gm.stats()["backend"] == "file"


def test_duplicate_episodes_do_not_crash(tmp_path):
    g = FileGraphStore(tmp_path / "g.json")
    for _ in range(3):
        g.add_episode("Повтор", "Полюс растёт на фоне золота.")
    assert g.stats()["episodes"] == 3
