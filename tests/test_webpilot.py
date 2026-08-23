"""Tests for webpilot (offline: HTTP mocked via _http_get)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet import webpilot as wp


def test_extract_relevant_keeps_hits():
    text = (
        "Золото достигло исторического максимума в 2026 году. "
        "Котировки выросли на 40 процентов. "
        "Погода сегодня солнечная и тёплая. "
        "Аналитики ждут роста цен на золото и дальше."
    )
    out = wp.extract_relevant(text, "рост золота", max_chars=2000)
    assert "Золото достигло" in out
    assert "Аналитики ждут роста" in out  # «роста»/«золото» совпали по стему
    assert "солнечная" not in out  # нерелевантное отброшено


def test_extract_relevant_empty():
    assert wp.extract_relevant("", "вопрос") == ""
    assert wp.extract_relevant("текст без совпадений длинный", "x" * 30) != ""


def test_search_jina(monkeypatch):
    fake = (
        "Search results for 'gold 2026':\n"
        "1. https://example.com/gold - Gold prices ATH\n"
        "2. https://example.org/market - Market news\n"
    )
    monkeypatch.setattr(wp, "_http_get", lambda url, timeout=12.0: fake)
    urls = wp.search("gold 2026", limit=2)
    assert urls == ["https://example.com/gold", "https://example.org/market"]


def test_search_duckduckgo_fallback(monkeypatch):
    fake = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg='
        'https%3A%2F%2Fexample.com%2Fnews&amp;rut=x">News</a>'
    )
    monkeypatch.setattr(wp, "_http_get", lambda url, timeout=12.0: fake)
    urls = wp.search("anything", limit=1)
    assert "https://example.com/news" in urls


def test_read_page_jina(monkeypatch):
    monkeypatch.setattr(
        wp, "_http_get",
        lambda url, timeout=12.0: "clean markdown body " * 50,
    )
    assert "clean markdown" in wp.read_page("https://example.com")


def test_read_page_fallback_scraper(monkeypatch):
    calls = []

    def fake_get(url, timeout=12.0):
        calls.append(url)
        return "x"  # слишком короткий для Jina

    monkeypatch.setattr(wp, "_http_get", fake_get)
    res = wp.scrape_url = None  # noqa
    # заменяем clean_scraper.scrape_url внутри read_page
    import token_diet.clean_scraper as cs

    monkeypatch.setattr(
        cs, "scrape_url",
        lambda url, max_chars=50000, timeout=12.0: type(
            "R", (), {"status": "ok", "text": "fallback text " * 20}
        )(),
    )
    out = wp.read_page("https://example.com")
    assert "fallback text" in out


def test_ask_web_basic(monkeypatch):
    page = (
        "<html><body>"
        '<a href="https://other.com/gold">золото рост</a>'
        "<p>Золото растёт из-за спроса центральных банков. "
        "Это важный тренд 2026 года. Обычный мусорный текст тут.</p>"
        "</body></html>"
    )

    def fake_get(url, timeout=12.0):
        return page

    monkeypatch.setattr(wp, "_http_get", fake_get)
    monkeypatch.setattr(wp, "search", lambda q, limit=3, timeout=12.0: ["https://one.com"])
    r = wp.ask_web("почему растёт золото", max_steps=2)
    assert r.pages_read >= 1
    assert "Золото растёт" in r.evidence
    assert "мусорный" not in r.evidence
    assert r.sources
    assert "Вопрос:" in r.prompt_block()
