"""Тесты сторожа рынка (market_watcher).

Проверяем: торговые часы MOEX, расчёт change_pct, генерацию правил
докупки, что price_event кормит движок, лимит суммы на сделку.
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.market_watcher import (  # noqa: E402
    change_vs_last,
    init_rules,
    is_market_open,
    is_trading_day,
)
from token_diet.workflow_engine import WorkflowEngine  # noqa: E402


# ── торговые часы ────────────────────────────────────────────────────────
def test_is_trading_day():
    assert is_trading_day(datetime.datetime(2026, 8, 17)) is True   # Пн
    assert is_trading_day(datetime.datetime(2026, 8, 15)) is False  # Сб


def test_market_open_monday_morning():
    # Пн 10:00 МСК — открыто
    dt = datetime.datetime(2026, 8, 17, 7, 0)  # UTC = 10:00 МСК
    assert is_market_open(dt) is True


def test_market_closed_before_open():
    # Пн 09:00 МСК (06:00 UTC) — закрыто
    dt = datetime.datetime(2026, 8, 17, 6, 0)
    assert is_market_open(dt) is False


def test_market_closed_after_close():
    # Пн 19:00 МСК (16:00 UTC) — закрыто
    dt = datetime.datetime(2026, 8, 17, 16, 0)
    assert is_market_open(dt) is False


def test_market_closed_weekend():
    # Сб 12:00 МСК — закрыто даже в «торговые» часы
    dt = datetime.datetime(2026, 8, 15, 9, 0)
    assert is_market_open(dt) is False


def test_change_vs_last():
    assert change_vs_last(41.72, 41.50) == 0.53
    assert change_vs_last(41.00, 42.00) == -2.38
    assert change_vs_last(41.72, None) == 0.0  # нет прошлой цены


# ── генерация правил докупки ────────────────────────────────────────────
def test_init_rules_generates_ladder(tmp_path):
    path = init_rules(tmp_path / "rules.yaml", ticker="SNGSP")
    text = path.read_text(encoding="utf-8")
    assert "SNGSP" in text
    assert "place_order" in text
    assert text.count("shares: 50") == 3          # 3 уровня
    assert "41.0" in text and "40.5" in text and "40.0" in text
    assert "max_amount: 2100" in text


def test_generated_rules_parse_and_fire(tmp_path, capsys):
    rules = tmp_path / "rules.yaml"
    init_rules(rules, ticker="SNGSP")
    eng = WorkflowEngine(rules)
    assert eng.load_rules() == 3
    # событие «цена 40.8» — уровень 2 и 3 не должны сработать, уровень 1 тоже нет
    res = eng.trigger("price_event", {"ticker": "SNGSP", "price": 40.8}, dry_run=True)
    # 41.0/40.5/40.0 — все условия «price <= X» при 40.8: сработают L1 и L2? нет:
    # 40.8 <= 41.0 ✓, 40.8 <= 40.5 ✗, 40.8 <= 40.0 ✗
    assert len(res) == 1
    assert res[0]["workflow"].startswith("SNGSP: докупка уровень 1")


def test_place_order_dry_run_no_side_effects(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
- name: "Докупка"
  trigger:
    on: price_event
    filter: "ticker == 'SNGSP' and price <= 41.0"
  steps:
    - id: buy
      action: place_order
      ticker: "SNGSP"
      side: buy
      shares: 50
      max_amount: 2100
""", encoding="utf-8")
    eng = WorkflowEngine(rules)
    eng.load_rules()
    res = eng.trigger("price_event", {"ticker": "SNGSP", "price": 40.5}, dry_run=True)
    step = res[0]["steps"][0]
    assert step["status"] == "dry_run"          # сухой прогон: заявки НЕТ
    assert step["output"]["action"] == "place_order"


def test_place_order_cap_without_token(tmp_path):
    """Без токена — честная ошибка, а не тишина. Лимит проверяется до API."""
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
- name: "Покупка"
  trigger:
    on: price_event
  steps:
    - id: buy
      action: place_order
      ticker: "SNGSP"
      side: buy
      shares: 99999
      max_amount: 100
""", encoding="utf-8")
    eng = WorkflowEngine(rules)
    eng.load_rules()
    res = eng.trigger("price_event", {"ticker": "SNGSP", "price": 1.0})
    step = res[0]["steps"][0]
    assert step["status"] == "error"  # TinkoffInvest недоступен/лимит — но не тишина
