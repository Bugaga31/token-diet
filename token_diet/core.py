"""Token Diet core: same information, fewer billed tokens.

Features:
- exact token metering (tiktoken when available)
- stable prompt prefixes for provider caching
- lossless StructPack for repeated JSON rows
- duplicate retrieval removal
- blob handles for large tool results
- exact + optional semantic cache
- bounded conversation history
- per-request budget guard
- deterministic field projection
- optional LLMLingua compression hook

Optional dependencies:
    pip install tiktoken
    pip install llmlingua
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any


class _Missing:
    """Sentinel for absent record fields.

    A dedicated type keeps `None` (a real JSON value) distinguishable from
    "this record had no such key", which StructPack must round-trip exactly.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "_MISSING"


_MISSING = _Missing()


# ---------------------------------------------------------------------------
# 1. Token counting and cost metering
# ---------------------------------------------------------------------------

_ENCODING_CACHE: dict[str, Any] = {}
_TOKEN_SPLIT_RE = re.compile(
    r"'s|'t|'re|'ve|'m|'ll|'d| ?[^\W\d_]+| ?\d+| ?[^\s\w]+|\s+"
)


def count_tokens(text: str, model: str | None = None) -> int:
    """Count tokens with tiktoken when installed, else approximate.

    The encoder is cached per model: building a tiktoken encoding costs far
    more than the encode call itself, and this function runs on every prompt
    section, so rebuilding it per call would dominate the cost of metering.
    """
    if not text:
        return 0

    key = model or "gpt-4o-mini"
    encoding = _ENCODING_CACHE.get(key)

    if encoding is None and key not in _ENCODING_CACHE:
        try:
            import tiktoken

            try:
                encoding = tiktoken.encoding_for_model(key)
            except KeyError:
                encoding = tiktoken.get_encoding("o200k_base")
        except Exception:
            encoding = None

        _ENCODING_CACHE[key] = encoding

    if encoding is not None:
        return len(encoding.encode(text))

    # Conservative fallback. Providers split text differently, so treat this
    # as an estimate for budgeting only, never as a billing figure.
    total = 0
    for piece in _TOKEN_SPLIT_RE.findall(text):
        value = piece.strip()

        if not value:
            total += max(1, len(piece) // 4)
        elif value.isalnum():
            total += max(1, math.ceil(len(value) / 4))
        else:
            total += len(value)

    return total


@dataclass
class Usage:
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass
class PriceTable:
    input_per_million: float
    cache_write_per_million: float
    cache_read_per_million: float
    output_per_million: float

    def calculate(self, usage: Usage) -> float:
        return (
            usage.input_tokens * self.input_per_million
            + usage.cache_write_tokens * self.cache_write_per_million
            + usage.cache_read_tokens * self.cache_read_per_million
            + usage.output_tokens * self.output_per_million
        ) / 1_000_000


class TokenMeter:
    """Measure cost per user-visible task, not per API call.

    Retries, tool calls, planner calls and judge calls share one task_id.
    Otherwise a dashboard reports per-call cost and understates real spend.
    """

    def __init__(self, prices: PriceTable):
        self.prices = prices
        self.tasks: dict[str, Usage] = {}
        self.sections: dict[str, int] = {}

    def record(self, task_id: str, response: Any) -> Usage:
        usage = getattr(response, "usage", None) or {}

        def get(name: str) -> int:
            if isinstance(usage, dict):
                return int(usage.get(name, 0) or 0)
            return int(getattr(usage, name, 0) or 0)

        current = Usage(
            input_tokens=get("input_tokens"),
            cache_write_tokens=get("cache_creation_input_tokens"),
            cache_read_tokens=get("cache_read_input_tokens"),
            output_tokens=get("output_tokens"),
        )

        self.tasks[task_id] = self.tasks.get(task_id, Usage()) + current
        return current

    def record_sections(
        self,
        sections: dict[str, str],
        counter: Callable[[str], int] = count_tokens,
    ) -> None:
        for name, text in sections.items():
            self.sections[name] = self.sections.get(name, 0) + counter(text)

    def total(self) -> Usage:
        result = Usage()
        for usage in self.tasks.values():
            result += usage
        return result

    def report(self) -> str:
        total = self.total()
        cost = self.prices.calculate(total)
        cached = total.cache_read_tokens
        all_input = total.input_tokens + total.cache_write_tokens + total.cache_read_tokens

        lines = [
            f"tasks: {len(self.tasks)}",
            f"total cost: ${cost:.4f}",
            f"cost/task: ${cost / max(1, len(self.tasks)):.4f}",
            f"input/output: {all_input}/{total.output_tokens}",
            f"cache hit: {100 * cached / max(1, all_input):.1f}%",
        ]

        if self.sections:
            largest = sorted(self.sections.items(), key=lambda item: item[1], reverse=True)
            lines.append(
                "input by section: "
                + ", ".join(f"{name}={tokens}" for name, tokens in largest)
            )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. Cache-stable prompts
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Stable JSON so random key order cannot destroy cache hits."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class PromptBuilder:
    """Order prompt sections static -> semi_static -> volatile.

    A timestamp, UUID or epoch near the top invalidates the whole provider
    cache prefix, so `add_static` rejects obviously volatile values.
    """

    static: list[str] = field(default_factory=list)
    semi_static: list[str] = field(default_factory=list)
    volatile: list[str] = field(default_factory=list)

    volatile_patterns = (
        (re.compile(r"\d{4}-\d{2}-\d{2}"), "date"),
        (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}"), "uuid"),
        (re.compile(r"\b\d{10,13}\b"), "epoch"),
    )

    def add_static(self, text: str) -> PromptBuilder:
        for pattern, label in self.volatile_patterns:
            if pattern.search(text):
                raise ValueError(
                    f"static prompt contains {label}; move it to semi_static or volatile"
                )

        self.static.append(text)
        return self

    def add_semi_static(self, text: str) -> PromptBuilder:
        self.semi_static.append(text)
        return self

    def add_volatile(self, text: str) -> PromptBuilder:
        self.volatile.append(text)
        return self

    def system_blocks(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []

        if self.static:
            blocks.append(
                {
                    "type": "text",
                    "text": "\n\n".join(self.static),
                    "cache_control": {"type": "ephemeral"},
                }
            )

        if self.semi_static:
            blocks.append({"type": "text", "text": "\n\n".join(self.semi_static)})

        return blocks

    def user_text(self) -> str:
        return "\n\n".join(self.volatile)


# ---------------------------------------------------------------------------
# 3. Lossless StructPack for repeated JSON records
# ---------------------------------------------------------------------------

_PACK_SEPARATOR = "|"
_PACK_NEWLINE = "\n"
_PACK_MISSING = "~M"
_PACK_NULL = "~0"
_UNSAFE_COLUMN_RE = re.compile(r"[|,:~\s]")


def _escape(value: str) -> str:
    return value.replace("~", "~~").replace("|", "~p").replace("\n", "~n")


def _unescape(value: str) -> str:
    result: list[str] = []
    index = 0
    replacements = {"~": "~", "p": "|", "n": "\n"}

    while index < len(value):
        if value[index] == "~" and index + 1 < len(value):
            result.append(replacements.get(value[index + 1], value[index + 1]))
            index += 2
        else:
            result.append(value[index])
            index += 1

    return "".join(result)


def _split_packed_row(row: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    index = 0

    while index < len(row):
        char = row[index]

        if char == "~" and index + 1 < len(row):
            current.append(row[index : index + 2])
            index += 2
        elif char == "|":
            cells.append("".join(current))
            current = []
            index += 1
        else:
            current.append(char)
            index += 1

    cells.append("".join(current))
    return cells


def _column_type(values: list[Any]) -> str:
    present = {type(v) for v in values if v is not None and v is not _MISSING}

    if not present:
        return "s"
    if present == {bool}:
        return "b"
    if present == {int}:
        return "i"
    if present <= {int, float}:
        return "f"
    if present == {str}:
        return "s"
    return "j"


def _dictionary_savings(value_length: int, uses: int, reference_length: int) -> int:
    """Characters saved by replacing `uses` copies of a value with a reference.

    Cost model: each use shrinks from the value to a reference, and one
    legend line ``#d @n=value\\n`` is added once. A fixed minimum-length floor
    cannot express this, because a 4-character ticker repeated 200 times pays
    off while a 12-character value used 3 times does not.
    """
    legend_line = len("#d ") + reference_length + len("=") + value_length + len("\n")
    return uses * (value_length - reference_length) - legend_line


def _packed_size(
    grid: list[list[Any]],
    types: list[str],
    legend: dict[str, str],
    columns: list[str],
) -> int:
    """Estimate the output size (chars) of a packed block for a given legend."""
    header_len = len("#p1 n=") + len(str(len(grid))) + len(" c=") + sum(
        len(c) + len(":") + 1 for c in columns
    )
    legend_len = sum(
        len("#d ") + len(ref) + len("=") + len(_escape(val)) + 1
        for val, ref in legend.items()
    )
    body_len = 0
    for row in grid:
        for value, _column_type in zip(row, types, strict=True):
            if value is _MISSING:
                body_len += len(_PACK_MISSING)
            elif value is None:
                body_len += len(_PACK_NULL)
            elif isinstance(value, str) and value in legend:
                body_len += len(legend[value])
            else:
                body_len += len(
                    _escape(json.dumps(value, ensure_ascii=False))
                    if not isinstance(value, str)
                    else _escape(value)
                )
            body_len += len(_PACK_SEPARATOR)
    return header_len + legend_len + body_len


def pack_records(
    records: list[dict[str, Any]],
    minimum_repeated_length: int = 2,
    minimum_repeated_uses: int = 2,
) -> str | None:
    """Pack repeated JSON records losslessly, or return None when unsafe."""
    if len(records) < 3:
        return None
    if not all(isinstance(record, dict) for record in records):
        return None

    columns: list[str] = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)

    if not columns or len(columns) > 60:
        return None

    # Keep the header unambiguous.
    if any(
        not isinstance(column, str) or _UNSAFE_COLUMN_RE.search(column) for column in columns
    ):
        return None

    density = sum(len(record) for record in records) / (len(records) * len(columns))
    if density < 0.45:
        return None

    grid = [[record.get(column, _MISSING) for column in columns] for record in records]
    types = [_column_type([row[i] for row in grid]) for i in range(len(columns))]

    frequencies: dict[str, int] = {}
    for row in grid:
        for value in row:
            if isinstance(value, str) and len(value) >= minimum_repeated_length:
                frequencies[value] = frequencies.get(value, 0) + 1

    candidates = sorted(
        ((v, uses) for v, uses in frequencies.items() if uses >= minimum_repeated_uses),
        key=lambda item: -item[1] * len(item[0]),
    )

    # Assign references greedily by payoff, keeping only values that pay for
    # their legend line at the reference width they receive.
    aggressive_legend: dict[str, str] = {}
    for value, uses in candidates:
        reference = f"@{len(aggressive_legend) + 1}"
        if _dictionary_savings(len(_escape(value)), uses, len(reference)) > 0:
            aggressive_legend[value] = reference

    conservative_legend = {
        value: reference
        for value, reference in aggressive_legend.items()
        if len(value) >= 8
    }

    # Character savings are not token savings: a reference like "@12" is dense
    # punctuation that many tokenizers split per character, so an aggressive
    # dictionary can cost MORE tokens than the plain values it replaced. Build
    # both variants and keep whichever has fewer character bytes, since that is
    # a tight proxy for token count without a live tokenizer.
    aggressive_len = _packed_size(grid, types, aggressive_legend, columns)
    conservative_len = _packed_size(grid, types, conservative_legend, columns)
    if aggressive_len <= conservative_len:
        legend = dict(aggressive_legend)
    else:
        legend = dict(conservative_legend)

    def encode_cell(value: Any, column_type: str) -> str:
        if value is _MISSING:
            return _PACK_MISSING
        if value is None:
            return _PACK_NULL
        if column_type == "j":
            return _escape(json.dumps(value, ensure_ascii=False))
        if column_type == "b":
            return "1" if value else "0"
        if isinstance(value, str):
            if value in legend:
                return legend[value]
            # Escape strings that could be mistaken for legend references.
            if value.startswith("@"):
                return _escape("@" + value)
            return _escape(value)
        return _escape(json.dumps(value, ensure_ascii=False))

    header = f"#p1 n={len(records)} c=" + ",".join(
        f"{column}:{column_type}" for column, column_type in zip(columns, types, strict=True)
    )
    lines = [header]

    for value, reference in legend.items():
        lines.append(f"#d {reference}={_escape(value)}")

    for row in grid:
        line = _PACK_SEPARATOR.join(
            encode_cell(value, column_type)
            for value, column_type in zip(row, types, strict=True)
        )
        # Data must not masquerade as a metadata line.
        if line.startswith("#"):
            line = "~h" + line
        lines.append(line)

    return _PACK_NEWLINE.join(lines)


def unpack_records(packed: str) -> list[dict[str, Any]]:
    lines = packed.split(_PACK_NEWLINE)

    if not lines or not lines[0].startswith("#p1 "):
        raise ValueError("invalid StructPack header")

    specification = lines[0].split(" c=", 1)[1]
    columns: list[str] = []
    types: list[str] = []

    for part in specification.split(","):
        name, column_type = part.rsplit(":", 1)
        columns.append(name)
        types.append(column_type)

    legend: dict[str, str] = {}
    body: list[str] = []

    for line in lines[1:]:
        if line.startswith("~h"):
            body.append(line[2:])
        elif line.startswith("#d "):
            reference, value = line[3:].split("=", 1)
            legend[reference] = _unescape(value)
        else:
            body.append(line)

    result: list[dict[str, Any]] = []

    for line in body:
        record: dict[str, Any] = {}
        cells = _split_packed_row(line)

        if len(cells) != len(columns):
            raise ValueError("row width does not match StructPack header")

        for name, column_type, raw in zip(columns, types, cells, strict=True):
            if raw == _PACK_MISSING:
                continue
            if raw == _PACK_NULL:
                record[name] = None
                continue
            if raw in legend:
                record[name] = legend[raw]
                continue
            if column_type == "j":
                record[name] = json.loads(_unescape(raw))
                continue
            if column_type == "b":
                record[name] = raw == "1"
                continue
            if column_type in {"i", "f"}:
                record[name] = json.loads(_unescape(raw))
                continue

            text = _unescape(raw)
            if text.startswith("@@"):
                text = text[1:]
            record[name] = text

        result.append(record)

    return result


def guarded_records(records: list[dict[str, Any]]) -> tuple[str, str, int, int]:
    """Send packed content only when it round-trips exactly and is smaller.

    Returns (text, mode, tokens_before, tokens_after).
    """
    original = json.dumps(records, ensure_ascii=False)
    before = count_tokens(original)
    packed = pack_records(records)

    if packed is None:
        return original, "json", before, before

    try:
        if unpack_records(packed) != records:
            return original, "json-unsafe", before, before
    except Exception:
        return original, "json-unsafe", before, before

    after = count_tokens(packed)

    if after >= before * 0.95:
        return original, "json-no-gain", before, before

    return packed, "structpack", before, after


STRUCTPACK_PREAMBLE = (
    "Rows are pipe-delimited. Header contains column:type. "
    "#d lines expand repeated values. "
    "~M means missing, ~0 means null, ~p means literal pipe, ~n means newline."
)


# ---------------------------------------------------------------------------
# 4. Retrieval deduplication
# ---------------------------------------------------------------------------


def _shingles(text: str, size: int = 5) -> frozenset[str]:
    words = re.findall(r"\w+", text.lower())
    return frozenset(
        " ".join(words[index : index + size])
        for index in range(max(1, len(words) - size + 1))
    )


def deduplicate_chunks(chunks: list[str], threshold: float = 0.85) -> tuple[list[str], int]:
    """Drop near-duplicate retrieval chunks, keeping the longest version.

    Similarity uses containment (overlap over the smaller shingle set), not
    Jaccard. A chunk whose content is already contained in a longer kept
    chunk contributes no new information but Jaccard scores it low precisely
    because the lengths differ, so Jaccard would keep paying for it.
    """
    ranked = sorted(enumerate(chunks), key=lambda item: len(item[1]), reverse=True)
    kept: list[tuple[int, str, frozenset[str]]] = []

    for original_index, chunk in ranked:
        current = _shingles(chunk)
        duplicate = any(
            len(current & existing) / max(1, min(len(current), len(existing))) >= threshold
            for _index, _chunk, existing in kept
        )
        if not duplicate:
            kept.append((original_index, chunk, current))

    kept.sort(key=lambda item: item[0])
    result = [chunk for _index, chunk, _sh in kept]
    return result, len(chunks) - len(result)


# ---------------------------------------------------------------------------
# 5. Blob handles
# ---------------------------------------------------------------------------


class BlobStore:
    """Keep large tool results out of repeated conversation history.

    The full body stays available via get(handle); the model sees a compact
    reference until it explicitly asks for the body.
    """

    def __init__(self, preview_chars: int = 500):
        self.preview_chars = preview_chars
        self._items: dict[str, str] = {}

    def put(self, body: str, kind: str = "blob") -> str:
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        handle = f"{kind}:{digest}"
        self._items[handle] = body
        return handle

    def get(self, handle: str) -> str:
        return self._items.get(handle, "")

    def reference(self, body: str, kind: str = "blob") -> str:
        if len(body) <= self.preview_chars:
            return body

        handle = self.put(body, kind)
        preview = body[: self.preview_chars].rstrip()

        return (
            f"<{handle} bytes={len(body)}>\n"
            f"{preview}\n"
            f"[truncated; fetch {handle} for the full body]\n"
            f"</{handle}>"
        )


# ---------------------------------------------------------------------------
# 6. Exact and semantic cache
# ---------------------------------------------------------------------------


def cosine_similarity(first: list[float], second: list[float]) -> float:
    numerator = sum(left * right for left, right in zip(first, second, strict=False))
    first_norm = math.sqrt(sum(value * value for value in first))
    second_norm = math.sqrt(sum(value * value for value in second))

    if not first_norm or not second_norm:
        return 0.0

    return numerator / (first_norm * second_norm)


class SemanticCache:
    """Exact normalization is free; embedding matching is optional.

    Never cache answers that depend on live data, user permissions, session
    state, current time or random generation.
    """

    def __init__(
        self,
        embed: Callable[[str], list[float]] | None = None,
        similarity_threshold: float = 0.94,
        ttl_seconds: float = 3600,
    ):
        self.embed = embed
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self._exact: dict[str, tuple[float, str]] = {}
        self._vectors: list[tuple[list[float], float, str]] = []

    @staticmethod
    def _normalize(text: str) -> str:
        text = re.sub(r"[^\w\s]", " ", text.lower())
        return " ".join(text.split())

    def get(self, question: str) -> str | None:
        now = time.time()
        exact = self._exact.get(self._normalize(question))

        if exact and now - exact[0] <= self.ttl_seconds:
            return exact[1]

        if not self.embed:
            return None

        vector = self.embed(question)
        best_answer: str | None = None
        best_score = 0.0

        for old_vector, timestamp, answer in self._vectors:
            if now - timestamp > self.ttl_seconds:
                continue
            score = cosine_similarity(vector, old_vector)
            if score > best_score:
                best_score = score
                best_answer = answer

        if best_score >= self.similarity_threshold:
            return best_answer

        return None

    def put(self, question: str, answer: str) -> None:
        now = time.time()
        self._exact[self._normalize(question)] = (now, answer)

        if self.embed:
            self._vectors.append((self.embed(question), now, answer))

    def clear(self) -> None:
        """Drop all cached answers and vectors."""
        self._exact.clear()
        self._vectors.clear()


# ---------------------------------------------------------------------------
# 7. Bounded conversation history
# ---------------------------------------------------------------------------


class ContextLedger:
    def __init__(
        self,
        keep_recent_turns: int = 6,
        maximum_tokens: int = 4000,
        counter: Callable[[str], int] = count_tokens,
    ):
        self.keep_recent_turns = keep_recent_turns
        self.maximum_tokens = maximum_tokens
        self.counter = counter
        self.turns: list[dict[str, str]] = []
        self.facts: list[str] = []

    def add(self, role: str, content: str) -> None:
        self.turns.append({"role": role, "content": content})

    def token_size(self) -> int:
        return sum(self.counter(turn["content"]) for turn in self.turns)

    def checkpoint(self, summarize: Callable[[Iterable[dict[str, str]]], list[str]]) -> None:
        if self.token_size() <= self.maximum_tokens:
            return
        if len(self.turns) <= self.keep_recent_turns:
            return

        old = self.turns[: -self.keep_recent_turns]
        recent = self.turns[-self.keep_recent_turns :]
        new_facts = summarize(old)
        existing = {fact.lower() for fact in self.facts}

        self.facts.extend(fact for fact in new_facts if fact.lower() not in existing)
        self.turns = recent

    def render(self) -> tuple[str, list[dict[str, str]]]:
        fact_block = ""

        if self.facts:
            fact_block = "Established facts:\n" + "\n".join(f"- {fact}" for fact in self.facts)

        return fact_block, self.turns


# ---------------------------------------------------------------------------
# 8. Budget guard and adaptive output limit
# ---------------------------------------------------------------------------


@dataclass
class BudgetDecision:
    allowed: bool
    estimated_cost: float
    output_limit: int
    reason: str


class BudgetGuard:
    """Stop runaway agents before the expensive call.

    The output limit is adaptive: simple prompts get a smaller cap, complex
    prompts get more room.
    """

    def __init__(
        self,
        prices: PriceTable,
        maximum_request_cost: float = 0.05,
        default_output_limit: int = 700,
    ):
        self.prices = prices
        self.maximum_request_cost = maximum_request_cost
        self.default_output_limit = default_output_limit

    def estimate(
        self,
        input_tokens: int,
        requested_output_tokens: int | None = None,
        complexity: float = 0.0,
    ) -> BudgetDecision:
        complexity = max(0.0, min(1.0, complexity))

        minimum_output = max(128, int(self.default_output_limit * 0.35))
        maximum_output = max(
            minimum_output,
            int(self.default_output_limit * (0.7 + complexity * 1.3)),
        )
        output_limit = min(requested_output_tokens or maximum_output, maximum_output)

        usage = Usage(input_tokens=input_tokens, output_tokens=output_limit)
        estimated = self.prices.calculate(usage)

        if estimated > self.maximum_request_cost:
            return BudgetDecision(
                allowed=False,
                estimated_cost=estimated,
                output_limit=output_limit,
                reason=(
                    f"estimated request cost ${estimated:.4f} exceeds "
                    f"limit ${self.maximum_request_cost:.4f}"
                ),
            )

        return BudgetDecision(
            allowed=True,
            estimated_cost=estimated,
            output_limit=output_limit,
            reason="within budget",
        )


_COMPLEX_INTENT_RE = re.compile(
    r"\b(compare|analyz\w*|debug|design|plan|calculate|explain why)\b", re.IGNORECASE
)


def estimate_complexity(question: str, context_tokens: int, tool_count: int = 0) -> float:
    """Cheap deterministic complexity score.

    Intentionally not a model call: asking a model whether to use a cheaper
    model defeats the optimization.
    """
    score = 0.0

    if len(question.split()) > 80:
        score += 0.25
    if context_tokens > 3000:
        score += 0.25
    if tool_count > 5:
        score += 0.20
    if _COMPLEX_INTENT_RE.search(question):
        score += 0.30

    return min(1.0, score)


# ---------------------------------------------------------------------------
# 9. Deterministic field projection
# ---------------------------------------------------------------------------


def project_fields(value: Any, fields: dict[str, list[str]]) -> Any:
    """Keep only the fields the current operation declared it needs.

    Safe only because the caller declares the fields explicitly. Preferable
    to asking an LLM to summarize a huge object.
    """
    if not isinstance(value, list):
        return value

    result = []

    for item in value:
        if not isinstance(item, dict):
            result.append(item)
            continue

        selected = {}

        for key, nested in fields.items():
            if key not in item:
                continue
            if nested and isinstance(item[key], dict):
                selected[key] = {
                    child: item[key][child] for child in nested if child in item[key]
                }
            else:
                selected[key] = item[key]

        result.append(selected)

    return result


# ---------------------------------------------------------------------------
# 10. Optional LLMLingua adapter
# ---------------------------------------------------------------------------


def compress_retrieved_context(text: str, target_token_ratio: float = 0.5) -> str:
    """Optional lossy lever for large retrieved documents only.

    Never apply to system instructions, tool schemas or the user question.
    """
    try:
        from llmlingua import PromptCompressor
    except ImportError as error:
        raise RuntimeError(
            "Install optional compression support with: pip install llmlingua"
        ) from error

    compressor = PromptCompressor()
    result = compressor.compress_prompt(
        text,
        rate=target_token_ratio,
        force_tokens=["refund", "deadline", "amount", "error"],
    )

    if isinstance(result, dict):
        return result.get("compressed_prompt", text)

    return str(result)


# ---------------------------------------------------------------------------
# 11. Example integration
# ---------------------------------------------------------------------------


def prepare_request(
    question: str,
    system_prompt: str,
    tools: dict[str, Any],
    records: list[dict[str, Any]],
    documents: list[str],
    history: ContextLedger,
    blobs: BlobStore,
) -> dict[str, Any]:
    facts, turns = history.render()

    builder = PromptBuilder()
    builder.add_static(system_prompt)
    builder.add_static(canonical_json(tools))

    if facts:
        builder.add_semi_static(facts)

    packed_records, mode, before, after = guarded_records(records)

    if mode == "structpack":
        builder.add_volatile(STRUCTPACK_PREAMBLE)

    for document in documents:
        builder.add_volatile(blobs.reference(document, "document"))

    builder.add_volatile(
        f"records_mode={mode}; records_tokens={before}->{after}\n{packed_records}"
    )
    builder.add_volatile(question)

    return {
        "system": builder.system_blocks(),
        "messages": [*turns, {"role": "user", "content": builder.user_text()}],
        "metadata": {
            "records_mode": mode,
            "records_tokens_before": before,
            "records_tokens_after": after,
        },
    }
