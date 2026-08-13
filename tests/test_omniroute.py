"""Тесты omniroute: маршрутизация, саб-агенты, fallback, судья."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import token_diet.omniroute as orm
from token_diet.omniroute import (
    OmniRouter,
    Provider,
    _judge,
    _split_tasks,
)


# ── стратегии выбора модели ─────────────────────────────────────────────
def _test_providers():
    return [
        {"name": "cheap1", "kind": "ollama", "model": "m1",
         "base_url": "http://x", "cost": 0.0, "latency": 0.2,
         "quality": 4, "tags": ["cheap", "fast"]},
        {"name": "big1", "kind": "ollama", "model": "m2",
         "base_url": "http://x", "cost": 0.0, "latency": 1.5,
         "quality": 9, "tags": ["balanced"]},
        {"name": "coder1", "kind": "ollama", "model": "m3",
         "base_url": "http://x", "cost": 0.0, "latency": 1.0,
         "quality": 7, "tags": ["coding", "reasoning"]},
    ]


def test_pick_fast_prefers_low_latency():
    r = OmniRouter(_test_providers())
    p = r.pick("auto/fast")
    assert p.name == "cheap1"


def test_pick_coding_prefers_coder():
    r = OmniRouter(_test_providers())
    p = r.pick("auto/coding")
    assert p.name == "coder1"


def test_pick_balanced_prefers_quality():
    r = OmniRouter(_test_providers())
    p = r.pick("auto/balanced")
    assert p.name == "big1"


def test_pick_excludes():
    r = OmniRouter(_test_providers())
    p = r.pick("auto/fast", exclude=["cheap1"])
    assert p.name != "cheap1"


# ── декомпозиция ────────────────────────────────────────────────────────
def test_split_numbered():
    parts = _split_tasks("1) Проверить цену. 2) Сравнить со стопом. "
                         "3) Принять решение.")
    assert len(parts) == 3


def test_split_single():
    parts = _split_tasks("Простое действие без разбиения")
    assert len(parts) == 1


def test_split_limits():
    parts = _split_tasks("1) A. 2) B. 3) C. 4) D. 5) E. 6) F. 7) G.",
                         max_subtasks=3)
    assert len(parts) <= 3


# ── саб-агенты и fallback (мок HTTP) ────────────────────────────────────
def test_subagent_ok(monkeypatch):
    def fake_call(provider, system, user, max_tokens=900, timeout=90):
        return "Итог: цена 1261, стоп пробит. Рекомендация: выйти."
    monkeypatch.setattr(orm, "_call_model", fake_call)
    r = OmniRouter(_test_providers())
    res = r.subagent("Проверить стоп Полюса")
    assert res.ok
    assert res.summary.startswith("Итог:")
    assert res.provider != "none"


def test_subagent_fallback(monkeypatch):
    calls = {"n": 0}

    def flaky(provider, system, user, max_tokens=900, timeout=90):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("первая модель упала")
        return "ответ от резервной"
    monkeypatch.setattr(orm, "_call_model", flaky)
    r = OmniRouter(_test_providers())
    res = r.subagent("Задача")
    assert res.ok
    assert res.fallbacks == 1
    assert "резервной" in res.summary


def test_subagent_all_down(monkeypatch):
    def boom(provider, system, user, max_tokens=900, timeout=90):
        raise ConnectionError("все упали")
    monkeypatch.setattr(orm, "_call_model", boom)
    r = OmniRouter(_test_providers())
    res = r.subagent("Задача")
    assert not res.ok
    assert "все модели упали" in res.error


# ── оркестрация ─────────────────────────────────────────────────────────
def test_orchestrate(monkeypatch):
    def fake_call(provider, system, user, max_tokens=900, timeout=90):
        return f"сабагент {provider.name}: сделано"
    monkeypatch.setattr(orm, "_call_model", fake_call)
    r = OmniRouter(_test_providers())
    out = r.orchestrate("1) Разведка. 2) Анализ. 3) Вывод.")
    assert out["subtasks"] == 3
    assert out["ok"] == 3
    assert "сабагент" in out["body"]


# ── судья (fusion) ──────────────────────────────────────────────────────
def test_judge_prefers_specific():
    a1 = orm.SubAgentResult(task="t", provider="p1", model="m1",
                            summary="да")
    a2 = orm.SubAgentResult(task="t", provider="p2", model="m2",
                            summary="Цена 1261, стоп 1270 пробит. "
                                    "Рекомендация: выйти.")
    best = _judge("t", [a1, a2])
    assert best.provider == "p2"


def test_fusion_uses_judge(monkeypatch):
    monkeypatch.setattr(orm, "_call_model",
                        lambda p, s, u, max_tokens=700, timeout=90:
                        f"ответ {p.name} 42")
    r = OmniRouter(_test_providers())
    out = r.fusion("Задача с числом")
    assert out["ok"]
    assert out["chosen"]


# ── провайдеры по умолчанию ─────────────────────────────────────────────
def test_default_providers_ollama():
    r = OmniRouter()
    assert any(p.kind == "ollama" for p in r.providers)
    assert len(r.providers) >= 3
