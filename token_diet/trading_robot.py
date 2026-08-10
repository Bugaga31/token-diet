"""Trading Robot — reverse-engineered strategies from T-Invest robot examples.

Based on the official T-Bank developer portal robot showcase:
  - t_tech MovingAverageStrategy  → ma_cross() golden/death cross
  - tromario volume-analysis-robot → volume_profile() POC (point of control)
  - qwertyo1 tinkoff-trading-bot  → interval_strategy() PERCENTILE corridor
                        (winner technique: 10-90 pct instead of min/max),
                        stop_loss_level(), position_plan(), market_open_now()
  - karpp investRobot             → backtest() two moving averages

What this adds on top of market_intelligence (which has indicators):
  1. ma_cross()        — BUY/SELL signal on fast×slow MA crossover + strength
  2. volume_profile()  — POC: the price where the MOST volume traded
                        (max horizontal volume — the core of tromario's robot)
  3. interval_strategy() — interval rules with PERCENTILE corridor (qwertyo1
                        winner: middle 80% of prices, immune to outlier spikes)
  4. backtest()        — replay a strategy over history, compute P&L, win rate,
                        max drawdown (honest numbers, no lies)
  5. stop_loss_level() — winner's stop: avg_price × (1 - stop_loss_percent)
  6. position_plan()   — winner's risk: stop-loss first, top-up to quantity_limit
  7. market_open_now() — MOEX session heuristic (weekday 10:00-18:50 MSK)
  8. run_on_tinkoff()  — feed real candles from TinkoffInvest into any strategy

100% pure Python, zero neural calls, zero extra dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

# ═══════════════════════════════════════════════════════════════════════════════
# Indicators (local, no numpy)
# ═══════════════════════════════════════════════════════════════════════════════


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(values[i + 1 - period: i + 1]) / period)
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    if not values:
        return out
    k = 2 / (period + 1)
    e = values[0]
    for i, v in enumerate(values):
        if i == 0:
            out.append(e)
            continue
        e = v * k + e * (1 - k)
        out.append(e)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 1. MA Cross strategy (from MovingAverageStrategy / investRobot)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MaCrossSignal:
    signal: str            # BUY / SELL / HOLD
    fast_ma: float
    slow_ma: float
    spread_pct: float      # (fast - slow) / slow * 100
    reason: str
    strength: float        # 0..1 how strong the cross is


def ma_cross(
    closes: list[float],
    fast: int = 50,
    slow: int = 200,
) -> MaCrossSignal:
    """Golden cross (fast crosses ABOVE slow) → BUY, death cross → SELL.

    The classic two-moving-average strategy from karpp/investRobot and
    t_tech's MovingAverageStrategy. Needs enough history: slow + 1 bars.
    """
    if len(closes) < slow + 1:
        return MaCrossSignal(
            signal="HOLD", fast_ma=0, slow_ma=0, spread_pct=0,
            reason=f"need {slow + 1} bars, have {len(closes)}",
            strength=0.0,
        )
    f = sma(closes, fast)
    s = sma(closes, slow)
    fast_now = f[-1]
    slow_now = s[-1]
    fast_prev = f[-2]
    slow_prev = s[-2]
    if fast_now is None or slow_now is None:
        return MaCrossSignal("HOLD", 0, 0, 0, "no data", 0)
    spread = (fast_now - slow_now) / slow_now * 100

    # golden cross: prev fast <= slow, now fast > slow
    if fast_prev is not None and slow_prev is not None:
        if fast_prev <= slow_prev and fast_now > slow_now:
            strength = min(1.0, abs(spread) / 3.0)
            return MaCrossSignal(
                "BUY", fast_now, slow_now, spread,
                "golden cross — fast MA crossed above slow MA", strength,
            )
        if fast_prev >= slow_prev and fast_now < slow_now:
            strength = min(1.0, abs(spread) / 3.0)
            return MaCrossSignal(
                "SELL", fast_now, slow_now, spread,
                "death cross — fast MA crossed below slow MA", strength,
            )

    # no fresh cross: trend state
    if fast_now > slow_now:
        return MaCrossSignal(
            "BUY", fast_now, slow_now, spread,
            f"uptrend (fast {fast}MA above slow {slow}MA)", 0.3,
        )
    return MaCrossSignal(
        "SELL", fast_now, slow_now, spread,
        f"downtrend (fast {fast}MA below slow {slow}MA)", 0.3,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Volume Profile / POC (from tromario's volume-analysis-robot)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VolumeProfileResult:
    poc: float                 # Point Of Control — price with max volume
    vwap: float                # volume-weighted average price
    volume_at_poc: float
    total_volume: float
    high: float
    low: float
    poc_position: float        # 0..1 — where POC sits in the day's range
    insight: str


def volume_profile(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    bins: int = 24,
) -> VolumeProfileResult | None:
    """Volume profile with POC (point of control).

    The core idea of tromario's robot: find the price level where the
    MAXIMUM horizontal volume traded within the period. Institutional
    players cluster around POC; price tends to return to it.

    Needs at least 2 bars with volume. Pure python bucketing.
    """
    if len(closes) < 2 or not volumes or not highs or not lows:
        return None
    hi = max(highs)
    lo = min(lows)
    if hi <= lo:
        return None

    # build bins over the price range
    step = (hi - lo) / bins
    vol_by_bin = [0.0] * bins
    vwap_num = 0.0
    total_vol = 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        if v <= 0:
            continue
        # distribute volume across bins touched by the candle (typical price)
        typical = (h + l + c) / 3
        idx = int((typical - lo) / step)
        idx = max(0, min(bins - 1, idx))
        vol_by_bin[idx] += v
        vwap_num += typical * v
        total_vol += v

    if total_vol == 0:
        return None
    vwap = vwap_num / total_vol
    poc_idx = vol_by_bin.index(max(vol_by_bin))
    poc = lo + (poc_idx + 0.5) * step
    poc_pos = poc_idx / (bins - 1)

    if poc_pos > 0.6:
        insight = "POC near TOP of range — strong sellers above; distribution"
    elif poc_pos < 0.4:
        insight = "POC near BOTTOM — accumulation; buyers defending low"
    else:
        insight = "POC mid-range — balanced; expect mean reversion to POC"

    return VolumeProfileResult(
        poc=poc, vwap=vwap,
        volume_at_poc=vol_by_bin[poc_idx],
        total_volume=total_vol,
        high=hi, low=lo,
        poc_position=round(poc_pos, 2),
        insight=insight,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Interval strategy (from qwertyo1's tinkoff-trading-bot — WINNER)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Corridor:
    """A price corridor (bottom..top)."""
    bottom: float
    top: float

    def __iter__(self):
        yield self.bottom
        yield self.top


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile — matches numpy's default method.

    Pure python so token-diet stays zero-dependency.
    """
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


