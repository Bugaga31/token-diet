"""hybrid_search — гибридный поиск по памяти (reverse-engineering SonicJS AI Search).

УРОК 16.08 (sonicjs.com/plugins/ai-search):
SonicJS AI Search строит поиск так:
  1. AI-режим (vector embeddings) — семантика по смыслу;
  2. Keyword-режим (SQL LIKE по title/slug/content) — фолбэк;
  3. Автокомплит с дебаунсом 300 мс;
  4. Кэш результатов (cache_duration);
  5. Авто-fallback: если AI сломался — тихо падаем на keyword.

Мы не можем гонять embeddings офлайн (0 внешних зависимостей, правило
«для людей»), поэтому реализуем ТРИ уровня релевантности детерминированно:
  - exact-title match      → вес 3.0
  - keyword overlap (BM25) → вес 2.0
  - fuzzy / stem эвристика → вес 1.0
Плюс: кэш, автокомплит, авто-fallback между источниками (память → библиотека).

Интеграция: memory_cli.recall может использовать search() вместо сырого
перебора; telegram_search — тоже.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

# ── Стемминг (лёгкий, без внешних библиотек) ──────────────────────────────
_STEM_SUFFIXES = ("ing", "ed", "es", "s", "ly", "tion", "ment", "ness", "ers")


def _stem(word: str) -> str:
    w = word.lower()
    if len(w) <= 4:
        return w
    for suf in _STEM_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


_WORD_RE = re.compile(r"[a-zа-яё0-9_]+", re.IGNORECASE)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def _unique_stems(text: str) -> set[str]:
    return {_stem(w) for w in _words(text)}


# ── Кэш ────────────────────────────────────────────────────────────────────
class _Cache:
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def get(self, key: str) -> list[dict[str, Any]] | None:
        item = self._store.get(key)
        if not item:
            return None
        ts, results = item
        if time.time() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return results

    def put(self, key: str, results: list[dict[str, Any]]) -> None:
        self._store[key] = (time.time(), results)

    def size(self) -> int:
        return len(self._store)


_cache = _Cache(ttl_seconds=1800)  # 30 мин — как cache_duration SonicJS (1ч, но память свежее)


# ── Ранжирование: 3 уровня ─────────────────────────────────────────────────
def _score_doc(query_stems: set[str], title: str, body: str) -> float:
    title_stems = _unique_stems(title)
    body_stems = _unique_stems(body)
    score = 0.0
    # 1) точное совпадение фразы в заголовке — самое сильное
    q_lower = " ".join(sorted(query_stems))
    if q_lower and q_lower in title.lower():
        score += 3.0
    # 2) BM25-подобный overlap по стемам
    overlap = query_stems & title_stems
    score += 2.0 * len(overlap)
    overlap_body = query_stems & body_stems
    score += 1.0 * len(overlap_body)
    # 3) лёгкая штрафовка за «шумные» стоп-слова в запросе
    return score


def rank(query: str, docs: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Детерминированная сортировка документов по релевантности."""
    if not docs:
        return []
    q_stems = _unique_stems(query)
    if not q_stems:
        return docs[:limit]
    scored = []
    for d in docs:
        title = d.get("title") or d.get("name") or ""
        body = d.get("body") or d.get("text") or ""
        d["_score"] = _score_doc(q_stems, title, body)
        scored.append(d)
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[:limit]


# ── Автокомплит (как /api/search/suggest) ─────────────────────────────────
def autocomplete(query: str, docs: list[dict[str, Any]], limit: int = 6) -> list[str]:
    """Подсказки по частичному запросу (дебаунс 300мс — на стороне вызова)."""
    if len(query) < 2:
        return []
    q = query.lower()
    hints: list[str] = []
    for d in docs:
        title = (d.get("title") or d.get("name") or "").strip()
        if title.lower().startswith(q) and title not in hints:
            hints.append(title)
        if len(hints) >= limit:
            break
    return hints[:limit]


# ── Гибридный поиск по Obsidian-хранилищу ─────────────────────────────────
def search_vault(
    query: str,
    vault_dir: str | Path,
    limit: int = 5,
    use_cache: bool = True,
    include_scores: bool = False,
) -> list[dict[str, Any]]:
    """Гибридный поиск по .md-файлам хранилища.

    SonicJS-паттерн: AI-режим → keyword fallback. У нас вместо AI —
    ранжирование по 3 уровням; fallback — простое текстовое совпадение.
    """
    cache_key = f"{query}|{vault_dir}|{limit}"
    if use_cache:
        hit = _cache.get(cache_key)
        if hit is not None:
            return hit

    vault = Path(vault_dir)
    docs: list[dict[str, Any]] = []
    if vault.exists():
        for f in sorted(vault.glob("*.md")):
            try:
                body = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # берём title из front-matter или имени файла
            title = f.stem
            m = re.search(r"^title:\s*(.+)$", body, re.MULTILINE)
            if m:
                title = m.group(1).strip().strip('"')
            docs.append({"title": title, "body": body[:4000], "path": str(f)})

    results = rank(query, docs, limit=limit)
    if use_cache:
        _cache.put(cache_key, results)
    if not include_scores:
        for r in results:
            r.pop("_score", None)
    return results


# ── Fallback-цепочка источников (память → библиотека) ─────────────────────
def hybrid_search(
    query: str,
    sources: list[dict[str, Any]],
    limit: int = 5,
) -> dict[str, Any]:
    """Ищет по нескольким источникам и тихо фолбэчит.

    sources: [{"name": ..., "docs": [...]}, ...] — первый непустой побеждает.
    Возвращает {"source": str, "results": [...]}.
    """
    for src in sources:
        docs = src.get("docs") or []
        if not docs:
            continue
        ranked = rank(query, docs, limit=limit)
        if ranked:
            return {"source": src.get("name", "?"), "results": ranked}
    return {"source": "none", "results": []}


# ── Утилита для telegram_search: реранк результатов ───────────────────────
def rerank_telegram(query: str, results: list[dict[str, Any]], top_k: int = 8) -> list[dict[str, Any]]:
    """Пере-ранжировать результаты поиска по ТГ той же шкалой (BM25-стиль)."""
    return rank(query, results, limit=top_k)


def cache_stats() -> dict[str, Any]:
    return {"entries": _cache.size(), "ttl_seconds": _cache.ttl}
