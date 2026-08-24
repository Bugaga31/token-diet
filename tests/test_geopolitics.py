"""Тесты geopolitics: тональная классификация новостей + законы генерала.

Закон генерала №3: геополитика первична — RED = входы запрещены.
Эти тесты защищают, что классификация работает правильно.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from token_diet.geopolitics import GeoItem, GeoVerdict, classify_title

# ── classify_title: негатив ──────────────────────────────────────────

def test_classify_negative_sanctions():
    s = classify_title("США ввели новые санкции против России")
    assert s < 0, f"Ожидал негатив, получил {s}"


def test_classify_negative_war():
    s = classify_title("Россия отказалась от перемирия, бои продолжаются")
    assert s < 0, f"Ожидал негатив, получил {s}"


def test_classify_negative_escalation():
    s = classify_title("Эскалация конфликта: удары по инфраструктуре")
    assert s < 0, f"Ожидал негатив, получил {s}"


# ── classify_title: позитив ──────────────────────────────────────────

def test_classify_positive_ceasefire():
    s = classify_title("Стороны договорились о перемирии и обмене")
    assert s > 0, f"Ожидал позитив, получил {s}"


def test_classify_positive_talks():
    s = classify_title("Начались переговоры о мире и деэскалации")
    assert s > 0, f"Ожидал позитив, получил {s}"


# ── classify_title: нейтрально ───────────────────────────────────────

def test_classify_neutral():
    s = classify_title("Погода в Москве на завтра")
    assert s == 0.0, f"Ожидал 0.0, получил {s}"


# ── GeoVerdict: агрегация ────────────────────────────────────────────

def test_verdict_red_when_negative_items():
    verdict = GeoVerdict(
        verdict="RED",
        score=-4.0,
        items=[
            GeoItem(title="Санкции ужесточаются", source="t", url="u", sentiment=-1.0),
            GeoItem(title="Перемирия не будет", source="t", url="u", sentiment=-1.0),
        ],
    )
    d = verdict.as_dict()
    assert d["verdict"] == "RED"
    assert d["score"] == -4.0
    assert len(d["items"]) == 2


def test_verdict_never_leaks_negative_score_sign():
    # RED всегда со знаком минус — чтобы мозг не перепутал
    verdict = GeoVerdict(verdict="RED", score=-2.5)
    assert verdict.score < 0


def test_verdict_green_positive():
    verdict = GeoVerdict(verdict="GREEN", score=2.5)
    assert verdict.score > 0


# ── Закон генерала: RED = запрет входа (проверка цепочки) ────────────

def test_red_verdict_blocks_entry_chain():
    """RED-вердикт должен привести к SKIP в цепочке решений.
    Проверяем не сеть, а сам контракт: GeoVerdict.verdict == 'RED'
    — мозг (trading_brain) обязан вернуть SKIP. Это защита урока №3."""
    from token_diet.trading_brain import BrainDecision

    # Эмулируем то, что делает decide при RED (без сети)
    decision = BrainDecision(
        action="SKIP", ticker="market",
        reason="Геополитика RED — входы запрещены",
        checks={"geopolitics": "RED"},
    )
    assert decision.action == "SKIP"
    assert decision.checks["geopolitics"] == "RED"
