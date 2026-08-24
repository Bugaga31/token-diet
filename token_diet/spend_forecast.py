"""Spend Forecast — предсказание расхода токенов/денег по истории использования.

Идея пришла из FinOps-практики облачных биллингов и LangSmith cost tracking:
команда узнаёт о перерасходе в конце месяца, когда менять уже поздно.
Здесь — простой детерминированный прогноз поверх дневной истории:

1. Скользящее среднее за ``window`` дней — базовая линия «обычного дня».
2. Доверительный коридор по разбросу остатков (σ) — честная ширина
   неопределённости вместо точечной цифры.
3. Аномалии: день, потративший больше ``mean + z * σ``, помечается —
   именно такие дни незаметно съедают бюджет.
4. Тренд — сравнение половин истории: растём, падаем или ровно.

Никаких зависимостей и стохастики: тот же вход — тот же прогноз,
в любой процесс и в любой год. Философия token-diet: прозрачная статистика
вместо чёрного ящика.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UsageDay:
    """Один день использования: израсходованные токены и стоимость в рублях."""

    date: str            # ISO 'YYYY-MM-DD'
    tokens: int
    cost_rub: float = 0.0


@dataclass
class Anomaly:
    """День с аномально высоким расходом."""

    date: str
    tokens: int
    cost_rub: float
    deviation_sigma: float   # на сколько σ день выше среднего


@dataclass
class SpendForecast:
    """Прогноз расхода: точечная оценка + коридор + диагнозы."""

    baseline_daily_tokens: float
    sigma_daily_tokens: float
    projected_month_tokens: float
    projected_month_cost_rub: float
    low_month_tokens: float
    high_month_tokens: float
    trend: str                    # "rising" | "falling" | "flat"
    trend_pct: float              # насколько вторая половина отличается от первой
    anomalies: list[Anomaly] = field(default_factory=list)

    def block(self) -> str:
        """Короткий текстовый блок для вставки в статус/отчёт."""
        lines = [
            f"[forecast] база {self.baseline_daily_tokens:.0f} ткн/день "
            f"(±{self.sigma_daily_tokens:.0f}), месяц ≈ "
            f"{self.projected_month_tokens:.0f} ткн",
        ]
        if self.projected_month_cost_rub > 0:
            lines.append(
                f"[forecast] бюджет месяца ≈ {self.projected_month_cost_rub:.0f} ₽"
                f"  (коридор {self.low_month_tokens:.0f}–{self.high_month_tokens:.0f} ткн)"
            )
        lines.append(f"[forecast] тренд: {self.trend} ({self.trend_pct:+.0%})")
        for a in self.anomalies[-3:]:
            lines.append(
                f"[forecast] ⚠ аномалия {a.date}: {a.tokens} ткн "
                f"(+{a.deviation_sigma:.1f}σ)"
            )
        return "\n".join(lines)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return var ** 0.5


def forecast_spend(
    history: list[UsageDay],
    window: int = 14,
    anomaly_z: float = 2.0,
    days_in_month: int = 30,
    confidence_sigma: float = 1.96,
) -> SpendForecast | None:
    """Прогноз месячного расхода по дневной истории.

    Args:
        history: дневные точки, порядок не важен (сортируются внутри).
        window: сколько последних дней считать «обычным днём».
        anomaly_z: порог в сигмах для пометки дня как аномалии.
        days_in_month: горизонт проекции.
        confidence_sigma: полуширина коридора (1.96 ≈ 95%).

    Returns None, если история пуста.
    """
    if not history:
        return None
    ordered = sorted(history, key=lambda d: d.date)

    tail_days = ordered[-window:]
    tail = [float(d.tokens) for d in tail_days]
    baseline = _mean(tail)

    # σ по остаткам от базовой линии на всей хвостовой выборке —
    # так коридор отражает реальную волатильность, а не сглаженную.
    residuals = [x - baseline for x in tail]
    sigma = _std(residuals)

    # Тренд: сравниваем средние двух половин истории (минимум 4 дня).
    trend, trend_pct = "flat", 0.0
    if len(ordered) >= 4:
        half = len(ordered) // 2
        first = _mean([float(d.tokens) for d in ordered[:half]])
        second = _mean([float(d.tokens) for d in ordered[half:]])
        if first > 0:
            trend_pct = second / first - 1.0
            if trend_pct > 0.15:
                trend = "rising"
            elif trend_pct < -0.15:
                trend = "falling"

    anomalies = []
    all_tokens = [float(d.tokens) for d in ordered]
    mu_all, sigma_all = _mean(all_tokens), _std(all_tokens)
    if sigma_all > 0:
        for d in ordered:
            dev = ((d.tokens - mu_all) / sigma_all)
            if dev > anomaly_z:
                anomalies.append(Anomaly(d.date, d.tokens, d.cost_rub, dev))

    return SpendForecast(
        baseline_daily_tokens=baseline,
        sigma_daily_tokens=sigma,
        projected_month_tokens=baseline * days_in_month,
        projected_month_cost_rub=(
            _mean([d.cost_rub for d in tail_days]) * days_in_month
        ),
        low_month_tokens=max(0.0, (baseline - confidence_sigma * sigma)) * days_in_month,
        high_month_tokens=(baseline + confidence_sigma * sigma) * days_in_month,
        trend=trend,
        trend_pct=trend_pct,
        anomalies=anomalies,
    )


def budget_runway(
    history: list[UsageDay], budget_rub: float, window: int = 14,
) -> tuple[int, float] | None:
    """За сколько дней вылетим в бюджет ``budget_rub`` при текущем темпе.

    Returns (дней до исчерпания, суровый темп ₽/день) or None.
    """
    if budget_rub <= 0 or not history:
        return None
    ordered = sorted(history, key=lambda d: d.date)[-window:]
    daily = _mean([d.cost_rub for d in ordered])
    if daily <= 0:
        return None
    return int(budget_rub // daily), daily


# ── Convenience ─────────────────────────────────────────────────────


def forecast_from_pairs(
    pairs: list[tuple[str, int, float]],
    window: int = 14,
    anomaly_z: float = 2.0,
    days_in_month: int = 30,
    confidence_sigma: float = 1.96,
) -> SpendForecast | None:
    """Обёртка над forecast_spend для сырых (date, tokens, cost) кортежей."""
    return forecast_spend(
        [UsageDay(d, t, c) for d, t, c in pairs],
        window=window,
        anomaly_z=anomaly_z,
        days_in_month=days_in_month,
        confidence_sigma=confidence_sigma,
    )
