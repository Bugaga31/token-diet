"""Tests for claude_mem — SQLite memory with auto-injection."""
from pathlib import Path

from token_diet.claude_mem import ClaudeMem, _split_observations, estimate_memory_injection_savings


def test_split_observations():
    obs = _split_observations(
        "Пользователь решил хранить память в Obsidian. Проект использует Python 3.12.")
    assert obs
    assert any("Obsidian" in o for o in obs)


def test_observe_and_retrieve(tmp_path):
    m = ClaudeMem(Path(tmp_path) / "mem.db", project="test")
    m.observe("Клиент решил перейти на FastAPI. Проект использует Redis для кэша.")
    hits = m.retrieve("что мы решили про фреймворк", top_k=3)
    assert hits
    assert any("FastAPI" in h.content for h in hits)
    m.close()


def test_dedup_no_duplicate():
    m = ClaudeMem(project="test2")
    first = m.observe("Важно: ставка по депозиту 19%.")
    second = m.observe("Важно: ставка по депозиту 19%.")
    assert len(first) >= 1
    assert second == []  # exact duplicate not stored again
    assert m.stats()["observations"] == len(first)
    m.close()


def test_inject_bumps_hits():
    m = ClaudeMem(project="test3")
    m.observe("Мы решили использовать BM25 для ранжирования.")
    block = m.inject("как ранжируем?", top_k=2)
    assert block.startswith("[Memory")
    assert "BM25" in block
    assert block.endswith("[/Memory]")
    st = m.stats()
    assert st["total_hits"] >= 1
    m.close()


def test_forget_older_than():
    m = ClaudeMem(project="test4")
    m.observe("Память для проверки удаления.")
    m.forget_older_than(0)  # everything older than now → removed
    assert m.stats()["observations"] == 0
    m.close()


def test_savings():
    s = estimate_memory_injection_savings(5000, 250)
    assert s["savings_pct"] == 95.0
