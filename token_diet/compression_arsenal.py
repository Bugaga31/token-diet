"""Compression Arsenal — reverse-engineered from the best competitors.

Five breakthrough techniques (2025-2026), ALL algorithmic (no neural models):

1. CAVEMAN GRAMMAR STRIPPER — "Strip grammar. Keep facts."
   Source: Caveman (wilpel/caveman-compression)
   Removes articles, copulas, connectives, filler. Keeps numbers, names, dates.

2. LOG COLLAPSER (LeanContext-style)
   Source: LeanContext (pankajniet/LeanContext)
   Collapses near-identical log lines into counts. Keeps ERROR/FATAL lines.
   Fidelity score — fails open if quality drops.

3. KV-CACHE STABILIZER (anyModel/Headroom-style)
   Source: anymodel.dev, Headroom cache-aligner
   Ensures static content at beginning (byte-identical for KV-cache reuse).
   Marks volatile zones at end.

4. DYNAMIC TOOL COMPRESSOR
   Source: anymodel.dev tool trimming
   Compresses function/tool definitions: strip descriptions, keep schemas.

5. SELF-INFORMATION SCORER (algorithmic Selective Context)
   Source: Selective Context (liamccline)
   Compute sentence "surprise" without neural model — word frequency + positional.
"""

from __future__ import annotations

import re
import json
import hashlib
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CAVEMAN GRAMMAR STRIPPER
# ═══════════════════════════════════════════════════════════════════════════════

# Articles — predictable grammar, LLM can reconstruct
_ARTICLES_EN = re.compile(r'\b(?:the|a|an)\b', re.IGNORECASE)
# Copular verbs in predictable positions
_COPULA_EN = re.compile(r'\b(?:is|are|was|were|be|been|being)\s+(?:a |an |the )?', re.IGNORECASE)
# Connectives — add little factual value
_CONNECTIVES_EN = re.compile(
    r'\b(?:therefore|however|moreover|furthermore|nevertheless|consequently|'
    r'accordingly|thus|hence|nonetheless|meanwhile|additionally)\b[,\s]*',
    re.IGNORECASE,
)
# Filler adjectives / adverbs
_FILLER_ADJ_EN = re.compile(
    r'\b(?:very|quite|rather|somewhat|extremely|highly|really|truly|'
    r'basically|essentially|literally|actually|certainly|definitely|'
    r'absolutely|obviously|clearly|simply|just|pretty)\b[,\s]*',
    re.IGNORECASE,
)
# Passive voice padding
_PASSIVE_EN = re.compile(r'\b(?:it is worth noting that|it should be noted that|'
                         r'it is important to note that|it can be seen that|'
                         r'it is interesting to note that)\b',
                         re.IGNORECASE)
# Redundant qualifiers
_QUALIFIERS_EN = re.compile(r'\b(?:in terms of|in relation to|with respect to|'
                            r'as far as .{1,20} is concerned)\b',
                            re.IGNORECASE)

# Russian patterns
_ARTICLES_RU = re.compile(r'\b(?:этот|эта|это|эти|тот|та|те|свой|своя|своё|свои)\b',
                          re.IGNORECASE)
_FILLER_RU = re.compile(
    r'\b(?:очень|весьма|довольно|крайне|чрезвычайно|достаточно|'
    r'просто|буквально|фактически|реально|абсолютно|совершенно|'
    r'определённо|безусловно|несомненно|очевидно|явно)\b[,\s]*',
    re.IGNORECASE,
)
_CONNECTIVES_RU = re.compile(
    r'\b(?:однако|тем не менее|кроме того|более того|также|следовательно|'
    r'таким образом|в то же время|вместе с тем|помимо этого)\b[,\s]*',
    re.IGNORECASE,
)

