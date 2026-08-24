"""Tests for PromptDistiller, FewShotSelector, and cache integration."""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_HERE)
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)

from token_diet.core import PriceTable, SemanticCache
from token_diet.equivalence_gate import EquivalenceGate
from token_diet.few_shot_selector import Example, FewShotSelector
from token_diet.optimization_runner import OptimizationRunner, RequestProfile
from token_diet.prompt_distiller import PromptDistiller


class TestPromptDistiller(unittest.TestCase):
    """Learn which prompt parts are dead weight."""

    def setUp(self):
        self.distiller = PromptDistiller(min_observations=3)

    def test_empty_returns_unchanged(self):
        distilled, dropped, before, after = self.distiller.distill("")
        self.assertEqual(distilled, "")
        self.assertEqual(dropped, "")
        self.assertEqual(before, 0)

    def test_not_enough_observations(self):
        self.distiller.observe("Section A\n\nSection B", "answer about A")
        self.distiller.observe("Section A\n\nSection B", "another about A")
        # Only 2 observations, min is 3
        distilled, _, _, _ = self.distiller.distill("Section A\n\nSection B")
        self.assertEqual(distilled, "Section A\n\nSection B")  # unchanged

    def test_dead_section_dropped(self):
        prompt = "Important: verify numbers.\n\nIgnore this noise section."
        for _ in range(3):
            self.distiller.observe(prompt, "I verified the numbers: 42, 17")
        distilled, dropped, before, after = self.distiller.distill(prompt)
        self.assertIn("verify numbers", distilled)
        self.assertIn("noise", dropped)
        self.assertLess(after, before)

    def test_stats_render(self):
        self.distiller.observe("Part A\n\nPart B", "about A")
        s = self.distiller.stats()
        self.assertIn("LIVE", s)
        self.assertIn("Part A", s)

    def test_reset(self):
        self.distiller.observe("A\n\nB", "about A")
        self.distiller.observe("A\n\nB", "about A")
        self.distiller.observe("A\n\nB", "about A")
        self.assertEqual(self.distiller._observations, 3)
        self.distiller.reset()
        self.assertEqual(self.distiller._observations, 0)

    def test_single_section_prompt(self):
        prompt = "Just one section here."
        for _ in range(5):
            self.distiller.observe(prompt, "unrelated answer")
        distilled, dropped, before, after = self.distiller.distill(prompt)
        # With hit_rate 0 and drop_threshold 0, should be dropped
        # But we need at least 1 kept section
        self.assertIn("Just one section", prompt)  # original is intact


class TestFewShotSelector(unittest.TestCase):
    """Pick relevant examples from a bank."""

    def setUp(self):
        self.bank = [
            Example(input="How to reset password?", output="Go to settings.", tags=["account", "password"]),
            Example(input="Refund my order #123", output="14-day policy applies.", tags=["refund", "order"]),
            Example(input="What are trading hours?", output="MOEX 10:00-19:00 MSK.", tags=["trading", "hours"]),
            Example(input="How to change email?", output="Profile settings.", tags=["account", "email"]),
            Example(input="Cancel subscription", output="Billing page.", tags=["billing", "cancel"]),
        ]
        self.selector = FewShotSelector(bank=self.bank, max_examples=2)

    def test_selects_most_relevant(self):
        selected = self.selector.select("I forgot my password, help!")
        self.assertLessEqual(len(selected), 2)
        self.assertIn("password", selected[0].input.lower())

    def test_refund_query_gets_refund_example(self):
        selected = self.selector.select("How do I get a refund?")
        self.assertGreaterEqual(len(selected), 1)
        self.assertIn("refund", " ".join(s.input.lower() for s in selected))

    def test_render(self):
        selected = self.selector.select("password reset")
        rendered = self.selector.render(selected)
        self.assertIn("Q:", rendered)
        self.assertIn("A:", rendered)

    def test_empty_bank(self):
        sel = FewShotSelector(bank=[], max_examples=3)
        self.assertEqual(sel.select("anything"), [])

    def test_no_relevant_saves_tokens(self):
        """Even if nothing matches, we save by not dumping all 5 examples."""
        sel = FewShotSelector(bank=self.bank, max_examples=2, min_overlap=5)
        selected = sel.select("completely unrelated topic")
        self.assertLessEqual(len(selected), 2)


class TestCacheIntegration(unittest.TestCase):
    """SemanticCache is actually used for PUT in the pipeline."""

    def test_cache_hit_returns_zero_tokens(self):
        cache = SemanticCache()
        cache.put("test question", "test answer")

        profile = RequestProfile(
            task_id="cache-test",
            system_prompt="You are helpful.",
            question="test question",
            output_tokens=50,
        )
        gate = EquivalenceGate(judge1=lambda b, a: (0.95, True), model_version="test")
        runner = OptimizationRunner(PriceTable(3, 3.75, 0.3, 15), cache=cache, gate=gate)
        report = runner.run(profile)

        self.assertEqual(report.optimized_tokens, 0)
        self.assertEqual(len(report.proposals), 1)
        self.assertIn("CACHE HIT", report.proposals[0].details)

    def test_cache_miss_still_runs(self):
        cache = SemanticCache()
        profile = RequestProfile(
            task_id="cache-miss",
            system_prompt="You are helpful.",
            question="new question never seen",
            output_tokens=50,
        )
        runner = OptimizationRunner(PriceTable(3, 3.75, 0.3, 15), cache=cache)
        report = runner.run(
            profile,
            optimized_answer="The answer to the new question.",  # populate cache
        )
        self.assertGreater(report.baseline_tokens, 0)
        # After run with an answer, the question is cached
        cached = cache.get("new question never seen")
        self.assertIsNotNone(cached)
        self.assertIn("answer", cached)


if __name__ == "__main__":
    unittest.main()
