"""Mistake Learner — память антипаттернов.

УРОК: ассистент 5 раз подряд пытался скопировать файл через adb input text,
игнорируя что ~ не работает, Permission denied, и adb push быстрее.
Это АНТИПАТТЕРН — и он не должен повторяться.

Mistake Learner:
1. Записывает каждую ошибку/неудачу в паттерн
2. При следующей похожей ситуации — предупреждает
3. Предлагает альтернативный подход
4. Хранит паттерны локально (в памяти и на диске)

Форматы паттернов:
- "NEVER: <подход>" — категорически запрещено
- "PREFER: <подход> OVER <подход>" — всегда выбирай первый
- "WARN: <ситуация> → <риск>" — будь осторожен
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# Anti-pattern types
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AntiPattern:
    """A learned mistake that should not be repeated."""

    pattern_id: str  # unique ID
    category: str  # NEVER | PREFER | WARN
    description: str  # human-readable
    condition: str  # keyword-based trigger
    alternative: str  # what to do instead
    severity: float  # 0..1 — how bad is repeating this?
    occurrences: int = 1  # how many times seen
    last_seen: float = field(default_factory=time.time)

    def matches(self, action: str) -> bool:
        """Does this action trigger this anti-pattern?"""
        # Simple keyword match
        action_lower = action.lower()
        keywords = self.condition.lower().split()
        return all(kw in action_lower for kw in keywords if kw)

    def to_dict(self) -> dict:
        return {
            "id": self.pattern_id,
            "category": self.category,
            "description": self.description,
            "condition": self.condition,
            "alternative": self.alternative,
            "severity": self.severity,
            "occurrences": self.occurrences,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AntiPattern:
        return cls(
            pattern_id=d["id"],
            category=d["category"],
            description=d["description"],
            condition=d["condition"],
            alternative=d["alternative"],
            severity=d.get("severity", 0.5),
            occurrences=d.get("occurrences", 1),
            last_seen=d.get("last_seen", time.time()),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Built-in anti-patterns — hard lessons learned
# ═══════════════════════════════════════════════════════════════════════════════

BUILTIN_PATTERNS: list[AntiPattern] = [
    # ── ADB/Termux lessons ──
    AntiPattern(
        pattern_id="NEVER_ADB_INPUT_TEXT_FOR_FILES",
        category="NEVER",
        description="adb shell input text НЕ копирует файлы. Для копирования используй adb push.",
        condition="adb shell input text cp",
        alternative="adb push <local> <remote> — один вызов, мгновенно",
        severity=0.9,
    ),
    AntiPattern(
        pattern_id="NEVER_TILDE_IN_ADB_INPUT",
        category="NEVER",
        description="~ (тильда) в adb input text вводится как / на телефоне.",
        condition="adb shell input text ~",
        alternative="Используй полный путь /data/data/com.termux/files/home/ вместо ~",
        severity=0.85,
    ),
    AntiPattern(
        pattern_id="PREFER_ADB_PUSH_OVER_INPUT_TEXT",
        category="PREFER",
        description="adb push быстрее adb shell input text в 100 раз.",
        condition="adb shell input text",
        alternative="adb push — копирует файл мгновенно",
        severity=0.7,
    ),
    AntiPattern(
        pattern_id="WARN_SCREEN_LOOP",
        category="WARN",
        description="Петля: скриншот → OCR → ввод → скриншот. Надёжнее использовать прямые команды.",
        condition="screencap tesseract input text",
        alternative="adb shell напрямую, без скриншотов, если возможно",
        severity=0.75,
    ),
    AntiPattern(
        pattern_id="NEVER_RUN_AS_FOR_WRITE",
        category="NEVER",
        description="run-as не даёт писать файлы. Используй /sdcard/ для записи.",
        condition="run-as write file",
        alternative="Пиши в /sdcard/Download/ или используй adb push",
        severity=0.8,
    ),

    # ── Coding lessons ──
    AntiPattern(
        pattern_id="PREFER_STR_REPLACE_OVER_WRITE_FILE",
        category="PREFER",
        description="str_replace сохраняет остальной код, write_file перезаписывает весь файл.",
        condition="write_file",
        alternative="str_replace для точечных правок, write_file только для новых файлов",
        severity=0.6,
    ),
    AntiPattern(
        pattern_id="WARN_SYNTAX_ERRORS_BEFORE_COMMIT",
        category="WARN",
        description="Всегда проверяй синтаксис Python после правок перед коммитом.",
        condition="str_replace py",
        alternative="python3 -m py_compile <file> после каждого изменения",
        severity=0.8,
    ),

    # ── User-relationship lessons (from the owner) ──
    AntiPattern(
        pattern_id="NEVER_FORCE_INVESTMENT_LENS",
        category="NEVER",
        description=("УРОК ОТ ВЛАДЕЛЬЦА: когда он спрашивает о простой вещи "
                     "(бесплатная подписка Яндекс Плюс), НЕ притягивай это "
                     "к инвестициям/акциям. Цель — не «прибыль любыми путями», "
                     "а помочь человеку прямо и по-человечески. Простой вопрос "
                     "= простой ответ."),
        condition="подписка плюс акции инвестиции",
        alternative=("Ответь на заданный вопрос прямо. Связывай с инвестициями "
                     "только если человек сам спросил про них."),
        severity=0.95,
    ),
    AntiPattern(
        pattern_id="NEVER_PROFIT_BY_ANY_MEANS",
        category="NEVER",
        description=("УРОК ОТ ВЛАДЕЛЬЦА: «максимальная прибыль» не значит «любыми "
                     "путями». Честность, безопасность и интересы человека "
                     "ВСЕГДА важнее выгоды. Цель — помочь людям, а не навязать "
                     "им инвестиционную повестку."),
        condition="максимальная прибыль любыми путями",
        alternative=("Оптимизируй прибыль в рамках честности и безопасности, "
                     "никогда не ценой простоты и доверия."),
        severity=1.0,
    ),

    # ── General AI assistant lessons ──
    AntiPattern(
        pattern_id="NEVER_OVEREXPLAIN",
        category="NEVER",
        description="Не объясняй каждую мелочь. Пользователь ждёт результат, а не лекцию.",
        condition="Let me explain let me elaborate",
        alternative="Покажи результат. Если нужно — краткий комментарий в 1 предложение.",
        severity=0.5,
    ),
    AntiPattern(
        pattern_id="PREFER_ACTION_OVER_DELIBERATION",
        category="PREFER",
        description="Действие всегда лучше размышления. Делай, а не думай вслух.",
        condition="Let me think I need to consider",
        alternative="Сразу к делу. Результат важнее процесса.",
        severity=0.4,
    ),
    AntiPattern(
        pattern_id="NEVER_ASK_PERMISSION_FOR_OBVIOUS",
        category="NEVER",
        description="Не спрашивай разрешения на очевидные действия.",
        condition="Should I May I Would you like me to",
        alternative="Делай. Если ошибка — пользователь скажет.",
        severity=0.55,
    ),
    AntiPattern(
        pattern_id="WARN_SPAWN_OVERLOAD",
        category="WARN",
        description="Спавнить 10+ параллельных агентов = избыточно для простых задач.",
        condition="spawn_agents agents 10",
        alternative="Группируй связанные задачи. 3-5 параллельных агентов — оптимально.",
        severity=0.65,
    ),
    AntiPattern(
        pattern_id="WARN_TOKEN_WASTE_ON_FORMATTING",
        category="WARN",
        description="Трата токенов на красивое форматирование (таблицы, эмодзи, рамки).",
        condition="═══ ─── │ ├ ┤",
        alternative="Простой текст. Контент важнее оформления.",
        severity=0.3,
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MistakeAlert:
    """Warning about a potential mistake before it happens."""

    pattern: AntiPattern
    triggered_by: str  # what action triggered it
    suggestion: str  # what to do instead

    @property
    def label(self) -> str:
        return f"[{self.pattern.category}] {self.pattern.description[:80]}"


class MistakeLearner:
    """Remember and avoid repeating mistakes.

    Usage:
        learner = MistakeLearner()
        learner.record("adb shell input text cp ...", success=False)
        # Next time:
        alert = learner.check("adb shell input text mv ...")
        if alert:
            print(f"DON'T: {alert.pattern.description}")
            print(f"DO: {alert.pattern.alternative}")
    """

    def __init__(self, storage_path: str | None = None):
        self.patterns: dict[str, AntiPattern] = {}
        self.storage_path = storage_path or os.path.join(
            os.path.expanduser("~"), ".config", "token_diet", "mistakes.json"
        )

        # Load built-in patterns
        for p in BUILTIN_PATTERNS:
            self.patterns[p.pattern_id] = p

        # Load from disk
        self._load()

    def _load(self) -> None:
        """Load learned patterns from disk."""
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path) as f:
                    data = json.load(f)
                for d in data:
                    p = AntiPattern.from_dict(d)
                    if p.pattern_id not in self.patterns:
                        self.patterns[p.pattern_id] = p
                    else:
                        # Update occurrences
                        self.patterns[p.pattern_id].occurrences = max(
                            self.patterns[p.pattern_id].occurrences,
                            p.occurrences,
                        )
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self) -> None:
        """Save learned patterns to disk."""
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            with open(self.storage_path, "w") as f:
                json.dump(
                    [p.to_dict() for p in self.patterns.values()],
                    f, indent=2, ensure_ascii=False,
                )
        except OSError:
            pass

    def record(
        self,
        action: str,
        *,
        success: bool = True,
        mistake_description: str = "",
    ) -> None:
        """Record an action outcome.

        If failure, this creates or reinforces an anti-pattern.
        """
        if success:
            return  # Don't learn from success (yet)

        # Check if this matches an existing pattern
        for pid, pattern in self.patterns.items():
            if pattern.matches(action):
                pattern.occurrences += 1
                pattern.last_seen = time.time()
                pattern.severity = min(1.0, pattern.severity + 0.05)
                self._save()
                return

        # New pattern — but only if we have a description
        if not mistake_description:
            return

        # Auto-generate pattern ID
        pid = f"LEARNED_{int(time.time())}"
        self.patterns[pid] = AntiPattern(
            pattern_id=pid,
            category="WARN",
            description=mistake_description[:200],
            condition=action[:100],
            alternative="(learned — no alternative yet)",
            severity=0.3,
        )
        self._save()

    def check(self, action: str) -> MistakeAlert | None:
        """Check if an action matches any known anti-pattern.

        Returns MistakeAlert if danger detected, None if safe.
        """
        for pattern in self.patterns.values():
            if pattern.matches(action):
                return MistakeAlert(
                    pattern=pattern,
                    triggered_by=action[:100],
                    suggestion=pattern.alternative,
                )
        return None

    def check_all(self, actions: list[str]) -> list[MistakeAlert]:
        """Check multiple actions at once."""
        alerts = []
        for action in actions:
            alert = self.check(action)
            if alert:
                alerts.append(alert)
        return alerts

    def get_injection(self, action: str) -> str | None:
        """Get a prompt injection to prevent a known mistake.

        Injects the alternative approach into the system prompt.
        """
        alert = self.check(action)
        if alert and alert.pattern.severity > 0.5:
            return (
                f"CRITICAL REMINDER: {alert.pattern.description} "
                f"Instead: {alert.pattern.alternative}"
            )
        return None

    def summary(self) -> str:
        """Human-readable summary of all learned patterns."""
        lines = [f"Mistake Learner — {len(self.patterns)} patterns:\n"]
        for pid, p in sorted(
            self.patterns.items(),
            key=lambda x: (-x[1].severity, -x[1].occurrences),
        ):
            lines.append(
                f"  [{p.category}] {p.description[:80]} "
                f"(×{p.occurrences}, severity={p.severity:.1f})"
            )
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# One-liner
# ═══════════════════════════════════════════════════════════════════════════════

_global_learner: MistakeLearner | None = None


def check_action(action: str) -> MistakeAlert | None:
    """One-liner: check if this action repeats a known mistake."""
    global _global_learner
    if _global_learner is None:
        _global_learner = MistakeLearner()
    return _global_learner.check(action)


def learn_from(action: str, success: bool, description: str = "") -> None:
    """One-liner: record an action outcome."""
    global _global_learner
    if _global_learner is None:
        _global_learner = MistakeLearner()
    _global_learner.record(action, success=success, mistake_description=description)
