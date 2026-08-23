"""Tests for trading_robot.py — reverse-engineered Tinkoff robot strategies."""

from datetime import datetime, timezone

from token_diet.trading_robot import (
    BacktestResult,
    IntervalSignal,
    MaCrossSignal,
    VolumeProfileResult,
    backtest,
    interval_strategy,
    ma_cross,
    market_open_now,
    percentile_corridor,
    position_plan,
    run_strategies,
    stop_loss_level,
    volume_profile,
)


class TestMaCross:
    def test_golden_cross_buy(self):
        # uptrend: prices rising steadily
        closes = list(range(1, 260))  # 1..259, monotonic up
        sig = ma_cross(closes, fast=10, slow=30)
        assert sig.signal == "BUY"
        assert sig.spread_pct > 0

    def test_death_cross_sell(self):
        # downtrend: prices falling
        closes = list(range(259, 0, -1))
        sig = ma_cross(closes, fast=10, slow=30)
        assert sig.signal == "SELL"
        assert sig.spread_pct < 0

    def test_not_enough_data(self):
        sig = ma_cross([1, 2, 3], fast=10, slow=30)
        assert sig.signal == "HOLD"
        assert "need" in sig.reason

    def test_cross_trigger_positive(self):
        # force a fresh golden cross: flat then jump
        closes = [100.0] * 220 + [100.0, 101.0, 103.0, 106.0, 110.0]
        sig = ma_cross(closes, fast=10, slow=20)
        assert sig.signal == "BUY"


class TestVolumeProfile:
    def test_poc_detected(self):
        highs = [100 + i * 0.5 for i in range(20)]
        lows = [99 + i * 0.5 for i in range(20)]
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
        volumes = [10.0] * 15 + [1000.0] * 5  # heavy volume at top
        vp = volume_profile(highs, lows, closes, volumes)
        assert vp is not None
        assert vp.poc > 0
        assert vp.total_volume > 0

    def test_insight_nonempty(self):
        highs = list(range(100, 120))
        lows = list(range(95, 115))
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
        vp = volume_profile(highs, lows, closes, [5.0] * 20)
        assert vp is not None
        assert len(vp.insight) > 5

    def test_not_enough_data(self):
        assert volume_profile([], [], [], []) is None

    def test_flat_range_returns_none(self):
        assert volume_profile([5, 5], [5, 5], [5, 5], [1, 1]) is None


class TestPercentileCorridor:
    def test_corridor_is_middle_80pct(self):
        # 1..100 → 10th pct ≈ 9.9, 90th pct ≈ 90.1
        closes = list(range(1, 101))
        corr = percentile_corridor(closes, lookback=100, interval_size=0.8)
        assert corr is not None
        assert 8 < corr.bottom < 11
        assert 89 < corr.top < 92
        assert corr.bottom < corr.top

    def test_outlier_does_not_shift_corridor(self):
        # one huge spike: min/max would jump, percentiles barely move
        closes = [100.0] * 50 + [1000.0]  # spike at the end
        corr = percentile_corridor(closes, lookback=51, interval_size=0.8)
        assert corr is not None
        assert corr.top < 200  # 90th percentile stays ~100

    def test_no_data(self):
        assert percentile_corridor([]) is None

    def test_iterable(self):
        corr = percentile_corridor([10, 20, 30, 40, 50], lookback=5)
        bottom, top = corr
        assert bottom < top

    def test_interval_size_clamped(self):
        # invalid interval_size must not invert the corridor
        corr = percentile_corridor([10, 20, 30, 40, 50], lookback=5,
                                   interval_size=0.0)
        assert corr is not None
        assert corr.bottom <= corr.top


class TestStopLoss:
    def test_formula(self):
        assert stop_loss_level(100.0, 0.01) == 99.0
        assert stop_loss_level(1330.0, 0.01) == 1316.7
        assert stop_loss_level(100.0, 0.05) == 95.0

    def test_zero_percent_no_change(self):
        assert stop_loss_level(100.0, 0.0) == 100.0


