"""Progressive Disclosure — lazy-load context instead of dumping everything.

The 2025-2026 meta-pattern: don't put 10K tokens of docs/tools/skills
into every prompt. Instead, provide a lightweight catalog (~80 tokens per item)
and load full details ONLY when the model requests them.

This is what anymodel.dev does with SKILL.md files,
what Anthropic recommends for Agent Skills,
and what MCP (Model Context Protocol) formalizes.

Result: context drops from 10K+ tokens to ~500 tokens baseline,
with on-demand expansion only when needed.

Usage:
    catalog = ProgressiveDisclosure()
    catalog.add_skill("database", "Query PostgreSQL", "Full docs here...")
    prompt = catalog.build_prompt(user_query)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SkillEntry:
    name: str
    short_description: str  # ~10-15 words, ~30 tokens
    full_content: str        # Full docs, can be 500+ tokens
    keywords: list[str] = field(default_factory=list)  # When to auto-include


class ProgressiveDisclosure:
    """Manage context via progressive disclosure pattern.

    Instead of dumping ALL skills/docs into every prompt:
    1. Build a compact catalog (name + 1-line description)
    2. Let the LLM request full details on demand
    3. Auto-include high-relevance skills based on keyword match
    """

    def __init__(self):
        self._skills: list[SkillEntry] = []

    def add_skill(self, name: str, short_description: str, full_content: str,
                  keywords: list[str] | None = None) -> None:
        self._skills.append(SkillEntry(
            name=name,
            short_description=short_description,
            full_content=full_content,
            keywords=keywords or [],
        ))

    def _match_relevance(self, query: str, skill: SkillEntry) -> bool:
        """Check if a skill is relevant to the query."""
        query_lower = query.lower()
        # Check keywords
        for kw in skill.keywords:
            if kw.lower() in query_lower:
                return True
        # Check name/description
        if skill.name.lower() in query_lower:
            return True
        return False

    def build_catalog(self) -> str:
        """Build a compact catalog of available skills."""
        if not self._skills:
            return ""

        lines = ["[Available context — request details with: need <name>]"]
        for s in self._skills:
            lines.append(f"- {s.name}: {s.short_description}")
        return "\n".join(lines)

    def build_prompt(self, query: str,
                     max_auto_skills: int = 3) -> str:
        """Build a progressive disclosure prompt.

        Includes:
        - Full content for auto-matched (high-relevance) skills
        - Catalog entries for remaining skills
        - The user query

        The model can request full details of catalog skills on demand.
        """
        if not self._skills:
            return query

        auto_include: list[SkillEntry] = []
        catalog_only: list[SkillEntry] = []

        for skill in self._skills:
            if self._match_relevance(query, skill) and len(auto_include) < max_auto_skills:
                auto_include.append(skill)
            else:
                catalog_only.append(skill)

        parts = []

        # Full content for auto-matched skills
        if auto_include:
            for skill in auto_include:
                parts.append(f"<context name=\"{skill.name}\">\n{skill.full_content}\n</context>")

        # Catalog for remaining skills
        if catalog_only:
            catalog_entries = []
            for skill in catalog_only:
                catalog_entries.append(f"- {skill.name}: {skill.short_description}")
            if catalog_entries:
                parts.append("[Other available context. Say \"need <name>\" to load full details.]")
                parts.append("\n".join(catalog_entries))

        parts.append(f"<query>\n{query}\n</query>")

        return "\n\n".join(parts)

    def estimate_tokens_saved(self, query: str) -> int:
        """Estimate tokens saved vs dumping everything."""
        from token_diet.core import count_tokens

        # Full dump: all skills
        full_dump = "\n\n".join(s.full_content for s in self._skills)
        full_tokens = count_tokens(full_dump)

        # Progressive: catalog + auto-matched only
        progressive = self.build_prompt(query)
        progressive_tokens = count_tokens(progressive)

        return full_tokens - progressive_tokens
