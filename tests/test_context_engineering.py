"""Tests for context_engineering — Anthropic context engineering rules."""

from __future__ import annotations

import unittest

from token_diet import count_tokens
from token_diet.context_engineering import (
    analyze_prompt,
    minimize_system_prompt,
    optimize_system_prompt,
    reorder_prompt,
)

BLOATED_PROMPT = """You are a helpful, accurate, concise assistant.
You are an AI assistant designed to be helpful.
NEVER write comments in your code.
NEVER write multi-line docstrings.
Be polite and professional at all times.
Always be concise and to the point.
Remember to verify facts before answering.
Remember to verify facts before answering.
Keep answers short and direct.
"""


class TestAnalyzePrompt(unittest.TestCase):
    def test_finds_bans_and_filler(self):
        report = analyze_prompt(BLOATED_PROMPT)
        self.assertGreaterEqual(len(report.ban_lines), 2)   # NEVER comments/docstrings
        self.assertGreaterEqual(len(report.filler_lines), 2)  # be helpful/concise
        self.assertGreaterEqual(len(report.duplicate_groups), 1)  # verify facts ×2

    def test_removable_tokens_positive(self):
        report = analyze_prompt(BLOATED_PROMPT)
        self.assertGreater(report.removable_tokens, 0)
        self.assertGreater(report.potential_savings_pct, 0)


class TestMinimizePrompt(unittest.TestCase):
    def test_cuts_tokens(self):
        res = minimize_system_prompt(BLOATED_PROMPT)
        self.assertLess(res.tokens_after, res.tokens_before)
        self.assertGreater(res.savings_pct, 10)

    def test_rewrites_ban_to_judgment(self):
        res = minimize_system_prompt(BLOATED_PROMPT)
        # "NEVER write comments" should be rewritten or removed, not kept verbatim
        self.assertNotIn("NEVER write comments", res.minimized)

    def test_never_empty(self):
        res = minimize_system_prompt("NEVER DO THIS. NEVER DO THAT.")
        self.assertTrue(res.minimized.strip())

    def test_short_prompt_unchanged(self):
        res = minimize_system_prompt("You are a helpful assistant.")
        self.assertEqual(res.tokens_after, res.tokens_before)


class TestReorderPrompt(unittest.TestCase):
    def test_documents_first_instructions_last(self):
        out = reorder_prompt(
            "system rules here",
            reference_docs=["doc one", "doc two"],
            final_instructions="now answer based on the documents",
        )
        self.assertLess(out.index("<documents>"), out.index("system rules"))
        self.assertGreater(out.index("<instructions>"), out.index("doc two"))
        self.assertTrue(out.rstrip().endswith("</instructions>"))


class TestOptimizePrompt(unittest.TestCase):
    def test_composite(self):
        r = optimize_system_prompt(BLOATED_PROMPT)
        self.assertIn("report", r)
        self.assertIn("minimized", r)
        self.assertGreater(r["savings_pct"], 0)
        self.assertLess(count_tokens(r["minimized"]), count_tokens(BLOATED_PROMPT))


if __name__ == "__main__":
    unittest.main()
