"""Commander — единый мозг: сигналы + капитал + риск → одно решение.

Раньше всё было по кускам: сигналы отдельно, размер позиции отдельно, стоп
отдельно. Этот модуль сводит их в ОДНО честное решение:

    decide(signals, capital, risk_pct) → купить кого, сколько, где стоп/цель
                                         (или «держать кэш — края нет»)

Чистая детерминированная логика, 0 LLM-вызовов. Легко тестируется.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .risk_metrics import position_size, trade_plan


BUY_THRESHOLD = 0.15   # lean выше этого → кандидат на покупку
SELL_THRESHOLD = -0.15
STOP_PCT = 5.0         # стоп −5% от входа
TARGET_PCT = 10.0      # цель +10% от входа


@dataclass
class Decision:
    action: str                      # "buy" / "hold" / "sell"
    ticker: str = ""
    reason: str = ""
    lean: float = 0.0
    plan: dict[str, Any] = field(default_factory=dict)
    ranked: list[dict[str, Any]] = field(default_factory=list)

    def render(self) -> str:
        if self.action == "buy":
            p = self.plan
            return (
                f"BUY {self.ticker} (lean {self.lean:+.3f})\n"
                f"  вход {p.get('entry')} | стоп {p.get('stop')} | цель {p.get('target')}\n"
                f"  размер: {p.get('lots')} лотов ({p.get('shares')} акций, {p.get('position_cost')} ₽)\n"
                f"  риск при стопе: {p.get('risk_amount')} ₽ ({p.get('actual_risk_pct')}%) | R:R {p.get('rr_ratio')}"
            )
        if self.action == "sell":
            return f"SELL {self.ticker} — {self.reason}"
        return f"HOLD (кэш) — {self.reason}"


def decide(
    signals: list[dict[str, Any]],
    capital: float,
    risk_pct: float = 2.0,
    *,
    stop_pct: float = STOP_PCT,
    target_pct: float = TARGET_PCT,
) -> Decision:
    """Принять решение по списку сигналов (из screen/full_signal).

    signals: список dict с ключами ticker, price, lean (и опц. verdict, lot_size).
    Логика:
      - ранжируем по lean (силе сигнала)
      - если сильнейший lean > BUY_THRESHOLD → BUY малым размером под риск
      - если сильнейший lean < SELL_THRESHOLD и позиция есть → SELL
      - иначе → HOLD (кэш, края нет)
    """
    ranked = sorted(signals, key=lambda s: -float(s.get("lean") or 0.0))
    top = ranked[0] if ranked else None

    if not top:
        return Decision("hold", reason="нет данных по рынку", ranked=ranked)

    lean = float(top.get("lean") or 0.0)
    price = float(top.get("price") or 0.0)

    if lean <= SELL_THRESHOLD:
        return Decision("sell", ticker=top.get("ticker", ""),
                        reason=f"сигнал слабый (lean {lean:+.3f})", lean=lean, ranked=ranked)

    if lean <= BUY_THRESHOLD:
        return Decision("hold",
                        reason=f"нет сильного сигнала (лучший lean {lean:+.3f} < {BUY_THRESHOLD})",
                        lean=lean, ranked=ranked)

    # BUY: считаем размер под риск
    entry = price
    stop = round(entry * (1 - stop_pct / 100), 2)
    target = round(entry * (1 + target_pct / 100), 2)
    lot = int(top.get("lot_size") or 1)
    plan = trade_plan(capital, risk_pct, entry, stop, target, lot_size=lot)
    plan["entry"] = entry

    return Decision("buy", ticker=top.get("ticker", ""),
                    reason=f"сильный сигнал (lean {lean:+.3f})",
                    lean=lean, plan=plan, ranked=ranked)


def full_plan(inv, tickers: list[str], capital: float,
              risk_pct: float = 2.0, days: int = 90) -> Decision:
    """Живой план: тянет full_signal по всем бумагам и принимает решение.

    inv — экземпляр TinkoffInvest. Использует full_signal (техника+momentum+
    сантимент) + lot_size для каждой бумаги.
    """
    signals: list[dict[str, Any]] = []
    for t in tickers:
        try:
            s = inv.full_signal(t, days=days)
            if s:
                s["lot_size"] = inv.lot_size(t)
                signals.append(s)
        except Exception:
            continue
    return decide(signals, capital, risk_pct)
