"""Tests for ponytail — tab/session manager."""
import tempfile
from pathlib import Path

from token_diet.ponytail import TabManager, estimate_tabs_savings


def test_open_and_dedupe():
    m = TabManager()
    t1 = m.open("https://example.com", "Example")
    t2 = m.open("https://example.com", "Example again")  # same URL
    assert t1.id == t2.id  # dedupe: same tab returned
    assert m.stats()["tabs"] == 1


def test_close():
    m = TabManager()
    a = m.open("https://a.com", "A")
    b = m.open("https://b.com", "B")
    assert m.close(a.id) is True
    assert m.close(999) is False
    assert m.stats()["tabs"] == 1


def test_activate_and_group():
    m = TabManager()
    a = m.open("https://a.com", "A")
    b = m.open("https://b.com", "B")
    m.activate(b.id)
    assert m.stats()["active"] == 1
    m.group([a.id, b.id], "work")
    assert all(t.group == "work" for t in m.tabs)


def test_compact_tabs_block():
    m = TabManager()
    m.open("https://github.com/Bugaga31/token-diet", "token-diet", active=True)
    m.open("https://tbank.ru/invest", "Т-Банк Инвестиции")
    block = m.compact_tabs()
    assert block.startswith("[Browser tabs]")
    assert "token-diet" in block
    assert "tbank.ru" in block


def test_session_persistence(tmp_path):
    path = Path(tmp_path) / "tabs.json"
    m = TabManager(path)
    m.open("https://a.com", "A")
    m.open("https://b.com", "B")
    m2 = TabManager(path)
    assert m2.stats()["tabs"] == 2


def test_estimate_savings():
    s = estimate_tabs_savings(5)
    assert s["screenshots_tokens"] == 5500
    assert s["savings_pct"] > 90
