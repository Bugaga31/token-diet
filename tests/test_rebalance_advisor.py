"""Тесты rebalance_advisor: дрейф-коридор, пыль, кэш, детерминизм.

Модуль только советует — тесты дополнительно проверяют, что в плане
нет ничего, похожего на исполнение ордеров (только строки действий).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.rebalance_advisor import (  # noqa: E402
    Holding,
    drift_report,
    rebalance_plan,
)


def _h(ticker: str, qty: float, price: float) -> Holding:
    return Holding(ticker=ticker, quantity=qty, price=price)


class TestWeightsAndDrift:
    def test_balanced_portfolio_is_hold(self):
        holdings = [_h("SBER", 1, 50_000), _h("LKOH", 1, 50_000)]
        plan = rebalance_plan(holdings, {"SBER": 0.5, "LKOH": 0.5})
        assert all(a.action == "HOLD" for a in plan.advices)
        assert plan.actions_needed == 0
        assert "действий не нужно" in plan.block()

    def test_overweight_sells(self):
        # SBER 90% при цели 50% → SELL половины перевеса
        holdings = [_h("SBER", 9, 10_000), _h("LKOH", 1, 10_000)]
        plan = rebalance_plan(holdings, {"SBER": 0.5, "LKOH": 0.5}, drift_band=0.05)
        by = {a.ticker: a for a in plan.advices}
        assert by["SBER"].action == "SELL"
        assert abs(by["SBER"].amount_rub - 40_000) < 1e-6
        assert by["SBER"].reason.startswith("перевес")
        assert by["LKOH"].action == "BUY"

    def test_underweight_buys(self):
        holdings = [_h("SBER", 1, 10_000), _h("LKOH", 9, 10_000)]
        plan = rebalance_plan(holdings, {"SBER": 0.5, "LKOH": 0.5})
        by = {a.ticker: a for a in plan.advices}
        assert by["SBER"].action == "BUY" and by["LKOH"].action == "SELL"


class TestGuards:
    def test_drift_band_holds_small_deviation(self):
        holdings = [_h("A", 52, 100), _h("B", 48, 100)]  # 52/48 vs 50/50
        plan = rebalance_plan(holdings, {"A": 0.5, "B": 0.5}, drift_band=0.05)
        assert all(a.action == "HOLD" for a in plan.advices)

    def test_dust_trades_suppressed(self):
        holdings = [_h("A", 53, 100), _h("B", 47, 100)]  # нужно ~300 ₽ сделки
        plan = rebalance_plan(
            holdings, {"A": 0.5, "B": 0.5},
            drift_band=0.01, min_trade_rub=1_000,
        )
        assert all(a.action == "HOLD" for a in plan.advices)
        assert any(a.reason == "пыль" for a in plan.advices)

    def test_missing_ticker_treated_as_zero_weight(self):
        plan = rebalance_plan(
            [_h("A", 10, 100)], {"A": 0.5, "B": 0.5}, min_trade_rub=100,
        )
        by = {a.ticker: a for a in plan.advices}
        assert by["B"].action == "BUY"
        assert abs(by["B"].current_weight) < 1e-9


class TestCash:
    def test_cash_counts_into_total_and_target(self):
        holdings = [_h("A", 10, 9_500)]           # 95 000 ₽
        plan = rebalance_plan(
            holdings, {"A": 1.0}, cash_rub=5_000,
            cash_target=0.05, drift_band=0.01, min_trade_rub=100,
        )
        by = {a.ticker: a for a in plan.advices}
        assert "_CASH" in by
        assert abs(by["_CASH"].target_weight - 0.05) < 1e-9
        assert abs(plan.total_value_rub - 100_000) < 1e-6

    def test_cash_weight_reported(self):
        plan = rebalance_plan([_h("A", 1, 100)], {"A": 1.0}, cash_rub=100)
        assert abs(plan.cash_weight - 0.5) < 1e-9


class TestEdgeCasesAndSafety:
    def test_empty_portfolio(self):
        plan = rebalance_plan([], {}, cash_rub=0)
        assert plan.advices == [] and plan.total_value_rub == 0

    def test_zero_total(self):
        assert rebalance_plan([_h("A", 3, 0.0)], {"A": 1.0}).advices == []

    def test_duplicate_tickers_merged(self):
        plan = rebalance_plan(
            [_h("A", 5, 100), _h("A", 5, 100)], {"A": 1.0},
        )
        assert len(plan.advices) == 1
        assert plan.advices[0].action == "HOLD"

    def test_advice_never_contains_orders(self):
        """Совет — это данные; исполнение не входит в API."""
        plan = rebalance_plan(
            [_h("X", 1, 100)], {"X": 0.5, "Y": 0.5},
        )
        allowed = {"BUY", "SELL", "HOLD"}
        assert all(a.action in allowed for a in plan.advices)
        assert not hasattr(plan, "execute") and not hasattr(plan, "submit")


class TestDriftReport:
    def test_sorted_by_abs_drift(self):
        rows = drift_report(
            [_h("A", 7, 100), _h("B", 3, 100)],
            {"A": 0.4, "B": 0.5, "C": 0.1},
        )
        assert rows[0][0] == "A"          # перевес +0.3 — самый большой дрейф
        assert abs(rows[0][1] - 0.3) < 1e-3
        assert rows[1][0] == "B"          # недовес −0.2
        assert rows[2][0] == "C"          # нет позиции — дрейф −0.1
