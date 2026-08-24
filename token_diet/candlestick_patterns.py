"""candlestick_patterns — TA-Lib style pattern detection, pure Python.

Reverse-engineered from TA-Lib's candlestick pattern functions:
    Each pattern is a deterministic rule over OHLC bars — body size,
    shadows, and the relation to previous bars. TA-Lib computes all of
    them with a C library; we compute the most useful ones with stdlib.

Why this matters for token-diet:
    A raw OHLC dump tells the model nothing structural. A compact line
    "Bullish Engulfing on PLZL (last bar)" is worth 20 candles — and a
    human can scan it in a second. Same data, ~90% fewer tokens, and
    the pattern is already *interpreted*.

Bar anatomy used by every rule:
    body      = |close - open|
    range     = high - low
    upper_shadow = high - max(open, close)
    lower_shadow = min(open, close) - low
"""

from __future__ import annotations

from dataclasses import dataclass

_EPS = 1e-9


@dataclass
class CandlePattern:
    """One detected pattern on one bar."""
    name: str
    direction: str = "neutral"     # bullish | bearish | neutral
    bar: int = -1
    strength: float = 0.5          # 0..1 rough confidence
    note: str = ""

    def to_line(self) -> str:
        arrow = {"bullish": "▲", "bearish": "▼", "neutral": "•"}.get(self.direction, "•")
        return f"{arrow} {self.name} (bar {self.bar}) {self.note}".strip()


def _anatomy(o: float, h: float, low: float, c: float) -> dict[str, float]:
    body = abs(c - o)
    rng = max(h - low, _EPS)
    return {
        "body": body,
        "range": rng,
        "upper": h - max(o, c),
        "lower": min(o, c) - low,
        "body_ratio": body / rng,
    }


# ── single-bar patterns ───────────────────────────────────────────────────────

def single_bar_patterns(o: float, h: float, low: float, c: float) -> list[CandlePattern]:
    """Patterns detectable on one bar alone."""
    a = _anatomy(o, h, low, c)
    out: list[CandlePattern] = []
    if a["body"] <= 0.1 * a["range"]:
        out.append(CandlePattern("Doji", "neutral", strength=0.4))
    if a["body"] > 0 and a["lower"] >= 2 * a["body"] and a["upper"] <= 0.1 * a["range"]:
        # hammer if in downtrend context (caller decides), shape is same
        out.append(CandlePattern("Hammer/Shooting-Star shape",
                                 "neutral", strength=0.45,
                                 note="long lower shadow, tiny upper"))
    if a["body"] > 0 and a["upper"] >= 2 * a["body"] and a["lower"] <= 0.1 * a["range"]:
        out.append(CandlePattern("Inverted-Hammer shape",
                                 "neutral", strength=0.45,
                                 note="long upper shadow, tiny lower"))
    if a["body"] <= 0.3 * a["range"] and a["body"] > 0.05 * a["range"]:
        out.append(CandlePattern("Spinning Top", "neutral", strength=0.3))
    return out


# ── two-bar patterns ──────────────────────────────────────────────────────────

def two_bar_patterns(
    po: float, ph: float, pl: float, pc: float,
    o: float, h: float, low: float, c: float,
) -> list[CandlePattern]:
    """Patterns between the previous bar and the current bar."""
    out: list[CandlePattern] = []
    p_body = abs(pc - po)
    body = abs(c - o)
    prev_bear = pc < po
    prev_bull = pc > po
    cur_bull = c > o
    cur_bear = c < o

    # Bullish Engulfing: prev red, cur green, cur body covers prev body
    if prev_bear and cur_bull and c >= po and o <= pc and body > p_body:
        out.append(CandlePattern("Bullish Engulfing", "bullish", strength=0.8))
    # Bearish Engulfing
    if prev_bull and cur_bear and c <= po and o >= pc and body > p_body:
        out.append(CandlePattern("Bearish Engulfing", "bearish", strength=0.8))
    # Bullish Harami: prev big red, cur small green inside prev body
    if prev_bear and cur_bull and body < p_body and o >= pc and c <= po:
        out.append(CandlePattern("Bullish Harami", "bullish", strength=0.55))
    # Bearish Harami
    if prev_bull and cur_bear and body < p_body and o <= pc and c >= po:
        out.append(CandlePattern("Bearish Harami", "bearish", strength=0.55))
    # Piercing Line: prev red, cur opens below prev low, closes above prev mid
    if prev_bear and cur_bull and o < pl and c > (po + pc) / 2:
        out.append(CandlePattern("Piercing Line", "bullish", strength=0.65))
    # Dark Cloud Cover: prev green, cur opens above prev high, closes below mid
    if prev_bull and cur_bear and o > ph and c < (po + pc) / 2:
        out.append(CandlePattern("Dark Cloud Cover", "bearish", strength=0.65))
    return out


