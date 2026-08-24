"""TF-IDF Scorer — query-aware retrieval compression.

LLMLingua-2's secret weapon: score sentences by relevance to the user query,
not just by internal density. This gives them 33.8% on retrieval vs our 11.7%.

Algorithm:
1. Build TF-IDF vector for each sentence
2. Build TF-IDF vector for user query
3. Score each sentence by cosine similarity to query
4. Keep top-K sentences, drop low-relevance ones

Pure algorithmic — zero neural models.
"""

from __future__ import annotations

import math
import re
from collections import Counter


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words (min 3 chars)."""
    return [w.lower() for w in re.findall(r'\w+', text) if len(w) >= 3]


def _compute_tf(tokens: list[str]) -> dict[str, float]:
    """Term frequency with log normalization."""
    counter = Counter(tokens)
    total = len(tokens) or 1
    return {word: count / total for word, count in counter.items()}


def _compute_idf(documents: list[list[str]]) -> dict[str, float]:
    """Inverse document frequency."""
    n_docs = len(documents) or 1
    idf: dict[str, float] = {}
    all_words: set[str] = set()
    for doc in documents:
        all_words.update(doc)
    for word in all_words:
        doc_count = sum(1 for doc in documents if word in doc)
        idf[word] = math.log((n_docs + 1) / (doc_count + 1)) + 1.0
    return idf


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors."""
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[w] * b[w] for w in common)
    norm_a = math.sqrt(sum(v ** 2 for v in a.values()))
    norm_b = math.sqrt(sum(v ** 2 for v in b.values()))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def score_sentences(
    sentences: list[str],
    query: str,
    keep_ratio: float = 0.5,
) -> tuple[list[str], list[float]]:
    """Score sentences by TF-IDF relevance to query.

    Returns (sorted_sentences, scores).
    """
    if len(sentences) <= 2:
        return sentences, [1.0] * len(sentences)

    # Tokenize
    query_tokens = _tokenize(query)
    sentence_tokens = [_tokenize(s) for s in sentences]

    # IDF over all sentences as documents
    all_docs = sentence_tokens + [query_tokens]
    idf = _compute_idf(all_docs)

    # TF-IDF vector for query
    query_tf = _compute_tf(query_tokens)
    query_vec = {w: query_tf.get(w, 0) * idf.get(w, 0) for w in query_tf}

    # TF-IDF vectors for each sentence
    scores = []
    for _i, tokens in enumerate(sentence_tokens):
        tf = _compute_tf(tokens)
        vec = {w: tf.get(w, 0) * idf.get(w, 0) for w in tf}
        sim = _cosine_similarity(query_vec, vec)
        # Boost by sentence length (longer = more informative)
        length_boost = min(1.0, len(tokens) / 50.0)
        scores.append(sim * 0.7 + length_boost * 0.3)

    # Keep first and last (context framing)
    keep_n = max(2, int(len(sentences) * keep_ratio))
    scored = [(i, scores[i]) for i in range(len(sentences))]
    scored.sort(key=lambda x: -x[1])

    # Always keep first and last sentence
    keep_indices = {0, len(sentences) - 1}
    for idx, _score in scored:
        if len(keep_indices) >= keep_n:
            break
        keep_indices.add(idx)

    # Return in original order
    result = [(i, sentences[i], scores[i]) for i in sorted(keep_indices)]
    return [s for _, s, _ in result], [sc for _, _, sc in result]


def compress_retrieval_tfidf(
    text: str,
    query: str = "",
    keep_ratio: float = 0.5,
) -> str:
    """Compress retrieval text by keeping only query-relevant sentences.

    This is LLMLingua-2's main advantage: they score sentences against
    the user query, not just by internal density.

    Args:
        text: retrieval document text
        query: user question (for relevance scoring)
        keep_ratio: fraction of sentences to keep

    Returns compressed text.
    """
    if not text or not query:
        return text

    # Split into sentences
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    if len(sentences) <= 2:
        return text

    kept, scores = score_sentences(sentences, query, keep_ratio)
    if len(kept) >= len(sentences):
        return text

    return " ".join(kept)
