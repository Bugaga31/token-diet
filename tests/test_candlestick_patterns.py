"""Tests for candlestick_patterns — TA-Lib style patterns."""
from token_diet.candlestick_patterns import (
    detect_patterns,
    latest_patterns,
    patterns_block,
    single_bar_patterns,
)


def test_doji():
    # body 0.2 of range 4 = 5% ≤ 10% → doji
    pats = single_bar_patterns(o=100, h=102, l=98, c=100.2)
    assert any(p.name == "Doji" for p in pats)


def test_bullish_engulfing():
    # prev: red bar 110→105; cur: green 103→111 covers prev body
    opens = [110, 103]
    highs = [112, 112]
    lows = [104, 102]
    closes = [105, 111]
    pats = detect_patterns(opens, highs, lows, closes)
    assert any(p.name == "Bullish Engulfing" and p.direction == "bullish" for p in pats)


def test_bearish_engulfing():
    opens = [105, 112]
    highs = [112, 114]
    lows = [104, 106]
    closes = [110, 105]
    pats = detect_patterns(opens, highs, lows, closes)
    assert any(p.name == "Bearish Engulfing" for p in pats)


def test_morning_star():
    # big red, small middle, big green closing above midpoint of bar1
    opens = [120, 115, 112]
    highs = [121, 117, 119]
    lows = [118, 113, 110]
    closes = [114, 114.5, 118.5]  # mid of bar1 = (120+114)/2 = 117; close 118.5 > 117
    pats = detect_patterns(opens, highs, lows, closes)
    assert any(p.name == "Morning Star" for p in pats)


def test_three_white_soldiers():
    opens = [100, 102, 105]
    highs = [102, 105, 108]
    lows = [99, 101, 104]
    closes = [101.5, 104.5, 107.5]
    pats = detect_patterns(opens, highs, lows, closes)
    assert any(p.name == "Three White Soldiers" for p in pats)


def test_latest_only_recent():
    opens = [110, 103, 120, 115, 100]
    highs = [112, 112, 121, 117, 102]
    lows = [104, 102, 118, 113, 98]
    closes = [105, 111, 114, 114.5, 99]
    all_pats = detect_patterns(opens, highs, lows, closes)
    recent = latest_patterns(opens, highs, lows, closes, lookback=2)
    assert recent
    assert max(p.bar for p in recent) >= len(opens) - 2


def test_patterns_block():
    opens = [110, 103]
    highs = [112, 112]
    lows = [104, 102]
    closes = [105, 111]
    block = patterns_block(opens, highs, lows, closes)
    assert block.startswith("[Candles")
    assert "Engulfing" in block
