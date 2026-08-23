"""Trading Brain — автономный мозг, соединяющий все органы чувств.

УРОКИ ГЕНЕРАЛА (законы, по которым мы торгуем):
1. Прибыль > привязанность. Позиция живёт, пока зарабатывает.
2. Покупать только то, что ЗНАЕШЬ что вырастет. Надежда — не сигнал.
3. Геополитику учитывать всегда. RED-фон = входов нет.

Этот модуль — единый мозг, который собирает ВСЕ источники в одно
решение о входе:

  GEOPOLITICS  → фон дня (GREEN/RED/NEUTRAL)     [урок 3]
  LIVE_SCAN    → кто растёт с объёмом сейчас      [урок 2]
  PULSE        → что говорит толпа                [урок 2]
  ORDERBOOK    → кто кого давит в стакане         [урок 2]

Правило входа (все условия одновременно):
  1) геополитика НЕ RED,
  2) бумага в топе сканера (рост с объёмом),
  3) Пульс не медвежий,
  4) стакан не показывает давление продавцов.

Решение: ENTER / WAIT / SKIP + причина. Никаких эмоций — только
проверяемые факты. Dry-run по умолчанию: ничего не покупает,
пока генерал не скажет «торгуй».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class BrainDecision:
    action: str          # ENTER / WAIT / SKIP
    ticker: str
    reason: str
    score: float = 0.0
    checks: dict = field(default_factory=dict)
    when: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "ticker": self.ticker,
            "reason": self.reason,
            "score": round(self.score, 1),
            "checks": self.checks,
            "when": self.when,
        }


def decide(
    ticker: str | None = None,
    min_score: float = 60.0,
    dry_run: bool = True,
) -> BrainDecision | list[dict]:
    """Главное решение мозга: стоит ли входить.

    Args:
        ticker: конкретная бумага. None = сканировать весь рынок.
        min_score: минимальный скор кандидата для рассмотрения.
        dry_run: True — только анализ, ничего не покупаем.
    """
    try:
        from .geopolitics import geo_verdict
        from .live_scan import scan_market
    except ImportError:
        from token_diet.geopolitics import geo_verdict
        from token_diet.live_scan import scan_market

    # ── УРОК 3: геополитика первична ──
    geo = geo_verdict(limit_per_topic=2)
    if geo.verdict == "RED":
        return BrainDecision(
            action="SKIP", ticker=ticker or "market",
            reason=f"Геополитика RED (балл {geo.score:+.2f}) — входы запрещены "
                   f"(урок генерала: Лавров/санкции двигают весь рынок вниз)",
            checks={"geopolitics": geo.verdict, "geo_score": geo.score},
        )

    # ── УРОК 2: ищем рост с объёмом ──
    results = scan_market(
        tickers=[ticker] if ticker else None,
        respect_geopolitics=False,  # уже проверили выше
    )
    if not results:
        return BrainDecision(
            action="WAIT", ticker=ticker or "market",
            reason="Ни одна бумага не растёт с объёмом прямо сейчас — "
                   "нет уверенности в росте (урок 2)",
            checks={"geopolitics": geo.verdict, "candidates": 0},
        )

    # Топ-кандидат
    cand = results[0]

    # ── УРОК 2: подтверждение Пульсом ──
    pulse = {"signal": "unknown", "score": 0.0}
    pulse_sentiment = None
    try:
        from .pulse_reader import pulse_sentiment
    except ImportError:
        try:
            from token_diet.pulse_reader import pulse_sentiment
        except Exception:
            pass
    except Exception:
        pass
    try:
        if pulse_sentiment is not None:
            pulse = pulse_sentiment(cand.ticker, limit=15)
    except Exception:
        pass  # Пульс недоступен — не блокируем, но учитываем ниже

    pulse_ok = pulse.get("signal") != "bearish"
    orderbook_ok = (cand.orderbook_lean is None) or (cand.orderbook_lean >= 0.0)

    checks = {
        "geopolitics": geo.verdict,
        "geo_score": geo.score,
        "candidate": cand.ticker,
        "cand_score": round(cand.score, 1),
        "cand_change_pct": cand.change_pct,
        "cand_volume_ratio": round(cand.volume_ratio, 2),
        "pulse": pulse.get("signal"),
        "pulse_score": round(pulse.get("score", 0.0), 2),
        "orderbook_lean": cand.orderbook_lean,
        "day_position": round(cand.day_position, 2),
    }

    # Все условия входа
    conditions = {
        "рост с объёмом": cand.score >= min_score,
        "Пульс не медвежий": pulse_ok,
        "стакан не давит": orderbook_ok,
    }
    passed = sum(1 for v in conditions.values() if v)

    if passed == 3:
        return BrainDecision(
            action="ENTER", ticker=cand.ticker,
            reason=f"ВСЕ условия: {cand.ticker} растёт +{cand.change_pct}% с объёмом "
                   f"x{cand.volume_ratio:.1f}, Пульс {pulse.get('signal')}, "
                   f"геополитика {geo.verdict}. Уверенность по уроку 2 есть.",
            score=cand.score, checks=checks,
        )
    elif passed == 2:
        return BrainDecision(
            action="WAIT", ticker=cand.ticker,
            reason=f"2/3 условий ({', '.join(k for k, v in conditions.items() if v)}) — "
                   f"не хватает полной уверенности, ждём подтверждения",
            score=cand.score, checks=checks,
        )
    else:
        return BrainDecision(
            action="SKIP", ticker=cand.ticker,
            reason="Сигналы противоречивы — рынок говорит против, не спорим",
            score=cand.score, checks=checks,
        )


def scan_all() -> list[dict]:
    """Просканировать рынок и вернуть решение по каждому топ-кандидату."""
    from .live_scan import scan_market
    results = scan_market()
    out = []
    for r in results[:5]:
        d = decide(ticker=r.ticker)
        out.append(d.as_dict())
    return out


if __name__ == "__main__":
    print("🧠 TRADING BRAIN — решение по рынку")
    print("=" * 64)
    d = decide()
    print(f"ДЕЙСТВИЕ: {d.action}")
    print(f"ПРИЧИНА:  {d.reason}")
    print(f"СКОР:     {d.score}")
    if d.checks:
        print("\nПРОВЕРКИ:")
        for k, v in d.checks.items():
            print(f"  {k}: {v}")
