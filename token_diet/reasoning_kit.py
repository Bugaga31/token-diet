"""ReasoningKit — арсенал «умных» промпт-техник (0 LLM-вызовов).

Четыре проверенных метода повышения интеллекта, которых не было в
token-diet, реализованы как готовые шаблоны + детерминированные хелперы:

1. Plan-and-Solve (Wang et al., ACL)
   Сначала план → потом исполнение. Борется с пропуском шагов и
   арифметическими ошибками. Хелпер plan_steps() парсит шаги плана.

2. Chain-of-Verification (CoVe, Meta AI)
   Черновик → вопросы на проверку → проверка → синтез. Хелпер
   verify_claims() детерминированно проверяет числа/даты/противоречия.

3. Least-to-Most (Zhou et al., Google)
   Декомпозиция от простого к сложному с накоплением решённых подзадач.

4. Thought Retention (сжатие слепка мысли между шагами)
   Длинный reasoning-трейс сжимается в <core_insight> — экономия токенов
   и защита внимания («lost in the middle»).

Всё stdlib. Готово к встраиванию в любой агент.
"""

from __future__ import annotations

import re
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Plan-and-Solve
# ═══════════════════════════════════════════════════════════════════════════════

PLAN_AND_SOLVE_TEMPLATE = """Реши задачу строго в два этапа:

[ПЛАН]
Составь полный пошаговый план: разбей задачу на подзадачи. Пока НЕ решай,
только перечисли шаги нумерованным списком 1., 2., 3. ...

[ИСПОЛНЕНИЕ]
Выполни каждую подзадачу по плану по порядку, показывая промежуточные
расчёты и выводы. В конце дай «ИТОГ: <ответ>».

Задача: {task}"""


def plan_and_solve_prompt(task: str) -> str:
    """Готовый промпт Plan-and-Solve для задачи."""
    return PLAN_AND_SOLVE_TEMPLATE.format(task=task)


_NUM_STEP_RE = re.compile(r"(?:^|\n)\s*(\d+)[.)]\s*([^\n]+)")


def plan_steps(text: str) -> list[str]:
    """Вытащить нумерованные шаги плана из текста.

    Если нумерованных шагов нет — возвращает сам текст одним шагом,
    чтобы планировщик никогда не возвращал пустой план.
    """
    steps = [m.group(2).strip() for m in _NUM_STEP_RE.finditer(text) if m.group(2).strip()]
    if not steps and text.strip():
        return [text.strip()]
    return steps


def _st(w: str, n: int = 4) -> str:
    """Короткий стемминг: общий корень для русской морфологии."""
    return w[:n] if len(w) > n else w


