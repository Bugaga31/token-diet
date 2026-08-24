"""Embedding Cache v2 — semantic response cache with confidence scoring.

Roadmap Month 2 W1. Answers one question: *can we return the cached answer
without risk?*

v1 (`core.SemanticCache`) is a binary similarity threshold: above it you get
the answer, below it you get None. That is unsafe in both directions — a
threshold low enough to be useful lets "price of AAPL at 180" serve a cached
"price of AAPL at 175" answer.

v2 combines several independent signals into a calibrated confidence:

1. Vector similarity — cosine over hashed char n-gram embeddings (built-in,
   pure Python, no GPU, no API). Deterministic across processes (hashlib,
   not salted ``hash()``) so the cache survives restarts.
2. Lexical Jaccard — content-word overlap, catches what bag-of-ngrams misses.
3. Numeric agreement — every number in the new question must appear in the
   cached question. Mismatch is a hard veto: numbers are facts, and facts
   are exactly what a cache must not invent.
4. Entity overlap — tickers/names must agree.

Decisions: EXACT (normalized match), SAFE (confident), RISKY (plausible but
not proven — caller decides), MISS. Volatile questions (time, weather,
live prices, personal state) are never cached and never served.

Philosophy matches the rest of token-diet: transparent, auditable statistics
instead of a proprietary black box.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

try:
    from .core import count_tokens
except ImportError:  # standalone use (tests, scripts with token-diet-lib on path)
    from core import count_tokens  # type: ignore[no-redef]


# ── Volatile questions ──────────────────────────────────────────────

_VOLATILE_PATTERNS = [
    re.compile(r"\b(?:now|today|current|latest|right now|at the moment|live)\b", re.IGNORECASE),
    re.compile(r"\b(?:сейчас|сегодня|текущ|последн|актуаль|свеж|в реальном времени)\w*", re.IGNORECASE),
    re.compile(r"\b(?:weather|forecast|погод\w*|прогноз)\b", re.IGNORECASE),
    re.compile(r"\b(?:price|quote|котировк\w*|курс)\b.*\?", re.IGNORECASE),
    re.compile(r"\b(?:my|мой|моя|моё|мои)\b", re.IGNORECASE),
]

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
# Words worth comparing as entities: long enough, capitalized mid-sentence,
# or ALLCAPS ticker-like.
_ENTITY_RE = re.compile(
    r"(?<!^)(?<![.!?]\s)(?<![,\s] )\b[A-ZА-ЯЁ][a-zа-яё]{2,}\b|\b[A-ZА-ЯЁ]{2,6}\b"
)

_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have how i in is it its of on or "
    "that the this to was what when where which who will with you your does do "
    "не и в на с что как по за из у же ли бы а но до от об о мне ты он она они "
    "мы вы это там так вот если когда который который есть был была было".split()
)

# Light suffix-stripping stemmer (longest first). Not Snowball — deliberately
# dumber and dependency-free: strip one inflection, keep >= 3 chars of stem.
_CYR_ENDINGS = sorted(
    (
        "иями", "ями", "ами", "иях", "ах", "ях", "ой", "ый", "ий", "ая", "яя",
        "ое", "ее", "ые", "ие", "ом", "ем", "ым", "им", "ов", "ев", "ей", "ую",
        "юю", "ил", "ыл", "ал", "ял", "ет", "ут", "ют", "ат", "ят", "ть", "ся",
        "у", "ю", "а", "я", "ы", "и", "е", "о", "ь",
    ),
    key=len,
    reverse=True,
)
_CYR_RE = re.compile(r"[а-яё]")


def stem_ru(word: str) -> str:
    """Strip one Russian inflection ('годы'/'году'→'год', 'писала'→'писал').

    Only words of length >= 4 are touched, and at least 3 characters always
    remain. Consonant-final stems ('пушкин') pass through untouched.
    """
    if len(word) < 4 or not _CYR_RE.search(word):
        return word
    for end in _CYR_ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[: len(word) - len(end)]
    return word


def extract_numbers(text: str) -> set[str]:
    """Normalized numbers in text ('3.5' == '3,5'; trailing zeros kept apart)."""
    return {m.group().replace(",", ".") for m in _NUMBER_RE.finditer(text)}


def extract_entities(text: str) -> set[str]:
    """Candidate entities: capitalized words and ALLCAPS tickers."""
    return {m.group() for m in _ENTITY_RE.finditer(text)}


def content_words(text: str) -> set[str]:
    """Stemmed content words minus stopwords — the lexical-overlap vocabulary."""
    return {
        stem_ru(w) for w in re.findall(r"[a-zа-яё0-9]+", text.lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


# ── Built-in embedder ───────────────────────────────────────────────


class HashedNGramEmbedder:
    """Deterministic hashed stem + word embedding. No dependencies.

    Fixed dimension, L2-normalized, stable across processes (md5 of the
    token, not salted ``hash()``) — that stability is what makes the JSON
    persistence meaningful.

    v2 design: raw char trigrams are gone on purpose. On short questions they
    are noise — shared function-word grams ("ени", "го ") outrank meaning, so
    "писал" vs "написал" paraphrases scored below unrelated texts that merely
    shared morphology. Instead each content word contributes:

    - its stem, weight 5 (morphology-insensitive core of the meaning);
    - a 3-char stem prefix, weight 2 (catches году/годы, каком/какие);
    - the exact surface form, weight 1 (a precision tiebreak).

    Words weigh ~8x more than any single surface variation, which is exactly
    the prior a Q&A cache wants: same words reshuffled ≈ same question.
    """

    STEM_WEIGHT = 5
    PREFIX_WEIGHT = 2
    EXACT_WEIGHT = 1

    def __init__(self, dim: int = 256, ngram_size: int = 3):
        if dim < 16:
            raise ValueError("dim must be >= 16")
        self.dim = dim
        self.ngram_size = ngram_size  # kept for API compatibility; unused in v2

    def _tokens(self, text: str) -> list[str]:
        """Weighted token stream: stems dominate, prefixes bridge, forms tie."""
        norm = re.sub(r"\s+", " ", text.lower()).strip()
        out: list[str] = []
        for w in re.findall(r"[a-zа-яё0-9]+", norm):
            if len(w) <= 2 or w in _STOPWORDS:
                continue
            s = stem_ru(w)
            out.extend([s] * self.STEM_WEIGHT)
            if len(s) > 3:
                out.extend([s[:3]] * self.PREFIX_WEIGHT)
            if s != w:
                out.append(w)
        return out

    def _indices(self, text: str) -> list[int]:
        digest = hashlib.md5
        return [
            int.from_bytes(digest(g.encode("utf-8")).digest()[:4], "little") % self.dim
            for g in self._tokens(text)
        ]

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for idx in self._indices(text):
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return num / (na * nb)


# ── Decisions & entries ─────────────────────────────────────────────


class Decision(NamedTuple):
    """Outcome of a cache lookup.

    action: EXACT | SAFE | RISKY | MISS — only EXACT/SAFE should be served
    automatically; RISKY means "similar enough to mention, not to trust".
    """
    action: str
    answer: str | None
    confidence: float
    reason: str
    similarity: float


@dataclass
class CacheEntry:
    question: str
    answer: str
    created: float
    hits: int = 0
    sparse_vector: dict[str, float] = field(default_factory=dict)


@dataclass
class CacheStats:
    lookups: int = 0
    exact_hits: int = 0
    safe_hits: int = 0
    risky: int = 0
    misses: int = 0
    refused_puts: int = 0
    tokens_saved: int = 0

    @property
    def hit_rate(self) -> float:
        served = self.exact_hits + self.safe_hits
        return served / self.lookups if self.lookups else 0.0


# ── The cache ───────────────────────────────────────────────────────


class EmbeddingCache:
    """Semantic cache that scores confidence instead of trusting a threshold.

    Signals combine into confidence = 0.60*similarity + 0.25*jaccard +
    0.15*entity_overlap, then guards apply:

    - numeric mismatch  → confidence capped at 0.35 (never SAFE);
    - entity mismatch   → confidence capped at 0.45;
    - volatile question → never cached, never served.

    The similarity gate alone is deliberately loose (default 0.65): v1 proved
    a binary threshold is unsafe in both directions, so here the *composite*
    confidence plus hard fact-vetoes do the guarding. A "175 vs 180 dollars"
    pair scores ~0.9 similarity and still dies at the numeric veto.

    Args mirror v1 where possible: pass your own ``embed`` (an API client)
    or use the built-in :class:`HashedNGramEmbedder`.
    """

    SIMILARITY_FLOOR = 0.55   # below this — MISS regardless of anything
    NUMERIC_VETO_CAP = 0.35
    ENTITY_MISMATCH_CAP = 0.45

    def __init__(
        self,
        embed: Callable[[str], list[float]] | None = None,
        dim: int = 256,
        similarity_threshold: float = 0.65,
        min_confidence: float = 0.72,
        ttl_seconds: float = 3600.0,
        max_entries: int = 512,
        path: str | Path | None = None,
        volatile_patterns: list[re.Pattern[str]] | None = None,
    ):
        self.embed = embed or HashedNGramEmbedder(dim=dim).embed
        # With a custom embedder the real dimension is unknown until the first
        # vector arrives; infer it instead of trusting ``dim``.
        self._dim = 0 if embed is not None else dim
        self.similarity_threshold = similarity_threshold
        self.min_confidence = min_confidence
        self.ttl_seconds = ttl_seconds
        self.max_entries = max(1, max_entries)
        self.path = Path(path) if path else None
        self.volatile_patterns = (
            volatile_patterns if volatile_patterns is not None else _VOLATILE_PATTERNS
        )
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self.stats = CacheStats()

    # ── Public API ──────────────────────────────────────────────────

    @staticmethod
    def normalize(text: str) -> str:
        text = re.sub(r"[^\w\s]", " ", text.lower())
        return " ".join(text.split())

    def is_volatile(self, question: str) -> bool:
        """Questions about now/live/personal state must never be cached."""
        return any(p.search(question) for p in self.volatile_patterns)

    def put(self, question: str, answer: str) -> bool:
        """Store Q→A. Returns False when the pair is unsafe to cache."""
        if not question.strip() or not answer.strip():
            return False
        if self.is_volatile(question):
            self.stats.refused_puts += 1
            return False

        key = self.normalize(question)
        vector = self.embed(question)
        self._dim = max(self._dim, len(vector))
        entry = CacheEntry(
            question=question,
            answer=answer,
            created=time.time(),
            sparse_vector={str(i): round(v, 4) for i, v in enumerate(vector) if v},
        )
        self._entries.pop(key, None)  # refresh position for LRU
        self._entries[key] = entry

        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return True

    def lookup(self, question: str) -> Decision:
        """Score the best candidate and classify the risk of serving it."""
        self.stats.lookups += 1

        key = self.normalize(question)
        exact = self._entries.get(key)
        if exact is not None and not self._expired(exact):
            exact.hits += 1
            self.stats.exact_hits += 1
            self.stats.tokens_saved += count_tokens(exact.answer)
            return Decision("EXACT", exact.answer, 1.0, "normalized match", 1.0)

        best: tuple[float, CacheEntry] | None = None
        q_vec = self.embed(question)
        q_nums = extract_numbers(question)
        q_words = content_words(question)
        q_entities = extract_entities(question)

        for entry in self._entries.values():
            if self._expired(entry):
                continue
            sim = cosine(q_vec, self._dense(entry, len(q_vec)))
            if best is None or sim > best[0]:
                best = (sim, entry)

        if best is None or best[0] < self.SIMILARITY_FLOOR:
            self.stats.misses += 1
            sim = best[0] if best else 0.0
            return Decision("MISS", None, 0.0, "no similar entry", sim)

        sim, entry = best
        confidence = self._confidence(
            sim, question, entry.question, q_nums, q_words, q_entities,
        )
        reason = f"sim={sim:.2f}"

        if confidence >= self.min_confidence and sim >= self.similarity_threshold:
            entry.hits += 1
            self.stats.safe_hits += 1
            self.stats.tokens_saved += count_tokens(entry.answer)
            return Decision("SAFE", entry.answer, round(confidence, 3), reason, round(sim, 3))

        action = "RISKY" if confidence >= self.NUMERIC_VETO_CAP else "MISS"
        if action == "RISKY":
            self.stats.risky += 1
        else:
            self.stats.misses += 1
        return Decision(action, None, round(confidence, 3), reason, round(sim, 3))

    def save(self) -> Path | None:
        """Persist entries (JSON). Vectors stored sparsely to keep it small."""
        if not self.path:
            return None
        payload = {
            "version": 2,
            "saved_at": time.time(),
            "entries": [
                {
                    "q": e.question,
                    "a": e.answer,
                    "created": e.created,
                    "hits": e.hits,
                    "vec": e.sparse_vector,
                }
                for e in self._entries.values()
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.path

    def load(self) -> int:
        """Load persisted entries. Returns how many were restored."""
        if not self.path or not self.path.exists():
            return 0
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        loaded = 0
        for item in raw.get("entries", []):
            if time.time() - item["created"] > self.ttl_seconds:
                continue
            entry = CacheEntry(
                question=item["q"],
                answer=item["a"],
                created=item["created"],
                hits=item.get("hits", 0),
                sparse_vector=item.get("vec", {}),
            )
            self._entries[self.normalize(entry.question)] = entry
            loaded += 1
            if entry.sparse_vector:
                self._dim = max(self._dim, 1 + max(int(i) for i in entry.sparse_vector))
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return loaded

    def clear(self) -> None:
        self._entries.clear()
        self.stats = CacheStats()

    def __len__(self) -> int:
        return len(self._entries)

    # ── Internals ───────────────────────────────────────────────────

    def _expired(self, entry: CacheEntry) -> bool:
        return time.time() - entry.created > self.ttl_seconds

    def _dense(self, entry: CacheEntry, dim: int) -> list[float]:
        """Materialize the sparse vector at the query's dimension.

        The query vector sets the length: a custom embedder may return
        vectors whose size differs from the constructor ``dim``, and cosine()
        refuses mismatched lengths (returning 0.0 — an invisible MISS).
        """
        vec = [0.0] * dim
        for idx_str, weight in entry.sparse_vector.items():
            idx = int(idx_str)
            if idx < dim:
                vec[idx] = weight
        return vec

    def _confidence(
        self,
        similarity: float,
        question: str,
        cached_question: str,
        q_nums: set[str],
        q_words: set[str],
        q_entities: set[str],
    ) -> float:
        c_nums = extract_numbers(cached_question)
        c_words = content_words(cached_question)
        c_entities = extract_entities(cached_question)

        jaccard = (
            len(q_words & c_words) / len(q_words | c_words)
            if q_words | c_words else 0.0
        )
        entity_overlap = (
            len(q_entities & c_entities) / len(q_entities | c_entities)
            if q_entities | c_entities else 1.0
        )

        confidence = 0.60 * similarity + 0.25 * jaccard + 0.15 * entity_overlap

        # Hard vetoes — facts beat vibes.
        if q_nums != c_nums:
            confidence = min(confidence, self.NUMERIC_VETO_CAP)
        elif q_entities and c_entities and not (q_entities & c_entities):
            confidence = min(confidence, self.ENTITY_MISMATCH_CAP)

        # Volatile questions can sneak past put(); never serve them either.
        if self.is_volatile(question):
            confidence = min(confidence, self.NUMERIC_VETO_CAP)
        return confidence


# ── Convenience ─────────────────────────────────────────────────────


def cached_answer(
    cache: EmbeddingCache, question: str, compute: Callable[[str], str],
) -> tuple[str, Decision]:
    """Return cached answer when safe, otherwise compute and store.

    RISKY decisions deliberately fall through to compute(): plausible-but-
    unproven is exactly when an LLM call is still worth paying for.
    """
    decision = cache.lookup(question)
    if decision.action in ("EXACT", "SAFE"):
        return decision.answer or "", decision
    answer = compute(question)
    cache.put(question, answer)
    return answer, decision
