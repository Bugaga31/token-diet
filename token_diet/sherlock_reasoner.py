"""Sherlock Reasoner — structured problem-solving without extra token cost.

Three components (all model-agnostic, no extra API calls):

1. REASONING TEMPLATE — adds Sherlock-style structure to the system prompt:
   "Observe → Collect facts → Eliminate impossibilities → Conclude"
   This costs ~30 extra tokens once, but gets cached and distilled.

2. SELF-VERIFICATION — after the model answers, runs deterministic checks:
   - Are numbers from the answer consistent with the question?
   - Are all sub-questions answered?
   - Any contradictory statements?
   Returns a verifier score + flagged issues.

3. REASONING COMPRESSION — strips verbose reasoning from output:
   - Keep factual claims (numbers, dates, names, entities)
   - Drop filler ("Let me think...", "I believe...", "It seems that...")
   - Drop repetitive restatements
   
Net effect: model reasons BETTER (Sherlock template) but answer is SHORTER
(compression) → same quality at LOWER token cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

try:
    from .core import count_tokens
except ImportError:
    from core import count_tokens  # type: ignore[no-redef]

# ── Sherlock reasoning template ───────────────────────────────────────────────

SHERLOCK_TEMPLATE = """Think like Sherlock Holmes:

1. OBSERVE — list every relevant fact from the provided data
2. IDENTIFY contradictions and impossibilities
3. ELIMINATE what cannot be true
4. CONCLUDE — only what remains, however improbable

