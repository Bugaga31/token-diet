"""Autopilot — полностью автономный трейдер по законам генерала.

УРОКИ ГЕНЕРАЛА:
1. Прибыль > привязанность. Держим, пока зарабатывает.
2. Покупать только то, что ЗНАЕШЬ что вырастет.
3. Геополитику учитывать всегда.
4. Каждая сделка = КОМИССИЯ. Вход имеет смысл только если
   ожидаемый ход больше двойной комиссии (вход + выход).

Цикл автопилота:
1. Спросить мозг (trading_brain): ENTER / WAIT / SKIP.
2. ENTER → проверить комиссию: ход кандидата ≥ 2 × комиссия + запас.
3. Купить (market), поставить trailing-стоп (как gmkn_stop_watch).
4. Дальше цикл стопа: подтягиваем за ценой, выходим по стопу.
5. Каждое действие → Obsidian + лог.

DRY_RUN=True по умолчанию: мозг всё считает, но НЕ покупает,
пока генерал не скажет «торгуй» (переменная DRY_RUN=False).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

from token_diet.tinkoff_invest import TinkoffInvest

# ═══════════ КОНФИГУРАЦИЯ ═══════════
DRY_RUN = True          # True: анализ без покупок. False: реальные сделки
COMMISSION_PCT = 0.003  # 0.3% за сделку (вход + выход = 0.6%)
MIN_MOVE_PCT = 1.2      # минимальный ожидаемый ход для входа (> 2×комиссия)
TRAIL_PCT = 0.04        # trailing-стоп: −4% от максимума
BREAKEVEN_TRIGGER = 0.02  # +2% от входа → стоп не ниже входа
MAX_POSITION_RUB = 6000   # максимум на одну позицию (осторожно, из ~18К)
INTERVAL_SCAN_SEC = 600   # сканирование рынка каждые 10 минут
INTERVAL_STOP_SEC = 180   # проверка стопа каждые 3 минуты
LOG = "/tmp/autopilot.log"
ALERT = "/tmp/autopilot_alert.txt"
STATE = "/tmp/autopilot_state.json"
# ═══════════════════════════════════


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
        vault.write(f"AUTOPILOT {title}", body)
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


def _current_lots(inv: TinkoffInvest, ticker: str) -> int:
    try:
        for p in inv.get_portfolio() or []:
            if ticker in str(getattr(p, "ticker", "")) or ticker in str(getattr(p, "figi", "")):
                return int(float(getattr(p, "quantity", 0)))
    except Exception:
        pass
    return 0


def commission_cost(price: float, lots: int, lot_size: int) -> float:
    """Стоимость комиссии за сделку (вход ИЛИ выход), в рублях."""
    return price * lots * lot_size * COMMISSION_PCT


def check_entry(inv: TinkoffInvest) -> str:
    """Спросить мозг: стоит ли входить. Вернёт 'enter' или 'wait'."""
    try:
        from token_diet.trading_brain import decide
    except ImportError:
        from .trading_brain import decide

    decision = decide()  # dry-run мозг: только анализ
    log(f"мозг: {decision.action} — {decision.reason[:120]}")

    if decision.action != "ENTER":
        return "wait"

    cand = decision.checks
    change = cand.get("cand_change_pct", 0.0)

    # ── УРОК 4: комиссия ──
    # Ход кандидата должен покрыть 2×комиссию + запас.
    # Если кандидат растёт на 1.0%, а комиссия 0.6% (вход+выход) —
    # чистый остаток мизерный, вход не имеет смысла.
    round_trip = COMMISSION_PCT * 2 * 100  # в %
    if change < MIN_MOVE_PCT:
        log(f"комиссия-фильтр: ход {change}% < MIN_MOVE_PCT {MIN_MOVE_PCT}% "
            f"(комиссия вход+выход {round_trip:.2f}%) — вход не окупится")
        return "wait"

    ticker = decision.checks.get("candidate", "")
    if not ticker:
        return "wait"

    # ── размер позиции: максимум MAX_POSITION_RUB на бумагу ──
    try:
        price = inv.get_quote(ticker).price
        lot_size = inv.lot_size(ticker) or 1
        lots = max(1, int(MAX_POSITION_RUB // (price * lot_size)))
    except Exception:
        price, lots, lot_size = 0, 1, 1

    fee_in = commission_cost(price, lots, lot_size)
    fee_out = fee_in
    log(f"ВХОД {ticker}: {lots} лотов × {price} ≈ {price*lots*lot_size:.0f}₽ "
        f"(комиссия вход {fee_in:.1f}₽ + выход {fee_out:.1f}₽)")

    if DRY_RUN:
        notify(
            "ENTER-сигнал (dry-run)",
            f"Мозг дал ENTER: {ticker}, рост {change}% с объёмом.\n"
            f"Купил бы {lots} лотов ≈ {price*lots*lot_size:.0f}₽.\n"
            f"DRY_RUN=True — НЕ покупаю. Скажи «торгуй», чтобы включить.",
        )
        return "wait"

    # ── реальная покупка ──
    r = inv.post_order(ticker, quantity=lots, direction="buy", order_type="market")
    state = _load_state()
    state.update({
        "ticker": ticker, "lots": lots, "entry_price": price,
        "max_price": price, "stop": round(price * (1 - TRAIL_PCT), 2),
        "lot_size": lot_size,
    })
    _save_state(state)
    notify(
        "AUTOPILOT: КУПИЛ",
        f"{ticker}: {lots} лотов по ~{price} ({price*lots*lot_size:.0f}₽).\n"
        f"Стоп: {state['stop']}. Ордер: {r}",
    )
    return "entered"


def check_stop(inv: TinkoffInvest, state: dict) -> str:
    """Trailing-стоп по открытой позиции."""
    ticker = state.get("ticker")
    if not ticker:
        return "none"
    entry = float(state.get("entry_price", 0))
    lots = int(state.get("lots", 0))

    q = inv.get_quote(ticker)
    if q is None:
        return "continue"
    price = q.price

    max_price = max(float(state.get("max_price", 0) or 0), price)
    state["max_price"] = max_price

    # trailing
    stop = float(state.get("stop", 0) or 0)
    trail = max_price * (1 - TRAIL_PCT)
    stop = max(stop, trail)
    # безубыток
    if max_price >= entry * (1 + BREAKEVEN_TRIGGER):
        stop = max(stop, entry * 1.005)
    state["stop"] = round(stop, 2)
    _save_state(state)

    log(f"{ticker} {price} (стоп {state['stop']}, макс {max_price:.2f})")

    if price <= stop:
        kind = "TRAIL STOP" if stop > entry * (1 - TRAIL_PCT) else "STOP HIT"
        if DRY_RUN:
            notify(kind + " (dry-run)", f"{ticker} {price} ≤ стоп {stop}, продал бы {lots} лотов")
            state.pop("ticker", None)
            _save_state(state)
            return "exit"
        r = inv.post_order(ticker, quantity=lots, direction="sell", order_type="market")
        notify(kind, f"{ticker} {price} ≤ стоп {stop}, продано {lots} лотов: {r}")
        state.pop("ticker", None)
        _save_state(state)
        return "exit"
    return "continue"


def main() -> None:
    log(f"🤖 AUTOPILOT запущен. DRY_RUN={DRY_RUN}, комиссия {COMMISSION_PCT*100:.1f}% "
        f"(вход+выход {COMMISSION_PCT*200:.1f}%), мин.ход {MIN_MOVE_PCT}%")
    inv = TinkoffInvest()
    state = _load_state()
    last_scan = 0.0

    while True:
        try:
            if state.get("ticker"):
                # Есть позиция → следим за стопом
                if check_stop(inv, state) == "exit":
                    log("позиция закрыта, ищу новый вход")
                    state = _load_state()
                    last_scan = 0.0  # сразу просканируем заново
            elif time.time() - last_scan >= INTERVAL_SCAN_SEC:
                # Нет позиции → сканируем рынок
                last_scan = time.time()
                action = check_entry(inv)
                state = _load_state()
                if action == "entered":
                    log("позиция открыта, переключаюсь в режим стопа")
        except Exception as e:
            log(f"ошибка цикла: {e}")
        time.sleep(min(INTERVAL_STOP_SEC, INTERVAL_SCAN_SEC))


if __name__ == "__main__":
    main()
