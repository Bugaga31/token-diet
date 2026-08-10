"""Tests for trading_robot.py — reverse-engineered Tinkoff robot strategies."""

from token_diet.trading_robot import (
    BacktestResult,
    IntervalSignal,
    MaCrossSignal,
    VolumeProfileResult,
    backtest,
    interval_strategy,
    ma_cross,
    run_strategies,
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


class TestIntervalStrategy:
    def test_buy_at_bottom(self):
        closes = [100, 90, 80, 81, 82, 83]
        sig = interval_strategy(closes, lookback=6)
        assert sig.signal == "BUY"

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
        assert r["combined_lean"] in ("BUY", "SELL", "HOLD")

    def test_uptrend_strategies(self):
        # realistic uptrend
        closes = [50.0] * 15 + [50 + i * 0.3 for i in range(55)] + [65, 64, 63, 66, 67, 68]
        r = run_strategies(closes)
        # MA cross should be BUY on uptrend; interval may be anything
        assert r["ma_cross"]["signal"] == "BUY"
        assert r["combined_lean"] in ("BUY", "SELL", "HOLD")
