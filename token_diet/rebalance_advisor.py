"""Rebalance Advisor — советы по ребалансировке портфеля к целевым весам.

Идеи взяты у робо-эдвайзеров (Betterment/Wealthfront): целевые веса,
коридор допустимого дрейфа и порог «пыли», ниже которого сделка не
оправдывает комиссию. Отличие от них принципиальное: модуль **не выставляет
ордера и не ходит в сеть**. На выходе — таблица решений (BUY/SELL/HOLD),
которую человек читает и исполняет сам или через свой терминал.

Правила:

1. Вес актива = стоимость / итог портфеля (включая кэш).
2. Дрейф от цели больше ``drift_band`` → SELL (перевес) или BUY (недовес).
3. Сделка меньше ``min_trade_rub`` → HOLD: комиссия съест смысл.
4. Кэш участвует как актив с целевым весом ``cash_target`` — классика
   робо-эдвайзеров: подушка должна существовать явно, а не «как получится».

Детерминированно, офлайн, без секретов — как всё в token-diet.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Holding:
    """Текущая позиция портфеля."""

    ticker: str
    quantity: float = 0.0
    price: float = 0.0        # текущая цена за единицу

    @property
    def value(self) -> float:
        return self.quantity * self.price


@dataclass
class TradeAdvice:
    """Рекомендация по одному тикеру. Решение исполняет человек."""

    ticker: str
    action: str                # "BUY" | "SELL" | "HOLD"
    amount_rub: float          # объём сделки (для HOLD — 0)
    current_weight: float      # фактический вес сейчас
    target_weight: float       # целевой вес
    drift_pct: float           # (текущий − цель), в долях
    reason: str


@dataclass
class RebalancePlan:
    """Итог ребалансировки: список советов + сводка."""

    advices: list[TradeAdvice] = field(default_factory=list)
    total_value_rub: float = 0.0
    turnover_rub: float = 0.0       # суммарный объём BUY+SELL
    cash_weight: float = 0.0

    @property
    def actions_needed(self) -> int:
        return sum(1 for a in self.advices if a.action != "HOLD")

    def block(self) -> str:
        lines = [
            f"[rebalance] портфель {self.total_value_rub:.0f} ₽, "
            f"кэш {self.cash_weight:.1%}, оборот {self.turnover_rub:.0f} ₽",
        ]
        for a in self.advices:
            icon = {"BUY": "+", "SELL": "−"}.get(a.action, " ")
            amt = f"{icon}{a.amount_rub:.0f} ₽" if a.action != "HOLD" else "—"
            lines.append(
                f"[rebalance] {a.ticker:<8} {a.action:<4} {amt:>12} "
                f"вес {a.current_weight:.1%}→цель {a.target_weight:.1%}  {a.reason}"
            )
        if not any(a.action != "HOLD" for a in self.advices):
            lines.append("[rebalance] всё в коридоре — действий не нужно")
        return "\n".join(lines)


def rebalance_plan(
    holdings: list[Holding],
    targets: dict[str, float],
    cash_rub: float = 0.0,
    drift_band: float = 0.05,
    min_trade_rub: float = 1000.0,
    cash_target: float = 0.05,
) -> RebalancePlan:
    """Считать план ребалансировки к целевым весам.

    Args:
        holdings: текущие позиции.
        targets: целевые веса инструментов (доли от 0 до 1).
        cash_rub: свободные деньги на счёте.
        drift_band: допустимый дрейф; внутри коридора — HOLD.
        min_trade_rub: сделки мельче — не предлагать («пыль»).
        cash_target: неявная цель для кэша, если его нет в ``targets``.
    """
    if not holdings and cash_rub <= 0:
        return RebalancePlan()

    full_targets = dict(targets)
    if cash_rub > 0 and "_CASH" not in full_targets and cash_target > 0:
        # нормируем инструменты к (1 - cash_target), чтобы сумма весов сошлась
        s = sum(full_targets.values())
        if s > 0:
            scale = (1.0 - cash_target) / s
            full_targets = {k: v * scale for k, v in full_targets.items()}
        full_targets["_CASH"] = cash_target

    total = cash_rub + sum(h.value for h in holdings)
    if total <= 0:
        return RebalancePlan()

    values: dict[str, float] = {"_CASH": cash_rub}
    for h in holdings:
        values[h.ticker] = values.get(h.ticker, 0.0) + h.value

    plan = RebalancePlan(total_value_rub=total, cash_weight=cash_rub / total)
    for ticker, target in sorted(full_targets.items()):
        value = values.get(ticker, 0.0)
        weight = value / total
        diff_rub = target * total - value
        drift = weight - target

        if abs(drift) <= drift_band or abs(diff_rub) < min_trade_rub:
            advice = TradeAdvice(
                ticker=ticker, action="HOLD", amount_rub=0.0,
                current_weight=weight, target_weight=target, drift_pct=drift,
                reason="в коридоре" if abs(drift) <= drift_band else "пыль",
            )
        elif diff_rub > 0:
            advice = TradeAdvice(
                ticker=ticker, action="BUY", amount_rub=diff_rub,
                current_weight=weight, target_weight=target, drift_pct=drift,
                reason=f"недовес {abs(drift):.1%}",
            )
        else:
            advice = TradeAdvice(
                ticker=ticker, action="SELL", amount_rub=-diff_rub,
                current_weight=weight, target_weight=target, drift_pct=drift,
                reason=f"перевес {abs(drift):.1%}",
            )
        plan.advices.append(advice)

    plan.turnover_rub = sum(a.amount_rub for a in plan.advices)
    return plan


# ── Convenience ─────────────────────────────────────────────────────


def drift_report(
    holdings: list[Holding], targets: dict[str, float], cash_rub: float = 0.0,
) -> list[tuple[str, float]]:
    """(тикер, дрейф) по убыванию отклонения — быстрый диагноз без плана."""
    total = cash_rub + sum(h.value for h in holdings)
    if total <= 0:
        return []
    weights = {}
    for h in holdings:
        weights[h.ticker] = weights.get(h.ticker, 0.0) + h.value / total
    rows = [
        (t, round(weights.get(t, 0.0) - w, 4))
        for t, w in targets.items()
    ]
    return sorted(rows, key=lambda r: abs(r[1]), reverse=True)
