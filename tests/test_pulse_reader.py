"""Tests for pulse_reader (offline: HTTP mocked via _fetch)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import token_diet.pulse_reader as pr


SAMPLE_POST = {
    "id": "12345",
    "text": "{$PLZL} Золото растёт, Полюс догонит. Держу!",
    "nickname": "TestUser",
    "inserted": "2026-08-12T14:21:00Z",
    "likesCount": 42,
    "commentsCount": 7,
    "instruments": [{"ticker": "PLZL"}, {"ticker": "GOLD"}],
    "hashtags": ["золото", "полюс"],
}


def test_parse_post():
    p = pr.parse_post(SAMPLE_POST)
    assert p is not None
    assert p.id == "12345"
    assert "Золото" in p.text
    assert p.nickname == "TestUser"
    assert p.likes == 42
    assert p.comments == 7
    assert "PLZL" in p.instruments


def test_parse_post_text_in_content_dict():
    raw = dict(SAMPLE_POST)
    raw["text"] = None
    raw["content"] = {"text": "контент в dict"}
    p = pr.parse_post(raw)
    assert p is not None
    assert p.text == "контент в dict"


def test_parse_post_invalid():
    assert pr.parse_post(None) is None
    assert pr.parse_post({"no_id": 1}) is None


def test_get_ticker_posts_mocked(monkeypatch):
    payload = {
        "status": "Ok",
        "payload": {"items": [SAMPLE_POST, {"id": "2", "text": "второй", "nickname": "A", "inserted": "2026-08-12T14:22:00Z"}]},
    }
    monkeypatch.setattr(pr, "_fetch", lambda url, timeout=15: payload)
    posts = pr.get_ticker_posts("PLZL")
    assert len(posts) == 2
    assert posts[0].likes == 42


def test_get_ticker_posts_http_error(monkeypatch):
    monkeypatch.setattr(pr, "_fetch", lambda url, timeout=15: None)
    assert pr.get_ticker_posts("PLZL") == []


def test_digest(monkeypatch):
    payload = {"status": "Ok", "payload": {"items": [SAMPLE_POST]}}
    monkeypatch.setattr(pr, "_fetch", lambda url, timeout=15: payload)
    d = pr.digest("PLZL")
    assert "TestUser" in d
    assert "42" in d  # likes


def test_search_keywords():
    posts = [
        pr.parse_post(SAMPLE_POST),
        pr.parse_post({"id": "9", "text": "продал всё", "nickname": "B", "inserted": "2026-08-12T14:23:00Z"}),
    ]
    hits = pr.search_keywords([p for p in posts if p], "золото")
    assert len(hits) == 1
    assert hits[0].nickname == "TestUser"


def test_pulse_sentiment_bearish(monkeypatch):
    bearish_posts = [
        {"id": str(i), "text": "всё падает, сливают, дно, паника", "nickname": "X", "inserted": "2026-08-12T14:20:00Z"}
        for i in range(5)
    ]
    payload = {"status": "Ok", "payload": {"items": bearish_posts}}
    monkeypatch.setattr(pr, "_fetch", lambda url, timeout=15: payload)
    s = pr.pulse_sentiment("PLZL")
    assert s["signal"] == "bearish"
    assert s["sample_size"] == 5
    assert s["bearish_pct"] >= 50
    assert "contrarian" in s  # толпа в панике → контрарный сигнал


def test_pulse_sentiment_bullish(monkeypatch):
    bullish_posts = [
        {"id": str(i), "text": "рост продолжится, покупать, лонг, ралли, закупился", "nickname": "Y", "inserted": "2026-08-12T14:20:00Z"}
        for i in range(5)
    ]
    payload = {"status": "Ok", "payload": {"items": bullish_posts}}
    monkeypatch.setattr(pr, "_fetch", lambda url, timeout=15: payload)
    s = pr.pulse_sentiment("PLZL")
    assert s["signal"] == "bullish"
    assert s["bullish_pct"] >= 50


def test_pulse_sentiment_no_posts(monkeypatch):
    monkeypatch.setattr(pr, "_fetch", lambda url, timeout=15: None)
    s = pr.pulse_sentiment("PLZL")
    assert s["signal"] == "neutral"
    assert s["sample_size"] == 0
