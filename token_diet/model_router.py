"""Model Router — complexity-based routing.

Inspired by Not Diamond and RouteLLM. Routes simple queries to cheap
models (DeepSeek Flash $0.14/M) and complex reasoning to premium models
(Claude Opus $5/M). Savings: 40-60% on mixed workloads.

Uses deterministic complexity scoring (no extra API calls):
  - Question length, keyword complexity, tool count
  - Not a model call — asking a model which model to use defeats the purpose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RouterConfig:
    """Model routing tiers."""

    cheap_model: str = "deepseek-v4-flash"
    cheap_price_in: float = 0.14
    cheap_price_out: float = 0.28

    mid_model: str = "gpt-4o-mini"
    mid_price_in: float = 0.15
    mid_price_out: float = 0.60

    premium_model: str = "claude-opus-4-5"
    premium_price_in: float = 5.00
    premium_price_out: float = 25.00

    cheap_threshold: float = 0.3   # below = cheap
    mid_threshold: float = 0.6     # below = mid, above = premium


@dataclass
class RoutingDecision:
    model: str
    tier: str  # "cheap" | "mid" | "premium"
    complexity: float
    reason: str
    estimated_cost: float = 0.0


_COMPLEX_WORDS = re.compile(
    r"\b(?:analyze|compare|evaluate|synthesize|debug|optimize|refactor|"
    r"reason|explain why|justify|prove|design|architecture|security|"
    r"vulnerability|compliance|audit|legal|medical|diagnose)\b",
    re.I,
)
_SIMPLE_WORDS = re.compile(
    r"\b(?:what is|how to|list|show|find|get|fetch|summary|summarize|"
    r"translate|convert|calculate|count)\b",
    re.I,
)


def route_query(
    question: str,
    context_tokens: int = 0,
    tool_count: int = 0,
    config: RouterConfig | None = None,
) -> RoutingDecision:
    """Route a query to the appropriate model tier.

    Complexity factors (all deterministic, no API call):
      - Question length (tokens)
      - Complex keywords (analyze, evaluate, debug, etc.)
      - Simple keywords (what is, list, show, etc.)
      - Context size (tokens)
      - Tool count

    Returns RoutingDecision with model and estimated cost.
    """
    cfg = config or RouterConfig()

    # Compute complexity score (0..1)
    words = len(question.split())
    complex_hits = len(_COMPLEX_WORDS.findall(question))
    simple_hits = len(_SIMPLE_WORDS.findall(question))

    score = 0.0

    # Question length
    if words > 80:
        score += 0.3
    elif words > 40:
        score += 0.15

    # Complex keywords
    score += min(0.6, complex_hits * 0.25)

    # Simple keywords reduce complexity
    score -= min(0.2, simple_hits * 0.05)

    # Context size
    if context_tokens > 5000:
        score += 0.2
    elif context_tokens > 2000:
        score += 0.1

    # Tool count
    if tool_count > 5:
        score += 0.15
    elif tool_count > 2:
        score += 0.05

    score = max(0.0, min(1.0, score))

    # Route
    if score <= cfg.cheap_threshold:
        model = cfg.cheap_model
        tier = "cheap"
    elif score <= cfg.mid_threshold:
        model = cfg.mid_model
        tier = "mid"
    else:
        model = cfg.premium_model
        tier = "premium"

    return RoutingDecision(
        model=model,
        tier=tier,
        complexity=score,
        reason=_describe_route(score, complex_hits, words, context_tokens, tool_count),
    )


def _describe_route(score: float, complex_hits: int, words: int, ctx: int, tools: int) -> str:
    parts = [f"complexity={score:.2f}"]
    if complex_hits:
        parts.append(f"{complex_hits} complex keywords")
    if words > 40:
        parts.append(f"{words} words")
    if ctx > 2000:
        parts.append(f"{ctx} ctx tokens")
    if tools:
        parts.append(f"{tools} tools")
    return "; ".join(parts)


def route_and_estimate(
    question: str,
    context_tokens: int = 0,
    output_tokens: int = 200,
    tool_count: int = 0,
    config: RouterConfig | None = None,
) -> RoutingDecision:
    """Route and estimate cost."""
    decision = route_query(question, context_tokens, tool_count, config)
    cfg = config or RouterConfig()

    prices = {
        "cheap": (cfg.cheap_price_in, cfg.cheap_price_out),
        "mid": (cfg.mid_price_in, cfg.mid_price_out),
        "premium": (cfg.premium_price_in, cfg.premium_price_out),
    }
    pin, pout = prices[decision.tier]
    decision.estimated_cost = (
        context_tokens * pin + output_tokens * pout
    ) / 1_000_000

    return decision


# ── Savings calculation ───────────────────────────────────────────────────────


@dataclass
class RouteSavings:
    direct_premium_cost: float
    routed_cost: float
    savings: float
    savings_pct: float
    cheap_pct: float  # % of queries routed to cheap tier
    mid_pct: float
    premium_pct: float


def estimate_mixed_savings(
    queries: list[str],
    config: RouterConfig | None = None,
    avg_ctx: int = 1000,
    avg_out: int = 200,
) -> RouteSavings:
    """Estimate savings from routing vs always using premium."""
    cfg = config or RouterConfig()
    decisions = [route_and_estimate(q, avg_ctx, avg_out, config=cfg) for q in queries]

    total_routed = sum(d.estimated_cost for d in decisions)
    total_premium = len(queries) * (
        avg_ctx * cfg.premium_price_in + avg_out * cfg.premium_price_out
    ) / 1_000_000

    cheap_count = sum(1 for d in decisions if d.tier == "cheap")
    mid_count = sum(1 for d in decisions if d.tier == "mid")
    premium_count = sum(1 for d in decisions if d.tier == "premium")
    total = len(queries)

    return RouteSavings(
        direct_premium_cost=total_premium,
        routed_cost=total_routed,
        savings=total_premium - total_routed,
        savings_pct=100 * (total_premium - total_routed) / max(0.000001, total_premium),
        cheap_pct=100 * cheap_count / total,
        mid_pct=100 * mid_count / total,
        premium_pct=100 * premium_count / total,
    )
