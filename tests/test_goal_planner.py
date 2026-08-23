"""Tests for goal_planner.py — GOAP planning with A* search."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from token_diet.goal_planner import (  # noqa: E402
    GOAPAction,
    GoalPlanner,
    PlanResult,
    TrajectoryMemory,
    WorldState,
    coding_actions,
    demo_goal,
)


def make_planner() -> GoalPlanner:
    return GoalPlanner(coding_actions())


class TestWorldState:
    def test_satisfies_and_apply(self):
        s = WorldState({"a": 1})
        assert s.satisfies({"a": 1})
        assert not s.satisfies({"a": 2})
        nxt = s.apply({"b": True})
        assert nxt.get("b") is True
        assert s.get("b") is None  # immutable

    def test_hashable(self):
        a, b = WorldState({"x": 1}), WorldState({"x": 1})
        assert hash(a) == hash(b)
        assert len({a, b}) == 1


class TestPlanner:
    def test_plans_to_goal(self):
        res = make_planner().plan(demo_goal())
        assert res.success
        names = res.names()
        # must reach tests_pass, reviewed, shipped
        assert "ship" in names
        assert "run_tests" in names
        assert names[-1] == "ship"

    def test_optimal_cost_first_step(self):
        res = make_planner().plan(demo_goal())
        # cheapest valid path starts with read_requirements
        assert res.plan[0].name == "read_requirements"

    def test_plan_is_optimal_cheapest(self):
        planner = make_planner()
        # add an expensive-but-shortcut action: cannot skip tests/review
        res = planner.plan(demo_goal())
        names = res.names()
        assert "write_tests" in names      # tests required for ship
        assert "review" in names           # review required for ship
        assert "write_code" in names
        # A* returns optimal: order must respect preconditions
        idx_code = names.index("write_code")
        idx_tests = names.index("write_tests")
        idx_ship = names.index("ship")
        assert idx_code < idx_tests < idx_ship

    def test_unreachable_goal(self):
        res = make_planner().plan({"impossible_thing": True})
        assert not res.success
        assert "no plan" in res.error

    def test_replan_after_failure(self):
        planner = make_planner()
        res = planner.plan(demo_goal())
        assert res.success
        # simulate: write_tests failed, facts unchanged -> plan again
        res2 = planner.replan(demo_goal(), WorldState(), "write_tests")
        assert res2.success


class TestMemory:
    def test_recall_after_plan(self):
        planner = make_planner()
        res = planner.plan(demo_goal())
        assert res.success
        cached = planner.plan(demo_goal())
        assert cached.from_memory is True
        assert cached.names() == res.names()

    def test_memory_keeps_cheapest(self):
        mem = TrajectoryMemory()
        mem.remember("g", ["a", "b"], 5.0)
        mem.remember("g", ["a"], 2.0)
        assert mem.recall("g") == ["a"]

    def test_sqlite_persistence(self, tmp_path):
        db = tmp_path / "plans.db"
        mem = TrajectoryMemory(db_path=str(db))
        mem.remember("g1", ["x", "y"], 3.0)
        mem.close()
        mem2 = TrajectoryMemory(db_path=str(db))
        assert mem2.recall("g1") == ["x", "y"]


class TestPrompt:
    def test_build_prompt_includes_plan(self):
        res = make_planner().plan(demo_goal())
        prompt = make_planner().build_prompt(res)
        assert "EXECUTE THIS OPTIMAL PLAN" in prompt
        for name in res.names():
            assert name in prompt

    def test_prompt_on_failure_blocks(self):
        res = make_planner().plan({"impossible": True})
        prompt = make_planner().build_prompt(res)
        assert "GOAL UNREACHABLE" in prompt

    def test_savings_positive(self):
        res = make_planner().plan(demo_goal())
        assert GoalPlanner.estimate_exploration_savings(res) >= 0.0


class TestDemo:
    def test_demo_actions_are_valid(self):
        planner = make_planner()
        res = planner.plan(demo_goal())
        assert res.success
        assert all(a.name in planner.actions for a in res.plan)
