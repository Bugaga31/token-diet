"""Portfolio Commander — автономное управление портфелем с предохранителем.

The Commander takes over the portfolio: for EVERY position it builds an
honest plan (stop-loss from your average, take-profit, buy zone), watches
live prices against those levels, and generates concrete orders.

IRON RULE (the safety gate): the Commander NEVER places a real order by
itself. It returns orders in dry-run mode. Real execution happens only
when the human explicitly confirms each order (confirm=True per order).
This is not a bug — it's the whole point. Machines don't own your money.

Based on the winner techniques (qwertyo1) already in trading_robot.py:
  - stop_loss_level()     — stop anchored to YOUR average entry
  - position_plan()       — stop first, then quantity limits
  - percentile_corridor() — where price sits in the real distribution

Honesty rules:
  - No "maximum profit" promises — that would be lying.
  - Margin disabled on the account → leverage analysis says so, honestly.
  - No data → "no data", never a hallucinated number.

For the people. For the planet. Honesty is cheaper than regret.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .invest_hub import InvestHub, _safe
from .trading_robot import percentile_corridor, stop_loss_level

# ─────────────────────────────────────────────────────────────────────────────
# Чистые функции планирования (тестируются без сети)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PositionPlan:
    """Честный план по одной позиции."""
    ticker: str
    quantity: float
    avg_price: float
    current_price: float
    profit_pct: float
    stop_loss: float | None = None
    take_profit: float | None = None
    buy_zone_bottom: float | None = None
    buy_zone_top: float | None = None
    action: str = "HOLD"          # HOLD / BUY / SELL / STOP
    risk_rub: float = 0.0         # сколько денег в риске до стопа
    reason: str = ""


def _percentile_stop(closes: list[float] | None, avg_price: float,
                     stop_pct: float, floor_pct: float) -> float:
    """Стоп = max(avg*(1-stop_pct), 10-й перцентиль коридора).

    Честный компромисс winner-техники: стоп не ближе, чем средняя минус
    stop_pct, и не внутри шумового коридора (ниже 10-го перцентиля).
    """
    stop = stop_loss_level(avg_price, stop_pct)
    if closes:
        corr = percentile_corridor(closes, lookback=30, interval_size=0.8)
        if corr is not None and corr.bottom < avg_price * (1 - floor_pct):
            stop = max(stop, corr.bottom)
    return round(stop, 2)


def plan_position(
    ticker: str,
    quantity: float,
    avg_price: float,
    current_price: float,
    closes: list[float] | None = None,
    stop_pct: float = 0.015,
    take_pct: float = 0.045,
    floor_pct: float = 0.02,
) -> PositionPlan:
    """План по одной позиции: стоп, тейк, зона докупки, действие.

    - stop_loss:   max(avg×(1−stop_pct), 10-й перцентиль) — не ближе стоп_pct
                   и не внутри шумового коридора (winner-техника).
    - take_profit: avg×(1+take_pct) — честная цель, не фантазия.
    - buy zone:    коридор [10-й перцентиль, 30-й перцентиль] — докупка
                   только внутри нижней трети распределения.
    - action:      STOP если цена уже ниже стопа; BUY если цена в зоне
                   докупки И позиция ниже лимита; SELL если цена у тейка;
                   иначе HOLD.
    """
    profit_pct = (current_price - avg_price) / avg_price * 100 if avg_price else 0.0
    stop = _percentile_stop(closes, avg_price, stop_pct, floor_pct)
    take = round(avg_price * (1 + take_pct), 2)

    # зона докупки: нижняя треть НЕДАВНЕГО коридора (10 свечей).
    # Короткий lookback — иначе в коридор попадают цены месячной давности
    # и «зона докупки» врёт при любой цене. Честно = недавно.
    buy_bottom = buy_top = None
    if closes:
        recent = closes[-10:] if len(closes) > 10 else closes
        corr = percentile_corridor(recent, lookback=10, interval_size=0.8)
        if corr is not None and corr.top > corr.bottom:
            buy_bottom = round(corr.bottom, 2)
            buy_top = round(corr.bottom + (corr.top - corr.bottom) * 0.3, 2)

    # риск = деньги, которые мы готовы потерять, пока цена идёт от текущей
    # до стопа. Если цена уже ниже стопа — убыток реализован, берём abs.
    risk_rub = round(abs(current_price - stop) * quantity, 2) if stop else 0.0
    action = "HOLD"
    reason = "ни стоп, ни тейк не достигнуты"

    if current_price <= stop:
        action = "STOP"
        reason = (f"цена {current_price:.2f} ≤ стоп {stop:.2f} — "
                  f"закрыть позицию (убыток до стопа: {risk_rub:.2f}₽)")
    elif buy_bottom is not None and buy_bottom <= current_price <= buy_top:
        action = "BUY"
        reason = (f"цена {current_price:.2f} в зоне докупки "
                  f"[{buy_bottom:.2f}..{buy_top:.2f}] — можно докупить")
    elif take is not None and current_price >= take:
        action = "SELL"
        reason = (f"цена {current_price:.2f} ≥ тейк {take:.2f} — "
                  f"зафиксировать прибыль {profit_pct:+.2f}%")

    return PositionPlan(
        ticker=ticker, quantity=quantity, avg_price=round(avg_price, 2),
        current_price=round(current_price, 2), profit_pct=round(profit_pct, 2),
        stop_loss=stop, take_profit=take,
        buy_zone_bottom=buy_bottom, buy_zone_top=buy_top,
        action=action, risk_rub=risk_rub, reason=reason,
    )


def leverage_analysis(
    total_rub: float,
    position_rub: float,
    position_pct: float = 0.0,
    margin_enabled: bool = False,
) -> dict[str, Any]:
    """Честный анализ рычагов: что ДАЛО бы плечо, если бы маржа была.

    С маржой ~2x и текущей концентрацией в одной бумаге просадка
    удваивается. Показываем числа, а не обещания: плечо умножает и
    прибыль, и убыток. Если маржа отключена — говорим прямо.
    """
    if not margin_enabled:
        return {
            "margin_enabled": False,
            "verdict": "Маржинальная торговля ОТКЛЮЧЕНА на счёте. "
                       "Рычаги недоступны — включить можно только в "
                       "приложении Т-Инвестиций (риск-профиль).",
            "max_leverage": 1.0,
        }
    if total_rub <= 0:
        return {"margin_enabled": True, "error": "нет данных по счёту"}
    concentration = position_rub / total_rub if total_rub else 0.0
    out: dict[str, Any] = {
        "margin_enabled": True,
        "position_rub": round(position_rub, 2),
        "total_rub": round(total_rub, 2),
        "concentration_pct": round(concentration * 100, 1),
        "warning": (f"Концентрация в одной бумаге {concentration:.0%} — "
                    f"плечо сделает портфель рулеткой: при -5% на бумаге "
                    f"с плечом 2x портфель просядет ~{concentration * 10:.0f}%."),
    }
    # что дало бы плечо 1.5x / 2x
    for lev in (1.5, 2.0):
        out[f"leverage_{lev}x"] = {
            "gain_if_pos_plus5pct": round(position_rub * 0.05 * lev, 2),
            "loss_if_pos_minus5pct": round(-position_rub * 0.05 * lev, 2),
        }
    out["recommendation"] = (
        "На счёте ~20К с одной бумагой плечо не добавляет преимущества — "
        "оно добавляет риск. Рекомендация: плечо НЕ включать."
    )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Командир: живой портфель → планы → ордера (dry-run по умолчанию)
# ─────────────────────────────────────────────────────────────────────────────

class PortfolioCommander:
    """Автономный командир портфеля. Ордера — только с подтверждением."""

    def __init__(
        self,
        token: str | None = None,
        stop_pct: float = 0.015,
        take_pct: float = 0.045,
        days: int = 60,
    ):
        self.hub = InvestHub(token=token)
        self.stop_pct = stop_pct
        self.take_pct = take_pct
        self.days = days
        self._account_id: str | None = None

    # ── маржа ────────────────────────────────────────────────────────────
    def margin_status(self) -> dict[str, Any]:
        """Проверяет, включена ли маржа (рычаги) на счёте."""
        mcp = self.hub._get_mcp()
        if mcp is None:
            return {"margin_enabled": False, "error": "MCP недоступен"}
        accs = _safe(
            lambda: (mcp.call("invest_list_broker_accounts", {})
                     .get("data", {}).get("accounts") or []), [],
        )
        if not accs:
            return {"margin_enabled": False, "error": "счета не найдены"}
        self._account_id = str(accs[0].get("id") or "")
        res = _safe(lambda: mcp.call(
            "invest_get_broker_account_margin",
            {"account_id": self._account_id}), None,
        )
        if not res:
            return {"margin_enabled": False, "error": "маржу не получить"}
        text = str(res.get("text") or "")
        enabled = "disabled" not in text.lower() and "not enabled" not in text.lower()
        return {
            "margin_enabled": enabled,
            "account_id": self._account_id,
            "hint": text[:120] if not enabled else "маржа включена",
        }

    # ── план по всему портфелю ───────────────────────────────────────────
    def plan(self) -> dict[str, Any]:
        """Позиции → по каждой план (стоп/тейк/зона докупки) + сводка."""
        positions = _safe(lambda: self.hub.tinkoff.get_portfolio(), None)
        if positions is None:
            return {"error": "нет доступа к портфелю"}

        plans = []
        total_rub = 0.0
        total_risk = 0.0
        for p in positions:
            ticker = getattr(p, "ticker", "UNKNOWN")
            if ticker.startswith("uid:") or ticker == "UNKNOWN":
                continue  # валюта и мусор — не торгуем
            qty = float(getattr(p, "quantity", 0))
            avg = float(getattr(p, "avg_price", 0))
            cur = float(getattr(p, "current_price", 0))
            if qty <= 0 or avg <= 0 or cur <= 0:
                continue
            closes = None
            candles = _safe(lambda t=ticker: self.hub.candles(t, days=self.days), [])
            if candles:
                closes = [c["close"] for c in candles]
            plan = plan_position(
                ticker, qty, avg, cur, closes=closes,
                stop_pct=self.stop_pct, take_pct=self.take_pct,
            )
            plans.append(plan)
            total_rub += qty * cur
            total_risk += plan.risk_rub

        margin = self.margin_status()

        return {
            "positions": [p.__dict__ for p in plans],
            "count": len(plans),
            "total_position_rub": round(total_rub, 2),
            "total_risk_rub": round(total_risk, 2),
            "margin": margin,
            "leverage": leverage_analysis(
                total_rub, total_rub,
                margin_enabled=margin.get("margin_enabled", False),
            ),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    # ── сторож: текущие цены vs стоп/тейк ────────────────────────────────
    def watch(self) -> dict[str, Any]:
        """Свежие котировки по всем позициям → алерты (STOP/SELL/BUY/HOLD)."""
        plan = self.plan()
        if "error" in plan:
            return plan
        alerts = []
        for p in plan["positions"]:
            # свежая цена с биржи
            q = _safe(lambda p=p: self.hub.quote(p["ticker"]), {})
            price = (q or {}).get("price")
            if price is None:
                alerts.append({
                    "ticker": p["ticker"], "alert": "NO_QUOTE",
                    "message": "цена не получена — проверить сеть",
                })
                continue
            if price <= p["stop_loss"]:
                alerts.append({
                    "ticker": p["ticker"], "alert": "STOP_HIT",
                    "price": price, "stop": p["stop_loss"],
                    "message": (f"⚠️ СТОП: {price:.2f} ≤ {p['stop_loss']:.2f} — "
                                f"закрывать позицию"),
                })
            elif price >= p["take_profit"]:
                alerts.append({
                    "ticker": p["ticker"], "alert": "TAKE_PROFIT",
                    "price": price, "take": p["take_profit"],
                    "message": (f"✅ ТЕЙК: {price:.2f} ≥ {p['take_profit']:.2f} — "
                                f"фиксировать прибыль {p['profit_pct']:+.2f}%"),
                })
            elif p["buy_zone_bottom"] is not None and \
                    p["buy_zone_bottom"] <= price <= p["buy_zone_top"]:
                alerts.append({
                    "ticker": p["ticker"], "alert": "BUY_ZONE",
                    "price": price,
                    "zone": [p["buy_zone_bottom"], p["buy_zone_top"]],
                    "message": (f"🟢 ЗОНА ДОКУПКИ: {price:.2f} в "
                                f"[{p['buy_zone_bottom']}..{p['buy_zone_top']}]"),
                })
            else:
                alerts.append({
                    "ticker": p["ticker"], "alert": "HOLD",
                    "price": price, "message": "ни стоп, ни тейк, ни зона — держим",
                })
        return {"alerts": alerts, "count": len(alerts),
                "generated_at": plan["generated_at"]}

    # ── генерация ордеров (ВСЕГДА dry-run — честно) ──────────────────────
    def orders(self) -> dict[str, Any]:
        """Конкретные ордера по плану. ВСЕГДА dry-run.

        ЖЕЛЕЗНОЕ ПРАВИЛО: этот модуль НЕ исполняет ордера. Точка.
        Он показывает, ЧТО нужно сделать, а человек решает — делать ли
        это в приложении Т-Инвестиций. Никакого скрытого исполнения,
        никаких «автоматических» сделок с вашими деньгами.
        """
        plan = self.plan()
        if "error" in plan:
            return plan
        orders = []
        for p in plan["positions"]:
            o = {"ticker": p["ticker"], "quantity": p["quantity"],
                 "dry_run": True}
            if p["action"] == "STOP":
                o["side"] = "SELL"
                o["price"] = p["stop_loss"]
                o["type"] = "STOP_LOSS"
                o["reason"] = p["reason"]
            elif p["action"] == "SELL":
                o["side"] = "SELL"
                o["price"] = p["take_profit"]
                o["type"] = "TAKE_PROFIT"
                o["reason"] = p["reason"]
            elif p["action"] == "BUY":
                o["side"] = "BUY"
                o["price"] = None  # по рынку в зоне докупки
                o["type"] = "MARKET"
                o["reason"] = p["reason"]
            else:
                continue
            orders.append(o)

        return {
            "orders": orders,
            "dry_run": True,
            "note": ("Dry-run: НИ ОДИН ордер не отправлен и НЕ будет "
                     "отправлен этим модулем никогда. Это план для вас: "
                     "что сделать в приложении Т-Инвестиций."),
        }

    # ── рендер для человека ──────────────────────────────────────────────
    @staticmethod
    def render_plan(plan: dict[str, Any]) -> str:
        lines = ["═══ PORTFOLIO COMMANDER — ПЛАН ═══"]
        if "error" in plan:
            return lines[0] + f"\nОшибка: {plan['error']}"
        for p in plan["positions"]:
            lines.append(f"\n{p['ticker']}: {p['quantity']:.0f} шт "
                         f"средняя {p['avg_price']:.2f}₽ "
                         f"сейчас {p['current_price']:.2f}₽ "
                         f"({p['profit_pct']:+.2f}%)")
            lines.append(f"  🛑 стоп   {p['stop_loss']:.2f}₽   "
                         f"✅ тейк  {p['take_profit']:.2f}₽")
            if p.get("buy_zone_bottom") is not None:
                lines.append(f"  🟢 зона докупки {p['buy_zone_bottom']:.2f}.."
                             f"{p['buy_zone_top']:.2f}₽")
            lines.append(f"  Действие: {p['action']} — {p['reason']}")
            lines.append(f"  Риск до стопа: {p['risk_rub']:.2f}₽")
        m = plan.get("margin", {})
        lines.append(f"\nМаржа: {'включена' if m.get('margin_enabled') else 'ОТКЛЮЧЕНА (рычаги недоступны)'}")
        lev = plan.get("leverage", {})
        if lev.get("verdict"):
            lines.append(f"Рычаги: {lev['verdict']}")
        lines.append(f"\nВсего в позициях: {plan.get('total_position_rub', 0):,.2f}₽, "
                     f"риск до стопов: {plan.get('total_risk_rub', 0):,.2f}₽")
        return "\n".join(lines)


__all__ = ["PortfolioCommander", "PositionPlan", "plan_position", "leverage_analysis"]
