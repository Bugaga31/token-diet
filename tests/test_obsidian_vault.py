"""Tests for obsidian_vault: persistent markdown memory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from token_diet.obsidian_vault import ObsidianVault


def test_write_creates_md_file(tmp_path):
    v = ObsidianVault(tmp_path)
    f = v.write("Цели", "Ради людей и планеты.", tags=["goals"])
    assert f.exists()
    content = f.read_text(encoding="utf-8")
    assert "title: Цели" in content
    assert "tags: [goals]" in content
    assert "Ради людей" in content


def test_read_returns_body(tmp_path):
    v = ObsidianVault(tmp_path)
    v.write("Урок", "Полюс: календарь → новости → форумы.")
    body = v.read("Урок")
    assert body is not None
    assert "Полюс" in body


def test_search_finds_relevant(tmp_path):
    v = ObsidianVault(tmp_path)
    v.write("Рынок", "Сбер и Газпром на рынке РФ. Дивиденды.")
    v.write("Игры", "Майнкрафт фермы блоков.")
    res = v.search("дивиденды рынок")
    titles = [t for t, _ in res]
    assert "Рынок" in titles


def test_search_empty_query(tmp_path):
    v = ObsidianVault(tmp_path)
    assert v.search("") == []


def test_context_for_prompt(tmp_path):
    v = ObsidianVault(tmp_path)
    v.write("Правила", "Никогда не врать. Проверять 10 раз.")
    ctx = v.context_for_prompt("как не врать")
    assert "Правила" in ctx
    assert "Никогда не врать" in ctx


def test_stats(tmp_path):
    v = ObsidianVault(tmp_path)
    v.write("A", "one")
    v.write("B", "two")
    assert v.stats()["notes"] == 2