class TestPositionPlan:
    def test_stop_loss_first(self):
        plan = position_plan(
            quantity=10, quantity_limit=10,
            avg_price=100.0, last_price=98.0, stop_loss_percent=0.01,
        )
        assert plan["action"] == "STOP_LOSS"
        assert plan["stop_loss_level"] == 99.0

    def test_above_limit_sells_all(self):
        plan = position_plan(quantity=15, quantity_limit=10, avg_price=100.0)
        assert plan["action"] == "SELL_ALL"

    def test_below_limit_tops_up(self):
        plan = position_plan(quantity=4, quantity_limit=10)
        assert plan["action"] == "BUY"
        assert plan["to_buy"] == 6

    def test_hold_within_limits_and_no_stop(self):
        plan = position_plan(quantity=5, quantity_limit=10,
                             avg_price=100.0, last_price=101.0)
        assert plan["action"] == "BUY"  # still topping up

    def test_limit_zero_never_trades(self):
        plan = position_plan(quantity=3, quantity_limit=0)
        assert plan["action"] == "HOLD"


class TestMarketOpen:
    def test_weekend_closed(self):
        sat = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)  # Saturday
        assert market_open_now(sat) is False
        sun = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)  # Sunday
        assert market_open_now(sun) is False

    def test_weekday_within_hours(self):
        # Monday 2026-08-10, 08:00 UTC = 11:00 MSK → open
        mon = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
        assert market_open_now(mon) is True

    def test_before_open(self):
        # Monday 06:00 UTC = 09:00 MSK → closed
        mon = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)
        assert market_open_now(mon) is False


class TestIntervalStrategy:
    def test_buy_at_bottom(self):
        closes = [100, 90, 80, 81, 82, 83]
        sig = interval_strategy(closes, lookback=6)
        assert sig.signal == "BUY"
        assert sig.percentile is True

    def test_sell_at_top(self):
        closes = [80, 90, 100, 99, 98, 97]
        sig = interval_strategy(closes, lookback=6)
        assert sig.signal == "SELL"

    def test_hold_mid(self):
        closes = [90, 92, 95, 93, 94, 93]
        sig = interval_strategy(closes, lookback=6)
        assert sig.signal == "HOLD"

    def test_no_data(self):
        sig = interval_strategy([])
        assert sig.signal == "HOLD"

    def test_minmax_mode_still_works(self):
        closes = [100, 90, 80, 81, 82, 83]
        sig = interval_strategy(closes, lookback=6, percentile_mode=False)
        assert sig.signal == "BUY"
        assert sig.percentile is False


class TestBacktest:
    def test_uptrend_profitable(self):
        # flat, then rise → golden cross (enter) → continue rising → profitable
        closes = [100.0] * 25 + [100 + i * 0.5 for i in range(30)] + [110 - i * 0.3 for i in range(30)]
        bt = backtest(closes, fast=3, slow=10)
        assert bt.total_pnl_pct > 0

    def test_backtest_structure_valid(self):
        # non-trivial: flat then rise then crash
        closes = [100.0] * 20 + [100 + i * 0.3 for i in range(20)] + [106 - i * 2.0 for i in range(20)]
        bt = backtest(closes, fast=3, slow=10)
        assert bt.final_equity > 0
        assert 0 <= bt.win_rate <= 1
        assert bt.verdict in ("PROFITABLE", "UNPROFITABLE", "FLAT", "no trades")

    def test_not_enough_data(self):
        bt = backtest([1, 2, 3])
        assert bt.verdict == "not enough history"

    def test_result_structure(self):
        closes = [100 + i * 0.5 for i in range(80)]
        bt = backtest(closes, fast=3, slow=10)
        assert bt.final_equity > 0
        assert 0 <= bt.win_rate <= 1


class TestRunStrategies:
    def test_combined_dict(self):
        closes = [100 + i for i in range(60)]
        highs = [c + 2 for c in closes]
        lows = [c - 2 for c in closes]
        vols = [10.0] * 60
        r = run_strategies(closes, highs, lows, vols)
        assert "ma_cross" in r
        assert "volume_profile" in r
        assert "interval" in r
        assert "corridor" in r
        assert r["combined_lean"] in ("BUY", "SELL", "HOLD")

    def test_uptrend_strategies(self):
        # realistic uptrend
        closes = [50.0] * 15 + [50 + i * 0.3 for i in range(55)] + [65, 64, 63, 66, 67, 68]
        r = run_strategies(closes)
        # MA cross should be BUY on uptrend; interval may be anything
        assert r["ma_cross"]["signal"] == "BUY"
        assert r["combined_lean"] in ("BUY", "SELL", "HOLD")
