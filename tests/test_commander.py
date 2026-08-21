import sys

sys.path.insert(0, ".")

from token_diet.commander import decide


def _sig(ticker, lean, price=100.0):
    return {"ticker": ticker, "lean": lean, "price": price, "lot_size": 10}


def test_buy_on_strong_signal():
    d = decide([_sig("GMKN", 0.3)], capital=20000, risk_pct=2.0)
    assert d.action == "buy"
    assert d.ticker == "GMKN"
    assert d.plan["lots"] > 0


def test_hold_on_weak_signal():
    d = decide([_sig("GMKN", 0.05), _sig("SBER", 0.1)], capital=20000)
    assert d.action == "hold"


def test_sell_on_strong_negative():
    d = decide([_sig("PLZL", -0.3)], capital=20000)
    assert d.action == "sell"


def test_picks_strongest():
    d = decide([_sig("GAZP", 0.05), _sig("GMKN", 0.3), _sig("SBER", 0.2)], capital=20000)
    assert d.action == "buy"
    assert d.ticker == "GMKN"


def test_hold_when_no_data():
    d = decide([], capital=20000)
    assert d.action == "hold"


def test_plan_has_risk_and_rr():
    d = decide([_sig("GMKN", 0.3)], capital=20000, risk_pct=2.0)
    assert "stop" in d.plan
    assert "target" in d.plan
    assert d.plan["rr_ratio"] >= 1.0
    assert d.plan["actual_risk_pct"] <= 2.5


def test_render():
    d = decide([_sig("GMKN", 0.3)], capital=20000)
    text = d.render()
    assert "GMKN" in text and "BUY" in text
