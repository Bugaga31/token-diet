"""efficient_thinking — 2026 reasoning techniques that make LLMs SMARTER for FEWER tokens.

Reverse-engineered from three 2026 papers + lab practice:

1. CHAIN-OF-DRAFT (arXiv:2502.18600, Silei Xu et al.)
   Keep each reasoning step to ~5 words — only equations, shorthand,
   critical transformations. Reduces reasoning tokens ~80% while
   keeping 90%+ of CoT accuracy. 3 parallel drafts cost LESS than
   one verbose chain (3×40 vs 1×200 tokens) AND give self-consistency.

2. OVERTHINKING DETECTOR (arXiv:2604.10739, "When More Thinking Hurts")
   Test-time compute has an inverted-U: beyond ~1500-2000 tokens on
   easy problems, marginal utility goes NEGATIVE. The strongest signal
   (r=0.78) is ANSWER OSCILLATION — changing intermediate conclusions
   repeatedly. Detect it → force early exit.

3. ADAPTIVE EFFORT (arXiv:2603.00578, Draft-Thinking)
   The model itself chooses Draft vs Deep mode per problem. We do this
   deterministically via complexity scoring (no extra calls).

4. ANSWER-FIRST SELF-CALIBRATION
   Tentative answer → 1-sentence verification → final. Zero-shot
   accuracy boost without longer reasoning.

Token economics: CoD alone cuts 80% of reasoning tokens on every
reasoning-heavy request. For the people. For the planet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

try:
    from .core import count_tokens
except ImportError:  # pragma: no cover
    from core import count_tokens  # type: ignore[no-redef]

# ── Chain-of-Draft ───────────────────────────────────────────────────────────


CHAIN_OF_DRAFT_PROMPT = """Solve the problem step by step, but keep each intermediate step to a MAXIMUM of 5 words — only essential equations, shorthand notes, or critical transformations.

Example:
Q: Jason had 20 lollipops. He gave Denny some. Now he has 12. How many did he give?
Draft:
20 - 12 = 8
Final: 8