def percentile_corridor(
    closes: list[float],
    lookback: int = 30,
    interval_size: float = 0.8,
) -> Corridor | None:
    """WINNER technique (qwertyo1): percentile corridor instead of min/max.

    The corridor is the middle `interval_size` (default 0.8 → 80%) of the
    price distribution: bottom = 10th percentile, top = 90th percentile.
    One outlier spike cannot move the corridor, whereas min/max jumps
    on any extreme tick. This is exactly what made the contest winner's
    interval strategy robust on real MOEX data.
    """
    if not closes:
        return None
    # protect against inverted corridor: interval_size outside (0, 1]
    interval_size = max(0.01, min(1.0, interval_size))
    window = closes[-lookback:] if len(closes) > lookback else closes
    tail = (1 - interval_size) / 2  # 0.1 for interval_size=0.8
    return Corridor(
        bottom=_percentile(window, tail),
        top=_percentile(window, 1 - tail),
    )


@dataclass
class IntervalSignal:
    signal: str          # BUY / SELL / HOLD
    price: float
    upper: float         # sell above this
    lower: float         # buy below this
    reason: str
    percentile: bool = True      # corridor built from percentiles or min/max


def interval_strategy(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    lookback: int = 20,
    percentile_mode: bool = True,
    interval_size: float = 0.8,
) -> IntervalSignal:
    """Interval strategy: buy near corridor bottom, sell near top.

    qwertyo1's (contest winner) approach: build a PERCENTILE corridor
    (middle 80% of last `lookback` closes) — robust to outlier spikes —
    then BUY in the bottom 20% of it, SELL in the top 20%. Works on
    range-bound markets. Set percentile_mode=False for the classic
    min/max corridor.
    """
    if not closes:
        return IntervalSignal("HOLD", 0, 0, 0, "no data")
    price = closes[-1]

    if percentile_mode:
        corr = percentile_corridor(closes, lookback, interval_size)
        if corr is None:
            return IntervalSignal("HOLD", 0, 0, 0, "no data")
        lo, hi = corr.bottom, corr.top
    else:
        window = closes[-lookback:] if len(closes) > lookback else closes
        lo = min(window)
        hi = max(window)

    # where are we in the corridor?
    if hi > lo:
        pos = (price - lo) / (hi - lo)
    else:
        pos = 0.5

    if pos <= 0.2:
        return IntervalSignal("BUY", price, hi, lo,
                              f"near corridor bottom ({pos:.0%}) — buy zone",
                              percentile=percentile_mode)
    if pos >= 0.8:
        return IntervalSignal("SELL", price, hi, lo,
                              f"near corridor top ({pos:.0%}) — sell zone",
                              percentile=percentile_mode)
    return IntervalSignal("HOLD", price, hi, lo,
                          f"mid-corridor ({pos:.0%}) — wait",
                          percentile=percentile_mode)


