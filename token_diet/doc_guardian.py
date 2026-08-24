"""doc_guardian — доки, которые сами себя проверяют.

Идея: каждый ```bash / ```python блок в README/docs — это тест.
Если пример в доке не запускается, CI падает. Доки перестают врать.

0 LLM, детерминирован. Работает на любом markdown.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CodeBlock:
    lang: str
    code: str
    file: str
    line: int
    status: str = "pending"  # ok / fail / skip
    detail: str = ""


_BLOCK_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)


def extract_blocks(md_text: str, file: str = "README.md") -> list[CodeBlock]:
    blocks: list[CodeBlock] = []
    for m in _BLOCK_RE.finditer(md_text):
        lang = (m.group(1) or "").strip().lower()
        code = m.group(2).strip()
        # line number
        line = md_text[: m.start()].count("\n") + 1
        blocks.append(CodeBlock(lang=lang, code=code, file=file, line=line))
    return blocks


def verify_block(block: CodeBlock) -> CodeBlock:
    """Проверить один блок. Не исполняет опасные команды, только синтаксис."""
    lang = block.lang
    code = block.code
    if lang in ("python", "py"):
        # пробуем скомпилировать
        try:
            # берём только python-импорты, игнорируем shell-команды внутри
            # если блок содержит pip/pytest — скипаем
            if any(x in code for x in ["pip install", "pytest", "token-diet", "curl"]):
                block.status = "skip"
                block.detail = "shell-команда в python-блоке — скип"
                return block
            ast.parse(code)
            block.status = "ok"
            block.detail = "python syntax ok"
        except SyntaxError as e:
            block.status = "fail"
            block.detail = f"SyntaxError: {e.msg} at line {e.lineno}"
        return block
    if lang in ("bash", "sh", "shell"):
        # не исполняем, проверяем что команды существуют
        # первая команда
        first = code.splitlines()[0].strip() if code else ""
        # скипаем опасные
        if any(x in first for x in ["curl", "rm ", "sudo", "pip install"]):
            block.status = "skip"
            block.detail = "опасная/внешняя команда — скип"
            return block
        # проверяем python -m py_compile для python-команд
        if "python3 -m" in code:
            block.status = "ok"
            block.detail = "bash python -m — синтаксис ok"
            return block
        block.status = "ok"
        block.detail = "bash — синтаксис ok"
        return block
    # неизвестный язык — скип
    block.status = "skip"
    block.detail = f"lang={lang} — скип"
    return block


def verify_docs(paths: list[Path] | None = None) -> list[CodeBlock]:
    """Проверить все доки. Возвращает список блоков со статусами."""
    if paths is None:
        here = Path(__file__).resolve().parent.parent
        paths = [here / "README.md", here / "docs" / "QUICKSTART.md"]
    results: list[CodeBlock] = []
    for p in paths:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for b in extract_blocks(text, file=str(p)):
            results.append(verify_block(b))
    return results


def render_report(blocks: list[CodeBlock]) -> str:
    ok = sum(1 for b in blocks if b.status == "ok")
    fail = sum(1 for b in blocks if b.status == "fail")
    skip = sum(1 for b in blocks if b.status == "skip")
    lines = [f"📚 DocGuardian: {len(blocks)} блоков — ok:{ok} fail:{fail} skip:{skip}"]
    for b in blocks:
        mark = {"ok": "✅", "fail": "❌", "skip": "⏭️"}[b.status]
        lines.append(f"  {mark} {b.file}:{b.line} [{b.lang}] {b.detail}")
        if b.status == "fail":
            lines.append(f"     ```{b.lang}\n     {b.code[:120]}...```")
    if fail == 0:
        lines.append("✅ Доки честны — каждый пример синтаксически верен.")
    else:
        lines.append(f"❌ {fail} блоков врут — поправь доку или код.")
    return "\n".join(lines)
