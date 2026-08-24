"""telegram_market_feed: live market news from curated Telegram channels.

Bridges the real world into token-diet's honest InvestmentAnalyzer:

    Telegram channels (curated for the RF market)
        -> fetch latest messages (Telethon, optional dependency)
        -> detect tickers / companies in the text
        -> convert to InvestmentAnalyzer.NewsItem
        -> feed straight into InvestmentAnalyzer for trap-aware verdicts
        -> or render a compact morning digest

Everything degrades gracefully: without Telethon / a session file / network
access the module still works for building NewsItems from plain text, and the
ticker/company dictionaries make it useful as a pure parser.

Security model (same as the rest of token-diet):
    * credentials are loaded from the user's own local files or env vars,
      NEVER committed, NEVER printed, NEVER logged.
    * the session file stays outside any git repository.

For the people. For the planet. Honesty is cheaper than regret.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .investment_analyzer import NewsItem

# ─────────────────────────────────────────────────────────────────────────────
# 1. Curated channels (the RF market "kitchen" — user-vetted, no signal spam)
# ─────────────────────────────────────────────────────────────────────────────

MARKET_CHANNELS: dict[str, str] = {
    # Первоисточники
    "bankrossii": "Банк России",
    "MoscowExchangeOfficial": "MOEX — Московская биржа",
    "minfin": "Минфин России",
    "government_rus": "Правительство России",
    # Макро и фундаментальный анализ
    "MMInsights": "MarketMaker's Insights (MMI)",
    "Bonds_lab": "Bonds lab",
    "bitkogan": "Евгений Коган",
    # Разборы компаний
    "smartlabnews": "СМАРТЛАБ",
    "newssmartlab": "СМАРТЛАБ НОВОСТИ",
    "bondsmartlab": "СМАРТЛАБ ОБЛИГАЦИИ",
    "smartlabru": "smart-lab",
    "invest_heroes": "Invest Heroes",
    "AK47pfl": "РынкиДеньгиВласть | РДВ",
    # Поток новостей
    "markettwits": "MarketTwits",
    "selfinvestor": "РБК Инвестиции",
    "rbc_invest": "РБК Инвестиции",
    "bcs_express": "БКС Экспресс",
    "t_bank_invest": "Т-Инвестиции",
    "finam_invest": "Финам Инвестиции",
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Russian ticker dictionary — name aliases -> MOEX ticker
# ─────────────────────────────────────────────────────────────────────────────

TICKER_ALIASES: dict[str, str] = {
    # Сбер
    "сбер": "SBER",
    "сбербанк": "SBER",
    "sber": "SBER",
    # Газпром
    "газпром": "GAZP",
    "gazprom": "GAZP",
    # Полюс
    "полюс": "PLZL",
    "polyus": "PLZL",
    # Яндекс
    "яндекс": "YDEX",
    "yandex": "YDEX",
    "ydex": "YDEX",
    # Т-Технологии / Тинькофф
    "т-технологии": "T",
    "тинькофф": "T",
    "tcs": "T",
    # Лукойл
    "лукойл": "LKOH",
    "lukoil": "LKOH",
    "lkoh": "LKOH",
    # Новатэк
    "новатэк": "NVTK",
    "novatek": "NVTK",
    # Норникель
    "норникель": "GMKN",
    "nornickel": "GMKN",
    "гмк": "GMKN",
    # Роснефть
    "роснефть": "ROSN",
    "rosneft": "ROSN",
    # Сургутнефтегаз
    "сургутнефтегаз": "SNGSP",
    "surgut": "SNGSP",
    # Татнефть
    "татнефть": "TATN",
    "tatneft": "TATN",
    # ВТБ
    "втб": "VTBR",
    "vtb": "VTBR",
    # Русал
    "русал": "RUAL",
    "rusal": "RUAL",
    # Акрон
    "акрон": "AKRN",
    "akron": "AKRN",
    # VK
    "вк": "VKCO",
    "vk": "VKCO",
    "vkco": "VKCO",
    # Ozon
    "озон": "OZON",
    "ozon": "OZON",
    # Магнит
    "магнит": "MGNT",
    "magnit": "MGNT",
    # Северсталь
    "северсталь": "CHMF",
    "severstal": "CHMF",
    # МТС
    "мтс": "MTSS",
    "mts": "MTSS",
    # Аэрофлот
    "аэрофлот": "AFLT",
    "aeroflot": "AFLT",
    # Мосбиржа
    "мосбиржа": "MOEX",
    "московская биржа": "MOEX",
    "moex": "MOEX",
    # Polymetal
    "полиметалл": "POLY",
    "polymetal": "POLY",
}

# Точные тикеры, которые могут встречаться прямо в тексте (SBER, GAZP, PLZL…)
_LITERAL_TICKER_RE = re.compile(r"\b([A-Z]{3,5})\b")

# Uppercase words that LOOK like tickers but are financial/economic jargon or
# English filler — never MOEX tickers. Keeps the feed honest (no fake signals).
_FALSE_POSITIVE_TICKERS = frozenset({
    "THE", "AND", "FOR", "NEW", "TOP", "OUT", "ALL", "ANY", "NOT", "YOU",
    "RUB", "USD", "EUR", "CNY", "API", "URL", "GIT", "PDF", "CEO", "CFO",
    "IPO", "SPO", "CAPEX", "EBITDA", "FCF", "EPS", "P/E", "DPS", "DCF",
    "GDP", "CPI", "PPI", "PMI", "CBR", "MOEX", "OTC", "ETF", "REIT",
    "MAX", "MIN", "AVG", "TOTAL", "YTD", "YoY", "Q1", "Q2", "Q3", "Q4",
    "ARS", "ONDO", "RGBI", "SPCX", "WGC", "LTM", "NTM", "H1", "H2",
})

# ─────────────────────────────────────────────────────────────────────────────
# 3. Pure parsers (no I/O, fully testable, no dependencies)
# ─────────────────────────────────────────────────────────────────────────────

def detect_tickers(text: str) -> list[str]:
    """Find MOEX tickers mentioned in a text snippet.

    Matches both company names (Сбер, Газпром…) and literal tickers (SBER,
    GAZP…). Returns unique tickers in order of appearance.
    """
    if not text:
        return []
    lowered = text.lower()
    found: list[str] = []
    seen: set[str] = set()

    # 1) company-name aliases
    for alias, ticker in TICKER_ALIASES.items():
        if alias in lowered and ticker not in seen:
            seen.add(ticker)
            found.append(ticker)

    # 2) literal tickers (uppercase, 3-5 letters, word-bounded)
    for m in _LITERAL_TICKER_RE.finditer(text):
        t = m.group(1)
        # reject common false positives: financial/economic abbreviations,
        # English filler and words that are not MOEX tickers.
        if t in _FALSE_POSITIVE_TICKERS:
            continue
        if t not in seen:
            seen.add(t)
            found.append(t)

    # 3) single-letter MOEX tickers: T (Т-Технологии) — standalone uppercase
    if re.search(r"\bT\b", text):
        if "T" not in seen:
            seen.add("T")
            found.append("T")

    return found


@dataclass
class RawMessage:
    """A raw message fetched from a channel (or constructed for tests)."""

    channel: str
    text: str
    published: datetime
    views: int = 0


@dataclass
class FeedMessage:
    """A parsed message with tickers attached."""

    channel: str
    text: str
    published: datetime
    views: int = 0
    tickers: list[str] = field(default_factory=list)


def parse_message(raw: RawMessage) -> FeedMessage:
    """Attach detected tickers to a raw message (pure function)."""
    return FeedMessage(
        channel=raw.channel,
        text=raw.text,
        published=raw.published,
        views=raw.views,
        tickers=detect_tickers(raw.text),
    )


def feed_to_news_items(messages: list[RawMessage]) -> list[NewsItem]:
    """Convert raw messages into InvestmentAnalyzer.NewsItem.

    One NewsItem per (message, ticker) pair, with the snippet truncated to
    keep the feed cheap to analyze.
    """
    items: list[NewsItem] = []
    for m in messages:
        fm = parse_message(m)
        for t in fm.tickers:
            snippet = " ".join(fm.text.split())[:180]
            items.append(
                NewsItem(
                    ticker=t,
                    snippet=snippet,
                    published=m.published.date(),
                )
            )
    return items


def compact_digest(messages: list[RawMessage], max_lines: int = 12) -> str:
    """Render a compact, token-cheap digest of the feed.

    One line per message: channel • headline. Tickers are tagged. This is the
    token-diet way — same information, fewer billed tokens.
    """
    lines: list[str] = []
    for m in messages:
        fm = parse_message(m)
        head = " ".join(fm.text.split())[:110]
        tag = ",".join(fm.tickers) if fm.tickers else "-"
        lines.append(f"[{m.channel}] ({tag}) {head}")
        if len(lines) >= max_lines:
            break
    if not lines:
        return "Пусто — каналы молчат."
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Live Telegram fetch (optional dependency: telethon)
# ─────────────────────────────────────────────────────────────────────────────

# Стандартные места для JSON-кредов Telegram (проверяются автоматически)
_CREDENTIALS_CANDIDATES = [
    "~/221099698.json",
    "~/.telegram-mcp/credentials.json",
    "~/.telegram-mcp/telegram.json",
    "~/.telegram/credentials.json",
]


def _load_credentials(credentials_path: str | None = None) -> tuple[int, str] | None:
    """Load (api_id, api_hash) from a local JSON or env vars. Never prints them.

    Precedence:
      1. credentials_path JSON (если задан)
      2. TELEGRAM_API_ID / TELEGRAM_API_HASH env vars
      3. автоматический поиск JSON-кредов в стандартных местах
         (~/221099698.json и др. — формат telethon: app_id/app_hash)

    Returns None if nothing usable is found (module still works offline).
    """
    import os

    candidates: list[str] = []
    if credentials_path:
        candidates.append(credentials_path)
    candidates += _CREDENTIALS_CANDIDATES

    for path in candidates:
        try:
            expanded = os.path.expanduser(path)
            if not os.path.exists(expanded):
                continue
            import json

            with open(expanded, encoding="utf-8") as fh:
                d = json.load(fh)
            api_id = int(d.get("app_id", d.get("api_id", 0)))
            api_hash = d.get("app_hash", d.get("api_hash", ""))
            if api_id and api_hash:
                return api_id, api_hash
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue

    try:
        api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
        api_hash = os.environ.get("TELEGRAM_API_HASH", "")
        if api_id and api_hash:
            return api_id, api_hash
    except (TypeError, ValueError):
        pass
    return None


class TelegramMarketFeed:
    """Fetch the latest messages from the curated market channels.

    Usage::

        feed = TelegramMarketFeed(session_path="~/.telegram-mcp/telegram_live.session")
        raw = await feed.fetch_latest(limit=5)     # async
        digest = compact_digest(raw)
        items = feed_to_news_items(raw)

    If telethon is not installed, or the session/credentials are missing,
    ``fetch_latest`` returns an empty list — the pure parsers above still work.
    """

    def __init__(
        self,
        session_path: str = "~/.telegram-mcp/telegram_live.session",
        credentials_path: str | None = None,
        proxy: tuple | None = None,
        channels: dict[str, str] | None = None,
    ):
        self.session_path = session_path
        self.credentials_path = credentials_path
        self.proxy = proxy or ("socks5", "127.0.0.1", 3066)
        self.channels = channels or MARKET_CHANNELS
        self._client = None

    # -- lazy client ---------------------------------------------------------
    def _make_client(self):
        """Build a Telethon client. Returns None if dependencies are missing."""
        try:
            import telethon  # noqa: F401
            from telethon import TelegramClient
        except ImportError:
            return None
        creds = _load_credentials(self.credentials_path)
        if creds is None:
            return None
        api_id, api_hash = creds
        import os

        session = os.path.expanduser(self.session_path)
        if not os.path.exists(session):
            return None
        kwargs = {"connection_retries": 5, "retry_delay": 2}
        if self.proxy and isinstance(self.proxy, tuple):
            try:
                import socks  # pysocks

                if self.proxy[0] in ("socks5", "socks"):
                    kwargs["proxy"] = (socks.SOCKS5, self.proxy[1], self.proxy[2])
            except ImportError:
                pass
        return TelegramClient(session, api_id, api_hash, **kwargs)

    async def _get_or_create_client(self):
        if self._client is None:
            self._client = self._make_client()
        return self._client

    # -- async fetch ---------------------------------------------------------
    async def fetch_latest(self, limit: int = 5) -> list[RawMessage]:
        """Fetch the latest `limit` messages per curated channel (async).

        Requires: telethon + a valid local session + credentials.
        Returns [] on any failure — never raises for offline use.
        """
        client = await self._get_or_create_client()
        if client is None:
            return []
        try:
            await client.connect()
            if not client.is_connected():
                return []
            out: list[RawMessage] = []
            for username in self.channels:
                try:
                    entity = await client.get_entity(username)
                    async for msg in client.iter_messages(entity, limit=limit):
                        if not msg.text:
                            continue
                        out.append(
                            RawMessage(
                                channel=self.channels[username],
                                text=msg.text[:500],
                                published=msg.date or datetime.now(),
                                views=getattr(msg, "views", 0) or 0,
                            )
                        )
                except Exception:
                    continue
            return out
        except Exception:
            return []
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    # -- sync convenience ----------------------------------------------------
    def fetch_latest_sync(self, limit: int = 5) -> list[RawMessage]:
        """Synchronous wrapper around fetch_latest (spins its own loop)."""
        try:
            import asyncio

            return asyncio.run(self.fetch_latest(limit=limit))
        except RuntimeError:
            return []


__all__ = [
    "FeedMessage",
    "MARKET_CHANNELS",
    "RawMessage",
    "TICKER_ALIASES",
    "TelegramMarketFeed",
    "compact_digest",
    "detect_tickers",
    "feed_to_news_items",
    "parse_message",
]
