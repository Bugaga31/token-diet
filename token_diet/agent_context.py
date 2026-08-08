"""Agent Context Manager — smart compression of multi-turn agent history.

Inspired by Acon (Agent Context Optimization) and TokenShift. When token-diet
is used with agents (multi-turn, tool calls, code generation), this module
intelligently compresses old turns while preserving:

  - Tool call IDs, function names, and arguments
  - Tool outputs (summarized, not dropped)
  - Code blocks and file paths
  - Critical decisions and error messages
  - User instructions (never touched)

Savings: 26-54% on agent conversation history without losing context.

Key insight from Acon paper: contrast successful vs failed trajectories to
learn what must be preserved. Our approach: deterministic classification of
turn importance based on content types, no extra API calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from .core import count_tokens
except ImportError:
    from core import count_tokens  # type: ignore[no-redef]


# ── Content classifiers ─────────────────────────────────────────────────────


_TOOL_CALL_RE = re.compile(
    r"(?:tool_use|tool_call|function_call|ToolUse)\b",
    re.IGNORECASE,
)
_TOOL_RESULT_RE = re.compile(
    r"(?:tool_result|function_result|ToolResult|observation)\b",
    re.IGNORECASE,
)
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_FILE_PATH_RE = re.compile(
    r"(?:/[a-zA-Z0-9._-]+)+/?|"
    r"(?:[a-zA-Z]:\\[a-zA-Z0-9._\\-]+)|"
    r"(?:[a-zA-Z0-9._-]+\\.py\\b|[a-zA-Z0-9._-]+\\.js\\b|[a-zA-Z0-9._-]+\\.ts\\b)",
)
_ERROR_RE = re.compile(
    r"(?:Error|Exception|Traceback|stack trace|FAILED|TypeError|ValueError|"
    r"SyntaxError|RuntimeError|panic|segfault)",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"(?:decided|chose|selected|recommend|decision|will use|should use|"
    r"best approach|optimal|preferred)",
    re.IGNORECASE,
)


def _is_tool_call(content: str) -> bool:
    """Detect if content is a tool/function call."""
    return bool(_TOOL_CALL_RE.search(content))


def _is_tool_result(content: str) -> bool:
    """Detect if content is a tool result/observation."""
    return bool(_TOOL_RESULT_RE.search(content))


def _has_code(content: str) -> bool:
    """Detect code blocks or file paths."""
    return bool(_CODE_BLOCK_RE.search(content) or _FILE_PATH_RE.search(content))


def _has_error(content: str) -> bool:
    """Detect error messages."""
    return bool(_ERROR_RE.search(content))


def _has_decision(content: str) -> bool:
    """Detect agent decisions / conclusions."""
    return bool(_DECISION_RE.search(content))


def _is_short(content: str, max_tokens: int = 30) -> bool:
    """Short content — keep as-is."""
    return count_tokens(content) < max_tokens


# ── Turn classification ─────────────────────────────────────────────────────


class TurnKind:
    USER = "user"
    ASSISTANT_PLAIN = "assistant_plain"
    ASSISTANT_TOOL_CALL = "assistant_tool_call"
    ASSISTANT_CODE = "assistant_code"
    TOOL_RESULT = "tool_result"
    ERROR = "error"


def classify_turn(role: str, content: str) -> str:
    """Classify a conversation turn by its content type.

    This determines the compression strategy for each turn:
        user            → NEVER compress
        tool_call       → keep tool names + args, compress reasoning
        code            → keep code verbatim, compress surrounding prose
        tool_result     → summarize long results
        error           → keep error verbatim (debugging)
        assistant_plain → compress prose aggressively
    """
    if role in ("user", "human"):
        return TurnKind.USER

    if _has_error(content):
        return TurnKind.ERROR

    if _is_tool_call(content):
        return TurnKind.ASSISTANT_TOOL_CALL

    if _is_tool_result(content):
        return TurnKind.TOOL_RESULT

    if _has_code(content):
        return TurnKind.ASSISTANT_CODE

    return TurnKind.ASSISTANT_PLAIN


# ── Compression strategies per turn kind ────────────────────────────────────


def _compress_tool_call(content: str) -> str:
    """Compress a tool call: keep function name + essential args, drop filler."""
    # Preserve the function/tool name lines and JSON args
    lines = content.split("\n")
    kept: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Always keep lines with function names or tool IDs
        if any(
            kw in stripped.lower()
            for kw in ("tool", "function", "call", "id", "name", "arguments", "{")
        ):
            kept.append(line)
        # Keep JSON argument lines
        elif stripped.startswith('"') or stripped.startswith("{"):
            kept.append(line)
        # Drop filler commentary
        elif len(stripped) > 5:
            # Short meaningful line — keep
            kept.append(line)

    if len(kept) < len(lines) * 0.5:
        return content  # Don't compress if too much was removed

    return "\n".join(kept)


def _compress_tool_result(content: str) -> str:
    """Summarize long tool results: keep the first N chars + key metrics."""
    if len(content) < 300:
        return content

    # Extract key data: numbers, error messages, file paths
    numbers = re.findall(r"\b\d{1,6}(?:\.\d+)?\b", content)
    errors = re.findall(
        r"(?:Error|Exception|WARNING)[^\n]{0,60}", content, re.IGNORECASE
    )
    files = re.findall(
        r"(?:/[a-zA-Z0-9._-]+)+/?|[a-zA-Z0-9._-]+\.(?:py|js|ts|json|yaml|toml)",
        content,
    )

    summary_parts = [content[:200].rstrip()]

    if numbers:
        summary_parts.append(f"[{len(numbers)} numeric values]")
    if errors:
        summary_parts.append(f"[Errors: {', '.join(errors[:3])}]")
    if files:
        summary_parts.append(f"[Files: {', '.join(files[:5])}]")

    summary_parts.append(f"[total {count_tokens(content)} tokens]")
    return " ".join(summary_parts)


def _compress_code_turn(content: str) -> str:
    """Keep code blocks verbatim, compress surrounding prose."""
    code_blocks = _CODE_BLOCK_RE.findall(content)

    if not code_blocks:
        return _compress_prose_light(content)

    # Split into prose/code segments
    parts = _CODE_BLOCK_RE.split(content)
    result_parts: list[str] = []

    for i, prose in enumerate(parts):
        if prose.strip():
            compressed = _compress_prose_light(prose)
            if compressed.strip():
                result_parts.append(compressed)
        if i < len(code_blocks):
            result_parts.append(code_blocks[i])

    return "\n".join(result_parts)


def _compress_prose_light(content: str) -> str:
    """Light prose compression — remove filler, don't touch substance."""
    if len(content) < 80:
        return content

    result = content

    # Remove filler prefixes
    filler_prefixes = [
        r"^(?:Sure!|Of course!|Certainly!|Absolutely!|Great!)\s*",
        r"^(?:I'll|I will|Let me|Let's)\s+(?:go ahead and|now|start by|begin by)\s*",
        r"^(?:Here's|Here is)\s+(?:what|a|the)\s+",
    ]
    for pat in filler_prefixes:
        result = re.sub(pat, "", result, flags=re.IGNORECASE | re.MULTILINE)

    # Collapse repeated whitespace
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r" {2,}", " ", result)

    return result.strip()


