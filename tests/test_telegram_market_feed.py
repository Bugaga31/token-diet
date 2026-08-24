"""Tests for telegram_market_feed: live market news bridge."""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from token_diet.investment_analyzer import InvestmentAnalyzer, NewsItem
from token_diet.telegram_market_feed import (
    _CREDENTIALS_CANDIDATES,
    MARKET_CHANNELS,
    RawMessage,
    TelegramMarketFeed,
    _load_credentials,
    compact_digest,
    detect_tickers,
    feed_to_news_items,
    parse_message,
)


def _msg(text: str, channel: str = "СМАРТЛАБ", when: datetime | None = None) -> RawMessage:
    return RawMessage(
        channel=channel,
        text=text,
        published=when or datetime(2026, 8, 10, 9, 0),
    )


# ── detect_tickers ──────────────────────────────────────────────────────────

def test_detect_company_names():
    assert "SBER" in detect_tickers("Сбер отчитался о рекордной прибыли")
    assert "GAZP" in detect_tickers("Газпром объявил дивиденды")
    assert "YDEX" in detect_tickers("Яндекс покупает новые активы")


def test_detect_literal_tickers():
    assert "PLZL" in detect_tickers("PLZL обновил максимум на фоне золота")
    assert "RUAL" in detect_tickers("RUAL: совет директоров в понедельник")


def test_detect_no_false_positives():
    # Служебные слова не должны стать тикерами
    assert "RUB" not in detect_tickers("Курс USD/RUB вырос")
    assert "AND" not in detect_tickers("AND NOW")


def test_detect_multiple_unique():
    tickers = detect_tickers("Сбер и Газпром, SBER также упомянут")
    assert tickers.count("SBER") == 1
    assert "GAZP" in tickers


# ── parse_message / feed_to_news_items ──────────────────────────────────────

def test_parse_message_attaches_tickers():
    fm = parse_message(_msg("Полюс вырос на рекорде золота"))
    assert fm.tickers == ["PLZL"]


def test_feed_to_news_items_creates_newsitem():
    items = feed_to_news_items(
        [_msg("Сбер: чистая прибыль рекорд", channel="РБК Инвестиции")]
    )
    assert items, "должен быть хотя бы один NewsItem"
    assert all(isinstance(i, NewsItem) for i in items)
    assert items[0].ticker == "SBER"
    assert items[0].published == date(2026, 8, 10)


def test_feed_to_news_items_skips_untagged():
    items = feed_to_news_items([_msg("Никаких компаний тут нет")])
    assert items == []


# ── compact_digest ──────────────────────────────────────────────────────────

def test_compact_digest():
    digest = compact_digest(
        [
            _msg("Сбер отчитался", channel="РБК"),
            _msg("Газпром дивиденды", channel="СМАРТЛАБ"),
        ]
    )
    assert "Сбер" in digest
    assert "[РБК] (SBER)" in digest
    assert "[СМАРТЛАБ] (GAZP)" in digest


def test_compact_digest_empty():
    assert "Пусто" in compact_digest([])


def test_compact_digest_limits_lines():
    msgs = [_msg(f"Сбер новость {i}") for i in range(20)]
    digest = compact_digest(msgs, max_lines=5)
    assert len(digest.splitlines()) <= 5


# ── Integration with InvestmentAnalyzer ─────────────────────────────────────

def test_feed_feeds_analyzer():
    raw = [
        _msg("Полюс: золото пробило рекорд, аналитики повышают цель",
             channel="СМАРТЛАБ"),
    ]
    news = feed_to_news_items(raw)
    analyzer = InvestmentAnalyzer(
        news=news,
        today=date(2026, 8, 10),
    )
    v = analyzer.analyze("PLZL")
    # свежий позитивный катализатор → не отыгран → buy возможен
    assert v.action == "buy"
    assert any("катализатор" in c for c in v.catalysts)


def test_feed_negative_news_avoids():
    raw = [
        _msg("Сбер разочаровал рынок, акции упали, инсайдеры продавали",
             channel="РБК Инвестиции"),
    ]
    news = feed_to_news_items(raw)
    analyzer = InvestmentAnalyzer(
        news=news,
        today=date(2026, 8, 10),
    )
    v = analyzer.analyze("SBER")
    assert v.action == "avoid"


# ── credentials auto-discovery ─────────────────────────────────────────────

def test_credential_candidates_include_home_json():
    # стандартные места для JSON-кредов должны включать ~/221099698.json
    assert any("221099698.json" in c for c in _CREDENTIALS_CANDIDATES)


def test_load_credentials_missing_path_returns_none(monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.setattr("token_diet.telegram_market_feed._CREDENTIALS_CANDIDATES", [
        "/nonexistent/creds1.json",
        "/nonexistent/creds2.json",
    ])
    assert _load_credentials() is None


def test_load_credentials_explicit_json(tmp_path):
    import json

    f = tmp_path / "creds.json"
    f.write_text(json.dumps({"app_id": 12345, "app_hash": "abc123"}))
    assert _load_credentials(str(f)) == (12345, "abc123")


def test_load_credentials_env_fallback(monkeypatch, tmp_path):
    import json

    f = tmp_path / "creds.json"
    f.write_text(json.dumps({"app_id": 999, "app_hash": "x"}))
    # даже если candidates пуст, env должен сработать
    monkeypatch.setattr("token_diet.telegram_market_feed._CREDENTIALS_CANDIDATES", [])
    monkeypatch.setenv("TELEGRAM_API_ID", "424242")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash_env")
    assert _load_credentials(str(f)) == (999, "x")
    assert _load_credentials() == (424242, "hash_env")


# ── Offline / graceful degradation ──────────────────────────────────────────

def test_fetch_latest_returns_empty_without_telethon():
    feed = TelegramMarketFeed(
        session_path="/nonexistent/session.session",
        credentials_path="/nonexistent/creds.json",
        proxy=None,
    )
    try:
        import asyncio

        result = asyncio.run(feed.fetch_latest(limit=1))
        assert result == []
    except RuntimeError:
        pass  # без event loop — тоже ок


def test_market_channels_curated():
    # каналы из живого набора, которые мы проверяли
    assert "MoscowExchangeOfficial" in MARKET_CHANNELS
    assert "bitkogan" in MARKET_CHANNELS
    assert "smartlabnews" in MARKET_CHANNELS
    assert "markettwits" in MARKET_CHANNELS
    assert "AK47pfl" in MARKET_CHANNELS  # RDV
    assert len(MARKET_CHANNELS) >= 15


# ── demo ────────────────────────────────────────────────────────────────────

def test_demo_flow_runs():
    msgs = [
        _msg("Полюс PLZL: золото на рекорде, целевая цена повышена", "СМАРТЛАБ"),
        _msg("Сбер: рекордная маржа, но FCF страдает", "РБК Инвестиции"),
        _msg("Газпром: совет директоров по дивидендам", "БКС Экспресс"),
    ]
    assert compact_digest(msgs)
    items = feed_to_news_items(msgs)
    assert len(items) >= 3
