"""Tests for factory_droid — реверс-инжиниринг Factory AI (Code Droid).

Проверяем три паттерна: ролевые дроиды + хендоффы, ambiguity engine
(спросить vs действовать), knowledge droid (инсайты репозитория).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from token_diet.factory_droid import (
    Coordinator,
    DROIDS,
    ambiguity,
    build_repo_knowledge,
    classify_task,
    droid_plan,
    droid_status_block,
    resolve_ambiguity,
)


# ── 1. Ролевые дроиды ────────────────────────────────────────────────────────

def test_classify_task_roles():
    assert classify_task("напиши функцию парсинга") == "code"
    assert classify_task("проверь код на баги и найди ошибки") == "review"
    assert classify_task("напиши юнит-тесты для модуля") == "test"
    assert classify_task("напиши инструкцию и документацию") == "docs"
    assert classify_task("разбери как устроен проект и архитектура") == "knowledge"


def test_classify_defaults_to_code():
    assert classify_task("просто текст без маркеров") == "code"


def test_droids_registry_complete():
    for role in ("code", "review", "test", "docs", "knowledge"):
        assert role in DROIDS
        spec = DROIDS[role]
        assert spec.name.endswith("Droid")
        assert spec.system_prompt


def test_droid_plan_shape():
    plan = droid_plan("напиши функцию", role="code")
    assert plan["role"] == "code"
    assert plan["droid"] == "CodeDroid"
    assert isinstance(plan["steps"], list) and len(plan["steps"]) >= 3
    assert plan["system_prompt"]


def test_droid_status_block_lists_all():
    block = droid_status_block()
    for role in ("code", "review", "test", "docs", "knowledge"):
        assert role in block


# ── 2. Ambiguity engine (спросить vs действовать) ────────────────────────────

def test_ambiguity_clear_task_acts():
    a = ambiguity("напиши функцию парсинга JSON с кешем")
    assert a["verdict"] == "act"
    assert a["score"] < 0.5


def test_ambiguity_vague_task_asks():
    a = ambiguity("сделай что-нибудь с памятью наверное")
    assert a["verdict"] == "ask"
    assert a["questions"], "должны быть уточняющие вопросы"


def test_ambiguity_delegation_acts():
    a = ambiguity("сам реши и сделай всё без вопросов")
    assert a["verdict"] == "act"


def test_ambiguity_concrete_acts():
    a = ambiguity("купи акции на 3 тысячи рублей")
    assert a["verdict"] == "act"


def test_resolve_ambiguity_modes():
    r1 = resolve_ambiguity("напиши тесты для модуля")
    assert r1["mode"] == "act" and r1["plan"] is not None
    r2 = resolve_ambiguity("может что-то улучшить в коде как-нибудь")
    assert r2["mode"] == "ask" and r2["plan"] is None


# ── 3. Coordinator: делегирование и хендоффы ─────────────────────────────────

def test_coordinator_delegate_code_chain():
    c = Coordinator()
    dec = c.delegate("напиши функцию для кеша")
    assert dec["mode"] == "act"
    assert dec["role"] == "code"
    roles = [h["from"] for h in dec["handoffs"]] + [dec["handoffs"][-1]["to"]]
    assert roles == ["code", "review", "test"]
    assert dec["handoffs"][0]["to"] == "review"
    assert dec["handoffs"][1]["to"] == "test"


def test_coordinator_delegate_ambiguous_asks():
    c = Coordinator()
    dec = c.delegate("что-то сделай наверное")
    assert dec["mode"] == "ask"
    assert dec["questions"]


def test_coordinator_forced_role():
    c = Coordinator()
    dec = c.delegate("напиши функцию", role="docs")
    assert dec["role"] == "docs"
    assert dec["handoffs"][0]["to"] == "review"


# ── 4. Knowledge Droid: инсайты репозитория ─────────────────────────────────

def _make_repo(tmp: Path) -> Path:
    (tmp / "token_diet").mkdir(parents=True)
    (tmp / "token_diet" / "core.py").write_text(
        "import json\nimport re\n\nAPI_KEY = 'sk-test1234567890abcdef'\n", encoding="utf-8")
    (tmp / "token_diet" / "utils.py").write_text(
        "import json\n\ndef helper():\n    return 1\n", encoding="utf-8")
    (tmp / "tests").mkdir()
    (tmp / "tests" / "test_core.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8")
    return tmp


def test_build_repo_knowledge():
    with tempfile.TemporaryDirectory() as d:
        repo = _make_repo(Path(d))
        k = build_repo_knowledge(repo)
        assert k.module_count >= 2
        assert k.test_count >= 1
        assert k.code_lines > 0
        assert any("тесты" in i for i in k.insights)
        assert any("зависимости" in i for i in k.insights)
        assert any("suspicious" in i or "подозрительные" in i for i in k.insights)


def test_coordinator_knowledge_block():
    with tempfile.TemporaryDirectory() as d:
        repo = _make_repo(Path(d))
        c = Coordinator(repo)
        block = c.knowledge_block()
        assert "KNOWLEDGE" in block
        assert str(repo) in block


def test_knowledge_ignores_venv():
    with tempfile.TemporaryDirectory() as d:
        repo = _make_repo(Path(d))
        (repo / ".venv" / "token_diet").mkdir(parents=True)
        (repo / ".venv" / "token_diet" / "junk.py").write_text("x = 1\n", encoding="utf-8")
        k = build_repo_knowledge(repo)
        # .venv не должен добавлять модули
        assert k.module_count == 2