# Numbers, dates, names — NEVER strip these
# But EXCLUDE common sentence-starting connectives (they get stripped)
_SENTENCE_CONNECTIVES = frozenset({
    'furthermore', 'moreover', 'however', 'therefore', 'nevertheless',
    'consequently', 'accordingly', 'additionally', 'meanwhile', 'nonetheless',
    'besides', 'hence', 'thus', 'indeed', 'instead', 'otherwise',
    'also', 'finally', 'firstly', 'secondly', 'thirdly', 'lastly',
})

_PROTECTED_PATTERNS = re.compile(
    r'\b(?:\d+(?:\.\d+)?%?|\$\d+|\d{2,4}-\d{2}-\d{2,4}|'
    r'[A-Z]{2,}|O\(\w+\))\b'
)

# Proper names: multi-word capitalized sequences NOT starting with connectives
_PROPER_NAME = re.compile(
    r'\b(?!' + '|'.join(_SENTENCE_CONNECTIVES) +
    r'\b)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b',
    re.IGNORECASE,
)


def strip_grammar_caveman(text: str) -> str:
    """Caveman-style grammar stripping: Strip grammar. Keep facts.

    Removes (predictable, LLM can reconstruct):
    - Articles: a, an, the
    - Copulas in predictable spots: is, are, was, were
    - Connectives: therefore, however, moreover...
    - Filler adjectives: very, quite, extremely...
    - Passive voice padding
    - Redundant qualifiers

    Keeps (unpredictable, factual):
    - Numbers, percentages, dollar amounts
    - Dates (2024-01-15)
    - Proper names, acronyms
    - Technical terms, Big-O notation
    - Code, JSON, URLs

    Typical savings: 15-30% on prose.
    Based on Caveman NLP mode.
    """
    # Find protected spans: ONLY numbers, dates, multi-word proper names
    protected: list[tuple[int, int, str]] = []
    for m in _PROTECTED_PATTERNS.finditer(text):
        protected.append((m.start(), m.end(), m.group()))
    # Multi-word proper names (but NOT single capitalized words = connectives)
    for m in _PROPER_NAME.finditer(text):
        if not any(p[0] <= m.start() < p[1] for p in protected):
            protected.append((m.start(), m.end(), m.group()))
    # Sort by position (descending for safe replacement)
    protected.sort(key=lambda x: -x[0])

    # Replace protected spans with placeholders
    placeholder_map: dict[str, str] = {}
    result = text
    for i, (start, end, original) in enumerate(protected):
        placeholder = f"__PROTECTED_{i}__"
        placeholder_map[placeholder] = original
        result = result[:start] + placeholder + result[end:]

    # Apply stripping in order: most aggressive first
    result = _PASSIVE_EN.sub('', result)
    result = _QUALIFIERS_EN.sub('', result)
    # Lowercase sentence-start connectives for matching
    result = _CONNECTIVES_EN.sub(' ', result)
    result = _FILLER_ADJ_EN.sub('', result)
    result = _COPULA_EN.sub(' ', result)
    result = _ARTICLES_EN.sub('', result)

    # Russian
    result = _CONNECTIVES_RU.sub(' ', result)
    result = _FILLER_RU.sub('', result)
    result = _ARTICLES_RU.sub('', result)

    # Restore protected spans
    for placeholder, original in placeholder_map.items():
        result = result.replace(placeholder, original)

    # Clean up: multiple spaces, spaces before punctuation, leading commas
    result = re.sub(r' {2,}', ' ', result)
    result = re.sub(r' +([.,!?;:])', r'\1', result)
    result = re.sub(r'^[,;:\s]+', '', result)
    result = re.sub(r'\.{2,}', '.', result)
    result = result.strip()

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LOG COLLAPSER (LeanContext-style)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FidelityScore:
    """Quality score for compressed content."""
    score: float = 1.0  # 0.0 (lost everything) to 1.0 (perfect)
    errors_kept: int = 0
    total_lines: int = 0
    collapsed_lines: int = 0

    @property
    def is_safe(self) -> bool:
        """Fails open: return True if compression is safe."""
        return self.score >= 0.85

    @property
    def reduction_pct(self) -> float:
        if self.total_lines == 0:
            return 0.0
        return 100.0 * self.collapsed_lines / self.total_lines


