"""Тесты экранного агента (screen_agent): JSON-разбор, действия, цикл.

Реальные клики по экрану НЕ выполняются — всё мокается.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

sys.path.insert(0, ".")
from token_diet.screen_agent import (
    AgentStep,
    TaskResult,
    _extract_json,
    ask_brain,
    check_deps,
    describe_task_result,
    execute_action,
    run_task,
)


# ── JSON-разбор ───────────────────────────────────────────────────────────────

def test_extract_json_plain():
    d = _extract_json('{"action": "click", "params": {"x": 10, "y": 20}, "reason": "ok"}')
    assert d and d["action"] == "click" and d["params"]["x"] == 10


def test_extract_json_with_markdown():
    d = _extract_json('```json\n{"action": "done", "params": {}, "reason": "готово"}\n```')
    assert d and d["action"] == "done"


def test_extract_json_with_surrounding_text():
    d = _extract_json('Размышляю... {"action": "type", "params": {"text": "hi"}, "reason": "ввод"} ну вот')
    assert d and d["action"] == "type" and d["params"]["text"] == "hi"


def test_extract_json_invalid():
    assert _extract_json("просто текст без JSON") is None
    assert _extract_json("") is None
    assert _extract_json(None) is None


# ── Выполнение действий ──────────────────────────────────────────────────────

def test_execute_done():
    ok, out = execute_action("done", {}, "")
    assert ok and out == "done"


def test_execute_wait():
    ok, out = execute_action("wait", {"ms": 50}, "")
    assert ok and "50ms" in out


def test_execute_unknown_action():
    ok, out = execute_action("teleport", {}, "")
    assert not ok and "неизвестное" in out


def test_execute_params_not_dict():
    # Модель может вернуть params строкой/списком — не должны крашиться
    ok, out = execute_action("click", "100,200", "")
    assert isinstance(ok, bool) and isinstance(out, str)

    ok2, out2 = execute_action("type", ["a", "b"], "")
    assert isinstance(ok2, bool) and isinstance(out2, str)


def test_check_deps():
    # На машине без xdotool вернёт список; на живой — может быть пустым
    missing = check_deps()
    assert isinstance(missing, list)


def test_run_task_stops_if_no_hands():
    """Нет инструментов управления — стоп сразу с причиной (ревью модели)."""
    with patch("token_diet.screen_agent.check_deps", return_value=["xdotool"]), \
         patch("token_diet.screen_agent.take_screenshot", return_value="/tmp/s.png"):
        r = run_task("нажми", max_steps=3)
    assert not r.done
    assert "xdotool" in r.reason


def test_run_task_stops_on_repeated_action():
    """Модель 3 раза шлёт одно и то же действие — анти-зацикливание (ревью модели)."""
    answers = iter([
        '{"action": "click", "params": {"x": 10, "y": 10}, "reason": ""}',
        "то же окно",
        '{"action": "click", "params": {"x": 10, "y": 10}, "reason": ""}',
        "то же окно",
        '{"action": "click", "params": {"x": 10, "y": 10}, "reason": ""}',
        "то же окно",
    ])

    def fake_brain(question, image_path, model=None, timeout=120):
        a = next(answers)
        return {"ok": True, "answer": a, "model": "test", "error": None}

    with patch("token_diet.screen_agent.take_screenshot", return_value="/tmp/s.png"), \
         patch("token_diet.screen_agent._safe_image_base64", return_value="QUJD"), \
         patch("token_diet.screen_agent.execute_action", return_value=(True, "ok")), \
         patch("token_diet.screen_agent.ask_brain", side_effect=fake_brain):
        r = run_task("цикл", max_steps=8)
    assert not r.done
    assert "повторяет" in r.reason


def test_execute_destructive_blocked():
    ok, out = execute_action("key", {"combo": "alt+F4"}, "")
    assert not ok and "заблокировано" in out


def test_execute_destructive_allowed():
    with patch("token_diet.screen_agent._scr", return_value=(0, "")):
        ok, out = execute_action("key", {"combo": "alt+F4"}, "", destructive_ok=True)
        assert ok


def test_execute_click_calls_scr():
    with patch("token_diet.screen_agent._scr", return_value=(0, "")) as m:
        ok, out = execute_action("click", {"x": 100, "y": 200, "button": 1}, "")
        assert ok
        m.assert_called_once_with("click", "100", "200", "1")


# ── Полный цикл ──────────────────────────────────────────────────────────────

def test_run_task_done_on_first_step():
    """Модель сразу говорит done — задача выполнена без действий."""
    def fake_brain(question, image_path, model=None, timeout=120):
        return {"ok": True, "answer": '{"action": "done", "params": {}, "reason": "уже готово"}',
                "model": "test-model", "error": None}

    with patch("token_diet.screen_agent.take_screenshot", return_value="/tmp/s.png"), \
         patch("token_diet.screen_agent._safe_image_base64", return_value="QUJD"), \
         patch("token_diet.screen_agent.ask_brain", side_effect=fake_brain):
        r = run_task("проверь экран", max_steps=3)
    assert r.done
    assert r.model == "test-model"
    assert len(r.steps) == 1


def test_run_task_click_then_done():
    """Модель кликает, потом done. Оба действия в истории."""
    # Порядок вызовов в цикле: решение(click) → проверка(текст) → решение(done)
    answers = iter([
        '{"action": "click", "params": {"x": 50, "y": 60}, "reason": "нажать кнопку"}',
        "Экран после клика: открылось окно.",
        '{"action": "done", "params": {}, "reason": "готово"}',
    ])

    def fake_brain(question, image_path, model=None, timeout=120):
        a = next(answers)
        return {"ok": True, "answer": a, "model": "test", "error": None}

    with patch("token_diet.screen_agent.take_screenshot", return_value="/tmp/s.png"), \
         patch("token_diet.screen_agent._safe_image_base64", return_value="QUJD"), \
         patch("token_diet.screen_agent.execute_action", return_value=(True, "click ok")), \
         patch("token_diet.screen_agent.ask_brain", side_effect=fake_brain):
        r = run_task("открой", max_steps=5)
    assert r.done
    assert r.steps[0].action == "click"
    assert r.steps[0].ok
    assert "открылось окно" in r.steps[0].observation


def test_run_task_stops_on_failed_action():
    """Если действие не выполнилось — цикл останавливается с причиной."""
    def fake_brain(question, image_path, model=None, timeout=120):
        return {"ok": True, "answer": '{"action": "click", "params": {"x": 1, "y": 2}, "reason": ""}',
                "model": "test", "error": None}

    with patch("token_diet.screen_agent.take_screenshot", return_value="/tmp/s.png"), \
         patch("token_diet.screen_agent._safe_image_base64", return_value="QUJD"), \
         patch("token_diet.screen_agent.execute_action", return_value=(False, "не вышло")), \
         patch("token_diet.screen_agent.ask_brain", side_effect=fake_brain):
        r = run_task("нажми", max_steps=5)
    assert not r.done
    assert "не вышло" in r.reason


def test_run_task_brain_error():
    """Мозг не отвечает — причина в результате."""
    with patch("token_diet.screen_agent.take_screenshot", return_value="/tmp/s.png"), \
         patch("token_diet.screen_agent.ask_brain",
               return_value={"ok": False, "answer": "", "model": "", "error": "нет сети"}):
        r = run_task("что-то", max_steps=3)
    assert not r.done
    assert "нет сети" in r.reason


def test_run_task_bad_json():
    """Модель вернула мусор — стоп с объяснением."""
    with patch("token_diet.screen_agent.take_screenshot", return_value="/tmp/s.png"), \
         patch("token_diet.screen_agent.ask_brain",
               return_value={"ok": True, "answer": "привет мир", "model": "m", "error": None}):
        r = run_task("что-то", max_steps=3)
    assert not r.done
    assert "не-JSON" in r.reason


def test_run_task_max_steps():
    """Модель шлёт разные wait — лимит шагов останавливает (не анти-цикл)."""
    # Каждый шаг = решение + проверка: на 3 шага нужно 6 вызовов
    waits = iter([10, 20, 30, 40, 50, 60])

    def fake_brain(question, image_path, model=None, timeout=120):
        if "Скриншот сделан ПОСЛЕ" in question:
            return {"ok": True, "answer": "то же окно", "model": "test", "error": None}
        ms = next(waits)
        return {"ok": True, "answer": f'{{"action": "wait", "params": {{"ms": {ms}}}, "reason": ""}}',
                "model": "test", "error": None}

    with patch("token_diet.screen_agent.take_screenshot", return_value="/tmp/s.png"), \
         patch("token_diet.screen_agent.ask_brain", side_effect=fake_brain):
        r = run_task("цикл", max_steps=3)
    assert not r.done
    assert "лимит" in r.reason
    assert len(r.steps) == 3


# ── Отчёт ────────────────────────────────────────────────────────────────────

def test_describe_task_result():
    r = TaskResult(task="тест", done=True, reason="ок", model="m")
    r.steps.append(AgentStep(action="done", params={}, reason="ок", step=1))
    txt = describe_task_result(r)
    assert "ВЫПОЛНЕНО" in txt and "тест" in txt
