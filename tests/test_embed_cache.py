"""Tests for embed_cache.py — Embedding Cache v2 с confidence scoring.

Ключевые инварианты:
- числа — это факты: несовпадение чисел никогда не даёт SAFE;
- volatile-вопросы не кэшируются и не отдаются;
- эмбеддинг детерминирован между процессами (иначе JSON-персистентность бессмысленна).
"""

import time

from token_diet.embed_cache import (
    CacheStats,
    Decision,
    EmbeddingCache,
    HashedNGramEmbedder,
    cached_answer,
    content_words,
    cosine,
    extract_entities,
    extract_numbers,
)


def _pushkin_cache():
    """Фикстура-хелпер: кэш с одним стабильным фактом."""
    cache = EmbeddingCache()
    cache.put(
        "В каком году Пушкин написал Евгения Онегина?",
        "Пушкин писал его более семи лет, с 1823 по 1830 год.",
    )
    return cache


class TestHashedNGramEmbedder:
    def test_deterministic_across_instances(self):
        """Один и тот же текст → один и тот же вектор (стабильный md5-хэш)."""
        e1 = HashedNGramEmbedder().embed("кэширование ответов без риска")
        e2 = HashedNGramEmbedder().embed("кэширование ответов без риска")
        assert e1 == e2
        assert all(v == 0 for v in HashedNGramEmbedder().embed(""))

    def test_normalized(self):
        vec = HashedNGramEmbedder().embed("hello world of tokens")
        norm = sum(v * v for v in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_similar_texts_more_similar_than_unrelated(self):
        e = HashedNGramEmbedder()
        base = e.embed("how to reduce llm token costs")
        close = e.embed("how to reduce llm token cost")
        far = e.embed("recipe for apple pie with cinnamon")
        assert cosine(base, close) > cosine(base, far)

    def test_dim_validation(self):
        import pytest
        with pytest.raises(ValueError):
            HashedNGramEmbedder(dim=8)


class TestExtractors:
    def test_numbers_comma_dot_equivalent(self):
        assert extract_numbers("рост 3,5 процента") == {"3.5"}
        assert extract_numbers("цена 180.25") == {"180.25"}

    def test_content_words_drop_stopwords(self):
        words = content_words("What is the price of AAPL today?")
        assert "price" in words and "aapl" in words
        assert "the" not in words and "what" not in words

    def test_entities_catch_tickers(self):
        ents = extract_entities("Should I buy AAPL or MSFT now?")
        assert "AAPL" in ents and "MSFT" in ents


class TestVolatileGuard:
    def test_live_question_is_volatile(self):
        cache = EmbeddingCache()
        assert cache.is_volatile("Какая цена AAPL прямо сейчас?")

    def test_stable_question_not_volatile(self):
        cache = EmbeddingCache()
        assert not cache.is_volatile("Кто написал роман Война и мир?")

    def test_volatile_never_cached(self):
        cache = EmbeddingCache()
        assert cache.put("Погода в Москве сейчас?", "Солнечно") is False
        assert cache.stats.refused_puts == 1
        assert len(cache) == 0


class TestLookupDecisions:
    def test_exact_match(self):
        q = "В каком году Пушкин написал Евгения Онегина?"
        d = _pushkin_cache().lookup(q)
        assert d.action == "EXACT"
        assert d.confidence == 1.0
        assert d.answer and "1823" in d.answer

    def test_paraphrase_scores_safe(self):
        cache = _pushkin_cache()
        cache = _pushkin_cache()
        d = cache.lookup("В какие годы Пушкин писал Евгения Онегина?")
        assert d.action == "SAFE", f"got {d.action} conf={d.confidence} sim={d.similarity}"
        assert d.confidence >= cache.min_confidence

    def test_numeric_mismatch_never_safe(self):
        """Числа различаются → вето: RISKY или MISS, но не SAFE."""
        cache = EmbeddingCache(similarity_threshold=0.5)
        cache.put("Цена акции равна 175 долларов", "Ответ про 175")
        d = cache.lookup("Цена акции равна 180 долларов")
        assert d.action != "SAFE"
        assert d.confidence <= EmbeddingCache.NUMERIC_VETO_CAP + 1e-9

    def test_unrelated_question_misses(self):
        d = _pushkin_cache().lookup("Как приготовить борщ?")
        assert d.action == "MISS"

    def test_empty_cache_misses(self):
        assert EmbeddingCache().lookup("что угодно").action == "MISS"


class TestLifecycle:
    def test_ttl_expiry(self):
        cache = EmbeddingCache(ttl_seconds=1.0)
        cache.put("вопрос без волатильности", "ответ")
        entry = next(iter(cache._entries.values()))
        entry.created = time.time() - 10
        assert cache.lookup("вопрос без волатильности").action == "MISS"

    def test_lru_eviction(self):
        cache = EmbeddingCache(max_entries=1)
        cache.put("первый стабильный вопрос", "ответ 1")
        cache.put("второй стабильный вопрос", "ответ 2")
        assert len(cache) == 1
        assert cache._entries[next(iter(cache._entries))].answer == "ответ 2"

    def test_stats_hit_rate(self):
        cache = _pushkin_cache()
        cache.lookup("В каком году Пушкин написал Евгения Онегина?")
        cache.lookup("совершенно другой вопрос про квант")
        s = cache.stats
        assert s.lookups == 2 and s.exact_hits == 1 and s.misses == 1
        assert s.hit_rate == 0.5
        assert s.tokens_saved > 0

    def test_save_load_roundtrip(self, tmp_path):
        path = tmp_path / "cache.json"
        c1 = EmbeddingCache(path=path)
        c1.put("Столица Австралии — какой город?", "Канберра.")
        assert c1.save() == path

        c2 = EmbeddingCache(path=path)
        restored = c2.load()
        assert restored == 1
        d = c2.lookup("Столица Австралии — какой город?")
        assert d.action == "EXACT"

    def test_load_skips_expired(self, tmp_path):
        import json
        path = tmp_path / "cache.json"
        path.write_text(json.dumps({
            "version": 2,
            "entries": [{
                "q": "старый вопрос",
                "a": "старый ответ",
                "created": time.time() - 99999,
                "hits": 0,
                "vec": {},
            }],
        }))
        c = EmbeddingCache(path=path)
        assert c.load() == 0

    def test_clear_resets_everything(self):
        cache = _pushkin_cache()
        cache.lookup("В каком году Пушкин написал Евгения Онегина?")
        cache.clear()
        assert len(cache) == 0
        assert cache.stats.lookups == 0


class TestCachedAnswerHelper:
    def test_compute_once_then_serve(self):
        calls: list[str] = []

        def compute(q: str) -> str:
            calls.append(q)
            return "Вычисленный ответ"

        cache = EmbeddingCache()
        q = "Стабильный вопрос о структуре ДНК"
        a1, d1 = cached_answer(cache, q, compute)
        a2, d2 = cached_answer(cache, q, compute)

        assert a1 == a2 == "Вычисленный ответ"
        assert calls == [q]                      # compute ровно один раз
        assert d1.action == "MISS" and d2.action == "EXACT"

    def test_risky_falls_through_to_compute(self):
        calls: list[str] = []

        def compute(q: str) -> str:
            calls.append(q)
            return "Свежий ответ"

        cache = EmbeddingCache(min_confidence=0.99)
        cache.put("вопрос про 100 единиц измерения", "почти идентичный вопрос")
        answer, d = cached_answer(cache, "вопрос про сто единиц измерения", compute)
        assert answer == "Свежий ответ"          # RISKY не отдаётся из кэша
        assert d.action in ("RISKY", "MISS")


class TestCustomEmbedder:
    def test_external_embed_function_respected(self):
        def fake_embed(text: str) -> list[float]:
            return [1.0, 0.0] if "cat" in text else [0.0, 1.0]

        cache = EmbeddingCache(embed=fake_embed)
        cache.put("cat question about gravity", "cats fall gracefully")
        d = cache.lookup("cat query regarding gravity")
        assert d.action == "SAFE"
        assert cache._dim == 2                   # размерность выведена из embed

    def test_sparse_vector_roundtrip_preserves_dim(self, tmp_path):
        path = tmp_path / "c.json"
        c1 = EmbeddingCache(dim=64, path=path)
        c1.put("вопрос про орбиты планет", "Кеплер, эллипсы.")
        assert c1.save() == path                # без save() грузить нечего
        c2 = EmbeddingCache(dim=64, path=path)
        c2.load()
        d = c2.lookup("вопрос про орбиты планет")
        assert d.action == "EXACT"
        assert c2._dim == 64


def test_decision_namedtuple_fields():
    d = Decision("MISS", None, 0.0, "no similar entry", 0.0)
    assert d.action == "MISS" and d.reason == "no similar entry"


def test_stats_defaults():
    s = CacheStats()
    assert s.hit_rate == 0.0
