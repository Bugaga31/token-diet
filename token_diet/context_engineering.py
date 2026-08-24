"""context_engineering — Anthropic's context engineering rules (reverse-engineered).

In July 2026 Anthropic engineer Thariq Shihipar showed that Claude Code's
system prompt was cut from ~2,686 words to ~500 words (≈80% smaller) with
ZERO measurable quality loss on coding evals. The rules behind that cut
are the single most valuable prompt-efficiency discovery for us:

    1. JUDGMENT, NOT BANS
       Replace absolute prohibitions ("NEVER write comments") with
       contextual guidance ("match the surrounding code's comment style").
       Capable models follow positive guidance better than negative bans,
       and bans waste tokens.

    2. INTERFACE DESIGN OVER FEW-SHOT EXAMPLES
       Teach the model through clean schemas/enums/parameter types, not
       through paragraphs of worked examples. The schema is the teacher.

    3. PROGRESSIVE DISCLOSURE
       Don't front-load every specialized instruction into the always-on
       system prompt. Keep specialized rules in skills that load only
       when needed (we already do this with focus_keeper, sherlock...).

    4. KILL REDUNDANCY
       The same instruction repeated in system prompt + tool defs +
       project files wastes tokens and creates conflicts. One source of
       truth.

    5. LIGHTWEIGHT PROJECT FILES
       CLAUDE.md-style files should hold only purpose + gotchas + paths,
       not generic best-practice rulebooks.

    6. DOCUMENT POSITIONING
       Long reference data near the top, final instructions at the end.

This module implements the ALGORITHMIC parts (1, 4, 5, 6) so any system
prompt can be rightsized deterministically — no LLM needed.

Token economics: a 2000-token system prompt that gets cut to 500 saves
1500 tokens on EVERY call (input + cached output). For 100k calls a
month that's the difference between a bill and no bill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

try:
    from .core import count_tokens
except ImportError:  # pragma: no cover
    from core import count_tokens  # type: ignore[no-redef]


# ── redundancy detection ─────────────────────────────────────────────────────

# Absolute bans / heavy-handed prohibitions that waste tokens
_BAN_PATTERNS = [
    r"\b(?:NEVER|ALWAYS|MUST NOT|MUST|DO NOT|DON'T|FORBIDDEN|PROHIBITED|"
    r"UNDER NO CIRCUMSTANCES)\b",
    r"\b(?:absolutely|strictly|definitely)\s+(?:never|always|must)\b",
]

# Generic best-practice filler that doesn't carry information
_FILLER_LINES = [
    r"^\s*(?:be\s+)?(?:helpful|accurate|concise|polite|professional|"
    r"thorough|careful|precise)\b.*$",
    r"^\s*you\s+(?:are|should|must)\s+be\s+(?:a|an)?\s*\w+\s+(?:assistant|helper|expert|bot).*$",
    r"^\s*(?:remember|always remember|keep in mind)\s+.*$",
    r"^\s*(?:please|feel free to|don't hesitate to)\s+.*$",
]

# Duplicate sentence detection: same core content repeated
_WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ]{4,}")


def _signature(sentence: str) -> str:
    """Content signature: the set of significant words, sorted."""
    return " ".join(sorted(set(w.lower() for w in _WORD_RE.findall(sentence))))


@dataclass
class RedundancyReport:
    """What's bloating a system prompt."""
    prompt: str
    tokens: int
    lines: int = 0
    ban_lines: list[str] = field(default_factory=list)     # absolute bans
    filler_lines: list[str] = field(default_factory=list)  # generic filler
    duplicate_groups: list[list[str]] = field(default_factory=list)

    @property
    def removable_tokens(self) -> int:
        return sum(count_tokens(ln) for ln in self.ban_lines + self.filler_lines)

    @property
    def potential_savings_pct(self) -> float:
        return round(100 * self.removable_tokens / max(1, self.tokens), 1)

    def render(self) -> str:
        lines = [
            f"Промпт: {self.tokens} токенов, {self.lines} строк",
            f"Запреты-пустышки: {len(self.ban_lines)} ({sum(count_tokens(ln) for ln in self.ban_lines)} ток.)",
            f"Общие фразы: {len(self.filler_lines)} ({sum(count_tokens(ln) for ln in self.filler_lines)} ток.)",
            f"Дубликаты: {len(self.duplicate_groups)} групп",
            f"Можно убрать: ~{self.removable_tokens} токенов ({self.potential_savings_pct}%)",
        ]
        if self.ban_lines:
            lines.append("\nЗапреты (заменить на контекстную форму):")
            lines.extend(f"  - {ln.strip()[:80]}" for ln in self.ban_lines[:5])
        if self.duplicate_groups:
            lines.append("\nДубликаты:")
            for g in self.duplicate_groups[:3]:
                lines.append(f"  * «{g[0][:60]}» × {len(g)}")
        return "\n".join(lines)


