"""MorningBrief — единый утренний пакет по позиции.

Оркестратор: соединяет все модули token-diet в один компактный брифинг,
готовый скормить любой LLM (умнее при меньшем числе токенов):

1. Стакан/позиция   — market_guard.snapshot_block (цена, P&L, стены, золото)
2. Настроение толпы — pulse_reader.pulse_sentiment (сигнал + топ посты)
3. Веб-новости      — webpilot.ask_web (evidence по вопросу про драйверы)
4. Сладкие места    — sweet_spot.scan (рейтинг корзины)
5. План дня         — детерминированные уровни/триггеры + готовый промпт
                     Plan-and-Solve для любой нейронки

CLI: python3 -m token_diet.morning_brief PLZL
Всё с фолбэками: если источник недоступен — секция пропускается, пакет жив.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MorningBrief:
    ticker: str
    sections: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sections is None:
            self.sections = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "sections": self.sections,
            "total_chars": sum(len(v) for v in self.sections.values()),
        }

    def block(self) -> str:
        """Компактный единый текст для любой LLM."""
        parts = [f"=== УТРЕННИЙ БРИФИНГ: {self.ticker} ==="]
        for name in ("snapshot", "sentiment", "web", "sweet", "plan"):
            if self.sections.get(name):
                parts.append(f"\n-- {name.upper()} --\n{self.sections[name]}")
        if not self.sections:
            parts.append("(все источники недоступны)")
        return "\n".join(parts)


# Реальные позиции: entry/stop/цель для честного P&L в брифинге
# (ключ — тикер; значения — параметры GuardConfig)
POSITIONS: dict[str, dict] = {
    "GMKN": {"entry": 120.57, "stop": 117.0, "target": 128.9},
    "PLZL": {"entry": 1339.6, "stop": 1290.0, "target": 1400.0},
}

# Драйверы по тикеру: что реально двигает бумагу (для веб-секции)
TICKER_DRIVERS: dict[str, str] = {
    "PLZL": "золото XAU/USD",
    "GMKN": "палладий, никель и медь на LME",
    "SBER": "ключевая ставка ЦБ и банковский сектор",
    "GAZP": "цены на газ и нефть",
    "YDEX": "IT-сектор и отчётность по рекламе",
}


def _sec_snapshot(ticker: str, gold_price: float | None) -> str:
    try:
        from .market_guard import GuardConfig, snapshot_block

        # Универсальный конфиг: берём тикер из брифинга, а не хардкод PLZL.
        # figi=None → get_quote сам резолвит FIGI по тикеру через find_figi.
        # entry/stop/target из карты реальных позиций → честный P&L.
        t = ticker.upper()
        pos = POSITIONS.get(t, {})
        cfg = GuardConfig(ticker=t, figi=None, **pos)
        return snapshot_block(cfg)
    except Exception as e:
        return f"(стакан недоступен: {type(e).__name__})"


def _sec_sentiment(ticker: str) -> str:
    try:
        from .pulse_reader import pulse_sentiment

        s = pulse_sentiment(ticker, limit=12)
        if not s.get("sample_size"):
            return "(Пульс: постов нет)"
        lines = [
            f"Сигнал: {s['signal']} (score {s['score']:+.2f}) | "
            f"быки {s['bullish_pct']}% / медведи {s['bearish_pct']}% | n={s['sample_size']}"
        ]
        for p in s.get("posts", [])[:2]:
            lines.append(f"  👍{p['likes']} @{p['nickname']}: {(p['text'] or '')[:100]}")
        if s.get("contrarian"):
            lines.append(f"  ⚡ {s['contrarian']}")
        return "\n".join(lines)
    except Exception as e:
        return f"(Пульс недоступен: {type(e).__name__})"


def _sec_web(ticker: str, gold_price: float | None) -> str:
    try:
        from .webpilot import ask_web

        driver = TICKER_DRIVERS.get(ticker.upper(), "рыночная конъюнктура")
        q = f"последние новости и прогноз по акции {ticker}: влияние {driver} на цену в 2026"
        r = ask_web(q, max_steps=3, max_chars=1200, timeout=10)
        if not r.evidence:
            return "(веб: свидетельств нет)"
        return f"Прочитано страниц: {r.pages_read}\n{r.evidence[:1200]}"
    except Exception as e:
        return f"(веб недоступен: {type(e).__name__})"


def _sec_sweet(gold_price: float | None) -> str:
    try:
        from .sweet_spot import scan

        spots = scan(gold_price=gold_price)
        lines = []
        for s in spots[:3]:
            lines.append(
                f"{s.ticker}: сладость {s.sweetness:.0f}/100 | "
                f"день {s.day_change_pct:+.1f}% | vs SMA20 {s.vs_sma20_pct:+.1f}% | "
                f"толпа {s.sentiment_signal}"
            )
        return "\n".join(lines) or "(сладкое: нет данных)"
    except Exception as e:
        return f"(сладкое недоступно: {type(e).__name__})"


def _sec_plan(ticker: str) -> str:
    """Детерминированный план дня: уровни, триггеры, вопрос для нейронки."""
    try:
        from .reasoning_kit import plan_and_solve_prompt

        task = (
            f"Проанализируй позицию по {ticker} на сегодня: "
            f"используй стакан, настроение толпы, веб-новости и золото из брифинга. "
            f"Решение: держать / докупить / продать, с уровнями входа и выхода."
        )
        prompt = plan_and_solve_prompt(task)
        return "Триггеры: пробой сопротивления → смотрим покупки; потеря пола → выход. " \
               f"Промпт для нейронки:\n{prompt}"
    except Exception as e:
        return f"(план недоступен: {type(e).__name__})"


def brief(ticker: str = "PLZL", gold_price: float | None = None) -> MorningBrief:
    """Собрать полный утренний пакет по тикеру."""
    if gold_price is None:
        try:
            import json as _json
            import urllib.request as _ur

            req = _ur.Request(
                "https://api.gold-api.com/price/XAU",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            gold_price = float(_json.loads(_ur.urlopen(req, timeout=8).read())["price"])
        except Exception:
            gold_price = None

    sections = {
        "snapshot": _sec_snapshot(ticker, gold_price),
        "sentiment": _sec_sentiment(ticker),
        "web": _sec_web(ticker, gold_price),
        "sweet": _sec_sweet(gold_price),
        "plan": _sec_plan(ticker),
    }
    return MorningBrief(ticker=ticker, sections=sections)


def brief_block(ticker: str = "PLZL", gold_price: float | None = None) -> str:
    """Один текст-пакет (удобно для ленты/промпта)."""
    return brief(ticker, gold_price).block()


__all__ = ["MorningBrief", "brief", "brief_block"]


if __name__ == "__main__":  # python3 -m token_diet.morning_brief PLZL
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "PLZL"
    print(brief_block(ticker))
