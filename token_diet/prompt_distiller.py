"""Prompt Distiller — learn which system prompt parts the model ignores.

After every API call, the distiller checks word overlap between each section
of the system prompt and the model's answer. Sections with zero overlap
across N consecutive calls are candidates for removal.

This is model-agnostic: no extra API calls needed, just word overlap counting.
The Gate confirms safety before any section is actually dropped.

Usage:
    distiller = PromptDistiller(min_observations=10, drop_threshold=0.0)
    distiller.observe(system_prompt, answer)   # after each call
    trimmed = distiller.distill(system_prompt)  # get the lean version
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

try:
    from .core import count_tokens
except ImportError:
    from core import count_tokens  # type: ignore[no-redef]

_WORD = re.compile(r"\w+")


@dataclass
class SectionStats:
    """Per-section tracking across observations."""

    text: str
    tokens: int = 0
    total_observations: int = 0
    hits: int = 0  # times at least one word overlapped with answer
    last_overlap: float = 0.0  # most recent overlap ratio


class PromptDistiller:
    """Learn which prompt sections are dead weight.

    Strategy: split system prompt on double-newlines (natural sections),
    count word overlap with each answer. Sections that NEVER overlap
    after min_observations calls are candidates for removal.
    """

    def __init__(
        self,
        min_observations: int = 5,
        drop_threshold: float = 0.0,  # max overlap to consider "dead"
        counter: Callable[[str], int] = count_tokens,
    ):
        self.min_observations = min_observations
        self.drop_threshold = drop_threshold
        self.counter = counter
        self._sections: dict[int, SectionStats] = {}
        self._observations: int = 0

    def observe(self, system_prompt: str, answer: str) -> None:
        """Record which sections overlapped with this answer."""
        if not system_prompt or not answer:
            return

        sections = self._split(system_prompt)
        answer_words = set(_WORD.findall(answer.lower()))

        for i, section_text in enumerate(sections):
            if i not in self._sections:
                self._sections[i] = SectionStats(
                    text=section_text,
                    tokens=self.counter(section_text),
                )
            stats = self._sections[i]
            stats.total_observations += 1
            section_words = set(_WORD.findall(section_text.lower()))
            if section_words:
                overlap = len(section_words & answer_words) / len(section_words)
                stats.last_overlap = overlap
                if overlap > 0:
                    stats.hits += 1

        self._observations += 1

    def distill(self, system_prompt: str) -> tuple[str, str, int, int]:
        """Return (distilled_prompt, dropped_text, before_tokens, after_tokens).

        Only drops sections when we have enough observations AND the section
        has NEVER been referenced (hit count == 0).
        """
        if not system_prompt:
            return system_prompt, "", 0, 0

        if self._observations < self.min_observations:
            return system_prompt, "", self.counter(system_prompt), self.counter(system_prompt)

        sections = self._split(system_prompt)
        before_tokens = self.counter(system_prompt)

        kept: list[str] = []
        dropped: list[str] = []

        for i, text in enumerate(sections):
            stats = self._sections.get(i)
            if stats is not None and stats.total_observations >= self.min_observations:
                # Section NEVER overlapped with ANY answer → candidate for removal
                hit_rate = stats.hits / max(1, stats.total_observations)
                if hit_rate <= self.drop_threshold:
                    dropped.append(text)
                    continue
            kept.append(text)

        if not dropped:
            return system_prompt, "", before_tokens, before_tokens

        distilled = "\n\n".join(kept)
        after_tokens = self.counter(distilled)
        dropped_text = "\n\n".join(dropped)
        return distilled, dropped_text, before_tokens, after_tokens

    def stats(self) -> str:
        """Human-readable section statistics."""
        if not self._sections:
            return "(no observations)"

        lines = [f"observations={self._observations}, sections={len(self._sections)}"]
        for i, s in sorted(self._sections.items()):
            hit_rate = s.hits / max(1, s.total_observations)
            status = "DEAD" if hit_rate <= self.drop_threshold else "LIVE"
            lines.append(
                f"  [{i}] {status} hit_rate={hit_rate:.0%} "
                f"tokens={s.tokens} snippet={s.text[:60]!r}"
            )
        return "\n".join(lines)

    def reset(self) -> None:
        """Clear all observations (e.g. after prompt redesign)."""
        self._sections.clear()
        self._observations = 0

    @staticmethod
    def _split(text: str) -> list[str]:
        """Split system prompt into logical sections on double-newlines."""
        parts = re.split(r"\n{2,}", text)
        return [p.strip() for p in parts if p.strip()]
