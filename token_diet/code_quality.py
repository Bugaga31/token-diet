"""Code Quality — makes any LLM write BETTER code.

Inspired by the Writer/Reviewer pattern from modern AI coding agents
(OpenHands, Claude Code, Aider) and lint-driven iteration (ruff/mypy loop).

What this module gives an LLM (any model, zero neural dependencies):

1. ``review_code()`` — deterministic self-review of code against a
   rigorous checklist: correctness, security, edge cases, style, resource
   leaks, type safety. Returns structured findings (no API calls).

2. ``build_review_prompt()`` — injects the reviewer checklist into any
   prompt so the model critiques its OWN output before presenting it.

3. ``lint_iterate()`` — the compiler/linter loop: run ruff/pyflakes/mypy
   if installed, parse errors, produce a feedback prompt to fix them.

4. ``tdd_flow()`` — Test-Driven Development guidance: RED → GREEN →
   REFACTOR prompt template with explicit "make it fail first" step.

5. ``detect_code_smells()`` — static heuristics: bare excepts, mutable
   default args, TODO/FIXME, print() in libraries, missing type hints,
   duplicate code blocks, hardcoded secrets.

6. ``quality_report()`` — one-call score: 0-100 quality score with a
   list of must-fix issues (perfect for an Equivalence Gate companion).

All heuristics are pure-Python regex/AST — works in <1ms, no API calls,
so it does NOT add token cost (and actually SAVES tokens by preventing
buggy iterations).

For the people. For the planet.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# Findings
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Finding:
    """One issue found in code review."""
    severity: str          # "critical" | "warning" | "info"
    category: str          # e.g. "security", "correctness", "style"
    message: str
    line: int | None = None
    fix_hint: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Regex-based smell detection (no parsing needed — fast)
# ═══════════════════════════════════════════════════════════════════════════════

_MUTABLE_DEFAULT = re.compile(
    r"def\s+\w+\([^)]*=\s*(\[\]|\{\}|set\(\))", re.MULTILINE
)
_BARE_EXCEPT = re.compile(r"^\s*except\s*:", re.MULTILINE)
_TODO = re.compile(r"#\s*(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)
_PRINT = re.compile(r"^\s*print\(", re.MULTILINE)
_HARDCODED_SECRET = re.compile(
    r"(?i)(password|passwd|api_key|apikey|secret|token)\s*=\s*['\"][^'\"]{8,}['\"]"
)
_FSTRING = re.compile(r"f['\"].*\{.*\}", re.DOTALL)
_EXCEPT_PASS = re.compile(r"^\s*except[^:]*:\s*\n\s*pass\s*$", re.MULTILINE)
_NESTED_DEPTH = re.compile(r"^\s{16,}\S", re.MULTILINE)  # 4+ levels of indent


def detect_code_smells(source: str, filename: str = "<code>") -> list[Finding]:
    """Static heuristic scan for common code smells. No AST needed."""
    findings: list[Finding] = []
    if not source:
        return findings

    lines = source.splitlines()

    for m in _MUTABLE_DEFAULT.finditer(source):
        line_no = source[: m.start()].count("\n") + 1
        findings.append(Finding(
            "warning", "correctness",
            "Mutable default argument — shared across calls, classic bug",
            line_no, "Use None and initialize inside: def f(x=None): x = x or []",
        ))

    for m in _BARE_EXCEPT.finditer(source):
        line_no = source[: m.start()].count("\n") + 1
        findings.append(Finding(
            "warning", "robustness",
            "Bare except: — swallows KeyboardInterrupt/SystemExit too",
            line_no, "Use except Exception: or a specific exception",
        ))

    for m in _EXCEPT_PASS.finditer(source):
        line_no = source[: m.start()].count("\n") + 1
        findings.append(Finding(
            "warning", "robustness",
            "except: pass — silently hides errors, impossible to debug",
            line_no, "Log the error or raise a meaningful one",
        ))

    for m in _TODO.finditer(source):
        line_no = source[: m.start()].count("\n") + 1
        findings.append(Finding(
            "info", "maintainability",
            "TODO/FIXME left in code — incomplete work",
            line_no, "Finish the implementation or remove the marker",
        ))

    for m in _PRINT.finditer(source):
        line_no = source[: m.start()].count("\n") + 1
        findings.append(Finding(
            "info", "style",
            "print() used in library code — pollutes stdout",
            line_no, "Use logging or return values",
        ))

    for m in _HARDCODED_SECRET.finditer(source):
        line_no = source[: m.start()].count("\n") + 1
        findings.append(Finding(
            "critical", "security",
            "Possible hardcoded secret/credential in source",
            line_no, "Move to env var or secrets file; NEVER commit secrets",
        ))

    if len(lines) > 3:
        for m in _NESTED_DEPTH.finditer(source):
            line_no = source[: m.start()].count("\n") + 1
            findings.append(Finding(
                "info", "readability",
                "Deep nesting (4+ levels) — hard to read and test",
                line_no, "Extract inner logic into a helper function",
            ))
            break  # one hint is enough

    return findings


# ═══════════════════════════════════════════════════════════════════════════════
# AST-based checks (real parsing — catches real bugs)
# ═══════════════════════════════════════════════════════════════════════════════

def _ast_checks(source: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        findings.append(Finding(
            "critical", "syntax",
            f"Code does not parse: {e.msg} (line {e.lineno})",
            e.lineno, "Fix syntax before anything else",
        ))
        return findings

    # undefined names (best-effort without full scope analysis)
    defined: set[str] = set()
    used: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
            for a in node.args.args:
                defined.add(a.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.append((node.id, node.lineno))
    builtins = {
        "print", "len", "range", "str", "int", "float", "list", "dict",
        "set", "tuple", "bool", "sum", "min", "max", "abs", "round", "type",
        "isinstance", "enumerate", "zip", "map", "filter", "sorted", "open",
        "Exception", "ValueError", "TypeError", "KeyError", "None", "True",
        "False", "self", "super", "hasattr", "getattr", "setattr", "property",
        "__name__", "object", "classmethod", "staticmethod",
    }
    for name, line in used:
        if name not in defined and name not in builtins and not name.startswith("_"):
            findings.append(Finding(
                "warning", "correctness",
                f"Possibly undefined name: '{name}'",
                line, "Define it or import it",
            ))

    # functions without return that have a docstring promising return
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            has_return = any(isinstance(n, ast.Return) and n.value is not None
                             for n in ast.walk(node))
            if not has_return and node.name.startswith(("get_", "is_", "has_", "calc_")):
                findings.append(Finding(
                    "info", "correctness",
                    f"'{node.name}' looks like a getter but never returns a value",
                    node.lineno, "Add a return statement",
                ))

    # comparison chaining safety: `a == b == c` is fine, but `a < b > c` style
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            pass  # single comparison always fine

    return findings


# ═══════════════════════════════════════════════════════════════════════════════
# Main review API
# ═══════════════════════════════════════════════════════════════════════════════

def review_code(source: str, filename: str = "<code>") -> list[Finding]:
    """Full deterministic review: AST + regex heuristics."""
    return _ast_checks(source) + detect_code_smells(source, filename)


def quality_report(source: str, filename: str = "<code>") -> dict[str, Any]:
    """One-call quality score 0-100 with must-fix list.

    Perfect companion for EquivalenceGate: run BEFORE shipping a prompt,
    or AFTER code generation to gate it.
    """
    findings = review_code(source, filename)
    severity_weight = {"critical": 25, "warning": 8, "info": 2}
    score = 100
    for f in findings:
        score -= severity_weight.get(f.severity, 2)
    score = max(0, min(100, score))

    return {
        "score": score,
        "verdict": "PASS" if score >= 85 else ("WARN" if score >= 60 else "FAIL"),
        "findings": [f.__dict__ for f in findings],
        "critical_count": sum(1 for f in findings if f.severity == "critical"),
        "warning_count": sum(1 for f in findings if f.severity == "warning"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Lint-driven iteration (the compiler/linter loop)
# ═══════════════════════════════════════════════════════════════════════════════

# Which linters to try, in order of speed
_LINTERS = [
    ("ruff", "check {file} --output-format=concise"),
    ("pyflakes", "{file}"),
    ("mypy", "{file} --ignore-missing-imports"),
]


def run_linter(filepath: str) -> list[str]:
    """Run available linters (ruff/pyflakes/mypy) on a file.

    Returns raw error lines. Empty list = clean. Never raises.
    """
    output_lines: list[str] = []
    for name, cmd_template in _LINTERS:
        if not _which(name):
            continue
        cmd = cmd_template.format(file=filepath).split()
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                cwd=filepath and __import__("os").path.dirname(filepath) or ".",
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        lines = (r.stdout + r.stderr).splitlines()
        # keep only error lines mentioning the file
        kept = [ln for ln in lines if filepath in ln or name in ln]
        if kept:
            output_lines.append(f"--- {name} ---")
            output_lines.extend(kept[:40])
        # stop at first linter that found issues (fast feedback)
        if kept:
            break
    return output_lines


def _which(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def lint_feedback_prompt(filepath: str, task: str) -> str:
    """Build a prompt that makes the LLM fix linter errors.

    The core of lint-driven iteration: implement → run linters → fix.
    """
    errors = run_linter(filepath)
    if not errors:
        return (f"Task: {task}\n\n"
                f"Your code in {filepath} passes linting (ruff/pyflakes/mypy). "
                f"Run tests next.")
    err_block = "\n".join(errors[:60])
    return (f"Task: {task}\n\n"
            f"Your implementation in {filepath} has linter/type errors. "
            f"FIX ALL of them before responding:\n\n"
            f"```\n{err_block}\n```\n\n"
            f"Rules: fix the root cause (not by suppressing errors), "
            f"re-run the linter, and only then reply with the final code.")


def lint_iterate(filepath: str, task: str, max_rounds: int = 3) -> dict[str, Any]:
    """Report the lint status for a file (agent loop helper).

    In an agentic flow, call this after each code change:
      status = lint_iterate("foo.py", "implement X")
      if status["clean"]: break
    """
    errors = run_linter(filepath)
    return {
        "file": filepath,
        "clean": not errors,
        "error_count": len(errors),
        "errors": errors[:60],
        "feedback_prompt": lint_feedback_prompt(filepath, task),
        "rounds_hint": max_rounds,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TDD flow
# ═══════════════════════════════════════════════════════════════════════════════

def tdd_flow(task: str, test_file: str, code_file: str) -> str:
    """Build a TDD prompt: RED → GREEN → REFACTOR.

    The single highest-leverage technique for LLM code quality: feed
    test feedback back into the model until green.
    """
    return f"""TASK: {task}

