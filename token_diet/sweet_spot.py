"""SweetSpot — сканер «самых сладких» возможностей.

Идея: найти бумаги, где стечение факторов даёт лучший риск/доходность
именно СЕЙЧАС (по-честному, по данным, без гаданий):

  1. Откат vs 20-дневное среднее — глубокий откат = запас до среднего
  2. Сентимент толпы (Пульс) — контрарный сигнал: паника = сладость
  3. Тейлвинд золота для золотодобытчиков (PLZL, SELG)
  4. Стабилизация дня — бумага, которая перестала рушиться

Счёт «сладости» (0..100):
  40 — запас отскока (глубина отката vs SMA20)
  30 — контрарный сентимент (чем паникливее толпа — тем слаще)
  20 — золотой тейлвинд (для золотодобытчиков при золоте > $4000)
  10 — стабилизация дня (не рушится прямо сейчас)

Всё на stdlib + существующих модулях (TinkoffInvest, pulse_sentiment).
Никаких LLM-вызовов — только данные и эвристики.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════

GOLD_PROXIES: dict[str, str] = {
    "PLZL": "Полюс",
    "SELG": "Solidcore (бывш. Polymetal)",
}

DEFAULT_BASKET: list[str] = [
    "PLZL", "SELG", "GMKN", "SBER", "OZON", "YDEX", "ALRS",
]


@dataclass
class SweetSpot:
    """Результат скана одной бумаги."""

    ticker: str
    name: str
    price: float
    day_change_pct: float
    vs_sma20_pct: float
    sentiment_score: float
    sentiment_signal: str
    gold_proxy: bool
    sweetness: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "price": self.price,
            "day_change_pct": round(self.day_change_pct, 2),
            "vs_sma20_pct": round(self.vs_sma20_pct, 2),
            "sentiment_score": self.sentiment_score,
            "sentiment_signal": self.sentiment_signal,
            "gold_proxy": self.gold_proxy,
            "sweetness": round(self.sweetness, 1),
            "note": self.note,
        }


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sma(values: list[float], n: int) -> float:
    if not values:
        return 0.0
    window = values[-n:]
    return sum(window) / len(window)


def _note_for(spot: SweetSpot) -> str:
    bits = []
    if spot.vs_sma20_pct < -3:
        bits.append(f"откат {spot.vs_sma20_pct:.0f}% от SMA20 — запас до среднего")
    if spot.sentiment_score < -0.1:
        bits.append("толпа негативна (контрарный)")
    elif spot.sentiment_score > 0.2:
        bits.append("толпа позитивна")
    if spot.gold_proxy:
        bits.append("золото на максимумах — тейлвинд")
    if spot.day_change_pct > 0:
        bits.append(f"день +{spot.day_change_pct:.1f}%")
    elif spot.day_change_pct < -4:
        bits.append(f"день {spot.day_change_pct:.1f}% — осторожно, ещё летит")
    return "; ".join(bits) if bits else "нейтрально"


def scan(
    tickers: list[str] | None = None,
    gold_price: float | None = None,
    use_sentiment: bool = True,
) -> list[SweetSpot]:
    """Сканировать корзину и вернуть список, отсортированный по сладости.

    gold_price: текущая цена золота XAU (если None — не учитывать тейлвинд).
    """
    from .tinkoff_invest import TinkoffInvest

    tickers = tickers or DEFAULT_BASKET
    inv = TinkoffInvest()
    results: list[SweetSpot] = []

    for t in tickers:
        try:
            candles = inv.get_candles(t, days=30)
        except Exception:
            continue
        if not candles or len(candles) < 2:
            continue

        closes = [c.close for c in candles]
        price = closes[-1]
        prev_close = closes[-2]
        day_change = (price / prev_close - 1) * 100 if prev_close else 0.0
        sma20 = _sma(closes, 20)
        vs_sma20 = (price / sma20 - 1) * 100 if sma20 else 0.0

        gold_proxy = t in GOLD_PROXIES
        s_score, s_signal = 0.0, "neutral"
        if use_sentiment:
            try:
                from .pulse_reader import pulse_sentiment

                s = pulse_sentiment(t, limit=12)
                s_score = float(s.get("score") or 0.0)
                s_signal = str(s.get("signal") or "neutral")
            except Exception:
                pass

        # ── Счёт сладости (0..100) ──────────────────────────────────────
        # 40: глубина отката vs SMA20 (до -12% даёт максимум)
        pull = _clamp(-vs_sma20, 0.0, 12.0)
        pts_pull = 40 * (pull / 12.0)
        # 30: контрарный сентимент (score=-1 → 30; score=+1 → 0)
        pts_sent = 30 * (1.0 - _clamp(s_score, -1.0, 1.0)) / 2.0
        # 20: золотой тейлвинд
        pts_gold = 20 if (gold_proxy and gold_price and gold_price > 4000) else 0
        # 10: стабилизация дня
        pts_day = 10 if day_change > -1.0 else (4 if day_change > -4.0 else 0)

        sweetness = pts_pull + pts_sent + pts_gold + pts_day

        spot = SweetSpot(
            ticker=t,
            name=GOLD_PROXIES.get(t, t),
            price=price,
            day_change_pct=day_change,
            vs_sma20_pct=vs_sma20,
            sentiment_score=s_score,
            sentiment_signal=s_signal,
            gold_proxy=gold_proxy,
            sweetness=round(sweetness, 1),
            note=_note_for(SweetSpot(  # type: ignore[arg-type]
                t, GOLD_PROXIES.get(t, t), price, day_change,
                vs_sma20, s_score, s_signal, gold_proxy, sweetness,
            )),
        )
        results.append(spot)

    results.sort(key=lambda s: s.sweetness, reverse=True)
    return results


def report(spots: list[SweetSpot]) -> str:
    """Человекочитаемый отчёт «самое сладкое → пресное»."""
    if not spots:
        return "[SweetSpot] корзина пуста или данные недоступны"
    lines = ["🍭 SweetSpot — самое сладкое сегодня:"]
    for i, s in enumerate(spots, 1):
        emoji = "🍭" if s.sweetness >= 60 else ("🍬" if s.sweetness >= 40 else "🫗")
        lines.append(
            f"{emoji} {i}. {s.ticker} ({s.name}) — сладость {s.sweetness:.0f}/100"
        )
        lines.append(
            f"      цена {s.price:.2f} | день {s.day_change_pct:+.1f}% | "
            f"vs SMA20 {s.vs_sma20_pct:+.1f}% | "
            f"толпа: {s.sentiment_signal} ({s.sentiment_score:+.2f})"
        )
        if s.note:
            lines.append(f"      💡 {s.note}")
    return "\n".join(lines)