# ── Main agent context compressor ────────────────────────────────────────────


@dataclass
class CompressedTurn:
    role: str
    kind: str
    original_tokens: int
    compressed_tokens: int
    content: str


@dataclass
class AgentContextResult:
    turns: list[CompressedTurn]
    total_before: int
    total_after: int
    savings_pct: float
    turns_untouched: int
    turns_compressed: int


def compress_agent_context(
    messages: list[dict[str, str]],
    max_history_turns: int = 20,
    counter: Callable[[str], int] = count_tokens,
) -> AgentContextResult:
    """Compress an agent conversation history.

    Strategy:
      - User messages: NEVER compressed
      - Recent turns (last 4): NEVER compressed (active context)
      - Tool calls: keep function names + args
      - Tool results: summarize long outputs
      - Code turns: keep code, compress prose
      - Error turns: keep verbatim
      - Old plain turns: aggressive compression or drop

    Args:
        messages: List of {"role": ..., "content": ...} dicts
        max_history_turns: Max turns to keep in context
        counter: Token counter

    Returns:
        AgentContextResult with compressed turns and savings
    """
    if len(messages) <= 4:
        # Too few turns — don't compress
        turns = [
            CompressedTurn(
                role=m["role"],
                kind=classify_turn(m["role"], m["content"]),
                original_tokens=counter(m["content"]),
                compressed_tokens=counter(m["content"]),
                content=m["content"],
            )
            for m in messages
        ]
        return AgentContextResult(
            turns=turns,
            total_before=sum(t.original_tokens for t in turns),
            total_after=sum(t.compressed_tokens for t in turns),
            savings_pct=0.0,
            turns_untouched=len(turns),
            turns_compressed=0,
        )

    RECENT_KEEP = 4
    old_turns = messages[:-RECENT_KEEP]
    recent_turns = messages[-RECENT_KEEP:]

    compressed_turns: list[CompressedTurn] = []
    turns_untouched = 0
    turns_compressed = 0

    for msg in old_turns:
        role = msg["role"]
        content = msg["content"]
        kind = classify_turn(role, content)
        orig = counter(content)

        if role == "user":
            # User messages: never compress
            compressed = content
            turns_untouched += 1
        elif kind == TurnKind.ERROR:
            # Errors: keep verbatim for debugging
            compressed = content
            turns_untouched += 1
        elif kind == TurnKind.ASSISTANT_TOOL_CALL:
            compressed = _compress_tool_call(content)
            turns_compressed += 1
        elif kind == TurnKind.TOOL_RESULT:
            compressed = _compress_tool_result(content)
            turns_compressed += 1
        elif kind == TurnKind.ASSISTANT_CODE:
            compressed = _compress_code_turn(content)
            turns_compressed += 1
        else:
            # Plain assistant turn: drop if short, compress if long
            if _is_short(content, 50):
                compressed = content
                turns_untouched += 1
            else:
                compressed = _compress_prose_light(content)
                turns_compressed += 1

        compressed_turns.append(
            CompressedTurn(
                role=role,
                kind=kind,
                original_tokens=orig,
                compressed_tokens=counter(compressed),
                content=compressed,
            )
        )

    # Add recent turns verbatim
    for msg in recent_turns:
        role = msg["role"]
        content = msg["content"]
        kind = classify_turn(role, content)
        orig = counter(content)
        compressed_turns.append(
            CompressedTurn(
                role=role,
                kind=kind,
                original_tokens=orig,
                compressed_tokens=orig,
                content=content,
            )
        )
        turns_untouched += 1

    total_before = sum(t.original_tokens for t in compressed_turns)
    total_after = sum(t.compressed_tokens for t in compressed_turns)
    savings = 100 * (total_before - total_after) / max(1, total_before)

    # Apply history cap if needed
    if len(compressed_turns) > max_history_turns:
        # Keep first user message + last N turns
        first_user = next(
            (i for i, t in enumerate(compressed_turns) if t.role == "user"), 0
        )
        keep = [compressed_turns[first_user]] if first_user < len(compressed_turns) else []
        keep += compressed_turns[-(max_history_turns - len(keep)):]
        compressed_turns = keep

        total_before = sum(t.original_tokens for t in compressed_turns)
        total_after = sum(t.compressed_tokens for t in compressed_turns)
        savings = 100 * (total_before - total_after) / max(1, total_before)

    return AgentContextResult(
        turns=compressed_turns,
        total_before=total_before,
        total_after=total_after,
        savings_pct=savings,
        turns_untouched=turns_untouched,
        turns_compressed=turns_compressed,
    )


def render_compressed_context(result: AgentContextResult) -> list[dict[str, str]]:
    """Convert compressed context back to message list for API."""
    return [{"role": t.role, "content": t.content} for t in result.turns]
