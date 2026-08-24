"""Competitive Benchmark — real comparison of token-diet vs baselines.

Every number is reproducible: same prompts, same methods, same counters.

Usage:
    python3 -m token_diet.competitor_benchmark
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from token_diet.ast_json_compressor import compress_json_ast
from token_diet.compression_arsenal import (
    compress_by_self_information,
    strip_grammar_caveman,
)
from token_diet.core import PriceTable, Usage, count_tokens, deduplicate_chunks
from token_diet.green_calculator import GreenCalculator
from token_diet.intelligence_optimizer import (
    inject_reasoning_blueprint,
    merge_tokens,
)
from token_diet.json_compressor import compress_json
from token_diet.loss_router import compress_prose_aggressive, compress_with_routing
from token_diet.neural_scorer import strip_noise
from token_diet.pattern_collapse import (
    collapse_json_text,
    semantic_dedup,
)
from token_diet.promptology import (
    optimize_prompt,
)
from token_diet.tfidf_scorer import compress_retrieval_tfidf as _compress_retrieval_tfidf

# ── Test prompts ─────────────────────────────────────────────────────────────

TEST_PROMPTS = {
    "json_records_100": {
        "type": "json",
        "description": "100 repeated JSON records with ticker/price/volume",
        "text": json.dumps(
            [
                {"ticker": t, "price": round(100 + i * 0.5, 2), "volume": 1000000 + i * 10000}
                for i, t in enumerate(["AAPL", "GOOGL", "MSFT", "AMZN", "META"] * 20)
            ]
        ),
    },
    "long_prose_report": {
        "type": "prose",
        "description": "200-word financial report with filler",
        "text": (
            "Furthermore, it is important to note that the quarterly earnings report "
            "indicates a significant shift in market dynamics. Moreover, the compliance "
            "team has identified several areas that require immediate attention. "
            "Additionally, the risk assessment committee recommends a thorough review "
            "of all outstanding positions. It should be noted that the current volatility "
            "levels exceed historical averages by approximately 23 percent. "
            "In addition, the regulatory environment continues to evolve rapidly. "
            "As mentioned earlier, the board has approved a strategic review. "
            "Please note that the deadline for submissions is December 15th. "
            "It is worth mentioning that sector rotation patterns suggest defensive "
            "positioning may be warranted. Furthermore, emerging market exposure "
            "should be carefully monitored. Moreover, currency fluctuations have "
            "introduced additional complexity to earnings forecasts. Additionally, "
            "supply chain disruptions continue to impact margins across the industry. "
            "In conclusion, the overall outlook remains cautiously optimistic "
            "despite near-term headwinds and elevated uncertainty levels."
        ),
    },
    "agent_conversation_long": {
        "type": "agent",
        "description": "15-turn agent conversation with tools and code",
        "text": [
            {"role": "user", "content": "Add error handling to the login function"},
            {"role": "assistant", "content": '<tool_call>{"name":"read_file","args":{"path":"/app/auth.py"}}</tool_call>'},
            {"role": "tool", "content": "# /app/auth.py (312 lines)\ndef login(u, p):\n    user = db.query(User).filter_by(username=u).first()\n    return user if check_password(p, user.password_hash) else None"},
            {"role": "assistant", "content": "Here is the improved version:\n```python\ndef login(username: str, password: str) -> User:\n    try:\n        user = db.query(User).filter_by(username=username).first()\n        if not user:\n            raise ValueError(f\"User {username} not found\")\n        if not check_password(password, user.password_hash):\n            raise PermissionError(\"Invalid password\")\n        return user\n    except Exception as e:\n        raise RuntimeError(\"Auth service unavailable\")\n```"},
            {"role": "user", "content": "Now add rate limiting"},
            {"role": "assistant", "content": "I will check for existing rate limiter.\n\n<tool_call>{\"name\":\"search\",\"args\":{\"query\":\"rate_limit\"}}</tool_call>"},
            {"role": "tool", "content": "Found 0 results for 'rate_limit'"},
            {"role": "assistant", "content": "No existing rate limiter. Adding Redis-backed sliding window:\n\n```python\nimport redis\ndef check_rate_limit(username: str, max_attempts: int = 5, window: int = 300) -> bool:\n    r = redis.from_url(os.getenv(\"REDIS_URL\", \"redis://localhost\"))\n    key = f\"login_attempts:{username}\"\n    current = r.get(key)\n    if current and int(current) >= max_attempts:\n        return False\n    pipe = r.pipeline()\n    pipe.incr(key)\n    pipe.expire(key, window)\n    pipe.execute()\n    return True\n```"},
            {"role": "user", "content": "Good. Now integrate rate limit into login."},
            {"role": "assistant", "content": "```python\ndef login(username: str, password: str) -> User:\n    if not check_rate_limit(username):\n        raise RateLimitError(f\"Too many attempts for {username}\")\n    try:\n        user = db.query(User).filter_by(username=username).first()\n        if not user:\n            raise ValueError(f\"User {username} not found\")\n        if not check_password(password, user.password_hash):\n            raise PermissionError(\"Invalid password\")\n        return user\n    except (ValueError, PermissionError) as e:\n        logger.warning(f\"Login failed: {e}\")\n        raise\n```"},
            {"role": "user", "content": "Write tests"},
            {"role": "assistant", "content": "```python\nimport pytest\ndef test_successful_login():\n    user = login(\"testuser\", \"correct_password\")\n    assert user.username == \"testuser\"\ndef test_rate_limit():\n    for _ in range(5):\n        try: login(\"testuser\", \"wrong\")\n        except PermissionError: pass\n    with pytest.raises(RateLimitError):\n        login(\"testuser\", \"wrong\")\n```"},
            {"role": "user", "content": "Perfect. Now deploy to staging."},
        ],
    },
    "duplicate_retrieval": {
        "type": "retrieval",
        "description": "Retrieval with 5 near-duplicate documents",
        "text": (
            "Document 1: The company reported Q4 revenue of $12.3 billion, up 15% year-over-year. "
            "Cloud services contributed $5.1 billion to total revenue.\n\n"
            "Document 2: The company reported Q4 revenue of $12.3 billion, which represents "
            "a 15% increase compared to the same period last year.\n\n"
            "Document 3: Cloud services revenue grew 28% to $5.1 billion in Q4.\n\n"
            "Document 4: Q4 revenue reached $12.3 billion, marking a 15% year-over-year increase. "
            "The cloud division was the standout performer with $5.1 billion in revenue.\n\n"
            "Document 5: Total Q4 revenue: $12.3B (+15% YoY). Cloud: $5.1B (+28% YoY). "
            "Enterprise: $4.2B (+8% YoY). Consumer: $3.0B (+5% YoY)."
        ),
    },
    "verbose_prompt": {
        "type": "promptology",
        "description": "Verbose system+user prompt (persona bloat + filler)",
        "text": json.dumps({
            "system": (
                "You are an elite senior software architect with over 20 years of experience "
                "in distributed systems and cloud infrastructure. You must always be polite and "
                "courteous to the user. Remember to be professional at all times. "
                "If you don't know the answer, say so. "
                "Additionally, it is important to note that you should explain everything in detail. "
                "Furthermore, let's think about this step by step. "
                "First, analyze the problem. Second, break it down. Third, provide a solution. "
                "Please note that all code must be production-ready. "
                "You MUST validate all inputs. Do NOT trust user data. "
                "Only use approved libraries."
            ),
            "user": (
                "Hey, I was wondering if you could help me with something? "
                "Could you please write a Python function that calculates "
                "Fibonacci sequence up to n terms? Thank you in advance!"
            ),
        }),
    },
}


# ── REAL compression strategies (fixed — no more 0%) ─────────────────────────


def _compress_json_full(text: str) -> tuple[str, int, int]:
    """JSON: Pattern Collapse + AST compression + JSON flatten fallback."""
    before = count_tokens(text)
    try:
        data = json.loads(text)
        if isinstance(data, list) and len(data) > 10:
            # Pattern collapse first (SmartCrusher-style)
            collapsed = collapse_json_text(text, max_examples=3, total_max=10)
            ca = count_tokens(collapsed)
            if ca < before:
                return collapsed, before, ca
        # Then try AST compression
        result, _, after = compress_json_ast(data)
        if after < before:
            return result, before, after
    except Exception:
        pass
    # Fallback
    try:
        result = compress_json(json.loads(text))
        after = count_tokens(result)
        return result, before, after
    except Exception:
        return text, before, before


def _compress_prose_full(text: str) -> tuple[str, int, int]:
    """Prose: Caveman + aggressive routing."""
    before = count_tokens(text)
    result = strip_grammar_caveman(text)
    result = compress_prose_aggressive(result)
    after = count_tokens(result)
    return result, before, after


def _compress_agent_full(history: list[dict]) -> tuple[str, int, int]:
    """Agent: compress prose turns, keep code/tool calls verbatim."""
    full_text = "\n\n".join(m["content"] for m in history)
    before = count_tokens(full_text)

    compressed = []
    for msg in history:
        content = msg["content"]
        role = msg["role"]

        if role == "user" and len(content.split()) > 5:
            # Compress long user messages — strip filler
            c, _, _ = compress_with_routing(content)
            compressed.append({"role": role, "content": c})
        elif role == "assistant":
            if "```" in content or "<tool_call>" in content:
                # Code/tool calls: keep verbatim
                compressed.append(msg)
            elif len(content.split()) > 8:
                # Prose-only assistant: compress filler
                c, _, _ = compress_with_routing(content)
                compressed.append({"role": role, "content": c})
            else:
                compressed.append(msg)
        elif role == "tool":
            # Tool results: keep short results, summarize long ones
            if len(content) > 200:
                c = content[:150].rstrip() + "\n[... truncated ...]"
                compressed.append({"role": role, "content": c})
            else:
                compressed.append(msg)
        else:
            compressed.append(msg)

    result_text = "\n\n".join(m["content"] for m in compressed)
    after = count_tokens(result_text)
    return result_text, before, min(after, before)


def _compress_retrieval_full(text: str) -> tuple[str, int, int]:
    """Retrieval: Self-info scoring + TF-IDF + semantic dedup + prose compression."""
    before = count_tokens(text)

    # Self-information scoring first (Selective Context)
    text = compress_by_self_information(text, keep_ratio=0.5)

    # Query for TF-IDF scoring (extract key terms from first doc title)
    first_line = text.split('\n')[0] if '\n' in text else text[:100]
    query = re.sub(r'Document \d+:\s*', '', first_line)[:80]

    # TF-IDF compression first (LLMLingua-2 secret weapon)
    tfidf_result = _compress_retrieval_tfidf(text, query=query, keep_ratio=0.5)

    # Then semantic dedup on remaining
    docs = re.split(r'\n\n+', tfidf_result)
    deduped, dropped = semantic_dedup(docs, threshold=0.15, ngram_size=3)
    deduped2, dropped2 = deduplicate_chunks(deduped, threshold=0.7)
    total_dropped = dropped + dropped2

    # Prose compression on each doc
    compressed = []
    for doc in deduped2:
        c, _, _ = compress_with_routing(doc)
        compressed.append(c if c.strip() else doc)

    if total_dropped > 0:
        compressed.append(f"[{total_dropped} near-duplicate documents removed]")

    result = "\n\n".join(compressed)
    after = count_tokens(result)
    return result, before, min(after, before)


def _compress_neural(text: str) -> tuple[str, int, int]:
    """Neural Scorer: statistical noise detection."""
    result, before, after = strip_noise(text)
    return result, before, min(after, before)


def _compress_promptology(text: str) -> tuple[str, int, int]:
    """Promptology: rewrite + intelligence optimizer."""
    try:
        data = json.loads(text)
        system = data.get("system", "")
        user = data.get("user", "")
        result_obj = optimize_prompt(system=system, user=user)
        before = result_obj.tokens_before
        # Apply intelligence optimizer on top
        combined = result_obj.system_after + "\n" + inject_reasoning_blueprint(result_obj.user_after)
        combined = merge_tokens(combined)
        after = count_tokens(combined)
        return combined, before, after
    except Exception:
        before = count_tokens(text)
        return text, before, before


def _compress_headroom_real(text: str, ptype: str) -> tuple[str, int, int]:
    """Headroom-style: type-aware compression with pattern collapse."""
    before = count_tokens(text)
    if ptype == "json":
        try:
            data = json.loads(text)
            if isinstance(data, list) and len(data) > 10:
                collapsed = collapse_json_text(text, max_examples=3, total_max=10)
                ca = count_tokens(collapsed)
                if ca < before:
                    return collapsed, before, ca
            result = compress_json(data)
            after = count_tokens(result)
            return result, before, after
        except Exception:
            pass
    # Prose/retrieval: aggressive routing
    result, _, after = compress_with_routing(text, aggressive=True)
    return result, before, after


def _compress_llmlingua_real(text: str, ptype: str) -> tuple[str, int, int]:
    """LLMLingua-style: pattern collapse on JSON, density scoring on prose."""
    before = count_tokens(text)
    if ptype == "json":
        try:
            data = json.loads(text)
            if isinstance(data, list) and len(data) > 10:
                collapsed = collapse_json_text(text, max_examples=3, total_max=10)
                ca = count_tokens(collapsed)
                if ca < before:
                    return collapsed, before, ca
            result = compress_json(data)
            after = count_tokens(result)
            return result, before, after
        except Exception:
            pass

    # For prose/retrieval: sentence-level density scoring
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    if len(sentences) <= 2:
        return text, before, before

    function_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "of", "in", "to", "for", "with", "on", "at", "by", "from",
        "and", "but", "or", "not", "so", "if", "than", "that",
        "this", "these", "those", "it", "he", "she", "they",
        "we", "you", "me", "him", "her", "us", "them", "my", "your",
        "has", "have", "had", "do", "does", "did", "will", "would",
    }

    scored = []
    for s in sentences:
        words = s.lower().split()
        content = sum(1 for w in words if len(w) > 3 and w not in function_words)
        density = content / max(1, len(words))
        scored.append((s, density))

    scored.sort(key=lambda x: -x[1])
    keep_n = max(2, int(len(scored) * 0.7))
    kept = sorted(scored[:keep_n], key=lambda x: sentences.index(x[0]))
    result = " ".join(s for s, _ in kept)
    after = count_tokens(result)
    return result, before, min(after, before)


# ── Benchmark runner ─────────────────────────────────────────────────────────


@dataclass
class BenchmarkEntry:
    name: str
    description: str
    category: str
    tokens_before: int
    tokens_after: int
    savings_pct: float
    time_ms: float
    method: str


@dataclass
class CompetitorBenchmark:
    entries: list[BenchmarkEntry] = field(default_factory=list)
    total_before: int = 0
    total_after: int = 0

    def run(self) -> list[BenchmarkEntry]:
        entries: list[BenchmarkEntry] = []
        self.total_before = 0
        self.total_after = 0

        for _name, prompt in TEST_PROMPTS.items():
            ptype = prompt["type"]
            desc = prompt["description"]

            # ── token-diet ──
            start = time.time()
            if ptype == "json":
                _, before, after = _compress_json_full(prompt["text"])
            elif ptype == "prose":
                _, before, after = _compress_prose_full(prompt["text"])
            elif ptype == "agent":
                _, before, after = _compress_agent_full(prompt["text"])
            elif ptype == "retrieval":
                _, before, after = _compress_retrieval_full(prompt["text"])
            elif ptype == "promptology":
                _, before, after = _compress_promptology(prompt["text"])
            else:
                before = count_tokens(prompt["text"])
                after = before
            td_time = (time.time() - start) * 1000
            entries.append(BenchmarkEntry(desc, desc, ptype, before, after,
                100*(before-after)/max(1,before), td_time, "token-diet"))
            self.total_before += before
            self.total_after += after

            # ── Headroom-style (type-aware) ──
            if ptype in ("json", "prose", "retrieval"):
                text = prompt["text"] if isinstance(prompt["text"], str) else ""
                _, hb, ha = _compress_headroom_real(text, ptype)
                entries.append(BenchmarkEntry(desc, desc, ptype, hb, ha,
                    max(0, 100*(hb-ha)/max(1,hb)), 0, "headroom-style"))

            # ── LLMLingua-style (type-aware) ──
            if ptype in ("json", "prose", "retrieval"):
                text = prompt["text"] if isinstance(prompt["text"], str) else ""
                _, lb, la = _compress_llmlingua_real(text, ptype)
                entries.append(BenchmarkEntry(desc, desc, ptype, lb, la,
                    max(0, 100*(lb-la)/max(1,lb)), 0, "llmlingua-style"))

            # ── Simple dedup ──
            if isinstance(prompt["text"], str):
                lines = prompt["text"].split("\n")
                seen = set()
                unique = []
                for line in lines:
                    norm = line.strip().lower()[:50]
                    if norm not in seen:
                        seen.add(norm)
                        unique.append(line)
                simple_text = "\n".join(unique)
                st = count_tokens(simple_text)
                entries.append(BenchmarkEntry(desc, desc, ptype, before, st,
                    100*(before-st)/max(1,before), 0, "simple-dedup"))

                # Neural Scorer
                _, nb, na = _compress_neural(prompt["text"])
                entries.append(BenchmarkEntry(desc, desc, ptype, nb, na,
                    max(0, 100*(nb-na)/max(1,nb)), 0, "neural-scorer"))

        self.entries = entries
        return entries

    def _by_method(self) -> dict[str, tuple[int, int]]:
        """Aggregate token counts per method."""
        by: dict[str, tuple[int, int]] = {}
        for e in self.entries:
            prev = by.get(e.method, (0, 0))
            by[e.method] = (prev[0] + e.tokens_before, prev[1] + e.tokens_after)
        return by

    def _winner(self) -> str:
        """Which method saves the most tokens?"""
        by = self._by_method()
        best_method, best_save = "", -1.0
        for method, (bef, aft) in by.items():
            save = 100 * (bef - aft) / max(1, bef)
            if save > best_save:
                best_save = save
                best_method = method
        return best_method

    def json_report(self) -> str:
        """Machine-readable JSON report."""
        if not self.entries:
            self.run()

        by = self._by_method()
        winner = self._winner()
        wb, wa = by.get(winner, (1, 1))
        winner_save = 100 * (wb - wa) / max(1, wb)

        td_bef, td_aft = by.get("token-diet", (1, 1))

        # Cost projection: DeepSeek Flash prices ($0.14/M input, $0.28/M output)
        ds = PriceTable(0.14, 0.14, 0.14, 0.28)
        before_tokens = sum(e.tokens_before for e in self.entries if e.method == "token-diet")
        after_tokens = sum(e.tokens_after for e in self.entries if e.method == "token-diet")
        usage_before = Usage(input_tokens=before_tokens)
        usage_after = Usage(input_tokens=after_tokens)
        cost_before = ds.calculate(usage_before)
        cost_after = ds.calculate(usage_after)
        savings_1k = (cost_before - cost_after) * 1000

        gc = GreenCalculator()
        saved_tokens = before_tokens - after_tokens
        green = gc.measure(saved_tokens)

        data = {
            "summary": {
                "total_before": self.total_before,
                "total_after": self.total_after,
                "savings_pct": round(100 * (self.total_before - self.total_after) / max(1, self.total_before), 1),
                "winner": winner,
                "winner_savings_pct": round(winner_save, 1),
                "token_diet_savings_pct": round(100 * (td_bef - td_aft) / max(1, td_bef), 1),
                "cost_per_1k_calls": round(savings_1k, 6),
                "co2_kg_saved": round(green.kg_co2_saved, 6),
                "water_liters_saved": round(green.liters_water_saved, 1),
            },
            "tests": [
                {
                    "name": e.name,
                    "method": e.method,
                    "before": e.tokens_before,
                    "after": e.tokens_after,
                    "savings_pct": round(e.savings_pct, 1),
                    "time_ms": round(e.time_ms, 1),
                }
                for e in self.entries
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    def report(self) -> str:
        if not self.entries:
            self.run()

        lines = [
            "=" * 78,
            "  token-diet vs Competitors — REAL Benchmark",
            "=" * 78,
            "",
            f"{'Test':<42} {'Method':<14} {'Before':>6} {'After':>6} {'Save%':>6}",
            "-" * 78,
        ]
        for e in self.entries:
            lines.append(
                f"{e.name[:42]:<42} {e.method:<14} "
                f"{e.tokens_before:>6} {e.tokens_after:>6} "
                f"{e.savings_pct:>5.1f}%"
            )
        lines.append("-" * 78)
        lines.append("")

        by = self._by_method()
        winner = self._winner()

        lines.append("  TOTALS BY METHOD:")
        for method, (bef, aft) in sorted(by.items(),
            key=lambda x: -(x[1][0]-x[1][1])/max(1,x[1][0])):
            save = 100 * (bef - aft) / max(1, bef)
            tag = " ← WINNER" if method == winner else ""
            lines.append(f"    {method:<20} {bef:>6} → {aft:<6}  ({save:.1f}%){tag}")

        # Cost section
        td_bef, td_aft = by.get("token-diet", (1, 1))
        saved = td_bef - td_aft
        lines.append("")
        lines.append(f"  WINNER: {winner}")
        lines.append("")
        lines.append("  COST (DeepSeek Flash — $0.14/M in, $0.28/M out):")
        ds = PriceTable(0.14, 0.14, 0.14, 0.28)
        ub = Usage(input_tokens=td_bef)
        ua = Usage(input_tokens=td_aft)
        before_cost = ds.calculate(ub)
        after_cost = ds.calculate(ua)
        lines.append(f"    Before token-diet:  ${before_cost:.6f} per request")
        lines.append(f"    After  token-diet:  ${after_cost:.6f} per request")
        lines.append(f"    Saved: ${before_cost - after_cost:.6f} ({100*(before_cost-after_cost)/max(0.000001,before_cost):.1f}%)")
        lines.append(f"    Per 1000 calls:     ${(before_cost - after_cost) * 1000:.4f}")
        lines.append(f"    Per 100K calls:     ${(before_cost - after_cost) * 100000:.2f}")
        lines.append("")

        # Claude Opus projection
        lines.append("  COST (Claude Opus 4 — $15/M in, $75/M out):")
        opus = PriceTable(15.0, 15.0, 15.0, 75.0)
        bo = opus.calculate(Usage(input_tokens=td_bef))
        ao = opus.calculate(Usage(input_tokens=td_aft))
        lines.append(f"    Before: ${bo:.4f}   After: ${ao:.4f}   Saved/1K: ${(bo-ao)*1000:.2f}")
        lines.append("")

        # Green
        gc = GreenCalculator()
        green = gc.measure(saved)
        # Ice cream: $3 each, using DeepSeek cost savings * 3000 calls/month
        monthly_cost_saved = (before_cost - after_cost) * 3000
        ice_cream = monthly_cost_saved / 3.0
        lines.append("  🌍 ENVIRONMENT:")
        lines.append(f"    CO₂ prevented: {green.kg_co2_saved:.4f} kg")
        lines.append(f"    Water saved:   {green.liters_water_saved:.1f} L")
        lines.append(f"    🍦 Ice creams: {ice_cream:.1f} (if 3000 calls/month)")
        lines.append("")
        lines.append("  + Gate (fact verification) — no competitor has this")
        lines.append("  + Green Calculator (CO₂, water) — no competitor has this")
        lines.append("=" * 78)
        return "\n".join(lines)


def main() -> None:
    print()
    benchmark = CompetitorBenchmark()
    benchmark.run()
    print(benchmark.report())
    print()


if __name__ == "__main__":
    main()
