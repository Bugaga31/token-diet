"""Dynamic Compression Ratio — adaptive compression strength per content type.

Rather than applying a fixed 46.6% compression everywhere, this module
adapts the compression strength based on what kind of content is in each
part of the prompt:

  Content Type          Compression Ratio    Strategy
  ────────────────────────────────────────────────────
  User questions         0% (verbatim)       NEVER touch
  Code blocks            0% (verbatim)       NEVER touch  
  Tool schemas           0-10% (gentle)      Strip descriptions only
  Numbers / IDs          0% (verbatim)       NEVER touch
  Error messages         0% (verbatim)       Debugging critical
  Retrieval chunks       40-60%              Aggressive dedup + trim
  System instructions    10-20%              Remove redundancies
  Agent history          20-40%              Classify per turn
  Prose / descriptions   50-70%              Aggressive removal
  Conversation filler    80-90%              Near-complete removal

Inspired by DiffuMask (parallel pruning per content type) and Acon
(agent-aware dynamic scaling).

Key principle: adaptive > aggressive. Better to leave money on the table
in one section than lose critical info in another.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

try:
    from .core import count_tokens
    from .loss_router import (
        _classify_segment,  # noqa: F401
        compress_prose_aggressive,
        compress_with_routing,
        reduce_output,  # noqa: F401
    )
except ImportError:
    from core import count_tokens  # type: ignore[no-redef]
    from loss_router import (  # type: ignore[no-redef]
        compress_prose_aggressive,
        compress_with_routing,
    )


@dataclass
class ContentSegment:
    """A classified segment of content with its compression target."""

    text: str
    content_type: str  # "user", "system", "code", "tool", "error", "prose", "retrieval"
    compression_ratio: float  # 0.0 = verbatim, 1.0 = full compression
    tokens_before: int = 0
    tokens_after: int = 0


@dataclass
class DynamicRatioResult:
    segments: list[ContentSegment]
    total_before: int
    total_after: int
    savings_pct: float
    content_distribution: dict[str, int]  # type -> token count


# ── Content type detection ──────────────────────────────────────────────────


_USER_QUESTION_MARKERS = re.compile(
    r"^(?:User:|Human:|Question:|Q:|Задача:|Вопрос:|\?\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_CODE_MARKERS = re.compile(r"```[\s\S]*?```|`[^`]+`")
_TOOL_SCHEMA_MARKERS = re.compile(
    r"(?:tools|functions|tool_schema|function_schema|parameters|properties)",
    re.IGNORECASE,
)
_ERROR_MARKERS = re.compile(
    r"(?:Traceback|Error|Exception|FAILED|panic|stack trace)",
    re.IGNORECASE,
)
_RETRIEVAL_MARKERS = re.compile(
    r"(?:retrieved|context|document|chunk|search result|source:)",
    re.IGNORECASE,
)
_SYSTEM_MARKERS = re.compile(
    r"(?:system:|SYSTEM:|instructions|you are|behave as|role:|act as)",
    re.IGNORECASE,
)


def _detect_content_type(text: str, role: str = "") -> str:
    """Detect the content type of a text segment.

    Priority order (first match wins):
      1. user — if from user role or question markers
      2. code — code blocks
      3. tool — tool schemas, function definitions
      4. error — error messages, tracebacks
      5. retrieval — retrieved documents, search results
      6. system — system instructions
      7. prose — everything else
    """
    if role in ("user", "human") or _USER_QUESTION_MARKERS.search(text[:200]):
        return "user"

    if _CODE_MARKERS.search(text):
        return "code"

    if _TOOL_SCHEMA_MARKERS.search(text[:300]):
        return "tool"

    if _ERROR_MARKERS.search(text):
        return "error"

    if _RETRIEVAL_MARKERS.search(text[:300]):
        return "retrieval"

    if _SYSTEM_MARKERS.search(text[:300]):
        return "system"

    return "prose"


# ── Compression ratio table ─────────────────────────────────────────────────

# Default ratios: fraction of tokens to KEEP (0.0 = verbatim, 1.0 = full compress)
DEFAULT_RATIOS: dict[str, float] = {
    "user": 0.0,        # Never compress user questions
    "code": 0.0,        # Never compress code
    "tool": 0.10,       # Gentle: strip descriptions only
    "error": 0.0,       # Never compress errors
    "retrieval": 0.45,  # Aggressive dedup + trim
    "system": 0.15,     # Remove redundancies
    "prose": 0.55,      # Aggressive removal
}


# ── Per-type compression strategies ─────────────────────────────────────────


def _compress_system(text: str, ratio: float) -> str:
    """Compress system instructions: remove redundancy, keep rules."""
    if ratio <= 0:
        return text

    lines = text.split("\n")
    seen: set[str] = set()
    kept: list[str] = []

    for line in lines:
        normalized = line.strip().lower()
        # Keep unique lines only
        if normalized and normalized not in seen:
            seen.add(normalized)
            kept.append(line)
        elif not normalized:
            kept.append(line)

    result = "\n".join(kept)
    # Remove filler from system prompts
    result = re.sub(
        r"\b(?:please|kindly|feel free|don't hesitate)\b[^.]*\.\s*",
        "",
        result,
        flags=re.IGNORECASE,
    )

    if count_tokens(result) >= count_tokens(text) * 0.95:
        return text
    return result


def _compress_retrieval(text: str, ratio: float) -> str:
    """Compress retrieval chunks: dedup + truncate long passages."""
    if ratio <= 0:
        return text

    # Split into chunks
    chunks = re.split(r"\n{2,}|\n(?=Source:|Document:|Chunk)", text)
    compressed_chunks: list[str] = []
    seen_content: set[str] = set()

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        # Dedup by content hash (first 100 chars)
        content_hash = chunk[:100].lower()
        if content_hash in seen_content:
            continue
        seen_content.add(content_hash)

        # Truncate long chunks
        if len(chunk) > 500:
            chunk = chunk[:500].rstrip() + "..."

        compressed_chunks.append(chunk)

    result = "\n\n".join(compressed_chunks)

    if count_tokens(result) >= count_tokens(text) * 0.90:
        return text
    return result


# ── Main dynamic ratio compressor ───────────────────────────────────────────


def classify_and_compress(
    text: str,
    role: str = "",
    ratios: dict[str, float] | None = None,
    counter: Callable[[str], int] = count_tokens,
) -> tuple[str, int, int, str]:
    """Classify content and apply appropriate compression ratio.

    Args:
        text: Content to compress
        role: Message role (user/assistant/system/tool)
        ratios: Override compression ratios per content type
        counter: Token counter

    Returns:
        (compressed_text, tokens_before, tokens_after, content_type)
    """
    if not text or len(text) < 50:
        return text, counter(text), counter(text), "short"

    content_type = _detect_content_type(text, role)
    r = (ratios or DEFAULT_RATIOS).get(content_type, 0.5)
    before = counter(text)

    if r <= 0 or content_type in ("user", "code", "error"):
        return text, before, before, content_type

    # Apply type-specific compression
    if content_type == "system":
        result = _compress_system(text, r)
    elif content_type == "retrieval":
        result = _compress_retrieval(text, r)
    elif content_type == "tool":
        # Gentle: only try routing compression
        result, _, _ = compress_with_routing(text, aggressive=False, counter=counter)
        if counter(result) >= before * 0.95:
            result = text
    else:
        # Prose: aggressive compression
        result = compress_prose_aggressive(text, keep_ratio=1.0 - r, counter=counter)

    after = counter(result)

    if after >= before * 0.95:
        return text, before, before, content_type

    return result, before, after, content_type


def dynamic_pipeline(
    sections: dict[str, str],
    ratios: dict[str, float] | None = None,
    counter: Callable[[str], int] = count_tokens,
) -> DynamicRatioResult:
    """Run dynamic compression on all prompt sections.

    Args:
        sections: Dict of section_name -> content_text
        ratios: Override compression ratios
        counter: Token counter

    Returns:
        DynamicRatioResult with per-segment stats
    """
    segments: list[ContentSegment] = []
    distribution: dict[str, int] = {}
    total_before = 0
    total_after = 0

    for name, text in sections.items():
        # Determine role from section name
        role = ""
        if name in ("question", "user"):
            role = "user"
        elif name == "system_prompt":
            role = "system"
        elif name == "tools":
            role = "tool"
        elif name == "retrieved_context":
            role = "retrieval"

        compressed, before, after, ctype = classify_and_compress(
            text, role=role, ratios=ratios, counter=counter
        )

        seg = ContentSegment(
            text=compressed,
            content_type=ctype,
            compression_ratio=(before - after) / max(1, before),
            tokens_before=before,
            tokens_after=after,
        )
        segments.append(seg)
        distribution[ctype] = distribution.get(ctype, 0) + before
        total_before += before
        total_after += after

    savings = 100 * (total_before - total_after) / max(1, total_before)

    return DynamicRatioResult(
        segments=segments,
        total_before=total_before,
        total_after=total_after,
        savings_pct=savings,
        content_distribution=distribution,
    )