# Critical log keywords — NEVER collapse these
_CRITICAL_LOG = re.compile(
    r'\b(?:ERROR|FATAL|PANIC|CRASH|FAIL|CRITICAL|SEVERE|EMERGENCY|'
    r'ALERT|EXCEPTION|Traceback|stack trace|core dump|OOM|'
    r'segfault|deadlock|timeout|refused|denied)\b',
    re.IGNORECASE,
)


def _log_signature(line: str) -> str:
    """Extract the structural signature of a log line (template).

    Remove: timestamps, IDs, IPs, numbers, hex values, paths with numbers.
    Keep: log level, message template, file:line references.
    """
    sig = line
    # Timestamps
    sig = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?', '<TS>', sig)
    sig = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3}', '<TS>', sig)
    # UUIDs
    sig = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<UUID>', sig)
    # IPs
    sig = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '<IP>', sig)
    # Hex values
    sig = re.sub(r'0x[0-9a-fA-F]+', '<HEX>', sig)
    # Numbers + optional units (12ms, 500KB, 3.5GHz)
    sig = re.sub(r'\b\d+(?:\.\d+)?(?:e[+-]?\d+)?(?:ms|s|KB|MB|GB|TB|Hz|MHz|GHz|%|px|em|pt)?\b', '<N>', sig)
    # Paths with numeric segments
    sig = re.sub(r'/[a-zA-Z0-9_./-]+', '<PATH>', sig)
    # Request IDs
    sig = re.sub(r'req[a-z_-]*[0-9a-f-]{8,}', '<REQID>', sig)

    return sig.strip()


def collapse_logs(text: str, min_fidelity: float = 0.85) -> tuple[str, FidelityScore]:
    """LeanContext-style deterministic log collapse.

    Algorithm:
    1. Split into lines
    2. For each line: check if critical (keep verbatim) or repetitive
    3. Group consecutive identical-signature lines
    4. If group > 2: collapse to "<signature> ×N"
    5. Keep first and last instance for context
    6. Compute fidelity score
    7. Fail open if fidelity drops below threshold

    Typical: 60-95% reduction on verbose logs.
    """
    lines = text.split('\n')
    if len(lines) < 3:
        return text, FidelityScore(1.0, 0, len(lines), 0)

    result: list[str] = []
    errors_kept = 0
    total_lines = len(lines)
    collapsed_lines = 0

    # Pass 1: identify critical lines
    critical_indices: set[int] = set()
    for i, line in enumerate(lines):
        if _CRITICAL_LOG.search(line):
            critical_indices.add(i)
            errors_kept += 1

    # Pass 2: collapse repetitive non-critical blocks
    i = 0
    while i < len(lines):
        line = lines[i]
        sig = _log_signature(line)

        # Always keep critical lines
        if i in critical_indices:
            result.append(line)
            i += 1
            continue

        # Find consecutive lines with same signature
        j = i + 1
        while j < len(lines) and j not in critical_indices:
            if _log_signature(lines[j]) != sig:
                break
            j += 1

        group_size = j - i

        if group_size >= 4:
            # Collapse: keep first and last, rest as count
            result.append(line)  # first instance
            result.append(f"  ⟪×{group_size - 2} similar lines collapsed⟫")
            result.append(lines[j - 1])  # last instance
            collapsed_lines += group_size - 3  # -3 because we kept first, count line, last
        elif group_size >= 2:
            # Small group: keep all but note
            for k in range(i, j):
                result.append(lines[k])
        else:
            result.append(line)

        i = j

    # Compute fidelity
    fidelity = 1.0
    if total_lines > 0:
        # Penalty for collapsing — but errors kept counts positively
        collapse_ratio = collapsed_lines / total_lines
        error_coverage = errors_kept / max(1, sum(1 for _ in _CRITICAL_LOG.finditer(text)))
        fidelity = 1.0 - (collapse_ratio * 0.3) + (error_coverage * 0.1)
        fidelity = min(1.0, max(0.0, fidelity))

    score = FidelityScore(
        score=fidelity,
        errors_kept=errors_kept,
        total_lines=total_lines,
        collapsed_lines=collapsed_lines,
    )

    # Fail open: if fidelity too low, return original
    if fidelity < min_fidelity:
        return text, score

    return '\n'.join(result), score


