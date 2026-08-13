"""risk_metrics — portfolio risk mathematics (OpenBB-style), pure Python.

Reverse-engineered from OpenBB's quantitative extension (openbb-
quantitative) and Freqtrade's risk/exit logic:

    OpenBB: Sharpe, Sortino, max drawdown, historical VaR, annualized
            volatility — the numbers every fund manager cites.
    Freqtrade: trailing stop — once a trade is in profit, the stop
            ratchets UP behind the price instead of sitting still.

Why this matters for token-diet:
    Instead of dumping a price history into the prompt and asking the
    model to "feel" the risk, we hand it 4-5 crisp numbers: Sharpe 1.2,
    max drawdown -18%, VaR95 -2.3%. That's analysis in ~80 tokens —
    and it is *exact*, not a vibe.

All functions are deterministic, stdlib only.
"""

from __future__ import annotations

from typing import Any


def returns_of(prices: list[float]) -> list[float]:
    """Simple period-over-period returns."""
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]


def sharpe_ratio(returns: list[float], rfr: float = 0.0,
                 periods_per_year: int = 252) -> float:
    """Risk-adjusted return: excess mean return per unit of volatility."""
    if not returns:
        return 0.0
    per_period_rfr = rfr / periods_per_year
    excess = [r - per_period_rfr for r in returns]
    mean_ex = sum(excess) / len(excess)
    mean_ret = sum(returns) / len(returns)
    var = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    std = var ** 0.5
    if std == 0:
        return 0.0
    return (mean_ex / std) * (periods_per_year ** 0.5)


def sortino_ratio(returns: list[float], rfr: float = 0.0,
                  periods_per_year: int = 252) -> float:
    """Sharpe, but penalizes only downside volatility."""
    if not returns:
        return 0.0
    per_period_rfr = rfr / periods_per_year
    excess = [r - per_period_rfr for r in returns]
    mean_ex = sum(excess) / len(excess)
    mean_ret = sum(returns) / len(returns)
    downside = sum(min(0.0, r - mean_ret) ** 2 for r in returns) / len(returns)
    ddev = downside ** 0.5
    if ddev == 0:
        return 0.0
    return (mean_ex / ddev) * (periods_per_year ** 0.5)


def max_drawdown(prices: list[float]) -> float:
    """Largest peak-to-trough decline, as a negative decimal (-0.18 = -18%)."""
    if not prices:
        return 0.0
    peak = prices[0]
    mdd = 0.0
    for p in prices:
        if p > peak:
            peak = p
        dd = (p - peak) / peak if peak else 0.0
        if dd < mdd:
            mdd = dd
    return mdd


def historical_var(returns: list[float], confidence: float = 0.95) -> float:
    """Historical Value-at-Risk: worst return at the given confidence.

    VaR95 = -2.3% means: in 95% of periods the loss won't exceed 2.3%.
    """
    if not returns:
        return 0.0
    sorted_r = sorted(returns)
    idx = int((1.0 - confidence) * len(sorted_r))
    idx = min(max(idx, 0), len(sorted_r) - 1)
    return sorted_r[idx]


def annualized_volatility(returns: list[float], periods_per_year: int = 252) -> float:
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    return (var ** 0.5) * (periods_per_year ** 0.5)


