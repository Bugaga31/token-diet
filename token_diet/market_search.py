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
    """Свежие новости по инструменту — три источника по порядку:

    1. FRESH_NEWS (телеграм-демон) — самый быстрый
    2. T-Invest API get_news (если SDK/эндпоинт жив)
    3. Браузер (web_agent) — страница Т-Инвестиций, всегда работает
    """
    import os

    path = os.path.expanduser("~/FRESH_NEWS.md")
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        text = ""
    out: list[dict] = []
    for line in text.splitlines():
        q = query.upper()
        if q[:4] in line.upper() or q in line.upper():
            out.append({"line": line.strip()[:200], "source": "tg"})
        if len(out) >= limit:
            return out

    # 2) T-Invest API (если новостной эндпоинт доступен)
    try:
        from .tinkoff_invest import TinkoffInvest

        inv = TinkoffInvest()
        api_news = inv.get_news(query, limit=limit)
        for n in api_news:
            title = n.get("title") or n.get("text") or ""
            if title:
                out.append({"line": title.strip()[:200],
                            "source": "t-invest", "time": n.get("time", "")})
        if len(out) >= limit:
            return out
    except Exception:
        pass

    # 3) Браузер: страница Т-Инвестиций по тикеру
    try:
        from .web_agent import visit

        ticker = query.split()[0].upper()
        r = visit(f"https://www.tbank.ru/invest/stocks/{ticker}/",
                  timeout_ms=35000)
        if r.get("status") == "ok":
            lines = [ln.strip() for ln in r.get("text", "").split("\n")
                     if ln.strip() and len(ln.strip()) > 25]
            # строки с признаками новости/анализа
            import re

            keys = re.compile(r"\d{1,2}\s+[а-яё]+\s+\d{4}|уровн|вход|анализ|"
                              r"прогноз|маржин|сюрприз|отчёт|отчет|дивиденд|"
                              r"точка входа", re.IGNORECASE)
            for line in lines:
                if keys.search(line):
                    out.append({"line": line[:200], "source": "t-bank-web"})
                if len(out) >= limit:
                    break
    except Exception:
        pass

    return out


__all__ = ["classify", "market_info"]
