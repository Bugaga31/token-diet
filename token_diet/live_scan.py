"""Live Scanner — находить бумаги, которые РАСТУТ прямо сейчас.

ЗАКОН ГЕНЕРАЛА: «Покупай только то, что ЗНАЕШЬ что вырастет».
Надежда — не сигнал. Рост с объёмом — сигнал.

Этот модуль — живой радар по всему ликвидному рынку:

1. Для каждой бумаги берём свечу СЕГОДНЯ (открытие → сейчас):
   - изменение % за день (рост?),
   - объём против среднего за 5 дней (растёт ли объём?),
   - позиция в дневном диапазоне (близко к хаю = сила).
2. Скоринг 0-100: рост + объём + сила + направление стакана.
3. Возвращает только тех, кто РАСТЁТ С ОБЪЁМОМ — кандидатов на вход.

Правила входа (минимум 2 из 3):
  1) цена растёт за день с объёмом выше среднего,
  2) цена у верхней части дневного диапазона (>= 60%),
  3) стакан: перевес покупателей.

Всё — чистая математика на свечах, 0 LLM-вызовов.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Ликвидные бумаги MOEX — база для сканирования
LIQUID_TICKERS: list[str] = [
    "SBER", "GAZP", "LKOH", "YDEX", "OZON", "NLMK", "MAGN", "CHMF",
    "ROSN", "TATN", "VKCO", "AFLT", "MTSS", "GMKN", "PLZL", "VTBR",
    "RUAL", "SNGS", "NVTK", "ALRS", "AFKS", "SMLT", "MOEX", "PHOR",
    "IRAO", "FIVE", "TCSG", "BANE", "MVID", "ASTR", "SOFL", "HHRU",
]


@dataclass
class ScanResult:
    ticker: str
    price: float
    change_pct: float            # изменение за день, %
    volume_ratio: float          # объём сегодня / средний за 5 дней
    day_position: float          # позиция в диапазоне дня 0..1 (1 = у хая)
    orderbook_lean: float | None # +1 быки / -1 медведи / None нет данных
    score: float                 # итоговый скоринг 0-100
    signals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "change_pct": self.change_pct,
            "volume_ratio": round(self.volume_ratio, 2),
            "day_position": round(self.day_position, 2),
            "orderbook_lean": self.orderbook_lean,
            "score": round(self.score, 1),
            "signals": self.signals,
        }


def _day_change(candles: list) -> tuple[float, float, float] | None:
    """Изменение за день, объём сегодня и средний объём за 5 дней."""
    if not candles:
        return None
    today = candles[-1]
    if today.volume <= 0:
        return None
    prev_close = candles[-2].close if len(candles) >= 2 else today.open
    change = (today.close - prev_close) / prev_close * 100 if prev_close else 0.0
    avg_vol = sum(c.volume for c in candles[-6:-1]) / 5 if len(candles) >= 6 else today.volume
    vol_ratio = today.volume / avg_vol if avg_vol else 1.0
    return change, today.volume, vol_ratio


def _day_position(candles: list) -> float:
    """Где цена сейчас в дневном диапазоне: 0 = у лоу, 1 = у хая."""
    if not candles:
        return 0.5
    today = candles[-1]
    hi, lo = today.high, today.low
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (today.close - lo) / (hi - lo)))


def _orderbook_lean(orderbook: dict | None) -> float | None:
    """Перевес стакана: суммарный объём покупок vs продаж в топ-10."""
    if not orderbook:
        return None
    bids = sum(b.get("quantity", 0) for b in orderbook.get("bids", [])[:10])
    asks = sum(a.get("quantity", 0) for a in orderbook.get("asks", [])[:10])
    total = bids + asks
    if total == 0:
        return None
    return round((bids - asks) / total, 2)  # +1 = чистые быки


def scan_market(
    tickers: list[str] | None = None,
    min_change: float = 0.3,
    min_volume_ratio: float = 1.0,
    max_results: int = 10,
    respect_geopolitics: bool = True,
) -> list[ScanResult]:
    """Просканировать рынок и вернуть только РАСТУЩИЕ с объёмом бумаги.

    Args:
        tickers: список тикеров (по умолчанию LIQUID_TICKERS).
        min_change: минимальный рост за день, %.
        min_volume_ratio: минимальное отношение объёма к среднему.
        max_results: сколько лучших вернуть.
        respect_geopolitics: если True и геополитический фон RED —
            возвращать пусто (урок генерала: геополитику учитывать).
    """
    if respect_geopolitics:
        try:
            from .geopolitics import geo_verdict
        except ImportError:
            from token_diet.geopolitics import geo_verdict
        try:
            geo = geo_verdict(limit_per_topic=2)
            if geo.verdict == "RED":
                # Урок генерала: Лавров сказал «перемирия не будет» → весь рынок
                # красный. В такой фон входы не делаем, даже при BUY-сигнале.
                return []
        except Exception:
            pass  # геополитика недоступна — не блокируем сканер

    try:
        from .tinkoff_invest import TinkoffInvest
    except ImportError:
        from token_diet.tinkoff_invest import TinkoffInvest

    inv = TinkoffInvest()
    results: list[ScanResult] = []

    for ticker in (tickers or LIQUID_TICKERS):
        try:
            candles = inv.get_candles(ticker, days=7)
            if len(candles) < 6:
                continue
            day = _day_change(candles)
            if not day:
                continue
            change, _vol, vol_ratio = day

            # Фильтр: бумага должна РАСТИ и с объёмом
            if change < min_change or vol_ratio < min_volume_ratio:
                continue

            pos = _day_position(candles)
            ob = inv.get_orderbook(ticker, depth=10)
            lean = _orderbook_lean(ob)

            # Скоринг 0-100
            score = 0.0
            score += min(change / 2.0, 1.0) * 35          # рост: до 35
            score += min(vol_ratio / 3.0, 1.0) * 30       # объём: до 30
            score += pos * 20                             # сила у хая: до 20
            if lean is not None:
                score += max(lean, 0.0) * 15              # стакан: до 15
            else:
                score += 7.5  # нет данных — нейтрально

            signals = []
            if change >= 1.0:
                signals.append(f"рост {change:+.1f}%")
            elif change >= 0.3:
                signals.append(f"рост {change:+.1f}%")
            if vol_ratio >= 1.5:
                signals.append(f"объём x{vol_ratio:.1f}")
            if pos >= 0.6:
                signals.append("у хая дня")
            if lean and lean >= 0.2:
                signals.append("стакан бычий")

            results.append(ScanResult(
                ticker=ticker, price=candles[-1].close,
                change_pct=round(change, 2), volume_ratio=vol_ratio,
                day_position=pos, orderbook_lean=lean, score=score,
                signals=signals,
            ))
        except Exception:
            continue  # тикер пропускаем, не роняем сканер

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:max_results]


def run_scan() -> list[dict]:
    """CLI-обёртка: просканировать рынок, вернуть топ-10 в dict."""
    results = scan_market()
    return [r.as_dict() for r in results]


def _print_geo_block() -> None:
    try:
        from .geopolitics import market_context_block
    except ImportError:
        from token_diet.geopolitics import market_context_block
    try:
        print(market_context_block())
    except Exception:
        pass


if __name__ == "__main__":
    _print_geo_block()
    print()
    print(f"{'ТИКЕР':<7}{'ЦЕНА':>9}{'ДЕНЬ%':>8}{'ОБЪЁМ':>8}{'УХАЯ':>7}{'СКОР':>6}  СИГНАЛЫ")
    print("-" * 78)
    for r in scan_market():
        lean = f"{r.orderbook_lean:+.2f}" if r.orderbook_lean is not None else "  -"
        print(f"{r.ticker:<7}{r.price:>9.1f}{r.change_pct:>+7.2f}%{r.volume_ratio:>7.1f}x"
              f"{r.day_position:>6.0%}{r.score:>6.1f}  {', '.join(r.signals)}")
