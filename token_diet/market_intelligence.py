"""Market Intelligence — honest signals from data, not guesses.

Reverse-engineered from:
- TradingView Pine Script: RSI, MACD, BB, MA crossover logic
- pandas-ta / FinTA: pure-Python indicator implementations
- Professional trading desks: multi-signal confluence + volume confirmation
- scipy.signal: peak detection for support/resistance

Philosophy: no neural models, no API calls. Pure math on price data.
Every signal has a confidence score. No signal = no trade.

For the people. Honesty is cheaper than regret.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Pure-Python Technical Indicators (zero dependencies beyond stdlib)
# ═══════════════════════════════════════════════════════════════════════════════

def sma(values: list[float], period: int) -> list[float | None]:
    """Simple Moving Average. First period-1 values are None."""
    if len(values) < period:
        return [None] * len(values)
    result: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        result.append(sum(window) / period)
    return result


def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential Moving Average."""
    if len(values) < period:
        return [None] * len(values)
    k = 2.0 / (period + 1)
    result: list[float | None] = [None] * (period - 1)
    # Seed with SMA
    seed = sum(values[:period]) / period
    result.append(seed)
    for i in range(period, len(values)):
        result.append(values[i] * k + (result[-1] or 0) * (1 - k))
    return result


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    """Relative Strength Index. Returns 0-100."""
    if len(values) < period + 1:
        return [None] * len(values)
    result: list[float | None] = [None] * period
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # First RSI
    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(100.0 - (100.0 / (1.0 + rs)))
    # Subsequent
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100.0 - (100.0 / (1.0 + rs)))
    return result


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9,
) -> dict[str, list[float | None]]:
    """MACD: returns {macd_line, signal_line, histogram}."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line: list[float | None] = []
    for i in range(len(values)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line.append(ema_fast[i] - ema_slow[i])  # type: ignore[operator]
        else:
            macd_line.append(None)
    # Signal line = EMA of macd_line
    valid_macd = [v for v in macd_line if v is not None]
    sig_line = ema(valid_macd, signal)
    # Pad signal line to match
    pad = len(values) - len(sig_line)
    signal_padded: list[float | None] = [None] * pad + sig_line
    # Histogram
    hist: list[float | None] = []
    for i in range(len(values)):
        if macd_line[i] is not None and signal_padded[i] is not None:
            hist.append(macd_line[i] - signal_padded[i])  # type: ignore[operator]
        else:
            hist.append(None)
    return {"macd": macd_line, "signal": signal_padded, "histogram": hist}


def bollinger_bands(
    values: list[float], period: int = 20, std_dev: float = 2.0,
) -> dict[str, list[float | None]]:
    """Bollinger Bands: {middle, upper, lower, width_pct}."""
    ma = sma(values, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    width: list[float | None] = []
    for i in range(len(values)):
        if ma[i] is not None:
            window = values[i - period + 1 : i + 1]
            variance = sum((x - ma[i]) ** 2 for x in window) / period  # type: ignore[operator]
            std = math.sqrt(variance)
            upper.append(ma[i] + std_dev * std)  # type: ignore[operator]
            lower.append(ma[i] - std_dev * std)  # type: ignore[operator]
            w = (upper[-1] - lower[-1]) / ma[i] * 100 if ma[i] else 0  # type: ignore[operator]
            width.append(w)
        else:
            upper.append(None)
            lower.append(None)
            width.append(None)
    return {"middle": ma, "upper": upper, "lower": lower, "width_pct": width}


def atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[float | None]:
    """Average True Range — volatility measure in price units."""
    n = min(len(high), len(low), len(close))
    tr: list[float] = []
    for i in range(n):
        if i == 0:
            tr.append(high[i] - low[i])
        else:
            tr.append(max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            ))
    # Wilder's smoothing
    result: list[float | None] = [None] * (period - 1)
    if len(tr) < period:
        return [None] * n
    atr_val = sum(tr[:period]) / period
    result.append(atr_val)
    for i in range(period, len(tr)):
        atr_val = (atr_val * (period - 1) + tr[i]) / period
        result.append(atr_val)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Support / Resistance detection (scipy peaks, pure-Python fallback)
# ═══════════════════════════════════════════════════════════════════════════════

def _find_peaks_pure(values: list[float], distance: int = 5, prominence: float = 0.02) -> list[int]:
    """Pure-Python peak detection (no scipy dependency)."""
    if len(values) < 2 * distance + 1:
        return []
    peaks = []
    for i in range(distance, len(values) - distance):
        left = values[i - distance : i]
        right = values[i + 1 : i + distance + 1]
        if values[i] > max(left) and values[i] > max(right):
            # Prominence check
            max_nearby = max(max(left), max(right))
            if (values[i] - max_nearby) / max(max_nearby, 0.01) > prominence:
                peaks.append(i)
    return peaks


def find_support_resistance(
    prices: list[float],
    distance: int = 5,
    prominence: float = 0.02,
) -> dict[str, list[float]]:
    """Find support (lows) and resistance (highs) levels.

    Uses scipy.signal.find_peaks if available, pure Python fallback.
    Returns {support: [...], resistance: [...]}.
    """
    try:
        from scipy.signal import find_peaks
        peaks, _ = find_peaks(prices, distance=distance, prominence=prominence * max(prices))
        troughs, _ = find_peaks([-p for p in prices], distance=distance, prominence=prominence * max(prices))
        return {
            "resistance": sorted([prices[i] for i in peaks], reverse=True)[:5],
            "support": sorted([prices[i] for i in troughs])[:5],
        }
    except ImportError:
        peaks = _find_peaks_pure(prices, distance, prominence)
        troughs = _find_peaks_pure([-p for p in prices], distance, prominence)
        return {
            "resistance": sorted([prices[i] for i in peaks], reverse=True)[:5],
            "support": sorted([prices[i] for i in troughs])[:5],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Multi-Signal Confluence Engine
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SignalResult:
    """A single indicator's signal."""

    name: str
    direction: int  # +1 bullish, -1 bearish, 0 neutral
    strength: float  # 0..1
    description: str = ""