Follow this style for the actual question. No verbose explanations."""


def chain_of_draft_prompt(question: str) -> str:
    """Build a Chain-of-Draft prompt: minimal reasoning, big savings."""
    return f"{CHAIN_OF_DRAFT_PROMPT}\n\nQ: {question}\nDraft:"


def chain_of_draft_compress(trace: str) -> tuple[str, int, int]:
    """Compress an existing verbose reasoning trace into draft style.

    Keeps lines with equations/numbers/symbols, drops filler sentences.
    Returns (draft, tokens_before, tokens_after).
    """
    before = count_tokens(trace)
    lines = trace.split("\n")
    kept: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # keep if it has math/numbers/code or is already short
        if re.search(r"[0-9=+\-*/%<>]|->|⇒|→|=", s) or len(s) < 40:
            kept.append(s)
    draft = "\n".join(kept)[:2000]
    after = count_tokens(draft)
    if after >= before:
        return trace, before, before
    return draft, before, after


# ── Overthinking detector ────────────────────────────────────────────────────


@dataclass
class OverthinkingResult:
    """Verdict on whether a reasoning trace is overthinking."""
    is_overthinking: bool = False
    oscillation_count: int = 0
    token_count: int = 0
    reason: str = ""

    def render(self) -> str:
        if self.is_overthinking:
            return (f"⚠️ OVERTHINKING: {self.oscillation_count} колебаний, "
                    f"{self.token_count} токенов — {self.reason}")
        return f"✓ норм ({self.token_count} токенов, {self.oscillation_count} колебаний)"


# Signals of answer oscillation: "wait, no", "actually", "on second thought",
# "hmm", "that's wrong", "let me reconsider", "correcting myself", "no wait"
_OSCILLATION_RE = re.compile(
    r"\b(?:wait|no wait|actually|on second thought|hmm|let me reconsider|"
    r"correct(?:ing|ed)? myself|that'?s wrong|i was wrong|never mind|"
    r"scratch that|reconsider|rethink|hold on)\b",
    re.IGNORECASE,
)


def detect_overthinking(
    trace: str,
    threshold_oscillations: int = 3,
    threshold_tokens: int = 1500,
) -> OverthinkingResult:
    """Detect overthinking in a reasoning trace (oscillation + length).

    Based on the Overthinking paper: answer oscillation (r=0.78) and
    excessive length are the two strongest signals that more thinking
    now HURTS. Call this before spending more tokens on a retry.
    """
    tokens = count_tokens(trace)
    osc = len(_OSCILLATION_RE.findall(trace.lower()))
    is_over = osc >= threshold_oscillations and tokens >= threshold_tokens
    if is_over:
        reason = ("колебания ответов + длинная цепочка — принудительный ранний "
                  "выход, дальнейшее размышление снизит точность")
    else:
        reason = ""
    return OverthinkingResult(
        is_overthinking=is_over,
        oscillation_count=osc,
        token_count=tokens,
        reason=reason,
    )


# ── Adaptive effort ──────────────────────────────────────────────────────────


def estimate_effort(question: str) -> str:
    """Deterministically classify reasoning effort: draft | standard | deep.

    draft: simple lookup/single-step (answer directly, ~0 reasoning)
    standard: routine multi-step (short draft-style reasoning)
    deep: symbolic/math/multi-constraint (full step-by-step allowed)
    """
    q = question.lower()
    depth = 0
    # complexity signals: comparison / conditional / multi-step
    if any(k in q for k in ["если", "if ", "then", "otherwise", "сравни",
                            "сравнить", "compare", "which", "сколько",
                            "what is the"]):
        depth += 1
    if any(k in q for k in ["выбери", "выбрать", "recommend", "лучший",
                            "best", "оптималь"]):
        depth += 1
    # symbolic / proof / calculation: heavy reasoning
    if any(k in q for k in ["докажи", "prove", "объясни почему",
                            "explain why"]):
        depth += 2
    if any(k in q for k in ["рассчитай", "calculate", "реши", "solve",
                            "уравнени", "equation", "=", "+", "-", "*"]):
        depth += 2
    if len(re.findall(r"\d+", q)) >= 3:
        depth += 1
    if len(q.split()) > 25:
        depth += 1

    if depth <= 1:
        return "draft"
    if depth <= 3:
        return "standard"
    return "deep"


_EFFORT_PROMPTS = {
    "draft": (
        "This is a straightforward question. Answer directly with the "
        "result — no step-by-step reasoning needed."
    ),
    "standard": (
        "Solve concisely. Max 5 words per reasoning step, only the "
        "essential transformations, then the final answer."
    ),
    "deep": (
        "This problem requires careful step-by-step reasoning. Show the "
        "full chain of thought with all intermediate results, then the "
        "final answer."
    ),
}


def adaptive_effort_prompt(question: str) -> tuple[str, str]:
    """Return (enhanced_question, mode) with the right effort instruction."""
    mode = estimate_effort(question)
    return f"{_EFFORT_PROMPTS[mode]}\n\nQ: {question}", mode


# ── Answer-first self-calibration ────────────────────────────────────────────


ANSWER_FIRST_PROMPT = """Answer-first self-calibration:
1. Give your immediate tentative answer.
2. Write ONE short verification sentence checking its validity.
3. If the check fails, correct it immediately and give the final answer.

Tentative Answer: ...
Verification Check: ...
Final Answer: ..."""


def answer_first_prompt(question: str) -> str:
    """Build an answer-first prompt (accuracy boost, no extra length)."""
    return f"{ANSWER_FIRST_PROMPT}\n\nQ: {question}"


# ── composite pipeline ───────────────────────────────────────────────────────


def optimize_thinking(question: str, mode: str = "auto") -> dict[str, Any]:
    """One-call: pick the right reasoning strategy for a question.

    auto → draft/standard/deep by complexity. Returns the prompt plus
    diagnostics (mode, estimated savings vs verbose CoT).
    """
    if mode == "auto":
        prompt, chosen = adaptive_effort_prompt(question)
    else:
        chosen = mode if mode in _EFFORT_PROMPTS else "standard"
        prompt = f"{_EFFORT_PROMPTS[chosen]}\n\nQ: {question}"

    # savings estimate: draft vs verbose CoT
    savings_est = {"draft": 80, "standard": 50, "deep": 0}[chosen]
    return {
        "mode": chosen,
        "prompt": prompt,
        "estimated_reasoning_savings_pct": savings_est,
        "note": ("Chain-of-Draft экономит до 80% токенов рассуждений "
                 "при 90%+ точности CoT"),
    }


__all__ = [
    "ANSWER_FIRST_PROMPT",
    "CHAIN_OF_DRAFT_PROMPT",
    "OverthinkingResult",
    "adaptive_effort_prompt",
    "answer_first_prompt",
    "chain_of_draft_compress",
    "chain_of_draft_prompt",
    "detect_overthinking",
    "estimate_effort",
    "optimize_thinking",
]
