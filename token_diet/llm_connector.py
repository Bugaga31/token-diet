"""LLM Connector — real API calls with actual token measurement.

Supports Anthropic, OpenAI, and DeepSeek APIs. When no API key is available,
falls back to deterministic simulation using count_tokens and the
built-in deterministic_judge for quality comparison.

DeepSeek pricing (per 1M tokens):
  deepseek-v4-flash: $0.14 input (cache miss), $0.14 input (cache hit), $0.28 output
  deepseek-v4-pro:   $0.435 input, $0.87 output

Usage:
    conn = LLMConnector.from_env()  # auto-detect available API keys
    result = conn.ask("What is the refund policy?", system_prompt="...")
    print(result.usage.input_tokens, result.usage.output_tokens, result.cost)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from .core import count_tokens, PriceTable, Usage
    from .loss_router import reduce_output
except ImportError:
    from core import count_tokens, PriceTable, Usage  # type: ignore[no-redef]
    from loss_router import reduce_output  # type: ignore[no-redef]

# Default price tables per provider
PROVIDER_PRICES = {
    "openai": PriceTable(3.0, 3.75, 0.30, 15.0),
    "anthropic": PriceTable(3.0, 3.75, 0.30, 15.0),
    "deepseek": PriceTable(0.14, 0.14, 0.14, 0.28),  # flash pricing
    "deepseek-pro": PriceTable(0.435, 0.435, 0.435, 0.87),
    "simulation": PriceTable(3.0, 3.75, 0.30, 15.0),
}


@dataclass
class LLMResult:
    answer: str
    usage: Usage
    cost: float
    model: str
    real: bool = False  # True = real API call, False = simulation

    def render(self) -> str:
        return (
            f"model={self.model} real={self.real}\n"
            f"input={self.usage.input_tokens} cache_w={self.usage.cache_write_tokens} "
            f"cache_r={self.usage.cache_read_tokens} output={self.usage.output_tokens}\n"
            f"cost=${self.cost:.6f}\nanswer_preview={self.answer[:120]!r}"
        )


class LLMConnector:
    """Ask an LLM and get back real token counts.

    Auto-detects available API keys: ANTHROPIC_API_KEY, OPENAI_API_KEY.
    Falls back to simulation when no keys are found.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        provider: str | None = None,
        prices: PriceTable | None = None,
    ):
        self.provider = provider or self._detect_provider(api_key)
        self.api_key = api_key or self._detect_key(self.provider)
        self.model = model or {
            "anthropic": "claude-3-5-haiku-latest",
            "openai": "gpt-4o-mini",
            "deepseek": "deepseek-v4-flash",
            "deepseek-pro": "deepseek-v4-pro",
            "simulation": "gpt-4o-mini-sim",
        }.get(self.provider, "deepseek-v4-flash")
        self.prices = prices or PROVIDER_PRICES.get(self.provider, PROVIDER_PRICES["simulation"])

    @staticmethod
    def from_env() -> LLMConnector:
        return LLMConnector()

    @staticmethod
    def _detect_provider(api_key: str | None) -> str:
        key = api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
        if os.getenv("DEEPSEEK_API_KEY") or (api_key and api_key.startswith("sk-") and not api_key.startswith("sk-ant-")):
            # Generic sk- keys default to deepseek since it's cheapest
            return "deepseek"
        if os.getenv("ANTHROPIC_API_KEY") or (api_key and key.startswith("sk-ant-")):
            return "anthropic"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        return "simulation"

    @staticmethod
    def _detect_key(provider: str) -> str:
        if provider == "deepseek" or provider == "deepseek-pro":
            return os.getenv("DEEPSEEK_API_KEY", "")
        if provider == "anthropic":
            return os.getenv("ANTHROPIC_API_KEY", "")
        if provider == "openai":
            return os.getenv("OPENAI_API_KEY", "")
        return ""

    def ask(
        self,
        question: str,
        system_prompt: str = "",
        tools: dict | None = None,
        max_tokens: int = 500,
    ) -> LLMResult:
        try:
            if self.provider == "simulation":
                return self._simulate(question, system_prompt, tools, max_tokens)
            if self.provider == "anthropic":
                return self._ask_anthropic(question, system_prompt, tools, max_tokens)
            if self.provider in ("openai", "deepseek", "deepseek-pro"):
                return self._ask_openai_compat(question, system_prompt, tools, max_tokens)
            return self._simulate(question, system_prompt, tools, max_tokens)
        except Exception as exc:
            # Graceful fallback: API error → simulation with apology
            simulated = self._simulate(question, system_prompt, tools, max_tokens)
            simulated.answer = (
                f"[API ERROR: {type(exc).__name__}. "
                f"Falling back to simulation.\n{simulated.answer}"
            )
            return simulated

    # ── simulation fallback ────────────────────────────────────────────

    def _simulate(
        self, question: str, system: str, tools: dict | None, max_tokens: int
    ) -> LLMResult:
        input_t = count_tokens(system) + count_tokens(question)
        if tools:
            input_t += count_tokens(json.dumps(tools, ensure_ascii=False))
        output_t = min(max_tokens, max(50, input_t // 4))
        usage = Usage(input_tokens=input_t, output_tokens=output_t)
        return LLMResult(
            answer=f"[SIMULATED ANSWER to: {question[:80]}]",
            usage=usage,
            cost=self.prices.calculate(usage),
            model=self.model,
            real=False,
        )

    # ── OpenAI / DeepSeek (both OpenAI-compatible) ─────────────────────

    def _ask_openai_compat(
        self, question: str, system: str, tools: dict | None, max_tokens: int
    ) -> LLMResult:
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("pip install openai") from exc

        # DeepSeek uses its own base URL; OpenAI uses default
        base_url = None
        if self.provider in ("deepseek", "deepseek-pro"):
            base_url = "https://api.deepseek.com"

        client = openai.OpenAI(api_key=self.api_key, base_url=base_url) if base_url else openai.OpenAI(api_key=self.api_key)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": question})
        kwargs: dict = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            kwargs["tools"] = tools
        response = client.chat.completions.create(**kwargs)
        usage_data = response.usage
        usage = Usage(
            input_tokens=usage_data.prompt_tokens,
            cache_write_tokens=0,
            cache_read_tokens=getattr(usage_data, "cache_read_input_tokens", 0) or 0,
            output_tokens=usage_data.completion_tokens,
        )
        details = getattr(usage_data, "prompt_tokens_details", None)
        if details is not None and hasattr(details, "cached_tokens"):
            cw = getattr(details, "cached_tokens", 0)
            usage.cache_write_tokens = max(0, usage.input_tokens - cw)
            usage.cache_read_tokens = cw
        else:
            usage.cache_write_tokens = usage.input_tokens
        return LLMResult(
            answer=response.choices[0].message.content or "",
            usage=usage,
            cost=self.prices.calculate(usage),
            model=self.model,
            real=True,
        )

    # ── Anthropic (anthropic package) ───────────────────────────────────

    def _ask_anthropic(
        self, question: str, system: str, tools: dict | None, max_tokens: int
    ) -> LLMResult:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("pip install anthropic") from exc

        client = anthropic.Anthropic(api_key=self.api_key)
        kwargs: dict = {
            "model": self.model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": question}],
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        response = client.messages.create(**kwargs)
        usage = Usage(
            input_tokens=response.usage.input_tokens,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            output_tokens=response.usage.output_tokens,
        )
        # Anthropic returns text blocks; concatenate
        answer = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        return LLMResult(
            answer=answer,
            usage=usage,
            cost=self.prices.calculate(usage),
            model=self.model,
            real=True,
        )