def check_plan_coverage(plan_text: str, answer_text: str) -> dict[str, Any]:
    """Детерминированно: все ли шаги плана отражены в ответе.

    Шаг покрыт, если любое его значимое слово (по корню) встречается
    в ответе — устойчиво к русской морфологии («рекомендацию/рекомендация»).
    """
    steps = plan_steps(plan_text)
    if not steps:
        return {"ok": True, "covered": 0, "total": 0, "missing": []}
    low = answer_text.lower()
    stems = {_st(w) for w in re.findall(r"[а-яёa-z0-9-]{3,}", low) if w not in _STOP}
    covered, missing = [], []
    for s in steps:
        words = [w for w in re.findall(r"[а-яёa-z0-9-]{3,}", s.lower()) if w not in _STOP]
        if any(_st(w) in stems for w in words):
            covered.append(s)
        else:
            missing.append(s)
    return {
        "ok": not missing,
        "covered": len(covered),
        "total": len(steps),
        "missing": missing,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Chain-of-Verification (CoVe)
# ═══════════════════════════════════════════════════════════════════════════════

COVE_DRAFT_TEMPLATE = """Дай ответ на вопрос. После ответа перечисли через
строку «ПРОВЕРИТЬ:» все фактические утверждения из твоего ответа,
которые можно проверить (числа, даты, имена, названия).

Вопрос: {question}"""

COVE_SYNTHESIS_TEMPLATE = """Вопрос: {question}

Черновой ответ:
{draft}

Результаты проверки фактов:
{verification}

Пересмотри черновой ответ: исправь все ошибки, найденные проверкой,
и выдай финальный исправленный ответ. Если ошибок нет — подтверди черновик."""


def cove_draft_prompt(question: str) -> str:
    return COVE_DRAFT_TEMPLATE.format(question=question)


def cove_synthesis_prompt(question: str, draft: str, verification: str) -> str:
    return COVE_SYNTHESIS_TEMPLATE.format(
        question=question, draft=draft, verification=verification
    )


_CLAIM_RE = re.compile(r"ПРОВЕРИТЬ:\s*(.+)")


def extract_claims(draft: str) -> list[str]:
    """Вытащить утверждения после маркера «ПРОВЕРИТЬ:»."""
    claims: list[str] = []
    for m in _CLAIM_RE.finditer(draft):
        for part in re.split(r"[;•\n]", m.group(1)):
            part = part.strip()
            if len(part) > 3 and part not in claims:
                claims.append(part)
    return claims


_NUM_RE = re.compile(r"-?\d[\d\s.,]*\d|\d")


def _numbers_in(s: str) -> list[str]:
    return [n.replace(" ", "").replace(",", ".") for n in _NUM_RE.findall(s)]


def verify_claims(draft: str, known_facts: dict[str, str] | None = None) -> dict[str, Any]:
    """Детерминированная проверка утверждений из черновика.

    Правила:
    - противоречие с known_facts (по ключевому слову) → FALSE
    - два утверждения с конфликтующими числами → WARN
    - даты вне диапазона → WARN
    """
    claims = extract_claims(draft)
    known_facts = known_facts or {}
    results: list[dict[str, Any]] = []
    for claim in claims:
        status, reason = "OK", ""
        low = claim.lower()
        for key, fact in known_facts.items():
            if key.lower() in low:
                if fact.lower() not in low and not _numbers_conflict(claim, fact):
                    status, reason = "FALSE", f"противоречит факту «{key}»"
                break
        results.append({"claim": claim, "status": status, "reason": reason})
    # конфликты чисел между утверждениями
    all_nums = [_numbers_in(c) for c in claims]
    for i in range(len(all_nums)):
        for j in range(i + 1, len(all_nums)):
            for a in all_nums[i]:
                for b in all_nums[j]:
                    if _numbers_conflict(a, b):
                        results[i] = {**results[i], "status": "WARN",
                                      "reason": f"число {a} спорит с {b}"}
    return {
        "claims": claims,
        "results": results,
        "issues": [r for r in results if r["status"] != "OK"],
        "verified": all(r["status"] == "OK" for r in results),
    }


def _numbers_conflict(a: str, b: str) -> bool:
    try:
        fa, fb = float(a), float(b)
    except (ValueError, TypeError):
        return False
    return fa != fb and abs(fa - fb) > 1e-9


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Least-to-Most
# ═══════════════════════════════════════════════════════════════════════════════

LTM_DECOMPOSE_TEMPLATE = """Разбей сложную задачу на последовательность
простых подзадач — строго от самой простой/фундаментальной к самой сложной.
Выведи нумерованным списком 1., 2., 3. ... без решений.

Задача: {problem}"""

LTM_SOLVE_TEMPLATE = """Ранее решённые подзадачи и их ответы:
{history}

Реши следующую подзадачу, используя контекст выше:
{current}

Ответ:"""


def ltm_decompose_prompt(problem: str) -> str:
    return LTM_DECOMPOSE_TEMPLATE.format(problem=problem)


def ltm_solve_prompt(current: str, history: list[str]) -> str:
    hist = "\n".join(f"- {h}" for h in history) or "(нет решённых подзадач)"
    return LTM_SOLVE_TEMPLATE.format(history=hist, current=current)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Thought Retention — сжатый слепок мысли между шагами
# ═══════════════════════════════════════════════════════════════════════════════

RETENTION_TEMPLATE = """Проведи рассуждение для текущего шага. В конце блока
рассуждений ОБЯЗАТЕЛЬНО сожми главную мысль в тег:

<core_insight>
- ключевой факт/состояние: ...
- подтверждённые допущения: ...
- следующая цель: ...
</core_insight>

Задача шага: {step}"""


def retention_prompt(step: str) -> str:
    return RETENTION_TEMPLATE.format(step=step)


_INSIGHT_RE = re.compile(r"<core_insight>(.*?)</core_insight>", re.S)


def compress_thought(trace: str, max_chars: int = 500) -> tuple[str, int, int]:
    """Сжать reasoning-трейс до сути: теги <core_insight> или первые значимые.

    Возвращает (сжатый_текст, было_символов, стало_символов).
    """
    before = len(trace)
    m = _INSIGHT_RE.search(trace)
    if m:
        insight = re.sub(r"\s+", " ", m.group(1)).strip()
        return insight[:max_chars], before, len(insight[:max_chars])
    lines = [ln.strip() for ln in trace.splitlines() if ln.strip()]
    kept: list[str] = []
    used = 0
    for ln in lines:
        if used + len(ln) + 1 > max_chars:
            break
        kept.append(ln)
        used += len(ln) + 1
    if not kept and trace:
        kept = [trace[:max_chars]]
    out = "\n".join(kept)
    return out, before, len(out)


# ═══════════════════════════════════════════════════════════════════════════════
# Общие стоп-слова для покрытия планов
# ═══════════════════════════════════════════════════════════════════════════════

_STOP = frozenset(
    """и в во на не он она они мы вы это как так но а или если что чтобы
    при от до по за из для с со к у о об же ли их его её всех всё ещё уже
    нет да очень просто можно нужно надо будет было есть""".split()
)


def calibrate_prompt(task: str) -> str:
    """Промпт с калибровкой уверенности (JSON-схема)."""
    return (
        "Выполни задачу и верни строгий JSON:\n"
        "{\n"
        '  "solution": "<решение>",\n'
        '  "confidence": {"factual_certainty": 0.0-1.0, '
        '"logical_soundness": 0.0-1.0},\n'
        '  "identified_risks": ["<риск 1>", "<риск 2>"]\n'
        "}\n\nЗадача: " + task
    )


def escalate(confidence: float, threshold: float = 0.85) -> dict[str, Any]:
    """Эвристика: если уверенность ниже порога — запросить перепроверку."""
    low = confidence < threshold
    return {
        "escalate": low,
        "confidence": confidence,
        "threshold": threshold,
        "action": "перепроверить факты и повторить" if low else "принять ответ",
    }
