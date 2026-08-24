"""cache_keepalive — keep provider prompt-cache alive at the optimal cost.

Reverse-engineered from "Keepalive Economics" (arXiv:2607.19214) and
"Don't Break the Cache" (arXiv:2601.06007).

THE PROBLEM:
    Provider prompt caching (Anthropic/OpenAI/DeepSeek) gives ~90%
    cheaper cached input tokens — but only while the cache is warm.
    Idle caches die after a TTL (Anthropic ~5 min, OpenAI ~5-10 min,
    DeepSeek ~5-10 min), and the next call pays FULL re-prefill cost
    for the whole stable prefix.

THE INSIGHT (the paper's math):
    - Pinging every 30s wastes money: you pay read-token price on every
      ping without needing the cache.
    - The OPTIMAL ping interval is the largest value safely below the
      TTL margin:  τ* ≈ TTL − 60s  (≈ 240s for a 5-min TTL).
    - Keepalive becomes COUNTERPRODUCTIVE when the idle gap exceeds
      the break-even horizon  I_max ≈ τ(w/r − 1)  ≈ 36-46 minutes.
      Past that, the keepalive "rent" exceeds the cost of a cold
      re-prefill → let the cache die.

WHAT THIS MODULE DOES:
    Pure-python policy engine: tells you WHEN to ping, WHEN to let the
    cache die, and ESTIMATES the savings vs no-keepalive. It is
    provider-agnostic — you plug in your own client call (the module
    never touches the network, so it stays testable offline).

Token economics:
    With keepalive, every call in a long agent session pays cached
    input price (~1/10th). Without it, the first call after every
    idle gap pays full price. On 50+ calls/day that's the difference
    between 100% and ~15% input cost.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# ── policy constants ─────────────────────────────────────────────────────────


@dataclass
class CachePolicy:
    """Provider cache policy: TTL and price ratio."""
    ttl_seconds: int = 300          # 5 minutes (Anthropic default)
    warm_read_ratio: float = 0.1    # cached-input price as fraction of full price

    @property
    def optimal_interval(self) -> int:
        """τ*: ping just before the TTL would expire (paper's formula)."""
        return max(60, self.ttl_seconds - 60)

    @property
    def break_even_horizon(self) -> int:
        """I_max: how long we keep paying rent before it's cheaper to re-prefill.

        I_max = τ(w/r − 1), where w = 1 (full price), r = warm_read_ratio.
        """
        return int(self.optimal_interval * (1.0 / self.warm_read_ratio - 1))


# ── keepalive manager (policy only, offline-testable) ────────────────────────


@dataclass
class KeepaliveDecision:
    """What the policy recommends right now."""
    should_ping: bool
    should_let_die: bool
    idle_seconds: float
    reason: str


@dataclass
class KeepaliveStats:
    """Accumulated savings estimate over a session."""
    pings_sent: int = 0
    calls_made: int = 0
    cache_warm_calls: int = 0
    cache_cold_calls: int = 0
    input_tokens_per_call: int = 0

    @property
    def savings_pct(self) -> float:
        """% of input tokens billed at the (cheap) cached rate."""
        if not self.calls_made:
            return 0.0
        return round(100 * self.cache_warm_calls / self.calls_made, 1)

    def render(self) -> str:
        return (
            f"Keepalive: {self.cache_warm_calls}/{self.calls_made} вызовов "
            f"с тёплым кэшем ({self.savings_pct}%) · пингов: {self.pings_sent}"
        )


class KeepaliveManager:
    """Decide when to ping the provider cache to keep it warm.

    Usage:
        km = KeepaliveManager()                  # default 5-min TTL policy
        if km.should_ping():                      # policy says ping now
            your_client.send_ping(...)            # your own 1-token call
            km.record_ping()                      # mark that we pinged
        # ... real calls ...
        km.record_call(cache_warm=True)           # mark each real call
    """

    def __init__(self, policy: CachePolicy | None = None):
        self.policy = policy or CachePolicy()
        self.last_active = time.time()
        self.stats = KeepaliveStats()

    # ── decisions ──────────────────────────────────────────────────────────

    def decide(self, now: float | None = None) -> KeepaliveDecision:
        """Return the recommended action (ping / let die / do nothing)."""
        now = now or time.time()
        idle = now - self.last_active

        # Past the break-even horizon → stop paying rent, let it die.
        if idle > self.policy.break_even_horizon:
            return KeepaliveDecision(
                should_ping=False,
                should_let_die=True,
                idle_seconds=idle,
                reason=(f"простой {idle/60:.0f} мин > порога "
                        f"{self.policy.break_even_horizon/60:.0f} мин — "
                        f"дешевле пере-префиллить заново, пинг невыгоден"),
            )

        # Idle approaching the TTL → ping now to refresh.
        if idle >= self.policy.optimal_interval:
            return KeepaliveDecision(
                should_ping=True,
                should_let_die=False,
                idle_seconds=idle,
                reason=(f"простой {idle:.0f}с ≥ оптимума "
                        f"{self.policy.optimal_interval}с — пингую, "
                        f"иначе кэш умрёт через "
                        f"{max(0, self.policy.ttl_seconds - idle):.0f}с"),
            )

        # Cache still warm, no ping needed.
        return KeepaliveDecision(
            should_ping=False,
            should_let_die=False,
            idle_seconds=idle,
            reason=f"кэш тёплый, пинг через {self.policy.optimal_interval - idle:.0f}с",
        )

    def should_ping(self, now: float | None = None) -> bool:
        return self.decide(now).should_ping

    # ── events ─────────────────────────────────────────────────────────────

    def record_ping(self, now: float | None = None) -> None:
        """Call after sending a keepalive ping (refreshes the clock)."""
        self.last_active = now or time.time()
        self.stats.pings_sent += 1

    def record_call(self, cache_warm: bool, now: float | None = None) -> None:
        """Call after a real request; cache_warm = was the prefix cached?"""
        self.last_active = now or time.time()
        self.stats.calls_made += 1
        if cache_warm:
            self.stats.cache_warm_calls += 1
        else:
            self.stats.cache_cold_calls += 1


# ── cost math ────────────────────────────────────────────────────────────────


@dataclass
class KeepaliveEconomics:
    """Cost comparison: keepalive vs no-keepalive for a session."""
    n_calls: int = 0
    input_tokens: int = 0
    price_per_1k_full: float = 0.0
    price_per_1k_warm: float = 0.0

    def no_keepalive_cost(self) -> float:
        """Every call pays FULL re-prefill (cache died during idle gaps)."""
        return self.n_calls * self.input_tokens / 1000 * self.price_per_1k_full

    def with_keepalive_cost(self, warm_calls: int | None = None) -> float:
        """Warm calls pay the cheap cached price, cold calls pay full."""
        warm = warm_calls if warm_calls is not None else max(0, self.n_calls - 1)
        cold = self.n_calls - warm
        per = self.input_tokens / 1000
        return warm * per * self.price_per_1k_warm + cold * per * self.price_per_1k_full

    def savings_pct(self, warm_calls: int | None = None) -> float:
        base = self.no_keepalive_cost()
        if base <= 0:
            return 0.0
        return round(100 * (base - self.with_keepalive_cost(warm_calls)) / base, 1)


def estimate_keepalive_savings(
    n_calls: int,
    input_tokens: int,
    price_full: float = 0.003,   # $ per 1k input (full)
    price_warm: float = 0.0003,  # $ per 1k input (cached)
) -> dict[str, Any]:
    """Quick estimate: how much keepalive saves on an agent session."""
    econ = KeepaliveEconomics(
        n_calls=n_calls, input_tokens=input_tokens,
        price_per_1k_full=price_full, price_per_1k_warm=price_warm,
    )
    base = econ.no_keepalive_cost()
    warm = econ.with_keepalive_cost()
    return {
        "n_calls": n_calls,
        "input_tokens_per_call": input_tokens,
        "no_keepalive_usd": round(base, 4),
        "with_keepalive_usd": round(warm, 4),
        "savings_usd": round(base - warm, 4),
        "savings_pct": econ.savings_pct(),
        "optimal_ping_seconds": CachePolicy().optimal_interval,
        "break_even_minutes": round(CachePolicy().break_even_horizon / 60, 1),
    }


__all__ = [
    "CachePolicy",
    "KeepaliveDecision",
    "KeepaliveEconomics",
    "KeepaliveManager",
    "KeepaliveStats",
    "estimate_keepalive_savings",
]
