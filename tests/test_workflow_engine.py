"""Тесты YAML-движка правил (из Buzz, buzz-workflow).

Проверяем: парсинг+валидация, безопасный eval, шаблоны, cron,
рыночные триггеры, approval-gates, SSRF-защита.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.workflow_engine import (  # noqa: E402
    WorkflowEngine,
    cron_matches,
    evaluate_condition,
    is_private_ip,
    parse_duration_secs,
    parse_workflow,
    resolve_template,
)


# ── парсинг и валидация ──────────────────────────────────────────────────
def test_parse_basic():
    y = """name: 'Полюс падение'
trigger:
  on: price_event
  filter: "ticker == 'PLZL' and change_pct <= -2"
steps:
  - id: alert
    action: send_alert
    text: 'PLZL упал'
"""
    defn, canonical = parse_workflow(y)
    assert defn["name"] == "Полюс падение"
    assert defn["enabled"] is True  # по умолчанию
    assert defn["steps"][0]["id"] == "alert"
    json.loads(canonical)  # канонический JSON парсится


def test_validate_rejects_empty_name():
    with __import__("pytest").raises(ValueError):
        parse_workflow("name: ''\ntrigger:\n  on: webhook\nsteps:\n  - id: s1\n    action: delay\n    duration: 1m\n")


def test_validate_rejects_empty_steps():
    with __import__("pytest").raises(ValueError):
        parse_workflow("name: x\ntrigger:\n  on: webhook\nsteps: []\n")


def test_validate_rejects_duplicate_step_ids():
    with __import__("pytest").raises(ValueError):
        parse_workflow(
            "name: x\ntrigger:\n  on: webhook\nsteps:\n"
            "  - id: s1\n    action: delay\n    duration: 1m\n"
            "  - id: s1\n    action: delay\n    duration: 1m\n"
        )


def test_validate_rejects_dash_in_step_id():
    with __import__("pytest").raises(ValueError):
        parse_workflow(
            "name: x\ntrigger:\n  on: webhook\nsteps:\n"
            "  - id: my-step\n    action: delay\n    duration: 1m\n"
        )


def test_validate_rejects_unknown_action():
    with __import__("pytest").raises(ValueError):
        parse_workflow(
            "name: x\ntrigger:\n  on: webhook\nsteps:\n"
            "  - id: s1\n    action: fly_to_moon\n"
        )


def test_schedule_requires_cron_or_interval():
    with __import__("pytest").raises(ValueError):
        parse_workflow("name: x\ntrigger:\n  on: schedule\nsteps:\n  - id: s1\n    action: delay\n    duration: 1m\n")


def test_schedule_rejects_sub_minute_interval():
    with __import__("pytest").raises(ValueError):
        parse_workflow(
            "name: x\ntrigger:\n  on: schedule\n  interval: 30s\n"
            "steps:\n  - id: s1\n    action: delay\n    duration: 1m\n"
        )


# ── безопасный eval ──────────────────────────────────────────────────────
def test_eval_comparisons():
    assert evaluate_condition("a >= 3", {"a": 4}) is True
    assert evaluate_condition("a < 3", {"a": 4}) is False
    assert evaluate_condition("ticker == 'PLZL'", {"ticker": "PLZL"}) is True
    assert evaluate_condition("trigger_ticker == 'PLZL'", {"trigger_ticker": "PLZL"}) is True
    assert evaluate_condition("ticker != 'GAZP'", {"ticker": "PLZL"}) is True


def test_eval_logic_and_functions():
    ctx = {"trigger_text": "P1 инцидент в production", "trigger_n": 5}
    assert evaluate_condition('str_contains(trigger_text, "P1")', ctx) is True
    assert evaluate_condition('str_contains(trigger_text, "P2")', ctx) is False
    assert evaluate_condition('str_starts_with(trigger_text, "P1")', ctx) is True
    assert evaluate_condition("str_len(trigger_text) > 3", ctx) is True
    assert evaluate_condition('str_contains(trigger_text, "P1") and trigger_n > 3', ctx) is True


def test_eval_no_code_injection():
    # eval() кода невозможен — только переменные и функции str_*
    with __import__("pytest").raises(ValueError):
        evaluate_condition("__import__('os').system('id')", {})
    with __import__("pytest").raises(ValueError):
        evaluate_condition("unknown_var > 5", {})


def test_eval_unknown_function_rejected():
    with __import__("pytest").raises(ValueError):
        evaluate_condition('evil_func("x")', {})


# ── шаблоны ──────────────────────────────────────────────────────────────
def test_template_resolve():
    ctx = {"ticker": "PLZL", "price": 1300, "change_pct": -2.5}
    out = resolve_template("{{trigger.ticker}} упал на {{trigger.change_pct}}%", ctx, {})
    assert out == "PLZL упал на -2.5%"


def test_template_truncate_and_unknown():
    ctx = {"text": "очень длинное сообщение"}
    assert resolve_template("{{trigger.text | truncate(5)}}", ctx, {}) == "очень"
    # неизвестный ключ остаётся как есть (как в Buzz)
    assert resolve_template("{{trigger.нет}} и текст", ctx, {}) == "{{trigger.нет}} и текст"


def test_template_step_outputs():
    ctx = {"ticker": "PLZL"}
    outputs = {"calc": {"target": 1400}}
    assert resolve_template("цель: {{steps.calc.output.target}}", ctx, outputs) == "цель: 1400"


# ── cron ─────────────────────────────────────────────────────────────────
def test_cron_matches():
    dt = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)  # понедельник
    assert cron_matches("0 9 * * 1-5", dt) is True
    assert cron_matches("0 10 * * 1-5", dt) is False
    assert cron_matches("0 9 * * 6-7", dt) is False  # вс-сб


def test_cron_ranges_and_lists():
    dt = datetime(2026, 8, 22, 12, 30, tzinfo=timezone.utc)  # суббота
    assert cron_matches("30 12 * * 6", dt) is True
    assert cron_matches("0,30 12 * * *", dt) is True
    assert cron_matches("*/15 * * * *", dt) is True


# ── длительности и SSRF ──────────────────────────────────────────────────
def test_duration_parsing():
    assert parse_duration_secs("30s") == 30
    assert parse_duration_secs("5m") == 300
    assert parse_duration_secs("1h") == 3600
    with __import__("pytest").raises(ValueError):
        parse_duration_secs("10x")


def test_ssrf_private_ip():
    assert is_private_ip("127.0.0.1") is True
    assert is_private_ip("192.168.1.1") is True
    assert is_private_ip("10.0.0.1") is True
    assert is_private_ip("100.64.0.1") is True  # CGNAT
    assert is_private_ip("::1") is True
    assert is_private_ip("8.8.8.8") is False
    assert is_private_ip("не-адрес") is True


# ── движок ───────────────────────────────────────────────────────────────
def test_engine_loads_and_triggers_price_event(tmp_path, capsys):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
- name: "Полюс падение"
  trigger:
    on: price_event
    filter: "ticker == 'PLZL' and change_pct <= -2"
  steps:
    - id: alert
      action: send_alert
      text: "PLZL упал на {{trigger.change_pct}}%"
""", encoding="utf-8")
    eng = WorkflowEngine(rules)
    assert eng.load_rules() == 1
    res = eng.trigger("price_event", {"ticker": "PLZL", "change_pct": -3.2, "price": 1290})
    assert len(res) == 1
    assert res[0]["status"] == "completed"
    assert res[0]["steps"][0]["status"] == "ok"
    out = capsys.readouterr().out
    assert "PLZL упал на -3.2%" in out


