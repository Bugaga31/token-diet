"""Market Search — единый поиск информации по рынку.

Одна команда, которая сама понимает, что спрашивают:

  "RU000A10BZJ4"      → ISIN → облигация (MOEX TQCB: живая цена)
  "GMKN" / "SBER"     → тикер → акция (T-Invest + MOEX)
  "Балтийский лизинг" → название → поиск + определение + котировка

Правило честности: если источник не отдал данные (у Т-Инвест нет бонда
в базе — это 404 «инструмент не найден», а не поломка) — сразу падаем
на запасной источник (MOEX) и сообщаем, откуда взяли цифру.
"""

from __future__ import annotations

import re
from typing import Any

# ISIN: 2 буквы + 10 символов (например RU000A10BZJ4)
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
# Тинькофф/Мосбиржа тикеры акций
_TICKER_RE = re.compile(r"^[A-Z]{1,6}$")


def classify(query: str) -> dict:
    """Чистая функция: определить, что за инструмент. Без сети."""
    q = (query or "").strip().upper()
    if _ISIN_RE.match(q):
        return {"type": "bond_isin", "value": q}
    if _TICKER_RE.match(q):
        return {"type": "ticker", "value": q}
    return {"type": "name", "value": query.strip()}


def _from_moex_bond(isin: str) -> dict | None:
    from .moex_feed import MoexFeed

    try:
        return MoexFeed().get_bond_quote(isin)
    except Exception:
        return None


def _from_tinvest_stock(ticker: str) -> dict | None:
    from .tinkoff_invest import TinkoffInvest

    try:
        inv = TinkoffInvest()
        q = inv.get_quote(ticker)
        if q is None:
            return None
        return {
            "ticker": ticker,
            "price": q.price,
            "change_pct": q.change_pct,
            "source": "tinvest",
        }
    except Exception:
        return None


def _from_moex_stock(ticker: str) -> dict | None:
    from .moex_feed import MoexFeed

    try:
        q = MoexFeed().get_quote(ticker)
        if q is None:
            return None
        return {
            "ticker": ticker,
            "price": q.price,
            "change_pct": q.change_pct,
            "source": "moex",
        }
    except Exception:
        return None


def market_info(query: str, with_news: bool = False) -> dict:
    """Полная карточка по запросу: цена + изменение + источник (+новости).

    Returns {"status", "kind", "query", "quote": {...}, "source", "news": [...]}.
    """
    kind = classify(query)

    if kind["type"] == "bond_isin":
        isin = kind["value"]
        quote = _from_moex_bond(isin)
        if quote is None:
            return {"status": "error", "kind": "bond", "query": query,
                    "error": "MOEX не отдал данные по облигации"}
        result = {
            "status": "ok",
            "kind": "bond",
            "query": query,
            "isin": isin,
            "quote": quote,
            "source": "moex_tqcb",
        }
    elif kind["type"] == "ticker":
        ticker = kind["value"]
        quote = _from_tinvest_stock(ticker)
        source = "tinvest"
        if quote is None:
            quote = _from_moex_stock(ticker)
            source = "moex"
        if quote is None:
            return {"status": "error", "kind": "stock", "query": query,
                    "error": "нет котировки ни в T-Invest, ни на MOEX"}
        result = {
            "status": "ok",
            "kind": "stock",
            "query": query,
            "quote": quote,
            "source": source,
        }
    else:
        # Название: ищем ISIN/тикер через веб-поиск
        return {
            "status": "needs_resolution",
            "kind": "name",
            "query": query,
            "hint": "укажи ISIN облигации (RU...) или тикер акции (GMKN, SBER)",
        }

    if with_news and result["status"] == "ok":
        result["news"] = _recent_news(result.get("query", query))
    return result


def _recent_news(query: str, limit: int = 3) -> list[dict]:
    """Свежие новости по инструменту из FRESH_NEWS (телеграм-демон)."""
    import os

    path = os.path.expanduser("~/FRESH_NEWS.md")
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return []
    out: list[dict] = []
    for line in text.splitlines():
        if query.upper()[:4] in line.upper() or query.upper() in line.upper():
            out.append({"line": line.strip()[:200]})
        if len(out) >= limit:
            break
    return out


__all__ = ["classify", "market_info"]
