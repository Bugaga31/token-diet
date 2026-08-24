"""open_generative — provider aggregation gateway (Open-Generative-AI style).

Reverse-engineered from Open-Generative-AI (Anil-matcha / muapi.ai):
    A self-hosted AI gateway that aggregates many generative models
    behind ONE normalized interface. Three techniques matter:

    1. Payload normalization — provider-specific request shapes are
       translated to/from one canonical schema (messages, model,
       params) so the caller never sees provider differences.
    2. Fallback chains — if provider A returns a rate-limit (429) or
       5xx, the request automatically fails over to provider B.
    3. Prompt caching — identical requests are served from a cache
       keyed on sha256(payload), saving both money and latency.

Our take — deterministic, pure stdlib:
    The ROUTING/FALLBACK/CACHE logic is fully testable with fake
    providers (no network, no keys). Plug real providers in later by
    providing a callable. No API keys live in this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# HTTP-ish statuses that trigger failover (mirrors provider gateways).
_RETRYABLE = {408, 429, 500, 502, 503, 504}
# Canonical parameter names (normalization vocabulary).
_CANONICAL = {
    "model": ("model", "model_name", "engine", "name"),
    "temperature": ("temperature", "temp"),
    "max_tokens": ("max_tokens", "max_output_tokens", "max_completion_tokens"),
    "top_p": ("top_p", "topP", "nucleus"),
}


@dataclass
class Provider:
    """One generative provider endpoint in the gateway registry."""
    name: str
    kind: str = "chat"             # chat | image | video | audio
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0
    healthy: bool = True
    last_error: str = ""
    handler: Callable | None = None   # pluggable callable for tests/live


@dataclass
class GatewayResult:
    ok: bool
    provider: str = ""
    status: int = 200
    data: Any = None
    error: str = ""
    cached: bool = False
    attempts: int = 1


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Map provider-specific keys onto the canonical schema (and back).

    forward: {"model_name": "x", "temp": 0.2} → {"model": "x",
             "temperature": 0.2, "max_tokens": ...}
    """
    out: dict[str, Any] = {}
    # copy passthrough keys (messages, prompt, image, etc.)
    for k, v in payload.items():
        mapped = None
        for canon, aliases in _CANONICAL.items():
            if k in aliases:
                mapped = canon
                break
        if mapped:
            out[mapped] = v
        else:
            out[k] = v
    return out