@dataclass
class ConfluenceReport:
    """Aggregated signal report."""

    ticker: str
    price: float
    signals: list[SignalResult] = field(default_factory=list)
    buy_count: int = 0
    sell_count: int = 0
    neutral_count: int = 0
    confidence: float = 0.0
    weighted_score: float = 0.0  # знаковый непрерывный счёт (−1..+1)
    verdict: str = "HOLD"  # BUY / SELL / HOLD
    price_targets: dict[str, list[float]] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if self.verdict == "BUY":
            return f"BUY {self.ticker} @ {self.price:.2f} (confidence: {self.confidence:.0%})"
        if self.verdict == "SELL":
            return f"SELL {self.ticker} @ {self.price:.2f} (confidence: {self.confidence:.0%})"
        return f"HOLD {self.ticker} @ {self.price:.2f} (signals mixed)"


def analyze_signals(
    ticker: str,
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
    *,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    sma_period: int = 20,
) -> ConfluenceReport:
    """Run all technical indicators and produce a confluence report.

    This is the core engine — reverse-engineered from TradingView Pine Script
    multi-indicator strategies used by professional traders.
    """
    if len(closes) < max(rsi_period, macd_slow, sma_period) + 5:
        return ConfluenceReport(ticker=ticker, price=closes[-1] if closes else 0)

    price = closes[-1]
    report = ConfluenceReport(ticker=ticker, price=price)
    signals: list[SignalResult] = []

    # ── 1. RSI ──
    rsi_vals = rsi(closes, rsi_period)
    rsi_now = rsi_vals[-1] if rsi_vals[-1] is not None else 50
    if rsi_now < 30:
        signals.append(SignalResult("RSI", +1, (30 - rsi_now) / 30, f"oversold ({rsi_now:.0f})"))
    elif rsi_now > 70:
        signals.append(SignalResult("RSI", -1, (rsi_now - 70) / 30, f"overbought ({rsi_now:.0f})"))
    else:
        signals.append(SignalResult("RSI", 0, 0.1, f"neutral ({rsi_now:.0f})"))

    # ── 2. MACD ──
    macd_data = macd(closes, macd_fast, macd_slow)
    macd_hist = macd_data["histogram"]
    if macd_hist[-1] is not None and macd_hist[-2] is not None:
        if macd_hist[-2] <= 0 and macd_hist[-1] > 0:
            signals.append(SignalResult("MACD", +1, 0.7, "bullish crossover"))
        elif macd_hist[-2] >= 0 and macd_hist[-1] < 0:
            signals.append(SignalResult("MACD", -1, 0.7, "bearish crossover"))
        else:
            direction = 1 if macd_hist[-1] > 0 else -1
            signals.append(SignalResult("MACD", direction, 0.3, f"trend {'up' if direction > 0 else 'down'}"))
    else:
        signals.append(SignalResult("MACD", 0, 0.0, "no data"))

    # ── 3. SMA trend ──
    sma_vals = sma(closes, sma_period)
    if sma_vals[-1] is not None:
        if price > sma_vals[-1]:
            signals.append(SignalResult("SMA", +1, 0.4, f"price > SMA{sma_period}"))
        else:
            signals.append(SignalResult("SMA", -1, 0.4, f"price < SMA{sma_period}"))

    # ── 4. Bollinger squeeze ──
    bb = bollinger_bands(closes, 20)
    if bb["width_pct"][-1] is not None:
        widths = [w for w in bb["width_pct"] if w is not None]
        if len(widths) >= 10 and widths[-1] < min(widths[-10:-1]) * 0.8:
            signals.append(SignalResult("Bollinger", +1, 0.6, "squeeze — breakout coming"))
        elif bb["upper"][-1] and price > (bb["upper"][-1] or 0):
            signals.append(SignalResult("Bollinger", -1, 0.5, "price above upper band"))
        elif bb["lower"][-1] and price < (bb["lower"][-1] or 0):
            signals.append(SignalResult("Bollinger", +1, 0.5, "price below lower band"))
        else:
            signals.append(SignalResult("Bollinger", 0, 0.1, "inside bands"))

    # ── 5. Volume confirmation (if available) ──
    if volumes and len(volumes) >= 20:
        avg_vol = sum(volumes[-20:]) / 20
        if volumes[-1] > avg_vol * 1.5:
            signals.append(SignalResult("Volume", 1 if price > (sma_vals[-1] or price) else -1, 0.3, "high volume"))

    # ── Aggregate ──
    report.signals = signals
    report.buy_count = sum(1 for s in signals if s.direction > 0)
    report.sell_count = sum(1 for s in signals if s.direction < 0)
    report.neutral_count = len(signals) - report.buy_count - report.sell_count

    total_signals = len(signals)
    if total_signals > 0:
        # Weighted: stronger signals count more
        weighted_score = sum(s.direction * s.strength for s in signals)
        report.weighted_score = round(weighted_score / (total_signals * 0.7), 4)
        report.confidence = min(1.0, abs(weighted_score) / (total_signals * 0.7))

    if report.buy_count >= 3 and report.buy_count > report.sell_count:
        report.verdict = "BUY"
    elif report.sell_count >= 3 and report.sell_count > report.buy_count:
        report.verdict = "SELL"
    else:
        report.verdict = "HOLD"

    # ── Price targets (support/resistance) ──
    sr = find_support_resistance(closes)
    report.price_targets = sr

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Sentiment scoring from text (integrates with telegram_market_feed)
# ═══════════════════════════════════════════════════════════════════════════════

