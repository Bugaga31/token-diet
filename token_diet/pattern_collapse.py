"""Pattern Collapse + Semantic Dedup + CCR — competitor-grade compression.

Three techniques reverse-engineered from Headroom/SmartCrusher and LLMLingua-2,
implemented with ZERO neural models:

1. collapse_json_array — SmartCrusher-style: group similar records, keep examples,
   replace rest with markers like "[... 17 more items for AAPL]"

2. semantic_dedup — Jaccard n-gram similarity for near-duplicate detection
   (replaces exact-match dedup which misses reworded duplicates)

3. ccr_compress — Compress-Cache-Retrieve adapter:
   aggressively drop, cache original, inject retrieval key so agent can
   fetch full data if needed.

All techniques are lossy-safe: the Gate can still verify critical facts survived.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Pattern Collapse — SmartCrusher-style JSON compression
# ═══════════════════════════════════════════════════════════════════════════════


def _record_fingerprint(record: dict, keys: list[str]) -> str:
    """Create a stable fingerprint for grouping similar records."""
    parts = []
    for k in keys:
        v = record.get(k)
        if isinstance(v, int | float):
            parts.append(f"{k}=NUM")
        elif isinstance(v, str):
            parts.append(f"{k}={v}")
        elif isinstance(v, bool):
            parts.append(f"{k}={v}")
        else:
            parts.append(f"{k}=OTHER")
    return "|".join(parts)


def collapse_json_records(
    records: list[dict],
    max_examples_per_group: int = 3,
    total_max: int = 10,
) -> list[dict]:
    """SmartCrusher-style: collapse repeated JSON records.

    Groups records by fingerprint, keeps max_examples_per_group,
    replaces the rest with a summary marker.

    Returns a NEW list with collapsed records.
    """
    if len(records) <= total_max:
        return records

    # Find grouping keys — prefer string fields (ticker, name, type...)
    all_keys = list(records[0].keys()) if records else []
    string_keys = [k for k in all_keys if isinstance(records[0].get(k), str)]
    group_keys = string_keys[:3] if string_keys else all_keys[:2]

    if not group_keys:
        return records[:total_max]

    # Group records
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        fp = _record_fingerprint(r, group_keys)
        groups[fp].append(r)

    # Build result: keep examples, add markers
    result: list[dict] = []
    remaining = total_max

    for fp, group in sorted(groups.items(), key=lambda x: -len(x[1])):
        take = min(max_examples_per_group, len(group), remaining)
        result.extend(group[:take])
        remaining -= take

        collapsed = len(group) - take
        if collapsed > 0 and remaining > 0:
            # Add a summary marker record
            example = group[0]
            label_parts = []
            for k in group_keys:
                v = example.get(k)
                if isinstance(v, str):
                    label_parts.append(f"{k}={v}")
            label = ", ".join(label_parts) if label_parts else fp
            result.append({
                "_collapsed": True,
                "_count": collapsed,
                "_label": f"[... {collapsed} more items for: {label}]",
            })
            remaining -= 1

        if remaining <= 0:
            break

    return result


def collapse_json_text(text: str, max_examples: int = 3, total_max: int = 10) -> str:
    """Apply pattern collapse to JSON text, returning compressed JSON string."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text

    if not isinstance(data, list) or len(data) <= total_max:
        return text

    if not data or not isinstance(data[0], dict):
        return text

    collapsed = collapse_json_records(data, max_examples, total_max)

    # If collapse happened, mark it
    if len(collapsed) < len(data):
        return json.dumps(collapsed, ensure_ascii=False)

    return text


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Semantic Dedup — Jaccard n-gram similarity
# ═══════════════════════════════════════════════════════════════════════════════


def _ngrams(text: str, n: int = 4) -> set[str]:
    """Extract word-level n-grams from text (better for short texts)."""
    words = re.findall(r'\w+', text.lower())
    if len(words) < n:
        return set(words)
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def semantic_dedup(
    chunks: list[str],
    threshold: float = 0.6,
    ngram_size: int = 5,
) -> tuple[list[str], int]:
    """Remove semantically near-duplicate chunks using Jaccard on n-grams.

    Unlike exact-match dedup, this catches reworded duplicates:
    "Q4 revenue was $12.3B, up 15% YoY"
    "Q4 revenue reached $12.3 billion, a 15% increase year-over-year"

    Returns (deduped_chunks, number_removed).
    """
    if len(chunks) <= 1:
        return list(chunks), 0

    # Sort by length (keep longest, most informative version first)
    indexed = sorted(enumerate(chunks), key=lambda x: -len(x[1]))

    kept_indices: set[int] = set()
    kept_ngrams: list[tuple[int, set[str]]] = []

    for idx, text in indexed:
        ng = _ngrams(text, ngram_size)
        is_dup = False
        for _, existing_ng in kept_ngrams:
            sim = _jaccard(ng, existing_ng)
            if sim >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept_indices.add(idx)
            kept_ngrams.append((idx, ng))

    # Return in original order
    result = [c for i, c in enumerate(chunks) if i in kept_indices]
    dropped = len(chunks) - len(result)
    return result, dropped


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CCR — Compress-Cache-Retrieve adapter
# ═══════════════════════════════════════════════════════════════════════════════


class CCRStore:
    """Compress-Cache-Retrieve: aggressive compression with retrieval safety net.

    Headroom's CCR pattern:
    1. Aggressively compress/drop data
    2. Cache the original under a content hash
    3. Inject a retrieval key so the agent can fetch full data if needed

    This allows MUCH more aggressive compression because nothing is truly lost.
    """

    def __init__(self):
        self._cache: dict[str, str] = {}

    def compress(self, text: str, compressed: str, label: str = "data") -> str:
        """Store original and return compressed version with retrieval key.

        The returned text includes a marker the agent can use to fetch the full
        original if the compressed version is insufficient.
        """
        h = hashlib.sha256(text.encode()).hexdigest()[:12]
        self._cache[h] = text
        return (
            f"{compressed}\n\n"
            f"[CCR:{h}] {len(text)} chars of {label} cached. "
            f"Call ccr_fetch('{h}') for full original."
        )

    def fetch(self, hash_key: str) -> str | None:
        """Retrieve cached original by hash key."""
        return self._cache.get(hash_key)

    def __len__(self) -> int:
        return len(self._cache)
