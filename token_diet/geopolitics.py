"""GeoPulse — геополитический радар для рынка.

УРОК ГЕНЕРАЛА: «Геополитику ты тоже учитывать должен — например то,
что Лавров сказал, что перемирия не будет».

Геополитика двигает рынок СИЛЬНЕЕ любых отчётов:
- заявление «перемирия не будет» → красный день по всему рынку,
- санкции/деэскалация → зелёный день.
Отчёты двигают ОДНУ бумагу. Геополитика двигает ВСЕ.

Этот модуль:
1. Собирает свежие геополитические заголовки (веб-поиск по темам).
2. Классифицирует тон: негатив/позитив/нейтрально по ключевым словам.
3. Даёт market_verdict: GREEN/RED/NEUTRAL день для всего рынка.
4. Интегрируется в live_scan: перед входом смотрим геополитический фон.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Ключевые темы для поиска — можно расширять
TOPICS: list[str] = [
    "перемирие Украина Россия",
    "санкции Россия США",
    "геополитика рынок акций",
    "Лавров заявление",
    "Путин заявление экономика",
]

# Слова-маркеры негатива (рынок вниз)
NEGATIVE_WORDS: list[str] = [
    "перемирие не будет", "не будет перемирия", "перемирия не будет",
    "отверг перемирие", "отверг", "отвергла", "отказались от перемирия",
    "отказалась", "отказался", "отказались", "отказало", "отказ",
    "санкци", "санкции", "усиление ударов", "ужесточить удары",
    "обострени", "эскалаци", "война продолжается", "дефолт",
    "блокада", "заморозк", "конфискаци", "угроз",
    "не пойдут на предложения", "ухудшени", "кризис",
    "прекращение поставок", "экспортные ограничения", "конфликт",
    "нет перемирию", "перемирию не быть", "не видит смысла в перемири",
]

# Слова-маркеры позитива (рынок вверх)
POSITIVE_WORDS: list[str] = [
    "перемирие достигнуто", "соглашение", "деэскалаци", "смягчение",
    "переговоры успешн", "договоренност", "снятие санкций",
    "перемири", "прекращение огня", "мирное урегулирование",
    "разморозк", "потепление", "нормализаци",
    "переговор", "договорились о", "договорил", "обмен пленными",
]


@dataclass
class GeoItem:
    title: str
    source: str
    url: str
    sentiment: float  # -1 (негатив) .. +1 (позитив)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "sentiment": self.sentiment,
        }


@dataclass
class GeoVerdict:
    verdict: str  # GREEN / RED / NEUTRAL
    score: float  # суммарный тональный балл
    items: list[GeoItem] = field(default_factory=list)
    updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "score": round(self.score, 2),
            "updated": self.updated,
            "items": [i.as_dict() for i in self.items[:8]],
        }


def classify_title(title: str) -> float:
    """Тональный балл заголовка: -1 (негатив) .. +1 (позитив)."""
    low = title.lower()
    neg = sum(1 for w in NEGATIVE_WORDS if w in low)
    pos = sum(1 for w in POSITIVE_WORDS if w in low)
    if neg == 0 and pos == 0:
        return 0.0
    total = neg + pos
    return round((pos - neg) / total, 2)


_CACHE_FILE = os.path.join(tempfile.gettempdir(), "token_diet_geo_cache.json")
_CACHE_TTL_SEC = 2 * 3600  # вердикт живёт 2 часа — сеть не должна его стирать


def _google_news(query: str, limit: int = 5, lang: str = "ru") -> list[tuple[str, str]]:
    """Свежие новости через Google News RSS (без API-ключей). (title, url)."""
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET

    hl = "ru" if lang == "ru" else "en"
    gl = "RU" if lang == "ru" else "US"
    url = (f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}"
           f"&hl={hl}&gl={gl}&ceid={hl}:{gl}")

    # Ретраи: сеть через DPI капризничает — пробуем до 3 раз с паузой
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                root = ET.fromstring(resp.read())
            break
        except Exception:
            if attempt == 2:
                return []
            time.sleep(1.0 * (attempt + 1))

    results: list[tuple[str, str]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = item.findtext("link") or ""
        if title and len(title) > 10:
            results.append((title, link))
        if len(results) >= limit:
            break
    return results


def _save_cache(verdict: GeoVerdict) -> None:
    try:
        data = {
            "verdict": verdict.verdict,
            "score": verdict.score,
            "updated": verdict.updated,
            "items": [i.as_dict() for i in verdict.items],
        }
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _load_cache() -> GeoVerdict | None:
    try:
        if not os.path.exists(_CACHE_FILE):
            return None
        if time.time() - os.path.getmtime(_CACHE_FILE) > _CACHE_TTL_SEC:
            return None  # кэш протух
        with open(_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        items = [GeoItem(**i) for i in data.get("items", [])]
        return GeoVerdict(
            verdict=data.get("verdict", "NEUTRAL"),
            score=data.get("score", 0.0),
            items=items,
            updated=data.get("updated", ""),
        )
    except Exception:
        return None


def _fetch_headlines(limit_per_topic: int = 3, topics: list[str] | None = None) -> list[GeoItem]:
    """Собрать свежие геополитические заголовки через Google News RSS."""
    items: list[GeoItem] = []
    for topic in (topics or TOPICS):
        try:
            for title, url in _google_news(topic, limit=limit_per_topic):
                if not title:
                    continue
                items.append(GeoItem(
                    title=title, source=_source_from_url(url),
                    url=url, sentiment=classify_title(title),
                ))
        except Exception:
            continue
    return items


def _source_from_url(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else "web"


def geo_verdict(limit_per_topic: int = 3, topics: list[str] | None = None) -> GeoVerdict:
    """Итоговый геополитический вердикт по свежим заголовкам.

    RED: перевес негатива → осторожность, входы не делать.
    GREEN: перевес позитива → можно агрессивнее.
    NEUTRAL: фон нейтральный.
    """
    items = _fetch_headlines(limit_per_topic, topics)

    # Сеть упала / пусто: отдаём последний живой вердикт из кэша (до 2 ч)
    if not items:
        cached = _load_cache()
        if cached is not None and cached.verdict != "NEUTRAL":
            return cached
        return GeoVerdict(verdict="NEUTRAL", score=0.0)

    total = sum(i.sentiment for i in items)
    neg = sum(1 for i in items if i.sentiment < 0)
    pos = sum(1 for i in items if i.sentiment > 0)

    if neg >= 2 and neg > pos:
        verdict = "RED"
    elif neg == 1 and pos == 0 and total < 0:
        verdict = "RED"  # один явный негатив без контр-позитива
    elif pos >= 2 and pos > neg:
        verdict = "GREEN"
    elif pos == 1 and neg == 0 and total > 0:
        verdict = "GREEN"
    else:
        verdict = "NEUTRAL"

    result = GeoVerdict(verdict=verdict, score=round(total, 2), items=items)
    if verdict != "NEUTRAL":
        _save_cache(result)
    else:
        # Живой фетч дал NEUTRAL, но фон не меняется каждые 5 минут:
        # если есть свежий явный кэш (RED/GREEN) — держим его, чтобы
        # вердикт не прыгал из-за капризной сети.
        cached = _load_cache()
        if cached is not None and cached.verdict != "NEUTRAL":
            return cached
    return result


def market_context_block(verdict: GeoVerdict | None = None) -> str:
    """Текстовый блок для morning_brief / live_scan."""
    v = verdict or geo_verdict()
    emoji = {"GREEN": "🟢", "RED": "🔴", "NEUTRAL": "⚪"}.get(v.verdict, "⚪")
    lines = [f"{emoji} Геополитика: {v.verdict} (балл {v.score:+.2f})"]
    for i in v.items[:4]:
        mark = "🟢" if i.sentiment > 0 else ("🔴" if i.sentiment < 0 else "⚪")
        lines.append(f"  {mark} {i.title[:90]} — {i.source}")
    return "\n".join(lines)


if __name__ == "__main__":
    v = geo_verdict()
    print(market_context_block(v))
    print()
    print("ВЕРДИКТ для рынка:", v.verdict)
    print("→ Красный день: входы только по сильнейшим сигналам, кэш в приоритете" if v.verdict == "RED"
          else "→ Зелёный фон: можно агрессивнее" if v.verdict == "GREEN"
          else "→ Нейтрально: решаем по бумагам")
