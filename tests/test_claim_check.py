"""Tests for claim_check.py — числовые заявления против фактического вывода.

Три класса тестов помечены REGRESSION: они закрывают баги, которые модуль
допустил при первом же прогоне по настоящему README. Это те же баги, против
которых он написан, — поэтому они зафиксированы, а не просто исправлены.
"""

from token_diet.claim_check import (
    Claim,
    ClaimAudit,
    HardcodedVerdict,
    Incoherence,
    Verification,
    audit_text,
    check_coherence,
    claim_check_prompt,
    extract_claims,
    find_hardcoded_verdicts,
    verify_claim,
)


class TestExtractClaims:
    def test_percent_savings(self):
        claims = extract_claims("Сокращает расходы на 33% дешевле обычного.")
        assert any(c.kind == "cost_reduction" and c.value == 33 for c in claims)

    def test_english_savings(self):
        claims = extract_claims("Delivers 42.9% savings on every call.")
        assert any(c.kind == "cost_reduction" and c.value == 42.9 for c in claims)

    def test_multiplier(self):
        claims = extract_claims("Packs 5.8× more context.")
        assert any(c.kind == "multiplier" and c.value == 5.8 for c in claims)

    def test_test_count(self):
        claims = extract_claims("1172 tests passed")
        assert any(c.kind == "test_count" and c.value == 1172 for c in claims)

    def test_line_numbers_are_1_indexed(self):
        claims = extract_claims("первая строка\nэкономия 20%")
        assert claims[0].line == 2

    def test_version_not_parsed_as_number(self):
        """«3.5.0» — версия, не число: три точки не должны стать float."""
        claims = extract_claims("Release v3.22.1 is out")
        versions = [c for c in claims if c.kind == "version"]
        assert versions and versions[0].value == 0.0

    def test_proof_nearby_detected(self):
        c = extract_claims("Экономия 22.8% (измерено ci_check.py)")[0]
        assert c.has_proof_nearby is True
        assert c.needs_verification is False

    def test_file_line_ref_counts_as_proof(self):
        c = extract_claims("Даёт 22.8% экономии, см. ci_check.py:104")[0]
        assert c.has_proof_nearby is True

    def test_modelled_marker_detected(self):
        c = extract_claims("Потенциально 46.6% экономии при полном внедрении")[0]
        assert c.is_modelled is True
        assert c.needs_verification is False

    def test_bare_number_needs_verification(self):
        c = extract_claims("Экономит 46.6% расходов.")[0]
        assert c.needs_verification is True

    def test_empty_safe(self):
        assert extract_claims("") == []
        assert extract_claims(None) == []

    def test_no_duplicate_claims_on_same_line(self):
        claims = extract_claims("экономия 33% дешевле, дешевле на 33%")
        vals = [(c.kind, c.value) for c in claims]
        assert len(vals) == len(set(vals))


class TestBadgeDecoding:
    """REGRESSION: URL-кодировка в бейджах прятала настоящие числа.

    `tests-603%20passed` парсился как «20 passed» — исходное число терялось,
    а на его месте появлялось несуществующее. Устаревший бейдж проходил
    проверку незамеченным.
    """

    def test_url_encoded_test_count(self):
        badge = "[![Tests](https://img.shields.io/badge/tests-603%20passed-green.svg)]()"
        vals = [c.value for c in extract_claims(badge) if c.kind == "test_count"]
        assert 603 in vals
        assert 20 not in vals

    def test_url_encoded_absolute(self):
        badge = "[![CO2](https://img.shields.io/badge/CO2-204K%20tonnes%2Fyear-green.svg)]()"
        vals = [c.value for c in extract_claims(badge) if c.kind == "absolute"]
        assert 204_000 in vals
        assert 20 not in vals

    def test_plain_text_unaffected(self):
        vals = [c.value for c in extract_claims("saves 204,000 tonnes") if c.kind == "absolute"]
        assert 204_000 in vals

    def test_magnitude_suffixes(self):
        vals = [c.value for c in extract_claims("340B liters and 9.7 million trees")
                if c.kind == "absolute"]
        assert 340_000_000_000 in vals
        assert 9_700_000 in vals

    def test_malformed_percent_does_not_crash(self):
        assert extract_claims("100% saved at %ZZ badge") is not None


