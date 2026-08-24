"""map_reduce — chunked fan-out processing of long documents (LlamaIndex/LangChain reverse-engineered).

Problem:
    A 100k-token document cannot fit in one context window. Naive
    truncation loses the middle. Naive full-prompt costs a fortune.

Solution (map-reduce, the industry standard):
    MAP   — split the document into token-budgeted chunks; run a CHEAP
            fast model on each chunk in PARALLEL (fan-out), producing a
            partial summary/answer per chunk.
    REDUCE— concatenate the partial results and run ONE expensive call
            on the (now small) aggregate to synthesize the final answer.

Token economics:
    Long doc = 100k tokens. Cheap model = 1/10th the price of the big one.
    Map: 10 chunks × 12k tokens on the cheap model.
    Reduce: ~3k tokens of partials on the expensive model.
    Total cost ≈ 25% of a single full-context call on the big model —
    and the result is usually BETTER (no lost middle).

This module is model-agnostic: you pass in the two callables. A
deterministic no-LLM fallback (extractive summarization via BM25/TF-IDF
sentence scoring) lets you use map-reduce even with ZERO API budget.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

try:
    from .core import count_tokens
except ImportError:  # pragma: no cover
    from core import count_tokens  # type: ignore[no-redef]

try:
    from .tfidf_scorer import score_sentences
except ImportError:  # pragma: no cover
    score_sentences = None  # type: ignore[assignment]


# ── chunking ─────────────────────────────────────────────────────────────────


def split_into_chunks(
    text: str,
    max_chars: int = 12000,
    overlap: int = 300,
) -> list[str]:
    """Split text into overlapping chunks on paragraph/word boundaries.

    Overlap preserves context across chunk seams (a sentence split in
    half is a hallucination risk).
    """
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + max_chars, n)
        if end < n:
            # rewind to paragraph break, else sentence end, else space
            for sep in ("\n\n", ". ", "! ", "? "):
                pos = text.rfind(sep, i + max_chars // 2, end)
                if pos > i:
                    end = pos + len(sep)
                    break
            else:
                sp = text.rfind(" ", i, end)
                if sp > i + max_chars // 2:
                    end = sp
        chunk = text[i:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        i = max(end - overlap, i + 1)
    return chunks


# ── deterministic fallback (no LLM needed) ───────────────────────────────────


def extractive_summarize(text: str, max_chars: int = 1500, query: str = "") -> str:
    """Extractive summarization: keep the most informative sentences.

    Uses TF-IDF sentence scoring against the query (LLMLingua-2 style)
    when available; falls back to position-based selection. Zero API calls.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if len(sentences) <= 3:
        return text[:max_chars]

    if score_sentences is not None and query:
        kept, _ = score_sentences(sentences, query, keep_ratio=0.35)
        result = " ".join(kept)
    else:
        # positional: first, then evenly sampled
        n = len(sentences)
        take = max(3, int(n * 0.3))
        idx = sorted({0, n - 1, *[int(i * (n - 1) / max(1, take - 1))
                                  for i in range(take)]})
        result = " ".join(sentences[i] for i in idx)

    return result[:max_chars]


# ── map-reduce pipeline ──────────────────────────────────────────────────────


@dataclass
class MapReduceResult:
    """Result of a map-reduce run."""
    n_chunks: int = 0
    input_tokens: int = 0
    map_tokens: int = 0
    reduce_tokens: int = 0
    total_tokens: int = 0
    answer: str = ""
    partials: list[str] = field(default_factory=list)
    used_llm: bool = False

    @property
    def savings_pct(self) -> float:
        """Savings vs a single full-context call on the expensive model.

        A single call would cost ~ input_tokens. Map-reduce costs
        map_tokens (cheap) + reduce_tokens (expensive). We report the
        token reduction assuming map uses 1x (real savings come from
        the price gap between cheap and expensive models).
        """
        if self.input_tokens <= 0:
            return 0.0
        return round(100 * (self.input_tokens - self.total_tokens)
                     / self.input_tokens, 1)


def map_reduce(
    text: str,
    map_fn: Callable[[str, str], str] | None = None,
    reduce_fn: Callable[[str, str], str] | None = None,
    query: str = "",
    max_chunk_chars: int = 12000,
    max_partials_chars: int = 20000,
    deterministic_fallback: bool = True,
) -> MapReduceResult:
    """Run map-reduce over a long document.

    Args:
        text: the document (any length)
        map_fn: callable(chunk_text, query) -> partial. If None, uses
                extractive_summarize (deterministic, free).
        reduce_fn: callable(partials_text, query) -> final answer. If None,
                concatenates partials (or summarizes if fallback enabled).
        query: the user's question (guides extractive map).
        max_chunk_chars: chunk budget for the MAP step.
        max_partials_chars: cap for the REDUCE input.
        deterministic_fallback: if True, never fail — use extractive
                fallback when map_fn/reduce_fn are missing.

    Returns:
        MapReduceResult with token accounting.
    """
    chunks = split_into_chunks(text, max_chars=max_chunk_chars)
    if not chunks:
        return MapReduceResult(answer="", n_chunks=0, input_tokens=count_tokens(text))

    input_tokens = count_tokens(text)
    used_llm = map_fn is not None and reduce_fn is not None

    # MAP
    partials: list[str] = []
    map_tokens = 0
    for chunk in chunks:
        if map_fn is not None:
            try:
                partial = map_fn(chunk, query)
                map_tokens += count_tokens(str(partial))
            except Exception as e:
                partial = f"[map error: {e}]"
                map_tokens += count_tokens(partial)
        else:
            partial = extractive_summarize(chunk, max_chars=max_chunk_chars // 4,
                                           query=query)
            map_tokens += count_tokens(partial)
        partials.append(partial)

    # REDUCE
    partials_text = "\n\n---CHUNK---\n\n".join(partials)
    if len(partials_text) > max_partials_chars:
        partials_text = partials_text[:max_partials_chars]

    if reduce_fn is not None:
        try:
            answer = reduce_fn(partials_text, query)
        except Exception as e:
            answer = f"[reduce error: {e}]\n\n{partials_text}"
    else:
        if deterministic_fallback:
            answer = extractive_summarize(partials_text, max_chars=8000, query=query)
        else:
            answer = partials_text

    reduce_tokens = count_tokens(answer)
    total = map_tokens + reduce_tokens

    return MapReduceResult(
        n_chunks=len(chunks),
        input_tokens=input_tokens,
        map_tokens=map_tokens,
        reduce_tokens=reduce_tokens,
        total_tokens=total,
        answer=answer,
        partials=partials,
        used_llm=used_llm,
    )


__all__ = [
    "MapReduceResult",
    "extractive_summarize",
    "map_reduce",
    "split_into_chunks",
]
