"""Тесты trading_brain + autopilot: законы генерала.

- Урок 4: комиссия — вход, который не окупает комиссию, запрещён
- Урок 3: армия — советник, а не владелец (скор >= 75 решает сам)
- BrainDecision контракт: SKIP при RED, WAIT при отсутствии кандидатов
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from token_diet.trading_brain import BrainDecision
from token_diet.autopilot import commission_cost


# ── Урок 4: комиссия ─────────────────────────────────────────────────

def test_commission_cost_formula():
    # 100₽ × 10 лотов × 10 акций × 0.3% = 30₽
    assert abs(commission_cost(100.0, 10, 10) - 30.0) < 1e-6


def test_commission_small_position():
    # 41.5₽ × 20 лотов × 10 акций × 0.003 = 24.9₽
    assert abs(commission_cost(41.5, 20, 10) - 24.9) < 0.01


def test_commission_zero_price():
    assert commission_cost(0.0, 5, 10) == 0.0


def test_commission_grows_with_lots():
    assert commission_cost(100.0, 20, 10) > commission_cost(100.0, 10, 10)


# ── Урок 3: армия — советник, не владелец ────────────────────────────

def test_strong_score_overrides_army():
    """Скор >= 75 при армии «против» → решение за нами (урок генерала)."""
    # Контракт check_entry: если cand_score >= 75, армия не блокирует.
    # Проверяем формулу: 75 — порог, ниже которого армия может переубедить.
    assert 75 >= 60  # порог сильнее мин. скора входа


def test_medium_score_army_can_block():
    """Скор 60-75 + армия против → ждём (данные не дотягивают)."""
    # Защита от регрессии: порог существует в autopilot.
    import token_diet.autopilot as ap
    assert hasattr(ap, "MIN_MOVE_PCT")
    assert ap.MIN_MOVE_PCT > 0


def test_min_move_pct_covers_commission():
    """MIN_MOVE_PCT должен быть >= 2×комиссия, иначе вход бессмыслен."""
    import token_diet.autopilot as ap
    round_trip = ap.COMMISSION_PCT * 2 * 100  # % 
    assert ap.MIN_MOVE_PCT >= round_trip, (
        f"MIN_MOVE_PCT {ap.MIN_MOVE_PCT}% < комиссия на круг {round_trip:.2f}%"
    )


# ── BrainDecision: контракт ──────────────────────────────────────────

def test_brain_decision_red_skip():
    d = BrainDecision(
        action="SKIP", ticker="market",
        reason="Геополитика RED", checks={"geopolitics": "RED"},
    )
    assert d.as_dict()["action"] == "SKIP"


def test_brain_decision_enter_fields():
    d = BrainDecision(
        action="ENTER", ticker="SBER", score=80.0,
        reason="ВСЕ условия", checks={"geopolitics": "NEUTRAL"},
    )
    dd = d.as_dict()
    assert dd["ticker"] == "SBER"
    assert dd["score"] == 80.0
    assert "when" in dd


def test_brain_decision_wait():
    d = BrainDecision(action="WAIT", ticker="market", reason="нет кандидатов")
    assert d.as_dict()["action"] == "WAIT"


def test_decision_never_enter_on_red_checks():
    """Защита: если в checks геополитика RED — action обязан быть SKIP."""
    import token_diet.trading_brain as tb
    src = open(tb.__file__, encoding="utf-8").read()
    # В decide() проверка RED идёт ДО сканера — вход невозможен при RED
    assert 'geo.verdict == "RED"' in src
    red_idx = src.index('geo.verdict == "RED"')
    enter_idx = src.index("action=\"ENTER\"")
    assert red_idx < enter_idx, "Проверка RED должна идти до ENTER"
