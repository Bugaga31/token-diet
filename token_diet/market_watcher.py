"""MarketWatcher — сторож рынка: кормит движок правил живыми ценами.

Сценарий: правило «докупить Сургут при просадке» само ничего не делает,
пока кто-то не подаст ему price_event. Этот модуль — тот «кто-то»:

- в торговые часы MOEX (Пн-Пт 10:00-18:50 МСК) опрашивает котировку;
- шлёт price_event {ticker, price, change_pct} в WorkflowEngine;
- сработавшие правила сами решают: алерт, аудит, place_order (с лимитом).

Защита (урок 15.08 — автопилот долбил API в выходные и лагал комп):
- в Сб/Вс и вне торговых часов — спит, API не трогает;
- интервал опроса настраивается (по умолчанию 5 минут).

100% офлайн-логика, 0 LLM-вызовов.
"""

from __future__ import annotations

import datetime
import time
from pathlib import Path

DEFAULT_INTERVAL_SEC = 300

# MOEX: Пн-Пт 10:00-18:50 МСК (UTC+3)
MSK_OFFSET = datetime.timedelta(hours=3)
MOEX_OPEN = datetime.time(10, 0)
MOEX_CLOSE = datetime.time(18, 50)


def is_trading_day(dt: datetime.datetime | None = None) -> bool:
    dt = dt or datetime.datetime.now()
    return dt.weekday() < 5  # 0=Пн ... 4=Пт


def is_market_open(dt: datetime.datetime | None = None) -> bool:
    """Торговые часы MOEX в МСК: Пн-Пт 10:00-18:50."""
    now = dt or datetime.datetime.now()
    msk = now + MSK_OFFSET
    if not is_trading_day(msk):
        return False
    t = msk.time()
    return MOEX_OPEN <= t <= MOEX_CLOSE


def change_vs_last(price: float, last: float | None) -> float:
    """Изменение в % от предыдущего опроса (0, если данных нет)."""
    if last is None or last <= 0:
        return 0.0
    return round((price - last) / last * 100, 2)


def feed_price_event(inv, eng: object, ticker: str, dry_run: bool = False,
                     last: float | None = None) -> tuple[float | None, list]:
    """Один опрос: котировка -> price_event -> сработавшие правила.

    Возвращает (последняя_цена, результаты_правил). last — цена прошлого
    опроса, для расчёта change_pct.
    """
    quote = inv.get_quote(ticker)
    if quote is None:
        return last, []
    price = quote.price
    event = {"ticker": ticker.upper(), "price": price,
             "change_pct": change_vs_last(price, last)}
    results = eng.trigger("price_event", event, dry_run=dry_run)
    return price, results


def watch(ticker: str, rules_path: Path | None = None,
          interval_sec: int = DEFAULT_INTERVAL_SEC,
          dry_run: bool = False, once: bool = False, max_iters: int = 0) -> None:
    """Главный цикл: опрос в торговые часы, сон вне их и в выходные."""
    from token_diet.tinkoff_invest import TinkoffInvest
    from token_diet.workflow_engine import WorkflowEngine

    inv = TinkoffInvest()
    if not inv.available:
        print("TinkoffInvest недоступен: нет токена (TINKOFF_TOKEN)")
        return

    eng = WorkflowEngine(Path(rules_path) if rules_path else None)
    n = eng.load_rules()
    print(f"[watch] {ticker}: правил загружено {n} (rules: {eng.rules_path})")
    if n == 0:
        print("[watch] ВАЖНО: правил нет — сторож будет молчать. Создай правила "
              "(token-diet rules example) или укажи --rules")
        return
    if dry_run:
        print("[watch] РЕЖИМ СУХОГО ПРОГОНА: заявки не исполняются")

    last: float | None = None
    iters = 0
    while True:
        if once or (max_iters and iters >= max_iters):
            break
        if not is_market_open():
            sleep_until = _next_open()
            print(f"[watch] рынок закрыт — сплю до {sleep_until:%H:%M} МСК")
            time.sleep(min(3600, max(60, (sleep_until - datetime.datetime.now()).total_seconds())))
            continue
        last, results = feed_price_event(inv, eng, ticker, dry_run=dry_run, last=last)
        if last is not None:
            print(f"[watch] {ticker}: {last:.2f} ₽ (change {change_vs_last(last, last):+.2f}%)")
        for r in results:
            print(f"  ⚡ {r['workflow']} → {r['status']}")
            for s in r.get("steps", []):
                tag = s.get("status")
                if tag in ("ok", "dry_run", "error", "skipped", "condition_error", "suspended"):
                    extra = s.get("output", {})
                    line = f"      [{tag}] {s['id']}"
                    if tag == "error":
                        line += f"  ← {extra.get('error', '')}"
                    print(line)
        if once:
            break
        iters += 1
        time.sleep(max(10, int(interval_sec)))


