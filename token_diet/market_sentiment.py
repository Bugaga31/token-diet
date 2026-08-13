"""Market Sentiment — собрать настроение толпы из Telegram-ленты и Пульса.

Раньше сантимент считался отдельно от техники. Этот модуль собирает тексты
упоминаний бумаги из двух источников и отдаёт их сигнальному движку
(generate_signal уже умеет замешивать сантимент 30% к технике 70%).

Источники:
1. FRESH_NEWS.md — локальная лента, которую пишет telegram-демон (быстро, без сети).
2. Пульс (pulse_reader) — посты по тикеру (сеть, отказоустойчиво).

Всё детерминированно, 0 LLM-вызовов.
"""

from __future__ import annotations

import re
from pathlib import Path

FRESH_NEWS_PATH = Path.home() / "FRESH_NEWS.md"

# тикер → слова, по которым ищем упоминание в ленте
_COMPANY_KEYWORDS: dict[str, list[str]] = {
    "GMKN": ["норникель", "норильский", "гмк"],
    "CHMF": ["северсталь"],
    "SBER": ["сбер", "сбербанк"],
    "GAZP": ["газпром"],
    "LKOH": ["лукойл"],
    "ROSN": ["роснефть"],
    "YDEX": ["яндекс"],
    "PLZL": ["полюс"],
    "MOEX": ["мосбиржа", "московская биржа"],
    "ALRS": ["алроса"],
    "MGNT": ["магнит"],
    "VTBR": ["втб"],
    "MAGN": ["ммк", "магнитогорск"],
    "NLMK": ["нлмк", "новолипецк"],
}


def company_keywords(ticker: str) -> list[str]:
    """Слова для поиска упоминаний бумаги (тикер + название компании)."""
    t = ticker.upper()
    return [t.lower()] + _COMPANY_KEYWORDS.get(t, [])


def from_fresh_news(ticker: str, limit: int = 12, path: Path | None = None) -> list[str]:
    """Найти упоминания бумаги в локальной ленте FRESH_NEWS.md.

    Возвращает обрезанные строки-упоминания (тексты для сантимента).
    """
    path = path or FRESH_NEWS_PATH
    if not path.exists():
        return []
    keys = company_keywords(ticker)
    texts: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            low = line.lower()
            if any(k in low for k in keys):
                # убираем префикс заголовка «## [...]» для чистоты
                clean = re.sub(r"^##\s*\[[^\]]*\]\s*", "", line).strip()
                if len(clean) > 6:
                    texts.append(clean[:300])
    except OSError:
        pass
    return texts[-limit:]


def from_pulse(ticker: str, limit: int = 10) -> list[str]:
    """Посты из Пульса по тикеру (сеть). Отказоустойчиво."""
    try:
        from .pulse_reader import get_ticker_posts
        posts = get_ticker_posts(ticker, limit=limit)
        return [p.text for p in posts if p and p.text]
    except Exception:
        return []


def gather_sentiment_texts(ticker: str, limit: int = 12, include_pulse: bool = True) -> list[str]:
    """Все тексты сантимента по бумаге: лента + Пульс."""
    texts = from_fresh_news(ticker, limit=limit)
    if include_pulse:
        texts += from_pulse(ticker, limit=limit)
    return texts
