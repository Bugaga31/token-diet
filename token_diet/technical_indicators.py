"""technical_indicators — the indicators we were missing, pure Python.

We already ship RSI/MACD/Bollinger/ATR in market_intelligence.py. This
module adds the ones we DIDN'T have — Stochastic, On-Balance Volume,
Williams %R, Money Flow Index — plus technical_snapshot(), a compact
"one-screen technical picture" built from both.

Reverse-engineered from:
    TA-Lib (stoch, obv, willr, mfi) and TradingView's standard
    indicator definitions. Deterministic, stdlib only.

Why this matters for token-diet:
    A compact snapshot — "RSI 34 (oversold), Stochastic %K 21/%D 25,
    OBV rising, MFI 28" — is ~60 tokens and is *analysis*, while the
    raw 30 candles behind it are ~800 tokens of numbers.
"""

from __future__ import annotations

from typing import Any


def ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average (seed = first value)."""
    if not values or period < 1:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append((v - out[-1]) * k + out[-1])
    return out


def sma(values: list[float], period: int) -> list[float | None]:
    if not values or period < 1:
        return []
    out: list[float | None] = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
        else:
            out.append(sum(values[i - period + 1:i + 1]) / period)
    return out


def stochastic(
    highs: list[float], lows: list[float], closes: list[float],
    k_period: int = 14, d_period: int = 3,
) -> tuple[list[float | None], list[float | None]]:
    """Stochastic oscillator %K and %D (0-100)."""
    n = min(len(highs), len(lows), len(closes))
    k_line: list[float | None] = []
    for i in range(n):
        if i < k_period - 1:
            k_line.append(None)
            continue
        hh = max(highs[i - k_period + 1:i + 1])
        ll = min(lows[i - k_period + 1:i + 1])
        if hh == ll:
            k_line.append(50.0)
        else:
            k_line.append((closes[i] - ll) / (hh - ll) * 100.0)
    # %D = SMA of %K
    k_vals = [k for k in k_line if k is not None]
    d_line: list[float | None] = [None] * (n - len(k_vals))
    for i in range(len(k_vals)):
        if i < d_period - 1:
            d_line.append(None)
        else:
            d_line.append(sum(k_vals[i - d_period + 1:i + 1]) / d_period)
    return k_line, d_line


def obv(closes: list[float], volumes: list[float]) -> list[float]:
    """On-Balance Volume: volume accumulates on up days, drains on down."""
    n = min(len(closes), len(volumes))
    if n == 0:
        return []
    out = [volumes[0]]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            out.append(out[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            out.append(out[-1] - volumes[i])
        else:
            out.append(out[-1])
    return out


def williams_r(
    highs: list[float], lows: list[float], closes: list[float],
    period: int = 14,
) -> list[float | None]:
    """Williams %R: -100 (oversold) .. 0 (overbought)."""
    n = min(len(highs), len(lows), len(closes))
    out: list[float | None] = []
    for i in range(n):
        if i < period - 1:
            out.append(None)
            continue
        hh = max(highs[i - period + 1:i + 1])
        ll = min(lows[i - period + 1:i + 1])
        out.append(-100.0 * (hh - closes[i]) / (hh - ll) if hh != ll else -50.0)
    return out


def mfi(
    highs: list[float], lows: list[float], closes: list[float],
    volumes: list[float], period: int = 14,
) -> list[float | None]:
    """Money Flow Index: volume-weighted RSI (0-100)."""
    n = min(len(highs), len(lows), len(closes), len(volumes))
    out: list[float | None] = []
    if n < period + 1:
        return [None] * n
    typical: list[float] = []
    flows: list[float] = []
    for i in range(n):
        tp = (highs[i] + lows[i] + closes[i]) / 3.0
        typical.append(tp)
        if i > 0:
            flows.append((tp - typical[i - 1], volumes[i]))
    for i in range(period, n):
        pos = neg = 0.0
        for (delta, vol) in flows[i - period:i]:
            if delta > 0:
                pos += vol
            elif delta < 0:
                neg += vol
        ratio = pos / neg if neg > 0 else 100.0
        out.append(100.0 - 100.0 / (1.0 + ratio))
    return [None] * period + out[: n - period]


def obv_trend(obv_values: list[float], lookback: int = 5) -> str:
    """rising | falling | flat — last `lookback` points of OBV."""
    recent = obv_values[-lookback:]
    if len(recent) < 2:
        return "flat"
    if recent[-1] > recent[0] * 1.001:
        return "rising"
    if recent[-1] < recent[0] * 0.999:
        return "falling"
    return "flat"


def technical_snapshot(
    highs: list[float], lows: list[float],
    closes: list[float], volumes: list[float],
) -> dict[str, Any]:
    """One-screen technical picture (reuses RSI/ATR from market_intelligence)."""
    from .market_intelligence import atr, rsi

    out: dict[str, Any] = {"bars": len(closes)}
    r = rsi(closes)
    if r and r[-1] is not None:
        out["rsi"] = round(r[-1], 1)
        out["rsi_state"] = ("oversold" if r[-1] <= 30
                            else "overbought" if r[-1] >= 70 else "neutral")
    k, d = stochastic(highs, lows, closes)
    if k and k[-1] is not None:
        out["stoch_k"] = round(k[-1], 1)
        out["stoch_d"] = round(d[-1], 1) if d and d[-1] is not None else None
        out["stoch_state"] = ("oversold" if k[-1] <= 20
                              else "overbought" if k[-1] >= 80 else "neutral")
    wr = williams_r(highs, lows, closes)
    if wr and wr[-1] is not None:
        out["williams_r"] = round(wr[-1], 1)
    m = mfi(highs, lows, closes, volumes)
    if m and m[-1] is not None:
        out["mfi"] = round(m[-1], 1)
        out["mfi_state"] = ("oversold" if m[-1] <= 20
                            else "overbought" if m[-1] >= 80 else "neutral")
    ob = obv(closes, volumes)
    if ob:
        out["obv_trend"] = obv_trend(ob)
    a = atr(highs, lows, closes)
    if a and a[-1] is not None and closes:
        out["atr"] = round(a[-1], 2)
        out["atr_pct"] = round(100 * a[-1] / closes[-1], 2)
    return out


def snapshot_block(
    highs: list[float], lows: list[float],
    closes: list[float], volumes: list[float],
) -> str:
    """Compact '[Technical]' block for an LLM prompt."""
    s = technical_snapshot(highs, lows, closes, volumes)
    if not s.get("rsi") and not s.get("stoch_k"):
        return "[Technical: insufficient data]"
    lines = ["[Technical]"]
    if "rsi" in s:
        lines.append(f"  RSI {s['rsi']} ({s['rsi_state']})")
    if "stoch_k" in s:
        d = f"/{s['stoch_d']}" if s.get("stoch_d") is not None else ""
        lines.append(f"  Stoch {s['stoch_k']}{d} ({s['stoch_state']})")
    if "williams_r" in s:
        lines.append(f"  Williams%R {s['williams_r']}")
    if "mfi" in s:
        lines.append(f"  MFI {s['mfi']} ({s['mfi_state']})")
    if "obv_trend" in s:
        lines.append(f"  OBV {s['obv_trend']}")
    if "atr_pct" in s:
        lines.append(f"  ATR {s['atr']} ({s['atr_pct']}%)")
    lines.append("[/Technical]")
    return "\n".join(lines)


__all__ = [
    "ema",
    "mfi",
    "obv",
    "obv_trend",
    "sma",
    "snapshot_block",
    "stochastic",
    "technical_snapshot",
    "williams_r",
]
