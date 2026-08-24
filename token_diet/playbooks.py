"""Playbook Memory — процедурная память успешных решений (SAGE-подход).

Две свежие техники 2026, реверс-инжиниринг, 0 LLM-вызовов:

1. SAGE (Sequential Rollout with Skill-Integrated Rewards) — агент сохраняет
   УСПЕШНЫЕ цепочки шагов в персистентную библиотеку «плейбуков». Перед
   выполнением похожего запроса ищет готовый проверенный рецепт и вставляет
   его в промпт как few-shot пример → меньше тупиковых веток, меньше токенов,
   выше качество. (mistake_learner учится на ОШИБКАХ — а это на УСПЕХАХ.)

2. Context Engineering / Tool Result Clearing (Anthropic) — после того как
   агент перешёл к следующему шагу, громоздкий результат предыдущего тула
   заменяется маркером «[Result cleared: N tokens omitted. Summary: ...]».
   Контекстное окно не замусоривается тяжёлыми JSON/HTML-дампaми.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


# ── стемминг ─────────────────────────────────────────────────────────────
def _stem(w: str) -> str:
    w = w.lower()
    if len(w) > 5:
        w = w.rstrip("аяоеёуюыиэьийовымихое")  # noqa: B005  # charset strip is the point: Russian vowel endings
        if len(w) < 3:
            w = w.lower()
    return w


@dataclass
class Playbook:
    id: str
    goal: str
    steps: list[str]
    success_criteria: str
    outcome: str = "success"
    uses: int = 0
    created_at: str = ""

    def render(self) -> str:
        return (f"ПРОВЕРЕННЫЙ РЕЦЕПТ [{self.goal}]:\n"
                + "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(self.steps))
                + f"\n  ✓ критерий успеха: {self.success_criteria}"
                + f"\n  (использован {self.uses} раз)")


class PlaybookStore:
    """Хранилище успешных решений. JSON на диске, работает всегда."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = os.environ.get(
                "TOKEN_DIET_PLAYBOOKS",
                str(Path.home() / ".token-diet" / "playbooks.json"),
            )
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.playbooks: list[Playbook] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for p in data.get("playbooks", []):
                self.playbooks.append(Playbook(**p))
        except Exception:
            self.playbooks = []

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"playbooks": [vars(p) for p in self.playbooks]},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    # ── запись успешного решения ───────────────────────────────────
    def add(self, goal: str, steps: list[str], success_criteria: str,
            outcome: str = "success") -> Playbook:
        now = datetime.now(timezone.utc).isoformat()
        pb = Playbook(
            id=f"pb_{int(datetime.now(timezone.utc).timestamp())}_{len(self.playbooks)}",
            goal=goal, steps=[s for s in steps if s.strip()],
            success_criteria=success_criteria, outcome=outcome,
            created_at=now,
        )
        self.playbooks.append(pb)
        self.save()
        return pb

    # ── поиск похожего рецепта ─────────────────────────────────────
    def find(self, query: str, limit: int = 2,
             only_success: bool = True) -> list[Playbook]:
        q_terms = {_stem(w) for w in re.findall(r"[a-zа-яё0-9]{3,}", query.lower())}
        scored: list[tuple[float, Playbook]] = []
        for pb in self.playbooks:
            if only_success and pb.outcome != "success":
                continue
            hay = {_stem(w) for w in re.findall(
                r"[a-zа-яё0-9]{3,}", pb.goal + " " + " ".join(pb.steps))}
            overlap = q_terms & hay
            if not overlap:
                continue
            score = len(overlap) * 2.0
            score += min(pb.uses, 5) * 0.5      # популярность
            score += 0.3                          # свежесть-премия
            scored.append((score, pb))
        scored.sort(key=lambda x: -x[0])
        return [pb for _, pb in scored[:limit]]

    def prompt_block(self, query: str, limit: int = 2) -> str:
        """Few-shot блок: готовый проверенный рецепт для похожей задачи."""
        hits = self.find(query, limit)
        if not hits:
            return ""
        lines = ["Проверенные рецепты из прошлого опыта (few-shot):", ""]
        for pb in hits:
            lines.append(pb.render())
            lines.append("")
        return "\n".join(lines)

    def remember(self, goal: str, steps: list[str],
                 success_criteria: str) -> str:
        """Запомнить и вернуть маркер (удобно как мета-тул агента)."""
        pb = self.add(goal, steps, success_criteria)
        return f"[playbook saved: {pb.id}] {pb.goal} → {len(pb.steps)} шагов"

    def stats(self) -> dict:
        return {"playbooks": len(self.playbooks),
                "total_uses": sum(p.uses for p in self.playbooks)}


# ═════════════════════════════════════════════════════════════════════════
# 2. Tool Result Clearing — маркерные заглушки вместо тяжёлых результатов
# ═════════════════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    # грубая оценка: ~4 символа на токен для смешанного текста
    return max(1, len(text) // 4)


def cleared_marker(content: str, summary: str | None = None,
                   max_summary_chars: int = 180) -> str:
    """Заменяет тяжёлый результат тула на компактный маркер.

    >>> cleared_marker("x" * 1000, "получено 3 ключа")
    '[Result cleared: 250 tokens omitted. Summary: получено 3 ключа]'
    """
    n = _estimate_tokens(content)
    if summary is None:
        flat = re.sub(r"\s+", " ", content).strip()
        if len(flat) > max_summary_chars:
            summary = flat[:max_summary_chars] + "…"
        else:
            summary = flat
    return f"[Result cleared: {n} tokens omitted. Summary: {summary}]"


def prune_history(messages: list[dict], keep_last_tool: bool = True,
                  clear_after_steps: int = 1) -> list[dict]:
    """Вытеснение: тяжёлые результаты тулов → маркеры.

    Оставляем последний (текущий) результат тула нетронутым, все более
    старые результаты заменяем на [Result cleared: N tokens...] с краткой
    выжимкой (первые 140 символов). Это техника Context Engineering:
    контекст не растёт от шага к шагу.
    """
    out: list[dict] = []
    tool_result_idx = [i for i, m in enumerate(messages)
                       if m.get("role") == "tool" and m.get("content")]
    # сколько результатов тулов оставить нетронутыми (с конца)
    keep = tool_result_idx[-1:] if keep_last_tool and tool_result_idx else []
    if keep and len(tool_result_idx) > 1 and clear_after_steps > 1:
        # если шагов больше, чем порог — чистим и предпоследние тоже
        keep = tool_result_idx[-(clear_after_steps):]
    for i, m in enumerate(messages):
        if i in keep:
            out.append(m)
            continue
        if m.get("role") == "tool" and m.get("content"):
            content = m["content"]
            text = content if isinstance(content, str) else str(content)
            out.append({"role": "tool",
                        "content": cleared_marker(text)})
        else:
            out.append(m)
    return out


def history_budget(messages: list[dict]) -> dict:
    """Сколько токенов съедают результаты тулов (для отчёта экономии)."""
    total, tool_total = 0, 0
    for m in messages:
        c = m.get("content", "")
        n = _estimate_tokens(c if isinstance(c, str) else str(c))
        total += n
        if m.get("role") == "tool":
            tool_total += n
    return {"total_tokens": total, "tool_tokens": tool_total,
            "tool_pct": round(tool_total / total * 100) if total else 0}
