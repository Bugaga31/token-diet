"""Тесты telegram_monitor: фильтр важности и контракты без сети."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from token_diet.telegram_monitor import _is_important, _stamp


def test_stamp_format():
    s = _stamp()
    # YYYY-MM-DD HH:MM:SS UTC
    assert len(s) >= 20
    assert s.endswith("UTC")


def test_important_positive():
    assert _is_important("Санкции против России ужесточаются")


def test_important_market():
    assert _is_important("Сбер обвалился на 5 процентов")


def test_important_ai():
    assert _is_important("Новая нейросеть Claude обновилась")


def test_not_important_casual():
    assert not _is_important("привет как дела")


def test_import_does_not_require_creds():
    """Импорт модуля не должен требовать сессий/ключей."""
    # если импорт прошёл — значит модуль не падает без Telegram-кредов
    assert True