class TestVerifyClaim:
    def test_confirmed_within_tolerance(self):
        claim = extract_claims("Экономия 22% дешевле")[0]
        v = verify_claim(claim, "| TOTAL | 3208 | 2477 | 731 | 22.8% |", tolerance=1.0)
        assert v.verdict == "confirmed"
        assert v.ok is True

    def test_contradicted_outside_tolerance(self):
        claim = extract_claims("Экономия 46.6% дешевле")[0]
        v = verify_claim(claim, "saved 731 (22.8%)", tolerance=1.0)
        assert v.verdict == "contradicted"
        assert v.ok is False
        assert v.actual == 22.8

    def test_unsupported_when_no_such_number(self):
        claim = extract_claims("Packs 5.8× more context")[0]
        v = verify_claim(claim, "no multipliers here at all")
        assert v.verdict == "unsupported"

    def test_real_readme_test_badge_contradicted(self):
        """Настоящий баг: бейдж 603, фактический прогон 1213."""
        claim = extract_claims("tests-603 passed")[0]
        v = verify_claim(claim, "1213 passed, 8 subtests passed in 80.26s", tolerance=0)
        assert v.verdict == "contradicted"
        assert v.actual == 1213


class TestFactCandidates:
    """REGRESSION: «фактом» экономии становился порог гарда.

    В машинном отчёте экономия лежит в колонке таблицы (`| 22.8% |`) без
    ключевого слова, поэтому единственным найденным «фактом» оказывалась
    строка `need <5% savings` — и вердикт выходил верным по неверной
    причине. Факт в выводе скрипта обязан читаться в любом формате.
    """

    def test_bare_table_percent_is_a_candidate(self):
        claim = extract_claims("Экономия 22% дешевле")[0]
        v = verify_claim(claim, "| TOTAL | 731 | 22.8% |", tolerance=1.0)
        assert 22.8 in v.candidates

    def test_guard_threshold_does_not_become_the_only_fact(self):
        report = (
            "| TOTAL | 3208 | 2477 | 731 | 22.8% |\n"
            "GUARD: blob ref would cost +90 tokens (need <5% savings)\n"
        )
        claim = extract_claims("Экономия 46.6% дешевле")[0]
        v = verify_claim(claim, report, tolerance=1.0)
        assert v.actual == 22.8, "ближайшим должен быть замер, не порог гарда"
        assert 5.0 in v.candidates

    def test_detail_reports_full_range(self):
        claim = extract_claims("Экономия 46.6% дешевле")[0]
        v = verify_claim(claim, "22.8% and 42.9% and 56.3%", tolerance=1.0)
        assert "диапазон" in v.detail
        assert "кандидатов 3" in v.detail

    def test_candidates_deduplicated(self):
        claim = extract_claims("Экономия 22% дешевле")[0]
        v = verify_claim(claim, "22.8% ... 22.8% ... 22.8%", tolerance=1.0)
        assert v.candidates.count(22.8) == 1

    def test_multiplier_kind_not_polluted_by_percents(self):
        claim = extract_claims("Packs 5.8× more context")[0]
        v = verify_claim(claim, "savings were 22.8% overall")
        assert v.verdict == "unsupported"

    def test_returns_dataclass(self):
        claim = extract_claims("Экономия 22% дешевле")[0]
        assert isinstance(verify_claim(claim, "22.8%"), Verification)


