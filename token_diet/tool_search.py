"""Tool Search + TDQS-линтер — как выбирать правильный инструмент.

Техники из свежей науки 2026 (реверс-инжиниринг, 0 LLM-вызовов):

1. MCP Tool Search — инструменты НЕ скармливаются модели списком «все оптом»
   (это жрёт контекст), а индексируются в лёгком реестре. Модель получает
   один мета-инструмент search_available_tools(query): находит нужную
   функцию по BM25/эвристикам и динамически подключает только её схему.

2. TDQS (Tool Definition Quality Score) — до 97% описаний инструментов
   дефектны («запахи»): нет цели, нет гайдлайнов «когда применять»,
   пустые описания параметров. Линтер раскладывает описание на 6 осей и
   честно пишет, что с ним не так — до того, как модель запутается.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── стемминг для русской морфологии ─────────────────────────────────────
def _stem(w: str) -> str:
    w = w.lower()
    if len(w) > 5:
        w = w.rstrip("аяоеёуюыиэьийовымихое")  # noqa: B005  # charset strip is the point: Russian vowel endings
        if len(w) < 3:
            w = w.lower()
    return w


# ═════════════════════════════════════════════════════════════════════════
# 1. TDQS-линтер: проверка описания инструмента по 6 осям
# ═════════════════════════════════════════════════════════════════════════

_ACTION_VERBS = re.compile(
    r"^(?:build|create|get|send|post|search|parse|analyze|compute|convert|"
    r"extract|summarize|translate|list|read|write|update|delete|run|start|"
    r"stop|check|verify|compare|merge|sort|filter|calculate|estimate|"
    r"подготовь|создай|получи|найди|отправь|проанализируй|проверь|верни|"
    r"скачай|загрузи|построй|посчитай|преобразуй|извлеки|сохрани|запиши|"
    r"удали|обнови|запусти|останови|сравни|объедини|отсортируй|"
    r"возвращает|выполняет|обрабатывает|генерирует|извлекает|проверяет)",
    re.IGNORECASE,
)
_GUIDELINE_HINTS = re.compile(
    r"\b(?:когда|для|используй|используйте|применяй|применяется|вызывай|"
    r"если|use|when|for|call)\b", re.IGNORECASE
)
_EFFECT_HINTS = re.compile(
    r"\b(?:пишет|отправляет|создаёт|удаляет|платит|покупает|продаёт|"
    r"публикует|банк|деньги|оплата|сделка|writes|sends|creates|deletes|"
    r"pays|buys|sells|publishes)\b", re.IGNORECASE
)
_EXAMPLE_HINTS = re.compile(
    r"\b(?:пример|формат|напр\.?|например|example|format|enum|один из|"
    r"вариант[ы]?|true|false)\b", re.IGNORECASE
)

_AXES = [
    ("purpose", "Цель ясна (глагол + что делает, до 120 символов)"),
    ("usage", "Гайдлайны: когда применять / когда НЕ применять"),
    ("transparency", "Побочные эффекты названы (пишет/платит/удаляет)"),
    ("params", "Каждый параметр описан и типизирован"),
    ("conciseness", "Краткость (описание ≤ 500 символов)"),
    ("completeness", "Пример/формат значений параметров"),
]


@dataclass
class TdqsResult:
    score: int                      # 0..100
    issues: list[str] = field(default_factory=list)
    axes: dict[str, bool] = field(default_factory=dict)


class TdqsLinter:
    """Эвристическая проверка качества описаний инструментов (без LLM)."""

    def check(self, name: str, description: str,
              parameters: dict | None = None) -> TdqsResult:
        issues: list[str] = []
        axes: dict[str, bool] = {}
        desc = (description or "").strip()
        params = parameters or {}

        # 1. purpose — глагол + длина
        ok = len(desc) >= 10 and len(desc) <= 120 and _ACTION_VERBS.match(desc)
        axes["purpose"] = bool(ok)
        if len(desc) < 10:
            issues.append(f"описание «{name}» пустое или короче 10 символов")
        elif len(desc) > 120:
            issues.append(f"описание «{name}» длиннее 120 символов — модель "
                          f"теряет суть (сейчас {len(desc)})")
        if not _ACTION_VERBS.match(desc):
            issues.append(f"описание «{name}» не начинается с глагола действия")

        # 2. usage — гайдлайны когда применять
        ok = bool(_GUIDELINE_HINTS.search(desc))
        axes["usage"] = ok
        if not ok:
            issues.append(f"«{name}»: нет гайдлайна «когда применять»")

        # 3. transparency — побочные эффекты
        ok = bool(_EFFECT_HINTS.search(desc))
        axes["transparency"] = ok
        if name.lower().startswith(("send", "post", "delete", "buy", "sell",
                                    "отправ", "удал", "покуп", "прода",
                                    "плат", "post_", "publish")):
            if not ok:
                issues.append(f"«{name}»: у этого инструмента есть побочные "
                              f"эффекты — назовите их в описании")

        # 4. params — каждый параметр описан
        ok_params = True
        for pname, pspec in params.items():
            pd = ""
            if isinstance(pspec, dict):
                pd = str(pspec.get("description", "")).strip()
            elif isinstance(pspec, str):
                pd = pspec.strip()
            if not pd:
                ok_params = False
                issues.append(f"«{name}»: параметр {pname} без описания")
        axes["params"] = ok_params

        # 5. conciseness
        ok = len(desc) <= 500
        axes["conciseness"] = ok
        if len(desc) > 500:
            issues.append(f"«{name}»: описание раздуто ({len(desc)} символов)")

        # 6. completeness — пример/формат
        ok = bool(_EXAMPLE_HINTS.search(desc))
        for pspec in params.values():
            if isinstance(pspec, dict) and pspec.get("example"):
                ok = True
        axes["completeness"] = ok
        if not ok:
            issues.append(f"«{name}»: нет примера/формата значений параметров")

        passed = sum(1 for v in axes.values() if v)
        return TdqsResult(score=round(passed / len(axes) * 100),
                          issues=issues, axes=axes)


# ═════════════════════════════════════════════════════════════════════════
# 2. Tool Registry + Tool Search (MCP Tool Search)
# ═════════════════════════════════════════════════════════════════════════

@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict = field(default_factory=dict)
    category: str = "general"
    tdqs: TdqsResult | None = None


class ToolRegistry:
    """Реестр инструментов: регистрация с проверкой + поиск по запросу."""

    def __init__(self, validate: bool = True):
        self.tools: dict[str, ToolSpec] = {}
        self.validate = validate
        self.linter = TdqsLinter()

    def register(self, name: str, description: str,
                 parameters: dict | None = None,
                 category: str = "general") -> ToolSpec:
        spec = ToolSpec(name=name, description=description,
                        parameters=parameters or {}, category=category)
        if self.validate:
            spec.tdqs = self.linter.check(name, description, parameters)
        self.tools[name] = spec
        return spec

    def register_many(self, tools: list[dict]) -> int:
        n = 0
        for t in tools:
            self.register(t["name"], t.get("description", ""),
                          t.get("parameters"), t.get("category", "general"))
            n += 1
        return n

    # ── поиск (BM25-подобный + категория + репутация) ─────────────
    def search(self, query: str, limit: int = 3) -> list[ToolSpec]:
        q_terms = [t for t in (_stem(w) for w in
                               re.findall(r"[a-zа-яё0-9]{3,}", query.lower()))
                   if len(t) >= 3]
        scored: list[tuple[float, ToolSpec]] = []
        for spec in self.tools.values():
            hay = _stem(spec.name) + " " + _stem(spec.description) + \
                  " " + _stem(spec.category)
            s = 0.0
            for t in q_terms:
                if t in _stem(spec.name):
                    s += 3.0
                elif t in hay:
                    s += 1.5
            if spec.tdqs and spec.tdqs.score >= 80:
                s += 0.5          # проверенное качество — чуть выше
            if s > 0:
                scored.append((s, spec))
        scored.sort(key=lambda x: -x[0])
        return [sp for _, sp in scored[:limit]]

    def resolve(self, name: str) -> ToolSpec | None:
        """Автоподключение: вернуть полную схему инструмента по имени."""
        return self.tools.get(name)

    def search_block(self, query: str, limit: int = 3) -> str:
        """Промпт-блок для мета-инструмента search_available_tools."""
        hits = self.search(query, limit)
        if not hits:
            return f"[tool_search] по запросу «{query}» ничего не найдено"
        lines = [f"[tool_search] по запросу «{query}» найдено:"]
        for sp in hits:
            lines.append(f"  - {sp.name}: {sp.description[:80]}")
        return "\n".join(lines)

    def summary(self, max_chars: int = 600) -> str:
        """Компактный список: имена + назначение в 1 строку (Level 1)."""
        lines = []
        for spec in self.tools.values():
            line = f"- {spec.name}: {spec.description[:60]}"
            if spec.tdqs and spec.tdqs.score < 60:
                line += " ⚠"
            lines.append(line)
        return "\n".join(lines)[:max_chars]

    def stats(self) -> dict:
        return {
            "tools": len(self.tools),
            "avg_tdqs": round(sum((t.tdqs.score if t.tdqs else 0)
                                  for t in self.tools.values())
                              / max(len(self.tools), 1)),
            "flagged": sum(1 for t in self.tools.values()
                           if t.tdqs and t.tdqs.score < 60),
        }