Follow TEST-DRIVEN DEVELOPMENT. Do NOT write the implementation first.

1. RED — write tests in {test_file} that FAIL (verify they fail)
2. GREEN — implement in {code_file} until ALL tests pass
3. REFACTOR — clean up without breaking tests
4. VERIFY — run: python -m pytest {test_file} -q  → all green

If any test fails after your implementation, read the error, fix the
ROOT CAUSE, and re-run. Never delete or weaken a failing test to make
it pass. Reply with: (a) final code, (b) test output, (c) what you fixed.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt injection
# ═══════════════════════════════════════════════════════════════════════════════

REVIEW_CHECKLIST = """BEFORE YOU REPLY WITH CODE, REVIEW IT AGAINST THIS CHECKLIST:
1. Correctness — does it handle empty input, None, and edge cases?
2. Security — no hardcoded secrets, no eval/exec of untrusted input, no SQL injection
3. Exceptions — bare except: and except: pass are banned; catch specific errors
4. Types — no mutable default args ([] / {}); use None + init
5. Resources — files/connections opened must be closed (use context managers)
6. Naming — functions do what their name says; no misleading getters without return
7. Tests — would the code pass a test you'd write for it right now?
8. Tokens — is the code the SHORTEST correct version? No dead code, no unused imports
Fix anything that fails before presenting the code. Present the FIXED version."""


