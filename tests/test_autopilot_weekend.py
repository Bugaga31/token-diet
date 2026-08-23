"""Тесты автопилота: защита выходных (урок 15.08 — не долбим API в Сб/Вс)."""

import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from token_diet.autopilot import is_trading_day


def test_monday_is_trading_day():
    assert is_trading_day(datetime.datetime(2026, 8, 17))  # понедельник


def test_friday_is_trading_day():
    assert is_trading_day(datetime.datetime(2026, 8, 14))  # пятница


def test_saturday_not_trading():
    assert not is_trading_day(datetime.datetime(2026, 8, 15))  # суббота


def test_sunday_not_trading():
    assert not is_trading_day(datetime.datetime(2026, 8, 16))  # воскресенье


def test_today_consistent_with_weekday():
    """is_trading_day() без аргумента = сегодня — по weekday()."""
    today = datetime.datetime.now()
    assert is_trading_day() == (today.weekday() < 5)