def correlation_matrix(series: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """Pearson correlation between all pairs of price series.

    series: {"SBER": [...prices...], "GAZP": [...], ...} (equal lengths).
    Returns {"SBER": {"SBER": 1.0, "GAZP": 0.62, ...}}.
    """
    names = list(series.keys())
    n = min(len(v) for v in series.values()) if series else 0
    if n < 2:
        return {}
    mat: dict[str, dict[str, float]] = {a: {} for a in names}
    for a in names:
        for b in names:
            if a == b:
                mat[a][b] = 1.0
                continue
            x, y = series[a][:n], series[b][:n]
            mx, my = sum(x) / n, sum(y) / n
            cov = sum((x[i] - mx) * (y[i] - my) for i in range(n)) / n
            sx = (sum((v - mx) ** 2 for v in x) / n) ** 0.5
            sy = (sum((v - my) ** 2 for v in y) / n) ** 0.5
            c = cov / (sx * sy) if sx * sy else 0.0
            mat[a][b] = round(c, 3)
    return mat


def trailing_stop(
    entry_price: float, current_price: float,
    atr: float, atr_multiplier: float = 2.0,
    activation_profit_pct: float = 1.0,
) -> dict[str, Any]:
    """Freqtrade-style trailing stop.

    Until the trade is up `activation_profit_pct`%, the stop sits
    `atr_multiplier`×ATR below entry (a normal stop). Once activated,
    the stop ratchets UP behind the price — never lower than the
    previous stop.
    """
    base_stop = entry_price - atr_multiplier * atr
    profit_pct = 100.0 * (current_price - entry_price) / entry_price if entry_price else 0.0
    activated = profit_pct >= activation_profit_pct
    if activated:
        stop = max(base_stop, current_price - atr_multiplier * atr)
    else:
        stop = base_stop
    return {
        "entry": entry_price,
        "current": current_price,
        "profit_pct": round(profit_pct, 2),
        "activated": activated,
        "stop": round(stop, 2),
        "distance_pct": round(100 * (current_price - stop) / current_price, 2) if current_price else 0.0,
    }


def position_size(
    capital: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    lot_size: int = 1,
) -> dict[str, Any]:
    """Риск-ориентированный размер позиции (сколько акций/лотов купить).

    Правило: если сработает стоп, потеряем РОВНО risk_pct % капитала —
    не больше. Это защита от главной ошибки трейдера: ставить слишком много.

    УРОК (13.08): лот ≠ акция. Возвращает и акции, и лоты (округляя вниз),
    чтобы нельзя было перепутать.

    Формула:  акции = (capital × risk_pct) / |entry − stop|
    """
    if capital <= 0 or risk_pct <= 0 or entry_price <= 0 or stop_price <= 0:
        return {"error": "некорректные входные параметры"}
    per_share_risk = abs(entry_price - stop_price)
    if per_share_risk == 0:
        return {"error": "стоп равен входу — риск не определён"}

    risk_amount = capital * risk_pct / 100.0
    shares = risk_amount / per_share_risk
    lot_size = max(1, int(lot_size))
    lots = int(shares // lot_size)
    actual_shares = lots * lot_size
    actual_risk = actual_shares * per_share_risk

    return {
        "risk_amount": round(risk_amount, 2),
        "per_share_risk": round(per_share_risk, 2),
        "shares_raw": round(shares, 2),
        "lot_size": lot_size,
        "lots": lots,
        "shares": actual_shares,
        "position_cost": round(actual_shares * entry_price, 2),
        "actual_risk_pct": round(100 * actual_risk / capital, 2) if capital else 0.0,
        "stop": round(stop_price, 2),
    }


def trade_plan(
    capital: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    target_price: float,
    lot_size: int = 1,
) -> dict[str, Any]:
    """Полный план сделки: размер + риск + профиль/риск (R:R)."""
    s = position_size(capital, risk_pct, entry_price, stop_price, lot_size)
    if "error" in s:
        return s
    risk = abs(entry_price - stop_price)
    reward = abs(target_price - entry_price)
    s["target"] = round(target_price, 2)
    s["reward"] = round(reward, 2)
    s["rr_ratio"] = round(reward / risk, 2) if risk else 0.0
    s["potential_profit"] = round(s["shares"] * reward, 2)
    return s


def risk_report(prices: list[float], rfr: float = 0.0,
                periods_per_year: int = 252) -> dict[str, Any]:
    """One-screen risk picture for a price series."""
    rets = returns_of(prices)
    if not rets:
        return {"bars": len(prices), "insufficient": True}
    return {
        "bars": len(prices),
        "sharpe": round(sharpe_ratio(rets, rfr, periods_per_year), 2),
        "sortino": round(sortino_ratio(rets, rfr, periods_per_year), 2),
        "max_drawdown_pct": round(100 * max_drawdown(prices), 1),
        "var95_pct": round(100 * historical_var(rets, 0.95), 2),
        "annual_vol_pct": round(100 * annualized_volatility(rets, periods_per_year), 1),
    }


def risk_block(prices: list[float]) -> str:
    """Compact '[Risk]' block for an LLM prompt."""
    r = risk_report(prices)
    if r.get("insufficient"):
        return "[Risk: insufficient data]"
    lines = ["[Risk]"]
    lines.append(f"  Sharpe {r['sharpe']} | Sortino {r['sortino']}")
    lines.append(f"  MaxDrawdown {r['max_drawdown_pct']}% | VaR95 {r['var95_pct']}%")
    lines.append(f"  AnnVol {r['annual_vol_pct']}%")
    lines.append("[/Risk]")
    return "\n".join(lines)


__all__ = [
    "annualized_volatility",
    "correlation_matrix",
    "historical_var",
    "max_drawdown",
    "returns_of",
    "risk_block",
    "risk_report",
    "sharpe_ratio",
    "sortino_ratio",
    "trailing_stop",
]
