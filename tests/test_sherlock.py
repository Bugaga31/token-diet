"""Tests for SherlockReasoner — smarter at lower cost."""

import sys, os, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_HERE)
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)

from token_diet.sherlock_reasoner import (
    SherlockReasoner,
    SelfVerifier,
    VerifierResult,
    sherlock_system_prompt,
    compress_reasoning,
    SHERLOCK_TEMPLATE,
)


class TestSherlockTemplate(unittest.TestCase):
    """System prompt enhancement."""

    def test_empty_base(self):
        result = sherlock_system_prompt("")
        self.assertIn("Sherlock", result)
        self.assertIn("OBSERVE", result)

    def test_adds_to_base(self):
        result = sherlock_system_prompt("You are a helpful assistant.")
        self.assertIn("Sherlock", result)
        self.assertIn("helpful assistant", result)

    def test_no_double_add(self):
        once = sherlock_system_prompt("You are helpful.")
        twice = sherlock_system_prompt(once)
        self.assertEqual(once.count("Sherlock"), twice.count("Sherlock"))

    def test_enhance_method(self):
        sr = SherlockReasoner()
        enhanced = sr.enhance("Be concise.")
        self.assertIn("OBSERVE", enhanced)


class TestSelfVerifier(unittest.TestCase):
    """Deterministic answer verification."""

    def setUp(self):
        self.v = SelfVerifier()

    def test_clean_answer(self):
        result = self.v.verify(
            "How many blocked orders for artem?",
            "artem has 5 blocked orders totalling $6,050."
        )
        self.assertGreaterEqual(result.score, 0.9)

    def test_empty_answer(self):
        result = self.v.verify("What is 2+2?", "")
        self.assertEqual(result.score, 0.0)
        self.assertIn("empty", result.missing_answers[0])

    def test_no_question_marks(self):
        result = self.v.verify("Summarize the data", "The data shows growth.")
        self.assertGreaterEqual(result.score, 0.9)

    def test_flagged_numbers(self):
        result = self.v.verify(
            "What is the refund policy?",
            "You get $500 back within 30 days."
        )
        # $500 and 30 are not in question → flagged
        self.assertGreater(len(result.flagged_numbers), 0)

    def test_renders_clean(self):
        result = VerifierResult(score=1.0)
        self.assertIn("all clear", result.render())


class TestReasoningCompression(unittest.TestCase):
    """Strip verbose reasoning, keep facts."""

    def test_compresses_thinking(self):
        text = "Let me think about this step by step. The answer is 42."
        compressed, before, after = compress_reasoning(text)
        self.assertNotIn("Let me think", compressed)
        self.assertIn("42", compressed)
        self.assertLess(after, before)

    def test_strips_ceremony(self):
        text = "In conclusion, the total is 100. I hope this helps! Let me know if you have questions."
        compressed, _, _ = compress_reasoning(text)
        self.assertIn("100", compressed)
        self.assertNotIn("I hope this helps", compressed)
        self.assertNotIn("Let me know", compressed)

    def test_preserves_facts(self):
        text = "Based on the data, artem has 5 blocked orders. The refund policy states 14 days."
        compressed, _, _ = compress_reasoning(text)
        self.assertIn("artem", compressed)
        self.assertIn("5 blocked", compressed)
        self.assertIn("14 days", compressed)

    def test_empty_unchanged(self):
        text = ""
        compressed, before, after = compress_reasoning(text)
        self.assertEqual(compressed, "")

    def test_short_text_preserved(self):
        text = "42"
        compressed, _, _ = compress_reasoning(text)
        self.assertIn("42", compressed)

    def test_transition_words_stripped(self):
        text = "Furthermore, the policy applies.\nMoreover, it is mandatory."
        compressed, _, _ = compress_reasoning(text)
        self.assertIn("policy applies", compressed.lower())
        self.assertIn("mandatory", compressed.lower())


class TestSherlockPipeline(unittest.TestCase):
    """End-to-end pipeline."""

    def test_full_pipeline(self):
        sr = SherlockReasoner()
        result = sr.pipeline(
            system_prompt="You are a financial analyst.",
            question="How many blocked orders?",
            answer="Let me think step by step. Based on the data, artem has 5 blocked orders. I hope this helps!"
        )
        self.assertIn("enhanced_prompt", result)
        self.assertIn("compressed_answer", result)
        self.assertGreaterEqual(result["token_savings"], 0)
        self.assertIn("Sherlock", result["enhanced_prompt"])


if __name__ == "__main__":
    unittest.main()
