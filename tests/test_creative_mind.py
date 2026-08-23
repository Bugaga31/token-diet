"""Tests for creative_mind — нестандартное мышление."""

from token_diet.creative_mind import (
    InvertedDictionary,
    dream_consolidate,
    haiku_compress,
    creative_solve,
)
from token_diet.core import count_tokens


def test_inverted_dictionary_learn_and_compress():
    d = InvertedDictionary()
    # 5 слов — ровно в диапазон 3-7, символ §0 (2 токена) выгоднее
    phrase = "alpha beta gamma delta epsilon"
    texts = [phrase] * 5 + ["hello world"] * 2
    added = d.learn(texts, min_hits=3)
    assert added >= 1
    # символ создан (хотя бы sub-фраза)
    assert any("alpha" in k for k in d.entries)
    compressed, before, after = d.compress(phrase + " please")
    assert after < before
    assert "§" in compressed
    # распаковка точна для целой фразы
    assert d.decompress(compressed) == phrase + " please"
    assert d.dictionary_block().startswith("СЛОВАРЬ")


def test_inverted_dictionary_no_hits():
    d = InvertedDictionary()
    added = d.learn(["unique phrase one", "unique phrase two"], min_hits=3)
    assert added == 0
    assert len(d.entries) == 0


def test_dream_consolidate():
    d = InvertedDictionary()
    history = ["hello world hello world hello world"] * 4
    report = dream_consolidate(history, d)
    assert report.patterns_found == 4
    assert "💤" in report.render()


def test_haiku_compress():
    long_text = (
        "The refund policy states that refunds are issued within 14 days. "
        "Shipping is free above $50. Returns require an RMA number. "
        "Furthermore, it is important to note that the policy applies to all users. "
        "The compliance team must review every blocked trade within 48 hours."
    )
    haiku, before, after = haiku_compress(long_text)
    assert after < before
    # числа сохранены
    assert "14" in haiku or "48" in haiku
    assert "/" in haiku  # разделитель строк хайку


def test_haiku_short_unchanged():
    short = "hello world"
    out, before, after = haiku_compress(short)
    assert out == short
    assert before == after


def test_creative_solve_offline():
    v = creative_solve("Как сжать промпт на 50% без потери смысла?")
    assert len(v.angles) == 3
    assert v.synthesis
    assert v.confidence in ("firm", "firm_with_caveat", "needs_proof")
    assert "🎨" in v.render()


def test_creative_solve_with_llm():
    def fake_llm(prompt: str) -> str:
        return "fake answer for: " + prompt[:20]

    v = creative_solve("test question", llm_call=fake_llm)
    assert len(v.angles) == 3
    assert "fake answer" in v.synthesis or "fake" in " ".join(v.angles)


def test_export_via_init():
    import token_diet
    assert hasattr(token_diet, "creative_solve")
    assert hasattr(token_diet, "InvertedDictionary")
    assert hasattr(token_diet, "haiku_compress")
    # lazy import works
    d = token_diet.InvertedDictionary()
    assert isinstance(d, InvertedDictionary)
