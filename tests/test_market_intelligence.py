"""Tests for market_intelligence.py — technical analysis + signals + sentiment."""

from token_diet.market_intelligence import (
    aggregate_sentiment,
    analyze_signals,
    atr,
    bollinger_bands,
    ema,
    estimate_price_range,
    find_support_resistance,
    generate_signal,
    macd,
    rank_market,
    rsi,
    score_sentiment,
    screen_market,
    sma,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Indicators
# ═══════════════════════════════════════════════════════════════════════════════

class TestSMA:
    def test_basic(self):
        result = sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        assert result == [None, None, 2.0, 3.0, 4.0]

    def test_too_short(self):
        assert sma([1.0, 2.0], 5) == [None, None]


class TestEMA:
    def test_basic(self):
        result = ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] is not None


class TestRSI:
    def test_all_equal(self):
        data = [10.0] * 20
        result = rsi(data)
        # All equal = no changes = RSI should be 100 (all gains, no losses)
        assert result[-1] is not None

    def test_uptrend(self):
        data = list(range(1, 21))  # 1, 2, 3, ..., 20 (always up)
        result = rsi(data)
        assert result[-1] is not None
        assert result[-1] > 50  # strong uptrend


class TestMACD:
    def test_basic(self):
        data = [float(i) for i in range(1, 40)]
        result = macd(data)
        assert "macd" in result
        assert "signal" in result
        assert "histogram" in result


class TestBollinger:
    def test_basic(self):
        data = [10.0, 11.0, 12.0, 11.0, 10.0] * 5
        result = bollinger_bands(data, period=5)
        assert "upper" in result
        assert "lower" in result
        assert result["upper"][-1] >= result["lower"][-1]


class TestATR:
    def test_basic(self):
        highs = [10.0, 11.0, 12.0] * 7
        lows = [9.0, 10.0, 11.0] * 7
        closes = [9.5, 10.5, 11.5] * 7
        result = atr(highs, lows, closes, period=5)
        assert result[-1] is not None
        assert result[-1] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Support / Resistance
# ═══════════════════════════════════════════════════════════════════════════════

class TestSR:
    def test_basic(self):
        data = [10.0, 12.0, 10.5, 13.0, 10.0, 14.0, 10.5] * 5
        result = find_support_resistance(data)
        assert "support" in result
        assert "resistance" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Signals
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnalyzeSignals:
    def test_basic(self):
        data = [float(i) for i in range(50, 20, -1)] + [float(i) for i in range(20, 40)]
        report = analyze_signals("TEST", data)
        assert report.ticker == "TEST"
        assert len(report.signals) >= 3  # RSI, MACD, SMA, Bollinger

    def test_short_data(self):
        report = analyze_signals("TEST", [1.0, 2.0, 3.0])
        assert report.price == 3.0


# ═══════════════════════════════════════════════════════════════════════════════
# Sentiment
# ═══════════════════════════════════════════════════════════════════════════════

class TestSentiment:
    def test_bullish(self):
        result = score_sentiment("покупать акции рост бычий рынок")
        assert result["score"] > 0

    def test_bearish(self):
        result = score_sentiment("продавать падение обвал медвежий")
        assert result["score"] < 0

    def test_neutral(self):
        result = score_sentiment("сегодня хорошая погода")
        assert result["score"] == 0.0

    def test_aggregate(self):
        texts = ["покупать рост", "покупать дивиденды прибыль", "золото дорожает"]
        result = aggregate_sentiment(texts)
        assert result["signal"] == "bullish"
        assert result["sample_size"] == 3

    def test_empty(self):
        result = aggregate_sentiment([])
        assert result["sample_size"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Full pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestGenerateSignal:
    def test_basic(self):
        closes = [1300 + i * 2 + (i % 5) * 5 for i in range(30)]
        signal = generate_signal("TEST", closes)
        assert signal.ticker == "TEST"
        assert signal.final_verdict in ("BUY", "SELL", "HOLD")
        assert 0 <= signal.final_confidence <= 1

    def test_with_sentiment(self):
        closes = list(range(100, 130))
        signal = generate_signal(
            "TEST", closes,
            sentiment_texts=["покупать рост бычий", "отличный отчёт"],
        )
        assert signal.sentiment_signal in ("bullish", "bearish", "neutral")


class TestLeanAndScreener:
    """Новый слой: непрерывный наклон lean + скринер ранжирования."""

    def test_lean_nonzero_with_enough_data(self):
        # 60 точек (как 90 дней торговли) — движку хватает на MACD(26)+буфер
        closes = [1300 + i * 3 + (i % 7) * 4 for i in range(60)]  # явный аптренд
        signal = generate_signal("TEST", closes)
        assert signal.lean > 0, f"аптренд должен давать положительный lean, а не {signal.lean}"
        assert len(signal.signals_detail) > 0

    def test_lean_negative_on_downtrend(self):
        closes = [2000 - i * 3 - (i % 5) * 2 for i in range(60)]  # явный даунтренд
        signal = generate_signal("TEST", closes)
        assert signal.lean < 0, f"даунтренд должен давать отрицательный lean, а не {signal.lean}"

    def test_short_data_returns_empty(self):
        # 23 свечи (как 30 календарных дней) — движку НЕ хватает, сигналов нет
        closes = [100 + i for i in range(23)]
        signal = generate_signal("SHORT", closes)
        assert signal.lean == 0.0
        assert len(signal.signals_detail) == 0

    def test_screen_market_sorts_by_lean(self):
        # Разные паттерны дают разные lean — скринер должен их различать и сортировать
        up = [100 + i * 2 for i in range(60)]
        down = [200 - i * 2 for i in range(60)]
        flat = [150 + 3 * (i % 2) for i in range(60)]
        ranked = screen_market({
            "UP": {"closes": up},
            "DOWN": {"closes": down},
            "FLAT": {"closes": flat},
        })
        assert len(ranked) == 3
        # ключевое: отсортировано по lean по убыванию
        for i in range(len(ranked) - 1):
            assert ranked[i].lean >= ranked[i + 1].lean
        # и lean реально различается (движок различает бумаги, а не плоский 0)
        leans = [s.lean for s in ranked]
        assert len(set(leans)) > 1

    def test_rank_market_text(self):
        up = [100 + i * 2 for i in range(60)]
        down = [200 - i * 2 for i in range(60)]
        ranked = screen_market({"UP": {"closes": up}, "DOWN": {"closes": down}})
        text = rank_market(ranked)
        assert "UP" in text and "DOWN" in text


class TestEstimateRange:
    def test_basic(self):
        closes = [100 + i for i in range(20)]
        result = estimate_price_range("TEST", closes)
        assert result["expected_high"] > result["expected_low"]
        assert result["daily_volatility_pct"] > 0
