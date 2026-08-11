"""Tests for efficient_thinking — 2026 reasoning techniques."""

from __future__ import annotations

import unittest

from token_diet.efficient_thinking import (
    adaptive_effort_prompt,
    chain_of_draft_compress,
    chain_of_draft_prompt,
    detect_overthinking,
    estimate_effort,
    optimize_thinking,
)
from token_diet import count_tokens


class TestChainOfDraft(unittest.TestCase):
    def test_prompt_built(self):
        p = chain_of_draft_prompt("Сколько будет 2+2?")
        self.assertIn("Q: Сколько будет 2+2?", p)
        self.assertIn("Draft:", p)
        self.assertIn("MAXIMUM of 5 words", p)

    def test_compress_verbose_trace(self):
        verbose = (
            "First, let me think about this problem carefully. "
            "We need to subtract the number of lollipops given away. "
            "So 20 minus 12 equals 8. "
            "Therefore the answer is 8."
        )
        draft, before, after = chain_of_draft_compress(verbose)
        self.assertEqual(before, count_tokens(verbose))
        self.assertLessEqual(after, before)
        self.assertIn("8", draft)

    def test_short_trace_unchanged(self):
        short = "x = 42"
        draft, before, after = chain_of_draft_compress(short)
        self.assertEqual(draft, short)


class TestEstimateEffort(unittest.TestCase):
    def test_simple_question_draft(self):
        self.assertEqual(estimate_effort("Который час?"), "draft")

    def test_math_deep(self):
        self.assertEqual(
            estimate_effort("Реши уравнение 3x + 5 = 20 и докажи решение"),
            "deep",
        )

    def test_routine_standard(self):
        self.assertEqual(estimate_effort("Сравни два варианта и выбери лучший"),
                         "standard")

    def test_adaptive_prompt_has_mode(self):
        prompt, mode = adaptive_effort_prompt("Как дела?")
        self.assertEqual(mode, "draft")
        self.assertIn("Q: Как дела?", prompt)


class TestOverthinking(unittest.TestCase):
    def test_no_overthinking_on_clean(self):
        res = detect_overthinking("answer is 42", threshold_oscillations=3)
        self.assertFalse(res.is_overthinking)

    def test_detects_oscillation(self):
        trace = (
            "The answer is 7. Wait, no, actually it's 8. "
            "Hmm, let me reconsider... on second thought 9. "
            "Never mind, I was wrong, it's 10. Actually 11. "
            "No wait, hold on, 12."
        )
        res = detect_overthinking(trace, threshold_oscillations=3,
                                  threshold_tokens=10)
        self.assertTrue(res.is_overthinking)
        self.assertGreaterEqual(res.oscillation_count, 3)

    def test_render(self):
        res = detect_overthinking("ok answer is 42")
        self.assertIn("✓ норм", res.render())


class TestOptimizeThinking(unittest.TestCase):
    def test_auto_mode(self):
        r = optimize_thinking("Что такое Python?")
        self.assertIn(r["mode"], ("draft", "standard", "deep"))
        self.assertIn("prompt", r)
        self.assertGreaterEqual(r["estimated_reasoning_savings_pct"], 0)


if __name__ == "__main__":
    unittest.main()
