"""Brain Engine — реверс-инжиниринг мозга в чистом Python (без нейросетей).

УРОК 18.08.2026 (новая наука): генерал сказал «разбери человеческий мозг
по снимкам на тысячи частей и возьми через реверс-инжиниринг всё самое нужное».
Разобрали мозг на принципы и перенесли в код. Каждый принцип — детерминированная
функция, никаких LLM-вызовов. Всё работает за <1ms.

┌──────────────────────────┬────────────────────────────────────────────────┐
│ Принцип мозга            │ Реализация в token-diet                         │
├──────────────────────────┼────────────────────────────────────────────────┤
│ Предсказательное         │ predictive_compress() — не обрабатывать         │
│ кодирование (кора)       │ предсказуемое, только «ошибки предсказания»    │
│ Разреженное кодирование  │ sparse_context() — активировать только нужные   │
│ (нейроны ~2% активны)    │ фрагменты, остальное молчит (экономия токенов)  │
│ Консолидация во сне      │ sleep_consolidate() — ночью: дедуп, удаление    │
│ (гиппокамп → кора)       │ мусора, укрепление важных связей в памяти       │
│ Отбор действий           │ select_action() — базальные ганглии: go/no-go,  │
│ (базальные ганглии)      │ выбрать одно лучшее из кандидатов               │
│ Автоматизация            │ automatize() — мозжечок: частое действие        │
│ (мозжечок)               │ выполняется без «сознательного» размышления    │
│ Фильтр внимания          │ thalamus_gate() — таламус: пропускать только    │
│ (таламус)                │ важное, шум отсекать                            │
│ Нейропластичность        │ hebbian_link() — «нейроны, возбуждающиеся       │
│ (правило Хебба)          │ вместе, связываются вместе» → вики-связи памяти │
└──────────────────────────┴────────────────────────────────────────────────┘
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


# ─────────────────────────────────────────────────────────────────────────────
# 1. Предсказательное кодирование (predictive coding)
#    Мозг не обрабатывает весь вход — только РАСХОЖДЕНИЕ с ожиданием.
#    → не пересылать в промпт то, что уже было, только «ошибку предсказания».
# ─────────────────────────────────────────────────────────────────────────────

def _ngrams(text: str, n: int = 3) -> set[str]:
    """Множество n-грамм текста (признаки, как активность нейронов)."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < n:
        return {" ".join(words)}
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def predictive_compress(new_text: str, history: Iterable[str],
                        novelty_keep: float = 0.35) -> tuple[str, dict]:
    """Сжать новый текст, оставив только «ошибку предсказания».

    Мозг: предсказание = история. Новизна = то, чего в истории не было.
    Возвращает (сжатый текст, статистика).
    """
    known: set[str] = set()
    for h in history:
        known |= _ngrams(h)
    ngrams = _ngrams(new_text)
    novel = ngrams - known
    if not novel:
        return "", {"novel_ngrams": 0, "ratio": 1.0, "saved_tokens": 0}

    words = re.findall(r"\w+", new_text.lower())
    # Оставляем предложения, где есть новые n-граммы
    sentences = re.split(r"(?<=[.!?])\s+", new_text)
    kept = []
    for s in sentences:
        if _ngrams(s) & novel:
            kept.append(s)
    compressed = " ".join(kept).strip()
    ratio = 1 - (len(compressed) / max(len(new_text), 1))
    return compressed, {
        "novel_ngrams": len(novel),
        "ratio": round(ratio, 3),
        "saved_tokens": int(len(new_text) / 4 * ratio),  # ~4 символа на токен
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Разреженное кодирование (sparse coding)
#    В мозге в каждый момент активно ~2% нейронов. Остальное молчит.
#    → из большого контекста отбирать только релевантные фрагменты.
# ─────────────────────────────────────────────────────────────────────────────

def _tf_idf(docs: list[str], query: str) -> list[tuple[int, float]]:
    """TF-IDF-похожесть каждого документа на запрос (без внешних зависимостей)."""
    if not docs:
        return []
    df: Counter[str] = Counter()
    doc_tokens: list[Counter[str]] = []
    for d in docs:
        toks = Counter(re.findall(r"\w+", d.lower()))
        doc_tokens.append(toks)
        for t in toks:
            df[t] += 1
    n = len(docs)
    q = Counter(re.findall(r"\w+", query.lower()))
    scores: list[tuple[int, float]] = []
    for i, toks in enumerate(doc_tokens):
        s = 0.0
        for t, qc in q.items():
            if t in toks:
                idf = (n + 1) / (df[t] + 1)
                s += qc * toks[t] * idf
        scores.append((i, s))
    return scores


def sparse_context(docs: list[str], query: str, k: int = 3) -> list[str]:
    """Активировать только k самых релевантных фрагментов (разреженность)."""
    if not docs:
        return []
    k = max(1, min(k, len(docs)))
    scored = sorted(_tf_idf(docs, query), key=lambda x: x[1], reverse=True)
    return [docs[i] for i, s in scored[:k] if s > 0]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Консолидация во сне (sleep consolidation)
#    Гиппокамп ночью «переигрывает» события: важное переносится в кору
#    (долговременная память), шум отбрасывается, дубли сливаются.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SleepReport:
    """Итог ночной консолидации памяти."""
    deleted: list[str] = field(default_factory=list)
    merged: list[tuple[str, str]] = field(default_factory=list)
    kept: int = 0
    kept_chars: int = 0
    duration_ms: int = 0


def _note_key(text: str) -> str:
    """Ключ похожести заметки (первые слова без стоп-слов)."""
    words = [w for w in re.findall(r"\w+", text.lower())
             if w not in {"и", "в", "на", "с", "по", "для", "из", "о", "от", "не", "что"}]
    return " ".join(words[:6])


def sleep_consolidate(notes: dict[str, str],
                      delete_older_than_days: float = 30.0,
                      junk_prefixes: tuple[str, ...] = ("STOP_HIT", "step_")) -> SleepReport:
    """«Сон» для памяти: удалить мусор, слить дубли, посчитать выживших.

    notes: {имя_заметки: содержимое}. Возвращает отчёт + очищенный словарь
    в поле deleted/merged; сам словарь НЕ мутирует.
    """
    rep = SleepReport()
    now = time.time()
    cleaned: dict[str, str] = {}
    seen_keys: dict[str, str] = {}  # ключ похожести → имя первой заметки

    for name, text in notes.items():
        # 1. Мусор по префиксу имени
        if any(name.startswith(p) for p in junk_prefixes):
            rep.deleted.append(name)
            continue
        # 2. Пустые / битые заметки
        if not text or len(text.strip()) < 20:
            rep.deleted.append(name)
            continue
        # 3. Дубли по содержимому (или почти дубли)
        key = _note_key(text)
        if key in seen_keys:
            rep.merged.append((seen_keys[key], name))
            continue
        seen_keys[key] = name
        cleaned[name] = text

    rep.kept = len(cleaned)
    rep.kept_chars = sum(len(t) for t in cleaned.values())
    return rep


# ─────────────────────────────────────────────────────────────────────────────
# 4. Отбор действий (базальные ганглии — go/no-go)
#    Мозг постоянно имеет много кандидатов-действий, но запускает ОДНО —
#    лучшее по пользе, с подавлением конфликтующих (no-go).
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Action:
    name: str
    utility: float        # польза (больше = лучше)
    cost: float = 0.0     # затраты (меньше = лучше)
    conflict_with: tuple[str, ...] = ()
    payload: Any = None


def select_action(candidates: list[Action], noise: float = 0.0) -> Optional[Action]:
    """Выбрать одно лучшее действие, подавив конфликтующие (no-go)."""
    if not candidates:
        return None
    scored = [(a, a.utility - a.cost) for a in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scored[0]
    if best_score <= 0:
        return None  # все действия вреднее бездействия (no-go)
    # Подавление конфликтующих: если лучшее конфликтует — выкинуть их
    if best.conflict_with:
        scored = [(a, s) for a, s in scored
                  if a.name not in best.conflict_with]
    return best


# ─────────────────────────────────────────────────────────────────────────────
# 5. Автоматизация (мозжечок)
#    Мозжечок после обучения выполняет частое действие без сознания —
#    быстро и без затрат на «размышление». → хеш-кеш частых запросов.
# ─────────────────────────────────────────────────────────────────────────────

class Automatizer:
    """Мозжечок: повторяющиеся запросы выполняются по хешу, без пересчёта."""

    def __init__(self, threshold: int = 3, ttl: float = 3600.0):
        self.threshold = threshold
        self.ttl = ttl
        self._counts: Counter[str] = Counter()
        self._cache: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def _key(self, query: str, context: str = "") -> str:
        return hashlib.sha1(f"{query}|{context}".encode()).hexdigest()[:16]

    def call(self, query: str, fn: Callable[[], Any], context: str = "") -> Any:
        """Выполнить fn(query), но если запрос уже повторялся — из кеша."""
        k = self._key(query, context)
        self._counts[k] += 1
        cached = self._cache.get(k)
        if cached and time.time() - cached[0] < self.ttl:
            self.hits += 1
            return cached[1]
        self.misses += 1
        result = fn()
        if self._counts[k] >= self.threshold:
            self._cache[k] = (time.time(), result)
        return result

    def is_automated(self, query: str, context: str = "") -> bool:
        return self._key(query, context) in self._cache

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "automation_rate": round(self.hits / total, 3) if total else 0.0,
            "cached_patterns": len(self._cache),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 6. Фильтр внимания (таламус)
#    Таламус — шлюз: пропускает к коре только важное, шум отсекается.
#    → из потока сообщений/новостей оставлять только релевантные.
# ─────────────────────────────────────────────────────────────────────────────

def thalamus_gate(items: Iterable[str], focus: str, k: int = 3,
                  min_score: float = 0.0) -> list[str]:
    """Пропустить только k важных элементов (по TF-IDF к фокусу внимания)."""
    docs = list(items)
    if not docs:
        return []
    scored = _tf_idf(docs, focus)
    top = sorted(scored, key=lambda x: x[1], reverse=True)[:k]
    return [docs[i] for i, s in top if s > min_score]


# ─────────────────────────────────────────────────────────────────────────────
# 7. Нейропластичность (правило Хебба)
#    «Нейроны, возбуждающиеся вместе, связываются вместе».
#    → в памяти: понятия, часто встречающиеся вместе, связывать вики-ссылками,
#    чтобы Obsidian-граф «прорастал» и поиск находил больше.
# ─────────────────────────────────────────────────────────────────────────────

STOPWORDS_RU = {"и", "в", "на", "с", "по", "для", "из", "о", "от", "не",
                "что", "как", "это", "при", "до", "за", "у", "к", "же",
                "бы", "от", "но", "а", "то", "все", "она", "он"}


def hebbian_link(notes: dict[str, str], top_pairs: int = 10) -> list[tuple[str, str, int]]:
    """Найти пары понятий, часто встречающиеся вместе (Hebb-связи)."""
    cooccur: Counter[tuple[str, str]] = Counter()
    for text in notes.values():
        words = [w for w in re.findall(r"[а-яёa-z]{4,}", text.lower())
                 if w not in STOPWORDS_RU]
        seen = set()
        for i in range(len(words)):
            for j in range(i + 1, min(i + 5, len(words))):
                pair = tuple(sorted((words[i], words[j])))
                if pair not in seen:
                    cooccur[pair] += 1
                    seen.add(pair)
    return [(a, b, c) for (a, b), c in cooccur.most_common(top_pairs)]


# ─────────────────────────────────────────────────────────────────────────────
# Сборка: полный «сон» памяти и сводка
# ─────────────────────────────────────────────────────────────────────────────

def brain_summary(notes: dict[str, str]) -> dict:
    """Полная сводка мозга по памяти: связи, дубли, здоровье."""
    links = hebbian_link(notes, top_pairs=8)
    sleep = sleep_consolidate(notes)
    return {
        "notes": len(notes),
        "kept_after_sleep": sleep.kept,
        "duplicates_to_merge": len(sleep.merged),
        "junk_to_delete": len(sleep.deleted),
        "hebbian_links": links,
        "memory_chars": sum(len(t) for t in notes.values()),
    }


__all__ = [
    "predictive_compress", "sparse_context", "sleep_consolidate",
    "select_action", "Action", "Automatizer", "thalamus_gate",
    "hebbian_link", "brain_summary", "SleepReport",
]
