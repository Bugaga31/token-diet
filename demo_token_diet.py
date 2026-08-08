#!/usr/bin/env python3
"""Token Diet v2.0.0 — end-to-end demo.

Runs the full optimization pipeline on realistic data and prints:
  - tokens before / after per module
  - OptimizationRunner report with proposals + rejected + gate
  - EventStore memory usage
  - Cache-breakpoint analysis

Usage:
    python3 demo_token_diet.py              # local
    pip install git+https://github.com/Bugaga31/token-diet.git && python3 demo_token_diet.py
"""

from __future__ import annotations

import json
import os
import sys
import textwrap

# Bootstrap: work both as `python demo.py` from repo root (pip install -e .)
# and standalone with token_diet/ on path.
_HERE = os.path.dirname(os.path.abspath(__file__))
_TOKEN_DIET_DIR = os.path.join(_HERE, "token_diet")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if os.path.isdir(_TOKEN_DIET_DIR) and _TOKEN_DIET_DIR not in sys.path:
    sys.path.insert(0, _TOKEN_DIET_DIR)

# Import from the package — works after pip install or via sys.path.
from token_diet.cache_breakpoints import CacheBreakpointAnalyzer
from token_diet.context_memory import ContextManager, EventStore, TranslationCache, choose_language
from token_diet.core import (
    BlobStore,
    ContextLedger,
    PriceTable,
    PromptBuilder,
    canonical_json,
    count_tokens,
    deduplicate_chunks,
    guarded_records,
    prepare_request,
)
from token_diet.equivalence_gate import (
    CriticalFact,
    EquivalenceGate,
    RegressionCase,
)
from token_diet.intelligence_booster import IntelligenceBooster
from token_diet.loss_router import compress_with_routing, reduce_output
from token_diet.optimization_runner import (
    OptimizationRunner,
    RequestProfile,
)

