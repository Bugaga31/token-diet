"""Date Anchor — LLMs must never guess the date.

УРОК: я написал «сегодня 11 августа», а было 10-е. Модель, которая не знает
точную дату, ломает весь анализ времени: «завтра», «послезавтра», «на этой
неделе», дивидендные отсечки, дедлайны. Это фатально для любой задачи.

Решение: модуль, который:
1. Берёт дату ИЗ СИСТЕМНЫХ ЧАСОВ (не угадывает!)
2. Умеет считать торговые дни (понедельник-пятница, без праздников)
3. Знает русские названия месяцев и дней
4. Внедряет якорь даты прямо в промпт любой модели

Принцип: НИКОГДА не спрашивай модель «какой сегодня день».
Всегда вставляй точную дату из date_anchor.

Для людей. Честность дешевле сожаления.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

# Русские названия
_MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_MONTHS_GEN_RU = [
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
]
_WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def today() -> date:
    """Точная сегодняшняя дата из системных часов. НЕ угадывает."""
    return date.today()


def now() -> datetime:
    """Точное текущее время из системных часов."""
    return datetime.now()


# ═══════════════════════════════════════════════════════════════════════════════
# Форматирование
# ═══════════════════════════════════════════════════════════════════════════════

def format_date_ru(d: date | None = None) -> str:
    """Формат: '10 августа 2026' (русские названия месяцев)."""
    d = d or today()
    return f"{d.day} {_MONTHS_RU[d.month - 1]} {d.year}"


def format_date_short(d: date | None = None) -> str:
    """Формат: '10.08.2026'."""
    d = d or today()
    return d.strftime("%d.%m.%Y")


def format_date_iso(d: date | None = None) -> str:
    """Формат: '2026-08-10' (ISO, безошибочный)."""
    d = d or today()
    return d.isoformat()


def weekday_ru(d: date | None = None) -> str:
    """День недели по-русски: 'понедельник'..."""
    d = d or today()
    return _WEEKDAYS_RU[d.weekday()]


def is_weekend(d: date | None = None) -> bool:
    """Суббота или воскресенье."""
    d = d or today()
    return d.weekday() >= 5


# ═══════════════════════════════════════════════════════════════════════════════
# Торговые дни (MOEX: пн-пт; праздники задаются отдельно)
# ═══════════════════════════════════════════════════════════════════════════════

# Праздники РФ 2026 (дни, когда MOEX закрыт) — обновляется по мере публикации
RUS_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6),
    date(2026, 1, 7), date(2026, 1, 8), date(2026, 2, 23), date(2026, 3, 9),
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 11), date(2026, 6, 12),
    date(2026, 11, 4),
}


def is_trading_day(d: date | None = None, holidays: set[date] | None = None) -> bool:
    """Является ли день торговым (не выходной и не праздник)."""
    d = d or today()
    if d.weekday() >= 5:
        return False
    holidays = holidays if holidays is not None else RUS_HOLIDAYS_2026
    return d not in holidays


def next_trading_day(d: date | None = None, holidays: set[date] | None = None) -> date:
    """Следующий торговый день строго ПОСЛЕ d.

    Пример: от пятницы → следующий понедельник.
    От понедельника → вторник.
    """
    d = d or today()
    d += timedelta(days=1)
    while not is_trading_day(d, holidays):
        d += timedelta(days=1)
    return d


def n_trading_days_ahead(n: int, d: date | None = None, holidays: set[date] | None = None) -> date:
    """Дата через N торговых дней (пропускает выходные и праздники).

    Пример: n_trading_days_ahead(1) от пятницы = следующий понедельник.
    n_trading_days_ahead(3) — через 3 торговых дня.
    """
    d = d or today()
    count = 0
    while count < n:
        d += timedelta(days=1)
        if is_trading_day(d, holidays):
            count += 1
    return d


def trading_days_between(start: date, end: date, holidays: set[date] | None = None) -> int:
    """Сколько торговых дней между start (не включая) и end (включая)."""
    count = 0
    d = start
    while d < end:
        d += timedelta(days=1)
        if is_trading_day(d, holidays):
            count += 1
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# Якорь даты для промпта — вставляется в любой системный промпт
# ═══════════════════════════════════════════════════════════════════════════════

_DATE_ANCHOR_TEMPLATE = (
    "CURRENT DATE ANCHOR (не угадывай дату, используй эти значения):\n"
    "  Today (ISO): {iso}\n"
    "  Today (RU):  {ru}\n"
    "  Weekday:     {weekday}\n"
    "  Is trading day: {trading}\n"
    "  Next trading day: {next_td}\n"
    "  In 3 trading days: {plus3}\n"
    "Все расчёты «завтра», «послезавтра», «на этой неделе» делай от TODAY={iso}."
)


def date_anchor_block(d: date | None = None) -> str:
    """Сформировать блок-якорь даты для вставки в промпт.

    Любая модель, получившая этот блок, НЕ сможет перепутать даты:
    все временные ссылки привязаны к точному TODAY.
    """
    d = d or today()
    return _DATE_ANCHOR_TEMPLATE.format(
        iso=format_date_iso(d),
        ru=format_date_ru(d),
        weekday=weekday_ru(d),
        trading="yes" if is_trading_day(d) else "no (weekend/holiday)",
        next_td=format_date_iso(next_trading_day(d)),
        plus3=format_date_iso(n_trading_days_ahead(3, d)),
    )


def inject_date_anchor(system_prompt: str = "", d: date | None = None) -> str:
    """Внедрить якорь даты в системный промпт.

    Ставится В НАЧАЛО промпта (максимальный приоритет внимания).
    Если блок уже есть — не дублирует.
    """
    block = date_anchor_block(d)
    if not system_prompt or not system_prompt.strip():
        return block
    if "CURRENT DATE ANCHOR" in system_prompt:
        return system_prompt
    return block + "\n\n---\n\n" + system_prompt


# ═══════════════════════════════════════════════════════════════════════════════
# Полезные расчёты для финансов
# ═══════════════════════════════════════════════════════════════════════════════

def days_until(d: date, target: date | None = None) -> int:
    """Календарных дней до целевой даты (может быть отрицательным)."""
    target = target or today()
    return (d - target).days


def trading_days_until(d: date, target: date | None = None) -> int:
    """Торговых дней до целевой даты."""
    target = target or today()
    return trading_days_between(target, d)


def describe_horizon(days: int) -> str:
    """Человеческое описание горизонта: 'сегодня', 'завтра', 'на этой неделе'..."""
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    if days == 2:
        return "послезавтра"
    if days <= 5:
        return "на этой неделе"
    if days <= 14:
        return "в течение двух недель"
    if days <= 31:
        return "в течение месяца"
    if days <= 92:
        return "в течение квартала"
    return "в долгосрочной перспективе"


def full_date_context(d: date | None = None) -> dict[str, Any]:
    """Полный контекст даты одной структурой (для API/тестов)."""
    d = d or today()
    plus3 = n_trading_days_ahead(3, d)
    return {
        "today": format_date_iso(d),
        "today_ru": format_date_ru(d),
        "today_short": format_date_short(d),
        "weekday": weekday_ru(d),
        "is_weekend": is_weekend(d),
        "is_trading_day": is_trading_day(d),
        "next_trading_day": format_date_iso(next_trading_day(d)),
        "in_1_trading_day": format_date_iso(n_trading_days_ahead(1, d)),
        "in_3_trading_days": format_date_iso(plus3),
        "horizon_3td": describe_horizon(trading_days_between(d, plus3)),
    }