class TestCheckCoherence:
    def test_catches_multiplier_plus_savings(self):
        """Главный баг README: «5.8× умнее при 33% дешевле»."""
        inc = check_coherence("Даёт 5.8× больше контекста при 33% дешевле.")
        assert len(inc) >= 1
        assert "РАЗНЫХ сценария" in inc[0].reason

    def test_catches_across_adjacent_lines(self):
        inc = check_coherence("Packs 3.4× more context.\nAnd it is 32.8% cheaper.")
        assert len(inc) >= 1

    def test_ignores_distant_lines(self):
        text = "Packs 3.4× more context.\n\n\n\n\nSeparately: 32.8% cheaper.\n"
        assert check_coherence(text) == []

    def test_small_multiplier_not_flagged(self):
        assert check_coherence("Даёт 1.1× больше контекста при 30% дешевле.") == []

    def test_tiny_savings_not_flagged(self):
        assert check_coherence("Даёт 5× больше контекста при 2% дешевле.") == []

    def test_savings_alone_is_fine(self):
        assert check_coherence("Экономит 22.8% токенов.") == []

    def test_returns_dataclass(self):
        inc = check_coherence("5.8× больше при 33% дешевле")
        assert isinstance(inc[0], Incoherence)


class TestFindHardcodedVerdicts:
    def test_catches_literal_comparison(self):
        src = 'lines.append("- Cost is **lower** than RAW despite 5x richer context")'
        found = find_hardcoded_verdicts(src)
        assert len(found) == 1
        assert "печатается всегда" in found[0].why

    def test_computed_verdict_not_flagged(self):
        src = 'print(f"cost is {\'lower\' if a < b else \'higher\'} than RAW")'
        assert find_hardcoded_verdicts(src) == []

    def test_comparison_on_line_not_flagged(self):
        src = 'if cost < raw:\n    print("Cost is lower than RAW")'
        assert find_hardcoded_verdicts(src) == []

    def test_comment_not_flagged(self):
        assert find_hardcoded_verdicts('# Cost is lower than RAW') == []

    def test_russian_verdict(self):
        found = find_hardcoded_verdicts('report.append("Стало дешевле")')
        assert len(found) == 1

    def test_reports_line_number(self):
        src = 'x = 1\ny = 2\nprint("it is better now")'
        assert find_hardcoded_verdicts(src)[0].line == 3

    def test_empty_safe(self):
        assert find_hardcoded_verdicts("") == []
        assert find_hardcoded_verdicts(None) == []

    def test_syntax_error_safe(self):
        assert find_hardcoded_verdicts("def broken( : print('is better')") == []

    def test_returns_dataclass(self):
        found = find_hardcoded_verdicts('print("Стало дешевле")')
        assert isinstance(found[0], HardcodedVerdict)


class TestVerdictNoiseFilter:
    """REGRESSION: первая версия давала 15 ложных на 2 настоящих.

    Она искала слово («better», «дешевле») в любой строке файла. Под нож
    попадали regex-паттерны, docstring'и, списки ключевых слов и вердикты,
    вычисленные в `if` на строку выше. Сканер с таким шумом бесполезен,
    поэтому разбор идёт по AST и требует связки-вердикта.
    """

    def test_regex_pattern_not_flagged(self):
        src = 'import re\n_C = re.compile(r"\\b(лучше|better|дешевле)\\b")'
        assert find_hardcoded_verdicts(src) == []

    def test_docstring_not_flagged(self):
        src = 'def f(other):\n    """Pareto: is this strictly better than other?"""\n    return 1'
        assert find_hardcoded_verdicts(src) == []

    def test_keyword_list_not_flagged(self):
        src = 'WORDS = ["выше рынка", "лучше рынка", "рекомендация покупать", "дивиденды"]'
        assert find_hardcoded_verdicts(src) == []

    def test_instruction_prose_not_flagged(self):
        """«Provide an improved response» — инструкция, не вердикт о факте."""
        src = 'p = "Provide an improved response addressing the critique."'
        assert find_hardcoded_verdicts(src) == []

    def test_verdict_guarded_by_if_on_earlier_line_not_flagged(self):
        """cache_keepalive.py:133 — вердикт под `if idle > horizon`."""
        src = (
            'def decide(idle, horizon):\n'
            '    if idle > horizon:\n'
            '        return Decision(reason=("простой большой — "\n'
            '                                "дешевле пере-префиллить заново"))\n'
        )
        assert find_hardcoded_verdicts(src) == []

    def test_real_bug_still_caught_among_noise(self):
        src = (
            'import re\n'
            '_C = re.compile(r"(лучше|better)")\n'
            'WORDS = ["выше рынка", "лучше рынка", "дивиденды"]\n'
            'def report(n):\n'
            '    """Is this better than that?"""\n'
            '    return [f"- Cost is **lower** than RAW despite 5x richer context"]\n'
        )
        found = find_hardcoded_verdicts(src)
        assert len(found) == 1
        assert "Cost is" in found[0].text

    def test_no_duplicate_for_fstring_constant(self):
        """AST обходит f-строку и её константу — находка должна быть одна."""
        src = 'x = [f"- Cost is **lower** than RAW despite 5x richer context"]'
        assert len(find_hardcoded_verdicts(src)) == 1


