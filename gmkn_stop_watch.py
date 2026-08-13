"""Программный стоп + прорыв-вотчдог для позиции GMKN.

Следит за позицией с двух сторон:
1. СТОП (вниз): цена <= 114.55 → продаёт 2 лота по рынку (защита капитала).
2. ПРОРЫВ (вверх): цена пробивает 20-дневный максимум → пишет тревогу
   BREAKOUT (сигнал на добавление позиции). Сам НЕ покупает — добавление
   решает управляющий с расчётом размера, но тревога приходит мгновенно.

Биржевой стоп заблокирован (30240), поэтому стоп программный.
Запуск:  python3 gmkn_stop_watch.py
Стоп:    tmux kill-session -t gmknstop
"""

import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

from token_diet.tinkoff_invest import TinkoffInvest
from token_diet.momentum import detect_breakout

TICKER = "GMKN"
STOP_PRICE = 114.55     # −5% от входа 120.57
QTY_LOTS = 2            # 20 акций (2 лота)
INTERVAL_SEC = 180      # 3 минуты
LOG = "/tmp/gmkn_stop.log"
ALERT = "/tmp/gmkn_alert.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def alert(title: str, body: str) -> None:
    with open(ALERT, "w") as f:
        f.write(f"{title}\n{body}\n")
    log(f"🚨 {title}")


def check(inv: TinkoffInvest) -> str:
    """Одна проверка. Возвращает 'exit' если позиция закрыта по стопу."""
    q = None
    for _ in range(3):
        q = inv.get_quote(TICKER)
        if q is not None:
            break
        time.sleep(5)
    if q is None:
        log(f"{TICKER}: нет котировки (3 попытки)")
        return "continue"
    price = q.price

    # ── 1. Стоп вниз ──
    if price <= STOP_PRICE:
        log(f"⚠️ STOP HIT: {price} ≤ {STOP_PRICE} — продаю {QTY_LOTS} лотов")
        r = inv.post_order(TICKER, quantity=QTY_LOTS, direction="sell", order_type="market")
        log(f"ордер: {r}")
        alert("STOP HIT", f"GMKN {price} ≤ {STOP_PRICE}, продано: {r}")
        return "exit"

    # ── 2. Прорыв вверх ──
    try:
        candles = inv.get_candles(TICKER, days=90)
        if len(candles) >= 21:
            closes = [c.close for c in candles]
            highs = [c.high for c in candles]
            vols = [c.volume for c in candles]
            brk = detect_breakout(closes, highs, vols, lookback=20)
            high20 = max(highs[-20:-1]) if len(highs) >= 21 else 0
            log(f"{TICKER} {price} (стоп {STOP_PRICE}, 20д-макс {high20:.1f}, прорыв {brk.direction})")
            if brk.direction == "up":
                alert("BREAKOUT GMKN",
                      f"GMKN {price} пробил 20-дневный максимум {high20:.1f}. "
                      f"Сигнал на добавление позиции (план: до 5 лотов).")
            return "continue"
    except Exception as e:
        log(f"ошибка прорыва: {e}")

    log(f"{TICKER} {price} (стоп {STOP_PRICE})")
    return "continue"


def main() -> None:
    log("стоп+прорыв вотчдог GMKN запущен")
    inv = TinkoffInvest()
    while True:
        try:
            if check(inv) == "exit":
                log("позиция закрыта по стопу, завершаю")
                return
        except Exception as e:
            log(f"ошибка: {e}")
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