def test_engine_filter_skips_non_matching(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
- name: "Полюс падение"
  trigger:
    on: price_event
    filter: "ticker == 'PLZL' and change_pct <= -2"
  steps:
    - id: alert
      action: send_alert
      text: "x"
""", encoding="utf-8")
    eng = WorkflowEngine(rules)
    eng.load_rules()
    res = eng.trigger("price_event", {"ticker": "GAZP", "change_pct": -5})
    assert res == []  # фильтр не сработал


def test_engine_dry_run_no_side_effects(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
- name: "Рост"
  trigger:
    on: price_event
    filter: "change_pct >= 3"
  steps:
    - id: a
      action: audit_log
      action_name: decision_made
      detail: '{"what": "x"}'
""", encoding="utf-8")
    eng = WorkflowEngine(rules)
    eng.load_rules()
    res = eng.trigger("price_event", {"change_pct": 5}, dry_run=True)
    assert res[0]["steps"][0]["status"] == "dry_run"


def test_engine_audit_log_writes_chain(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
- name: "Фиксация"
  trigger:
    on: price_event
    filter: "change_pct >= 3"
  steps:
    - id: log
      action: audit_log
      action_name: decision_made
      detail: '{"what": "рост {{trigger.ticker}}", "pct": "{{trigger.change_pct}}"}'
""", encoding="utf-8")
    eng = WorkflowEngine(rules)
    eng.load_rules()
    # переопределяем путь цепочки через подмену в monkey-стиле
    chain_file = tmp_path / "audit.jsonl"
    from token_diet.audit_chain import AuditChain
    # ВАЖНО: захватываем исходный МЕТОД (не класс), иначе лямбда зациклится
    orig_init = AuditChain.__init__
    AuditChain.__init__ = lambda self, path=None, chain_id=None: orig_init(
        self, path or chain_file, chain_id)
    try:
        res = eng.trigger("price_event", {"ticker": "PLZL", "change_pct": 4.0})
        step = res[0]["steps"][0] if res else None
        assert step and step["status"] == "ok", f"step: {step}"
        chain = AuditChain(chain_file)
        ok, bad = chain.verify()
        assert ok
        entries = chain.entries()
        assert entries[-1].action == "decision_made"
        assert entries[-1].detail["what"] == "рост PLZL"
    finally:
        AuditChain.__init__ = orig_init


def test_engine_approval_gate_suspends(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
- name: "Согласование"
  trigger:
    on: webhook
  steps:
    - id: ask
      action: request_approval
      from: "@general"
      message: "Подтверди?"
    - id: after
      action: send_alert
      text: "не должно выполниться"
""", encoding="utf-8")
    eng = WorkflowEngine(rules)
    eng.load_rules()
    res = eng.trigger("webhook", {})
    assert res[0]["steps"][0]["status"] == "suspended"
    assert res[0]["steps"][0]["approval_token"]
    assert len(res[0]["steps"]) == 1  # после gate выполнение остановлено


def test_engine_schedule_cron_due(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
- name: "Утренний обзор"
  trigger:
    on: schedule
    cron: "0 9 * * 1-5"
  steps:
    - id: alert
      action: send_alert
      text: "обзор"
""", encoding="utf-8")
    eng = WorkflowEngine(rules)
    eng.load_rules()
    due = eng.scheduled_due(datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc))
    assert len(due) == 1
    not_due = eng.scheduled_due(datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc))
    assert not_due == []


def test_engine_condition_step_skip(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
- name: "Условный шаг"
  trigger:
    on: price_event
  steps:
    - id: s1
      if: "change_pct >= 5"
      action: send_alert
      text: "большой рост"
""", encoding="utf-8")
    eng = WorkflowEngine(rules)
    eng.load_rules()
    res = eng.trigger("price_event", {"change_pct": 2})
    assert res[0]["steps"][0]["status"] == "skipped"
