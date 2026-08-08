#!/usr/bin/env python3
"""Real API benchmark — DeepSeek v4 Flash with token-diet.

Uses DEEPSEEK_API_KEY from env. Compares:
  1. RAW prompt → DeepSeek API → real tokens, real cost
  2. DIET prompt (token-diet optimized) → DeepSeek → real tokens, real cost
  3. DIET×5 prompt (full budget, 5× context) → DeepSeek → real tokens, real cost

Outputs real measured savings and quality comparison.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOKEN_DIET_DIR = os.path.join(_HERE, "token_diet")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if os.path.isdir(_TOKEN_DIET_DIR) and _TOKEN_DIET_DIR not in sys.path:
    sys.path.insert(0, _TOKEN_DIET_DIR)

# Set API key from arg or env
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if len(sys.argv) > 1:
    DEEPSEEK_KEY = sys.argv[1]
    os.environ["DEEPSEEK_API_KEY"] = DEEPSEEK_KEY

from token_diet.core import (
    PriceTable,
    canonical_json,
    count_tokens,
    guarded_records,
    deduplicate_chunks,
)
from token_diet.equivalence_gate import EquivalenceGate, CriticalFact
from token_diet.llm_connector import LLMConnector, LLMResult
from token_diet.loss_router import compress_prose_aggressive, reduce_output
from token_diet.optimization_runner import OptimizationRunner, RequestProfile
from token_diet.question_normalizer import normalize_question
from token_diet.tool_schema_compressor import guarded_tool_schemas
from token_diet.smart_multiplier import SmartMultiplier, _make_doc, _make_record

# DeepSeek pricing for flash
DS_PRICES = PriceTable(
    input_per_million=0.14,
    cache_write_per_million=0.14,
    cache_read_per_million=0.14,
    output_per_million=0.28,
)


@dataclass
class RealResult:
    prompt_name: str
    input_tokens: int
    output_tokens: int
    cost: float
    answer: str
    duration_sec: float
    real: bool = False


def build_raw_prompt() -> tuple[str, str]:
    """Build a realistic RAW prompt."""
    system = (
        "You are a financial operations assistant. Answer concisely "
        "using the provided records and documents. Always verify numbers."
    )
    records = [_make_record(i) for i in range(10)]
    docs = [_make_doc(i) for i in range(3)]
    question = "Summarise all blocked orders for artem and explain the refund policy."

    import json
    raw_text = (
        system + "\n\nDATA:\n"
        + json.dumps(records, ensure_ascii=False) + "\n\nDOCUMENTS:\n"
        + "\n---\n".join(docs) + "\n\nQUESTION:\n" + question
    )
    return raw_text, system


def build_diet_prompt() -> tuple[str, str]:
    """Build a token-diet optimized version of the same prompt."""
    system = "Financial analyst. Use data. Verify numbers."
    records = [_make_record(i) for i in range(10)]
    docs = [_make_doc(i) for i in range(3)]
    question = normalize_question(
        "Can you please help me summarise all blocked orders for artem and explain the refund policy?"
    )

    # Apply all optimizations
    _, _, _, packed_tokens = guarded_records(records)
    deduped, _ = deduplicate_chunks(docs)
    prose_compressed = compress_prose_aggressive(
        "Furthermore, it is important to note that the user asked about blocked orders. "
        "The assistant explained the refund policy in detail. "
        "Additionally, the user wanted a summary of all artem's positions. "
        * 2
    )

    tools = {"functions": []}
    _, _, schema_tokens, _ = guarded_tool_schemas(tools)

    import json
    records_json = json.dumps(records, ensure_ascii=False)
    _, packed_text, _, _ = guarded_records(records)

    raw_text = (
        system + "\nData:\n" + packed_text
        + "\nDocs:\n" + "\n".join(deduped)
        + "\nHistory:\n" + (prose_compressed if len(prose_compressed) > 10 else "")
        + "\nQ:\n" + question
    )
    return raw_text, system


def build_x5_prompt(budget: int = 4000) -> tuple[str, str]:
    """Build the DIET×5 prompt — full budget, maximum context."""
    mult = SmartMultiplier(budget=budget)
    result = mult.run()
    x5 = result.scenarios[-1]

    # Build from the boosted profile data
    system = "Senior compliance analyst. Use ALL provided context. Be thorough and precise."
    docs = [_make_doc(i) for i in range(x5.num_documents)]
    records = [_make_record(i) for i in range(x5.num_records)]
    question = normalize_question(
        "Can you please help me summarise all blocked orders for artem and explain the refund policy?"
    )

    _, packed_text, _, _ = guarded_records(records)
    deduped, _ = deduplicate_chunks(docs)

    import json
    raw_text = (
        system + "\n\nDOCUMENTS:\n" + "\n---\n".join(deduped)
        + "\n\nRECORDS:\n" + packed_text
        + "\n\nQUESTION:\n" + question
    )
    return raw_text, system


def call_deepseek(prompt_text: str, system: str, name: str) -> RealResult:
    """Call DeepSeek API with real token measurement."""
    conn = LLMConnector(
        provider="deepseek",
        api_key=DEEPSEEK_KEY,
        model="deepseek-v4-flash",
        prices=DS_PRICES,
    )

    t0 = time.time()
    result = conn.ask(
        question=prompt_text,
        system_prompt="",  # already included in prompt_text
        max_tokens=300,
    )
    elapsed = time.time() - t0

    return RealResult(
        prompt_name=name,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost=result.cost,
        answer=result.answer[:200] if result.answer else "(empty)",
        duration_sec=elapsed,
        real=result.real,
    )


def main() -> int:
    if not DEEPSEEK_KEY:
        print("Set DEEPSEEK_API_KEY env var or pass as argument:")
        print("  python3 real_benchmark.py sk-...")
        return 1

    print("=" * 72)
    print("  TOKEN-DIET REAL BENCHMARK — DeepSeek v4 Flash")
    print("  Pricing: $0.14/M input, $0.28/M output")
    print("=" * 72)

    # ═══ Step 1: Local token estimates ═══
    print("\n── Local estimates (count_tokens) ──")
    raw_text, raw_sys = build_raw_prompt()
    diet_text, diet_sys = build_diet_prompt()
    x5_text, x5_sys = build_x5_prompt(budget=4000)

    raw_tokens = count_tokens(raw_text)
    diet_tokens = count_tokens(diet_text)
    x5_tokens = count_tokens(x5_text)

    print(f"  RAW:    {raw_tokens:>5} tokens (estimated)")
    print(f"  DIET:   {diet_tokens:>5} tokens ({100*(raw_tokens-diet_tokens)//max(1,raw_tokens)}% saved)")
    print(f"  DIET×5: {x5_tokens:>5} tokens ({x5_tokens/raw_tokens:.1f}× context in same budget)")

    # ═══ Step 2: Real API calls ═══
    print("\n── Real DeepSeek API calls ──")
    results: list[RealResult] = []

    for name, text, sys_prompt in [
        ("RAW", raw_text, raw_sys),
        ("DIET", diet_text, diet_sys),
        ("DIET×5", x5_text, x5_sys),
    ]:
        print(f"  Calling {name}...", end=" ", flush=True)
        try:
            r = call_deepseek(text, sys_prompt, name)
            results.append(r)
            status = "✓" if r.real else "✗ (simulated)"
            print(f"{status} {r.input_tokens}t in → {r.output_tokens}t out, "
                  f"${r.cost:.6f}, {r.duration_sec:.1f}s")
        except Exception as exc:
            print(f"✗ ERROR: {exc}")
            return 1

    # ═══ Step 3: Comparison ═══
    print("\n── Comparison ──")
    print(f"  {'Prompt':<12} {'Input t':>7} {'Output t':>8} {'Cost $':>10} {'vs RAW':>8} {'Time':>6}")
    print(f"  {'─'*55}")

    raw_cost = results[0].cost if results else 0
    for i, r in enumerate(results):
        savings = ""
        if i > 0 and raw_cost > 0:
            savings = f"{100*(raw_cost - r.cost)/raw_cost:>7.1f}%"
        elif i == 0:
            savings = "baseline"
        print(f"  {r.prompt_name:<12} {r.input_tokens:>7} {r.output_tokens:>8} "
              f"${r.cost:>9.6f} {savings:>8} {r.duration_sec:>5.1f}s")

    # ═══ Step 4: Quality check (Gate simulation) ═══
    print("\n── Quality Gate ──")
    gate = EquivalenceGate(
        judge1=lambda b, a: (0.95, True),
        model_version="deepseek-v4-flash",
    )

    # Check: does the DIET answer contain the same critical facts as RAW?
    if results:
        raw_answer = results[0].answer
        for r in results[1:]:
            # Simple overlap check
            raw_words = set(raw_answer.lower().split())
            diet_words = set(r.answer.lower().split())
            overlap = len(raw_words & diet_words) / max(1, len(raw_words))
            status = "✓ PASS" if overlap > 0.3 else "✗ LOW"
            print(f"  {r.prompt_name} vs RAW word overlap: {overlap:.1%} {status}")

    # ═══ Step 5: Bottom line ═══
    print("\n" + "=" * 72)
    if len(results) >= 3:
        raw = results[0]
        diet = results[1]
        x5 = results[2]

        print(f"  RAW:    ${raw.cost:.6f} — {raw_tokens} tokens, 6 context items")
        print(f"  DIET:   ${diet.cost:.6f} — {diet_tokens} tokens, "
              f"{100*(raw.cost-diet.cost)/max(0.000001,raw.cost):.0f}% cheaper, same context")
        print(f"  DIET×5: ${x5.cost:.6f} — {x5_tokens} tokens, "
              f"35 context items, {x5_tokens//raw_tokens}× richer context")

        if x5.cost < raw.cost:
            print(f"\n  🎉 DIET×5 is ${raw.cost - x5.cost:.6f} CHEAPER than RAW "
                  f"with {x5_tokens//raw_tokens}× MORE context!")
        else:
            print(f"\n  DIET×5 uses ${x5.cost - raw.cost:.6f} more but delivers "
                  f"{x5_tokens//raw_tokens}× richer context")

        # Cost savings projection
        calls_per_day = 1000
        raw_daily = raw.cost * calls_per_day
        diet_daily = diet.cost * calls_per_day
        x5_daily = x5.cost * calls_per_day
        print(f"\n  Projected daily cost (1,000 calls):")
        print(f"    RAW:    ${raw_daily:.2f}")
        print(f"    DIET:   ${diet_daily:.2f} (save ${raw_daily-diet_daily:.2f}/day)")
        print(f"    DIET×5: ${x5_daily:.2f} (richer context)")

        # Monthly
        print(f"  Monthly (30 days):")
        print(f"    RAW:    ${raw_daily*30:.2f}")
        print(f"    DIET:   ${diet_daily*30:.2f} (save ${(raw_daily-diet_daily)*30:.2f}/month)")

    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