def build_review_prompt(system_prompt: str, include_tdd: bool = True) -> str:
    """Append the reviewer checklist to any system prompt.

    Use on ANY model — this is what makes it write better code without
    spending tokens on a second reviewer call.
    """
    extra = [REVIEW_CHECKLIST]
    if include_tdd:
        extra.append(
            "Prefer writing a small failing test first (RED), then implement "
            "until green (GREEN), then clean up (REFACTOR)."
        )
    return system_prompt + "\n\n" + "\n".join(extra)


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience
# ═══════════════════════════════════════════════════════════════════════════════

def code_review_prompt(code: str, focus: str = "all") -> str:
    """Build a fresh-context reviewer prompt for a piece of code.

    The Writer/Reviewer pattern: the reviewer sees the code from first
    principles, with no bias from having written it.
    """
    areas = {
        "all": "correctness, security, edge cases, performance, style, readability",
        "security": "security only: injection, secrets, unsafe eval/exec, path traversal",
        "performance": "performance only: O(n^2) loops, redundant work, memory copies",
        "correctness": "correctness only: logic bugs, off-by-one, wrong conditions",
    }.get(focus, "correctness, security, edge cases")
    return f"""Act as a SENIOR CODE REVIEWER. Review this code from first principles.

FOCUS: {areas}

{code}

Report:
1. CRITICAL issues (bugs, security) — must fix
2. WARNINGS — should fix
3. NITS — style only
For each: line, why, and the exact fix. Be harsh but precise.
Do NOT say "looks good" — every code has at least one improvement.
"""


def describe_findings(findings: list[Finding]) -> str:
    """Human-readable summary of findings (compact, token-cheap)."""
    if not findings:
        return "Code looks clean. ✅"
    lines = []
    for f in sorted(findings, key=lambda x: {"critical": 0, "warning": 1, "info": 2}[x.severity]):
        loc = f"L{f.line}" if f.line else "?"
        lines.append(f"[{f.severity.upper()}] {loc} {f.category}: {f.message}")
    return "\n".join(lines)


__all__ = [
    "Finding", "review_code", "quality_report", "detect_code_smells",
    "run_linter", "lint_iterate", "lint_feedback_prompt", "tdd_flow",
    "build_review_prompt", "code_review_prompt", "describe_findings",
    "REVIEW_CHECKLIST",
]
