"""Тесты rick_panel: пульт Рика — поиск модулей по ситуации."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from token_diet.rick_panel import ARSENAL, categories, find, panel_block


def test_arsenal_not_empty():
    assert len(ARSENAL) > 5, "Арсенал должен иметь минимум 6 категорий"


def test_categories_include_key():
    cats = categories()
    assert any("деньг" in c.lower() for c in cats) or any(
        "инвест" in c.lower() for c in cats
    ), f"Нет категории про деньги: {cats}"


def test_find_returns_results():
    results = find("деньги")
    assert isinstance(results, list)
    assert len(results) > 0


def test_find_case_insensitive():
    a = find("ПАМЯТЬ")
    b = find("память")
    assert len(a) == len(b)


def test_find_unknown_returns_empty():
    results = find("zxqj")
    assert results == []


def test_panel_block_has_arsenal_header():
    block = panel_block()
    assert "АРСЕНАЛ" in block


def test_panel_block_query_found():
    block = panel_block("память")
    assert "память" in block.lower()


def test_panel_block_unknown_helpful():
    block = panel_block("zxqj")
    assert "нет" in block.lower() or "Скажи точнее" in block


def test_all_entries_have_call():
    """Каждая запись арсенала должна содержать команду вызова."""
    for cat, items in ARSENAL.items():
        for name, _desc, call in items:
            assert call.strip(), f"{cat}/{name}: пустая команда вызова"
