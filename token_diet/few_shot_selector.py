"""Few-shot Selector — pick the most relevant examples from a bank.

Model-agnostic: no embedding model required. Uses keyword overlap for
relevance scoring. With optional embeddings, switches to cosine similarity.

A bank of 20 examples costs ~2000 tokens. Picking the top 3 saves ~85%.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Example:
    """One few-shot example."""

    input: str  # user question
    output: str  # expected answer
    tags: list[str] = field(default_factory=list)  # keywords for filtering


_WORD = re.compile(r"\w+")


class FewShotSelector:
    """Select top-K examples from a bank by relevance to the query.

    Works without embeddings (keyword overlap) and with embeddings
    (cosine similarity) when available. Falls back gracefully.
    """

    def __init__(
        self,
        bank: list[Example] | None = None,
        embed: Callable[[str], list[float]] | None = None,
        max_examples: int = 3,
        min_overlap: int = 1,  # minimum keyword overlap to be relevant
    ):
        self.bank = bank or []
        self.embed = embed
        self.max_examples = max_examples
        self.min_overlap = min_overlap

    def add(self, example: Example) -> None:
        self.bank.append(example)

    def select(self, query: str) -> list[Example]:
        """Return top-K most relevant examples for this query."""
        if not self.bank:
            return []

        if self.embed:
            return self._select_by_embedding(query)
        return self._select_by_keywords(query)

    # -- keyword-based (no embedding model needed) ----------------------------

    def _select_by_keywords(self, query: str) -> list[Example]:
        query_words = set(_WORD.findall(query.lower()))
        if not query_words:
            return self.bank[: self.max_examples]

        scored: list[tuple[float, Example]] = []
        for ex in self.bank:
            input_words = set(_WORD.findall(ex.input.lower()))
            tag_words = set(_WORD.findall(" ".join(ex.tags).lower()))
            all_words = input_words | tag_words
            if not all_words:
                continue
            overlap = len(query_words & all_words)
            if overlap >= self.min_overlap:
                # Score: Jaccard similarity
                jaccard = overlap / len(query_words | all_words)
                # Bonus for tag matches
                tag_overlap = len(query_words & tag_words)
                score = jaccard + 0.5 * tag_overlap / max(1, len(tag_words))
                scored.append((score, ex))

        scored.sort(key=lambda x: -x[0])
        return [ex for _, ex in scored[: self.max_examples]]

    # -- embedding-based (optional, if you have an embedding model) -----------

    def _select_by_embedding(self, query: str) -> list[Example]:
        assert self.embed is not None
        q_vec = self.embed(query)

        def _dot(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b))

        scored: list[tuple[float, Example]] = []
        for ex in self.bank:
            in_vec = self.embed(ex.input)  # type: ignore[misc]
            sim = _dot(q_vec, in_vec) / (
                (sum(x * x for x in q_vec) ** 0.5)
                * (sum(x * x for x in in_vec) ** 0.5)
                + 1e-10
            )
            # Bonus for tag overlap
            tag_words = set(_WORD.findall(" ".join(ex.tags).lower()))
            query_words = set(_WORD.findall(query.lower()))
            tag_bonus = len(tag_words & query_words) / max(1, len(tag_words)) * 0.3
            scored.append((sim + tag_bonus, ex))

        scored.sort(key=lambda x: -x[0])
        return [ex for _, ex in scored[: self.max_examples]]

    def render(self, examples: list[Example], separator: str = "\n\n") -> str:
        """Render selected examples as a prompt string."""
        parts: list[str] = []
        for ex in examples:
            parts.append(f"Q: {ex.input}\nA: {ex.output}")
        return separator.join(parts)
