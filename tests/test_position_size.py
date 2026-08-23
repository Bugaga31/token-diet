import sys

sys.path.insert(0, ".")

from token_diet.risk_metrics import position_size, trade_plan


def test_position_size_risk_capped():
    # капитал 20к, риск 2%, вход 100, стоп 95 → потеряем ровно 400 руб при стопе
    s = position_size(20000, 2, 100, 95, lot_size=1)
    assert s["risk_amount"] == 400.0
    # 400 / 5 = 80 акций
    assert s["shares"] == 80
    assert s["position_cost"] == 8000.0


def test_position_size_respects_lot():
    # лот 10: 80 акций → 8 лотов → 80 акций
    s = position_size(20000, 2, 100, 95, lot_size=10)
    assert s["lots"] == 8
    assert s["shares"] == 80


def test_position_size_rounds_down_lots():
    # 85 акций сырых → при лоте 10 → 8 лотов = 80 акций
    s = position_size(20000, 2.1, 100, 95, lot_size=10)
    assert s["shares"] <= 85
    assert s["shares"] % 10 == 0


def test_position_size_invalid():
    assert "error" in position_size(0, 2, 100, 95)
    assert "error" in position_size(10000, 2, 100, 100)


def test_position_size_higher_risk_more_shares():
    low = position_size(20000, 1, 100, 95)
    high = position_size(20000, 3, 100, 95)
    assert high["shares"] > low["shares"]


def test_trade_plan_rr():
    # вход 100, стоп 95 (риск 5), цель 110 (награда 10) → R:R = 2.0
    p = trade_plan(20000, 2, 100, 95, 110)
    assert p["rr_ratio"] == 2.0
    assert p["potential_profit"] == p["shares"] * 10


def test_trade_plan_invalid():
    assert "error" in trade_plan(0, 2, 100, 95, 110)
