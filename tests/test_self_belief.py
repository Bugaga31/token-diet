"""Tests for self_belief.py — заслуженная уверенность, не назначенная.

Главный инвариант: модуль НЕ ужесточает тон без доказательства. Если эти
тесты падают — модуль превратился в генератор slop, ровно того, из-за
которого в README появилось «5.8× при 33% дешевле».
"""

from token_diet.self_belief import (
    BeliefVerdict,
    FirmUpResult,
    PersistPlan,
    belief_prompt,
    confident_rewrite,
    earned_confidence,
    firm_up,
    persist,
    rewrite_surrender,
    strip_apology,
)


class TestStripApology:
    def test_removes_apology_sentence(self):
        out = strip_apology("Извините за путаницу. Тесты прошли: 1172 passed.")
        assert "звин" not in out.lower()
        assert "1172 passed" in out

    def test_removes_self_deprecation_keeps_fact(self):
        out = strip_apology("Я всего лишь модель, но импорт занимает 1.08 с.")
        assert "всего лишь" not in out
        assert "1.08" in out

    def test_removes_english_apology(self):
        out = strip_apology("Sorry about that. The test suite passes.")
        assert "sorry" not in out.lower()
        assert "test suite passes" in out.lower()

    def test_removes_permission_seeking(self):
        out = strip_apology("Let me think about this. The answer is 42.")
        assert "let me think" not in out.lower()
        assert "42" in out

    def test_empty_input_safe(self):
        assert strip_apology("") == ""
        assert strip_apology(None) == ""

    def test_clean_text_untouched(self):
        src = "Прогнал тесты: 1172 passed за 167с."
        assert strip_apology(src) == src


class TestFirmUp:
    """Ядро модуля: хедж снимается ТОЛЬКО под доказательство."""

    def test_hedge_with_evidence_removed(self):
        r = firm_up("Мне кажется, импорт занимает 1.08 с (замерил).")
        assert r.hedges_removed == 1
        assert "кажется" not in r.text
        assert "1.08" in r.text

    def test_hedge_without_evidence_KEPT(self):
        """Самый важный тест: без пруфа хедж остаётся. Это честность."""
        r = firm_up("Вероятно, это ускорит работу вдвое.")
        assert r.hedges_kept == 1
        assert r.hedges_removed == 0
        assert "Вероятно" in r.text
        assert r.honest is True

    def test_file_line_counts_as_evidence(self):
        r = firm_up("Похоже, баг в server.py:251.")
        assert r.hedges_removed == 1
        assert "server.py:251" in r.text

    def test_percent_counts_as_evidence(self):
        r = firm_up("Кажется, сжатие даёт 22.8%.")
        assert r.hedges_removed == 1

    def test_mixed_text_splits_correctly(self):
        r = firm_up(
            "Кажется, тесты прошли: 1172 passed. Вероятно, стоит отрефакторить."
        )
        assert r.hedges_removed == 1   # первое — с пруфом
        assert r.hedges_kept == 1      # второе — без
        assert "Вероятно" in r.text

    def test_capitalizes_after_removal(self):
        r = firm_up("Кажется, замерил 500 мс.")
        assert r.text[0].isupper()

    def test_empty_safe(self):
        assert firm_up("").text == ""
        assert firm_up(None).text == ""

    def test_returns_dataclass(self):
        assert isinstance(firm_up("текст"), FirmUpResult)


class TestPersist:
    def test_module_not_found_hint(self):
        p = persist("ModuleNotFoundError: No module named ruff")
        assert "зависимост" in p.next_move
        assert p.diagnosis

    def test_permission_hint(self):
        assert "прав" in persist("Permission denied").next_move

    def test_timeout_hint(self):
        assert "таймаут" in persist("exit=124 timeout").next_move.lower()

    def test_unknown_error_gets_fallback(self):
        p = persist("something totally weird")
        assert p.next_move
        assert p.diagnosis == ""

    def test_escalates_when_exhausted(self):
        p = persist("timeout", attempt=3, max_attempts=3)
        assert p.escalate is True
        assert p.should_retry is False
        assert "пользовател" in p.as_line()

    def test_retries_while_attempts_left(self):
        p = persist("timeout", attempt=1, max_attempts=3)
        assert p.should_retry is True
        assert p.escalate is False
        assert p.attempts_left == 2

    def test_returns_dataclass(self):
        assert isinstance(persist("err"), PersistPlan)


