"""Тесты spend_forecast: прогноз, коридор, тренд, аномалии, runway."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.spend_forecast import (  # noqa: E402
    UsageDay,
    budget_runway,
    forecast_from_pairs,
    forecast_spend,
)


def _flat_history(days: int, tokens: int, cost_per_day: float = 10.0):
    return [
        UsageDay(date=f"2026-08-{d:02d}", tokens=tokens, cost_rub=cost_per_day)
        for d in range(1, days + 1)
    ]


class TestForecastBasics:
    def test_empty_history_returns_none(self):
        assert forecast_spend([]) is None

    def test_flat_history_predicts_itself(self):
        fc = forecast_spend(_flat_history(20, tokens=1000, cost_per_day=5.0))
        assert fc is not None
        assert abs(fc.baseline_daily_tokens - 1000.0) < 1e-9
        assert abs(fc.projected_month_tokens - 30_000.0) < 1e-9
        assert abs(fc.projected_month_cost_rub - 150.0) < 1e-9

    def test_window_limits_baseline(self):
        # 27 дней по 1000, затем 3 дня по 2000 (даты не пересекаются)
        hist = _flat_history(27, 1000)
        hist += [UsageDay(f"2026-09-{d:02d}", 2000) for d in range(1, 4)]
        fc = forecast_spend(hist, window=14)
        assert fc is not None
        assert fc.baseline_daily_tokens > 1000  # всплеск учтён
        full = forecast_spend(hist, window=30)
        assert full is not None
        assert full.baseline_daily_tokens < fc.baseline_daily_tokens

    def test_confidence_corridor_brackets_point(self):
        hist = _flat_history(20, 1000)
        hist[::3] = [UsageDay(d.date, d.tokens * 2, d.cost_rub) for d in hist[::3]]
        fc = forecast_spend(hist)
        assert fc is not None
        assert fc.low_month_tokens <= fc.projected_month_tokens <= fc.high_month_tokens


class TestTrend:
    def test_rising_detected(self):
        hist = [UsageDay(f"2026-07-{d:02d}", t) for d, t in enumerate(
            [500] * 10 + [1500] * 10, start=1)]
        fc = forecast_spend(hist)
        assert fc is not None
        assert fc.trend == "rising"
        assert fc.trend_pct > 1.0

    def test_falling_detected(self):
        hist = [UsageDay(f"2026-07-{d:02d}", t) for d, t in enumerate(
            [1500] * 10 + [500] * 10, start=1)]
        fc = forecast_spend(hist)
        assert fc is not None
        assert fc.trend == "falling"

    def test_flat_is_flat(self):
        fc = forecast_spend(_flat_history(12, 800))
        assert fc is not None and fc.trend == "flat"

    def test_too_short_for_trend(self):
        fc = forecast_spend(_flat_history(3, 800))
        assert fc is not None and fc.trend == "flat"


class TestAnomalies:
    def test_spike_flagged(self):
        hist = _flat_history(14, 1000)
        hist[7] = UsageDay("2026-08-08", tokens=20_000, cost_rub=90.0)
        fc = forecast_spend(hist)
        assert fc is not None
        assert len(fc.anomalies) == 1
        assert fc.anomalies[0].tokens == 20_000
        assert fc.anomalies[0].deviation_sigma > 2

    def test_no_false_alarms_on_smooth_data(self):
        fc = forecast_spend(_flat_history(21, 1200))
        assert fc is not None and fc.anomalies == []

    def test_block_mentions_anomaly(self):
        hist = _flat_history(14, 1000)
        hist[3] = UsageDay("2026-08-04", tokens=25_000, cost_rub=99.0)
        text = forecast_spend(hist).block()
        assert "аномалия" in text
        assert "2026-08-04" in text


class TestRunway:
    def test_runway_days(self):
        hist = _flat_history(14, 100, cost_per_day=50.0)
        rw = budget_runway(hist, budget_rub=1000.0)
        assert rw is not None
        days, rate = rw
        assert days == 20 and abs(rate - 50.0) < 1e-9

    def test_runway_guards(self):
        assert budget_runway([], 1000) is None
        assert budget_runway(_flat_history(5, 10), 0) is None
        assert budget_runway(_flat_history(5, 10, cost_per_day=0.0), 500) is None


class TestPairsWrapper:
    def test_forecast_from_pairs_matches_objects(self):
        pairs = [(f"2026-08-{d:02d}", 700, 3.0) for d in range(1, 11)]
        a = forecast_from_pairs(pairs)
        b = forecast_spend([UsageDay(*p) for p in pairs])
        assert a is not None and b is not None
        assert a.projected_month_tokens == b.projected_month_tokens
