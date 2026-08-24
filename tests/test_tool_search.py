"""Тесты tool_search: ToolRegistry (поиск) + TdqsLinter (качество описаний)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from token_diet.tool_search import TdqsLinter, ToolRegistry


# ── TDQS-линтер ──────────────────────────────────────────────────────────
def test_good_description_scores_high():
    linter = TdqsLinter()
    r = linter.check(
        "send_message",
        "Отправляет сообщение в Telegram. Когда: после получения текста от "
        "пользователя. Пишет данные в чат. Формат text.",
        parameters={
            "chat_id": {"description": "ID чата", "type": "int", "example": "123"},
            "text": {"description": "Текст сообщения", "type": "str"},
        },
    )
    assert r.score >= 80, r.issues


def test_smelly_description_flagged():
    linter = TdqsLinter()
    r = linter.check("do_thing", "делает что-то", parameters={"x": {}})
    assert r.score < 60
    assert any("параметр" in i for i in r.issues)
    assert any("глагол" in i for i in r.issues)


def test_side_effect_transparency():
    linter = TdqsLinter()
    # удаление без упоминания побочного эффекта — флаг
    r = linter.check("delete_user", "удаляет пользователя из системы",
                parameters={})
    assert r.axes["transparency"] is True or "побочн" in " ".join(r.issues)


# ── ToolRegistry: поиск ──────────────────────────────────────────────────
def test_registry_search_finds_right_tool():
    reg = ToolRegistry()
    reg.register("get_quote", "Возвращает текущую цену акции по тикеру. "
                "Когда: нужно узнать котировку. Формат число.",
                {"ticker": {"description": "Тикер", "type": "str"}})
    reg.register("send_message", "Отправляет сообщение в Telegram. "
                "Когда: нужно уведомить. Пишет в чат.",
                {"text": {"description": "Текст", "type": "str"}})
    reg.register("parse_pdf", "Извлекает текст из PDF-файла. Когда: нужно "
                "прочитать документ.",
                {"path": {"description": "Путь к файлу", "type": "str"}})

    hits = reg.search("цена акции котировка")
    assert hits and hits[0].name == "get_quote"

    hits2 = reg.search("отправить уведомление в чат")
    assert hits2 and hits2[0].name == "send_message"


def test_resolve_schema():
    reg = ToolRegistry()
    reg.register("get_quote", "Цена акции", {"ticker": {"description": "Тикер"}})
    spec = reg.resolve("get_quote")
    assert spec is not None and "ticker" in spec.parameters
    assert reg.resolve("нет_такого") is None


def test_search_block_and_summary():
    reg = ToolRegistry()
    reg.register("get_quote", "Возвращает цену акции по тикеру. "
                "Когда: нужна котировка.", {"ticker": {"description": "Тикер"}})
    block = reg.search_block("котировка")
    assert "get_quote" in block
    assert reg.stats()["tools"] == 1
    assert "get_quote" in reg.summary()


def test_register_many():
    reg = ToolRegistry()
    n = reg.register_many([
        {"name": "a", "description": "Первый инструмент. Когда: случай A."},
        {"name": "b", "description": "Второй инструмент. Когда: случай B."},
    ])
    assert n == 2 and reg.stats()["tools"] == 2
