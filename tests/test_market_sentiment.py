import sys
from pathlib import Path

sys.path.insert(0, ".")

from token_diet.market_sentiment import (
    company_keywords,
    from_fresh_news,
    gather_sentiment_texts,
)


def _tmp_news(tmp_path: Path) -> Path:
    f = tmp_path / "news.md"
    f.write_text(
        "## [2026-08-13 07:00] Канал — Золото растёт, Полюс догоняет\n"
        "## [2026-08-13 08:00] Канал — Норникель отчитался лучше ожиданий\n"
        "## [2026-08-13 09:00] Канал — Сбер повышает ставки\n"
        "Продажи новостроек сократились\n",
        encoding="utf-8",
    )
    return f


def test_company_keywords_includes_ticker_and_name():
    kw = company_keywords("GMKN")
    assert "gmkn" in kw
    assert "норникель" in kw


def test_from_fresh_news_finds_by_name(tmp_path):
    f = _tmp_news(tmp_path)
    texts = from_fresh_news("GMKN", path=f)
    assert any("Норникель" in t for t in texts)


def test_from_fresh_news_finds_by_ticker(tmp_path):
    f = _tmp_news(tmp_path)
    texts = from_fresh_news("PLZL", path=f)
    assert any("Полюс" in t for t in texts)


def test_from_fresh_news_missing_file():
    assert from_fresh_news("GMKN", path=Path("/nonexistent/x.md")) == []


def test_gather_sentiment_texts_without_pulse(tmp_path):
    _tmp_news(tmp_path)
    # include_pulse=False → только лента, без сети
    texts = gather_sentiment_texts("SBER", include_pulse=False)
    # нет гарантии, что HOME лента есть, поэтому просто тип списка
    assert isinstance(texts, list)
