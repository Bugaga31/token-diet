"""Tests for Smart Multiplier — 5× intelligence proof."""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_HERE)
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)

from token_diet.smart_multiplier import (
    HARD_FACTS,
    MultiplierResult,
    Scenario,
    SmartMultiplier,
    _make_doc,
    _make_record,
    compare_llm_quality,
)


class TestSmartMultiplierDocs(unittest.TestCase):
    """Data generators produce valid, non-empty output."""

    def test_make_doc(self):
        for seed in range(10):
            doc = _make_doc(seed)
            self.assertGreater(len(doc), 32)
            self.assertIn("DOC-", doc)

    def test_make_record(self):
        for seed in range(10):
            rec = _make_record(seed)
            self.assertIn("ticker", rec)
            self.assertIn("status", rec)
            self.assertIn("amount", rec)


class TestScenarioBuilding(unittest.TestCase):
    """Each scenario produces the right shape of data."""

    def setUp(self):
        self.mult = SmartMultiplier(budget=2000)

    def test_scenario_raw_shape(self):
        sc, _ = self.mult._scenario_raw()
        self.assertEqual(sc.num_documents, 1)
        self.assertEqual(sc.num_records, 5)
        self.assertEqual(sc.num_memory_events, 0)
        self.assertEqual(sc.context_items, 6)
        self.assertGreater(sc.tokens, 0)
        self.assertGreater(sc.cost, 0)

    def test_scenario_diet_tokens_less_or_equal(self):
        raw_sc, raw_prof = self.mult._scenario_raw()
        diet_sc, _ = self.mult._scenario_diet(raw_prof)
        # DIET should not exceed RAW tokens
        self.assertLessEqual(diet_sc.tokens, raw_sc.tokens)
        self.assertEqual(diet_sc.context_items, raw_sc.context_items)

    def test_scenario_diet_x5_has_more_context(self):
        _, raw_prof = self.mult._scenario_raw()
        diet_sc, diet_prof = self.mult._scenario_diet(raw_prof)
        x5_sc = self.mult._scenario_diet_x5(diet_prof, diet_sc.tokens)
        # DIET×5 must have more context items than RAW
        self.assertGreater(x5_sc.context_items, 6)
        self.assertGreaterEqual(x5_sc.num_documents, 2)


class TestMultiplierResult(unittest.TestCase):
    """The result class correctly computes multiplier."""

    def test_run_returns_valid_result(self):
        mult = SmartMultiplier(budget=2000)
        result = mult.run()
        self.assertIsInstance(result, MultiplierResult)
        self.assertGreaterEqual(len(result.scenarios), 3)
        self.assertGreater(result.multiplier_vs_raw, 1.0)

    def test_markdown_output(self):
        mult = SmartMultiplier(budget=2000)
        result = mult.run()
        md = result.to_markdown()
        self.assertIn("A: RAW", md)
        self.assertIn("B: DIET", md)
        self.assertIn("C: DIET", md)
        self.assertIn("| Scenario", md)
        self.assertIn("| A:", md)
        self.assertIn("| B:", md)
        self.assertIn("| C:", md)

    def test_multiplier_math(self):
        """multiplier_vs_raw = Scenario C items / Scenario A items."""
        result = MultiplierResult(
            scenarios=[
                Scenario("A", 500, 0.001, 1, 5, 0, 6, True, 0.01, 1.0, ""),
                Scenario("B", 350, 0.0007, 1, 5, 0, 6, True, 0.017, 0.95, ""),
                Scenario("C", 1800, 0.005, 8, 15, 3, 26, True, 0.014, 0.96, ""),
            ],
            budget=2000,
            multiplier_vs_raw=26 / 6,
        )
        # 26 / 6 ≈ 4.33
        self.assertAlmostEqual(result.multiplier_vs_raw, 26 / 6, places=2)
        md = result.to_markdown()
        self.assertIn("4.3×", md)


class TestCompareLLMQuality(unittest.TestCase):
    """compare_llm_quality works with simulation fallback."""

    def test_works_with_simulation(self):
        result = compare_llm_quality(connector=None, budget=2000)
        self.assertIn("multiplier", result)
        self.assertGreater(result["multiplier"], 1.0)
        self.assertIn("markdown", result)
        self.assertIn("Smart Multiplier", result["markdown"])


class TestHardFacts(unittest.TestCase):
    """Critical facts are meaningful strings."""

    def test_facts_non_empty(self):
        for fact in HARD_FACTS:
            self.assertGreater(len(fact), 4, f"Fact too short: {fact!r}")


if __name__ == "__main__":
    unittest.main()
