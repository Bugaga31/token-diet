"""polyglot — know every language, break nothing.

token-diet is not a Python-only tool. Real agent sessions mix Python,
JavaScript/TypeScript, Java/Kotlin, Go, Rust, C/C++, SQL, JSON, YAML,
Shell, and more. Compressing code you don't understand is how you break
it.

This module gives token-diet polyglot awareness, zero dependencies:

    1. detect_language(code, filename) — identify the language by
       extension + syntax fingerprints (shebang, keywords, imports).

    2. protect_code(text, language) — split code into "protected spans"
       (strings, comments, identifiers, numbers) and "compressible
       prose", so compressors never mangle syntax.

    3. code_budget(code, language) — estimate how much of a file you
       can safely drop (imports, blank lines, type boilerplate) vs
       what must stay (logic).

    4. SYSTEM_PROMPTS — ready-made system-prompt snippets per language
       that make the model write idiomatic code in that language
       (costs ~40 tokens once, cached).

    5. build_toolchain() — detect what compilers/interpreters exist on
       this machine (java, rustc, node, gcc, python...) so the agent
       knows what it can actually run and test.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field

# ── language detection ───────────────────────────────────────────────────────


# extension → language
_EXT_MAP: dict[str, str] = {
    ".py": "python", ".pyw": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cxx": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".sql": "sql",
    ".json": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".html": "html", ".htm": "html",
    ".css": "css",
    ".md": "markdown",
    ".txt": "text",
    ".gradle": "gradle", ".groovy": "groovy",
    ".dockerfile": "dockerfile",
    ".lua": "lua",
    ".r": "r",
    ".pl": "perl",
    ".ex": "elixir", ".exs": "elixir",
    ".hs": "haskell",
    ".scala": "scala",
}

# syntax fingerprints: keyword → language (checked on first ~2000 chars)
_FINGERPRINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*(?:import|from)\s+\w+.*\n(?:def |class |async def )", re.M), "python"),
    (re.compile(r"\b(?:const|let|var)\s+\w+\s*=\s*(?:require\(|import\s)", re.M), "javascript"),
    (re.compile(r"\b(?:interface|type)\s+\w+\s*[={].*\b(?:string|number|boolean)\b", re.M), "typescript"),
    (re.compile(r"\bpublic\s+(?:static\s+)?(?:class|void|int|String)\b", re.M), "java"),
    (re.compile(r"\bfun\s+\w+\s*\([^)]*\)\s*(?::\s*\w+)?\s*[={]", re.M), "kotlin"),
    (re.compile(r"\bpackage\s+\w+(?:\.\w+)*\b.*\n(?:import\s+\w+|func\s+\w+)", re.M | re.S), "go"),
    (re.compile(r"\bfn\s+\w+\s*\([^)]*\)\s*->\s*\w+", re.M), "rust"),
    (re.compile(r"#include\s*<[^>]+>", re.M), "c"),
    (re.compile(r"using\s+System(?:\.\w+)*;", re.M), "csharp"),
    (re.compile(r"SELECT\s+[\s\S]{0,80}\bFROM\b", re.I | re.M), "sql"),
    (re.compile(r"def\s+\w+\s*\([^)]*\)\s*:\s*end", re.M), "ruby"),
    (re.compile(r"<\?php", re.M), "php"),
    (re.compile(r"#include\s*<[^>]+>\s*\n\s*(?:using|namespace)", re.M), "cpp"),
]

_SHEBANG_MAP = {
    "python": "python",
    "bash": "bash",
    "sh": "bash",
    "zsh": "bash",
    "node": "javascript",
    "ruby": "ruby",
    "perl": "perl",
}


def detect_language(code: str = "", filename: str = "") -> str:
    """Detect the programming language of a code snippet or file.

    Priority: filename extension → shebang → syntax fingerprints.
    Returns a canonical language name (python, javascript, ...) or "text".
    """
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        base = os.path.basename(filename).lower()
        if ext in _EXT_MAP:
            return _EXT_MAP[ext]
        if base in ("dockerfile",):
            return "dockerfile"
        if base.startswith("makefile"):
            return "makefile"

    head = code[:2000]
    if head.startswith("#!"):
        sh = head.split("\n", 1)[0][2:].strip().lower()
        for key, lang in _SHEBANG_MAP.items():
            if key in sh:
                return lang

    for pattern, lang in _FINGERPRINTS:
        if pattern.search(head):
            return lang

    return "text"


# ── code protection (never break syntax) ─────────────────────────────────────


# Multi-line comment patterns per language
_MULTILINE_COMMENTS: dict[str, tuple[str, str]] = {
    "python": ('"""', '"""'),
    "javascript": ("/*", "*/"),
    "typescript": ("/*", "*/"),
    "java": ("/*", "*/"),
    "kotlin": ("/*", "*/"),
    "go": ("/*", "*/"),
    "rust": ("/*", "*/"),
    "c": ("/*", "*/"),
    "cpp": ("/*", "*/"),
    "csharp": ("/*", "*/"),
    "swift": ("/*", "*/"),
    "ruby": ("=begin", "=end"),
    "sql": ("/*", "*/"),
    "css": ("/*", "*/"),
}

# line comment prefixes
_LINE_COMMENTS: dict[str, str] = {
    "python": "#", "javascript": "//", "typescript": "//", "java": "//",
    "kotlin": "//", "go": "//", "rust": "//", "c": "//", "cpp": "//",
    "csharp": "//", "swift": "//", "ruby": "#", "php": "//", "sql": "--",
    "bash": "#", "gradle": "//", "groovy": "//", "yaml": "#", "toml": "#",
    "r": "#", "perl": "#", "elixir": "#", "haskell": "--", "scala": "//",
    "lua": "--",
}


def protect_code(text: str, language: str = "") -> list[tuple[int, int, str]]:
    """Find protected spans (strings, comments) that compressors must not touch.

    Returns [(start, end, kind)] where kind ∈ {"string", "comment"}.
    Used by compressors to skip these regions or preserve them verbatim.
    """
    if not text:
        return []
    language = language or detect_language(text)
    spans: list[tuple[int, int, str]] = []

    # Multi-line comments
    mc = _MULTILINE_COMMENTS.get(language)
    if mc:
        start_delim, end_delim = mc
        for m in re.finditer(re.escape(start_delim) + r".*?" + re.escape(end_delim),
                             text, re.DOTALL):
            spans.append((m.start(), m.end(), "comment"))

    # Line comments (only outside string spans)
    lc = _LINE_COMMENTS.get(language)
    if lc:
        for m in re.finditer(r"^[ \t]*" + re.escape(lc) + r"[^\n]*", text, re.M):
            if not _inside_span(m.start(), spans):
                spans.append((m.start(), m.end(), "comment"))

    # Strings: single/double quotes, and backticks for JS/TS
    string_re = re.compile(r'"[^"\n\\]*(?:\\.[^"\n\\]*)*"|\'[^\'\n\\]*(?:\\.[^\'\n\\]*)*\'')
    if language in ("javascript", "typescript", "python", "kotlin", "go", "rust"):
        string_re = re.compile(
            r'`[^`\\]*(?:\\.[^`\\]*)*`'          # template literal
            r'|"[^"\n\\]*(?:\\.[^"\n\\]*)*"'
            r'|\'[^\'\n\\]*(?:\\.[^\'\n\\]*)*\''
        )
    for m in string_re.finditer(text):
        if not _inside_span(m.start(), spans):
            spans.append((m.start(), m.end(), "string"))

    spans.sort(key=lambda s: s[0])
    return spans


def _inside_span(pos: int, spans: list[tuple[int, int, str]]) -> bool:
    return any(s <= pos < e for s, e, _ in spans)


# ── code budget: what can be safely dropped ──────────────────────────────────


@dataclass
class CodeBudget:
    """Which parts of a code file can be compressed vs must stay."""
    language: str
    total_chars: int
    import_chars: int
    comment_chars: int
    blank_chars: int
    body_chars: int
    droppable_pct: float   # imports+comments+blank as share of total


def code_budget(code: str, language: str = "") -> CodeBudget:
    """Estimate how much of a file is droppable (imports, comments, blanks).

    Used by compress_with_routing to decide how aggressive it can be:
    a file that's 40% imports+comments can lose ~half without touching
    the logic.
    """
    language = language or detect_language(code)
    total = len(code)

    # blank lines
    blanks = len(re.findall(r"^\s*$", code, re.M))
    blank_chars = blanks * 1  # each blank line is 1 char (newline)

    # line comments
    lc = _LINE_COMMENTS.get(language)
    comment_chars = 0
    if lc:
        comment_chars = sum(len(m.group()) for m in
                            re.finditer(r"^[ \t]*" + re.escape(lc) + r"[^\n]*",
                                        code, re.M))

    # imports block (rough): lines starting with import/from/using/include/require
    import_re = re.compile(
        r"^\s*(?:import|from|using|include|require|#include|package|namespace)\b.*$",
        re.M,
    )
    import_chars = sum(len(m.group()) for m in import_re.finditer(code))

    body_chars = max(0, total - comment_chars - import_chars - blank_chars)
    droppable = comment_chars + import_chars + blank_chars
    return CodeBudget(
        language=language,
        total_chars=total,
        import_chars=import_chars,
        comment_chars=comment_chars,
        blank_chars=blank_chars,
        body_chars=body_chars,
        droppable_pct=round(100 * droppable / max(1, total), 1),
    )


# ── system prompt templates per language ─────────────────────────────────────


SYSTEM_PROMPTS: dict[str, str] = {
    "python": (
        "Write idiomatic Python 3. Use type hints, list/dict comprehensions "
        "where clear, context managers for resources, f-strings. Prefer "
        "stdlib over dependencies. Keep functions small and pure."
    ),
    "javascript": (
        "Write modern ES2022+. Use const/let, arrow functions, optional "
        "chaining, template literals. Handle errors with try/catch. "
        "Prefer async/await over raw promises."
    ),
    "typescript": (
        "Write strict TypeScript. Always type function parameters and "
        "returns. Use interfaces over types for objects. Avoid `any` "
        "except at API boundaries. Use generics where they add value."
    ),
    "java": (
        "Write Java 17+ following standard conventions. Use records for "
        "data carriers, sealed classes for hierarchies, streams for "
        "collections. Keep methods small, name things clearly."
    ),
    "kotlin": (
        "Write idiomatic Kotlin: data classes, null-safety with ?. and "
        "?:, extension functions, when expressions, coroutines for "
        "async. Avoid Java-style boilerplate."
    ),
    "go": (
        "Write idiomatic Go following gofmt. Use error returns (never "
        "panic), goroutines with channels for concurrency, interfaces "
        "small and focused. Handle errors explicitly with if err != nil."
    ),
    "rust": (
        "Write safe Rust. Use the borrow checker idiomatically, Result "
        "and Option instead of panics, derive where possible, small "
        "traits. Run clippy before finalizing."
    ),
    "c": (
        "Write portable C11. Use standard library, explicit error "
        "handling, correct memory management (free every malloc), "
        "const-correctness, no UB."
    ),
    "cpp": (
        "Write modern C++20. Use RAII, smart pointers, std algorithms, "
        "constexpr where possible. Avoid raw new/delete and manual loops "
        "when std algorithms fit."
    ),
    "csharp": (
        "Write modern C#. Use records, pattern matching, LINQ, async/await. "
        "Follow Microsoft naming conventions (PascalCase for methods)."
    ),
    "sql": (
        "Write portable SQL. Use parameterized queries, explicit JOINs, "
        "meaningful aliases, indexes on filter columns. Never SELECT *."
    ),
    "bash": (
        "Write POSIX-friendly bash. Quote every variable, use set -euo "
        "pipefail, prefer [[ ]] over [ ], handle errors explicitly, "
        "avoid parsing ls."
    ),
}


def system_prompt_for(language: str) -> str:
    """Return the idiomatic-code system prompt for a language (or empty)."""
    return SYSTEM_PROMPTS.get(language, "")


# ── toolchain detection ──────────────────────────────────────────────────────


@dataclass
class Toolchain:
    """What compilers/interpreters exist on this machine."""
    available: dict[str, str] = field(default_factory=dict)

    def has(self, tool: str) -> bool:
        return tool in self.available

    def path(self, tool: str) -> str:
        return self.available.get(tool, "")


def build_toolchain() -> Toolchain:
    """Detect installed compilers/interpreters/toolchains."""
    tc = Toolchain()
    for tool in (
        "python3", "python", "node", "npm", "java", "javac", "kotlinc",
        "gradle", "go", "rustc", "cargo", "gcc", "g++", "clang", "dotnet",
        "swift", "ruby", "php", "perl", "docker", "apktool", "jadx",
        "sdkmanager", "adb",
    ):
        p = shutil.which(tool)
        if p:
            tc.available[tool] = p
    return tc


__all__ = [
    "CodeBudget",
    "SYSTEM_PROMPTS",
    "Toolchain",
    "build_toolchain",
    "code_budget",
    "detect_language",
    "protect_code",
    "system_prompt_for",
]