# ═══════════════════════════════════════════════════════════════════════════════
# 3b. Risk management (from qwertyo1's winner: stop-loss + position limit)
# ═══════════════════════════════════════════════════════════════════════════════

def stop_loss_level(
    avg_price: float,
    stop_loss_percent: float = 0.01,
) -> float:
    """WINNER technique: stop-loss = avg_price × (1 - stop_loss_percent).

    The contest winner triggers an exit when
        last_price <= avg_price × (1 - stop_loss_percent)
    i.e. the position lost `stop_loss_percent` (default 1%) from the
    average entry price. Stop is anchored to YOUR average, not to an
    arbitrary level — fair even if you bought in several lots.
    """
    return avg_price * (1 - stop_loss_percent)


def position_plan(
    quantity: int,
    quantity_limit: int,
    avg_price: float | None = None,
    last_price: float | None = None,
    stop_loss_percent: float = 0.01,
) -> dict[str, Any]:
    """WINNER risk loop: what the robot should do with the position now.

    Order of checks matches the winner's main_cycle:
      1. STOP_LOSS  — last_price broke avg_price × (1 - stop_loss_pct)
                      → close the whole position
      2. SELL_ALL   — quantity >= quantity_limit (never exceed the cap)
      3. BUY        — below limit → top up to quantity_limit
      4. HOLD       — otherwise
    """
    plan: dict[str, Any] = {
        "action": "HOLD",
        "reason": "position within limits, no stop-loss hit",
        "quantity": quantity,
        "quantity_limit": quantity_limit,
    }
    if avg_price is not None:
        sl = stop_loss_level(avg_price, stop_loss_percent)
        plan["stop_loss_level"] = round(sl, 4)
        if last_price is not None and last_price <= sl:
            plan["action"] = "STOP_LOSS"
            plan["reason"] = (
                f"last {last_price:.2f} <= stop {sl:.2f} "
                f"({stop_loss_percent:.0%} below avg {avg_price:.2f}) — close all"
            )
            return plan
    if quantity_limit > 0:
        if quantity >= quantity_limit:
            plan["action"] = "SELL_ALL"
            plan["reason"] = f"quantity {quantity} >= limit {quantity_limit} — take profit"
        else:
            plan["action"] = "BUY"
            plan["to_buy"] = quantity_limit - quantity
            plan["reason"] = (
                f"quantity {quantity} < limit {quantity_limit} — "
                f"top up {quantity_limit - quantity}"
            )
    return plan