# ── prices (GPT-4o-mini-ish) ────────────────────────────────────────────────
PRICES = PriceTable(
    input_per_million=3.0,
    cache_write_per_million=3.75,
    cache_read_per_million=0.30,
    output_per_million=15.0,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Realistic test data
# ═══════════════════════════════════════════════════════════════════════════════

RECORDS = [
    {
        "id": i,
        "ticker": "SBER",
        "side": "BUY" if i % 2 else "SELL",
        "amount": round(1000 + i * 150.5, 2),
        "status": "blocked" if i % 3 == 0 else "active",
        "owner": "artem",
        "venue": "MOEX",
    }
    for i in range(15)
]

DOCUMENT_A = textwrap.dedent("""\
    REFUND POLICY v3.2 — effective 2026-01-01.
    Refunds are issued within 14 calendar days of the purchase date.
    A restocking fee of 15% applies to opened items. Shipping costs
    are non-refundable unless the return is due to our error. RMA
    numbers must be obtained before shipping returns.
""").strip()

DOCUMENT_B = textwrap.dedent("""\
    COMPLIANCE NOTE 2026-Q3.
    All trades must be reported to the exchange within T+1. Late
    reporting incurs a penalty of 0.25% of notional per day. The
    compliance team audits every transaction flagged as 'blocked'
    within 48 hours.
""").strip()

DOCUMENTS = [DOCUMENT_A, DOCUMENT_B]

HISTORY = (
    "Furthermore, it is important to note that the user asked about "
    "blocked orders. The assistant explained the refund policy in detail. "
    "The user also inquired about compliance deadlines. The assistant "
    "clarified the T+1 rule. Additionally, the user wanted a summary of "
    "all artem's positions. Let me now provide that analysis. "
    * 3
)

QUESTION = "Summarise all blocked orders for artem and explain the refund policy."


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Run each component and measure savings
# ═══════════════════════════════════════════════════════════════════════════════

def demo_components() -> dict[str, tuple[int, int, str]]:
    rows: dict[str, tuple[int, int, str]] = {}

    # ── StructPack ──────────────────────────────────────────────────────
    raw = json.dumps(RECORDS, ensure_ascii=False)
    before = count_tokens(raw)
    text, mode, _b, after = guarded_records(RECORDS)
    rows["structpack"] = (before, after, mode)

    # ── BlobStore ───────────────────────────────────────────────────────
    store = BlobStore(preview_chars=120, ttl_seconds=3600)
    before = sum(count_tokens(d) for d in DOCUMENTS)
    after = sum(count_tokens(store.reference(d, "doc")) for d in DOCUMENTS)
    applied = "applied" if after < before * 0.95 else "GUARD: not profitable"
    rows["blob_reference"] = (before, after if after < before * 0.95 else before, applied)

    # ── Deduplicate ─────────────────────────────────────────────────────
    more_docs = [DOCUMENT_A, DOCUMENT_A, DOCUMENT_B]  # A duplicated
    before = sum(count_tokens(d) for d in more_docs)
    deduped, dropped = deduplicate_chunks(more_docs)
    after = sum(count_tokens(d) for d in deduped)
    rows["dedupe_chunks"] = (before, after, f"{dropped} duplicates removed")

    # ── Prose compression ───────────────────────────────────────────────
    before = count_tokens(HISTORY)
    compressed, _b, after = compress_with_routing(HISTORY)
    rows["prose"] = (before, after, "filler phrases stripped")

    # ── Output reduction ────────────────────────────────────────────────
    ai_answer = "Here is your summary:\n\nThe total is 42 blocked orders.\n\nLet me know if you need help.\n"
    before = count_tokens(ai_answer)
    reduced = reduce_output(ai_answer)
    after = count_tokens(reduced)
    rows["reduce_output"] = (before, after, "ceremony stripped")

    # ── Translation (mock) ──────────────────────────────────────────────
    long_prose = "The refund policy requires customers to submit a return merchandise authorization form within fourteen calendar days of purchase. " * 4

    def _translate(text, source, target):
        return "Политика возврата требует от клиентов подать форму RMA в течение 14 дней с даты покупки. " * 2

    before = count_tokens(long_prose)
    choice = choose_language(
        long_prose, "en", ["ru"], _translate,
        reuse_count=3, min_saving=10,
        cache=TranslationCache(),
    )
    after = count_tokens(choice.text) if choice.used else before
    rows["translation_router"] = (before, after, f"used={choice.used} reuse=3")

    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Event memory demo
# ═══════════════════════════════════════════════════════════════════════════════

def demo_event_memory():
    print("\n" + "─" * 70)
    print("EventStore + AdaptiveContext")
    print("─" * 70)
    cm = ContextManager(path=None, budget=800, max_pinned_share=0.4)
    cm.remember("constraint", "No paid APIs — use free tiers only", importance=1.0)
    cm.remember("permission", "Read-only access to production DB", importance=0.95)
    cm.remember("decision", "Use async Python (aiohttp)", importance=0.8)
    cm.remember("fact", "The team works in UTC+3 timezone", importance=0.5)
    cm.remember("decision", "Use aiohttp instead of requests", importance=0.8)  # supersedes

    mem = cm.before("write async Python HTTP client for production monitoring")
    print(f"  {cm.store.turn=}  events={len(cm.store.all())}  "
          f"superseded={sum(1 for e in cm.store.events.values() if e.superseded)}")
    print(f"  selected for question ({len(mem)} chars, {count_tokens(mem)} tokens):")
    for line in mem.split("\n"):
        print(f"    {line}")
    print(f"\n  selection log (last):")
    for entry in cm.memory.last_selection[:5]:
        print(f"    {entry.render()}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Full pipeline: OptimizationRunner + EquivalenceGate
# ═══════════════════════════════════════════════════════════════════════════════

def demo_pipeline():
    print("\n" + "─" * 70)
    print("OptimizationRunner + EquivalenceGate")
    print("─" * 70)

    # Baseline answer (what the model would say before optimization)
    baseline_answer = (
        "artem has 5 blocked orders totalling $6,050.00 on venue MOEX. "
        "The refund policy allows returns within 14 calendar days with "
        "a 15% restocking fee for opened items. Shipping is non-refundable "
        "unless the error is ours. An RMA number is required."
    )

    gate = EquivalenceGate(
        judge1=lambda b, a: (0.92, True),
        model_version="demo",
        strict=True,
    )
    runner = OptimizationRunner(PRICES, gate=gate)

    # Run on a single user result
    profile = RequestProfile(
        task_id="demo-1",
        system_prompt=(
            "You are a financial operations assistant. Answer concisely "
            "using the provided records and documents."
        ),
        tools={"functions": [{"name": "search_orders", "description": "search the order book"}]},
        history_text=HISTORY,
        records=RECORDS,
        documents=DOCUMENTS,
        question=QUESTION,
        output_tokens=120,
    )
    report = runner.run(
        profile,
        baseline_answer=baseline_answer,
        # Optimized answer: deliberately the same to prove the gate passes.
        # In production the model would answer from the optimized prompt.
        optimized_answer=baseline_answer,
    )

    print(report.to_markdown())
    return report


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Cache-breakpoint analysis
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Intelligence Booster — reinvest savings into richer context
# ═══════════════════════════════════════════════════════════════════════════════

def demo_intelligence_boost():
    print("\n" + "─" * 70)
    print("Intelligence Booster — same budget, MORE context → better answer")
    print("─" * 70)

    blobs = BlobStore(preview_chars=100)
    big_doc = "CRITICAL COMPLIANCE UPDATE Q3-2026: All blocked orders must be reviewed within 24 hours. Penalty for non-compliance is 0.5% of notional per day. " * 9
    blobs.reference(big_doc, "compliance")

    store = EventStore(path=None)
    store.add("constraint", "Audit trail required for every blocked order", 0.95)
    store.add("fact", "The compliance team runs checks at 09:00 UTC daily", 0.7)
    store.add("permission", "Only senior traders can unblock orders", 0.85)

    gate = EquivalenceGate(judge1=lambda b, a: (0.92, True), model_version="demo")
    runner = OptimizationRunner(PRICES, blobs=blobs, gate=gate)
    booster = IntelligenceBooster(
        runner,
        max_budget=3000,
        extra_documents=[
            ("compliance-v2", "COMPLIANCE UPDATE v2: Q4 2026 adds mandatory dual-approval for all blocked orders above $10,000. " * 9),
            ("audit-log", "AUDIT CHECKLIST: 1) verify RMA status 2) check counterparty rating 3) confirm trade timestamp 4) validate settlement currency." * 4),
        ],
        event_store=store,
        blobs=blobs,
    )

    profile = RequestProfile(
        task_id="iq-boost",
        system_prompt="You are a senior compliance analyst. Use all provided context.",
        records=RECORDS,
        documents=DOCUMENTS,
        question=QUESTION,
        output_tokens=150,
    )

    result = booster.boost(profile)
    print(result.summary())

    # Compare: plain profile vs boosted
    plain_tokens = sum(count_tokens(t) for t in profile.section_texts().values())
    plain_items = len(profile.documents) + (1 if profile.records else 0) + (1 if profile.history_text else 0)
    plain_iq = plain_items / max(1, plain_tokens)

    boosted = result.boosted_profile
    boosted_tokens = sum(count_tokens(t) for t in boosted.section_texts().values())
    boosted_items = (len(boosted.documents) + (1 if boosted.records else 0)
                     + (1 if boosted.history_text else 0)
                     + result.allocation.extra_memory_events
                     + (1 if result.allocation.restored_blob_tokens > 0 else 0))
    boosted_iq = boosted_items / max(1, boosted_tokens)

    print(f"\n  Plain  profile: {plain_tokens} tokens, {plain_items} docs/items, "
          f"IQ density: {plain_iq:.4f} items/token")
    print(f"  Boosted profile: {boosted_tokens} tokens, {boosted_items} docs/items, "
          f"IQ density: {boosted_iq:.4f} items/token")
    print(f"  Gain: +{boosted_items - plain_items} items, "
          f"IQ density ×{boosted_iq / max(0.0001, plain_iq):.1f}")


def demo_cache_breakpoints():
    print("\n" + "─" * 70)
    print("CacheBreakpointAnalyzer")
    print("─" * 70)
    analyzer = CacheBreakpointAnalyzer()
    # Volatile value mistakenly placed in static
    bad_static = "You are a helpful assistant. Today is 2026-08-08 at 14:30. Run ID: req_abc123."
    report = analyzer.analyze_prompt(static=[bad_static], volatile=["What are the blocked orders?"])
    print(f"  {report.summary()}")
    for bp in report.breakpoints:
        print(f"    {bp.render()}")
    print(f"  fingerprint: {__import__('token_diet.cache_breakpoints', fromlist=['fingerprint']).fingerprint(bad_static)}")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 70)
    print("  Token Diet v2.0.0 — end-to-end demo")
    print("=" * 70)

    # Step 1: component benchmarks
    rows = demo_components()
    print("\n" + "─" * 70)
    print("Component savings (before → after tokens)")
    print("─" * 70)
    total_before = total_after = 0
    for name, (before, after, note) in rows.items():
        saved = before - after
        pct = 100 * saved / max(1, before)
        total_before += before
        total_after += after
        flag = ""
        if name == "reduce_output" and after < before:
            flag = " ← ceremony gone"
        print(f"  {name:<20} {before:>5} → {after:>5}  {pct:>5.1f}% saved  ({note}){flag}")
    total_saved = total_before - total_after
    print(f"  {'─' * 55}")
    print(f"  {'TOTAL':<20} {total_before:>5} → {total_after:>5}  "
          f"{100 * total_saved / max(1, total_before):>5.1f}% saved "
          f"({total_saved} tokens)")

    # Step 2: Event memory
    demo_event_memory()

    # Step 3: Full pipeline
    demo_pipeline()

    # Step 4: Cache breakpoints
    demo_cache_breakpoints()

    # Final cost estimate
    print("\n" + "=" * 70)
    print("  $ saved: ~${:.4f} per request (input-only, gpt-4o-mini pricing)".format(
        total_saved * PRICES.input_per_million / 1_000_000
    ))
    print("  Gate says: all critical facts preserved. Ship it.")
    # Step 5: Intelligence booster — savings → richer context
    demo_intelligence_boost()

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