def analyze_prompt(prompt: str) -> RedundancyReport:
    """Scan a system prompt for bans, filler, and duplicates."""
    report = RedundancyReport(prompt=prompt, tokens=count_tokens(prompt))
    lines = prompt.split("\n")
    report.lines = len(lines)
    seen: dict[str, list[str]] = {}

    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 8:
            continue

        # duplicates FIRST (by content signature) — a line can both be
        # a filler AND a duplicate; duplicates must be caught regardless
        sig = _signature(stripped)
        is_dup = False
        if len(sig.split()) >= 4 and sig in seen:
            is_dup = True
            if len(seen[sig]) == 1:
                report.duplicate_groups.append([seen[sig][0], stripped])
            else:
                report.duplicate_groups[-1].append(stripped)
        seen[sig] = [stripped]
        if is_dup:
            continue

        # bans
        if any(re.search(p, stripped, re.IGNORECASE) for p in _BAN_PATTERNS):
            report.ban_lines.append(stripped)
            continue
        # filler
        if any(re.match(p, stripped, re.IGNORECASE) for p in _FILLER_LINES):
            report.filler_lines.append(stripped)
            continue

    # keep the newest occurrence of each duplicate (progressive disclosure)
    return report


def _is_duplicate_of(seen: dict[str, list[str]], sentence: str) -> bool:
    sig = _signature(sentence)
    return sig in seen and len(seen[sig]) > 0


# ── prompt minimization ──────────────────────────────────────────────────────


@dataclass
class MinimizeResult:
    """Result of system-prompt minimization."""
    original: str
    minimized: str
    tokens_before: int
    tokens_after: int
    savings_pct: float
    removed_ban_lines: int
    removed_filler_lines: int
    removed_duplicate_lines: int


_BAN_REWRITES = [
    # (ban pattern, contextual replacement)
    (re.compile(r"\bNEVER\s+write\s+comments?\b", re.I),
     "match the surrounding code's comment density"),
    (re.compile(r"\bNEVER\s+write\s+(?:multi-line|multiline|long)\s+docstrings?\b", re.I),
     "keep docstrings consistent with the surrounding code"),
    (re.compile(r"\bALWAYS\s+use\s+type\s+hints?\b", re.I),
     "follow the type-hint style already used in this codebase"),
    (re.compile(r"\bNEVER\s+use\s+(?:exceptions?|try.catch)\b", re.I),
     "use error handling the way the surrounding code does"),
    (re.compile(r"\bDO\s+NOT\s+add\s+(?:unnecessary|extra)\s+code\b", re.I),
     "write code that is as simple as the problem allows"),
]