class TestFStringWithoutPlaceholder:
    """REGRESSION: префикс f без {} ничего не вычисляет.

    `smart_multiplier.py:181` — это f-строка БЕЗ единого плейсхолдера.
    Первая версия фильтра считала любой префикс f признаком вычисления и
    пропускала строку: именно так вбитая похвала и выжила в проде.
    """

    def test_fstring_without_placeholder_is_flagged(self):
        src = 'lines = [f"- Cost is **lower** than RAW despite 5x richer context"]'
        found = find_hardcoded_verdicts(src)
        assert len(found) == 1, "f-строка без {} не вычисляет ничего"

    def test_fstring_with_placeholder_not_flagged(self):
        src = 'lines = [f"- Cost is {verdict} than RAW"]'
        assert find_hardcoded_verdicts(src) == []

    def test_real_smart_multiplier_line(self):
        src = 'lines += [\n    f"- Cost is **lower** than RAW despite 5× richer context",\n]'
        assert len(find_hardcoded_verdicts(src)) == 1


class TestAuditText:
    def test_splits_proven_modelled_unverified(self):
        text = (
            "Экономия 22.8% (измерено ci_check.py)\n"
            "Потенциально 46.6% экономии\n"
            "Сокращает на 90% дешевле\n"
        )
        a = audit_text(text)
        assert len(a.proven) == 1
        assert len(a.modelled) == 1
        assert len(a.unverified) == 1

    def test_untrustworthy_with_bare_numbers(self):
        a = audit_text("Экономит 46.6% расходов.")
        assert a.trustworthy is False
        assert a.notes

    def test_trustworthy_when_all_proven(self):
        a = audit_text("Экономия 22.8% дешевле (измерено ci_check.py)")
        assert a.trustworthy is True

    def test_incoherence_makes_untrustworthy(self):
        a = audit_text("Проверено: 5.8× больше контекста при 33% дешевле (измерено).")
        assert a.incoherent
        assert a.trustworthy is False

    def test_summary_mentions_counts(self):
        s = audit_text("Экономит 46.6% расходов.").summary()
        assert "заявлений: 1" in s
        assert "голословно: 1" in s

    def test_empty_text_is_trustworthy(self):
        a = audit_text("")
        assert a.total == 0
        assert a.trustworthy is True

    def test_returns_dataclass(self):
        assert isinstance(audit_text("экономия 20% дешевле"), ClaimAudit)


class TestPrompt:
    def test_prompt_is_short(self):
        assert len(claim_check_prompt().split()) < 120

    def test_prompt_demands_source(self):
        p = claim_check_prompt().lower()
        assert "измерил" in p or "прогноз" in p

    def test_prompt_stable(self):
        assert claim_check_prompt() == claim_check_prompt()
