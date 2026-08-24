"""Position Guard — страж портфеля: уровни стопов и статусы позиций.

Отвечает на один вопрос по каждой позиции: *нужен ли стоп и где он
должен стоять?* Объединяет:

- ``risk_metrics.trailing_stop`` — freqtrade-style ATR-трейлинг;
- ``trading_robot.stop_loss_level`` — фикс-стоп от средней цены входа;
- ``market_intelligence.atr`` — волатильность для ширины стопа.

Граница модуля принципиальна: он **не выставляет ордера**. На выходе —
статус (OK/WARN/BREACHED), рекомендованная цена стопа и готовый текст,
который человек исполняет в своём терминале (или подтверждает через
``order_orchestra``). Данные приходят через инжектируемый fetcher —
тесты и офлайн-режим работают без сети.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

try:
    from .market_intelligence import atr as _atr
    from .risk_metrics import trailing_stop
    from .trading_robot import stop_loss_level
except ImportError:  # standalone use
    from market_intelligence import atr as _atr  # type: ignore[no-redef]
    from risk_metrics import trailing_stop  # type: ignore[no-redef]
    from trading_robot import stop_loss_level  # type: ignore[no-redef]


@dataclass
class Position:
    """Позиция для наблюдения."""

    ticker: str
    quantity: float = 0.0
    avg_price: float = 0.0


@dataclass
class GuardVerdict:
    """Вердикт по одной позиции."""

    ticker: str
    status: str                    # "OK" | "WARN" | "BREACHED"
    price: float                   # текущая цена
    stop_price: float              # рекомендованный уровень стопа
    stop_kind: str                 # "atr-trailing" | "fixed-pct" | "none"
    distance_pct: float            # % до стопа от текущей цены
    profit_pct: float = 0.0        # P&L позиции против средней
    note: str = ""

    def line(self) -> str:
        icon = {"OK": "✓", "WARN": "⚠", "BREACHED": "⛔"}.get(self.status, "?")
        return (
            f"[guard] {icon} {self.ticker:<8} {self.price:>9.2f} ₽ · "
            f"P&L {self.profit_pct:+.1f}% · стоп {self.stop_price:.2f} ₽ "
            f"({self.stop_kind}, −{self.distance_pct:.1f}%) [{self.status}] "
            f"{self.note}".rstrip()
        )


@dataclass
class GuardReport:
    """Сводка по всем позициям."""

    verdicts: list[GuardVerdict] = field(default_factory=list)

    @property
    def breached(self) -> list[GuardVerdict]:
        return [v for v in self.verdicts if v.status == "BREACHED"]

    @property
    def warnings(self) -> list[GuardVerdict]:
        return [v for v in self.verdicts if v.status == "WARN"]

    def block(self) -> str:
        lines = ["[guard] страж портфеля:"]
        lines += [v.line() for v in self.verdicts]
        if not self.verdicts:
            lines.append("[guard] позиций нет")
        for v in self.breached:
            lines.append(
                f"[guard] ⛔ {v.ticker}: цена {v.price:.2f} ниже/на уровне стопа "
                f"{v.stop_price:.2f} — время решать (продать/подтвердить удержание)"
            )
        return "\n".join(lines)


CandleFetcher = Callable[[str, int], list[tuple[float, float, float]]]


def _default_fetcher(ticker: str, days: int) -> list[tuple[float, float, float]]:
    """Реальный фетчер: (high, low, close) дневные свечи из Tinkoff API."""
    try:
        from .tinkoff_invest import TinkoffInvest

        candles = TinkoffInvest().get_candles(ticker, days=days)
        return [(c.high, c.low, c.close) for c in (candles or [])]
    except Exception:  # noqa: BLE001 - сеть капризна, страж не должен падать
        return []


def guard_position(
    position: Position,
    candles: list[tuple[float, float, float]],
    *,
    atr_multiplier: float = 2.5,
    fixed_pct: float = 0.05,
    warn_pct: float = 2.0,
) -> GuardVerdict | None:
    """Вердикт по одной позиции из истории свечей (high, low, close).

    Стоп выбирается консервативно: max(фикс от средней, ATR-trailing),
    пока трейлинг не активирован прибылью; после активации — трейлинг.
    """
    if not candles or position.avg_price <= 0:
        return None
    highs = [c[0] for c in candles]
    lows = [c[1] for c in candles]
    closes = [c[2] for c in candles]
    price = closes[-1]

    atr_series = [a for a in _atr(highs, lows, closes, period=14) if a is not None]
    atr_value = atr_series[-1] if atr_series else price * fixed_pct / atr_multiplier

    trail = trailing_stop(
        entry_price=position.avg_price, current_price=price,
        atr=max(atr_value, 1e-9), atr_multiplier=atr_multiplier,
    )
    fixed_stop = stop_loss_level(position.avg_price, stop_loss_percent=fixed_pct)

    if trail["activated"]:
        stop, kind = trail["stop"], "atr-trailing"
    else:
        # до активации держим стоп не глубже фиксированного процента
        stop, kind = max(trail["stop"], fixed_stop), (
            "atr-trailing" if trail["stop"] >= fixed_stop else "fixed-pct"
        )

    profit_pct = 100.0 * (price - position.avg_price) / position.avg_price
    distance_pct = 100.0 * (price - stop) / price if price else 0.0

    if price <= stop:
        status, note = "BREACHED", "цена пробила уровень стопа"
    elif distance_pct < warn_pct:
        status, note = "WARN", f"до стопа меньше {warn_pct:g}%"
    else:
        status, note = "OK", ""

    return GuardVerdict(
        ticker=position.ticker,
        status=status,
        price=round(price, 4),
        stop_price=round(stop, 4),
        stop_kind=kind,
        distance_pct=round(distance_pct, 2),
        profit_pct=round(profit_pct, 2),
        note=note,
    )


def watch_positions(
    positions: list[Position],
    fetcher: CandleFetcher | None = None,
    days: int = 60,
    **kwargs: float,
) -> GuardReport:
    """Прогнать все позиции через стража.

    fetcher по умолчанию ходит в Tinkoff API; в тестах подставляется свой.
    Позиции без данных молча пропускаются (в отчёте их нет).
    """
    fetch = fetcher or _default_fetcher
    report = GuardReport()
    for pos in positions:
        try:
            candles = fetch(pos.ticker, days)
        except Exception:  # noqa: BLE001
            continue
        verdict = guard_position(pos, candles, **kwargs)
        if verdict is not None:
            report.verdicts.append(verdict)
    return report
