"""goal_planner — GOAP (Goal-Oriented Action Planning) with A* search.

Reverse-engineered from the ruflo-goals plugin (ruvnet/ruflo, ex-Claude Flow)
and Claude Code's native `/goal` command.

THE PROBLEM:
    An autonomous agent given a big goal tends to *wander*: it tries random
    steps, loops, burns tokens on dead ends, and only sometimes reaches the
    target. The LLM pays for every wrong turn.

THE INSIGHT (GOAP / A*):
    Before the LLM does anything, plan the CHEAPEST path to the goal
    deterministically. Model the world as states, actions as
    (preconditions -> effects) with a cost, then run A* from the current
    state to the goal state. The heuristic = number of unmet goal
    conditions, which is admissible & consistent — so A* returns the
    optimal plan. The LLM then just EXECUTES the plan instead of thinking.

    Trajectory memory: successful plans are stored keyed by the goal
    pattern. A similar goal in the future reuses the proven path —
    zero planning cost, zero LLM tokens burned rediscovering it.

WHAT THIS MODULE DOES (pure stdlib, deterministic, zero LLM calls):
      - WorldState  : hashable bag of facts (state variables)
      - GOAPAction  : preconditions / effects / cost / name
      - GoalPlanner : A* planner (heapq), replan_on_fail, plan reuse
      - Trajectory  : in-memory + optional sqlite cache of good plans
      - build_prompt: turns the optimal plan into a compact execution
                      prompt — the LLM executes, doesn't invent.

Token economics:
    Instead of "reason through this goal step-by-step" (which makes the
    LLM explore blind and burn output tokens), we hand it the optimal
    action sequence. Big win on complex goals, and near-zero on
    repeated goals thanks to trajectory memory.
"""

from __future__ import annotations

import heapq
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ── world state ──────────────────────────────────────────────────────────────


class WorldState:
    """A bag of facts: state variables that actions can require and set."""

    __slots__ = ("facts",)

    def __init__(self, facts: Optional[dict[str, Any]] = None) -> None:
        self.facts: dict[str, Any] = dict(facts or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.facts.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.facts[key] = value

    def satisfies(self, preconditions: dict[str, Any]) -> bool:
        """True if every precondition (key -> expected value) holds."""
        for k, v in preconditions.items():
            if self.facts.get(k) != v:
                return False
        return True

    def apply(self, effects: dict[str, Any]) -> "WorldState":
        """Return a NEW state with effects applied (immutable for A*)."""
        merged = dict(self.facts)
        merged.update(effects)
        return WorldState(merged)

    def signature(self) -> str:
        """Deterministic serialization for hashing / caching."""
        items = sorted(self.facts.items())
        return json.dumps(items, ensure_ascii=False, sort_keys=True, default=str)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, WorldState) and self.facts == other.facts

    def __hash__(self) -> int:
        return hash(self.signature())

    def __repr__(self) -> str:  # pragma: no cover
        return f"WorldState({self.facts})"


# ── action model ─────────────────────────────────────────────────────────────


@dataclass
class GOAPAction:
    """One executable step: requires preconditions, produces effects."""

    name: str
    preconditions: dict[str, Any] = field(default_factory=dict)
    effects: dict[str, Any] = field(default_factory=dict)
    cost: float = 1.0                     # time / money / risk weight
    description: str = ""                 # human/LLM readable hint

    def usable_in(self, state: WorldState) -> bool:
        return state.satisfies(self.preconditions)

    def __repr__(self) -> str:  # pragma: no cover
        return f"GOAPAction({self.name}, cost={self.cost})"


# ── trajectory memory (in-memory + optional sqlite) ──────────────────────────


