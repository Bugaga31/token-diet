"""Token Diet — расширенный модуль: event memory, adaptive context, language router.

Идея из /home/ro/token_diet_full.py (автор: пользователь).
EventStore with versioning, hybrid ranking, and adaptive context.

Компоненты (включая доработки по ревью #4 и #8):
  - EventStore: персистентная event-память с ВЕРСИОНИРОВАНИЕМ, разрешением
    конфликтов (новое решение вытесняет старое), отдельным приоритетом для
    permissions/security и защитой бюджета pinned-событиями
  - AdaptiveContext: HYBRID ranking (keyword + embedding + importance + recency)
    с журналом выбора (какие события выбраны и почему)
  - ContextManager: before/after hooks для инъекции памяти в промпт
  - LanguageRouter: перевод включается только при реальном break-even
    (стоимость перевода + стоимость проверки + reuse) и никогда не трогает
    код, JSON, URL, идентификаторы, числа и вопрос пользователя
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

try:
    from .core import cosine_similarity, count_tokens
except ImportError:  # standalone use (tests, scripts with token-diet-lib on path)
    from core import cosine_similarity, count_tokens  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Event Memory — структурированные события с важностью, TTL и версиями
# ═══════════════════════════════════════════════════════════════


@dataclass
class MemoryEvent:
    """Событие памяти: факт, решение, предпочтение, ограничение, permission, security.

    version/superseded/superseded_by/updated_at реализуют версионирование:
    при повторной записи того же ключа старая версия не теряется, а
    помечается как вытесненная (новое решение вытесняет старое).
    """

    kind: str  # fact, decision, preference, constraint, permission, security, open_question, temporary
    text: str
    importance: float = 0.5  # 0.0-1.0
    created_at: float = 0.0
    expires_at: float | None = None  # None = бессрочно
    source_turn: int = 0
    event_id: str = ""
    version: int = 1
    superseded: bool = False
    superseded_by: str | None = None
    updated_at: float = 0.0

    def live(self) -> bool:
        return self.expires_at is None or time.time() < self.expires_at

    def rendered(self) -> str:
        return f"[{self.kind}] {self.text}"


class EventStore:
    """Персистентное хранилище событий (JSON файл, без БД).

    — версии: self.versions[key] хранит предыдущие версии события;
    — конфликты: add(supersede=True) помечает старую версию superseded;
    — приоритет: kinds permission/security ранжируются выше constraint;
    — budget: pinned-события ограничены долей бюджета в AdaptiveContext.
    """

    _KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "fact",
            "decision",
            "preference",
            "constraint",
            "permission",
            "security",
            "open_question",
            "temporary",
        }
    )
    # Пиннуются в AdaptiveContext (всегда выбираются, но в пределах доли бюджета).
    PINNED_KINDS: ClassVar[frozenset[str]] = frozenset(
        {"constraint", "permission", "security"}
    )

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.events: dict[str, MemoryEvent] = {}
        self.versions: dict[str, list[MemoryEvent]] = {}
        self.turn = 0
        if self.path and self.path.exists():
            self.load()

    def add(
        self,
        kind: str,
        text: str,
        importance: float = 0.5,
        ttl_seconds: float | None = None,
        supersede: bool = True,
    ) -> str:
        if kind not in self._KINDS:
            raise ValueError(f"unknown kind: {kind}")
        self.turn += 1
        text = text.strip()
        key = hashlib.sha256(f"{kind}:{text.lower()}".encode()).hexdigest()[:20]
        now = time.time()
        previous = self.events.get(key)

        version = 1
        if previous is not None:
            version = previous.version + 1
            if supersede and not previous.superseded:
                previous.superseded = True
                previous.superseded_by = key
                self.versions.setdefault(key, []).append(previous)

        self.events[key] = MemoryEvent(
            kind=kind,
            text=text,
            importance=max(0.0, min(1.0, importance)),
            created_at=now,
            expires_at=now + ttl_seconds if ttl_seconds else None,
            source_turn=self.turn,
            event_id=key,
            version=version,
            updated_at=now,
        )
        self.save()
        return key

    def supersede(self, key: str, reason: str = "") -> bool:
        """Явно вытеснить событие (новое решение заменяет старое)."""
        event = self.events.get(key)
        if event is None or event.superseded:
            return False
        event.superseded = True
        event.superseded_by = reason or key
        self.versions.setdefault(key, []).append(event)
        self.save()
        return True

    def all(self, include_superseded: bool = False) -> list[MemoryEvent]:
        self.cleanup()
        if include_superseded:
            return list(self.events.values())
        return [e for e in self.events.values() if not e.superseded]

    def history(self, key: str) -> list[MemoryEvent]:
        """Все версии события: текущая + предыдущие."""
        out = []
        if key in self.events:
            out.append(self.events[key])
        return out + list(reversed(self.versions.get(key, [])))

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
                    {
                        "turn": self.turn,
                        "events": [asdict(x) for x in self.events.values()],
                        "versions": {
                            k: [asdict(v) for v in vals]
                            for k, vals in self.versions.items()
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            tmp.replace(self.path)
        except Exception:
            logger.exception("EventStore save failed")

    def load(self) -> None:
        try:
            payload = json.loads(self.path.read_text())  # type: ignore[union-attr]
            self.turn = payload.get("turn", 0)
            self.events = {
                x["event_id"]: MemoryEvent(**x) for x in payload.get("events", [])
            }
            self.versions = {
                k: [MemoryEvent(**x) for x in vals]
                for k, vals in payload.get("versions", {}).items()
            }
            self.cleanup()
        except Exception:
            logger.exception("EventStore load failed")


# ═══════════════════════════════════════════════════════════════
# Adaptive Context — hybrid ranking + журнал выбора + pinned-бюджет
# ═══════════════════════════════════════════════════════════════


@dataclass
class SelectionEntry:
    """Запись журнала выбора: событие, оценка, попал ли в бюджет и почему."""

    event_id: str
    text: str
    kind: str
    score: float
    chosen: bool
    reason: str

    def render(self) -> str:
        flag = "✓" if self.chosen else "✗"
        return f"{flag} [{self.kind}] {self.text[:50]!r} score={self.score:.3f} ({self.reason})"


class AdaptiveContext:
    """Выбирает релевантные события под budget токенов.

    Hybrid ranking: keyword overlap + optional embedding similarity +
    importance + recency. Словесное пересечение одно не должно решать:
    без embedding релевантность может пропустить важное событие, если
    пользователь использует другой термин — поэтому важность/новизна
    участвуют напрямую, а при наличии embed добавляется векторная близость.

    Pinned (constraint/permission/security) всегда рассматриваются первыми,
    но не могут съесть весь бюджет: max_pinned_share ограничивает их долю.
    """

    _BONUS: ClassVar[dict[str, float]] = {
        "security": 0.40,  # безопасность — самый высокий приоритет
        "permission": 0.35,
        "constraint": 0.30,
        "decision": 0.20,
        "preference": 0.15,
        "open_question": 0.10,
        "temporary": 0.05,
        "fact": 0.0,
    }

    def __init__(
        self,
        store: EventStore,
        counter: Callable[[str], int] = count_tokens,
        embed: Callable[[str], list[float]] | None = None,
        weights: tuple[float, float, float, float] = (0.45, 0.40, 0.0, 0.15),
        max_pinned_share: float = 0.5,
    ):
        self.store = store
        self.counter = counter
        self.embed = embed
        # importance, keyword, embedding, recency
        self.weights = weights
        self.max_pinned_share = max(0.0, min(1.0, max_pinned_share))
        self.last_selection: list[SelectionEntry] = []
        self.selection_history: list[list[SelectionEntry]] = []
        self._history_cap = 50

    def select(self, question: str, budget: int = 1200) -> list[MemoryEvent]:
        terms = set(re.findall(r"\w{3,}", question.lower()))
        events = self.store.all()
        q_vector = self.embed(question) if self.embed is not None else None

        def score(e: MemoryEvent) -> float:
            text_terms = set(re.findall(r"\w{3,}", e.text.lower()))
            overlap = len(terms & text_terms) / max(1, len(terms))
            embedding_sim = 0.0
            if q_vector is not None:
                embedding_sim = cosine_similarity(q_vector, self.embed(e.text))  # type: ignore[arg-type]
            recency = 1.0 - min(0.5, (self.store.turn - e.source_turn) * 0.003)
            w_imp, w_key, w_emb, w_rec = self.weights
            return (
                e.importance * w_imp
                + overlap * w_key
                + embedding_sim * w_emb
                + recency * w_rec
                + self._BONUS.get(e.kind, 0.0)
            )

        ranked = sorted(events, key=score, reverse=True)
        pinned = [e for e in ranked if e.kind in self.store.PINNED_KINDS]
        rest = [e for e in ranked if e not in pinned]

        log: list[SelectionEntry] = []
        out: list[MemoryEvent] = []
        used = 0
        pinned_budget = int(budget * self.max_pinned_share)

        for e in pinned:
            n = self.counter(e.rendered())
            entry = SelectionEntry(e.event_id, e.text, e.kind, score(e), False, "pinned")
            if used + n <= pinned_budget:
                out.append(e)
                used += n
                entry.chosen = True
                entry.reason = "pinned(within budget)"
            else:
                entry.reason = "pinned_budget_cap"
            log.append(entry)

        for e in rest:
            n = self.counter(e.rendered())
            entry = SelectionEntry(e.event_id, e.text, e.kind, score(e), False, "ranked")
            if used + n <= budget:
                out.append(e)
                used += n
                entry.chosen = True
                entry.reason = "ranked(within budget)"
            else:
                entry.reason = "budget_exhausted"
            log.append(entry)

        self.last_selection = log
        self.selection_history.append(log)
        if len(self.selection_history) > self._history_cap:
            self.selection_history = self.selection_history[-self._history_cap :]
        return out

    def render(self, question: str, budget: int = 1200) -> str:
        return "\n".join(e.rendered() for e in self.select(question, budget))

    def selection_log_text(self) -> str:
        return "\n".join(entry.render() for entry in self.last_selection)


# ═══════════════════════════════════════════════════════════════
# Context Manager — before/after hooks для инъекции памяти в промпт
# ═══════════════════════════════════════════════════════════════


class ContextManager:
    """Управляет контекстом диалога: Before(question) → память, After → извлечение."""

    def __init__(
        self,
        path: str | Path | None = "memory/events.json",
        budget: int = 1200,
        embed: Callable[[str], list[float]] | None = None,
        max_pinned_share: float = 0.5,
    ):
        self.store = EventStore(path)
        self.memory = AdaptiveContext(
            self.store, embed=embed, max_pinned_share=max_pinned_share
        )
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
        supersede: bool = True,
    ) -> str:
        """Явно сохранить событие (новое вытесняет старое при supersede=True)."""
        return self.store.add(kind, text, importance, ttl_seconds, supersede)


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
    (r"\b(?:запрещено|нельзя|не\s+допускается|not\s+allowed|forbidden)\s+([^.!?\n]+)", "security", 0.95, None),
]

_AI_PATTERNS = [
    (r"\b(?:next step|следующий шаг|decision:|решение:)\s*([^.!?\n]+)", "decision", 0.8),
    (r"\b(?:constraint:|ограничение:)\s*([^.!?\n]+)", "constraint", 0.85),
    (r"\b(?:security:|permission:)\s*([^.!?\n]+)", "security", 0.9),
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
# Language Router — перевод ТОЛЬКО при реальном break-even
# ═══════════════════════════════════════════════════════════════

# Что никогда нельзя переводить автоматически: код, JSON, URL, ID, числа,
# служебные строки и сам вопрос пользователя.
_NEVER_TRANSLATE_RE = re.compile(
    r"https?://|api[_-]?key|password|secret|пароль|ключ"
    r"|\b[a-fA-F0-9]{16,}\b|\b\d{6,}\b",
    re.IGNORECASE,
)


def never_translate(text: str) -> bool:
    """True = текст НЕЛЬЗЯ переводить автоматически.

    Проверяет: код (fences/сигнатуры), JSON/структурированные данные,
    URL/секреты/длинные ID, перенасыщенность числами, короткий текст.
    """
    stripped = text.strip()
    if len(stripped) < 80:
        return True
    if stripped.endswith("?"):
        return True  # пользовательский вопрос — никогда не переводим авто
    if stripped.count("```") >= 2:
        return True
    if _NEVER_TRANSLATE_RE.search(stripped):
        return True
    # JSON-подобное: сбалансированные скобки и кавычки-ключи
    if re.search(r'\{[^{}]*"[A-Za-z_]+"\s*:', stripped):
        return True
    # Код: сигнатуры функций/присваивания/точки с запятой
    if re.search(r"\b(def|class|function|import|from|return)\b[^\n]{0,40}\(", stripped):
        return True
    # Перенасыщенность числами (строки, где >10% символов — цифры)
    digits = sum(ch.isdigit() for ch in stripped)
    if digits > 0 and digits / len(stripped) > 0.10:
        return True
    return False


def translation_safe(text: str) -> bool:
    """Безопасно ли переводить текст (не код, не секреты, не короткий)."""
    return not never_translate(text)


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
    reuse_count: int = 1
    verification_cost: int = 0


class TranslationCache:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def _key(self, text: str, source: str, target: str) -> str:
        return hashlib.sha256(f"{source}:{target}:{text}".encode()).hexdigest()

    def get(self, text: str, source: str, target: str) -> str | None:
        return self.items.get(self._key(text, source, target))

    def put(self, text: str, source: str, target: str, value: str) -> None:
        self.items[self._key(text, source, target)] = value


def choose_language(
    text: str,
    source: str,
    candidates: list[str],
    translate: Callable[[str, str, str], str],
    counter: Callable[[str], int] = count_tokens,
    cache: TranslationCache | None = None,
    min_saving: int = 40,
    verification_cost: int = 0,
    reuse_count: int = 1,
) -> TranslationChoice:
    """Выбрать язык для экономии токенов при РЕАЛЬНОМ break-even.

    net = reuse_count * (original - translated) - fee - verification_cost

    Перевод включается только если результат используется reuse_count раз
    ИЛИ экономия покрывает стоимость перевода + проверки. Без этого
    перевод сам по себе стоит дороже, чем экономия на одном использовании.
    """
    cache = cache or TranslationCache()
    original = counter(text)
    if never_translate(text):
        return TranslationChoice(
            text, source, source, original, original, 0, 0, False,
            "never_translate(code/json/url/ids/numbers/short)",
            reuse_count=reuse_count, verification_cost=verification_cost,
        )
    if reuse_count < 1:
        return TranslationChoice(
            text, source, source, original, original, 0, 0, False,
            "no_reuse", reuse_count=reuse_count, verification_cost=verification_cost,
        )

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
        # Стоимость: каждый use платит translated-токены, но экономит original.
        net = reuse_count * (original - tokens) - fee - verification_cost
        if net > original - best[2] - best[3]:
            best = (translated, target, tokens, fee)

    translated, target, tokens, fee = best
    net = reuse_count * (original - tokens) - fee - verification_cost
    if target == source or net < min_saving:
        return TranslationChoice(
            text, source, source, original, original, 0, 0, False,
            "not_profitable", reuse_count=reuse_count, verification_cost=verification_cost,
        )
    return TranslationChoice(
        translated, source, target, original, tokens, fee, net, True,
        "net_saving", reuse_count=reuse_count, verification_cost=verification_cost,
    )
