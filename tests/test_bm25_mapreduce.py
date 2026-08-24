"""Tests for reverse-engineered retrieval & long-doc modules: BM25 + map-reduce."""

from __future__ import annotations

import unittest

from token_diet.bm25_reranker import (
    BM25Reranker,
    rerank_chunks,
)
from token_diet.map_reduce import (
    extractive_summarize,
    map_reduce,
    split_into_chunks,
)


class TestBM25Reranker(unittest.TestCase):
    def setUp(self):
        self.docs = [
            "Полюс добывает золото в Сибири и на Урале.",
            "Яндекс занимается поиском и рекламой в интернете.",
            "Сбербанк выдаёт кредиты и принимает вклады.",
            "Золото — драгоценный металл, Полюс его добывает.",
        ]

    def test_rare_term_wins(self):
        r = BM25Reranker(self.docs)
        hits = r.rerank("золото добыча", top_k=2)
        # The doc that mentions both rare words should rank top
        self.assertEqual(hits[0].index, 0)

    def test_top_k_limits(self):
        r = BM25Reranker(self.docs)
        hits = r.rerank("золото", top_k=2)
        self.assertLessEqual(len(hits), 2)

    def test_scores_ordered_desc(self):
        r = BM25Reranker(self.docs)
        hits = r.rerank("золото добыча", top_k=4)
        scores = [h.score for h in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_rerank_chunks(self):
        hits = rerank_chunks("золото", self.docs, top_k=2)
        self.assertEqual(len(hits), 2)
        self.assertIn("золото", hits[0].text.lower())

    def test_empty(self):
        r = BM25Reranker([])
        self.assertEqual(r.rerank("x"), [])


class TestMapReduce(unittest.TestCase):
    def test_split_into_chunks(self):
        text = "Слово. " * 3000  # ~18k chars
        chunks = split_into_chunks(text, max_chars=5000)
        self.assertGreater(len(chunks), 2)
        for c in chunks:
            self.assertLessEqual(len(c), 5100)

    def test_map_reduce_deterministic(self):
        text = " ".join(
            f"Пункт {i}: компания увеличила выручку на {i} процентов." for i in range(50)
        )
        res = map_reduce(text, query="выручка", max_chunk_chars=400)
        self.assertFalse(res.used_llm)          # no LLM passed
        self.assertGreater(res.n_chunks, 1)
        self.assertIn("Пункт", res.answer)
        self.assertGreater(res.total_tokens, 0)
        # honest behavior: small chunks may not compress (partials add up),
        # but the pipeline must always produce a non-empty answer
        self.assertTrue(len(res.answer) > 0)

    def test_map_reduce_saves_on_large_text(self):
        # On a genuinely long, repetitive document the extractive MAP
        # step must compress (real-world case, e.g. FRESH_NEWS 24% saving)
        text = "\n".join(
            f"Новость {i}: компания отчиталась о росте выручки на {i} процентов. "
            f"Дивиденды рекомендованы советом директоров." for i in range(120)
        )
        res = map_reduce(text, query="выручка", max_chunk_chars=3000)
        self.assertGreater(res.n_chunks, 1)
        self.assertGreater(res.input_tokens, res.total_tokens)
        self.assertGreater(res.savings_pct, 0)

    def test_map_reduce_with_callables(self):
        def my_map(chunk, query):
            return f"MAP({len(chunk)})"

        def my_reduce(partials, query):
            return f"REDUCE({len(partials)})"

        res = map_reduce(
            "Один. " * 3000,
            map_fn=my_map,
            reduce_fn=my_reduce,
            max_chunk_chars=2000,
        )
        self.assertTrue(res.used_llm)
        self.assertTrue(res.answer.startswith("REDUCE("))
        self.assertGreater(res.n_chunks, 2)

    def test_extractive_summarize(self):
        text = " ".join(f"Предложение номер {i} с важной информацией." for i in range(20))
        out = extractive_summarize(text, max_chars=500)
        self.assertLess(len(out), len(text))
        self.assertIn("Предложение", out)

    def test_empty_input(self):
        res = map_reduce("", query="")
        self.assertEqual(res.n_chunks, 0)


if __name__ == "__main__":
    unittest.main()
