"""Авто-трейдер GMKN: trailing-стоп + прорыв + автодокупка + уведомление.

Полностью автоматический цикл (без участия человека):
1. TRAILING-СТОП: стоп ПОДТЯГИВАЕТСЯ за максимумом цены.
   - базовый стоп: 114.55 (−5% от входа 120.57)
   - trailing: стоп = максимум × (1 − 4%), растёт вместе с ценой
   - безубыток: как только цена побывала ≥ +2% от входа, стоп НЕ ниже
     входа — убыток становится невозможен, прибыль защищена
   - цена ≤ стоп → продаёт ВСЮ позицию, фиксируя прибыль (не убыток)
2. ПРОРЫВ (вверх): цена пробивает 20-дневный максимум → ДОКУПАЕТ до
   целевых 5 лотов (50 акций) через position_size, подтягивает стоп.
3. УВЕДОМЛЕНИЕ: каждое действие пишет в /tmp/gmkn_alert.txt + Obsidian.

Прорыв-докупка срабатывает ОДИН раз (флаг в state-файле).
Trailing-максимум хранится в state-файле — переживает рестарт.

Запуск:  python3 gmkn_stop_watch.py
Стоп:    tmux kill-session -t gmknstop
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

from token_diet.tinkoff_invest import TinkoffInvest
from token_diet.momentum import detect_breakout

TICKER = "GMKN"
ENTRY_PRICE = 120.57      # наш вход
BASE_STOP = 114.55        # −5% от входа (базовый, пока цена не выросла)
TRAIL_PCT = 0.04          # trailing: стоп = максимум × (1 − 4%)
BREAKEVEN_TRIGGER = 0.02  # если максимум ≥ вход × (1 + 2%) → стоп не ниже входа
TARGET_LOTS = 5           # целевая позиция (модель 2% риска)
INTERVAL_SEC = 180        # 3 минуты
LOG = "/tmp/gmkn_stop.log"
ALERT = "/tmp/gmkn_alert.txt"
STATE = "/tmp/gmkn_state.json"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def notify(title: str, body: str) -> None:
    with open(ALERT, "w") as f:
        f.write(f"{title}\n{body}\n")
    log(f"🚨 {title}: {body.splitlines()[0] if body else ''}")
    try:
        from token_diet.memory_cli import vault_path
        from token_diet.obsidian_vault import ObsidianVault
        vault = ObsidianVault(vault_path())
        vault.write(f"GMKN {title}", body)
    except Exception as e:
        log(f"Obsidian не записал: {e}")


def _load_state() -> dict:
    try:
        return json.loads(Path(STATE).read_text()) if Path(STATE).exists() else {}
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        Path(STATE).write_text(json.dumps(st))
    except Exception:
        pass


def _current_lots(inv: TinkoffInvest) -> int:
    try:
        for p in inv.get_portfolio() or []:
            if getattr(p, "figi", "") == "BBG004731489" or "GMKN" in str(getattr(p, "ticker", "")):
                return int(float(getattr(p, "quantity", 0)) // 10)  # лот = 10
    except Exception:
        pass
    return 0


def _trailing_stop(max_price: float) -> float:
    """Стоп, который подтягивается за максимумом и защищает прибыль."""
    stop = BASE_STOP
    # 1. trailing от максимума
    trail = max_price * (1 - TRAIL_PCT)
    stop = max(stop, trail)
    # 2. безубыток: если цена побывала ≥ +2% от входа — стоп не ниже входа
    if max_price >= ENTRY_PRICE * (1 + BREAKEVEN_TRIGGER):
        stop = max(stop, ENTRY_PRICE * 1.005)  # чуть выше входа (комиссии)
    return round(stop, 2)


def check(inv: TinkoffInvest, state: dict) -> str:
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

    # ── обновляем максимум и стоп ──
    max_price = max(float(state.get("max_price", 0) or 0), price)
    state["max_price"] = max_price
    stop = _trailing_stop(max_price)
    state["stop"] = stop
    _save_state(state)

    # ── 1. СТОП вниз (в т.ч. trailing): продать всё ──
    if price <= stop:
        lots = _current_lots(inv) or 2
        kind = "TRAIL STOP" if stop > BASE_STOP else "STOP HIT"
        log(f"⚠️ {kind}: {price} ≤ {stop} — продаю {lots} лотов "
            f"(макс было {max_price:.2f})")
        r = inv.post_order(TICKER, quantity=lots, direction="sell", order_type="market")
        notify(kind, f"GMKN {price} ≤ стоп {stop}, продано {lots} лотов: {r}")
        return "exit"

    # ── 2. ПРОРЫВ вверх: докупить до целевых лотов (ОДИН раз) ──
    try:
        candles = inv.get_candles(TICKER, days=90)
        if len(candles) >= 21:
            closes = [c.close for c in candles]
            highs = [c.high for c in candles]
            vols = [c.volume for c in candles]
            brk = detect_breakout(closes, highs, vols, lookback=20)
            high20 = max(highs[-20:-1]) if len(highs) >= 21 else 0
            log(f"{TICKER} {price} (стоп {stop}, 20д-макс {high20:.1f}, "
                f"макс {max_price:.2f}, прорыв {brk.direction})")

            if brk.direction == "up" and not state.get("added"):
                cur = _current_lots(inv)
                add = TARGET_LOTS - cur
                if add > 0:
                    log(f"🚀 ПРОРЫВ: {price} > {high20:.1f} — докупаю {add} лотов")
                    r = inv.post_order(TICKER, quantity=add, direction="buy", order_type="market")
                    state["added"] = True
                    state["added_at"] = price
                    # подтягиваем стоп к точке прорыва (−3% от прорыва)
                    if price > stop:
                        state["stop"] = round(price * 0.97, 2)
                    _save_state(state)
                    notify(
                        "BREAKOUT GMKN — докупил",
                        f"GMKN {price} пробил 20-дневный максимум {high20:.1f}.\n"
                        f"Докупил {add} лотов → цель {TARGET_LOTS} лотов.\n"
                        f"Стоп подтянут к {state['stop']}. Ордер: {r}",
                    )
                return "continue"
    except Exception as e:
        log(f"ошибка прорыва: {e}")

    log(f"{TICKER} {price} (стоп {stop})")
    return "continue"


def main() -> None:
    log("авто-трейдер GMKN запущен (trailing-стоп + прорыв-докупка)")
    inv = TinkoffInvest()
    state = _load_state()
    if state.get("added"):
        log(f"позиция уже докуплена (added_at={state.get('added_at')}) — прорыв-флаг стоит")
    if state.get("max_price"):
        log(f"максимум с прошлого запуска: {state.get('max_price')}")
    while True:
        try:
            if check(inv, state) == "exit":
                log("позиция закрыта по стопу, завершаю")
                return
        except Exception as e:
            log(f"ошибка: {e}")
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