# Russian bullish/bearish word lists
_BULLISH_RU = {
    "рост", "расти", "вырастет", "поднимется", "покупать", "покупка",
    "лонг", "бычий", "позитив", "хороший отчёт", "прибыль выросла",
    "дивиденды", "рекомендация покупать", "выше рынка", "лучше рынка",
    "оптимистично", "восстановление", "ралли", "прорыв",
    # сленг Пульса/розницы
    "закупился", "докупаю", "добираю", "набираю", "дожмём", "выстрелит",
    "в лонге", "перезакупился", "отскок", "дно позади", "голд-ралли",
}
_BEARISH_RU = {
    "падение", "падать", "упадёт", "снизится", "продавать", "продажа",
    "шорт", "медвежий", "негатив", "плохой отчёт", "убыток",
    "упал", "обвалился", "рухнул", "коррекция", "слив",
    "пессимистично", "слабый", "хуже рынка", "рекомендация продавать",
    # сленг Пульса/розницы
    "сливают", "слили", "вышел", "выхожу", "не верю", "дно не видно",
    "паника", "дамп", "стопы выбивают",
}
_NEUTRAL_RU = {
    "нейтрально", "держать", "hold", "стабильно", "боковик",
    "консолидация", "флэт", "неопределённость",
}


def score_sentiment(text: str) -> dict[str, Any]:
    """Score sentiment of a text snippet (Russian + English).

    Returns {score: -1..+1, strength: 0..1, words_found: int}.
    Negative = bearish, positive = bullish.
    """
    text_lower = text.lower()
    bullish = sum(1 for w in _BULLISH_RU if w in text_lower)
    bearish = sum(1 for w in _BEARISH_RU if w in text_lower)
    neutral = sum(1 for w in _NEUTRAL_RU if w in text_lower)

    total = bullish + bearish + neutral
    if total == 0:
        return {"score": 0.0, "strength": 0.0, "words_found": 0}

    score = (bullish - bearish) / max(total, 1)
    strength = total / max(10, total + 5)  # cap at ~0.7
    return {"score": score, "strength": strength, "words_found": total}