def minimize_system_prompt(prompt: str, aggressive: bool = True) -> MinimizeResult:
    """Right-size a system prompt using Anthropic's context engineering rules.

    Algorithm:
      1. Remove duplicated lines (keep newest occurrence).
      2. Remove generic filler lines ("be helpful", "you are an assistant").
      3. Rewrite absolute bans into contextual guidance (judgment, not bans).
      4. Remove pure prohibition lines that carry no information (aggressive).
      5. Collapse whitespace.

    Returns MinimizeResult with honest token accounting. Never returns
    an empty prompt: if everything would be removed, the first lines are kept.
    """
    tokens_before = count_tokens(prompt)
    lines = prompt.split("\n")
    out: list[str] = []
    removed_ban = 0
    removed_filler = 0
    removed_dup = 0

    # pass 1: find duplicate signatures (keep last index)
    sig_to_idx: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if len(stripped) < 8:
            continue
        sig = _signature(stripped)
        if len(sig.split()) >= 4:
            sig_to_idx.setdefault(sig, []).append(i)

    dup_indices: set[int] = set()
    for idxs in sig_to_idx.values():
        if len(idxs) > 1:
            for i in idxs[:-1]:
                dup_indices.add(i)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            out.append(line)  # keep blank-line structure for now
            continue

        # 1) duplicates
        if i in dup_indices:
            removed_dup += 1
            continue

        # 2) filler
        if aggressive and any(re.match(p, stripped, re.IGNORECASE)
                              for p in _FILLER_LINES):
            removed_filler += 1
            continue

        # 3) ban rewrites
        rewritten = stripped
        for pat, repl in _BAN_REWRITES:
            if pat.search(stripped):
                rewritten = pat.sub(repl, stripped)
                break
        if rewritten != stripped:
            out.append(rewritten)
            continue

        # 4) pure bans (aggressive): remove if no concrete content survives
        if aggressive and any(re.search(p, stripped, re.IGNORECASE)
                              for p in _BAN_PATTERNS):
            # keep only if the line contains concrete tokens beyond the ban
            sig = _signature(stripped)
            concrete = [w for w in sig.split() if w not in
                        {"never", "always", "must", "not", "do", "you"}]
            if len(concrete) < 3:
                removed_ban += 1
                continue
        out.append(line)

    # collapse whitespace
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # safety: never return empty
    if not text or count_tokens(text) < 5:
        text = prompt.strip()

    tokens_after = count_tokens(text)
    savings = round(100 * (tokens_before - tokens_after) / max(1, tokens_before), 1)

    return MinimizeResult(
        original=prompt,
        minimized=text,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        savings_pct=savings,
        removed_ban_lines=removed_ban,
        removed_filler_lines=removed_filler,
        removed_duplicate_lines=removed_dup,
    )


# ── document positioning (rule 6) ────────────────────────────────────────────


def reorder_prompt(
    system_prompt: str,
    reference_docs: list[str] | None = None,
    final_instructions: str = "",
) -> str:
    """Apply Anthropic's document positioning rule.

    Long reference data goes NEAR THE TOP (before the main instructions),
    final instructions (what to do with the data) go AT THE END. This
    reportedly improves multi-document response quality by up to 30%.

    Returns the reordered prompt string.
    """
    parts: list[str] = []
    if reference_docs:
        parts.append("<documents>")
        parts.extend(f"<document>\n{d}\n</document>" for d in reference_docs)
        parts.append("</documents>")
    if system_prompt.strip():
        parts.append(system_prompt.strip())
    if final_instructions.strip():
        parts.append(f"<instructions>\n{final_instructions.strip()}\n</instructions>")
    return "\n\n".join(parts)


# ── composite: analyze + minimize + report ───────────────────────────────────


def optimize_system_prompt(prompt: str) -> dict[str, Any]:
    """One-call rightsizing: analyze, minimize, and report.

    Returns dict with report + minimized prompt + savings.
    """
    report = analyze_prompt(prompt)
    result = minimize_system_prompt(prompt, aggressive=True)
    return {
        "report": report.render(),
        "minimized": result.minimized,
        "tokens_before": result.tokens_before,
        "tokens_after": result.tokens_after,
        "savings_pct": result.savings_pct,
        "removed": {
            "bans": result.removed_ban_lines,
            "filler": result.removed_filler_lines,
            "duplicates": result.removed_duplicate_lines,
        },
    }


__all__ = [
    "MinimizeResult",
    "RedundancyReport",
    "analyze_prompt",
    "minimize_system_prompt",
    "optimize_system_prompt",
    "reorder_prompt",
]
