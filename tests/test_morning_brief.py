"""Tests for morning_brief (offline: source modules mocked)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.morning_brief import MorningBrief, brief, brief_block


def test_block_assembles_sections(monkeypatch):
    monkeypatch.setattr(
        "token_diet.morning_brief._sec_snapshot", lambda t, g: "Цена 1276 | P&L -5%"
    )
    monkeypatch.setattr(
        "token_diet.morning_brief._sec_sentiment", lambda t: "Сигнал: neutral"
    )
    monkeypatch.setattr(
        "token_diet.morning_brief._sec_web", lambda t, g: "evidence text"
    )
    monkeypatch.setattr(
        "token_diet.morning_brief._sec_sweet", lambda g: "PLZL 40/100"
    )
    monkeypatch.setattr(
        "token_diet.morning_brief._sec_plan", lambda t: "План готов"
    )
    b = brief("PLZL", gold_price=4420.0)
    block = b.block()
    assert "УТРЕННИЙ БРИФИНГ" in block
    assert "Цена 1276" in block
    assert "neutral" in block
    assert "План готов" in block
    assert "PLZL 40/100" in block


def test_block_graceful_when_empty():
    b = MorningBrief(ticker="PLZL", sections={})
    assert "все источники недоступны" in b.block()


def test_sec_sentiment_fallback(monkeypatch):
    # pulse_sentiment бросает исключение → секция не роняет брифинг
    import token_diet.morning_brief as mb

    def boom(t, limit=12):
        raise RuntimeError("net down")

    monkeypatch.setattr("token_diet.pulse_reader.pulse_sentiment", boom)
    out = mb._sec_sentiment("PLZL")
    assert "Пульс недоступен" in out


def test_sec_plan_has_prompt():
    import token_diet.morning_brief as mb

    out = mb._sec_plan("PLZL")
    assert "ПЛАН" in out or "Триггеры" in out