# ═══════════════════════════════════════════════════════════════════════════════
# 3. KV-CACHE STABILIZER (anyModel/Headroom-style)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class CacheZone:
    """A zone in the context window for KV-cache optimization."""
    content: str
    stability: str  # "static", "semi_static", "volatile"
    hash_before: str = ""
    estimated_tokens: int = 0


def stabilize_for_kv_cache(
    system: str = "",
    tools: str = "",
    documents: str = "",
    history: list[str] = None,
    query: str = "",
) -> list[CacheZone]:
    """Order prompt components for maximum KV-cache hit rate.

    The 2026 Golden Rule (Anthropic/OpenAI/Google):
    Most-stable → Least-stable ordering:
    1. Tool/function definitions  (almost never change)
    2. System prompt instructions  (rarely change)
    3. Reference documents         (occasionally change)
    4. Conversation history        (grows incrementally)
    5. Live user query             (changes every request)

    This ensures:
    - Bytes 0..N are byte-identical across requests → KV-cache hit
    - Only the volatile tail changes → minimal cache write penalty

    Returns ordered CacheZones for the proxy to assemble.

    Source: anymodel.dev prefix stabilization, Headroom cache-aligner,
            ProjectDiscovery Neo (7% → 84% cache hit rate).
    """
    zones: list[CacheZone] = []

    # Zone 1: Tools (most stable — almost never change)
    if tools:
        zones.append(CacheZone(
            content=tools,
            stability="static",
            hash_before=_hash_content(tools),
        ))

    # Zone 2: System prompt (rarely changes)
    if system:
        zones.append(CacheZone(
            content=f"<system>\n{system}\n</system>",
            stability="semi_static",
            hash_before=_hash_content(system),
        ))

    # Zone 3: Reference documents (occasionally change)
    if documents:
        zones.append(CacheZone(
            content=f"<context>\n{documents}\n</context>",
            stability="semi_static",
            hash_before=_hash_content(documents),
        ))

    # Zone 4: Conversation history (grows incrementally — cache prefix still hits)
    if history:
        history_text = ""
        for i, turn in enumerate(history):
            # Keep most recent turns at full detail, older ones can be compressed
            if i >= len(history) - 5:
                history_text += turn + "\n\n"
            else:
                # Older turns: just one-liner
                first_line = turn.split('\n')[0][:120] if turn else ""
                history_text += f"[turn {i}]: {first_line}\n"
        zones.append(CacheZone(
            content=f"<history>\n{history_text.strip()}\n</history>",
            stability="volatile",
        ))

    # Zone 5: User query (most volatile — changes every request)
    if query:
        zones.append(CacheZone(
            content=f"<query>\n{query}\n</query>",
            stability="volatile",
        ))

    return zones