def payload_key(payload: dict[str, Any]) -> str:
    """Deterministic cache key: sha256 of the canonical payload."""
    canon = json.dumps(normalize_payload(payload), sort_keys=True,
                       ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


class GenerativeGateway:
    """Provider registry + fallback chains + payload cache.

    Usage (with fake providers for tests):
        gw = GenerativeGateway()
        gw.add(Provider("a", handler=lambda p: {"status": 429}))
        gw.add(Provider("b", handler=lambda p: {"status": 200, "text": "ok"}))
        res = gw.generate({"prompt": "hi"})
        # -> provider="b", attempts=2  (failed over on 429)
    """

    def __init__(self, cache_max: int = 1024):
        self.providers: list[Provider] = []
        self.cache: dict[str, dict[str, Any]] = {}
        self.cache_max = cache_max
        self.stats: dict[str, int] = {
            "calls": 0, "cache_hits": 0, "failovers": 0, "errors": 0}

    def add(self, provider: Provider) -> None:
        self.providers.append(provider)

    # ── core dispatch ────────────────────────────────────────────────────

    def generate(
        self,
        payload: dict[str, Any],
        order: list[str] | None = None,
        use_cache: bool = True,
        max_attempts: int = 3,
    ) -> GatewayResult:
        """Send a request through the provider chain (with failover+cache)."""
        self.stats["calls"] += 1
        key = payload_key(payload)

        if use_cache and key in self.cache:
            self.stats["cache_hits"] += 1
            cached = self.cache[key]
            return GatewayResult(ok=True, provider=cached["provider"],
                                 data=cached["data"], cached=True, attempts=1)

        chain = order or [p.name for p in self.providers if p.healthy]
        attempts = 0
        last_err = ""
        for name in chain:
            provider = self._by_name(name)
            if not provider or not provider.healthy:
                continue
            attempts += 1
            try:
                if provider.handler is not None:
                    raw = provider.handler(payload)
                else:
                    # no handler → simulate a live endpoint contract
                    raw = self._live_call(provider, payload)
                status = raw.get("status", 200)
                if status in _RETRYABLE:
                    self.stats["failovers"] += 1
                    last_err = f"{name}: HTTP {status}"
                    provider.last_error = last_err
                    continue
                if status >= 400:
                    self.stats["errors"] += 1
                    provider.last_error = f"{name}: HTTP {status}"
                    return GatewayResult(ok=False, provider=name, status=status,
                                         error=provider.last_error,
                                         attempts=attempts)
                # success
                provider.healthy = True
                if use_cache and len(self.cache) < self.cache_max:
                    self.cache[key] = {"provider": name, "data": raw.get("data")}
                return GatewayResult(ok=True, provider=name, status=status,
                                     data=raw.get("data"), attempts=attempts)
            except Exception as e:  # handler raised → failover
                attempts += 1
                self.stats["failovers"] += 1
                last_err = f"{name}: {type(e).__name__}: {e}"
                provider.last_error = last_err

        self.stats["errors"] += 1
        return GatewayResult(ok=False, status=0,
                             error=last_err or "no healthy providers",
                             attempts=attempts)

    # ── live-endpoint contract (default handler) ─────────────────────────

    def _live_call(self, provider: Provider, payload: dict) -> dict:
        """Default: call the provider's HTTP API. No-op here (no network).

        Subclass or set provider.handler to plug a real transport
        (httpx/urllib). Kept deterministic so the gateway logic is
        testable offline.
        """
        raise NotImplementedError(
            f"provider '{provider.name}' has no handler; set one or subclass")

    # ── helpers ──────────────────────────────────────────────────────────

    def _by_name(self, name: str) -> Provider | None:
        for p in self.providers:
            if p.name == name:
                return p
        return None

    def estimate_cost(self, provider: str, in_tokens: int, out_tokens: int) -> float:
        p = self._by_name(provider)
        if not p:
            return 0.0
        return (in_tokens / 1_000_000 * p.cost_per_1m_input
                + out_tokens / 1_000_000 * p.cost_per_1m_output)

    def choose_cheapest(self, in_tokens: int, out_tokens: int) -> str:
        """Pick the healthy provider with the lowest estimated cost."""
        best, best_cost = None, float("inf")
        for p in self.providers:
            if not p.healthy:
                continue
            cost = self.estimate_cost(p.name, in_tokens, out_tokens)
            if cost < best_cost:
                best, best_cost = p.name, cost
        return best or ""

    def summary(self) -> dict[str, Any]:
        return {
            "providers": [p.name for p in self.providers],
            "healthy": [p.name for p in self.providers if p.healthy],
            "cache_size": len(self.cache),
            **self.stats,
        }


def estimate_gateway_savings(
    calls: int, cache_hit_rate: float, avg_prompt_tokens: int,
    price_per_1m_input: float,
) -> dict[str, Any]:
    """Money saved by caching + failover (per 1M calls)."""
    cached_calls = calls * cache_hit_rate
    fresh_calls = calls - cached_calls
    saved_tokens = cached_calls * avg_prompt_tokens
    saved_usd = saved_tokens / 1_000_000 * price_per_1m_input
    return {
        "calls": calls,
        "cache_hit_rate": cache_hit_rate,
        "cached_calls": int(cached_calls),
        "fresh_calls": int(fresh_calls),
        "saved_tokens": int(saved_tokens),
        "saved_usd": round(saved_usd, 2),
    }


__all__ = [
    "GatewayResult",
    "GenerativeGateway",
    "Provider",
    "estimate_gateway_savings",
    "normalize_payload",
    "payload_key",
]
