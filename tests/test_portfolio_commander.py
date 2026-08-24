"""Tests for portfolio_commander.py — honest portfolio management with safety gate."""

from token_diet.portfolio_commander import (
    leverage_analysis,
    plan_position,
)


class TestPlanPosition:
    def test_stop_and_take_levels(self):
        plan = plan_position(
            "PLZL", quantity=14, avg_price=1339.60,
            current_price=1338.00, stop_pct=0.015, take_pct=0.045,
        )
        assert plan.ticker == "PLZL"
        assert plan.stop_loss is not None
        assert plan.take_profit is not None
        # стоп ~ -1.5% от средней, тейк ~ +4.5%
        assert plan.stop_loss <= 1339.60 * 0.985 + 1
        assert abs(plan.take_profit - round(1339.60 * 1.045, 2)) < 1

    def test_stop_hit_gives_STOP(self):
        plan = plan_position(
            "X", quantity=10, avg_price=100.0, current_price=90.0,
            stop_pct=0.015,
        )
        assert plan.action == "STOP"

    def test_take_profit_gives_SELL(self):
        plan = plan_position(
            "X", quantity=10, avg_price=100.0, current_price=110.0,
            take_pct=0.045,
        )
        assert plan.action == "SELL"
        assert plan.take_profit <= 110.0

    def test_hold_in_middle(self):
        plan = plan_position(
            "X", quantity=10, avg_price=100.0, current_price=102.0,
            stop_pct=0.015, take_pct=0.045,
        )
        assert plan.action == "HOLD"

    def test_risk_rub_is_money_at_stake(self):
        plan = plan_position(
            "X", quantity=14, avg_price=100.0, current_price=102.0,
            stop_pct=0.02,
        )
        # риск = (цена - стоп) * кол-во
        expected = (102.0 - 98.0) * 14
        assert abs(plan.risk_rub - expected) < 1.0

    def test_closes_shift_stop_to_percentile(self):
        # цены шумят сильно вниз → 10-й перцентиль ниже, стоп = max()
        closes = [100.0] * 30 + [95.0, 96.0, 94.0, 97.0, 98.0]
        plan = plan_position(
            "X", quantity=10, avg_price=100.0, current_price=99.0,
            closes=closes, stop_pct=0.015,
        )
        assert plan.stop_loss is not None
        assert plan.buy_zone_bottom is not None


class TestLeverageAnalysis:
    def test_margin_disabled_is_honest(self):
        r = leverage_analysis(20000, 19000, margin_enabled=False)
        assert r["margin_enabled"] is False
        assert "ОТКЛЮЧЕНА" in r["verdict"]
        assert r["max_leverage"] == 1.0

    def test_margin_enabled_shows_math(self):
        r = leverage_analysis(20000, 19000, margin_enabled=True)
        assert r["margin_enabled"] is True
        assert "2.0x" in r or "leverage_2.0x" in r
        assert r["concentration_pct"] > 90

    def test_zero_total_safe(self):
        r = leverage_analysis(0, 0, margin_enabled=True)
        assert "error" in r


class TestCommanderSafety:
    def test_orders_are_dry_run_by_default(self, monkeypatch):
        from token_diet.portfolio_commander import PortfolioCommander
        pc = PortfolioCommander(token="")

        # имитируем портфель с действием BUY — самый опасный кейс
        fake_plan = {
            "positions": [{
                "ticker": "PLZL", "quantity": 14, "avg_price": 1339.60,
                "current_price": 1338.00, "profit_pct": -0.12,
                "stop_loss": 1319.51, "take_profit": 1399.88,
                "buy_zone_bottom": 1292.0, "buy_zone_top": 1308.0,
                "action": "BUY", "risk_rub": 250.0,
                "reason": "зона докупки",
            }],
            "count": 1,
        }
        monkeypatch.setattr(pc, "plan", lambda: fake_plan)

        r = pc.orders()
        assert r.get("dry_run") is True
        assert "orders" in r and len(r["orders"]) == 1
        for o in r["orders"]:
            assert o.get("dry_run") is True  # никогда не исполняется
        assert "НЕ будет отправлен" in r["note"]

    def test_orders_honest_no_execute_method(self):
        from token_diet.portfolio_commander import PortfolioCommander
        # в классе НЕТ метода исполнения — подтверждение честности
        assert not hasattr(PortfolioCommander, "execute")
