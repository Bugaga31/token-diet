import sys

sys.path.insert(0, ".")

from token_diet.metacognition import (
    calibrated_verdict,
    difficulty,
    flag_uncertainty,
    metacognition_prompt,
    prior_confidence,
    self_critique,
)
from token_diet.step_back import (
    Case,
    analogy_keywords,
    analogy_prompt,
    find_analogies,
    step_back_prompt,
    step_back_question,
)


def test_difficulty_why_harder_than_what():
    easy = difficulty("что такое акция")
    hard = difficulty("почему золото растёт и как это влияет на инфляцию")
    assert hard.score > easy.score


def test_difficulty_comparison_and_numbers():
    d = difficulty("сравни Полюс и Газпром, у кого P/E ниже 5")
    assert d.score >= 0.3
    assert any("сравн" in r or "числ" in r for r in d.reasons)


def test_prior_confidence_bounds():
    for q in ["что такое вода", "докажи теорему об оптимизации портфеля с налогами"]:
        c = prior_confidence(q)
        assert 0.0 <= c <= 1.0


def test_flag_uncertainty_hedge():
    flags = flag_uncertainty("Наверное, Полюс вырастет, я думаю это вероятно.")
    kinds = {f.kind for f in flags}
    assert "hedge" in kinds


def test_flag_uncertainty_overconfident():
    flags = flag_uncertainty("Точно, 100%, гарантированно вырастет.")
    assert any(f.kind == "overconfident" for f in flags)


def test_flag_uncertainty_evasive():
    flags = flag_uncertainty("Не знаю, нет данных.")
    assert any(f.kind == "evasive" for f in flags)


def test_self_critique_empty():
    r = self_critique("что такое X", "")
    assert not r.is_clean
    assert r.score == 0.0


def test_self_critique_number_conflict():
    r = self_critique("цена акции 100 рублей", "цена 5000 рублей")
    assert any(c.severity == "critical" for c in r.critiques)


def test_self_critique_clean():
    r = self_critique("что такое акция", "Акция — это долевая ценная бумага, дающая право на часть прибыли компании.")
    assert r.score >= 0.8


def test_calibrated_verdict_double_check():
    v = calibrated_verdict("почему золото растёт и как это влияет на инфляцию", "наверное, точно вырастет")
    assert v.should_double_check
    assert 0.0 <= v.confidence <= 1.0


def test_metacognition_prompt_simple_empty():
    assert metacognition_prompt("что такое вода") == ""


def test_metacognition_prompt_hard_nonempty():
    p = metacognition_prompt("почему золото растёт")
    assert "уверен" in p


def test_step_back_question_why():
    g = step_back_question("почему золото растёт")
    assert "общие принципы" in g or "принцип" in g


def test_step_back_question_compare():
    g = step_back_question("что лучше: Сбер или Газпром")
    assert "Сбер" in g and "Газпром" in g


def test_step_back_prompt_two_stage():
    p = step_back_prompt("почему золото растёт")
    assert "шаг назад" in p
    assert "почему золото растёт" in p


def test_analogy_keywords_stopwords_removed():
    kw = analogy_keywords("как купить золото на бирже")
    assert "как" not in kw
    assert any("золот" in k or "бирж" in k or "купи" in k for k in kw)


def test_find_analogies_and_prompt():
    cases = [
        Case(title="Полюс", problem="золото растёт, покупаем акции золотодобытчика", solution="купить на откате"),
        Case(title="Вода", problem="как варить суп", solution="кипятить воду"),
    ]
    hits = find_analogies("как заработать на росте золота", cases)
    assert hits
    assert hits[0][0].title == "Полюс"
    p = analogy_prompt("как заработать на росте золота", cases)
    assert "Полюс" in p
