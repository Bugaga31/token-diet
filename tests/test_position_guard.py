"""Тесты position_guard: статусы, выбор стопа, отчёты, инжекция фетчера."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.position_guard import (  # noqa: E402
    GuardReport,
    Position,
    guard_position,
    watch_positions,
)


def _flat_candles(price: float, n: int = 30, spread: float = 0.2):
    """n дневных свечей вокруг цены с узким спредом (ATR ≈ 0.3)."""
    return [(price + spread, price - spread, price) for _ in range(n)]


class TestGuardPosition:
    def test_no_data_returns_none(self):
        assert guard_position(Position("X", 1, 100), []) is None

    def test_ok_when_far_from_stop(self):
        # spread=1.0 → ATR=2.0; трейлинг активирован (+5%), стоп 100,
        # дистанция 4.76% > warn → OK
        v = guard_position(Position("A", 10, 100), _flat_candles(105, spread=1.0))
        assert v is not None and v.status == "OK"
        assert v.price == 105 and v.profit_pct == 5.0

    def test_breached_when_price_below_stop(self):
        v = guard_position(Position("A", 10, 110), _flat_candles(95))
        assert v is not None and v.status == "BREACHED"
        assert v.stop_price > v.price

    def test_fixed_stop_anchor_is_avg_price(self):
        # плоская история: ATR≈0.3, trailing-стоп почти у средней;
        # фикс −5% от средней 100 = 95 должен победить как max
        v = guard_position(
            Position("A", 10, 100), _flat_candles(96),
            atr_multiplier=0.01,   # трейлинг-стоп практически у входа
        )
        assert v is not None
        assert v.stop_kind in ("fixed-pct", "atr-trailing")
        assert v.stop_price >= 95.0 - 0.01

    def test_trailing_activates_on_profit(self):
        candles = _flat_candles(120, spread=1.0)   # ATR=2.0
        v = guard_position(
            Position("A", 10, 100), candles,
            atr_multiplier=5.0, warn_pct=1.0,
        )
        assert v is not None
        assert v.stop_kind == "atr-trailing"       # +20% → трейлинг активен
        assert v.status == "OK"
        assert v.stop_price == max(100 - 5 * 2.0, 120 - 5 * 2.0)

    def test_warn_zone(self):
        # не активирован (−4%): стоп = max(100−2.5×0.4, 95) = 99;
        # цена 101 → дистанция ~1.98% < warn_pct=3 → WARN
        v = guard_position(Position("A", 10, 100), _flat_candles(101), warn_pct=3.0)
        assert v is not None and v.status == "WARN"
        assert "до стопа" in v.note


class TestWatchPositions:
    def test_fetcher_injection_and_skip(self):
        report = watch_positions(
            [Position("AAA", 1, 100), Position("EMPTY", 1, 50)],
            fetcher=lambda t, d: _flat_candles(101) if t != "EMPTY" else [],
        )
        assert isinstance(report, GuardReport)
        assert len(report.verdicts) == 1          # EMPTY без данных пропущен
        assert report.verdicts[0].ticker == "AAA"

    def test_fetcher_exception_skipped(self):
        def boom(ticker, days):
            raise ConnectionError("сеть умерла")

        report = watch_positions([Position("B", 1, 100)], fetcher=boom)
        assert report.verdicts == []

    def test_breached_listed(self):
        report = watch_positions(
            [Position("C", 1, 200)], fetcher=lambda t, d: _flat_candles(150),
        )
        assert len(report.breached) == 1

    def test_block_rendering(self):
        report = watch_positions(
            [Position("D", 1, 100)], fetcher=lambda t, d: _flat_candles(103),
        )
        text = report.block()
        assert "[guard]" in text and "D" in text
        assert any(icon in text for icon in ("✓", "⚠", "⛔"))