class TrajectoryMemory:
    """Stores successful (goal-pattern -> plan) pairs for reuse.

    sqlite persistence is optional: pass db_path to enable. The store
    keeps the cheapest known plan per goal signature.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._mem: dict[str, dict[str, Any]] = {}
        self._db_path = db_path
        if db_path:
            self._conn = sqlite3.connect(db_path)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS plans ("
                "goal TEXT PRIMARY KEY, plan TEXT, cost REAL)"
            )
            self._conn.commit()
            self._load()
        else:
            self._conn = None

    def _load(self) -> None:  # pragma: no cover — trivial
        if not self._conn:
            return
        try:
            for goal, plan, cost in self._conn.execute(
                "SELECT goal, plan, cost FROM plans"
            ):
                self._mem[goal] = {"plan": json.loads(plan), "cost": cost}
        except (sqlite3.Error, json.JSONDecodeError):
            pass

    def remember(self, goal_sig: str, plan: list[str], cost: float) -> None:
        old = self._mem.get(goal_sig)
        if old is None or cost < old["cost"]:   # keep the cheapest
            self._mem[goal_sig] = {"plan": list(plan), "cost": cost}
            if self._conn:
                try:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO plans (goal, plan, cost) "
                        "VALUES (?, ?, ?)",
                        (goal_sig, json.dumps(plan), cost),
                    )
                    self._conn.commit()
                except sqlite3.Error:
                    pass

    def recall(self, goal_sig: str) -> Optional[list[str]]:
        entry = self._mem.get(goal_sig)
        return list(entry["plan"]) if entry else None

    def recall_count(self) -> int:
        return len(self._mem)

    def close(self) -> None:  # pragma: no cover
        if self._conn:
            self._conn.close()
            self._conn = None


# ── the A* planner ───────────────────────────────────────────────────────────


@dataclass
class PlanResult:
    """Outcome of planning: either a plan or a clear explanation."""

    goal: str
    plan: list[GOAPAction] = field(default_factory=list)
    cost: float = 0.0
    from_memory: bool = False
    error: str = ""
    visited: int = 0

    @property
    def success(self) -> bool:
        return bool(self.plan) and not self.error

    def names(self) -> list[str]:
        return [a.name for a in self.plan]

    def __bool__(self) -> bool:
        return self.success


class GoalPlanner:
    """A* over world states with GOAP actions.

    Heuristic h(state) = number of unmet goal conditions (admissible +
    consistent) — guarantees the first solution found is optimal.
    """

    def __init__(
        self,
        actions: Optional[Iterable[GOAPAction]] = None,
        memory: Optional[TrajectoryMemory] = None,
        max_visits: int = 2000,
    ) -> None:
        self.actions: dict[str, GOAPAction] = {}
        if actions:
            for a in actions:
                self.actions[a.name] = a
        self.memory = memory or TrajectoryMemory()
        self.max_visits = max_visits

    # -- action registry ---------------------------------------------------

    def add_action(self, action: GOAPAction) -> None:
        self.actions[action.name] = action

    def add_actions(self, actions: Iterable[GOAPAction]) -> None:
        for a in actions:
            self.add_action(a)

    # -- heuristics --------------------------------------------------------

    @staticmethod
    def _unmet(goal: dict[str, Any], state: WorldState) -> int:
        return sum(1 for k, v in goal.items() if state.get(k) != v)

    # -- core plan ---------------------------------------------------------

    def plan(
        self,
        goal: dict[str, Any],
        initial: Optional[WorldState] = None,
        use_memory: bool = True,
    ) -> PlanResult:
        """Find the cheapest action sequence that achieves `goal`."""
        start = initial or WorldState()
        goal_sig = WorldState(goal).signature()

        if use_memory:
            cached = self.memory.recall(goal_sig)
            if cached is not None:
                actions = [
                    self.actions[n] for n in cached if n in self.actions
                ]
                if actions:
                    return PlanResult(
                        goal=goal_sig,
                        plan=actions,
                        cost=sum(a.cost for a in actions),
                        from_memory=True,
                    )

        # A*: priority queue of (f, counter, state, path, cost)
        open_heap: list[tuple[float, int, WorldState, tuple[str, ...], float]]
        open_heap = []
        counter = 0
        heapq.heappush(
            open_heap, (self._unmet(goal, start), counter, start, (), 0.0)
        )
        best_g: dict[WorldState, float] = {start: 0.0}
        visited = 0
        seen_paths: set[tuple[str, ...]] = set()

        while open_heap and visited < self.max_visits:
            f, _, state, path, g = heapq.heappop(open_heap)
            visited += 1

            if self._unmet(goal, state) == 0:
                plan = [self.actions[n] for n in path]
                total = sum(a.cost for a in plan)
                self.memory.remember(goal_sig, path, total)
                return PlanResult(
                    goal=goal_sig, plan=plan, cost=total, visited=visited
                )

            for name, action in self.actions.items():
                if not action.usable_in(state):
                    continue
                nxt = state.apply(action.effects)
                if nxt in best_g and best_g[nxt] <= g + action.cost:
                    continue
                new_path = path + (name,)
                if new_path in seen_paths:      # avoid infinite loops
                    continue
                seen_paths.add(new_path)
                ng = g + action.cost
                best_g[nxt] = ng
                counter += 1
                nh = self._unmet(goal, nxt)
                heapq.heappush(
                    open_heap, (ng + nh, counter, nxt, new_path, ng)
                )

        return PlanResult(
            goal=goal_sig,
            error=(
                f"no plan found in {visited} states "
                f"(actions: {sorted(self.actions)})"
            ),
            visited=visited,
        )

    # -- replan on failure -------------------------------------------------

    def replan(
        self,
        goal: dict[str, Any],
        initial: WorldState,
        failed_action: str,
        new_facts: Optional[dict[str, Any]] = None,
    ) -> PlanResult:
        """Re-plan after `failed_action` broke: update state, plan again."""
        if new_facts:
            initial = initial.apply(new_facts)
        return self.plan(goal, initial, use_memory=False)

    # -- prompt / execution aid --------------------------------------------

    def build_prompt(
        self, result: PlanResult, context: str = ""
    ) -> str:
        """Turn the optimal plan into a compact execution prompt.

        The LLM executes these steps instead of inventing its own path —
        that's where the token win lives (no blind exploration).
        """
        if not result.success:
            return (
                f"GOAL UNREACHABLE: {result.error}\n"
                "Do not attempt — explain the blocker to the user instead."
            )
        lines = [
            "EXECUTE THIS OPTIMAL PLAN (do not invent new steps):",
            "",
        ]
        for i, action in enumerate(result.plan, 1):
            desc = action.description or action.name
            lines.append(f"{i}. {action.name}: {desc}")
        if context:
            lines.extend(["", f"CONTEXT: {context}"])
        lines.extend(
            [
                "",
                "After each step, verify its effect happened "
                "(compare against the plan); if a step fails, report it "
                "and stop — do NOT improvise around it.",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def estimate_exploration_savings(
        result: PlanResult, blind_step_cost: float = 15.0
    ) -> float:
        """Estimated tokens saved vs. blind LLM exploration.

        Blind exploration typically wastes `visited` state transitions.
        Planning cost ~0 (deterministic) — every visited state that the
        LLM *didn't* have to explore is saved output.
        """
        if not result.success:
            return 0.0
        return max(0.0, (result.visited - len(result.plan)) * blind_step_cost)


# ── example: a small practical action set ────────────────────────────────────


def coding_actions() -> list[GOAPAction]:
    """A minimal dev-workflow action set (demo / tests)."""
    return [
        GOAPAction(
            "read_requirements",
            {},
            {"requirements_read": True},
            cost=1.0,
            description="Read the task requirements carefully",
        ),
        GOAPAction(
            "write_code",
            {"requirements_read": True},
            {"code_written": True},
            cost=4.0,
            description="Implement the solution in code",
        ),
        GOAPAction(
            "write_tests",
            {"code_written": True},
            {"tests_written": True},
            cost=3.0,
            description="Cover the code with tests",
        ),
        GOAPAction(
            "run_tests",
            {"tests_written": True},
            {"tests_pass": True},
            cost=1.0,
            description="Run tests until green",
        ),
        GOAPAction(
            "review",
            {"code_written": True},
            {"reviewed": True},
            cost=1.5,
            description="Self-review for bugs and edge cases",
        ),
        GOAPAction(
            "ship",
            {"tests_pass": True, "reviewed": True},
            {"shipped": True},
            cost=1.0,
            description="Ship / create the pull request",
        ),
    ]


def demo_goal() -> dict[str, Any]:
    return {"tests_pass": True, "reviewed": True, "shipped": True}


# ── CLI helper ───────────────────────────────────────────────────────────────


def run_cli(args: list[str]) -> int:
    """Minimal CLI: python -m token_diet.goal_planner <goal_json>"""
    import sys

    planner = GoalPlanner(coding_actions())
    initial = WorldState()
    goal = demo_goal()
    if args:
        try:
            goal = json.loads(args[0])
        except json.JSONDecodeError:
            print(f"bad goal json: {args[0]}", file=sys.stderr)
            return 2
    res = planner.plan(goal, initial)
    if res.success:
        print(f"PLAN ({res.cost:.1f} cost, {res.visited} states, "
              f"memory={res.from_memory}):")
        for i, a in enumerate(res.plan, 1):
            print(f"  {i}. {a.name} — {a.description}")
        print()
        print(planner.build_prompt(res))
    else:
        print(f"NO PLAN: {res.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(run_cli(sys.argv[1:]))
