"""Tests for market_guard — snapshot, stress tests, alerts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from token_diet.market_guard import (
    GuardConfig,
    _evaluate_status,
    stress_test,
    stress_block,
)


def test_status_logic():
    cfg = GuardConfig()
    assert _evaluate_status({"price": 1280.0, "gold": 4400}, cfg) == "STOP_HIT"
    assert _evaluate_status({"price": 1295.0, "gold": 4400}, cfg) == "BELOW_RED"
    assert _evaluate_status({"price": 1308.0, "gold": 4400}, cfg) == "HOLD"
    assert _evaluate_status({"price": 1340.0, "gold": 4400}, cfg) == "GREEN"
    assert _evaluate_status({"price": 1400.0, "gold": 4400}, cfg) == "TARGET_HIT"
    assert _evaluate_status({"price": 1300.0, "gold": 4300}, cfg) == "GOLD_BREACH"


def test_gold_floor():
    cfg = GuardConfig(gold_floor=4350)
    assert _evaluate_status({"price": 1300.0, "gold": 4349.0}, cfg) == "GOLD_BREACH"
    assert _evaluate_status({"price": 1300.0, "gold": 4350.0}, cfg) == "BELOW_RED"


def test_stress_scenarios_cover_levels():
    cfg = GuardConfig()
    rows = stress_test(cfg)
    prices = {r["price"] for r in rows}
    assert cfg.stop in prices
    assert cfg.red_line in prices
    assert cfg.entry in prices
    assert cfg.target in prices
    # сортировка не ломается и все строки имеют P&L
    for r in rows:
        assert "pnl_pct" in r and "pnl_rub" in r


def test_stress_block_format():
    cfg = GuardConfig()
    block = stress_block(cfg)
    assert "[Stress — PLZL]" in block
    assert "Стоп" in block and "Цель 1400" in block
    lines = block.splitlines()
    assert len(lines) >= 6  # ценовые сценарии + золото


def test_stress_rub_sane():
    # вход 1339.6, при цене 1340 P&L ≈ +0.03% ≈ +6 руб на 20к
    cfg = GuardConfig(entry=1339.6, portfolio_size=20000)
    rows = stress_test(cfg)
    for r in rows:
        if abs(r["price"] - 1340.0) < 1.0:
            assert abs(r["pnl_rub"]) < 50
