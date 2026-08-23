"""Momentum — ловить сильные движения (прорывы), а не только отскоки.

Основной движок (market_intelligence) — mean-reversion: RSI перекуплен → SELL,
перепродан → BUY. Это риск-менеджмент, но он НЕ ловит те самые «+10% за день»,
когда бумага пробивает максимум и улетает с объёмом.

Этот модуль — momentum-компонент, которого не хватало:

1. Rate of Change (ROC) — насколько сильно цена изменилась за N дней.
2. Breakout detection — цена закрылась ВЫШЕ максимума последних N дней
   (или ниже минимума) + подтверждение объёмом.
3. momentum_lean — непрерывный наклон momentum (−1..+1) для ранжирования.

Всё — чистая математика на свечах, 0 LLM-вызовов.
"""

from __future__ import annotations

from dataclasses import dataclass


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Rate of Change
# ═══════════════════════════════════════════════════════════════════════════════

def rate_of_change(closes: list[float], period: int = 10) -> float | None:
    """Процентное изменение цены за period свечей. None если мало данных."""
    if len(closes) < period + 1:
        return None
    past = closes[-period - 1]
    if past == 0:
        return None
    return round((closes[-1] - past) / past * 100, 3)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Breakout detection
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BreakoutResult:
    direction: str = "none"   # "up" / "down" / "none"
    strength: float = 0.0     # 0..1
    description: str = ""
    volume_ratio: float = 1.0 # текущий объём / средний

    @property
    def is_breakout(self) -> bool:
        return self.direction in ("up", "down")


def detect_breakout(
    closes: list[float],
    highs: list[float] | None = None,
    volumes: list[float] | None = None,
    lookback: int = 20,
) -> BreakoutResult:
    """Прорыв: цена закрылась выше максимума (или ниже минимума) за lookback дней.

    Классический момент-сигнал: когда бумага пробивает недавний диапазон
    с повышенным объёмом — это начало сильного движения (часто те самые +10%).
    """
    if len(closes) < lookback + 1:
        return BreakoutResult()

    window_high = max(closes[-lookback:-1]) if not highs else max(highs[-lookback:-1])
    window_low = min(closes[-lookback:-1]) if not highs else min(highs[-lookback:-1])
    last = closes[-1]

    # объёмное подтверждение
    vol_ratio = 1.0
    if volumes and len(volumes) >= lookback + 1:
        avg_vol = sum(volumes[-lookback:-1]) / lookback
        if avg_vol > 0:
            vol_ratio = round(volumes[-1] / avg_vol, 2)

    if last > window_high:
        # сила прорыва: насколько выше + объём
        over = (last - window_high) / window_high if window_high else 0
        strength = min(1.0, 0.4 + over * 20 + max(0.0, (vol_ratio - 1.0)) * 0.2)
        desc = f"breakout above {lookback}d high, vol ×{vol_ratio}"
        return BreakoutResult("up", round(strength, 3), desc, vol_ratio)

    if last < window_low:
        under = (window_low - last) / window_low if window_low else 0
        strength = min(1.0, 0.4 + under * 20 + max(0.0, (vol_ratio - 1.0)) * 0.2)
        desc = f"breakdown below {lookback}d low, vol ×{vol_ratio}"
        return BreakoutResult("down", round(strength, 3), desc, vol_ratio)

    return BreakoutResult(volume_ratio=vol_ratio)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Continuous momentum lean
# ═══════════════════════════════════════════════════════════════════════════════

def momentum_lean(
    closes: list[float],
    highs: list[float] | None = None,
    volumes: list[float] | None = None,
    roc_period: int = 10,
    lookback: int = 20,
) -> float:
    """Непрерывный наклон momentum (−1..+1) для ранжирования бумаг.

    Комбинирует ROC (сила тренда) и прорыв (breakout) с объёмом.
    """
    lean = 0.0

    roc = rate_of_change(closes, roc_period)
    if roc is not None:
        # нормируем: ±10% = полный сигнал
        lean += max(-1.0, min(1.0, roc / 10.0)) * 0.5

    brk = detect_breakout(closes, highs, volumes, lookback)
    if brk.direction == "up":
        lean += brk.strength * 0.5
    elif brk.direction == "down":
        lean -= brk.strength * 0.5

    return round(max(-1.0, min(1.0, lean)), 4)
