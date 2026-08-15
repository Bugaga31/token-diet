"""Тесты market_guard: контракт _evaluate_status (без сети)."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from token_diet.market_guard import GuardConfig, _evaluate_status


def _snap(price, gold=None, news=None):
    return {
        "price": price,
        "gold": gold,
        "news": news or [],
        "orderbook": {"balance_pct": 0.0},
        "time": "test",
    }


def test_guard_config_defaults():
    cfg = GuardConfig()
    assert cfg.ticker == "PLZL"
    assert cfg.target > cfg.entry  # цель выше входа
    assert cfg.stop < cfg.entry  # стоп ниже входа


def test_no_data():
    cfg = GuardConfig()
    assert _evaluate_status(_snap(price=None), cfg) == "NO_DATA"


def test_price_below_stop():
    cfg = GuardConfig()
    assert _evaluate_status(_snap(price=cfg.stop - 5, gold=cfg.gold_floor + 50), cfg) == "STOP_HIT"


def test_gold_below_floor():
    cfg = GuardConfig()
    assert _evaluate_status(_snap(price=cfg.entry + 5, gold=cfg.gold_floor - 100), cfg) == "GOLD_BREACH"


def test_below_red_line():
    cfg = GuardConfig()
    assert _evaluate_status(_snap(price=cfg.red_line - 1, gold=cfg.gold_floor + 50), cfg) == "BELOW_RED"


def test_target_hit():
    cfg = GuardConfig()
    assert _evaluate_status(_snap(price=cfg.target + 5, gold=cfg.gold_floor + 50), cfg) == "TARGET_HIT"


def test_green_at_entry():
    cfg = GuardConfig()
    assert _evaluate_status(_snap(price=cfg.entry + 5, gold=cfg.gold_floor + 50), cfg) == "GREEN"


def test_hold_below_entry():
    cfg = GuardConfig()
    status = _evaluate_status(
        _snap(price=cfg.red_line + 1, gold=cfg.gold_floor + 50), cfg
    )
    assert status in ("HOLD", "GREEN")


def test_priority_stop_over_gold():
    """Стоп важнее золота: цена ниже стопа = STOP_HIT даже при плохом золоте."""
    cfg = GuardConfig()
    assert _evaluate_status(
        _snap(price=cfg.stop - 1, gold=cfg.gold_floor - 200), cfg
    ) == "STOP_HIT"
