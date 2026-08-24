"""Factory Droid — реверс-инжиниринг Factory AI (Code Droid) в чистом Python.

УРОК 18.08.2026: генерал дал https://factory.com/product/cli и статью про
Droid — Factory AI собрала автономного программиста из трёх паттернов:

┌───────────────────────────┬─────────────────────────────────────────────────┐
│ Паттерн Factory AI        │ Реализация в token-diet (0 LLM-вызовов, <1ms)   │
├───────────────────────────┼─────────────────────────────────────────────────┤
│ Coordinator + ролевые     │ Coordinator.delegate(): роль (code/review/test/ │
│ Droids (code/review/test/ │ docs/knowledge) → свой специалист-дроид,        │
│ docs/knowledge)           │ структурный хендофф → следующий дроид           │
│ Ambiguity engine          │ ambiguity(): «спросить уточнение vs действовать»│
│ («спросить или делать»)   │ по неоднозначности запроса (детерминированно)   │
│ Knowledge droid           │ build_repo_knowledge(): индекс репозитория +    │
│ (synthetic insights)      │ синтетические инсайты — не перечитывать код     │
└───────────────────────────┴─────────────────────────────────────────────────┘

Всё детерминированное, без нейронок: генерал просил «ничего невозможного нет,
но токены не жечь». Дроиды выдают план/вопросы/инсайты — решение принимает
координатор (как я), либо модель через --omni.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 1. Ролевые дроиды (как у Factory: code / review / test / docs / knowledge)
#    Каждый дроид — узкий специалист со своей системной ролью и правилами.
# ─────────────────────────────────────────────────────────────────────────────

# Ключевые слова задач → роли. Используется и в classify, и в планировщике.
ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "code": ("напиши", "реализуй", "implement", "сделай функцию", "написать код",
             "добавь модуль", "функцию", "класс", "скрипт", "код для", "фичу",
             "feature", "create", "build", "write code", "кодить"),
    "review": ("ревью", "review", "проверь код", "найди баги", "оцени код",
               "code review", "найди ошибки", "аудит кода", "качество"),
    "test": ("тест", "test", "проверь работу", "напиши тесты", "юнит",
             "проверить", "убедись", "покрытие"),
    "docs": ("документ", "docs", "инструкция", "readme", "объясни как",
             "напиши гайд", "описание", "интерфейс", "документация"),
    "knowledge": ("что умеет", "разбери", "обзор", "архитектура", "как устроен",
                  "инсайт", "исследуй", "explore", "структура проекта", "знания"),
}

ROLE_SYSTEM_PROMPTS: dict[str, str] = {
    "code": (
        "Ты — Code Droid (Factory AI). Пишешь минимальный, читаемый код по "
        "конвенциям проекта. Сначала 1-строчный план, потом код, потом как проверить."
    ),
    "review": (
        "Ты — Review Droid. Ищешь баги, дыры безопасности и узкие места. "
        "Формат: КРИТИЧНО / ВАЖНО / МЕЛОЧЬ + конкретная строка и фикс."
    ),
    "test": (
        "Ты — Test Droid. Пишешь тесты, которые доказывают работу, "
        "а не «проходят ради галочки». Офлайн, без сети."
    ),
    "docs": (
        "Ты — Docs Droid. Пишешь короткие, точные инструкции для человека и "
        "для нейронки: команды, примеры, ожидаемый результат."
    ),
    "knowledge": (
        "Ты — Knowledge Droid. Синтезируешь инсайты из репозитория/памяти: "
        "паттерны, связи, что где лежит — чтобы не перечитывать код заново."
    ),
}


@dataclass
class DroidSpec:
    """Спецификация дроида: роль, имя, системный промпт, специализация."""
    role: str
    name: str
    system_prompt: str
    focus: tuple[str, ...] = ()


DROIDS: dict[str, DroidSpec] = {
    role: DroidSpec(role=role, name=f"{role.capitalize()}Droid",
                    system_prompt=ROLE_SYSTEM_PROMPTS[role],
                    focus=ROLE_KEYWORDS[role])
    for role in ROLE_SYSTEM_PROMPTS
}


def classify_task(task: str) -> str:
    """Определить роль дроида по задаче (по ключевым словам). Без LLM.

    Специфичные маркеры («инструкция», «документация», «тесты», «баги»)
    весят больше общих («напиши») — иначе «напиши инструкцию» уйдёт в code.
    """
    low = task.lower()
    scores: Counter[str] = Counter()
    for role, kws in ROLE_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in low:
                scores[role] += 1
    if not scores:
        return "code"  # по умолчанию — делать
    # специфичные маркеры — двойной вес
    specific = {
        "docs": ("инструкция", "документация", "гайд", "документ", "readme"),
        "test": ("тест", "тесты", "юнит", "покрытие"),
        "review": ("баги", "ошибки", "ревью"),
        "knowledge": ("архитектура", "обзор", "разбери", "исследуй"),
    }
    for role, kws in specific.items():
        for kw in kws:
            if kw.lower() in low:
                scores[role] += 1
    return scores.most_common(1)[0][0]


def droid_plan(task: str, role: str | None = None) -> dict:
    """План дроида: роль, шаги, что проверить. Детерминированный каркас."""
    role = role or classify_task(task)
    spec = DROIDS.get(role, DROIDS["code"])
    return {
        "role": role,
        "droid": spec.name,
        "focus": list(spec.focus[:4]),
        "steps": [
            "понять задачу и ограничения (что можно, что нельзя)",
            f"выполнить как {spec.name}: применить фокус {spec.focus[0] if spec.focus else '—'}",
            "проверить результат (для кода — запустить, для текста — перечитать)",
            "отдать координатору структурированный результат",
        ],
        "system_prompt": spec.system_prompt,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Ambiguity engine (Factory: «спросить vs действовать»)
#    Модель-координатор решает: запрос неоднозначен → задать уточнение;
#    однозначен → действовать. Детерминированная версия: эвристики.
# ─────────────────────────────────────────────────────────────────────────────

# Слова, выдающие неоднозначность: нет конкретики
_AMBIGUITY_MARKERS = (
    "что-то", "как-то", "лучше", "похоже", "наверное", "возможно", "может",
    "условно", "примерно", "какой-нибудь", "разные", "всякие", "как хочешь",
    "сам реши", "something", "maybe", "perhaps", "somehow", "whatever",
    "etc", "и т.д.", "и т.п.", "ну такое", "типа",
)
# Слова, которые, наоборот, говорят «действуй сам, не спрашивай»
_ACTION_MARKERS = (
    "сам реши", "сам действуй", "без вопросов", "не спрашивай", "делай сам",
    "разрешаю", "поручаю", "ты решаешь", "у тебя полная свобода", "на твоё усмотрение",
    "you decide", "act on your own", "do it", "don't ask",
)
# Сильное действие: задача конкретна сама по себе
_STRONG_ACTIONS = (
    "напиши", "создай", "исправь", "добавь", "удали", "переведи", "купи",
    "продай", "прочитай", "найди", "покажи", "скажи", "выполни", "реализуй",
    "implement", "install", "настрой", "выстави", "открой", "закрой",
)
# Слабое действие: без конкретики всё равно неоднозначно
_WEAK_ACTIONS = ("сделай", "проверь", "займись", "посмотри", "глянь",
                 "поработай", "помоги", "разберись",)
# Конкретика, которая делает задачу однозначной
_SPECIFICITY_MARKERS = _STRONG_ACTIONS + _WEAK_ACTIONS + (
    "нужно", "требуется", "должен", "обязан",
)


def ambiguity(request: str) -> dict:
    """Оценить неоднозначность запроса → спросить уточнение или действовать.

    Возвращает: {score 0..1, verdict: act/ask, reasons, questions}.
    Чем выше score — тем неоднозначнее.
    """
    low = request.lower()
    signals: list[str] = []

    # 1. Вопрос — это просьба об информации, не «задача» (обычно однозначно)
    is_question = bool(re.search(r"[?？]", request)) or low.startswith(("что", "как", "когда", "где", "почему", "зачем", "кто"))
    # 2. Нет глагола-действия и нет конкретики
    has_action = any(m in low for m in _SPECIFICITY_MARKERS)
    # 3. Маркеры неоднозначности
    amb_count = sum(1 for m in _AMBIGUITY_MARKERS if m in low)
    # 4. Маркеры «действуй сам» (снимают неоднозначность)
    act_count = sum(1 for m in _ACTION_MARKERS if m in low)
    # 5. Конкретика: числа, тикеры, имена файлов
    specifics = len(re.findall(r"\d+|\.\w{2,5}\b|[A-ZА-Я]{2,8}", request))

    score = 0.0
    if amb_count:
        score += min(0.25 * amb_count, 0.5)
        signals.append(f"неоднозначные слова: {amb_count}")
    if not has_action and not is_question:
        score += 0.3
        signals.append("нет явного действия/вопроса")
    if specifics >= 3:
        score -= 0.25
        signals.append(f"есть конкретика: {specifics}")
    # Слабое действие («сделай что-нибудь») — само по себе неоднозначно
    weak = any(m in low for m in _WEAK_ACTIONS)
    strong = any(m in low for m in _STRONG_ACTIONS)
    if weak and not strong:
        score += 0.25 if amb_count else 0.15
        signals.append("слабое действие без конкретики")
    if strong:
        score -= 0.15
        signals.append("сильное конкретное действие")
    # «сделай что-нибудь» без указания ЧТО — вопрос, а не действие
    if weak and not strong and not is_question and not specifics:
        score += 0.3
        signals.append("нет объекта действия (что именно?)")
    if re.search(r"что[- ]нибудь|что[- ]то|как[- ]нибудь|кого[- ]нибудь", low):
        score += 0.3
        signals.append("объект размытый («что-нибудь»)")
    # «действуй сам» снимает неоднозначность ТОЛЬКО при реальном действии
    if act_count and strong:
        score -= 0.4
        signals.append("явное «действуй сам» при конкретном действии")
    elif act_count and has_action and "сам реши" in low:
        # «сам реши и сделай» — делегирование, спрашивать не надо
        score -= 0.4
        signals.append("делегирование «сам реши» при действии")
    elif act_count:
        signals.append("«действуй сам», но действия нет — всё ещё неоднозначно")
    score = max(0.0, min(1.0, score))

    verdict = "ask" if score >= 0.5 else "act"

    questions: list[str] = []
    if verdict == "ask":
        if not has_action:
            questions.append("Что именно нужно сделать — что должно получиться в итоге?")
        if amb_count:
            questions.append("Уточни детали: что/где/как именно?")
        if specifics == 0:
            questions.append("Есть ли конкретика: цифры, тикеры, файлы, сроки?")

    return {
        "score": round(score, 2),
        "verdict": verdict,
        "reasons": signals,
        "questions": questions,
    }


def resolve_ambiguity(request: str, clarifying: Iterable[str] = ()) -> dict:
    """Спросить, если нужно; иначе — план действий. Итог для координатора."""
    amb = ambiguity(request)
    if amb["verdict"] == "act":
        plan = droid_plan(request)
        return {"mode": "act", "ambiguity": amb, "plan": plan}
    return {"mode": "ask", "ambiguity": amb, "plan": None}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Knowledge Droid (Factory: synthetic insights)
#    Индекс репозитория: структура, модули, связи — чтобы не перечитывать
#    код заново. Инсайты генерируются детерминированно из файлов.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RepoKnowledge:
    """Синтетические знания о репозитории."""
    root: str
    module_count: int
    file_count: int
    code_lines: int
    test_count: int
    top_imports: list[tuple[str, int]] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    suspicious: list[str] = field(default_factory=list)


_IGNORED_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                 ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
                 ".idea", ".vscode", ".agents"}


def _iter_source_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.py"):
        if any(part in _IGNORED_DIRS for part in p.parts):
            continue
        yield p


def build_repo_knowledge(root: str | Path, max_files: int = 400) -> RepoKnowledge:
    """Просканировать репозиторий и собрать инсайты (0 LLM, <1с)."""
    root = Path(root).resolve()
    files = list(_iter_source_files(root))[:max_files]
    tests = [f for f in files if f.name.startswith("test_") or "/tests/" in str(f)]
    modules: list[str] = []

    imports: Counter[str] = Counter()
    suspicious: list[str] = []
    code_lines = 0

    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        code_lines += len(text.splitlines())
        rel = f.relative_to(root)
        # имена модулей без расширения
        if "token_diet" in rel.parts and f.suffix == ".py":
            modules.append(f.stem)
        # топ импортов (внешние библиотеки)
        for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z_][\w]*)", text, re.M):
            mod = m.group(1)
            if mod not in ("token_diet", "token_diet.cli", "typing", "__future__"):
                imports[mod] += 1
        # подозрительные места: TODO/FIXME, захардкоженные ключи
        for m in re.finditer(r"#\s*(TODO|FIXME|XXX)", text):
            suspicious.append(f"{rel}:{text.count(chr(10), 0, m.start()) + 1} {m.group(1)}")
            if len(suspicious) >= 8:
                break
        if re.search(r"(api[_-]?key|secret|password|token)\s*=\s*[\"'][A-Za-z0-9_-]{16,}",
                     text, re.IGNORECASE):
            suspicious.append(f"{rel}: возможный захардкоженный секрет")

    insights: list[str] = []
    if modules:
        # самые «связанные» модули — кандидаты на рефакторинг
        counts = Counter(modules)
        insights.append(f"модулей в пакете: {len(modules)} ({', '.join(list(counts)[:6])}...)")
    if tests:
        insights.append(f"тесты: {len(tests)} файлов — покрытие есть, "
                        f"{'но код без тестов тоже есть' if len(tests) < max(3, len(files) // 6) else 'пропорция здоровая'}")
    if imports:
        top = imports.most_common(5)
        insights.append("внешние зависимости: " + ", ".join(f"{m}({c})" for m, c in top))
    insights.append(f"кода: {code_lines} строк в {len(files)} файлах")
    if suspicious:
        insights.append(f"⚠️ подозрительные места: {len(suspicious)} (см. suspicious)")

    return RepoKnowledge(
        root=str(root),
        module_count=len(modules),
        file_count=len(files),
        code_lines=code_lines,
        test_count=len(tests),
        top_imports=imports.most_common(8),
        modules=sorted(set(modules)),
        insights=insights,
        suspicious=suspicious[:10],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Coordinator — оркестратор: выбирает дроида, обрабатывает хендоффы,
#    решает «спросить или действовать», собирает инсайты знаний.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Handoff:
    """Структурный хендофф между дроидами (Factory: handoffs)."""
    from_role: str
    to_role: str
    artifact: str
    note: str = ""


class Coordinator:
    """Координатор дроидов: delegate(task) → план + хендофф-цепочка."""

    def __init__(self, repo_root: str | Path | None = None):
        self.repo_root = Path(repo_root) if repo_root else None
        self._knowledge: RepoKnowledge | None = None

    # ── знания ──────────────────────────────────────────────────────────────
    def knowledge(self) -> RepoKnowledge | None:
        """Инсайты репозитория (с кешем — мозжечок, не пересчитывать)."""
        if self._knowledge is None and self.repo_root and self.repo_root.exists():
            self._knowledge = build_repo_knowledge(self.repo_root)
        return self._knowledge

    def knowledge_block(self) -> str:
        """Человекочитаемый блок знаний репозитория."""
        k = self.knowledge()
        if not k:
            return "нет репозитория для анализа (укажи --repo)"
        lines = [f"📚 KNOWLEDGE: {k.root}",
                 f"   модулей: {k.module_count} · файлов: {k.file_count} "
                 f"· строк кода: {k.code_lines} · тестов: {k.test_count}"]
        for i in k.insights:
            lines.append(f"   • {i}")
        if k.top_imports:
            lines.append("   зависимости: " + ", ".join(
                f"{m}({c})" for m, c in k.top_imports[:5]))
        if k.suspicious:
            lines.append("   ⚠️ подозрительно:")
            for s in k.suspicious[:5]:
                lines.append(f"     - {s}")
        return "\n".join(lines)

    # ── делегирование ───────────────────────────────────────────────────────
    def delegate(self, task: str, role: str | None = None) -> dict:
        """Выбрать дроида, решить ask/act, построить цепочку хендоффов."""
        amb = resolve_ambiguity(task)
        if amb["mode"] == "ask":
            return {
                "mode": "ask",
                "task": task,
                "ambiguity": amb["ambiguity"],
                "questions": amb["ambiguity"]["questions"],
                "handoffs": [],
            }
        plan = amb["plan"]
        role = role or plan["role"]
        # Хендофф-цепочка по роли (Factory: code → review → test; docs → review)
        chain: list[str]
        if role == "code":
            chain = ["code", "review", "test"]
        elif role == "review":
            chain = ["review", "code"]
        elif role == "test":
            chain = ["test", "code"]
        elif role == "docs":
            chain = ["docs", "review"]
        else:
            chain = ["knowledge"]

        handoffs: list[Handoff] = []
        for i in range(len(chain) - 1):
            handoffs.append(Handoff(
                from_role=chain[i],
                to_role=chain[i + 1],
                artifact=f"{DROIDS[chain[i]].name} результат → {DROIDS[chain[i + 1]].name}",
                note="проверить и дополнить, не переписывать",
            ))

        return {
            "mode": "act",
            "task": task,
            "role": role,
            "droid": DROIDS[role].name,
            "system_prompt": DROIDS[role].system_prompt,
            "plan": plan["steps"],
            "handoffs": [{"from": h.from_role, "to": h.to_role,
                          "artifact": h.artifact, "note": h.note}
                         for h in handoffs],
            "ambiguity": amb["ambiguity"],
        }

    # ── вопрос к живой модели (опционально, экономит токены) ───────────────
    def ask_droid(self, task: str, role: str | None = None,
                  omni: bool = False, max_tokens: int = 800) -> str:
        """Выполнить задачу живой моделью через выбранного дроида.

        omni=True → локальный роутер OmniRoute; иначе AnyModel-армия.
        """
        dec = self.delegate(task, role)
        if dec["mode"] == "ask":
            return "Нужно уточнение:\n" + "\n".join(f"  - {q}" for q in dec["questions"])
        prompt = (f"[{dec['role']} droid]\n{dec['system_prompt']}\n\n"
                  f"Задача: {task}\n"
                  f"План: {' → '.join(dec['plan'])}\n"
                  f"Верни структурированный результат.")
        if omni:
            from .model_army import ask_omni
            return ask_omni(prompt, role="coding", max_tokens=max_tokens)
        from .model_army import ask
        return ask(prompt, role="brain", max_tokens=max_tokens)


def droid_status_block() -> str:
    """Сводка фабрики дроидов для панели."""
    lines = ["🤖 FACTORY DROIDS (реверс Factory AI):"]
    for role, spec in DROIDS.items():
        lines.append(f"   {role:<10} {spec.name:<14} фокус: {', '.join(spec.focus[:3])}")
    lines.append("   chain: code → review → test · docs → review · ask/act через ambiguity")
    return "\n".join(lines)


__all__ = [
    "DROIDS", "DroidSpec", "Handoff", "Coordinator", "RepoKnowledge",
    "ambiguity", "build_repo_knowledge", "classify_task", "droid_plan",
    "droid_status_block", "resolve_ambiguity",
]
