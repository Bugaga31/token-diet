"""Тесты playbooks: процедурная память + маркерное затирание результатов."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from token_diet.playbooks import (
    PlaybookStore,
    cleared_marker,
    history_budget,
    prune_history,
)


# ── Playbook Memory ──────────────────────────────────────────────────────
def test_add_and_find_similar(tmp_path):
    store = PlaybookStore(tmp_path / "pb.json")
    store.add(
        goal="Проверить цену Полюса и решить про стоп",
        steps=["Взять котировку PLZL", "Сравнить со стопом 1270",
               "При пробое — выйти"],
        success_criteria="Решение принято по факту цены",
    )
    hits = store.find("цена Полюса стоп")
    assert hits, "похожий запрос должен найти рецепт"
    assert "PLZL" in " ".join(hits[0].steps) or "1270" in " ".join(hits[0].steps)


def test_find_ignores_failures(tmp_path):
    store = PlaybookStore(tmp_path / "pb.json")
    store.add("Задача X", ["шаг 1"], "критерий", outcome="failed")
    assert store.find("Задача X") == []


def test_prompt_block_fewshot(tmp_path):
    store = PlaybookStore(tmp_path / "pb.json")
    store.add("Собрать утренний брифинг",
              ["Снять стакан", "Спросить настроение толпы", "Собрать новости"],
              "Пакет из 5 секций готов")
    block = store.prompt_block("утренний брифинг стакан новости")
    assert "ПРОВЕРЕННЫЙ РЕЦЕПТ" in block
    assert "утренний брифинг" in block
    # непохожий запрос — пустой блок
    assert store.prompt_block("zzzzzz qqqqqq") == ""


def test_remember_marker(tmp_path):
    store = PlaybookStore(tmp_path / "pb.json")
    marker = store.remember("Помыть посуду", ["включить воду", "помыть"],
                            "Посуда чистая")
    assert marker.startswith("[playbook saved:")


# ── Tool Result Clearing ─────────────────────────────────────────────────
def test_cleared_marker_format():
    big = "данные " * 200  # ~1200 символов
    m = cleared_marker(big, summary="получено 3 ключа")
    assert m.startswith("[Result cleared: ")
    assert "tokens omitted" in m
    assert "получено 3 ключа" in m
    assert len(m) < 200  # маркер компактный


def test_prune_history_keeps_last_tool():
    msgs = [
        {"role": "user", "content": "сделай анализ"},
        {"role": "assistant", "content": "зову инструмент"},
        {"role": "tool", "content": "JSON " * 500},     # старый тяжёлый результат
        {"role": "assistant", "content": "зову следующий"},
        {"role": "tool", "content": "данные " * 400},   # текущий результат
    ]
    out = prune_history(msgs)
    # последний результат тула сохранён
    assert out[-1]["content"].startswith("данные")
    # старый заменён маркером
    marker = out[2]["content"]
    assert marker.startswith("[Result cleared: ")
    assert "tokens omitted" in marker


def test_history_budget():
    msgs = [
        {"role": "user", "content": "привет"},
        {"role": "tool", "content": "x" * 400},
    ]
    b = history_budget(msgs)
    assert b["tool_tokens"] == 100
    assert b["total_tokens"] >= 100