def aggregate_sentiment(texts: list[str]) -> dict[str, Any]:
    """Aggregate sentiment across multiple text sources.

    Returns {score, strength, sample_size, signal}.
    """
    if not texts:
        return {"score": 0.0, "strength": 0.0, "sample_size": 0, "signal": "neutral"}

    scores = [score_sentiment(t) for t in texts]
    weighted = sum(s["score"] * s["strength"] for s in scores)
    total_strength = sum(s["strength"] for s in scores)
    agg_score = weighted / max(total_strength, 0.01)

    signal = "bullish" if agg_score > 0.2 else ("bearish" if agg_score < -0.2 else "neutral")
    return {
        "score": agg_score,
        "strength": total_strength / len(texts),
        "sample_size": len(texts),
        "signal": signal,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Full pipeline: price data + sentiment → trading signal
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradingSignal:
    """Final trading recommendation."""

    ticker: str
    price: float
    technical_verdict: str  # BUY / SELL / HOLD
    technical_confidence: float
    sentiment_score: float
    sentiment_signal: str
    final_verdict: str  # BUY / SELL / HOLD
    final_confidence: float
    lean: float = 0.0  # непрерывный наклон −1..+1 (даже при HOLD)
    price_targets: dict[str, list[float]] = field(default_factory=dict)
    signals_detail: list[SignalResult] = field(default_factory=list)

    @property
    def lean_direction(self) -> str:
        if self.lean > 0.05:
            return "bullish"
        if self.lean < -0.05:
            return "bearish"
        return "flat"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "technical": {"verdict": self.technical_verdict, "confidence": self.technical_confidence},
            "sentiment": {"score": self.sentiment_score, "signal": self.sentiment_signal},
            "final": {"verdict": self.final_verdict, "confidence": self.final_confidence},
            "lean": self.lean,
            "lean_direction": self.lean_direction,
            "price_targets": self.price_targets,
        }


