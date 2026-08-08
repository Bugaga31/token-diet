"""Token Diet — расширенный модуль: event memory, adaptive context, language router.

Идея из /home/ro/token_diet_full.py (автор: пользователь).
Версия для DarkBit — интегрирована с существующим token_diet/core.py.

Новые компоненты:
  - EventStore: персистентная event-память (fact/decision/preference/constraint)
  - AdaptiveContext: ranking событий по релевантности к вопросу + budget guard
  - ContextManager: before/after hooks для инъекции памяти в промпт
  - LanguageRouter: перевод текста для экономии токенов (English → Russian и обратно)
  - extract_events: авто-извлечение constraints/decisions/preferences из диалога
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import ClassVar

from .core import count_tokens

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Event Memory — структурированные события с важностью и TTL
# ═══════════════════════════════════════════════════════════════


@dataclass
class MemoryEvent:
    """Событие памяти: факт, решение, предпочтение, ограничение."""

    kind: str  # fact, decision, preference, constraint, open_question, temporary
    text: str
    importance: float = 0.5  # 0.0-1.0
    created_at: float = 0.0
    expires_at: float | None = None  # None = бессрочно
    source_turn: int = 0
    event_id: str = ""

    def live(self) -> bool:
        return self.expires_at is None or time.time() < self.expires_at

    def rendered(self) -> str:
        return f"[{self.kind}] {self.text}"


class EventStore:
    """Персистентное хранилище событий (JSON файл, без БД)."""

    _KINDS: ClassVar[frozenset[str]] = frozenset(
        {"fact", "decision", "preference", "constraint", "open_question", "temporary"}
    )

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.events: dict[str, MemoryEvent] = {}
        self.turn = 0
        if self.path and self.path.exists():
            self.load()

    def add(
        self,
        kind: str,
        text: str,
        importance: float = 0.5,
        ttl_seconds: float | None = None,
    ) -> str:
        if kind not in self._KINDS:
            raise ValueError(f"unknown kind: {kind}")
        text = text.strip()
        if not text:
            return ""
        self.turn += 1
        key = hashlib.sha256(f"{kind}:{text.lower()}".encode()).hexdigest()[:20]
        now = time.time()
        self.events[key] = MemoryEvent(
            kind=kind,
            text=text,
            importance=max(0.0, min(1.0, importance)),
            created_at=now,
            expires_at=now + ttl_seconds if ttl_seconds else None,
            source_turn=self.turn,
            event_id=key,
        )
        self.save()
        return key

    def all(self) -> list[MemoryEvent]:
        self.cleanup()
        return list(self.events.values())

    def cleanup(self) -> None:
        before = len(self.events)
        self.events = {k: v for k, v in self.events.items() if v.live()}
        if len(self.events) != before:
            self.save()

    def remove(self, key: str) -> None:
        self.events.pop(key, None)
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {"turn": self.turn, "events": [asdict(x) for x in self.events.values()]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            tmp.replace(self.path)
        except Exception:
            logger.exception("EventStore save failed")

    def load(self) -> None:
        try:
            p = json.loads(self.path.read_text())  # type: ignore[union-attr]
            self.turn = p.get("turn", 0)
            self.events = {
                x["event_id"]: MemoryEvent(**x) for x in p.get("events", [])
            }
            self.cleanup()
        except Exception:
            logger.exception("EventStore load failed")


# ═══════════════════════════════════════════════════════════════
# Adaptive Context — ranking событий по релевантности к вопросу
# ═══════════════════════════════════════════════════════════════


class AdaptiveContext:
    """Выбирает релевантные события под budget токенов."""

    _BONUS: ClassVar[dict[str, float]] = {
        "constraint": 0.30,
        "decision": 0.20,
        "preference": 0.15,
        "open_question": 0.10,
        "temporary": 0.05,
        "fact": 0.0,
    }

    def __init__(self, store: EventStore, counter: Callable[[str], int] = count_tokens):
        self.store = store
        self.counter = counter

    def select(self, question: str, budget: int = 1200) -> list[MemoryEvent]:
        terms = set(re.findall(r"\w{3,}", question.lower()))
        events = self.store.all()

        def score(e: MemoryEvent) -> float:
            text_terms = set(re.findall(r"\w{3,}", e.text.lower()))
            term_overlap = len(terms & text_terms) / max(1, len(terms))
            recency_penalty = min(0.3, (self.store.turn - e.source_turn) * 0.003)
            return e.importance * 0.45 + term_overlap * 0.4 + self._BONUS.get(e.kind, 0) - recency_penalty

        ordered = sorted(events, key=score, reverse=True)
        # Constraints всегда сверху (hard constraints)
        pinned = [e for e in events if e.kind == "constraint"]
        ordered = pinned + [e for e in ordered if e not in pinned]

        out: list[MemoryEvent] = []
        used = 0
        for e in ordered:
            n = self.counter(e.rendered())
            if used + n <= budget:
                out.append(e)
                used += n
        return out

    def render(self, question: str, budget: int = 1200) -> str:
        return "\n".join(e.rendered() for e in self.select(question, budget))


# ═══════════════════════════════════════════════════════════════
# Context Manager — before/after hooks для инъекции памяти в промпт
# ═══════════════════════════════════════════════════════════════


class ContextManager:
    """Управляет контекстом диалога: Before(question) → память, After → извлечение."""

    def __init__(self, path: str | Path | None = "memory/events.json", budget: int = 1200):
        self.store = EventStore(path)
        self.memory = AdaptiveContext(self.store)
        self.budget = budget

    def before(self, question: str) -> str:
        """Получить релевантную память для вопроса."""
        return self.memory.render(question, self.budget)

    def after(self, user_text: str, assistant_text: str) -> None:
        """Извлечь события из диалога после ответа."""
        extract_events(user_text, assistant_text, self.store)

    def remember(
        self,
        kind: str,
        text: str,
        importance: float = 0.5,
        ttl_seconds: float | None = None,
    ) -> str:
        """Явно сохранить событие."""
        return self.store.add(kind, text, importance, ttl_seconds)


# ═══════════════════════════════════════════════════════════════
# Event Extraction — авто-извлечение constraints/decisions из диалога
# ═══════════════════════════════════════════════════════════════

# Паттерны (русско-английские)
_USER_PATTERNS = [
    (r"\b(?:я предпочитаю|мне нравится|I prefer|I like)\s+([^.!?\n]+)", "preference", 0.8, None),
    (r"\b(?:не использовать|avoid|don'?t use)\s+([^.!?\n]+)", "preference", 0.7, None),
    (r"\b(?:мне нужно|требуется|I need|must|обязательно)\s+([^.!?\n]+)", "constraint", 0.9, None),
    (r"\b(?:без |without |не больше |limit )\s*([^.!?\n]+)", "constraint", 0.8, None),
    (r"\b(?:мы решили|давай|let'?s|will use|используем)\s+([^.!?\n]+)", "decision", 0.9, None),
]

_AI_PATTERNS = [
    (r"\b(?:next step|следующий шаг|decision:|решение:)\s*([^.!?\n]+)", "decision", 0.8),
    (r"\b(?:constraint:|ограничение:)\s*([^.!?\n]+)", "constraint", 0.85),
]


def extract_events(user_text: str, assistant_text: str, store: EventStore) -> None:
    """Извлечь события из текста пользователя и ответа AI."""
    for pat, kind, imp, ttl in _USER_PATTERNS:
        for m in re.finditer(pat, user_text, re.IGNORECASE):
            with contextlib.suppress(Exception):
                store.add(kind, m.group(1).strip(), imp, ttl)

    for pat, kind, imp in _AI_PATTERNS:
        for m in re.finditer(pat, assistant_text, re.IGNORECASE):
            with contextlib.suppress(Exception):
                store.add(kind, m.group(1).strip(), imp, None)


# ═══════════════════════════════════════════════════════════════
# Language Router — перевод для экономии токенов
# ═══════════════════════════════════════════════════════════════


@dataclass
class TranslationChoice:
    text: str
    source: str
    target: str
    original_tokens: int
    translated_tokens: int
    translation_cost: int
    saved_tokens: int
    used: bool
    reason: str


class TranslationCache:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def _key(self, text: str, source: str, target: str) -> str:
        return hashlib.sha256(f"{source}:{target}:{text}".encode()).hexdigest()

    def get(self, text: str, source: str, target: str) -> str | None:
        return self.items.get(self._key(text, source, target))

    def put(self, text: str, source: str, target: str, value: str) -> None:
        self.items[self._key(text, source, target)] = value


def translation_safe(text: str) -> bool:
    """Безопасно ли переводить текст (не код, не секреты, не короткий)."""
    if len(text) < 80 or text.count("```") >= 2:
        return False
    return not re.search(
        r"https?://|\b[a-fA-F0-9]{16,}\b|\b(api[_-]?key|password|secret|пароль|ключ)\b",
        text,
        re.IGNORECASE,
    )


def choose_language(
    text: str,
    source: str,
    candidates: list[str],
    translate: Callable[[str, str, str], str],
    counter: Callable[[str], int] = count_tokens,
    cache: TranslationCache | None = None,
    min_saving: int = 40,
) -> TranslationChoice:
    """Выбрать язык для экономии токенов (перевод окупает себя)."""
    cache = cache or TranslationCache()
    original = counter(text)
    if not translation_safe(text):
        return TranslationChoice(text, source, source, original, original, 0, 0, False, "unsafe_or_short")

    best = (text, source, original, 0)
    for target in candidates:
        if target == source:
            continue
        translated = cache.get(text, source, target)
        fee = 0 if translated is not None else max(16, original // 5)
        if translated is None:
            translated = translate(text, source, target)
            cache.put(text, source, target, translated)
        tokens = counter(translated)
        net = original - tokens - fee
        if net > original - best[2] - best[3]:
            best = (translated, target, tokens, fee)

    translated, target, tokens, fee = best
    net = original - tokens - fee
    if target == source or net < min_saving:
        return TranslationChoice(text, source, source, original, original, 0, 0, False, "not_profitable")
    return TranslationChoice(translated, source, target, original, tokens, fee, net, True, "net_saving")