def _next_open() -> datetime.datetime:
    """Ближайший момент открытия MOEX (в МСК)."""
    now = datetime.datetime.now() + MSK_OFFSET
    nxt = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if nxt <= now or not is_trading_day(nxt):
        while not is_trading_day(nxt) or nxt <= now:
            nxt += datetime.timedelta(days=1)
            nxt = nxt.replace(hour=10, minute=0, second=0, microsecond=0)
    return nxt


def init_rules(path: Path | None = None, ticker: str = "SNGSP") -> Path:
    """Сгенерировать правила докупки: лёстница на просадках (3 уровня).

    Стратегия для Сургута (вход 41.50, стоп 39.5): докупаем мелкими
    партиями на падениях — каждая сделка с лимитом суммы и записью
    в hash-chain. Правки уровней — в YAML руками.
    """
    if not path:
        path = Path.home() / "token-diet-memory" / f"rules-{ticker.lower()}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    rules = f"""# Докупка {ticker}: лёстница на просадках (уровни — правь под себя)
# Каждый уровень: 50 шт (~2к ₽), лимит суммы на сделку, запись в hash-chain.
- name: "{ticker}: докупка уровень 1 (цена <= 41.0)"
  trigger:
    on: price_event
    filter: "ticker == '{ticker}' and price <= 41.0"
  steps:
    - id: buy
      action: place_order
      ticker: "{ticker}"
      side: buy
      shares: 50
      max_amount: 2100
    - id: log
      action: audit_log
      action_name: trade_executed
      detail: '{{"level": 1, "ticker": "{ticker}", "price": "{{{{trigger.price}}}}"}}'
    - id: alert
      action: send_alert
      text: "Докупка L1: {ticker} по {{{{trigger.price}}}}₽ — 50 шт"

- name: "{ticker}: докупка уровень 2 (цена <= 40.5)"
  trigger:
    on: price_event
    filter: "ticker == '{ticker}' and price <= 40.5"
  steps:
    - id: buy
      action: place_order
      ticker: "{ticker}"
      side: buy
      shares: 50
      max_amount: 2100
    - id: log
      action: audit_log
      action_name: trade_executed
      detail: '{{"level": 2, "ticker": "{ticker}", "price": "{{{{trigger.price}}}}"}}'
    - id: alert
      action: send_alert
      text: "Докупка L2: {ticker} по {{{{trigger.price}}}}₽ — 50 шт"

- name: "{ticker}: докупка уровень 3 (цена <= 40.0)"
  trigger:
    on: price_event
    filter: "ticker == '{ticker}' and price <= 40.0"
  steps:
    - id: buy
      action: place_order
      ticker: "{ticker}"
      side: buy
      shares: 50
      max_amount: 2100
    - id: log
      action: audit_log
      action_name: trade_executed
      detail: '{{"level": 3, "ticker": "{ticker}", "price": "{{{{trigger.price}}}}"}}'
    - id: alert
      action: send_alert
      text: "Докупка L3: {ticker} по {{{{trigger.price}}}}₽ — 50 шт"
"""
    path.write_text(rules, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="token-diet watch",
                                description="Сторож рынка: живые цены -> правила докупки/продажи")
    p.add_argument("ticker", help="тикер (SNGSP)")
    p.add_argument("--rules", default=None, help="файл правил YAML")
    p.add_argument("--init", action="store_true", help="сгенерировать правила докупки и выйти")
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SEC, help="сек между опросами")
    p.add_argument("--dry-run", action="store_true", help="не исполнять заявки")
    p.add_argument("--once", action="store_true", help="один опрос и выход")
    p.add_argument("--iters", type=int, default=0, help="сколько опросов (0 = бесконечно)")
    args = p.parse_args(argv)
    if args.init:
        path = init_rules(Path(args.rules) if args.rules else None, ticker=args.ticker.upper())
        print(f"✓ Правила докупки: {path}")
        print("  Уровни: 41.0 / 40.5 / 40.0, по 50 шт с лимитом 2100₽. Правь в YAML.")
        print("  Запуск сторожа: token-diet watch SNGSP --rules <файл>")
        return 0
    watch(args.ticker, rules_path=Path(args.rules) if args.rules else None,
          interval_sec=args.interval, dry_run=args.dry_run,
          once=args.once, max_iters=args.iters)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
