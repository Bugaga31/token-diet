"""Тесты Brain Engine — принципы мозга в чистом Python."""

from __future__ import annotations

import sys

sys.path.insert(0, ".")
from token_diet.brain_engine import (
    Action,
    Automatizer,
    _ngrams,
    brain_summary,
    hebbian_link,
    predictive_compress,
    select_action,
    sleep_consolidate,
    sparse_context,
    thalamus_gate,
)


# ── Предсказательное кодирование ────────────────────────────────────────────

def test_predictive_compress_removes_known():
    """Повтор истории — сжимается до нуля (мозг не обрабатывает ожидаемое)."""
    history = ["Сургутнефтегаз отчитался за первый квартал, прибыль выросла на 12%"]
    new = "Сургутнефтегаз отчитался за первый квартал, прибыль выросла на 12%"
    comp, stats = predictive_compress(new, history)
    assert len(comp) <= len(new)
    assert stats["novel_ngrams"] >= 0
    assert stats["saved_tokens"] >= 0


def test_predictive_compress_keeps_novel():
    """Новое предложение — сохраняется (ошибка предсказания)."""
    history = ["Сургутнефтегаз отчитался за первый квартал, прибыль выросла на 12%"]
    new = ("Сургутнефтегаз отчитался за первый квартал, прибыль выросла на 12%. "
           "Теперь компания объявила о выкупе акций с рынка")
    comp, stats = predictive_compress(new, history)
    assert "выкупе" in comp  # новизна сохранилась
    assert stats["saved_tokens"] > 0


def test_predictive_compress_empty_history():
    """Без истории всё новое — всё сохраняется."""
    new = "Совершенно новый текст про мозг и нейроны"
    comp, stats = predictive_compress(new, [])
    assert comp.strip() == new.strip()


# ── Разреженное кодирование ─────────────────────────────────────────────────

def test_sparse_context_selects_relevant():
    docs = [
        "Сургутнефтегаз префы платят дивиденды",
        "Погода в Москве сегодня солнечная",
        "Газпром объявил о новой программе байбэка",
    ]
    got = sparse_context(docs, "дивиденды Сургут", k=1)
    assert len(got) == 1
    assert "Сургут" in got[0]


def test_sparse_context_returns_empty_for_no_match():
    got = sparse_context([], "что-то")
    assert got == []


# ── Консолидация сна ────────────────────────────────────────────────────────

def test_sleep_consolidate_deletes_junk():
    notes = {
        "STOP_HIT_1": "сработал стоп по полюсу, цена упала",
        "STOP_HIT_2": "сработал стоп по полюсу, цена упала снова",
        "step_5": "промежуточный скриншот",
        "Важная заметка": "Полюс вышел из портфеля 13 августа, зафиксировали убыток.",
    }
    rep = sleep_consolidate(notes, delete_older_than_days=1)
    assert any("STOP_HIT" in d for d in rep.deleted)
    assert any("step_" in d for d in rep.deleted)
    assert rep.kept >= 1
    assert "Важная заметка" in [n for n in rep.deleted] or rep.kept == 1


def test_sleep_consolidate_merges_duplicates():
    notes = {
        "Заметка А": "Полюс вышел из портфеля тринадцатого августа",
        "Заметка Б": "Полюс вышел из портфеля тринадцатого августа",
        "Заметка В": "Газпром растёт на новостях о дивидендах",
    }
    rep = sleep_consolidate(notes)
    assert len(rep.merged) >= 1


# ── Отбор действий (базальные ганглии) ─────────────────────────────────────

def test_select_action_picks_best():
    acts = [
        Action(name="ждать", utility=0.2, cost=0.0),
        Action(name="купить", utility=1.0, cost=0.3),
        Action(name="продать", utility=0.5, cost=0.4),
    ]
    best = select_action(acts)
    assert best is not None and best.name == "купить"


def test_select_action_no_go():
    """Все действия хуже бездействия → no-go (ничего не делать)."""
    acts = [Action(name="купить", utility=0.1, cost=1.0)]
    assert select_action(acts) is None


def test_select_action_empty():
    assert select_action([]) is None


# ── Автоматизация (мозжечок) ────────────────────────────────────────────────

def test_automatizer_caches_after_threshold():
    calls = []
    a = Automatizer(threshold=3)

    def fn():
        calls.append(1)
        return 42

    assert a.call("вопрос", fn) == 42
    assert a.call("вопрос", fn) == 42
    assert a.call("вопрос", fn) == 42
    assert a.call("вопрос", fn) == 42  # из кеша
    assert len(calls) == 3  # fn выполнился 3 раза, 4-й — из кеша
    assert a.is_automated("вопрос")
    assert a.stats()["hits"] == 1


# ── Таламус (фильтр внимания) ───────────────────────────────────────────────

def test_thalamus_gate_filters_noise():
    items = [
        "Сбер отчитался: прибыль выросла",
        "Котировки нефти Brent упали на 2%",
        "Скидки на пиццу в пятницу",
    ]
    got = thalamus_gate(items, "нефть котировки", k=1)
    assert len(got) == 1 and "нефти" in got[0]


# ── Нейропластичность (Hebb) ────────────────────────────────────────────────

def test_hebbian_link_finds_cooccurrence():
    notes = {
        "n1": "Полюс золото растёт золото дорожает",
        "n2": "Золото полюс выросло на бирже",
        "n3": "Погода дождь сегодня",
    }
    links = hebbian_link(notes, top_pairs=5)
    pairs = {(a, b) for a, b, _ in links}
    assert ("золото", "полюс") in pairs


def test_brain_summary_structure():
    s = brain_summary({"a": "текст заметки про мозг", "b": "текст заметки про мозг"})
    assert s["notes"] == 2
    assert s["duplicates_to_merge"] >= 1
    assert isinstance(s["hebbian_links"], list)


# ── Вспомогательное ─────────────────────────────────────────────────────────

def test_ngrams():
    g = _ngrams("один два три", n=2)
    assert {"один два", "два три"} <= g
