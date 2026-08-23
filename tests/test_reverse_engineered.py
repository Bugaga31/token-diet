"""Tests for reverse-engineered modules: live_docs, tool_output_pruner, clean_scraper."""

from __future__ import annotations

import unittest

from token_diet.clean_scraper import html_to_text
from token_diet.live_docs import (
    DEFAULT_TTL_HOURS,
    KNOWN_PACKAGES,
    DocsCache,
    LiveDocsResult,
    _trim_to_tokens,
    get_live_docs,
)
from token_diet.tool_output_pruner import (
    fuzzy_fingerprint,
    prune_tool_outputs,
    render_pruned,
    result_hash,
)
from token_diet import count_tokens


class TestResultHash(unittest.TestCase):
    def test_normalizes_whitespace(self):
        self.assertEqual(
            result_hash("a  b\n\nc"),
            result_hash("a b c"),
        )

    def test_different_content_different_hash(self):
        self.assertNotEqual(result_hash("hello world"), result_hash("goodbye world"))


class TestFuzzyFingerprint(unittest.TestCase):
    def test_ignores_timestamps(self):
        a = "run at 2026-08-11 13:24:05, took 123ms, ok"
        b = "run at 2026-08-12 09:01:00, took 88ms, ok"
        self.assertEqual(fuzzy_fingerprint(a), fuzzy_fingerprint(b))

    def test_different_content_differs(self):
        self.assertNotEqual(
            fuzzy_fingerprint("request succeeded"),
            fuzzy_fingerprint("request failed"),
        )


class TestPruneToolOutputs(unittest.TestCase):
    def test_dedups_exact_duplicates(self):
        msgs = [
            {"role": "user", "content": "check git status"},
            {"role": "tool", "content": "On branch master\nChanged: a.py b.py"},
            {"role": "tool", "content": "On branch master\nChanged: a.py b.py"},  # dup
        ]
        res = prune_tool_outputs(msgs)
        self.assertEqual(res.removed_duplicates, 1)
        self.assertGreater(res.savings_pct, 0)
        kept = [t for t in res.turns if t.action == "keep" and t.role == "tool"]
        self.assertEqual(len(kept), 1)

    def test_drops_obsolete_keeps_newest(self):
        msgs = [
            {"role": "tool", "content": "On branch master\nChanged: old.py"},
            {"role": "tool", "content": "On branch master\nChanged: new.py"},
        ]
        res = prune_tool_outputs(msgs)
        kept = [t.content for t in res.turns if t.role == "tool" and t.content]
        self.assertEqual(kept, ["On branch master\nChanged: new.py"])
        self.assertGreaterEqual(res.removed_obsolete, 1)

    def test_keeps_user_and_assistant_verbatim(self):
        msgs = [
            {"role": "user", "content": "important user instruction"},
            {"role": "assistant", "content": "my reasoning here"},
            {"role": "tool", "content": "On branch master\nChanged: a.py b.py"},
            {"role": "tool", "content": "On branch master\nChanged: a.py b.py"},
        ]
        res = prune_tool_outputs(msgs)
        user_turns = [t for t in res.turns if t.role == "user"]
        self.assertEqual(user_turns[0].content, "important user instruction")
        self.assertEqual(user_turns[0].action, "keep")

    def test_truncates_oversized_results(self):
        big = "data line\n" * 5000  # way over the cap
        msgs = [
            {"role": "user", "content": "run query"},
            {"role": "tool", "content": big},
        ]
        res = prune_tool_outputs(msgs, max_result_tokens=200)
        self.assertGreaterEqual(res.truncated, 1)
        kept = [t for t in res.turns if t.role == "tool" and t.content]
        self.assertLess(count_tokens(kept[0].content), 500)

    def test_render_pruned_roundtrip(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "Out branch x"},
            {"role": "tool", "content": "Out branch x"},
        ]
        res = prune_tool_outputs(msgs)
        out = render_pruned(res)
        self.assertEqual(len(out), len(msgs))
        self.assertEqual(out[0]["content"], "hi")


class TestCleanScraper(unittest.TestCase):
    def test_strips_nav_footer_scripts(self):
        html = (
            "<html><head><title>Title</title></head><body>"
            "<script>var x=1;</script>"
            "<nav>Menu link1 link2</nav>"
            "<article><h1>Head</h1><p>First paragraph.</p><p>Second.</p></article>"
            "<footer>Footer text</footer>"
            "</body></html>"
        )
        text = html_to_text(html)
        self.assertIn("Head", text)
        self.assertIn("First paragraph.", text)
        self.assertNotIn("Menu", text)
        self.assertNotIn("Footer", text)
        self.assertNotIn("var x=1", text)

    def test_collapses_whitespace(self):
        html = "<p>a   b\n\n\n   c</p>"
        text = html_to_text(html)
        self.assertNotIn("   ", text)

    def test_empty_html(self):
        self.assertEqual(html_to_text(""), "")


class TestLiveDocsHelpers(unittest.TestCase):
    def test_known_packages_registry(self):
        self.assertIn("requests", KNOWN_PACKAGES)
        self.assertIn("fastapi", KNOWN_PACKAGES)
        self.assertIn("react", KNOWN_PACKAGES)

    def test_trim_to_tokens(self):
        long_text = "слово " * 2000
        trimmed = _trim_to_tokens(long_text, max_tokens=100)
        self.assertLess(count_tokens(trimmed), count_tokens(long_text))
        self.assertLess(count_tokens(trimmed), 400)

    def test_docs_cache_ttl(self):
        cache = DocsCache(ttl_hours=DEFAULT_TTL_HOURS)
        self.assertEqual(cache.ttl_hours, DEFAULT_TTL_HOURS)

    def test_live_docs_offline_graceful(self):
        # unknown package → error result, no crash
        res = get_live_docs("zzz-не-существующий-пакет-98765", max_tokens=200,
                            use_cache=False)
        self.assertIsInstance(res, LiveDocsResult)
        self.assertTrue(hasattr(res, "error"))


if __name__ == "__main__":
    unittest.main()