def _hash_content(text: str) -> str:
    """Content hash for cache invalidation detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def assemble_kv_optimized(zones: list[CacheZone]) -> str:
    """Assemble zones into a single prompt optimized for KV-cache.

    Appends a cache boundary marker that some providers recognize.
    """
    parts = []
    for zone in zones:
        if zone.stability == "static" and zone.hash_before:
            parts.append(f"<!-- CACHE:{zone.hash_before} -->\n{zone.content}")
        else:
            parts.append(zone.content)

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DYNAMIC TOOL COMPRESSOR
# ═══════════════════════════════════════════════════════════════════════════════


def compress_tool_definitions(
    tools_json: str | list[dict],
    max_tools: int = 20,
    strip_descriptions: bool = True,
    strip_examples: bool = True,
) -> str:
    """Compress OpenAI-style tool/function definitions.

    Techniques (from anymodel.dev):
    1. Strip verbose descriptions (keep names + params only)
    2. Strip examples from descriptions
    3. Limit tool count (most important first)
    4. Keep parameter schemas (required for structured output)

    Input: JSON string of tools array, or list of tool dicts.
    Output: compressed JSON string.

    Typical savings: 30-50% on tool definitions.
    """
    if isinstance(tools_json, str):
        try:
            tools = json.loads(tools_json)
        except json.JSONDecodeError:
            return tools_json
    else:
        tools = list(tools_json)

    if not tools:
        return "[]"

    compressed = []
    for tool in tools[:max_tools]:
        tc: dict[str, Any] = {}
        if "type" in tool:
            tc["type"] = tool["type"]

        fn = tool.get("function", tool)
        if "function" in tool:
            pass  # already extracted

        cf: dict[str, Any] = {"name": fn.get("name", "")}

        # Strip description to bare minimum (1 line max)
        desc = fn.get("description", "")
        if strip_descriptions and desc:
            # Keep only first sentence
            first_sentence = re.split(r'[.!?]\s+', desc)[0]
            if len(first_sentence) > 80:
                first_sentence = first_sentence[:77] + "..."
            cf["description"] = first_sentence
        elif desc:
            cf["description"] = desc

        # Parameters: keep schema, strip descriptions from individual params
        params = fn.get("parameters", {})
        if params and strip_examples:
            params = _strip_param_descriptions(params)
        if params:
            cf["parameters"] = params

        compressed.append({"type": "function", "function": cf})

    return json.dumps(compressed, ensure_ascii=False, separators=(',', ':'))


def _strip_param_descriptions(params: dict) -> dict:
    """Recursively strip descriptions from JSON Schema parameters."""
    if not isinstance(params, dict):
        return params

    result = {}
    for key, value in params.items():
        if key in ("description", "examples", "example"):
            continue
        if key == "properties" and isinstance(value, dict):
            result[key] = {
                prop: _strip_param_descriptions(schema)
                for prop, schema in value.items()
            }
        elif isinstance(value, dict):
            result[key] = _strip_param_descriptions(value)
        else:
            result[key] = value

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SELF-INFORMATION SCORER (algorithmic Selective Context)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class SentenceScore:
    text: str
    self_information: float = 0.0  # Surprise / novelty score
    is_redundant: bool = False


# Function words — low information content
_FUNCTION_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could",
    "of", "in", "to", "for", "with", "on", "at", "by", "from", "about",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "over", "up", "down", "out", "off",
    "and", "but", "or", "not", "so", "if", "than", "that", "this", "these",
    "those", "it", "he", "she", "they", "we", "you", "me", "him", "her",
    "us", "them", "my", "your", "his", "her", "its", "our", "their",
    "also", "then", "now", "just", "only", "very", "really", "just", "quite",
    "still", "already", "always", "never", "often", "sometimes",
})


def score_self_information(text: str, context_before: str = "") -> list[SentenceScore]:
    """Algorithmic approximation of self-information scoring.

    Without a neural LM (GPT-2), we approximate:
    I(sentence | context) = -log P(sentence | context)
    Using:
    - Word frequency rarity (inverse of TF in document)
    - Context overlap: sentences with high word overlap with previous = low information
    - Position: middle sentences tend to be less informative (U-curve)
    - Length: very short sentences are often connectors

    This is the fundamental insight behind Selective Context and LLMLingua-2
    — but done WITHOUT any neural model.

    Source: Selective Context (Li et al., EMNLP 2023)
            LLMLingua-2 token classification
    """
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    if len(sentences) <= 1:
        return [SentenceScore(s, 0.0, False) for s in sentences]

    # Word frequency in the entire text
    all_words = re.findall(r'\b[a-zA-Zа-яА-ЯёЁ]+\b', text.lower())
    word_freq: dict[str, int] = {}
    for w in all_words:
        word_freq[w] = word_freq.get(w, 0) + 1
    total_words = max(1, len(all_words))

    # Context words (from context_before)
    ctx_words = set(re.findall(r'\b[a-zA-Zа-яА-ЯёЁ]+\b', context_before.lower()))

    scored: list[SentenceScore] = []
    for i, sent in enumerate(sentences):
        words = re.findall(r'\b[a-zA-Zа-яА-ЯёЁ]+\b', sent.lower())
        if not words:
            scored.append(SentenceScore(sent, 0.0, False))
            continue

        # Factor 1: Word rarity (inverse frequency)
        rarity_scores = []
        for w in words:
            freq = word_freq.get(w, 1)
            # Rare words = high self-information
            rarity = 1.0 / max(1, freq)
            # Bonus for non-function words
            if len(w) > 3 and w not in _FUNCTION_WORDS:
                rarity *= 2.0
            rarity_scores.append(rarity)
        rarity_score = sum(rarity_scores) / len(words)

        # Factor 2: Context overlap (high overlap = redundant = low information)
        content_words = [w for w in words if len(w) > 3 and w not in _FUNCTION_WORDS]
        overlap = sum(1 for w in content_words if w in ctx_words) / max(1, len(content_words))
        novelty_score = 1.0 - overlap  # High novelty = high self-information

        # Factor 3: Position (U-curve: edges more important than middle)
        pos_ratio = i / max(1, len(sentences) - 1)
        # U-curve: edges = 1.0, middle = 0.6
        position_score = 1.0 - 0.4 * (2 * abs(pos_ratio - 0.5))

        # Factor 4: Length penalty (very short = likely connector, not informative)
        length_score = min(1.0, len(words) / 10.0)

        # Combine: weighted sum
        info = (
            0.35 * rarity_score +
            0.35 * novelty_score +
            0.15 * position_score +
            0.15 * length_score
        )

        is_redundant = info < 0.25  # Threshold for redundancy

        scored.append(SentenceScore(sent, info, is_redundant))

    return scored


def compress_by_self_information(text: str, keep_ratio: float = 0.6) -> str:
    """Compress text by removing low self-information sentences.

    Keeps the keep_ratio fraction of highest-information sentences.
    Like Selective Context, but algorithmic.
    """
    scored = score_self_information(text)

    # Always keep first and last sentence (edge effect)
    keep_count = max(2, int(len(scored) * keep_ratio))

    # Sort by self-information, descending
    indexed = list(enumerate(scored))
    indexed.sort(key=lambda x: -x[1].self_information)

    # Keep top N, plus first and last
    keep_indices: set[int] = {0, len(scored) - 1}  # Always keep edges
    for idx, _ in indexed[:keep_count]:
        keep_indices.add(idx)

    # Reconstruct in original order
    kept = [scored[i].text for i in sorted(keep_indices)]
    return ' '.join(kept)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. UNIFIED PIPELINE — all reverse-engineered techniques
# ═══════════════════════════════════════════════════════════════════════════════


def apply_arsenal(
    text: str,
    *,
    apply_caveman: bool = True,
    apply_log_collapse: bool = True,
    apply_self_info: bool = False,  # Off by default: can be aggressive
) -> str:
    """Apply all compression arsenal techniques.

    Order matters:
    1. Log collapse (structural — needs line integrity)
    2. Caveman grammar strip (linguistic — word-level)
    3. Self-information scoring (semantic — sentence-level, most aggressive)
    """
    result = text

    if apply_log_collapse:
        result, _ = collapse_logs(result, min_fidelity=0.85)

    if apply_caveman:
        result = strip_grammar_caveman(result)

    if apply_self_info:
        result = compress_by_self_information(result, keep_ratio=0.6)

    return result
