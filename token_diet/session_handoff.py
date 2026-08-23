"""SessionHandoff — само-суммаризация контекста (реверс-инжиниринг buzz-agent).

Взято из Buzz (Block Inc., Apache 2.0), VISION_AGENT.md:
«When context fills up, a session summarizes its own history and continues.»

Мечта генерала: «запущу Hermes/любую ИИ — и она помнит всё». Здесь —
механика: НОВАЯ сессия читает ОДИН компактный блок и продолжает с того же
места, не перечитывая простыни истории.

Что входит в handoff (всё из НАШИХ систем, 0 LLM-вызовов):
- ФАКТЫ из переданной истории (тикеры, цены, %, даты — детерминированный
  экстрактор, без нейронок);
- РЕШЕНИЯ из hash-chain журнала (audit_chain) — их нельзя подделать;
- УРОКИ из памяти Obsidian (файлы «Урок: …»);
- ПРАВИЛА из YAML-движка (что активно сейчас);
- СОСТОЯНИЕ: сколько места сэкономили (сжатие).

Детерминированно, файл-хранилище, 0 LLM-вызовов.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional

TICKER_RE = re.compile(r"\b[A-ZА-Я]{3,6}\b(?=\s*[:\-–]?\s*\d)")
PRICE_RE = re.compile(r"\d[\d\s.,]*\s*(?:₽|руб(?:\.|лей)?|\$|%|\s%|проц(?:ента|ентов)?)")
DATE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b|\b\d{4}-\d{2}-\d{2}\b")

MAX_FACTS = 40
MAX_LESSONS = 6
MAX_DECISIONS = 15
MAX_RULES = 10


def extract_facts(text: str) -> list[str]:
    """Детерминированная выжимка фактов: тикеры, цены/проценты, даты."""
    facts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 4 or len(line) > 200:
            continue
        hits = []
        for m in TICKER_RE.finditer(line):
            hits.append(m.group(0))
        for m in PRICE_RE.finditer(line):
            hits.append(m.group(0).strip())
        for m in DATE_RE.finditer(line):
            hits.append(m.group(0))
        if hits:
            facts.append(line[:180])
    return _dedup(facts)[:MAX_FACTS]


def _dedup(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for ln in lines:
        key = re.sub(r"\s+", " ", ln).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(ln)
    return out


def _count_chars(x: str) -> int:
    return len(x)


def compress_history(history: str, max_chars: int = 6000) -> list[str]:
    """Сжать историю: убрать дубли, шум, оставить несущие строки."""
    lines = [ln.strip() for ln in history.splitlines() if ln.strip()]
    kept: list[str] = []
    total = 0
    for ln in _dedup(lines):
        # шум: огромные дампы, повторяющиеся логи
        if len(ln) > 400:
            ln = ln[:200] + "… [обрезано]"
        if total + len(ln) > max_chars:
            break
        kept.append(ln)
        total += len(ln)
    return kept


def chain_decisions(chain_path: Optional[Path] = None, limit: int = MAX_DECISIONS) -> list[str]:
    """Решения из hash-chain (prediction/decision/trade) — свежие первыми."""
    try:
        from token_diet.audit_chain import AuditChain
        chain = AuditChain(chain_path) if chain_path else AuditChain()
        out = []
        for e in reversed(chain.entries(limit=limit * 3)):
            if e.action not in ("prediction_made", "decision_made",
                                "trade_executed", "goal_set", "lesson_learned"):
                continue
            d = e.detail or {}
            what = d.get("what") or d.get("ticker") or d.get("note") or ""
            line = f"#{e.seq} {e.action}: {what} {('@' + e.created_at[:16]) if what else e.created_at[:16]}"
            out.append(line.strip())
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001
        return []


def vault_lessons(vault_dir: Optional[Path] = None, limit: int = MAX_LESSONS) -> list[str]:
    """Уроки из Obsidian: файлы с именем «Урок …» — первые строки."""
    try:
        from token_diet.memory_cli import vault_path
        base = vault_dir or vault_path()
    except Exception:  # noqa: BLE001
        base = vault_dir
    if not base or not Path(base).exists():
        return []
    out: list[str] = []
    for f in sorted(Path(base).glob("*Урок*")):
        try:
            head = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        title = f.stem
        first = next((l.strip() for l in head if l.strip() and not l.startswith("---")), "")
        out.append(f"«{title}»{': ' + first[:120] if first else ''}")
        if len(out) >= limit:
            break
    return out


def active_rules(rules_path: Optional[Path] = None, limit: int = MAX_RULES) -> list[str]:
    """Активные правила из YAML-движка."""
    try:
        from token_diet.workflow_engine import WorkflowEngine
        eng = WorkflowEngine(Path(rules_path) if rules_path else None)
        eng.load_rules()
        return [f"{w.get('name')} [{ (w.get('trigger') or {}).get('on')}]"
                for w in eng.workflows[:limit]]
    except Exception:  # noqa: BLE001
        return []


def estimate_savings(source_chars: int, handoff_chars: int) -> dict:
    """Экономия места (и токенов) от handoff."""
    ratio = round(handoff_chars / max(1, source_chars) * 100, 1)
    return {
        "source_chars": source_chars,
        "handoff_chars": handoff_chars,
        "compressed_to_pct": ratio,
        "saved_pct": round(100 - ratio, 1),
    }


def build_handoff(history: Optional[str] = None, max_history_chars: int = 6000,
                  include_chain: bool = True, include_lessons: bool = True,
                  include_rules: bool = True,
                  chain_path: Optional[Path] = None,
                  vault_dir: Optional[Path] = None,
                  rules_path: Optional[Path] = None) -> dict:
    """Собрать handoff-блок: факты истории + решения + уроки + правила."""
    sections: dict[str, list[str]] = {}
    source_chars = len(history or "")

    if history:
        kept = compress_history(history, max_history_chars)
        facts = extract_facts("\n".join(kept))
        if facts:
            sections["ФАКТЫ ИЗ ИСТОРИИ"] = facts
        if not facts and kept:
            sections["ХОД ИСТОРИИ"] = kept[:10]
    if include_chain:
        dec = chain_decisions(chain_path)
        if dec:
            sections["РЕШЕНИЯ (hash-chain, не подделать)"] = dec
    if include_lessons:
        les = vault_lessons(vault_dir)
        if les:
            sections["УРОКИ ИЗ ПАМЯТИ"] = les
    if include_rules:
        rules = active_rules(rules_path)
        if rules:
            sections["АКТИВНЫЕ ПРАВИЛА"] = rules

    handoff_text = render_handoff(sections)
    stats = estimate_savings(source_chars, len(handoff_text))
    return {"sections": sections, "handoff_text": handoff_text, "stats": stats}


def render_handoff(sections: dict[str, list[str]]) -> str:
    out = ["=== ПРОДОЛЖЕНИЕ СЕССИИ (сгенерировано token-diet handoff) ==="]
    if not sections:
        out.append("(пусто — нечего переносить)")
        return "\n".join(out)
    for title, lines in sections.items():
        out.append("")
        out.append(f"## {title}")
        out.extend(f"- {ln}" for ln in lines)
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="token-diet handoff",
        description="Компактный слепок сессии: факты + решения + уроки — чтобы новая ИИ продолжила с того же места")
    p.add_argument("--history", default=None, help="файл с историей/транскриптом (необязательно)")
    p.add_argument("--max", type=int, default=6000, help="макс. символов истории в handoff")
    p.add_argument("--no-chain", action="store_true", help="без решений из hash-chain")
    p.add_argument("--no-lessons", action="store_true", help="без уроков из памяти")
    p.add_argument("--no-rules", action="store_true", help="без правил")
    p.add_argument("--out", default=None, help="сохранить handoff в файл")
    args = p.parse_args(argv)

    history = None
    if args.history:
        try:
            history = Path(args.history).read_text(encoding="utf-8")
        except OSError as e:
            print(f"не могу прочитать {args.history}: {e}")
            return 2

    h = build_handoff(history, max_history_chars=args.max,
                      include_chain=not args.no_chain,
                      include_lessons=not args.no_lessons,
                      include_rules=not args.no_rules)
    print(h["handoff_text"])
    st = h["stats"]
    print(f"\n[handoff] источник {st['source_chars']} симв. → слепок {st['handoff_chars']} "
          f"({st['compressed_to_pct']}%, экономия {st['saved_pct']}%)")
    if args.out:
        Path(args.out).write_text(h["handoff_text"], encoding="utf-8")
        print(f"[handoff] сохранено: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
