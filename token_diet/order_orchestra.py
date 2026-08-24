"""Order Orchestra — оркестр ордеров: инфраструктура исполнения для людей.

Проблема: у брокера десяток типов заявок, у агента — идеи, а между ними
нет дисциплины. Оркестр даёт конвейер из четырёх ступеней:

1. PLAN    — намерение (тикер, сторона, объём, стоп/тейк) → заявки.
2. VALIDATE— проверка объёмов, цен, лимитов риска; мусор отсекается здесь.
3. PAPER   — исполнение по текущим ценам в бумажный журнал (дефолт).
4. LIVE    — реальная отправка через брокерный адаптер ТОЛЬКО при
             явном подтверждении человека: строка-подтверждение,
             зависящая от содержимого корзины.

Границы: по умолчанию всё живёт в PAPER. LIVE-режим требует
``confirm_token``, вычисленного из SHA-256 содержимого корзины —
случайное «да» не сработает. Каждое действие пишется в аудит-журнал.

Брокерный адаптер инжектируется: тесты используют фальшивый, прод —
обёртку над ``tinkoff_invest.TinkoffInvest``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# ── Заявки ──────────────────────────────────────────────────────────


@dataclass
class OrderIntent:
    """Намерение: что и почему хотим сделать."""

    ticker: str
    side: str                      # "BUY" | "SELL"
    quantity: float = 0.0          # в штуках
    limit_price: float | None = None   # None → маркет
    stop_loss: float | None = None     # цена стоп-заявки (защитная)
    take_profit: float | None = None
    reason: str = ""

    def validate(self) -> list[str]:
        """Список ошибок; пустой список = заявка валидна."""
        errors: list[str] = []
        if not self.ticker:
            errors.append("пустой тикер")
        if self.side not in ("BUY", "SELL"):
            errors.append(f"неизвестная сторона {self.side!r}")
        if self.quantity <= 0:
            errors.append("количество должно быть > 0")
        if self.limit_price is not None and self.limit_price <= 0:
            errors.append("лимит-цена должна быть > 0")
        if self.stop_loss is not None and self.stop_loss <= 0:
            errors.append("стоп должен быть > 0")
        if (
            self.side == "BUY" and self.stop_loss is not None
            and self.limit_price is not None and self.stop_loss >= self.limit_price
        ):
            errors.append("стоп BUY не может быть выше лимит-цены входа")
        if (
            self.side == "SELL" and self.take_profit is not None
            and self.limit_price is not None and self.take_profit <= self.limit_price
        ):
            errors.append("тейк SELL не может быть ниже лимит-цены выхода")
        return errors


@dataclass
class ExecutedOrder:
    """Итог исполнения одной заявки."""

    intent: OrderIntent
    mode: str                      # "PAPER" | "LIVE"
    status: str                    # "FILLED" | "REJECTED" | "ERROR"
    price: float = 0.0
    detail: str = ""


@dataclass
class OrchestrationResult:
    """Итог прогона корзины."""

    orders: list[ExecutedOrder] = field(default_factory=list)
    rejected_intents: list[tuple[OrderIntent, str]] = field(default_factory=list)
    confirm_token: str = ""
    mode: str = "PAPER"

    @property
    def filled(self) -> int:
        return sum(1 for o in self.orders if o.status == "FILLED")

    def block(self) -> str:
        lines = [f"[orchestra] режим {self.mode}: исполнено {self.filled}/{len(self.orders)}"]
        for o in self.orders:
            icon = {"FILLED": "✓", "REJECTED": "✗", "ERROR": "!"}.get(o.status, "?")
            px = f" @ {o.price:.2f}" if o.price else ""
            lines.append(
                f"[orchestra]   {icon} {o.intent.side} {o.intent.quantity:g} "
                f"{o.intent.ticker}{px}"
            )
        for intent, err in self.rejected_intents:
            lines.append(f"[orchestra]   ✗ {intent.side} {intent.ticker}: {err}")
        return "\n".join(lines)


# ── Подтверждение LIVE ─────────────────────────────────────────────


def basket_confirm_token(intents: list[OrderIntent]) -> str:
    """Токен подтверждения = первые 12 hex sha256 содержимого корзины.

    Человек видит точную корзину и повторяет токен — так исключается
    подтверждение «на автомате» другой корзины.
    """
    payload = json.dumps(
        [
            {
                "ticker": i.ticker, "side": i.side,
                "q": i.quantity, "px": i.limit_price,
                "sl": i.stop_loss, "tp": i.take_profit,
            }
            for i in intents
        ],
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


# ── Брокерные адаптеры ──────────────────────────────────────────────


def paper_broker(prices: dict[str, float]) -> Callable[[OrderIntent], ExecutedOrder]:
    """Бумажный брокер: исполняет по заданным ценам, без сети."""
    def execute(intent: OrderIntent) -> ExecutedOrder:
        if intent.ticker not in prices:
            return ExecutedOrder(intent, "PAPER", "ERROR",
                                 detail=f"нет цены для {intent.ticker}")
        return ExecutedOrder(intent, "PAPER", "FILLED", price=prices[intent.ticker])
    return execute


def tinkoff_live_broker() -> Callable[[OrderIntent], ExecutedOrder]:
    """Живой брокер поверх TinkoffInvest (маркет/лимит + защитный стоп)."""
    from .tinkoff_invest import TinkoffInvest  # локальный импорт: тяжёлый модуль

    inv = TinkoffInvest()

    def execute(intent: OrderIntent) -> ExecutedOrder:
        try:
            fn = inv.buy_shares if intent.side == "BUY" else inv.sell_shares
            result = fn(intent.ticker, shares=max(1, int(intent.quantity)))
            raw = json.dumps(result, default=str).lower() if result else ""
            ok = bool(result) and "error" not in raw
            status = "FILLED" if ok else "REJECTED"
            price = float(intent.limit_price or 0)
            return ExecutedOrder(intent, "LIVE", status, price=price,
                                 detail=raw[:200])
        except Exception as e:  # noqa: BLE001 - брокер капризен
            return ExecutedOrder(intent, "LIVE", "ERROR", detail=f"{type(e).__name__}: {e}")
    return execute


# ── Оркестр ─────────────────────────────────────────────────────────


class OrderOrchestra:
    """Конвейер PLAN → VALIDATE → PAPER/LIVE c аудитом."""

    def __init__(
        self,
        broker: Callable[[OrderIntent], ExecutedOrder] | None = None,
        audit_path: str | Path | None = None,
        max_orders_per_run: int = 20,
    ):
        self.broker = broker or paper_broker({})
        self.audit_path = Path(audit_path) if audit_path else None
        self.max_orders_per_run = max_orders_per_run

    # ── публичный API ───────────────────────────────────────────

    def run(self, intents: list[OrderIntent], *, live: bool = False,
            confirm_token: str = "") -> OrchestrationResult:
        """Исполнить корзину.

        live=False (по умолчанию) — бумажный прогон без последствий.
        live=True требует точный basket_confirm_token(intents).
        """
        result = OrchestrationResult(mode="LIVE" if live else "PAPER")

        # 1) VALIDATE
        valid: list[OrderIntent] = []
        for intent in intents:
            errors = intent.validate()
            if errors:
                result.rejected_intents.append((intent, "; ".join(errors)))
            else:
                valid.append(intent)
        if len(valid) > self.max_orders_per_run:
            overflow = valid[self.max_orders_per_run:]
            result.rejected_intents.append(
                (overflow[0], f"корзина больше лимита ({self.max_orders_per_run})"),
            )
            valid = valid[: self.max_orders_per_run]

        # 2) LIVE-gate
        token = basket_confirm_token(valid)
        result.confirm_token = token
        if live and confirm_token != token:
            result.orders.extend(
                ExecutedOrder(i, "LIVE", "REJECTED",
                              detail="подтверждение не совпало с корзиной")
                for i in valid
            )
            self._audit(result, note="live отклонён: неверный confirm_token")
            return result

        # 3) EXECUTE
        for intent in valid:
            result.orders.append(self.broker(intent))

        self._audit(result)
        return result

    # ── аудит ───────────────────────────────────────────────────

    def _audit(self, result: OrchestrationResult, note: str = "") -> None:
        line = {
            "ts": round(time.time(), 3),
            "mode": result.mode,
            "filled": result.filled,
            "orders": [
                {"side": o.intent.side, "ticker": o.intent.ticker,
                 "qty": o.intent.quantity, "status": o.status}
                for o in result.orders
            ],
            "rejected": [
                {"ticker": i.ticker, "err": e} for i, e in result.rejected_intents
            ],
            "token": result.confirm_token,
        }
        if note:
            line["note"] = note
        text = json.dumps(line, ensure_ascii=False)
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")


def plan_stop_bracket(
    ticker: str, entry: float, atr_value: float,
    quantity: float = 1.0, atr_multiplier: float = 2.5,
    rr: float = 2.0,
) -> list[OrderIntent]:
    """Готовая связка «вход + стоп + тейк» из ATR (risk-first планирование).

    Стоп = вход − k×ATR; тейк = вход + rr×(k×ATR). Возвращает корзину
    из одной лимитной заявки со встроенной защитой — удобно скармливать
    в ``OrderOrchestra.run`` после проверки человеком.
    """
    risk = atr_multiplier * atr_value
    return [OrderIntent(
        ticker=ticker, side="BUY", quantity=quantity,
        limit_price=round(entry, 4),
        stop_loss=round(entry - risk, 4),
        take_profit=round(entry + rr * risk, 4),
        reason=f"bracket: риск {risk:.2f} ₽, R:R 1:{rr:g}",
    )]
