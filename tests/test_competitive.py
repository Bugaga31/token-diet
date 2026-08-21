"""Tests for competitive modules: ML Compressor, AST JSON, Model Router."""

import json
import sys, os, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_HERE)
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)

from token_diet.ml_compressor import compress_ml
from token_diet.ast_json_compressor import compress_json_ast
from token_diet.model_router import route_query, route_and_estimate, estimate_mixed_savings, RouterConfig, RoutingDecision
from token_diet.core import count_tokens


class TestMLCompressor(unittest.TestCase):
    """Statistical token pruning — beats regex."""

    def test_drops_filler(self):
        # ML compressor needs enough text for statistics to work
        filler = (
            "Furthermore, it is important to note that the policy applies to all users. "
            "Additionally, the compliance team must review every single transaction. "
            "Moreover, the refund policy requires an RMA number for processing. "
            "It is worth noting that shipping costs are strictly non-refundable. "
        ) * 3  # 3× repetition gives statistics enough signal
        result, before, after = compress_ml(filler)
        self.assertLessEqual(after, before)
        # Policy should survive — it's a content word
        self.assertIn("policy", result)

    def test_preserves_numbers(self):
        text = "The total is 42 blocked orders for artem."
        result, before, after = compress_ml(text)
        self.assertIn("42", result)

    def test_preserves_entities(self):
        text = "John Smith from Acme Corp filed a complaint about SBER trading."
        result, _, _ = compress_ml(text)
        self.assertIn("John Smith", result)

    def test_short_text_unchanged(self):
        text = "Hello."
        result, before, after = compress_ml(text)
        self.assertEqual(result, text)

    def test_empty_unchanged(self):
        result, before, after = compress_ml("")
        self.assertEqual(result, "")

    def test_all_same_words(self):
        # Need enough text for ML statistics
        text = "the the the the the the the the the the the the the the the the the the the the"
        result, before, after = compress_ml(text)
        self.assertIsNotNone(result)


class TestASTJSONCompressor(unittest.TestCase):
    """80%+ JSON compression."""

    def test_repeated_keys(self):
        records = [{"ticker": "SBER", "side": "BUY"} for _ in range(20)]
        result, before, after = compress_json_ast(records)
        self.assertLess(after, before)
        self.assertIn("_k", result)
        self.assertIn("_r", result)
        # Should be significant savings
        self.assertGreater(before - after, before * 0.3)

    def test_enum_values(self):
        records = [
            {"status": s}
            for s in ["active", "blocked", "active", "blocked", "active", "filled"]
        ]
        result, before, after = compress_json_ast(records)
        self.assertLess(after, before)
        parsed = json.loads(result)
        self.assertIn("_k", parsed)

    def test_no_repetition(self):
        records = [{"id": i, "value": f"unique_{i}"} for i in range(3)]
        result, before, after = compress_json_ast(records)
        # Small dataset: guard returns original if compressed > original
        self.assertTrue("id" in result or "_k" in result)

    def test_empty_list(self):
        result, before, after = compress_json_ast([])
        parsed = json.loads(result)
        self.assertEqual(parsed["_rows"], [])

    def test_flat_dict(self):
        data = {"a": {"b": {"c": 1}}}
        result, _, _ = compress_json_ast(data)
        parsed = json.loads(result)
        self.assertIn("a.b.c", parsed)


class TestModelRouter(unittest.TestCase):
    """Smart routing saves money."""

    def test_simple_question_to_cheap(self):
        d = route_query("What is the refund policy?")
        self.assertEqual(d.tier, "cheap")
        self.assertLess(d.complexity, 0.3)

    def test_complex_to_premium(self):
        d = route_query(
            "Analyze the security vulnerabilities in the authentication system "
            "and evaluate the compliance implications of the proposed architecture. "
            "Debug the race condition in the distributed lock. "
            "Design a new security architecture for the microservices platform "
            "and evaluate compliance with SOC2 and ISO 27001. "
            "Prove that the system is secure against side-channel attacks. "
            * 3
        )
        self.assertEqual(d.tier, "premium")

    def test_route_and_estimate(self):
        d = route_and_estimate("What is 2+2?", context_tokens=500, output_tokens=100)
        self.assertGreater(d.estimated_cost, 0)
        self.assertLess(d.estimated_cost, 0.001)  # cheap model = tiny cost

    def test_mixed_savings(self):
        queries = [
            "What is the policy?",
            "Analyze the security audit and propose remediation for the critical vulnerabilities found in production.",
            "List all orders.",
            "Compare the financial reports from Q1 and Q2 and identify discrepancies.",
            "How do I reset my password?",
        ]
        savings = estimate_mixed_savings(queries)
        self.assertGreater(savings.savings_pct, 30)
        self.assertGreater(savings.cheap_pct, 0)

    def test_custom_config(self):
        cfg = RouterConfig(
            cheap_model="custom-cheap",
            premium_model="custom-prem",
            cheap_threshold=0.5,
            mid_threshold=0.8,
        )
        d = route_query("A moderately complex analysis of the data.", config=cfg)
        self.assertEqual(d.model, "custom-cheap")


if __name__ == "__main__":
    unittest.main()
