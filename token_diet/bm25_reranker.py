"""bm25_reranker — Okapi BM25 lexical reranking (FlashRank/rank_bm25 reverse-engineered).

Why BM25 beats plain keyword overlap:
    Plain overlap counts matching words. BM25 additionally rewards
    RARE query words (idf) and penalizes very long documents (length
    normalization) — so it retrieves the SHORT relevant chunk over a
    long document that happens to contain the word once.

Why this matters for token-diet:
    Better retrieval = fewer tokens wasted on irrelevant chunks AND
    higher answer quality (the right page of the book). A 5% retrieval
    improvement beats a 20% compression improvement, because wrong
    context poisons the answer.

Pure stdlib — no numpy, no sklearn, no embeddings. Okapi BM25 with
defaults k1=1.5, b=0.75 (the classic BM25-tuned values).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]{3,}")


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, min length 3 (like the rest of the library)."""
    return [w.lower() for w in _WORD_RE.findall(text)]


@dataclass
class RerankHit:
    """One reranked document."""
    index: int
    score: float
    text: str
    tokens: int = 0


class BM25Reranker:
    """Okapi BM25 over a set of documents, with per-query scoring.

    Usage:
        r = BM25Reranker(docs)         # docs: list[str]
        hits = r.rerank(query, top_k=3)  # sorted best-first
    """

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b

        # tokenized docs + term frequencies
        self.tok_docs = [_tokenize(d) for d in docs]
        self.doc_len = [len(t) for t in self.tok_docs]
        self.avgdl = sum(self.doc_len) / max(1, len(self.doc_len))

        # df: document frequency per term; postings: term → [doc_idx...]
        self.df: dict[str, int] = {}
        self.postings: dict[str, list[int]] = {}
        for i, toks in enumerate(self.tok_docs):
            seen: set[str] = set()
            for t in toks:
                if t not in seen:
                    seen.add(t)
                    self.df[t] = self.df.get(t, 0) + 1
                self.postings.setdefault(t, []).append(i)

        self.n_docs = len(docs)

    # ── scoring ──────────────────────────────────────────────────────────

    def _idf(self, term: str) -> float:
        n = self.df.get(term, 0)
        # BM25+ smoothing: log((N - n + 0.5)/(n + 0.5)), floor at 0 for terms
        # in all docs. Use the Lucene-style formula that never goes negative.
        return math.log(1 + (self.n_docs - n + 0.5) / (n + 0.5))

    def score(self, query: str, doc_index: int) -> float:
        """BM25 score of one document for a query (Lucene variant)."""
        if not self.docs or doc_index >= len(self.tok_docs):
            return 0.0
        toks = self.tok_docs[doc_index]
        dl = self.doc_len[doc_index]
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1

        q_terms = set(_tokenize(query))
        total = 0.0
        for term in q_terms:
            if term not in tf:
                continue
            f = tf[term]
            denom = f + self.k1 * (1 - self.b + self.b * dl / max(1.0, self.avgdl))
            total += self._idf(term) * f * (self.k1 + 1) / max(1e-9, denom)
        return total

    def rerank(self, query: str, top_k: int = 3) -> list[RerankHit]:
        """Return top-k documents sorted by BM25 score (desc)."""
        if not self.docs or not query.strip():
            return [RerankHit(i, 0.0, d) for i, d in enumerate(self.docs[:top_k])]

        scores = [(self.score(query, i), i) for i in range(len(self.docs))]
        scores.sort(key=lambda x: -x[0])
        hits = []
        for s, i in scores[:top_k]:
            hits.append(RerankHit(
                index=i, score=round(s, 4), text=self.docs[i],
                tokens=len(self.tok_docs[i]),
            ))
        return hits


# ── integration with Library / chunk retrieval ───────────────────────────────


def rerank_chunks(
    query: str,
    chunks: list[str],
    top_k: int = 3,
    min_score: float = 0.0,
) -> list[RerankHit]:
    """Rerank a list of chunks (e.g. library candidates) by BM25.

    Args:
        query: user question
        chunks: candidate text chunks (could be the top-8 by keyword
                overlap, then BM25 picks the best 3)
        top_k: how many to return
        min_score: drop hits below this score (0 = keep all)

    Returns:
        Sorted RerankHit list.
    """
    if not chunks:
        return []
    r = BM25Reranker(chunks)
    hits = r.rerank(query, top_k=max(1, top_k))
    if min_score > 0:
        hits = [h for h in hits if h.score >= min_score]
    return hits


def rerank_library(
    query: str,
    library,
    candidates: int = 8,
    top_k: int = 3,
) -> list[dict]:
    """Upgrade a Library.search(): pull keyword candidates, BM25-rerank them.

    The Library's own search already scores by word overlap; BM25 then
    re-ranks the top candidates so the single most relevant chunk wins.
    Returns the same result dicts as Library.search but reordered.
    """
    try:
        hits = library.search(query, limit=candidates)
    except Exception:
        return []
    if not hits:
        return []
    chunks = [h["text"] for h in hits]
    r = BM25Reranker(chunks)
    ranked = r.rerank(query, top_k=top_k)
    out = []
    for hit in ranked:
        original = hits[hit.index]
        original["score"] = round(original.get("score", 0) * (1 + hit.score), 3)
        out.append(original)
    return out


__all__ = [
    "BM25Reranker",
    "RerankHit",
    "rerank_chunks",
    "rerank_library",
]