# ── three-bar patterns ────────────────────────────────────────────────────────

def three_bar_patterns(
    o1, h1, l1, c1,
    o2, h2, l2, c2,
    o3, h3, l3, c3,
) -> list[CandlePattern]:
    """Patterns across three consecutive bars."""
    out: list[CandlePattern] = []
    b1, b2, b3 = (abs(c1 - o1), abs(c2 - o2), abs(c3 - o3))
    r1, r2, r3 = (max(h1 - l1, _EPS), max(h2 - l2, _EPS), max(h3 - l3, _EPS))

    # Morning Star: big red, small middle (gap down), big green closing > mid of bar1
    if (c1 < o1 and b1 > 0.4 * r1 and b2 <= 0.2 * r2 and c3 > o3
            and c3 >= (o1 + c1) / 2 and b3 > 0.3 * r3):
        out.append(CandlePattern("Morning Star", "bullish", strength=0.85))
    # Evening Star: mirror
    if (c1 > o1 and b1 > 0.4 * r1 and b2 <= 0.2 * r2 and c3 < o3
            and c3 <= (o1 + c1) / 2 and b3 > 0.3 * r3):
        out.append(CandlePattern("Evening Star", "bearish", strength=0.85))
    # Three White Soldiers: three consecutive strong green, each closing higher
    if (c1 > o1 and c2 > o2 and c3 > o3
            and c2 > c1 and c3 > c2
            and b1 > 0.3 * r1 and b2 > 0.3 * r2 and b3 > 0.3 * r3):
        out.append(CandlePattern("Three White Soldiers", "bullish", strength=0.75))
    # Three Black Crows
    if (c1 < o1 and c2 < o2 and c3 < o3
            and c2 < c1 and c3 < c2
            and b1 > 0.3 * r1 and b2 > 0.3 * r2 and b3 > 0.3 * r3):
        out.append(CandlePattern("Three Black Crows", "bearish", strength=0.75))
    return out


# ── full sweep over a series ──────────────────────────────────────────────────

def detect_patterns(
    opens: list[float], highs: list[float],
    lows: list[float], closes: list[float],
) -> list[CandlePattern]:
    """Detect all patterns over an OHLC series (multi-bar aware)."""
    n = min(len(opens), len(highs), len(lows), len(closes))
    found: list[CandlePattern] = []
    for i in range(n):
        o, h, low, c = opens[i], highs[i], lows[i], closes[i]
        for p in single_bar_patterns(o, h, low, c):
            p.bar = i
            found.append(p)
        if i >= 1:
            po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
            for p in two_bar_patterns(po, ph, pl, pc, o, h, low, c):
                p.bar = i
                found.append(p)
        if i >= 2:
            o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
            o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
            for p in three_bar_patterns(o1, h1, l1, c1, o2, h2, l2, c2, o, h, low, c):
                p.bar = i
                found.append(p)
    return found


def latest_patterns(
    opens: list[float], highs: list[float],
    lows: list[float], closes: list[float],
    lookback: int = 3,
) -> list[CandlePattern]:
    """Only patterns involving the most recent `lookback` bars."""
    found = detect_patterns(opens, highs, lows, closes)
    n = len(opens)
    return [p for p in found if p.bar >= n - lookback]


def patterns_block(
    opens: list[float], highs: list[float],
    lows: list[float], closes: list[float],
    lookback: int = 3,
) -> str:
    """Compact '[Candles]' block for an LLM prompt."""
    pats = latest_patterns(opens, highs, lows, closes, lookback)
    if not pats:
        return "[Candles: no patterns in recent bars]"
    lines = ["[Candles — recent bar patterns]"]
    lines += ["  " + p.to_line() for p in pats[:8]]
    lines.append("[/Candles]")
    return "\n".join(lines)


__all__ = [
    "CandlePattern",
    "detect_patterns",
    "latest_patterns",
    "patterns_block",
    "single_bar_patterns",
    "three_bar_patterns",
    "two_bar_patterns",
]
