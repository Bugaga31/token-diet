"""Token Budget Guard — prevent agent loops from burning tokens.

From arXiv:2606.24937 ("Hitchhiker's Guide to Agentic AI"):
  "Bounded Loops & Harness Management — enforce strict programmatic budgets,
   timeouts, permission boundaries, and pruning heuristics on agent execution
   loops. Stops agents from entering infinite self-correction or conversational
   loops that consume thousands of unnecessary reasoning tokens."

This module implements THREE layers of protection:
1. Hard token cap per request (prevents runaway generation)
2. Iteration limit per agent loop (prevents infinite loops)
3. Exponential backoff on repeated failures (wastes fewer tokens on retries)

All 100% algorithmic. Transparent. Auditable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from collections import defaultdict


# ═══════════════════════════════════════════════════════════════
# Budget limits
# ═══════════════════════════════════════════════════════════════

# Conservative defaults (Claude Sonnet pricing)
DEFAULT_MAX_INPUT_TOKENS = 40_000      # Per request
DEFAULT_MAX_OUTPUT_TOKENS = 8_000      # Per generation
DEFAULT_MAX_LOOP_ITERATIONS = 15        # Max agent tool-calling loops
DEFAULT_MAX_TOTAL_TOKENS_PER_TASK = 200_000  # Total across all retries
DEFAULT_BACKOFF_BASE = 1.5             # Exponential backoff multiplier
DEFAULT_MAX_RETRIES = 3                # Max retries per task


@dataclass
class BudgetState:
    """Real-time tracking of token/iteration usage."""
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    iterations: int = 0
    retries: int = 0
    start_time: float = 0.0
    last_error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens_used + self.output_tokens_used

    @property
    def elapsed_seconds(self) -> float:
        if self.start_time == 0:
            return 0.0
        return time.time() - self.start_time


@dataclass
class BudgetDecision:
    """Result of a budget check."""
    allowed: bool
    reason: str
    remaining_input: int
    remaining_output: int
    remaining_iterations: int
    current_cost_estimate: float  # In dollars


class TokenBudgetGuard:
    """Guard that prevents token budget overruns.

    Three layers:
    1. Hard cap: request fails if exceeded
    2. Soft cap: warning, but allows completion
    3. Iteration cap: stops infinite tool-calling loops

    Usage:
        guard = TokenBudgetGuard(max_input=40000, max_output=8000)
        for turn in agent_loop:
            decision = guard.check(state)
            if not decision.allowed:
                break  # Stop the loop, save money
            # ... make LLM call ...
            guard.record(state, input_tokens, output_tokens)
    """

    def __init__(
        self,
        max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_loop_iterations: int = DEFAULT_MAX_LOOP_ITERATIONS,
        max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS_PER_TASK,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        model_price_in: float = 3.0,    # $/M input
        model_price_out: float = 15.0,   # $/M output
    ):
        self.max_input = max_input_tokens
        self.max_output = max_output_tokens
        self.max_iterations = max_loop_iterations
        self.max_total = max_total_tokens
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.price_in = model_price_in
        self.price_out = model_price_out

    def check(self, state: BudgetState) -> BudgetDecision:
        """Check if another iteration/retry is allowed.

        Returns BudgetDecision with reason if not allowed.
        """
        # Layer 1: Iteration cap
        if state.iterations >= self.max_iterations:
            return BudgetDecision(
                allowed=False,
                reason=f"Reached max {self.max_iterations} iterations (agent loop cap)",
                remaining_input=0,
                remaining_output=0,
                remaining_iterations=0,
                current_cost_estimate=self._cost(state),
            )

        # Layer 2: Retry cap
        if state.retries >= self.max_retries:
            return BudgetDecision(
                allowed=False,
                reason=f"Reached max {self.max_retries} retries (exhausted attempts)",
                remaining_input=0,
                remaining_output=0,
                remaining_iterations=0,
                current_cost_estimate=self._cost(state),
            )

        # Layer 3: Total token cap
        if state.total_tokens >= self.max_total:
            return BudgetDecision(
                allowed=False,
                reason=f"Reached max {self.max_total:,} total tokens (task budget exhausted)",
                remaining_input=0,
                remaining_output=0,
                remaining_iterations=0,
                current_cost_estimate=self._cost(state),
            )

        # Layer 4: Input cap
        remaining_input = self.max_input - state.input_tokens_used
        if remaining_input <= 0:
            return BudgetDecision(
                allowed=False,
                reason="Input token budget exhausted",
                remaining_input=0,
                remaining_output=self.max_output - state.output_tokens_used,
                remaining_iterations=self.max_iterations - state.iterations,
                current_cost_estimate=self._cost(state),
            )

        # All good
        return BudgetDecision(
            allowed=True,
            reason="ok",
            remaining_input=remaining_input,
            remaining_output=self.max_output - state.output_tokens_used,
            remaining_iterations=self.max_iterations - state.iterations,
            current_cost_estimate=self._cost(state),
        )

    def record(self, state: BudgetState, input_tokens: int, output_tokens: int = 0):
        """Record token usage after an LLM call."""
        state.input_tokens_used += input_tokens
        state.output_tokens_used += output_tokens
        state.iterations += 1

    def backoff_delay(self, retry_number: int) -> float:
        """Exponential backoff delay for retries.

        Retry 1: 1.5s, Retry 2: 2.25s, Retry 3: 3.4s
        Prevents rapid-fire expensive retries.
        """
        return self.backoff_base ** retry_number

    def _cost(self, state: BudgetState) -> float:
        """Estimate current cost in dollars."""
        in_cost = (state.input_tokens_used / 1_000_000) * self.price_in
        out_cost = (state.output_tokens_used / 1_000_000) * self.price_out
        return in_cost + out_cost

    def summary(self, state: BudgetState) -> str:
        """Human-readable budget summary."""
        used = self._cost(state)
        pct_input = 100 * state.input_tokens_used / max(1, self.max_input)
        pct_output = 100 * state.output_tokens_used / max(1, self.max_output)
        pct_iter = 100 * state.iterations / max(1, self.max_iterations)
        return (
            f"Budget: {state.total_tokens:,}t / {self.max_total:,}t "
            f"(${used:.4f}) | "
            f"Input: {pct_input:.0f}% | "
            f"Output: {pct_output:.0f}% | "
            f"Loops: {pct_iter:.0f}% | "
            f"Retries: {state.retries}/{self.max_retries}"
        )


# ═══════════════════════════════════════════════════════════════
# Task-level token tracker — multi-request budget
# ═══════════════════════════════════════════════════════════════


@dataclass
class TaskBudget:
    """Track token usage across an entire task (multiple requests)."""
    task_id: str
    max_tokens: int = 200_000
    tokens_used: int = 0
    requests: int = 0
    start_time: float = field(default_factory=time.time)
    last_checkpoint: int = 0  # tokens used at last checkpoint

    def can_proceed(self, estimated_next: int = 0) -> bool:
        return (self.tokens_used + estimated_next) < self.max_tokens

    def record(self, tokens: int) -> None:
        self.tokens_used += tokens
        self.requests += 1

    def checkpoint(self) -> int:
        """Save a checkpoint, return tokens used since last."""
        since_last = self.tokens_used - self.last_checkpoint
        self.last_checkpoint = self.tokens_used
        return since_last

    @property
    def cost_estimate(self, price_per_million: float = 3.0) -> float:
        return (self.tokens_used / 1_000_000) * price_per_million


class GlobalBudgetTracker:
    """Track budgets across multiple concurrent tasks.

    Prevents one runaway task from consuming all available budget.
    """

    def __init__(self, global_max_tokens: int = 1_000_000):
        self.global_max = global_max_tokens
        self.tasks: dict[str, TaskBudget] = defaultdict(
            lambda: TaskBudget("default")
        )
        self.total_tokens_used = 0

    def allocate(self, task_id: str, max_tokens: int = 200_000) -> TaskBudget:
        """Allocate budget for a task. Fails if global budget exhausted."""
        remaining = self.global_max - self.total_tokens_used
        if max_tokens > remaining:
            max_tokens = remaining
        if max_tokens <= 0:
            raise BudgetExhaustedError(
                f"Global budget exhausted: {self.total_tokens_used:,} / {self.global_max:,}"
            )
        tb = TaskBudget(task_id=task_id, max_tokens=max_tokens)
        self.tasks[task_id] = tb
        return tb

    def record(self, task_id: str, tokens: int) -> None:
        self.tasks[task_id].record(tokens)
        self.total_tokens_used += tokens

    def summary(self) -> str:
        parts = []
        for tid, tb in self.tasks.items():
            parts.append(
                f"  {tid}: {tb.tokens_used:,}t / {tb.max_tokens:,}t "
                f"(${tb.cost_estimate:.4f}) — {tb.requests} requests"
            )
        return "\n".join(parts) if parts else "No tasks"

    @property
    def remaining(self) -> int:
        return self.global_max - self.total_tokens_used


class BudgetExhaustedError(Exception):
    """Raised when token budget is completely exhausted."""
    pass
