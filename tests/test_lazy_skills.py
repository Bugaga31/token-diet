"""Tests for lazy_skills.py — lazy-loading skill registry."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from token_diet.core import count_tokens  # noqa: E402
from token_diet.lazy_skills import (  # noqa: E402
    Skill,
    SkillRegistry,
    default_registry,
)


def make_reg() -> SkillRegistry:
    reg = SkillRegistry()
    reg.register("git", ["commit", "merge", "branch"],
                 "git instructions one two three four five", priority=8)
    reg.register("tests", ["test", "pytest"],
                 "tests instructions one two three", priority=7)
    reg.register("docker", ["docker", "container"],
                 "docker instructions one two three four five six", priority=4)
    return reg


class TestRegistry:
    def test_register_and_count(self):
        reg = make_reg()
        assert len(reg.skills) == 3
        assert "git" in reg.skills

    def test_matches_keyword(self):
        reg = make_reg()
        assert reg.skills["git"].matches("please commit this change")
        assert not reg.skills["git"].matches("write a poem")

    def test_select_returns_only_matched(self):
        reg = make_reg()
        sel = reg.select("run the tests for docker image")
        names = {s.name for s in sel}
        assert names == {"tests", "docker"}
        assert "git" not in names

    def test_ranking_by_priority(self):
        reg = make_reg()
        # "tests" (7) should rank before "docker" (4)
        sel = reg.select("docker test")
        assert [s.name for s in sel] == ["tests", "docker"]

    def test_budget_filters(self):
        reg = make_reg()
        query = "commit then run pytest inside docker"
        # Tiny budget → nothing fits
        assert reg.select(query, budget_tokens=1) == []
        # Generous budget → everything fits
        assert len(reg.select(query, budget_tokens=100000)) == 3
        # Budget = exactly git's weight → only highest-priority git fits
        n_git = count_tokens(reg.skills["git"].instructions)
        sel = reg.select(query, budget_tokens=n_git)
        assert [s.name for s in sel] == ["git"]


class TestBuildPrompt:
    def test_no_match_returns_base(self):
        reg = make_reg()
        assert reg.build_prompt("BASE", "poetry please") == "BASE"

    def test_match_appends_instructions(self):
        reg = make_reg()
        prompt = reg.build_prompt("BASE", "how do i merge branches?")
        assert "BASE" in prompt
        assert "git" in prompt.lower()
        assert "docker" not in prompt.lower()

    def test_empty_query_returns_base(self):
        reg = make_reg()
        assert reg.build_prompt("BASE", "") == "BASE"


class TestSavings:
    def test_savings_pct_honest(self):
        reg = make_reg()
        # query matches only 1 of 3 skills → ~2/3 skipped
        pct = reg.savings_pct("commit now")
        assert pct > 50  # git is 1 of 3 instruction blocks
        assert pct < 100

    def test_no_match_100_percent(self):
        reg = make_reg()
        assert reg.savings_pct("unrelated topic") == 100.0

    def test_all_match_zero(self):
        reg = make_reg()
        assert reg.savings_pct("commit docker test") == 0.0


class TestDefaultRegistry:
    def test_has_skills(self):
        reg = default_registry()
        assert len(reg.skills) >= 5

    def test_security_skill_matches_credentials(self):
        reg = default_registry()
        sel = reg.select("where is my api key stored?")
        assert any(s.name == "security" for s in sel)

    def test_regex_trigger(self):
        reg = SkillRegistry()
        reg.register("ru-prose", ["re:^напиши|ре:^составь"],
                     "RU_WRITING_RULES")
        assert reg.select("напиши пост").__len__() == 1
        assert reg.select("hello world").__len__() == 0

    def test_all_matched_skills_have_instructions(self):
        reg = default_registry()
        for s in reg.skills.values():
            assert s.instructions.strip()


class TestSkillModel:
    def test_case_insensitive(self):
        sk = Skill("x", ["COMMIT"], "inst")
        assert sk.matches("I will Commit now")