class TestRewriteSurrender:
    def test_replaces_giving_up_with_next_move(self):
        out = rewrite_surrender("Не могу это сделать.", "Permission denied")
        assert "не могу" not in out.lower()
        assert "прав" in out

    def test_no_duplicate_when_two_surrenders(self):
        """Две формулировки сдачи = одна сдача, приём подставляется один раз."""
        out = rewrite_surrender(
            "Не могу это сделать, ничего не получается.",
            "ModuleNotFoundError: No module named yaml",
        )
        assert out.count("следующий приём") == 1

    def test_escalate_wording(self):
        out = rewrite_surrender("Сдаюсь.", "timeout", attempt=3)
        assert "пользовател" in out

    def test_leaves_normal_text_alone(self):
        src = "Сделал, проверил: 1172 passed."
        assert rewrite_surrender(src) == src


class TestEarnedConfidence:
    def test_empty_boast_gets_needs_proof(self):
        """Голословное «всё работает» НЕ получает твёрдый тон."""
        v = earned_confidence("Всё работает идеально, полностью готово!")
        assert v.tone == "needs_proof"
        assert v.earned is False
        assert v.unverified_boasts >= 1
        assert v.notes

    def test_evidence_gets_firm(self):
        v = earned_confidence("Прогнал тесты: 1172 passed за 167с. Проверил ci_check.py: PASS.")
        assert v.tone == "firm"
        assert v.earned is True
        assert v.evidence_count >= 2

    def test_partial_evidence_gets_caveat(self):
        v = earned_confidence(
            "Замерил: импорт 1.08 с. Вероятно, стоит сделать ленивый импорт."
        )
        assert v.tone == "firm_with_caveat"
        assert v.hedges_kept >= 1

    def test_no_evidence_no_boast_needs_proof(self):
        v = earned_confidence("Этот подход должен помочь.")
        assert v.tone == "needs_proof"
        assert v.earned is False

    def test_catches_real_readme_bug(self):
        """Настоящая строка из smart_multiplier.py:181 — вбитая похвала без расчёта."""
        v = earned_confidence("Cost is lower than RAW despite 5x richer context. Everything works.")
        assert v.earned is False
        assert v.unverified_boasts >= 1

    def test_apology_stripped_in_pipeline(self):
        v = earned_confidence("Извините. Замерил: 1.08 с.")
        assert "звин" not in v.text.lower()
        assert v.apologies_removed >= 1

    def test_returns_dataclass(self):
        assert isinstance(earned_confidence("текст"), BeliefVerdict)

    def test_overconfidence_is_not_evidence(self):
        """«Точно 100%» — усилитель, а не замер. Регрессия: раньше давало firm."""
        v = earned_confidence("Точно 100% всё работает идеально!")
        assert v.earned is False
        assert v.tone == "needs_proof"
        assert v.overconfident_markers >= 1

    def test_guaranteed_without_measurement_rejected(self):
        v = earned_confidence("Гарантированно 100% без ошибок")
        assert v.earned is False

    def test_real_percentage_still_counts(self):
        """Фикс не должен убить настоящее измерение с процентами."""
        v = earned_confidence("Прогнал: 100% тестов прошли, 1172 passed за 167с.")
        assert v.earned is True
        assert v.evidence_count >= 2

    def test_empty_safe(self):
        v = earned_confidence("")
        assert v.text == ""
        assert v.earned is False


class TestPrompt:
    def test_prompt_is_short(self):
        p = belief_prompt()
        assert len(p.split()) < 120

    def test_prompt_forbids_fake_success(self):
        p = belief_prompt().lower()
        assert "не проверил" in p or "честное" in p

    def test_prompt_stable(self):
        assert belief_prompt() == belief_prompt()


class TestConfidentRewrite:
    def test_one_liner_combines_all(self):
        out = confident_rewrite(
            "Извините, не могу это сделать.",
            "ModuleNotFoundError: No module named yaml",
        )
        assert "звин" not in out.lower()
        assert "не могу" not in out.lower()

    def test_preserves_facts(self):
        out = confident_rewrite("Я всего лишь модель. Замерил: 1172 passed.")
        assert "1172" in out
