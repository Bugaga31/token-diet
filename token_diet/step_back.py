"""Step-Back prompting + Analogical reasoning (DeepMind 2023).

Две техники «интеллекта через абстракцию» — их в арсенале не было:

1. **Take a Step Back**: вместо лобового ответа на конкретный вопрос сначала
   абстрагируйся к общему принципу, ответь на него, потом примени к частному.
   (DeepMind, 2023: +до 27% точности на физике/химии, работает и на общих задачах)

2. **Analogical reasoning**: найди похожую уже решённую задачу (из playbooks /
   памяти) и перенеси её решение на текущую. Мозг человека так и думает.

Всё — детерминированная переформулировка + поиск по ключевым словам, 0 LLM-вызовов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Step-Back: конкретное → общий принцип
# ═══════════════════════════════════════════════════════════════════════════════

_GENERALIZATION = [
    # (регулярка на конкретику, общая переформулировка)
    (re.compile(r"почему\s+(.+)", re.IGNORECASE), "Какие общие принципы/причины объясняют явления такого рода: {topic}?"),
    (re.compile(r"как\s+(?:сделать|построить|написать|решить|исправить|настроить)\s+(.+)", re.IGNORECASE),
     "Какова общая методика/алгоритм решения задач такого типа: {topic}?"),
    (re.compile(r"(?:что|кто)\s+лучше[:,\s]+(.+?)\s+(?:или|vs)\s+(.+)", re.IGNORECASE),
     "По каким общим критериям сравнивают сущности вроде «{a}» и «{b}»?"),
    (re.compile(r"сколько\s+(?:стоит|будет)\s+(.+)", re.IGNORECASE),
     "Каков общий порядок величины и факторы, определяющие стоимость/значение: {topic}?"),
    (re.compile(r"(?:найди|вычисли|посчитай)\s+(.+)", re.IGNORECASE),
     "Какая общая формула/метод решает задачи вычисления: {topic}?"),
]


def step_back_question(question: str) -> str:
    """Переформулировать конкретный вопрос в более общий «принципиальный» вопрос.

    Возвращает общий вопрос или исходный, если переформулировка не удалась.
    """
    q = (question or "").strip()
    for rx, tmpl in _GENERALIZATION:
        m = rx.search(q)
        if m:
            groups = m.groups()
            topic = groups[0].strip() if groups else ""
            if len(groups) == 2:
                return tmpl.format(a=groups[0].strip(), b=groups[1].strip())
            return tmpl.format(topic=topic)
    # общий фолбэк: любой вопрос → «какой общий принцип за этим стоит»
    return f"Какой общий принцип или категория лежит в основе вопроса: «{q}»?"


def step_back_prompt(question: str) -> str:
    """Двухступенчатый промпт: сначала общий принцип, потом применение к частному.

    Это scaffold для ЛЮБОЙ модели — она сначала «делает шаг назад»,
    а потом спускается к конкретике.
    """
    general = step_back_question(question)
    return (
        f"Сделай шаг назад. Сначала ответь на общий вопрос:\n"
        f"«{general}»\n\n"
        f"Затем, используя этот общий принцип, реши исходный вопрос:\n"
        f"«{question}»"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Аналогии: найти похожую решённую задачу и перенести решение
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Case:
    title: str
    problem: str
    solution: str = ""
    tags: list[str] = field(default_factory=list)


_STOP_RU = {"и", "в", "на", "с", "по", "для", "как", "что", "чем", "при", "от",
            "из", "к", "о", "об", "не", "ли", "а", "но", "то", "же", "это", "или",
            "the", "and", "of", "to", "for", "with", "a", "an", "in", "on", "is"}


def analogy_keywords(text: str, limit: int = 8) -> list[str]:
    """Вытащить значимые слова для поиска аналогий (без стоп-слов, со стеммингом)."""
    words = re.findall(r"[а-яёa-z0-9]{3,}", (text or "").lower())
    seen: list[str] = []
    for w in words:
        stem = w[:5]  # грубый стемминг: «золотом»/«золото» → «золот»
        if w in _STOP_RU or stem in _STOP_RU:
            continue
        if stem not in [s[:5] for s in seen]:
            seen.append(w)
        if len(seen) >= limit:
            break
    return seen


def _overlap(a: list[str], b: list[str]) -> int:
    a_stems = {w[:5] for w in a}
    return sum(1 for w in b if w[:5] in a_stems)


def find_analogies(question: str, cases: list[Case], min_overlap: int = 1) -> list[tuple[Case, int]]:
    """Найти кейсы, похожие на вопрос по пересечению ключевых слов.

    Возвращает список (кейс, число совпадений), отсортированный по релевантности.
    """
    qk = analogy_keywords(question)
    scored: list[tuple[Case, int]] = []
    for c in cases:
        ck = analogy_keywords(c.problem + " " + c.title) + [t.lower() for t in c.tags]
        ov = _overlap(qk, ck)
        if ov >= min_overlap:
            scored.append((c, ov))
    scored.sort(key=lambda x: -x[1])
    return scored


def analogy_prompt(question: str, cases: list[Case], top_k: int = 2) -> str:
    """Промпт с аналогиями: «вот похожая решённая задача, примени тот же подход»."""
    hits = find_analogies(question, cases)[:top_k]
    if not hits:
        return ""
    lines = []
    for c, ov in hits:
        title = f"[{c.title}] " if c.title else ""
        lines.append(f"• {title}Задача: {c.problem}\n  Решение: {c.solution or '(нет записи решения)'}")
    return (
        f"Похожие уже решённые задачи (примени тот же подход):\n"
        + "\n".join(lines)
        + f"\n\nТеперь реши исходную задачу по аналогии: «{question}»"
    )
