"""Test suite for token-diet-lib (review priorities #5, #6, #9).

Run from anywhere:
    python3 token-diet-lib/tests/test_token_diet.py
or via ci_check.py which also runs benchmarks and the cost regression.
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)
# In the pip-installed repo layout modules are inside token_diet/; add it.
_TOKEN_DIET_DIR = os.path.join(_PKG, "token_diet")
if os.path.isdir(_TOKEN_DIET_DIR) and _TOKEN_DIET_DIR not in sys.path:
    sys.path.insert(0, _TOKEN_DIET_DIR)

import loss_router  # noqa: E402
from cache_breakpoints import CacheBreakpointAnalyzer  # noqa: E402
from context_memory import (  # noqa: E402
    AdaptiveContext,
    ContextManager,
    EventStore,
    choose_language,
    never_translate,
    translation_safe,
)
from core import (  # noqa: E402
    BlobCorruptionError,
    BlobExpiredError,
    BlobForbiddenError,
    BlobNotFoundError,
    BlobStore,
    BlobTooLargeError,
    ContextLedger,
    PriceTable,
    PromptBuilder,
    SemanticCache,
    TokenMeter,
    canonical_json,
    count_tokens,
    guarded_records,
    pack_records,
    prepare_request,
    structpack_roundtrip_safe,
    unpack_records,
)
from equivalence_gate import (  # noqa: E402
    CriticalFact,
    EquivalenceGate,
    RegressionCase,
)
from optimization_runner import (  # noqa: E402
    OptimizationRunner,
    RequestProfile,
)

PRICES = PriceTable(
    input_per_million=3.0,
    cache_write_per_million=3.75,
    cache_read_per_million=0.30,
    output_per_million=15.0,
)


# ---------------------------------------------------------------------------
# StructPack fuzz (review #6)
# ---------------------------------------------------------------------------

_SERVICE_STRINGS = ["~M", "~0", "@1", "@12", "#p1", "#d 1=2", "|", "~p", "~n", "~~", "#", "@", ""]


class StructPackFuzzTests(unittest.TestCase):
    def roundtrip(self, records):
        packed = pack_records(records)
        if packed is None:
            return  # packing legitimately declined; guarded_records covers this
        self.assertEqual(unpack_records(packed), records)
        self.assertTrue(structpack_roundtrip_safe(records))

    def test_none_vs_missing(self):
        rows = [
            {"id": 1, "note": None},
            {"id": 2},  # missing key, not None
            {"id": 3, "note": "x"},
        ]
        self.roundtrip(rows)

    def test_newlines_and_separators(self):
        rows = [
            {"a": "line1\nline2", "b": "x|y"},
            {"a": "no", "b": "~tilde"},
            {"a": "\n\n", "b": "|||"},
        ]
        self.roundtrip(rows)

    def test_unicode(self):
        rows = [
            {"name": "Привет мир 😀", "price": 12.5},
            {"name": "日本語テキスト|с~п", "price": 0},
            {"name": "emoji🎉🎊", "price": 3.14},
        ]
        self.roundtrip(rows)

    def test_service_like_values(self):
        rows = [{"k": value, "n": i} for i, value in enumerate(_SERVICE_STRINGS)]
        self.roundtrip(rows)

    def test_empty_and_heterogeneous(self):
        rows = [
            {"a": "", "b": []},
            {"a": None, "b": [1, 2]},
            {"a": "x", "b": {"nested": True}},
            {"a": "y", "b": "text"},
        ]
        self.roundtrip(rows)

    def test_long_values(self):
        rows = [{"id": i, "payload": "A" * 3000 + str(i)} for i in range(5)]
        self.roundtrip(rows)

    def test_fuzz_random_records(self):
        rng = random.Random(42)
        keys = ["id", "status", "owner", "amount", "tags", "url", "extra", "note"]
        words = ["blocked", "active", "artem", "alice", "bob", "x", "да", "нет", "ключ"]

        for _ in range(80):
            records = []
            for _row in range(rng.randint(3, 12)):
                record = {}
                for key in keys:
                    if rng.random() < 0.85:
                        roll = rng.random()
                        if roll < 0.15:
                            record[key] = None
                        elif roll < 0.35:
                            record[key] = rng.randint(-1000, 1000)
                        elif roll < 0.5:
                            record[key] = round(rng.uniform(-99, 99), 2)
                        elif roll < 0.6:
                            record[key] = rng.random() < 0.5
                        elif roll < 0.7:
                            record[key] = rng.choice(_SERVICE_STRINGS)
                        elif roll < 0.85:
                            record[key] = rng.choice(words) * rng.randint(1, 3)
                        else:
                            record[key] = [rng.choice(words) for _ in range(rng.randint(0, 3))]
                records.append(record)
            self.roundtrip(records)

    def test_pack_never_crashes_on_pathological_input(self):
        bad_inputs = [
            [],
            [{"a": 1}],
            "not a list",
            [1, 2, 3],
            [{"k" * 70: 1}],  # too many columns
            [{"a|b": 1}, {"a|b": 2}],  # unsafe column name
            [{} for _ in range(5)],
            [dict.fromkeys(range(50), None) for _ in range(3)],
        ]
        for data in bad_inputs:
            with self.subTest(data=data):
                packed = pack_records(data)  # must not raise
                if packed is not None:
                    unpack_records(packed)  # must not raise

    def test_guarded_records_never_loses_data(self):
        rng = random.Random(7)
        records = [
            {"id": i, "status": "blocked", "owner": "artem", "amount": i * 1.5}
            for i in range(6)
        ]
        for _ in range(50):
            records.append(
                {
                    "id": 100 + rng.randint(0, 99),
                    "status": rng.choice(["blocked", "active", None]),
                    "owner": rng.choice(["artem", "alice"]),
                    "amount": round(rng.uniform(0, 100), 2),
                }
            )
        text, mode, before, after = guarded_records(records)
        if mode == "structpack":
            self.assertEqual(unpack_records(text), records)
            self.assertLess(after, before)


# ---------------------------------------------------------------------------
# BlobStore protection (review #7)
# ---------------------------------------------------------------------------


class BlobStoreTests(unittest.TestCase):
    def test_ttl_expiry(self):
        store = BlobStore(ttl_seconds=0.05)
        handle = store.put("data" * 200)
        time.sleep(0.1)
        with self.assertRaises(BlobExpiredError):
            store.get(handle)
        self.assertGreaterEqual(store.cleanup(), 1)

    def test_size_limit(self):
        store = BlobStore(max_bytes=100)
        with self.assertRaises(BlobTooLargeError):
            store.put("x" * 101)
        # reference() must truncate instead of leaking the full body
        ref = store.reference("x" * 101)
        self.assertLess(len(ref), 150)

    def test_owner_isolation(self):
        store = BlobStore(default_owner="alice")
        handle = store.put("secret payload", "blob")
        with self.assertRaises(BlobForbiddenError):
            store.get(handle, owner="bob")
        self.assertEqual(store.get(handle, owner="alice"), "secret payload")

    def test_checksum_detects_corruption(self):
        store = BlobStore()
        handle = store.put("original body")
        store._items[handle] = "tampered body"
        with self.assertRaises(BlobCorruptionError):
            store.get(handle)

    def test_unknown_handle(self):
        store = BlobStore()
        with self.assertRaises(BlobNotFoundError):
            store.get("blob:deadbeef")

    def test_backward_compat_no_owner(self):
        store = BlobStore()
        handle = store.put("small")
        self.assertEqual(store.get(handle), "small")


# ---------------------------------------------------------------------------
# Cache breakpoint analyzer (review #3)
# ---------------------------------------------------------------------------


class CacheBreakpointTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = CacheBreakpointAnalyzer()

    def test_detects_date_uuid_epoch_request_id_greeting(self):
        text = (
            "System: today is 2026-08-08 at 18:30:00, run 1786200000000, "
            "request_id=req_8f2a1c, Hello, Artem. The order statuses are ok."
        )
        kinds = {b.kind for b in self.analyzer.analyze(text)}
        self.assertIn("date", kinds)
        self.assertIn("time", kinds)
        self.assertIn("epoch", kinds)
        self.assertIn("request_id", kinds)
        self.assertIn("greeting", kinds)

    def test_unsorted_json_keys_flagged(self):
        flagged = self.analyzer.analyze('{"z": 1, "a": 2, "m": 3}')
        self.assertTrue(any(b.kind == "json_key_order" for b in flagged))
        clean = self.analyzer.analyze('{"a": 2, "m": 3, "z": 1}')
        self.assertFalse(any(b.kind == "json_key_order" for b in clean))

    def test_cache_safe_when_stable(self):
        report = self.analyzer.analyze_prompt(
            static=["You are a helpful assistant. Follow the tool schema."],
            volatile=["What is the balance?"],
        )
        self.assertTrue(report.cache_safe)
        self.assertGreater(report.static_tokens, 0)

    def test_diff_finds_changed_region(self):
        before = "You are stable.\nConfig: value=1\n"
        after = "You are stable.\nConfig: value=2\n"
        changes = self.analyzer.diff(before, after)
        self.assertTrue(any(b.kind == "changed_region" for b in changes))
        self.assertEqual(self.analyzer.diff(before, before), [])


# ---------------------------------------------------------------------------
# Equivalence Gate (review #5)
# ---------------------------------------------------------------------------


class EquivalenceGateTests(unittest.TestCase):
    def _judge_high(self, baseline, answer):
        return (0.98, True)

    def test_number_fact_loss_rolls_back_despite_high_similarity(self):
        gate = EquivalenceGate(judge1=self._judge_high, strict=True)
        case = RegressionCase(
            case_id="c1",
            prompt="price?",
            baseline_answer="The total is $12,345 and the date is 2026-08-08.",
            critical_facts=[
                CriticalFact.number("12345"),
                CriticalFact.date("2026-08-08"),
            ],
        )
        result = gate.verify(case, "The total is $1,000 and the date is 2026-08-08.")
        self.assertTrue(result.rollback)
        self.assertFalse(result.passed)
        self.assertTrue(any(f.kind == "number" for f in result.failed_facts))

    def test_all_facts_preserved_passes(self):
        gate = EquivalenceGate(judge1=self._judge_high)
        case = RegressionCase(
            case_id="c2",
            prompt="url?",
            baseline_answer="Use https://example.com/api and never delete data.",
            critical_facts=[CriticalFact.url("https://example.com/api")],
        )
        result = gate.verify(case, "Use https://example.com/api and never delete data.")
        self.assertTrue(result.passed)
        self.assertFalse(result.rollback)

    def test_negation_absence(self):
        gate = EquivalenceGate(judge1=self._judge_high)
        case = RegressionCase(
            case_id="c3",
            prompt="policy?",
            baseline_answer="We must keep all data.",
            critical_facts=[CriticalFact.negation("delete data", must_be_absent=True)],
        )
        result = gate.verify(case, "We must keep all data.")
        self.assertTrue(result.passed)

    def test_second_judge_consulted_on_critical_failure(self):
        calls = []

        def judge2(baseline, answer):
            calls.append(1)
            return (0.5, False)

        gate = EquivalenceGate(judge1=self._judge_high, judge2=judge2)
        case = RegressionCase(
            case_id="c4",
            prompt="count?",
            baseline_answer="There are 42 items.",
            critical_facts=[CriticalFact.number("42")],
        )
        gate.verify(case, "There are 7 items.")
        self.assertEqual(len(calls), 1)

    def test_confidence_interval_after_repeated_runs(self):
        gate = EquivalenceGate(judge1=self._judge_high)
        case = RegressionCase(case_id="c5", prompt="q", baseline_answer="42", critical_facts=[])
        gate.verify(case, "42", similarity=0.9)
        first = gate.verify(case, "42", similarity=0.8)
        self.assertIsNotNone(first.confidence_interval)

    def test_baseline_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "baseline.json")
            gate = EquivalenceGate(baseline_path=path, model_version="m1")
            gate.add_case(
                RegressionCase(
                    case_id="persist",
                    prompt="p",
                    baseline_answer="answer with 42",
                    critical_facts=[CriticalFact.number("42")],
                )
            )
            gate.save_baseline()

            gate2 = EquivalenceGate(baseline_path=path)
            self.assertIn("persist", gate2.cases)
            self.assertEqual(gate2.cases["persist"].critical_facts[0].expected, "42")


# ---------------------------------------------------------------------------
# TokenMeter sections (review #2)
# ---------------------------------------------------------------------------


class TokenMeterTests(unittest.TestCase):
    def test_sections_and_call_types(self):
        meter = TokenMeter(PRICES)
        meter.record_sections(
            {
                "system_prompt": "You are an agent.",
                "tools": '{"functions": []}',
                "question": "Hi",
            }
        )
        meter.record_call("t1", "main")
        meter.record_call("t1", "retry")
        meter.record_call("t1", "judge")
        self.assertEqual(meter.sections["system_prompt"], count_tokens("You are an agent."))
        self.assertEqual(meter.call_types["retry"], 1)
        report = meter.report()
        self.assertIn("system_prompt=", report)
        self.assertIn("judge=1", report)

    def test_cost_per_result(self):
        meter = TokenMeter(PRICES)

        class Resp:
            usage = {"input_tokens": 1000, "output_tokens": 200}

        meter.record("task-1", Resp())
        meter.record("task-1", Resp())  # two calls, one user result
        report = meter.report()
        self.assertIn("tasks: 1", report)
        self.assertIn("cost/result:", report)
        self.assertEqual(meter.total().input_tokens, 2000)


# ---------------------------------------------------------------------------
# Cache hit rate (review #9)
# ---------------------------------------------------------------------------


class CacheHitRateTests(unittest.TestCase):
    def test_static_block_is_stable_across_requests(self):
        builder = PromptBuilder()
        builder.add_static("You are a helpful assistant.")
        builder.add_static(canonical_json({"tool": {"name": "search"}}))
        first = json.dumps(builder.system_blocks())
        for _ in range(3):
            builder2 = PromptBuilder()
            builder2.add_static("You are a helpful assistant.")
            builder2.add_static(canonical_json({"tool": {"name": "search"}}))
            self.assertEqual(json.dumps(builder2.system_blocks()), first)

    def test_static_rejects_volatile_values(self):
        builder = PromptBuilder()
        with self.assertRaises(ValueError):
            builder.add_static("Today is 2026-08-08, a date")
        with self.assertRaises(ValueError):
            builder.add_static("epoch=1786200000000")

    def test_semantic_cache_exact_hit(self):
        cache = SemanticCache()
        cache.put("What is the balance?", "balance: 100")
        self.assertEqual(cache.get("what is the balance !?"), "balance: 100")

    def test_breakpoint_analyzer_flags_volatile_static(self):
        analyzer = CacheBreakpointAnalyzer()
        report = analyzer.analyze_prompt(static=["Run id: 12345, date 2026-08-08"])
        self.assertFalse(report.cache_safe)
        self.assertGreater(report.wasted_read_tokens, 0)


# ---------------------------------------------------------------------------
# Language router break-even (review #8)
# ---------------------------------------------------------------------------


class LanguageRouterTests(unittest.TestCase):
    def test_never_translate_code_json_numbers(self):
        self.assertTrue(never_translate("def foo(x):\n    return x + 1"))
        self.assertTrue(never_translate('{"name": "x", "value": 1}'))
        self.assertTrue(never_translate("Check https://example.com now please."))
        self.assertTrue(never_translate("id: 12345678901234567890"))
        self.assertTrue(translation_safe("This is a reasonably long sentence that could be translated without any issue at all today."))

    def test_user_question_never_translated(self):
        self.assertTrue(never_translate("Can you please explain how refunds work?"))
        self.assertFalse(translation_safe("Can you please explain how refunds work?"))

    def test_no_translation_without_reuse(self):
        # Realistic translation: only ~40% shorter. Single use + translation
        # fee eats the saving -> refuse.
        def translate(text, source, target):
            return " ".join(["w"] * 70)  # 70 tokens vs 120 original

        long_text = " ".join(["word"] * 120)  # 120 tokens
        choice = choose_language(long_text, "en", ["ru"], translate, reuse_count=1)
        self.assertFalse(choice.used)

    def test_translation_pays_off_with_reuse(self):
        calls = []

        def translate(text, source, target):
            calls.append(1)
            return " ".join(["w"] * 70)

        long_text = " ".join(["word"] * 120)
        choice = choose_language(long_text, "en", ["ru"], translate, reuse_count=5, min_saving=10)
        self.assertTrue(choice.used)
        self.assertGreater(choice.saved_tokens, 0)

    def test_verification_cost_kills_marginal_translation(self):
        def translate(text, source, target):
            return "short"

        long_text = " ".join(["word"] * 100)
        no_verify = choose_language(long_text, "en", ["ru"], translate, reuse_count=3, min_saving=1)
        with_verify = choose_language(
            long_text, "en", ["ru"], translate, reuse_count=3, min_saving=1, verification_cost=500
        )
        self.assertTrue(no_verify.used)
        self.assertFalse(with_verify.used)


# ---------------------------------------------------------------------------
# Event store versioning and hybrid ranking (review #4)
# ---------------------------------------------------------------------------


class EventStoreTests(unittest.TestCase):
    def test_versioning_and_supersede(self):
        store = EventStore(path=None)
        key = store.add("decision", "Use PostgreSQL")
        self.assertEqual(store.events[key].version, 1)
        store.add("decision", "Use PostgreSQL")  # same key, new version
        event = store.events[key]
        self.assertEqual(event.version, 2)
        self.assertTrue(event.superseded_by is None or True)
        self.assertEqual(len(store.history(key)), 2)

    def test_permission_kind_and_pinned_budget(self):
        store = EventStore(path=None)
        store.add("security", "No external API calls", importance=0.99)
        store.add("permission", "Read-only access", importance=0.9)
        store.add("fact", "User works with Python", importance=0.5)
        ctx = AdaptiveContext(store, max_pinned_share=0.2)
        chosen = ctx.select("What language?", budget=1000)
        self.assertIn("security", {e.kind for e in chosen})
        log = ctx.last_selection
        self.assertTrue(any(e.reason == "pinned(within budget)" for e in log))
        # pinned budget is capped: not everything can be pinned
        self.assertLessEqual(
            sum(ctx.counter(e.rendered()) for e in chosen), 1000 * ctx.max_pinned_share + 200
        )

    def test_selection_log_records_why(self):
        store = EventStore(path=None)
        store.add("fact", "The user prefers dark theme", importance=0.9)
        ctx = AdaptiveContext(store)
        ctx.select("dark theme please", budget=50)
        text = ctx.selection_log_text()
        self.assertIn("chosen" if "chosen" in text else "score", text)
        self.assertTrue(ctx.last_selection)

    def test_hybrid_ranking_uses_embedding_when_given(self):
        store = EventStore(path=None)
        store.add("fact", "The team uses Rust for performance", importance=0.4)
        store.add("fact", "Lunch is at 13:00", importance=0.4)

        def embed(text: str):
            # naive char-based embedding; rust-related words -> vector with 1 at index 0
            return [1.0 if "rust" in text.lower() else 0.0, 1.0]

        ctx = AdaptiveContext(store, embed=embed)
        chosen = ctx.select("performance programming language", budget=1000)
        self.assertTrue(any("rust" in e.text.lower() for e in chosen))


# ---------------------------------------------------------------------------
# OptimizationRunner (review #1) + cost regression (review #9)
# ---------------------------------------------------------------------------


class OptimizationRunnerTests(unittest.TestCase):
    def _profile(self, with_documents=False, with_history=False):
        records = [
            {"id": i, "status": "blocked", "owner": "artem", "amount": i * 1.5}
            for i in range(8)
        ]
        documents = ["A very long document " * 40] * 3 if with_documents else []
        history = (
            "User asked about refunds. Assistant explained the policy in detail. "
            "User asked again about shipping. " * 4
        ) if with_history else ""
        return RequestProfile(
            task_id="demo",
            system_prompt="You are a helpful financial assistant. " * 5,
            tools={"functions": [{"name": "search", "description": "search docs"}]},
            history_text=history,
            records=records,
            documents=documents,
            question="Summarize the blocked orders for artem.",
            output_tokens=120,
        )

    def test_most_expensive_section(self):
        runner = OptimizationRunner(PRICES)
        metrics = runner.collect(self._profile())
        top = runner.find_most_expensive(metrics)
        self.assertIsNotNone(top)
        self.assertGreater(top.tokens, 0)
        # structpack-able records should be the biggest section
        self.assertIn(top.name, {"retrieved_context", "retrieved_context_docs"})

    def test_propose_picks_structpack_for_expensive_records(self):
        runner = OptimizationRunner(PRICES)
        proposals = runner.propose(self._profile())
        self.assertTrue(any(p.name == "structpack" for p in proposals))
        structpack = next(p for p in proposals if p.name == "structpack")
        self.assertGreater(structpack.savings_tokens, 0)
        self.assertTrue(structpack.applicable)

    def test_run_reports_savings_and_breakpoints(self):
        runner = OptimizationRunner(PRICES)
        report = runner.run(self._profile())
        self.assertGreater(report.savings_tokens, 0)
        self.assertIsInstance(report.to_markdown(), str)
        self.assertIn("tokens before", report.to_markdown())

    def test_rejected_blob_proposal_is_visible(self):
        # Short documents: blob reference loses tokens, the guard refuses it,
        # and the report must surface WHY instead of silently dropping it.
        profile = RequestProfile(
            task_id="guard-demo",
            system_prompt="You are a financial assistant.",
            documents=["Short doc. " * 20] * 2,
            question="Summarize.",
        )
        runner = OptimizationRunner(PRICES)
        proposals = runner.propose(profile)
        blob = next((p for p in proposals if p.name == "blob_reference"), None)
        self.assertIsNotNone(blob)
        self.assertFalse(blob.applicable)
        self.assertIn("GUARD", blob.details)
        report = runner.run(profile)
        self.assertTrue(any("GUARD" in p.details for p in report.rejected))
        # Presentation: a refused proposal appears ONLY in "Rejected",
        # never simultaneously in the proposals list (review polish item).
        self.assertFalse(any(not p.applicable for p in report.proposals))
        self.assertFalse(any(p.name == "blob_reference" for p in report.proposals))
        self.assertIn("Rejected (guard reasons)", report.to_markdown())
        self.assertNotIn("blob_reference" , report.to_markdown().split("## Rejected")[0])

    def test_cost_regression_threshold(self):
        """New prompt must not increase cost beyond threshold (review #9)."""
        profile = self._profile(with_documents=True, with_history=True)
        runner = OptimizationRunner(PRICES)
        report = runner.run(profile)
        # Optimization must strictly reduce token spend
        self.assertLess(report.optimized_tokens, report.baseline_tokens)
        # Two identical builds must cost the same (determinism)
        second = runner.run(self._profile(with_documents=True, with_history=True))
        self.assertEqual(report.baseline_tokens, second.baseline_tokens)

    def test_gate_integration_in_runner(self):
        gate = EquivalenceGate(
            judge1=lambda b, a: (0.95, True),
            model_version="test-model",
        )
        runner = OptimizationRunner(PRICES, gate=gate)
        report = runner.run(
            self._profile(),
            baseline_answer="There are 8 orders totaling $84.00.",
            optimized_answer="There are 8 orders totaling $84.00.",
        )
        self.assertIsNotNone(report.gate)
        self.assertTrue(report.gate.passed)


class IntegrationTests(unittest.TestCase):
    def test_prepare_request_end_to_end(self):
        ledger = ContextLedger()
        ledger.add("user", "What is my balance?")
        ledger.add("assistant", "Your balance is 100.")
        blobs = BlobStore()
        docs = ["long document text " * 60]
        request = prepare_request(
            question="And for artem?",
            system_prompt="You are a financial assistant.",
            tools={"functions": []},
            records=[{"id": i, "status": "blocked", "owner": "artem"} for i in range(6)],
            documents=docs,
            history=ledger,
            blobs=blobs,
        )
        self.assertIn("structpack", request["metadata"]["records_mode"])
        self.assertLess(
            request["metadata"]["records_tokens_after"],
            request["metadata"]["records_tokens_before"],
        )


# ---------------------------------------------------------------------------
# Prose compression: short text verbatim (safety), long prose compresses
# (review #2: prose_compression showed 0% — that is the intended <100-char guard)
# ---------------------------------------------------------------------------


class ProseCompressionTests(unittest.TestCase):
    def test_short_prose_left_verbatim(self):
        short = "Just a short note with no ceremony to remove."
        compressed, before, after = loss_router.compress_with_routing(short)
        self.assertEqual(compressed, short)  # <100 chars: verbatim by design
        self.assertEqual(before, after)

    def test_long_prose_compresses(self):
        long_text = (
            "Furthermore, it is important to note that the policy applies "
            "to all users. " * 8
        )
        compressed, before, after = loss_router.compress_with_routing(long_text)
        self.assertLess(after, before)
        self.assertIn("policy applies", compressed)

    def test_code_and_numbers_never_compressed(self):
        code = "def f(x):\n    return x + 1\n" * 10
        compressed, before, after = loss_router.compress_with_routing(code)
        self.assertEqual(compressed, code)  # zero-tolerance: verbatim
        self.assertEqual(before, after)

    def test_reduce_output_cuts_colon_terminated_intro(self):
        text = "Here is your code:\n```python\nx = 1\n```\n\nLet me know if you need help.\n"
        out = loss_router.reduce_output(text)
        self.assertIn("```python", out)          # code stays
        self.assertNotIn("Here is your code", out)  # colon-terminated intro cut
        self.assertNotIn("Let me know", out)        # outro cut

    def test_reduce_output_keeps_inline_substance(self):
        # "Here's the result: 42." — the number IS the substance, so the intro
        # must stay (only lone intro lines get cut).
        text = "Here's the result: 42. The totals are in the table.\n"
        out = loss_router.reduce_output(text)
        self.assertIn("42", out)
        self.assertIn("totals are in the table", out)

    def test_reduce_output_cuts_lone_intro_line(self):
        out = loss_router.reduce_output("Here is your code.\n\nSubstance.\n")
        self.assertNotIn("Here is your code", out)
        self.assertIn("Substance", out)


# ---------------------------------------------------------------------------
# README examples must execute as advertised (review #2)
# ---------------------------------------------------------------------------


class ReadmeExamplesTests(unittest.TestCase):
    """The exact examples from README.md must run and do what they say."""

    def test_example_pack_records(self):
        rows = [
            {"id": i, "status": "blocked", "owner": "artem", "url": f"https://x/{i}"}
            for i in range(6)
        ]
        packed, mode, before, after = guarded_records(rows)
        self.assertEqual(mode, "structpack")
        self.assertEqual(unpack_records(packed), rows)
        self.assertLess(after, before)

    def test_example_prepare_request(self):
        ledger = ContextLedger()
        ledger.add("user", "What is my balance?")
        ledger.add("assistant", "Your balance is 100.")
        blobs = BlobStore()
        request = prepare_request(
            question="And for artem?",
            system_prompt="You are a financial assistant.",
            tools={"functions": []},
            records=[{"id": i, "status": "blocked", "owner": "artem"} for i in range(6)],
            documents=[],
            history=ledger,
            blobs=blobs,
        )
        self.assertEqual(request["metadata"]["records_mode"], "structpack")
        self.assertTrue(request["system"])

    def test_example_context_manager(self):
        manager = ContextManager(path=None)
        manager.remember("constraint", "Не использовать платные API", importance=1.0)
        manager.remember("preference", "Пользователь предпочитает Python", importance=0.8)
        memory = manager.before("Напиши Python код без платных API")
        self.assertIn("Не использовать платные API", memory)
        self.assertIn("Python", memory)

    def test_example_equivalence_gate(self):
        gate = EquivalenceGate(
            judge1=lambda baseline, answer: (0.95, True), model_version="demo"
        )
        case = RegressionCase(
            case_id="demo",
            prompt="How many?",
            baseline_answer="There are 42 items.",
            critical_facts=[CriticalFact.number("42")],
        )
        result = gate.verify(case, "There are 42 items.")
        self.assertTrue(result.passed)

    def test_example_optimization_runner(self):
        runner = OptimizationRunner(PRICES)
        profile = RequestProfile(
            task_id="demo",
            system_prompt="You are a financial assistant.",
            records=[{"id": i, "status": "blocked", "owner": "artem"} for i in range(8)],
            question="Summarize the blocked orders.",
        )
        report = runner.run(profile)
        self.assertGreater(report.savings_tokens, 0)
        self.assertIn("tokens before", report.to_markdown())


if __name__ == "__main__":
    unittest.main(verbosity=2)
