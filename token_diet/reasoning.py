"""Reasoning Optimization — Self-Consistency + Reflexion.

Two methods to increase intelligence WITHOUT adding prompt tokens:

1. Self-Consistency (Wang et al., 2022):
   Sample multiple reasoning paths, majority vote on final answer.
   ZERO extra prompt tokens — purely inference-time optimization.

2. Reflexion (Shinn et al., 2023):
   Self-evaluate output, generate critique, retry with feedback.
   Verbal reinforcement loop without external supervision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# 1. SELF-CONSISTENCY — multiple samples, majority vote
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ConsistencyResult:
    """Result of self-consistency voting."""
    answers: list[str] = field(default_factory=list)
    reasoning_paths: list[str] = field(default_factory=list)
    winner: str = ""
    confidence: float = 0.0
    votes: dict[str, int] = field(default_factory=dict)

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.6


def extract_final_answer(text: str) -> str:
    """Extract the final answer from a reasoning chain.

    Looks for patterns like:
    - "Answer: X"
    - "The answer is X"
    - "Final result: X"
    - Last line after "---"
    - Last numbered item
    """
    # Try explicit answer markers
    patterns = [
        r'(?i)(?:final\s+)?answer\s*[:=]\s*(.+?)(?:\.\s*$|$)',
        r'(?i)the\s+answer\s+is\s+(.+?)(?:\.\s*$|$)',
        r'(?i)(?:final\s+)?result\s*[:=]\s*(.+?)(?:\.\s*$|$)',
        r'(?i)conclusion\s*[:=]\s*(.+?)(?:\.\s*$|$)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            return m.group(1).strip().rstrip('.')

    # Last non-empty line
    lines = [ln.strip() for ln in text.strip().split('\n') if ln.strip()]
    if lines:
        return lines[-1].rstrip('.')

    return text.strip()[-200:]


def normalize_answer(answer: str) -> str:
    """Normalize answer for comparison (lowercase, strip punctuation, collapse whitespace)."""
    a = answer.lower().strip()
    a = re.sub(r'[^\w\s]', '', a)
    a = re.sub(r'\s+', ' ', a).strip()
    return a


def self_consistency_merge(
    responses: list[str],
    num_samples: int = 5,
) -> ConsistencyResult:
    """Merge multiple reasoning samples via majority vote.

    Args:
        responses: list of LLM response strings
        num_samples: expected number of samples

    Returns:
        ConsistencyResult with winner and confidence.
    """
    if len(responses) < 2:
        return ConsistencyResult(
            answers=[responses[0] if responses else ""],
            winner=responses[0] if responses else "",
            confidence=1.0,
        )

    answers = [extract_final_answer(r) for r in responses]
    reasoning = responses

    # Count votes
    votes: dict[str, int] = {}
    for a in answers:
        norm = normalize_answer(a)
        # Find existing similar answer
        found = False
        for existing in list(votes.keys()):
            if norm == normalize_answer(existing) or norm in normalize_answer(existing) or normalize_answer(existing) in norm:
                votes[existing] += 1
                found = True
                break
        if not found:
            votes[a] = 1

    winner = max(votes, key=votes.get)
    confidence = votes[winner] / len(answers)

    return ConsistencyResult(
        answers=answers,
        reasoning_paths=reasoning,
        winner=winner,
        confidence=confidence,
        votes=votes,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. REFLEXION — self-evaluate, critique, retry
# ═══════════════════════════════════════════════════════════════════════════════


_SELF_EVALUATION_PROMPT = (
    "Evaluate the response below against these criteria:\n"
    "1. Factual accuracy — is every claim correct?\n"
    "2. Completeness — does it fully answer the question?\n"
    "3. Conciseness — is there filler or redundancy?\n"
    "4. Constraint compliance — does it follow all rules?\n\n"
    "Response:\n{response}\n\n"
    "Output ONLY: PASS or FAIL followed by one-sentence reason."
)


_REFINEMENT_PROMPT = (
    "Your previous response was critiqued:\n{critique}\n\n"
    "Original question: {question}\n\n"
    "Previous response:\n{previous}\n\n"
    "Provide an improved response addressing the critique."
)


@dataclass
class ReflexionResult:
    original: str
    critique: str
    refined: str
    passed: bool
    iterations: int


def reflexion_loop(
    llm_call: Any,
    question: str,
    max_iterations: int = 3,
    evaluator: Any = None,
) -> ReflexionResult:
    """Self-refine an answer through iterative self-evaluation.

    The model:
    1. Generates an answer
    2. Evaluates its own answer against criteria
    3. If FAIL, critiques and regenerates
    4. Repeats up to max_iterations times

    This improves accuracy WITHOUT external supervision —
    the model is its own judge.

    Args:
        llm_call: function(prompt) -> str that calls the LLM
        question: the original user question
        max_iterations: max refinement cycles
        evaluator: optional separate evaluator LLM (uses llm_call if None)
    """
    evaluate = evaluator if evaluator else llm_call

    # Step 1: Generate initial answer
    current_answer = llm_call(question)
    iterations = 1

    for _i in range(max_iterations):
        # Step 2: Self-evaluate
        eval_prompt = _SELF_EVALUATION_PROMPT.format(response=current_answer)
        eval_result = evaluate(eval_prompt).strip().upper()

        passed = eval_result.startswith("PASS")
        critique = eval_result.replace("PASS", "").replace("FAIL", "").strip(": -")

        if passed:
            return ReflexionResult(
                original=current_answer,
                critique=critique,
                refined=current_answer,
                passed=True,
                iterations=iterations,
            )

        # Step 3: Refine
        refine_prompt = _REFINEMENT_PROMPT.format(
            critique=critique,
            question=question,
            previous=current_answer,
        )
        current_answer = llm_call(refine_prompt)
        iterations += 1

    return ReflexionResult(
        original=current_answer,
        critique=critique,
        refined=current_answer,
        passed=False,
        iterations=iterations,
    )
