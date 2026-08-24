"""Smoke tests для модулей, ранее не покрытых прямыми тестами.

17 модулей не упоминались в tests/* по имени — этот файл закрывает
слепую зону: импорт + офлайн-поведение + базовые инварианты.
Все проверки <10ms, без сети и без ключей.
"""
from __future__ import annotations

import pathlib
import unittest


class UncoveredSmokeTests(unittest.TestCase):
    def test_intelligence_amplifier(self):
        from token_diet.intelligence_amplifier import amplify_intelligence, plan_reasoning
        plan = plan_reasoning("compare two options and pick best")
        self.assertIsNotNone(plan)
        res = amplify_intelligence("Explain refund policy", examples=[{"q": "refund?", "a": "14 days"}])
        self.assertIsNotNone(res)

    def test_intelligence_optimizer(self):
        from token_diet.intelligence_optimizer import detect_task_type, optimize_intelligence
        t = detect_task_type("compare and calculate")
        self.assertIsInstance(t, str)
        res = optimize_intelligence("Solve: 2+2*2")
        self.assertIsNotNone(res)

    def test_llm_connector(self):
        from token_diet.llm_connector import LLMConnector
        c = LLMConnector(provider="test", model="test-model")
        self.assertEqual(c.model, "test-model")
        # from_env should not crash without keys
        c2 = LLMConnector.from_env()
        self.assertIsNotNone(c2)

    def test_memory_cli(self):
        from token_diet.memory_cli import main, vault_path
        p = vault_path()
        self.assertIsInstance(p, pathlib.Path)
        self.assertTrue(callable(main))

    def test_night_shift(self):
        from token_diet import night_shift
        self.assertTrue(hasattr(night_shift, "collect_context"))
        self.assertTrue(hasattr(night_shift, "render_report"))
        # collect_context is offline
        ctx = night_shift.collect_context()
        self.assertIsInstance(ctx, (str, dict))

    def test_omniroute_mcp(self):
        import token_diet.omniroute_mcp as m
        self.assertTrue(hasattr(m, "main"))
        self.assertTrue(callable(m.main))

    def test_paper_techniques(self):
        from token_diet.paper_techniques import (
            build_capc_prompt,
            estimate_capc_savings,
            tron_serialize,
        )
        p = build_capc_prompt(query="hello")
        self.assertIsInstance(p, str)
        s = estimate_capc_savings(system_tokens=1000)
        self.assertIsInstance(s, (int, float, dict))
        t = tron_serialize({"a": 1, "b": [2, 3]})
        self.assertIsInstance(t, str)

    def test_pattern_collapse(self):
        from token_diet.pattern_collapse import collapse_json_text, semantic_dedup
        txt = collapse_json_text('{"a": 1, "a": 1, "b": 2}')
        self.assertIsInstance(txt, str)
        # semantic dedup on duplicates
        deduped, dropped = semantic_dedup(["hello world", "hello world", "different"])
        self.assertGreaterEqual(len(deduped), 1)

    def test_progressive_disclosure(self):
        from token_diet.progressive_disclosure import ProgressiveDisclosure
        pd = ProgressiveDisclosure()
        pd.add_skill("test", "do thing", full_content="detailed test skill content")
        cat = pd.build_catalog()
        self.assertIsInstance(cat, str)
        self.assertIn("test", cat.lower())

    def test_promptology(self):
        from token_diet.promptology import remove_russian_filler, rewrite_system_prompt
        out = rewrite_system_prompt("You are a helpful assistant. Please be kind.")
        self.assertIsInstance(out, str)
        cleaned = remove_russian_filler("ну вот как бы это сказать, помоги пожалуйста")
        self.assertIsInstance(cleaned, str)

    def test_question_normalizer(self):
        from token_diet.question_normalizer import normalize_question
        q = normalize_question("Can you please explain refund policy?")
        self.assertIsInstance(q, str)
        self.assertIn("refund", q.lower())

    def test_session_bridge(self):
        from token_diet import session_bridge
        self.assertTrue(hasattr(session_bridge, "find_firefox_cookies_db"))
        self.assertTrue(hasattr(session_bridge, "read_cookies"))
        self.assertTrue(callable(session_bridge.find_firefox_cookies_db))

    def test_telegram_search(self):
        import token_diet.telegram_search as ts
        self.assertTrue(hasattr(ts, "search_telegram"))
        self.assertTrue(callable(ts.search_telegram))

    def test_tfidf_scorer(self):
        from token_diet.tfidf_scorer import compress_retrieval_tfidf, score_sentences
        sentences = ["refund policy is 14 days", "random text about cats"]
        scored, weights = score_sentences(sentences, "refund policy and returns")
        self.assertEqual(len(scored), 2)
        compressed = compress_retrieval_tfidf("refund policy is important. cats are cute. returns require RMA.", query="refund")
        self.assertIsInstance(compressed, str)

    def test_tg_scout(self):
        import importlib
        mod = importlib.import_module("token_diet.tg_scout")
        self.assertTrue(hasattr(mod, "scout"))
        self.assertTrue(hasattr(mod, "scout_block"))

    def test_token_budget_guard(self):
        from token_diet.token_budget_guard import BudgetState, TokenBudgetGuard
        guard = TokenBudgetGuard(max_input_tokens=10000)
        state = BudgetState(input_tokens_used=500, iterations=1)
        decision = guard.check(state)
        self.assertTrue(hasattr(decision, "allowed"))
        guard.record(state, input_tokens=500)
        # summary may need state arg depending on version
        try:
            s = guard.summary(state)
        except TypeError:
            s = guard.summary()
        self.assertIsInstance(s, str)

    def test_tool_schema_compressor(self):
        from token_diet.tool_schema_compressor import compress_tool_schemas, guarded_tool_schemas
        schemas = {"functions": [{"name": "search", "description": "search docs with many words " * 10, "parameters": {"type": "object", "properties": {"q": {"type": "string", "description": "query"}}}}]}
        compressed = compress_tool_schemas(schemas)
        self.assertIsInstance(compressed, dict)
        guarded, before, after, applied = guarded_tool_schemas(schemas)
        self.assertIsInstance(guarded, dict)
        self.assertIsInstance(applied, bool)