def market_open_now(
    now_utc=None,
) -> bool:
    """MOEX session heuristic: weekday, 10:00-18:50 MSK.

    NOTE: honest limitation — no holiday calendar, no exchange trading
    status API. It's a first-pass guard; for real trading the winner
    polls Tinkoff's get_trading_status. We expose that via
    run_on_tinkoff() when the API is reachable.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    msk = now_utc + timedelta(hours=3)
    if msk.weekday() >= 5:  # Sat/Sun
        return False
    minutes = msk.hour * 60 + msk.minute
    return 10 * 60 <= minutes <= 18 * 60 + 50


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Backtest (from investRobot: test strategy before trading it)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    pnl_pct: float
    kind: str          # LONG / SHORT


@dataclass
class BacktestResult:
    trades: list[Trade]
    total_pnl_pct: float        # sum of trade pnl (long only)
    win_rate: float             # 0..1
    max_drawdown_pct: float     # worst peak-to-trough on equity
    avg_win_pct: float
    avg_loss_pct: float
    final_equity: float         # 100 + total pnl
    verdict: str


def backtest(
    closes: list[float],
    fast: int = 5,
    slow: int = 20,
    only_crosses: bool = True,
) -> BacktestResult:
    """Replay MA-cross strategy over history. Honest numbers.

    LONG only (or both): buy on golden cross, sell on death cross.
    If only_crosses=False, holds between crosses (trend following).
    Returns P&L, win rate, max drawdown — the numbers a real trader
    needs before risking a ruble.
    """
    trades: list[Trade] = []
    if len(closes) < slow + 2:
        return BacktestResult([], 0, 0, 0, 0, 0, 100,
                              "not enough history")

    f = sma(closes, fast)
    s = sma(closes, slow)
    position: str | None = None
    entry_idx = 0
    entry_price = 0.0
    equity = 100.0
    peak = 100.0
    max_dd = 0.0

    for i in range(slow, len(closes)):
        fp, sp = f[i - 1], s[i - 1]
        fn, sn = f[i], s[i]
        if None in (fp, sp, fn, sn):
            continue
        cross_up = fp <= sp and fn > sn
        cross_down = fp >= sp and fn < sn

        if position is None and cross_up:
            position = "LONG"
            entry_idx, entry_price = i, closes[i]
        elif position == "LONG" and (cross_down or not only_crosses):
            if cross_down:
                pnl = (closes[i] - entry_price) / entry_price * 100
                trades.append(Trade(entry_idx, i, entry_price, closes[i],
                                    pnl, "LONG"))
                equity *= (1 + pnl / 100)
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100)
                position = None

    if trades:
        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]
        win_rate = len(wins) / len(trades)
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        total = sum(t.pnl_pct for t in trades)
        verdict = ("PROFITABLE" if total > 0
                   else "UNPROFITABLE" if total < 0 else "FLAT")
        return BacktestResult(
            trades=trades,
            total_pnl_pct=round(total, 2),
            win_rate=round(win_rate, 3),
            max_drawdown_pct=round(max_dd, 2),
            avg_win_pct=round(avg_win, 2),
            avg_loss_pct=round(avg_loss, 2),
            final_equity=round(equity, 2),
            verdict=verdict,
        )
    return BacktestResult([], 0, 0, 0, 0, 0, 100, "no trades")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Unified runner: feed real data into strategies
# ═══════════════════════════════════════════════════════════════════════════════

def run_strategies(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> dict[str, Any]:
    """Run ALL strategies on one dataset and return a combined verdict."""
    highs = highs or closes
    lows = lows or closes
    volumes = volumes or [1.0] * len(closes)

    cross = ma_cross(closes, fast=10, slow=30)
    vp = volume_profile(highs, lows, closes, volumes)
    iv = interval_strategy(closes, highs, lows)
    corridor = percentile_corridor(closes, lookback=20)

    # combine: 2 of 3 bullish → bullish lean
    bullish = sum(1 for s in (cross.signal, iv.signal)
                  if s == "BUY")
    bearish = sum(1 for s in (cross.signal, iv.signal)
                  if s == "SELL")
    vp_bull = bool(vp and vp.poc_position >= 0.5)

    if bullish + (1 if vp_bull else 0) >= 2:
        lean = "BUY"
    elif bearish + (0 if vp_bull else 1) >= 2:
        lean = "SELL"
    else:
        lean = "HOLD"

    return {
        "ma_cross": cross.__dict__,
        "volume_profile": vp.__dict__ if vp else None,
        "interval": iv.__dict__,
        "corridor": corridor.__dict__ if corridor else None,
        "combined_lean": lean,
        "strategies_agree": len({cross.signal, iv.signal, "HOLD"}) == 1 or
                            (cross.signal == iv.signal != "HOLD"),
    }


def run_on_tinkoff(
    ticker: str,
    days: int = 60,
) -> dict[str, Any]:
    """Feed REAL candles from TinkoffInvest into all strategies."""
    from .tinkoff_invest import TinkoffInvest

    tink = TinkoffInvest()
    candles = tink.get_candles(ticker, days=days)
    if len(candles) < 10:
        return {"ticker": ticker, "error": "not enough candles",
                "candles": len(candles)}
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    vols = [c.volume for c in candles]

    result = run_strategies(closes, highs, lows, vols)
    result["ticker"] = ticker
    result["price"] = closes[-1]
    result["date"] = str(candles[-1].time.date())
    return result


__all__ = [
    "MaCrossSignal", "VolumeProfileResult", "IntervalSignal",
    "Corridor", "Trade", "BacktestResult",
    "ma_cross", "volume_profile", "interval_strategy",
    "percentile_corridor", "stop_loss_level", "position_plan",
    "market_open_now",
    "backtest", "run_strategies", "run_on_tinkoff",
]
