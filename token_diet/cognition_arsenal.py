"""Cognition Arsenal — reverse-engineered intelligence techniques.

Real prompt-level techniques that measurably improve LLM reasoning WITHOUT
changing model weights (from the research literature, reimplemented here):

1. Tree-of-Thoughts  (Yao et al., 2023)  — branch multiple reasoning paths,
   self-score each, search with backtracking.
2. Multi-Agent Debate (Liang et al., 2023) — several agents propose answers,
   critique each other over rounds, converge to consensus. Reduces
   single-model bias and hallucination.
3. Decomposed Reasoning (Khot et al., 2023) — split a hard question into
   sub-questions, solve each, assemble the final answer.
4. CognitiveRouter — THE optimization: choose the cheapest strategy that
   actually improves the answer for THIS question. Simple question → 0 extra
   tokens. Hard question → spend reasoning tokens only where they pay off.
   Maximizes "IQ per token", not raw IQ.

Everything is algorithmic (no neural models required). The only dependency
is a callable `llm_call(prompt) -> str` so it works with ANY provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .core import count_tokens
from .reasoning import self_consistency_merge

# ═══════════════════════════════════════════════════════════════════════════════
# 1. TREE-OF-THOUGHTS — search over reasoning paths
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ThoughtNode:
    """One node in the thought tree."""
    text: str
    score: float = 0.0
    depth: int = 0
    children: list["ThoughtNode"] = field(default_factory=list)


@dataclass
class TotResult:
    """Result of a Tree-of-Thoughts search."""
    best: ThoughtNode
    thoughts_explored: int
    calls_made: int
    confidence: float


# Score prompt: forces the model to rate progress on a 0-1 scale.
_SCORE_PROMPT = (
    "You are evaluating a step of reasoning toward solving:\n"
    "{problem}\n\n"
    "Current reasoning step:\n{thought}\n\n"
    "Rate how much progress this step makes toward a correct solution "
    "from 0.0 (useless/wrong) to 1.0 (essentially solves it).\n"
    "Output ONLY the number, no explanation."
)


def _parse_score(text: str) -> float:
    """Extract a 0-1 score from a model response."""
    m = re.search(r"(0(?:\.\d+)?|1(?:\.0+)?|\.\d+)", text)
    if not m:
        return 0.5
    try:
        val = float(m.group(1))
    except ValueError:
        return 0.5
    return max(0.0, min(1.0, val))


def tree_of_thoughts(
    problem: str,
    llm_call: Callable[[str], str],
    branches: int = 3,
    max_depth: int = 2,
    judge: Callable[[str, str], float] | None = None,
) -> TotResult:
    """Search multiple reasoning paths with self-scoring and backtracking.

    At each level, generate `branches` candidate steps, score each, keep the
    best one, and expand it one level deeper. Returns the best leaf.

    Args:
        problem: the question to solve.
        llm_call: function(prompt) -> str producing reasoning steps.
        branches: how many candidate steps per level.
        max_depth: how many levels to expand.
        judge: optional function(problem, thought) -> float score.
               Defaults to llm_call-based self-scoring.
    """
    if judge is None:
        def judge(problem: str, thought: str) -> float:
            return _parse_score(llm_call(_SCORE_PROMPT.format(
                problem=problem, thought=thought)))

    root = ThoughtNode(text=problem, depth=0)
    calls_made = 0

    def expand(node: ThoughtNode) -> ThoughtNode:
        nonlocal calls_made
        if node.depth >= max_depth:
            node.score = judge(problem, node.text)
            calls_made += 1
            return node

        best_child: ThoughtNode | None = None
        for _ in range(branches):
            child_text = llm_call(
                f"Solve step {node.depth + 1} toward this problem.\n"
                f"Problem: {problem}\n"
                f"So far: {node.text}\n"
                f"Give ONLY the next reasoning step, 1-3 sentences."
            ).strip()
            calls_made += 1
            child = ThoughtNode(text=child_text, depth=node.depth + 1)
            child.score = judge(problem, child.text)
            calls_made += 1
            node.children.append(child)
            if best_child is None or child.score > best_child.score:
                best_child = child

        # Expand the single best child (greedy beam of width 1 at next level)
        assert best_child is not None
        return expand(best_child)

    best = expand(root)
    explored = sum(1 for _ in _iter_nodes(root))
    return TotResult(best=best, thoughts_explored=explored,
                     calls_made=calls_made, confidence=best.score)


def _iter_nodes(node: ThoughtNode):
    yield node
    for child in node.children:
        yield from _iter_nodes(child)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. MULTI-AGENT DEBATE — propose, critique, converge
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DebateResult:
    """Result of a multi-agent debate."""
    answers: list[str]
    final_answer: str
    rounds: int
    agreement: float  # 0-1 how much agents converged


_DEBATE_PROPOSE = (
    "You are agent #{i} of {n} solving the same problem. Be independent and\n"
    "think for yourself — do not copy others. Produce a complete answer.\n\n"
    "Problem: {problem}\n\n"
    "Your answer:"
)


_DEBATE_REVISE = (
    "You are agent #{i}. Your previous answer was:\n"
    "{mine}\n\n"
    "Here are the other agents' answers:\n{others}\n\n"
    "Critique the others: where are they wrong or incomplete? Then give your\n"
    "revised, improved final answer, incorporating anything they got right.\n\n"
    "Problem: {problem}\n\n"
    "Your revised answer:"
)


def multi_agent_debate(
    problem: str,
    llm_call: Callable[[str], str],
    agents: int = 3,
    rounds: int = 2,
) -> DebateResult:
    """Run an adversarial debate to reduce bias and hallucination.

    Args:
        problem: the question.
        llm_call: function(prompt) -> str.
        agents: number of independent agents (2-5 sensible).
        rounds: debate rounds after the initial proposals.
    """
    from .reasoning import normalize_answer

    n = max(2, min(agents, 5))
    answers: list[str] = []

    # Round 0: independent proposals
    for i in range(n):
        answers.append(llm_call(
            _DEBATE_PROPOSE.format(i=i + 1, n=n, problem=problem)
        ).strip())

    # Debate rounds
    for _ in range(rounds):
        new_answers: list[str] = []
        for i in range(n):
            others = "\n".join(
                f"Agent {j + 1}: {a}" for j, a in enumerate(answers) if j != i
            )
            new_answers.append(llm_call(
                _DEBATE_REVISE.format(
                    i=i + 1, mine=answers[i], others=others, problem=problem
                )
            ).strip())
        answers = new_answers

    # Agreement: fraction of agents whose normalized answer matches the winner
    normed = [normalize_answer(a) for a in answers]
    from collections import Counter
    counts = Counter(normed)
    winner_norm, top_count = counts.most_common(1)[0]
    final = answers[normed.index(winner_norm)]
    agreement = top_count / n

    return DebateResult(answers=answers, final_answer=final,
                        rounds=rounds, agreement=agreement)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DECOMPOSED REASONING — split, solve, assemble
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DecomposedResult:
    """Result of decomposed reasoning."""
    sub_questions: list[str]
    sub_answers: list[str]
    final_answer: str
    parts: int


_DECOMPOSE_PROMPT = (
    "To answer this question well, break it into {max_parts} or fewer clear\n"
    "sub-questions that together fully cover it. List each on its own line,\n"
    "numbered 1., 2., 3. ... Nothing else.\n\n"
    "Question: {problem}"
)


def decompose_and_solve(
    problem: str,
    llm_call: Callable[[str], str],
    max_parts: int = 4,
) -> DecomposedResult:
    """Split a hard question into sub-questions, answer each, assemble.

    Falls back to a direct single answer if the model doesn't produce a
    parseable decomposition (honest degradation).
    """
    plan = llm_call(_DECOMPOSE_PROMPT.format(
        max_parts=max_parts, problem=problem
    ))

    # Only numbered/bulleted lines count as a real decomposition. If the
    # model didn't produce a parseable list, fall back to a direct answer
    # instead of treating prose as a sub-question (honest degradation).
    subs: list[str] = []
    for line in plan.splitlines():
        m = re.match(r"^\s*(?:\d+[.)]\s*|[-*]\s+)(.+)", line)
        if m:
            subs.append(m.group(1).strip())

    subs = subs[:max_parts]

    if not subs:
        # Decomposition failed — direct answer (no pretending)
        direct = llm_call(f"Answer this question: {problem}").strip()
        return DecomposedResult(
            sub_questions=[], sub_answers=[], final_answer=direct, parts=0
        )

    answers: list[str] = []
    for q in subs:
        answers.append(llm_call(f"Answer this sub-question: {q}").strip())

    assembled = llm_call(
        "Assemble a final, complete answer to the original question using the\n"
        "sub-answers below. Be concise but do not drop any important fact.\n\n"
        "Original question: {problem}\n\n"
        "Sub-answers:\n{answers}"
    ).format(problem=problem, answers="\n".join(
        f"{i + 1}. {a}" for i, a in enumerate(answers)
    )).strip()

    return DecomposedResult(
        sub_questions=subs, sub_answers=answers,
        final_answer=assembled, parts=len(subs),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. COGNITIVE ROUTER — cheapest strategy that actually helps
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class CognitionPlan:
    """Chosen cognitive strategy for a question."""
    strategy: str          # fast | refine | sc | tot | debate | decompose
    complexity: int        # 0-5+
    estimated_extra_tokens: int
    reason: str


# Complexity signals: what makes a question worth spending tokens on.
_HARD_SIGNALS = re.compile(
    r"\b(compare|contrast|analyze|evaluate|explain why|why|how does|"
    r"what if|prove|solve|calculate|compute|design|debug|refactor|"
    r"optimize|which (is|option)|recommend|assess|risks?|trade.?offs?|"
    r"multiple|candidates|dilemma|decide|plan)\b",
    re.IGNORECASE,
)

_LONG_WORD = re.compile(r"\w{9,}")

# Estimated extra tokens per strategy (rough, from LLM call sizes).
_STRATEGY_COST: dict[str, int] = {
    "fast": 0,
    "refine": 220,
    "sc": 350,
    "tot": 480,
    "debate": 600,
    "decompose": 500,
}


def assess_complexity(question: str) -> int:
    """Heuristic complexity score (0 = trivial, 5+ = hard)."""
    if not question or not question.strip():
        return 0
    q = question.strip()
    words = len(q.split())
    score = 0
    score += min(3, len(_HARD_SIGNALS.findall(q)))
    score += 1 if len(_LONG_WORD.findall(q)) >= 2 else 0
    if words > 40:
        score += 1
    if q.count("?") > 1 or "?" not in q and words > 15:
        score += 1
    return score


def choose_cognition_strategy(
    question: str,
    budget_tokens: int,
    complexity: int | None = None,
) -> CognitionPlan:
    """Pick the cheapest strategy likely to improve THIS question.

    The rule (this is the whole point): don't burn reasoning tokens on
    questions that don't need them. IQ-per-token, not raw IQ.
    """
    c = complexity if complexity is not None else assess_complexity(question)

    if c <= 1:
        return CognitionPlan("fast", c, 0,
                             "simple question — extra reasoning would waste tokens")
    if c == 2:
        return CognitionPlan("refine", c, _STRATEGY_COST["refine"],
                             "medium — one self-refine pass improves accuracy cheaply")
    if c == 3:
        return CognitionPlan("sc", c, _STRATEGY_COST["sc"],
                             "hard — self-consistency voting beats single pass")
    if c == 4:
        return CognitionPlan("tot", c, _STRATEGY_COST["tot"],
                             "very hard — search multiple reasoning paths")
    # c >= 5
    if budget_tokens >= 700:
        return CognitionPlan("decompose", c, _STRATEGY_COST["decompose"],
                             "complex — decompose then assemble; budget allows it")
    return CognitionPlan("tot", c, _STRATEGY_COST["tot"],
                         "complex but budget-limited — ToT is more token-efficient")


def cognitive_solve(
    question: str,
    llm_call: Callable[[str], str],
    budget_tokens: int = 2000,
    plan: CognitionPlan | None = None,
    max_refine: int = 2,
) -> tuple[str, CognitionPlan, dict[str, Any]]:
    """Run the chosen cognitive strategy and return (answer, plan, stats).

    This is the unified entry point: given any question and any LLM callable,
    it decides the strategy, runs it, and reports honest stats (tokens spent,
    calls made, strategy, complexity).

    Args:
        question: user question.
        llm_call: function(prompt) -> str.
        budget_tokens: max tokens we are willing to spend on cognition.
        plan: precomputed CognitionPlan (auto if None).
        max_refine: max iterations for the refine strategy.
    """
    if plan is None:
        plan = choose_cognition_strategy(question, budget_tokens)

    stats: dict[str, Any] = {
        "strategy": plan.strategy,
        "complexity": plan.complexity,
        "calls": 0,
        "extra_tokens": 0,
        "confidence": None,
    }

    def counted(prompt: str) -> str:
        stats["calls"] += 1
        stats["extra_tokens"] += count_tokens(prompt)
        return llm_call(prompt)

    if plan.strategy == "fast":
        answer = counted(f"Answer the question. Be concise and correct.\n"
                         f"Question: {question}")
        return answer, plan, stats

    if plan.strategy == "refine":
        from .reasoning import reflexion_loop
        res = reflexion_loop(counted, question, max_iterations=max_refine)
        stats["confidence"] = 1.0 if res.passed else 0.0
        return res.refined, plan, stats

    if plan.strategy == "sc":
        # Sample multiple independent reasoning paths, majority vote.
        paths: list[str] = []
        for i in range(3):
            paths.append(counted(
                f"Reason step by step, then output 'Final answer:' followed by\n"
                f"the answer. Question: {question}"
            ))
        merged = self_consistency_merge(paths, num_samples=3)
        stats["confidence"] = merged.confidence
        return merged.winner, plan, stats

    if plan.strategy == "tot":
        res = tree_of_thoughts(question, counted, branches=2, max_depth=2)
        stats["confidence"] = res.confidence
        stats["calls"] += res.calls_made - stats["calls"]
        stats["extra_tokens"] += count_tokens(res.best.text)
        return res.best.text, plan, stats

    if plan.strategy == "debate":
        res = multi_agent_debate(question, counted, agents=3, rounds=1)
        stats["confidence"] = res.agreement
        return res.final_answer, plan, stats

    if plan.strategy == "decompose":
        res = decompose_and_solve(question, counted, max_parts=4)
        stats["confidence"] = 1.0 if res.parts > 0 else 0.0
        return res.final_answer, plan, stats

    # Unknown strategy — honest fallback to fast
    answer = counted(f"Answer the question: {question}")
    return answer, plan, stats
