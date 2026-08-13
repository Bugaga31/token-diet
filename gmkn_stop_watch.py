"""Авто-трейдер GMKN: стоп + прорыв + автодокупка + уведомление.

Полностью автоматический цикл (без участия человека):
1. СТОП (вниз): цена <= 114.55 → продаёт ВСЮ позицию по рынку.
2. ПРОРЫВ (вверх): цена пробивает 20-дневный максимум → ДОКУПАЕТ до целевых
   5 лотов (50 акций) через position_size, подтягивает стоп к точке прорыва.
3. УВЕДОМЛЕНИЕ: каждое действие пишет в /tmp/gmkn_alert.txt + Obsidian.

Прорыв-докупка срабатывает ОДИН раз (флаг в state-файле), чтобы не
покупать повторно на каждом цикле.

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
STOP_PRICE = 114.55     # −5% от входа 120.57
TARGET_LOTS = 5         # целевая позиция (модель 2% риска)
INTERVAL_SEC = 180      # 3 минуты
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
    # пишем в Obsidian (память) — увижу при следующем обращении
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
    """Сколько лотов GMKN сейчас в портфеле."""
    try:
        for p in inv.get_portfolio() or []:
            if getattr(p, "figi", "") == "BBG004731489" or "GMKN" in str(getattr(p, "ticker", "")):
                return int(float(getattr(p, "quantity", 0)) // 10)  # лот = 10
    except Exception:
        pass
    return 0


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

    # ── 1. СТОП вниз: продать всё ──
    if price <= STOP_PRICE:
        lots = _current_lots(inv) or 2
        log(f"⚠️ STOP HIT: {price} ≤ {STOP_PRICE} — продаю {lots} лотов")
        r = inv.post_order(TICKER, quantity=lots, direction="sell", order_type="market")
        notify("STOP HIT", f"GMKN {price} ≤ {STOP_PRICE}, продано {lots} лотов: {r}")
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
            log(f"{TICKER} {price} (стоп {STOP_PRICE}, 20д-макс {high20:.1f}, прорыв {brk.direction})")

            if brk.direction == "up" and not state.get("added"):
                cur = _current_lots(inv)
                add = TARGET_LOTS - cur
                if add > 0:
                    log(f"🚀 ПРОРЫВ: {price} > {high20:.1f} — докупаю {add} лотов")
                    r = inv.post_order(TICKER, quantity=add, direction="buy", order_type="market")
                    state["added"] = True
                    state["added_at"] = price
                    _save_state(state)
                    notify(
                        "BREAKOUT GMKN — докупил",
                        f"GMKN {price} пробил 20-дневный максимум {high20:.1f}.\n"
                        f"Докупил {add} лотов → цель {TARGET_LOTS} лотов.\nОрдер: {r}",
                    )
                return "continue"
    except Exception as e:
        log(f"ошибка прорыва: {e}")

    log(f"{TICKER} {price} (стоп {STOP_PRICE})")
    return "continue"


def main() -> None:
    log("авто-трейдер GMKN запущен (стоп + прорыв-докупка)")
    inv = TinkoffInvest()
    state = _load_state()
    if state.get("added"):
        log(f"позиция уже докуплена (added_at={state.get('added_at')}) — прорыв-флаг стоит")
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