def generate_signal(
    ticker: str,
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
    sentiment_texts: list[str] | None = None,
) -> TradingSignal:
    """Generate a complete trading signal from price data + sentiment.

    This is THE function — combines technical analysis and sentiment into
    one actionable recommendation, just like professional trading desks.

    Usage:
        # Just technical (no sentiment)
        signal = generate_signal("PLZL", closes=[1340, 1345, 1350, ...])

        # With Telegram sentiment
        signal = generate_signal("PLZL", closes=..., sentiment_texts=tg_messages)
    """
    # Technical analysis
    report = analyze_signals(ticker, closes, highs, lows, volumes)

    # Sentiment
    sentiment = aggregate_sentiment(sentiment_texts or [])

    # Combine: 70% technical + 30% sentiment
    tech_weight = 0.7
    sent_weight = 0.3

    # Используем знаковый непрерывный счёт техники (а не только BUY/SELL),
    # чтобы даже HOLD-бумаги имели разный наклон lean.
    tech_score = report.weighted_score

    sent_score = sentiment["score"] * sentiment["strength"]

    combined = tech_score * tech_weight + sent_score * sent_weight

    if combined > 0.15:
        verdict = "BUY"
        confidence = min(1.0, abs(combined) * 1.5)
    elif combined < -0.15:
        verdict = "SELL"
        confidence = min(1.0, abs(combined) * 1.5)
    else:
        verdict = "HOLD"
        confidence = max(0.1, 0.5 - abs(combined))

    return TradingSignal(
        ticker=ticker,
        price=closes[-1] if closes else 0,
        technical_verdict=report.verdict,
        technical_confidence=report.confidence,
        sentiment_score=sentiment["score"],
        sentiment_signal=sentiment["signal"],
        final_verdict=verdict,
        final_confidence=confidence,
        lean=round(combined, 3),
        price_targets=report.price_targets,
        signals_detail=report.signals,
    )


def screen_market(
    candles_by_ticker: dict[str, dict],
) -> list[TradingSignal]:
    """Прогнать сигналы по всем тикерам и отсортировать по lean (наклону).

    Даже если формально всё HOLD, эта функция показывает, КТО ближе всего
    к BUY (положительный lean) и кто ближе всего к SELL (отрицательный).
    Это превращает плоское «всё HOLD» в ранжированный список для слежки.

    candles_by_ticker: {тикер: {"closes": [...], "highs": [...],
                                 "lows": [...], "volumes": [...],
                                 "sentiment": [...]}}
    """
    results: list[TradingSignal] = []
    for ticker, data in candles_by_ticker.items():
        s = generate_signal(
            ticker,
            data["closes"],
            data.get("highs"),
            data.get("lows"),
            data.get("volumes"),
            data.get("sentiment"),
        )
        results.append(s)
    results.sort(key=lambda s: -s.lean)
    return results


def rank_market(signals: list[TradingSignal]) -> str:
    """Красивый текстовый ранкинг: кто к покупке, кто к продаже."""
    if not signals:
        return "нет данных"
    lines = [f"{"ТИКЕР":6} {"ЦЕНА":>9} {"LEAN":>7}  НАКЛОН"]
    for s in signals:
        arrow = "🟢" if s.lean > 0.05 else ("🔴" if s.lean < -0.05 else "⚪")
        lines.append(f"{arrow} {s.ticker:5} {s.price:>9} {s.lean:>+7.3f}  {s.lean_direction}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Quick price target: "where will it go?"
# ═══════════════════════════════════════════════════════════════════════════════

def estimate_price_range(
    ticker: str,
    closes: list[float],
    confidence: float = 0.68,  # 1 sigma
) -> dict[str, Any]:
    """Estimate tomorrow's price range based on recent volatility.

    Uses ATR for expected range. Returns {low, high, center, atr}.
    """
    if len(closes) < 15:
        return {"ticker": ticker, "error": "need 15+ data points"}

    # Volatility from recent 14 days
    changes = [abs(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    avg_change = sum(changes[-14:]) / min(len(changes), 14)
    price = closes[-1]

    low = price * (1 - avg_change)
    high = price * (1 + avg_change)

    # Support/resistance
    sr = find_support_resistance(closes)

    return {
        "ticker": ticker,
        "price": price,
        "expected_low": round(low, 2),
        "expected_high": round(high, 2),
        "daily_volatility_pct": round(avg_change * 100, 2),
        "support_levels": sr.get("support", [])[:3],
        "resistance_levels": sr.get("resistance", [])[:3],
    }