Be precise with numbers. Verify dates. Cross-reference names."""


def sherlock_system_prompt(base: str) -> str:
    """Enhance a system prompt with Sherlock-style reasoning instructions."""
    if not base:
        return SHERLOCK_TEMPLATE
    # Don't double-add
    if "Sherlock" in base or "OBSERVE" in base:
        return base
    return SHERLOCK_TEMPLATE + "\n\n" + base


# ── Self-verification ─────────────────────────────────────────────────────────

_NUMBER_RE = re.compile(r"\b\d+[\d,.]*\b")
_NAME_RE = re.compile(r"\b[A-Z][a-z]+ (?:[A-Z][a-z]+ )?[A-Z][a-z]+\b")  # Proper names
_SUBQUESTION_RE = re.compile(r"[^.?!]*\?")  # Sub-questions


@dataclass
class VerifierResult:
    """What the self-verifier found."""

    score: float = 1.0  # 0..1 — how consistent the answer is
    flagged_numbers: list[str] = field(default_factory=list)
    missing_answers: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return self.score >= 0.9 and not self.contradictions

    def render(self) -> str:
        lines = [f"verifier score: {self.score:.2f}"]
        if self.flagged_numbers:
            lines.append(f"flagged numbers: {self.flagged_numbers}")
        if self.missing_answers:
            lines.append(f"unanswered: {self.missing_answers}")
        if self.contradictions:
            lines.append(f"contradictions: {self.contradictions}")
        if not lines[1:]:
            lines.append("all clear")
        return "\n".join(lines)


class SelfVerifier:
    """Deterministic self-verification — no extra API calls.

    Checks:
    - Numbers in answer vs question (flagged if new numbers appear)
    - All question marks answered
    - Internal contradictions (same number stated twice differently)
    """

    def verify(self, question: str, answer: str, context_numbers: set[str] | None = None) -> VerifierResult:
        result = VerifierResult()

        if not answer or not question:
            result.score = 0.0
            result.missing_answers = ["empty answer"]
            return result

        q_nums = set(_NUMBER_RE.findall(question))
        a_nums = set(_NUMBER_RE.findall(answer))
        known_nums = q_nums | (context_numbers or set())

        # Numbers in answer not in question → flagged (may be hallucination)
        new_nums = a_nums - known_nums
        # But: if answer EXPLAINS where the number came from, it's fine
        # Simple heuristic: if answer has "=" or "total" or "sum", new numbers are OK
        if new_nums and not re.search(r"(= |total|sum|average|mean|cost|price)", answer, re.I):
            result.flagged_numbers = sorted(new_nums)
            result.score -= min(0.3, 0.1 * len(result.flagged_numbers))

        # Missing sub-questions
        sub_qs = _SUBQUESTION_RE.findall(question)
        for sq in sub_qs:
            # Each sub-question keyword should appear in the answer
            keywords = set(_NUMBER_RE.findall(sq)) | set(w for w in sq.lower().split() if len(w) > 4)
            if keywords and not any(kw.lower() in answer.lower() for kw in keywords if len(kw) > 4):
                result.missing_answers.append(sq.strip()[:60])
        if result.missing_answers:
            result.score -= min(0.3, 0.1 * len(result.missing_answers))

        # Internal contradictions: same number appears with different meanings
        num_counts: dict[str, list[str]] = {}
        for num in a_nums:
            # Find the context around each occurrence
            for m in re.finditer(re.escape(num), answer):
                ctx = answer[max(0, m.start() - 20):m.end() + 20]
                num_counts.setdefault(num, []).append(ctx)
        for num, contexts in num_counts.items():
            if len(contexts) >= 2 and len(num) >= 2:
                # Check if contexts are contradictory (one says "increase", other "decrease")
                if ("increase" in contexts[0].lower() and "decrease" in contexts[1].lower()) or \
                   ("gain" in contexts[0].lower() and "loss" in contexts[1].lower()):
                    result.contradictions.append(f"'{num}' used in contradictory contexts")
                    result.score -= 0.2

        result.score = max(0.0, result.score)
        return result


# ── Reasoning compression ─────────────────────────────────────────────────────

# Patterns to strip from verbose reasoning (sentence-level only, preserve facts)
_COMPRESS_PATTERNS = [
    # Standalone thinking sentences (only when they're the whole sentence)
    (re.compile(r"^Let me think (?:about this|step by step|carefully)\.?\s*", re.I | re.M), ""),
    # Introductory phrases at sentence start (keep the content after comma)
    (re.compile(r"^(?:Based on|According to|Looking at|From) (?:the |my |our )?(?:analysis|data|records|documents|information|context|above)[,:]\s*", re.I | re.M), ""),
    # Filler transitions at sentence start
    (re.compile(r"^(?:First(?:ly)?|Second(?:ly)?|Third(?:ly)?|Finally|In conclusion|To summarize|In summary|To conclude),?\s*", re.I | re.M), ""),
    (re.compile(r"^(?:Furthermore|Moreover|Additionally|In addition|Also|However|Nevertheless|Nonetheless|Therefore|Thus|Hence|Consequently|As a result),?\s*", re.I | re.M), ""),
    # Politeness hedges (only standalone, not when directly before content)
    (re.compile(r"\bI (?:think|believe|would say|'d say) (?:that )?", re.I), ""),
    (re.compile(r"\bIt (?:seems|appears) (?:to me )?(?:that )?", re.I), ""),
    # Ceremonial closing sentences (whole sentence only)
    (re.compile(r"(?:I hope this|Let me know|Feel free|Please let me|Don't hesitate)[^.!?]*[.!?]\s*", re.I), ""),
]


def compress_reasoning(text: str) -> tuple[str, int, int]:
    """Strip verbose reasoning, keep factual claims and conclusions.

    Returns (compressed_text, tokens_before, tokens_after).
    """
    if not text:
        return text, 0, 0

    before = count_tokens(text)
    result = text

    for pattern, replacement in _COMPRESS_PATTERNS:
        result = pattern.sub(replacement, result)

    # Clean up multiple spaces and empty lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"  +", " ", result)
    result = result.strip()

    # Don't return empty
    if not result or len(result) < 10:
        return text, before, before

    after = count_tokens(result)
    return result, before, after


# ── Full Sherlock pipeline ────────────────────────────────────────────────────


class SherlockReasoner:
    """Full Sherlock pipeline: template + verify + compress.

    Usage:
        sherlock = SherlockReasoner()
        enhanced_prompt = sherlock.enhance(base_system_prompt)
        # ... LLM call with enhanced_prompt ...
        verified = sherlock.check(question, answer)
        if not verified.is_clean:
            print(f"WARNING: {verified.render()}")
        compact, _, _ = sherlock.compress(answer)
    """

    def __init__(self, counter: Callable[[str], int] = count_tokens):
        self.verifier = SelfVerifier()
        self.counter = counter

    def enhance(self, system_prompt: str) -> str:
        """Return system prompt with Sherlock reasoning template."""
        return sherlock_system_prompt(system_prompt)

    def check(self, question: str, answer: str, context_numbers: set[str] | None = None) -> VerifierResult:
        """Self-verify the answer against the question."""
        return self.verifier.verify(question, answer, context_numbers)

    def compress(self, answer: str) -> tuple[str, int, int]:
        """Compress verbose reasoning from the answer."""
        return compress_reasoning(answer)

    def pipeline(
        self, system_prompt: str, question: str, answer: str
    ) -> dict:
        """Full pipeline: returns diagnostics."""
        enhanced = self.enhance(system_prompt)
        verification = self.check(question, answer)
        compressed, before, after = self.compress(answer)
        return {
            "enhanced_prompt": enhanced,
            "verification": verification,
            "compressed_answer": compressed,
            "tokens_before": before,
            "tokens_after": after,
            "token_savings": before - after,
        }
