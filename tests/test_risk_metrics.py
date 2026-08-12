"""Tests for risk_metrics — Sharpe/Sortino/MDD/VaR/correlation/trailing stop."""
from token_diet.risk_metrics import (
    annualized_volatility,
    correlation_matrix,
    historical_var,
    max_drawdown,
    returns_of,
    risk_block,
    risk_report,
    sharpe_ratio,
    sortino_ratio,
    trailing_stop,
)

PRICES = [100, 102, 101, 105, 110, 108, 112, 115, 114, 118, 120, 119, 122, 125]
FLAT = [100] * 10


def test_sharpe_of_rising_series_positive():
    rets = returns_of(PRICES)
    assert sharpe_ratio(rets) > 0


def test_sortino_of_flat_series_zero():
    rets = returns_of(FLAT)
    assert sortino_ratio(rets) == 0.0


def test_max_drawdown():
    assert max_drawdown([100, 120, 110, 130]) == -10 / 120  # -0.0833
    assert max_drawdown([100, 50, 100]) == -0.5


def test_historical_var():
    rets = [-0.01, -0.02, -0.03, -0.04, 0.01, 0.02, 0.03]
    var = historical_var(rets, 0.95)
    assert var <= -0.02


def test_correlation():
    up = [i for i in range(20)]
    down = [20 - i for i in range(20)]
    mat = correlation_matrix({"A": up, "B": up, "C": down})
    assert mat["A"]["A"] == 1.0
    assert mat["A"]["B"] > 0.99
    assert mat["A"]["C"] < -0.99


def test_trailing_stop_activates():
    # entry 100, price 103 (+3%), ATR 1 → activated, stop = 103 - 2 = 101
    r = trailing_stop(100, 103, atr=1.0, atr_multiplier=2.0, activation_profit_pct=1.0)
    assert r["activated"] is True
    assert r["stop"] == 101.0


def test_trailing_stop_before_activation():
    # not yet +1%: stop stays at entry - 2*ATR = 98
    r = trailing_stop(100, 100.5, atr=1.0, atr_multiplier=2.0, activation_profit_pct=1.0)
    assert r["activated"] is False
    assert r["stop"] == 98.0


def test_risk_report_and_block():
    rep = risk_report(PRICES)
    assert rep["sharpe"] > 0
    assert "max_drawdown_pct" in rep
    block = risk_block(PRICES)
    assert block.startswith("[Risk]")
    assert "Sharpe" in block


def test_annualized_vol():
    assert annualized_volatility(returns_of(FLAT)) == 0.0
