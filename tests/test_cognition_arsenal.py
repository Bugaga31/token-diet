"""Tests for cognition_arsenal — reverse-engineered intelligence techniques."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "token_diet"))

from token_diet.cognition_arsenal import (
    _parse_score,
    assess_complexity,
    choose_cognition_strategy,
    cognitive_solve,
    decompose_and_solve,
    multi_agent_debate,
    tree_of_thoughts,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers: scripted fake LLMs (deterministic, offline)
# ═══════════════════════════════════════════════════════════════════════════════


def _fake_llm_correct(prompt: str) -> str:
    """Fake LLM: if asked to score, returns a high score; else a good step."""
    if "Rate how much progress" in prompt:
        return "0.9"
    return "Consider edge cases and verify each step of the computation."


def _fake_llm_decomposer(prompt: str) -> str:
    if "break it into" in prompt:
        return "1. What is the key variable?\n2. What formula applies?\n3. What is the numeric result?"
    if "Answer this sub-question" in prompt:
        return "The key variable is x."
    if "Assemble a final" in prompt:
        return "Final assembled answer combining all parts."
    return "Generic answer."


def _fake_llm_debate(prompt: str) -> str:
    if "agent #2" in prompt or "agent #3" in prompt:
        return "Answer B: 42"
    return "Answer A: 42"


# ═══════════════════════════════════════════════════════════════════════════════
# Complexity assessment & strategy routing
# ═══════════════════════════════════════════════════════════════════════════════


def test_assess_complexity_trivial():
    assert assess_complexity("Hello") == 0
    assert assess_complexity("") == 0
    assert assess_complexity("What time is it?") <= 1


def test_assess_complexity_hard():
    q = ("Compare and contrast the performance and trade-offs of "
         "Python versus Rust for building high-scale web servers, "
         "evaluate the ecosystem maturity, analyze memory safety "
         "implications and recommend the best option.")
    assert assess_complexity(q) >= 3


def test_router_fast_for_simple():
    plan = choose_cognition_strategy("What is 2+2?", budget_tokens=1000)
    assert plan.strategy == "fast"
    assert plan.estimated_extra_tokens == 0


def test_router_scales_with_complexity():
    simple = choose_cognition_strategy("Hello", budget_tokens=1000)
    hard = choose_cognition_strategy(
        "Compare Python vs Rust for web servers, evaluate performance, "
        "memory safety, ecosystem, and recommend the best.",
        budget_tokens=1000,
    )
    order = ["fast", "refine", "sc", "tot", "decompose"]
    assert order.index(hard.strategy) > order.index(simple.strategy)


def test_router_respects_budget():
    # Complex question (c>=5): low budget → ToT, big budget → debate
    hard_q = ("Analyze the risks and trade-offs of multiple candidate "
              "architectures, evaluate the failure modes, calculate the "
              "expected cost of each option, decide which to adopt, and "
              "assess the regulatory compliance implications of the plan")
    assert assess_complexity(hard_q) >= 5
    low = choose_cognition_strategy(hard_q, budget_tokens=200)
    mid = choose_cognition_strategy(hard_q, budget_tokens=900)
    high = choose_cognition_strategy(hard_q, budget_tokens=2000)
    assert low.strategy == "tot"
    assert mid.strategy == "decompose"
    assert high.strategy == "debate"


# ═══════════════════════════════════════════════════════════════════════════════
# Tree of Thoughts
# ═══════════════════════════════════════════════════════════════════════════════


def test_parse_score():
    assert _parse_score("0.85") == 0.85
    assert _parse_score(" 1.0 ") == 1.0
    assert _parse_score("no number") == 0.5


def test_tree_of_thoughts_runs_and_scores():
    res = tree_of_thoughts("Solve: 2+2", _fake_llm_correct,
                           branches=2, max_depth=2)
    assert res.thoughts_explored >= 1
    assert res.calls_made >= 3
    assert res.best.text  # non-empty best thought
    assert 0.0 <= res.confidence <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-agent debate
# ═══════════════════════════════════════════════════════════════════════════════


def test_debate_converges():
    res = multi_agent_debate("What is the answer?", _fake_llm_debate,
                             agents=3, rounds=1)
    assert len(res.answers) == 3
    assert res.final_answer
    assert 0.0 <= res.agreement <= 1.0


def test_debate_agents_bounded():
    # Even if asked for 9 agents, we cap at 5 to control cost
    res = multi_agent_debate("q?", _fake_llm_debate, agents=9, rounds=1)
    assert len(res.answers) <= 5


# ═══════════════════════════════════════════════════════════════════════════════
# Decomposed reasoning
# ═══════════════════════════════════════════════════════════════════════════════


def test_decompose_splits_and_assembles():
    res = decompose_and_solve("Hard multi-step question?", _fake_llm_decomposer)
    assert res.parts == 3
    assert len(res.sub_questions) == 3
    assert len(res.sub_answers) == 3
    assert "assembled" in res.final_answer.lower()


def test_decompose_falls_back_honestly():
    # LLM that can't decompose → direct answer, parts == 0, no crash
    def no_plan(prompt: str) -> str:
        return "I cannot break this down. Direct answer: 7."
    res = decompose_and_solve("q?", no_plan)
    assert res.parts == 0
    assert res.final_answer  # still an answer


# ═══════════════════════════════════════════════════════════════════════════════
# Cognitive solve — unified entry point with honest stats
# ═══════════════════════════════════════════════════════════════════════════════


def test_cognitive_solve_fast_path():
    ans, plan, stats = cognitive_solve("What is 2+2?", _fake_llm_correct)
    assert plan.strategy == "fast"
    assert stats["calls"] == 1
    assert ans


def test_cognitive_solve_hard_path():
    q = ("Compare and contrast Python vs Rust for web servers, evaluate "
         "performance and memory safety, and recommend the best.")
    ans, plan, stats = cognitive_solve(q, _fake_llm_correct, budget_tokens=2000)
    assert plan.strategy in ("refine", "sc", "tot", "decompose", "debate")
    assert stats["strategy"] == plan.strategy
    assert stats["calls"] >= 1
    assert ans


def test_cognitive_solve_honest_stats():
    """Stats must be truthful: calls counted, strategy reported."""
    q = ("Analyze the trade-offs of multiple deployment options, evaluate "
         "the failure modes and risks, and recommend the best strategy.")
    _, plan, stats = cognitive_solve(q, _fake_llm_correct, budget_tokens=5000)
    assert stats["strategy"] == plan.strategy
    assert stats["calls"] > 0
    assert stats["extra_tokens"] > 0
    # Confidence may be None for fast, but for hard strategies it's set
    assert "confidence" in stats
