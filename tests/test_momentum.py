import sys

sys.path.insert(0, ".")

from token_diet.momentum import (
    detect_breakout,
    momentum_lean,
    rate_of_change,
)


def test_rate_of_change_positive():
    closes = [100 + i for i in range(15)]  # рост
    roc = rate_of_change(closes, period=10)
    assert roc is not None and roc > 0


def test_rate_of_change_negative():
    closes = [200 - i for i in range(15)]
    roc = rate_of_change(closes, period=10)
    assert roc is not None and roc < 0


def test_rate_of_change_short():
    assert rate_of_change([1, 2, 3], period=10) is None


def test_breakout_up():
    # 20 дней в диапазоне 100-105, потом прорыв вверх на 110
    closes = [100 + (i % 5) for i in range(20)] + [110]
    brk = detect_breakout(closes, lookback=20)
    assert brk.direction == "up"
    assert brk.is_breakout


def test_breakout_down():
    closes = [110 - (i % 5) for i in range(20)] + [95]
    brk = detect_breakout(closes, lookback=20)
    assert brk.direction == "down"


def test_breakout_none_inside_range():
    closes = [100 + (i % 3) for i in range(22)]
    brk = detect_breakout(closes, lookback=20)
    assert brk.direction == "none"
    assert not brk.is_breakout


def test_breakout_volume_confirmation():
    # прорыв вверх с объёмным всплеском — strength выше
    closes = [100 + (i % 5) for i in range(20)] + [110]
    volumes = [100] * 20 + [500]  # последний день объём ×5
    brk = detect_breakout(closes, volumes=volumes, lookback=20)
    assert brk.volume_ratio > 1.0
    assert brk.strength >= 0.4


def test_momentum_lean_bounds():
    closes = [100 + i * 0.5 for i in range(40)]
    lean = momentum_lean(closes)
    assert -1.0 <= lean <= 1.0


def test_momentum_lean_upbeat():
    # рост + прорыв вверх → положительный lean
    closes = [100 + (i % 4) for i in range(20)] + [112]
    lean = momentum_lean(closes)
    assert lean > 0
