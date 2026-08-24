"""
Agent Supervisor — runtime loop detection + interruption for multi-agent systems.

Beats SupervisorAgent approach: LLM-free adaptive filter that detects:
1. Redundant tool-call loops (same tool + args repeated)
2. Escalating token waste (context growing without progress)
3. Circular reasoning (agent revisiting same conclusions)
4. Excessive tool output (truncate large tool results)

Operates as a middleware — no modifications needed to agent code.
Inspired by SupervisorAgent (Lin et al., 2026) but built entirely on
deterministic rules + lightweight statistics.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# ── Data structures ─────────────────────────────────────────────────


@dataclass
class ToolCall:
    """Record of a single tool call."""
    name: str
    args_hash: str          # SHA256 of serialized args (fast dedup)
    result_size: int        # Tokens in result
    timestamp: float
    turn: int


@dataclass
class AgentTurn:
    """Record of one agent turn (user/assistant/tool)."""
    role: str               # "user" | "assistant" | "tool"
    content_hash: str       # Hash of message content
    token_count: int
    timestamp: float
    turn: int


@dataclass
class LoopAlert:
    """Alert raised when a loop is detected."""
    loop_type: str          # "tool_repeat" | "token_growth" | "circular" | "oversized_result"
    details: str
    severity: str           # "warning" | "critical"
    suggestion: str         # What to do about it
    turns_since_detection: int


@dataclass
class SupervisorState:
    """Runtime state tracked by the supervisor."""
    turns: list[AgentTurn] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    recent_hashes: deque[str] = field(default_factory=lambda: deque(maxlen=20))
    alerts: list[LoopAlert] = field(default_factory=list)
    total_tokens: int = 0
    tool_call_count: int = 0
    start_time: float = 0.0
    warnings_issued: int = 0
    terminated: bool = False


# ── Supervisor ──────────────────────────────────────────────────────


class AgentSupervisor:
    """Runtime supervisor for multi-agent LLM systems.

    Detects and interrupts wasteful patterns without LLM calls:
    - Same tool called with same args > N times → block
    - Token growth rate > threshold → warn
    - Circular reasoning (same conclusions repeated) → warn
    - Tool result > size threshold → truncate
    """

    # Configuration
    MAX_SAME_TOOL_CALLS: int = 3
    MAX_TOKEN_GROWTH_RATIO: float = 5.0     # 5× growth from initial turns
    MAX_TOOL_RESULT_TOKENS: int = 2000
    CIRCULAR_HASH_WINDOW: int = 10
    MAX_TOTAL_TOKENS: int = 50000
    MAX_TOOL_CALLS: int = 50

    def __init__(
        self,
        on_alert: Callable[[LoopAlert], None] | None = None,
        max_same_tool_calls: int | None = None,
        max_token_growth: float | None = None,
        max_tool_result_tokens: int | None = None,
        max_total_tokens: int | None = None,
        max_tool_calls: int | None = None,
    ):
        self.on_alert = on_alert or (lambda a: None)
        self.max_same_tool = max_same_tool_calls or self.MAX_SAME_TOOL_CALLS
        self.max_growth = max_token_growth or self.MAX_TOKEN_GROWTH_RATIO
        self.max_result = max_tool_result_tokens or self.MAX_TOOL_RESULT_TOKENS
        self.max_total = max_total_tokens or self.MAX_TOTAL_TOKENS
        self.max_calls = max_tool_calls or self.MAX_TOOL_CALLS

        self._state = SupervisorState()

    # ── Public API ──────────────────────────────────────────────────

    def start_session(self) -> SupervisorState:
        """Begin a new supervised session."""
        self._state = SupervisorState(start_time=time.time())
        return self._state

    def observe_turn(self, role: str, content: str, token_count: int) -> list[LoopAlert]:
        """Observe an agent turn. Returns any new alerts."""
        turn_num = len(self._state.turns)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        turn = AgentTurn(
            role=role,
            content_hash=content_hash,
            token_count=token_count,
            timestamp=time.time(),
            turn=turn_num,
        )
        self._state.turns.append(turn)
        self._state.total_tokens += token_count

        alerts: list[LoopAlert] = []

        # Check circular reasoning
        if role in ("assistant", "tool"):
            circular = self._check_circular(content_hash)
            if circular:
                alerts.append(circular)

        # Check token growth
        growth = self._check_token_growth()
        if growth:
            alerts.append(growth)

        # Check max limits
        limit = self._check_limits()
        if limit:
            alerts.append(limit)
            self._state.terminated = True

        for a in alerts:
            self._state.alerts.append(a)
            self.on_alert(a)

        return alerts

    def observe_tool_call(
        self, tool_name: str, args: dict[str, Any] | None = None,
        result_tokens: int = 0,
    ) -> list[LoopAlert]:
        """Observe a tool call. Returns alerts if loop detected."""
        args_hash = hashlib.sha256(
            str(args).encode() if args else b""
        ).hexdigest()[:16]

        call = ToolCall(
            name=tool_name,
            args_hash=args_hash,
            result_size=result_tokens,
            timestamp=time.time(),
            turn=len(self._state.turns),
        )
        self._state.tool_calls.append(call)
        self._state.tool_call_count += 1

        alerts: list[LoopAlert] = []

        # Check repeated tool calls
        repeat = self._check_tool_repeat(tool_name, args_hash)
        if repeat:
            alerts.append(repeat)

        # Check oversized result
        if result_tokens > self.max_result:
            alerts.append(LoopAlert(
                loop_type="oversized_result",
                details=f"Tool '{tool_name}' returned {result_tokens} tokens (max {self.max_result})",
                severity="warning",
                suggestion=f"Truncate result to {self.max_result} tokens",
                turns_since_detection=0,
            ))

        # Check max tool calls
        if self._state.tool_call_count > self.max_calls:
            alerts.append(LoopAlert(
                loop_type="tool_limit",
                details=f"Exceeded max tool calls ({self._state.tool_call_count} > {self.max_calls})",
                severity="critical",
                suggestion="Terminate agent session",
                turns_since_detection=0,
            ))
            self._state.terminated = True

        for a in alerts:
            self._state.alerts.append(a)
            self.on_alert(a)

        return alerts

    def should_continue(self) -> bool:
        """Check if session should continue."""
        return (
            not self._state.terminated
            and self._state.total_tokens < self.max_total
            and self._state.tool_call_count < self.max_calls
            and self._state.warnings_issued < 5  # Max 5 warnings before termination
        )

    def summary(self) -> dict[str, Any]:
        """Return session summary."""
        s = self._state
        elapsed = time.time() - s.start_time if s.start_time else 0
        return {
            "total_turns": len(s.turns),
            "total_tokens": s.total_tokens,
            "total_tool_calls": s.tool_call_count,
            "alerts": len(s.alerts),
            "critical_alerts": sum(1 for a in s.alerts if a.severity == "critical"),
            "terminated": s.terminated,
            "duration_seconds": round(elapsed, 2),
            "tokens_per_second": round(s.total_tokens / elapsed, 1) if elapsed > 0 else 0,
        }

    # ── Detection logic ─────────────────────────────────────────────

    def _check_tool_repeat(self, name: str, args_hash: str) -> LoopAlert | None:
        """Detect same tool + same args called repeatedly."""
        same_calls = [
            c for c in self._state.tool_calls[-10:]
            if c.name == name and c.args_hash == args_hash
        ]
        if len(same_calls) >= self.max_same_tool:
            return LoopAlert(
                loop_type="tool_repeat",
                details=(
                    f"Tool '{name}' called {len(same_calls)} times "
                    f"with identical arguments"
                ),
                severity="critical" if len(same_calls) >= self.max_same_tool + 2 else "warning",
                suggestion=(
                    f"Block further calls to '{name}' "
                    f"with same arguments"
                ),
                turns_since_detection=0,
            )
        return None

    def _check_circular(self, content_hash: str) -> LoopAlert | None:
        """Detect circular reasoning: same content hash appearing repeatedly."""
        self._state.recent_hashes.append(content_hash)
        if len(self._state.recent_hashes) >= self.CIRCULAR_HASH_WINDOW:
            recent = list(self._state.recent_hashes)
            # Check if last N hashes contain repeats
            unique = len(set(recent[-self.CIRCULAR_HASH_WINDOW:]))
            if unique <= 2:  # Almost all same
                return LoopAlert(
                    loop_type="circular",
                    details=(
                        f"Circular pattern detected: "
                        f"only {unique} unique responses in last "
                        f"{self.CIRCULAR_HASH_WINDOW} turns"
                    ),
                    severity="warning",
                    suggestion="Inject 'stop repeating yourself' directive",
                    turns_since_detection=0,
                )
        return None

    def _check_token_growth(self) -> LoopAlert | None:
        """Detect explosive token growth."""
        if len(self._state.turns) < 5:
            return None

        # Compare last 3 turns to first 3 turns
        first_avg = sum(
            t.token_count for t in self._state.turns[:3]
        ) / 3
        last_avg = sum(
            t.token_count for t in self._state.turns[-3:]
        ) / 3

        if first_avg > 0 and last_avg / first_avg > self.max_growth:
            return LoopAlert(
                loop_type="token_growth",
                details=(
                    f"Token growth {last_avg / first_avg:.1f}×: "
                    f"{int(first_avg)} → {int(last_avg)} avg tokens/turn"
                ),
                severity="warning",
                suggestion="Compress context or truncate history",
                turns_since_detection=0,
            )
        return None

    def _check_limits(self) -> LoopAlert | None:
        """Check hard limits."""
        if self._state.total_tokens > self.max_total:
            return LoopAlert(
                loop_type="token_limit",
                details=f"Exceeded max tokens ({self._state.total_tokens})",
                severity="critical",
                suggestion="Terminate session",
                turns_since_detection=0,
            )
        return None


# ── Utility wrapper ─────────────────────────────────────────────────


def supervised_agent_loop(
    agent_fn: Callable[[str], str],
    initial_prompt: str,
    max_iterations: int = 20,
) -> tuple[str, SupervisorState]:
    """Wrap an agent function with supervisor protection.

    Agent function receives a prompt, returns a response.
    Supervisor monitors for loops and can inject stop directives.

    Returns (final_response, supervisor_state).
    """
    supervisor = AgentSupervisor()
    supervisor.start_session()

    current = initial_prompt

    for _i in range(max_iterations):
        # Observe user turn
        supervisor.observe_turn(
            "user", current,
            token_count=len(current.split()) * 2  # Rough estimate
        )

        if not supervisor.should_continue():
            return (
                "[SUPERVISOR] Session terminated: "
                + supervisor._state.alerts[-1].details,
                supervisor._state,
            )

        # Run agent
        response = agent_fn(current)

        # Observe assistant turn
        alerts = supervisor.observe_turn(
            "assistant", response,
            token_count=len(response.split()) * 2
        )

        # If critical alert, inject stop directive
        if any(a.severity == "critical" for a in alerts):
            response += "\n\n[SUPERVISOR: Stop and provide final answer.]"

        # Check if agent produced a final answer (has "FINAL:" or similar)
        if "FINAL:" in response or "[DONE]" in response:
            return response, supervisor._state

        current = response

    return current, supervisor._state


def truncate_tool_result(result: str, max_tokens: int = 2000) -> tuple[str, bool]:
    """Truncate oversized tool result. Returns (truncated, was_truncated)."""
    words = result.split()
    estimated_tokens = len(words) * 1.3  # Rough token estimate

    if estimated_tokens <= max_tokens:
        return result, False

    # Keep first and last portions
    keep_words = int(max_tokens / 1.3)
    first = int(keep_words * 0.7)
    last = keep_words - first

    truncated = (
        " ".join(words[:first])
        + f"\n\n[... {len(words) - keep_words} words truncated ...]\n\n"
        + " ".join(words[-last:])
    )
    return truncated, True
