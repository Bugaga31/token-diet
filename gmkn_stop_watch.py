"""Программный стоп-лосс для позиции GMKN.

Биржевой стоп (StopOrdersService) заблокирован: брокер требует подтверждение
рисков в приложении (ошибка 30240). Поэтому стоп реализован программно:
демон раз в N минут проверяет цену и продаёт позицию по рынку при пробое.

Запуск:  python3 gmkn_stop_watch.py
Остановка: tmux kill-session -t gmknstop
"""

import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

from token_diet.tinkoff_invest import TinkoffInvest

TICKER = "GMKN"
STOP_PRICE = 114.55     # -5% от входа 120.57
QTY_LOTS = 2            # 20 акций (2 лота)
INTERVAL_SEC = 180      # 3 минуты
LOG = "/tmp/gmkn_stop.log"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def check_and_act(inv: TinkoffInvest) -> bool:
    q = None
    for attempt in range(3):
        q = inv.get_quote(TICKER)
        if q is not None:
            break
        time.sleep(5)
    if q is None:
        log(f"{TICKER}: нет котировки (3 попытки)")
        return False
    price = q.price
    log(f"{TICKER} {price} (стоп {STOP_PRICE})")
    if price <= STOP_PRICE:
        log(f"⚠️ STOP HIT: {price} ≤ {STOP_PRICE} — продаю {QTY_LOTS} лотов")
        r = inv.post_order(TICKER, quantity=QTY_LOTS, direction="sell", order_type="market")
        log(f"ордер: {r}")
        return True
    return False


def main() -> None:
    log("стоп-вотчдог GMKN запущен")
    inv = TinkoffInvest()
    while True:
        try:
            if check_and_act(inv):
                log("позиция закрыта по стопу, завершаю работу")
                return
        except Exception as e:
            log(f"ошибка: {e}")
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
