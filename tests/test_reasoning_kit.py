"""Tests for reasoning_kit (offline, deterministic)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet import reasoning_kit as rk


def test_plan_and_solve_prompt():
    p = rk.plan_and_solve_prompt("посчитать ROI")
    assert "[ПЛАН]" in p and "[ИСПОЛНЕНИЕ]" in p and "посчитать ROI" in p


def test_plan_steps():
    text = "1. Собрать данные\n2. Посчитать прибыль\n3. Итог"
    assert rk.plan_steps(text) == ["Собрать данные", "Посчитать прибыль", "Итог"]


def test_check_plan_coverage():
    plan = "1. Собрать данные\n2. Посчитать прибыль\n3. Выдать рекомендацию"
    answer = "Данные собраны. Прибыль посчитана. Рекомендация: держать."
    r = rk.check_plan_coverage(plan, answer)
    assert r["ok"] is True
    assert r["total"] == 3


def test_check_plan_coverage_missing():
    plan = "1. Собрать данные\n2. Сходить на Луну"
    answer = "Данные собраны."
    r = rk.check_plan_coverage(plan, answer)
    assert r["ok"] is False
    assert any("Луну" in m for m in r["missing"])


def test_cove_flow():
    draft = "Ответ: Полюс вырос на 5%. ПРОВЕРИТЬ: рост 5%; дивиденды 2026"
    claims = rk.extract_claims(draft)
    assert len(claims) == 2
    v = rk.verify_claims(draft, known_facts={"рост": "3%"})
    assert any(r["status"] == "WARN" for r in v["results"])  # 5% vs 3% спорят


def test_cove_verify_ok():
    draft = "Ответ. ПРОВЕРИТЬ: рост 10%"
    v = rk.verify_claims(draft, known_facts={"рост": "10%"})
    assert v["verified"] is True


def test_ltm():
    d = rk.ltm_decompose_prompt("сложная задача")
    assert "от самой простой" in d
    s = rk.ltm_solve_prompt("подзадача 2", ["подзадача 1: ответ"])
    assert "подзадача 1: ответ" in s


def test_compress_thought_tags():
    trace = "думаю... <core_insight>\nключевой факт: золото растёт\nследующая цель: купить\n</core_insight> хвост"
    out, before, after = rk.compress_thought(trace, max_chars=300)
    assert "золото растёт" in out
    assert "хвост" not in out
    assert before > after


def test_compress_thought_fallback():
    out, before, after = rk.compress_thought("длинная строка " * 200, max_chars=100)
    assert after <= 100


def test_calibrate_and_escalate():
    p = rk.calibrate_prompt("посчитать риск")
    assert '"solution"' in p and '"confidence"' in p
    assert rk.escalate(0.5)["escalate"] is True
    assert rk.escalate(0.95)["escalate"] is False
