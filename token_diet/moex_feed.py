"""MOEX Feed — free Moscow Exchange data via ISS API. NO TOKEN NEEDED.

Tinkoff Invest API requires a token and only works while the market is
reachable. MOEX ISS (https://iss.moex.com) is open and free — quotes,
candles and dividends for ANY Russian security, no registration.

What this adds on top of tinkoff_invest:
- ``get_quote()`` — free live quotes (no token, useful for scripts/CI)
- ``get_candles()`` — historical candles (feeds market_intelligence)
- ``get_dividends()`` — dividend history per security (smart-lab style)
- ``dividend_yield()`` — current dividend yield estimate
- ``next_dividends()`` — upcoming ex-dividend dates (calendar view)
- ``top_by_yield()`` — scan a watchlist, sort by dividend yield

Data is pure REST + JSON — works behind proxies/DPI, respects
``iss.meta=off`` for smaller responses.

For the people. For the planet.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any

ISS_BASE = "https://iss.moex.com/iss"


def _fetch(url: str, timeout: int = 15) -> dict | None:
    """GET a JSON URL with SSL fallback (DPI/proxy-friendly)."""
    try:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
                    return json.loads(resp.read().decode())
            raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _table(data: dict, name: str) -> tuple[list[str], list[list]]:
    """Extract columns + rows from an ISS response block."""
    block = data.get(name)
    if not block:
        return [], []
    return list(block.get("columns", [])), list(block.get("data", []))


@dataclass
class MoexQuote:
    ticker: str
    price: float
    prev_price: float | None
    change_pct: float | None
    time: str = ""
    currency: str = "RUB"


@dataclass
class MoexCandle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MoexDividend:
    ticker: str
    isin: str
    value: float
    currency: str
    registry_close: date | None   # дата закрытия реестра (ex-date)
    recorded: bool = False


@dataclass
class MoexFeed:
    """Free MOEX data. All methods return empty/None on failure — never raise."""

    # ── quote ───────────────────────────────────────────────────────────
    def get_quote(self, ticker: str) -> MoexQuote | None:
        """Live quote from MOEX TQBR board (free, no token)."""
        url = (f"{ISS_BASE}/engines/stock/markets/shares/boards/TQBR/securities/"
               f"{urllib.parse.quote(ticker)}.json?iss.meta=off")
        data = _fetch(url)
        if not data:
            return None
        cols, rows = _table(data, "securities")
        if not rows:
            return None
        row = dict(zip(cols, rows[0]))
        prev = _as_float(row.get("PREVPRICE"))
        price = prev  # закрытие вчера — baseline
        # текущая цена из marketdata block, если доступен
        mcols, mrows = _table(data, "marketdata")
        if mrows:
            mrow = dict(zip(mcols, mrows[0]))
            cur = _as_float(mrow.get("LAST") or mrow.get("CURRENTPRICE"))
            if cur and cur > 0:
                price = cur
        change = None
        if prev and prev > 0 and price:
            change = (price - prev) / prev * 100.0
        return MoexQuote(
            ticker=ticker.upper(), price=price or 0.0,
            prev_price=prev, change_pct=change,
            currency=str(row.get("CURRENCYID") or "RUB"),
        )

    # ── candles ─────────────────────────────────────────────────────────
    def get_candles(
        self, ticker: str, days: int = 30,
        interval: str = "day",
    ) -> list[MoexCandle]:
        """Historical candles (1 day / 10 min / hour / month)."""
        # ISS interval codes: 1min=1, 10min=10, 1hour=60, 1day=24, 1week=7, 1month=31
        iv_map = {"1min": "1", "10min": "10", "hour": "60", "day": "24",
                  "week": "7", "month": "31"}
        interval_code = iv_map.get(interval.lower(), "24")
        url = (f"{ISS_BASE}/engines/stock/markets/shares/boards/TQBR/securities/"
               f"{urllib.parse.quote(ticker)}/candles.json?interval={interval_code}"
               f"&iss.meta=off&from={_date_days_ago(days)}")
        data = _fetch(url)
        if not data:
            return []
        cols, rows = _table(data, "candles")
        if not rows:
            return []
        candles = []
        for r in rows:
            row = dict(zip(cols, r))
            t = row.get("begin")
            try:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00")) if t else datetime.now()
            except (ValueError, AttributeError):
                dt = datetime.now()
            candles.append(MoexCandle(
                time=dt,
                open=_as_float(row.get("open")),
                high=_as_float(row.get("high")),
                low=_as_float(row.get("low")),
                close=_as_float(row.get("close")),
                volume=_as_float(row.get("volume")),
            ))
        return candles

    # ── dividends ───────────────────────────────────────────────────────
    def get_dividends(self, ticker: str, limit: int = 20) -> list[MoexDividend]:
        """Dividend history from MOEX (free, no token)."""
        url = (f"{ISS_BASE}/securities/{urllib.parse.quote(ticker)}/dividends.json"
               f"?iss.meta=off")
        data = _fetch(url)
        if not data:
            return []
        cols, rows = _table(data, "dividends")
        if not rows:
            return []
        result = []
        for r in rows[:limit]:
            row = dict(zip(cols, r))
            rd = row.get("registryclosedate")
            try:
                rdate = date.fromisoformat(rd) if rd else None
            except ValueError:
                rdate = None
            result.append(MoexDividend(
                ticker=ticker.upper(),
                isin=str(row.get("isin") or ""),
                value=_as_float(row.get("value")),
                currency=str(row.get("currencyid") or "RUB"),
                registry_close=rdate,
            ))
        return result

    def dividend_yield(
        self, ticker: str, lookback_years: float = 1.0,
    ) -> dict[str, Any]:
        """Estimate dividend yield: sum of last 12 months / current price."""
        quote = self.get_quote(ticker)
        dividends = self.get_dividends(ticker)
        if not quote or not quote.price or not dividends:
            return {"ticker": ticker, "yield_pct": None, "sum": 0.0,
                    "count": 0, "error": "no data"}
        cutoff = datetime.now().year - lookback_years
        recent = [d for d in dividends
                  if d.registry_close and d.registry_close.year >= cutoff]
        total = sum(d.value for d in recent)
        return {
            "ticker": ticker,
            "price": quote.price,
            "sum_last_12m": round(total, 2),
            "count": len(recent),
            "yield_pct": round(total / quote.price * 100, 2) if total else 0.0,
        }

    def next_dividends(self, watchlist: list[str]) -> list[dict[str, Any]]:
        """Upcoming ex-dates across a watchlist, sorted by date.

        Returns entries with future registry close dates.
        """
        today = date.today()
        events = []
        for ticker in watchlist:
            for d in self.get_dividends(ticker, limit=40):
                if d.registry_close and d.registry_close >= today:
                    events.append({
                        "ticker": d.ticker,
                        "ex_date": d.registry_close.isoformat(),
                        "value": d.value,
                        "currency": d.currency,
                    })
        events.sort(key=lambda e: e["ex_date"])
        return events

    def top_by_yield(self, watchlist: list[str]) -> list[dict[str, Any]]:
        """Scan watchlist, rank by dividend yield (desc)."""
        results = []
        for t in watchlist:
            y = self.dividend_yield(t)
            if y.get("yield_pct") is not None:
                results.append(y)
        results.sort(key=lambda r: r.get("yield_pct") or 0, reverse=True)
        return results


# ═══════════════════════════════════════════════════════════════════════════════

def _as_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _date_days_ago(days: int) -> str:
    from datetime import timedelta
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


__all__ = [
    "MoexFeed", "MoexQuote", "MoexCandle", "MoexDividend",
    "dividend_yield", "next_dividends", "top_by_yield",
]


# ── module-level convenience ──────────────────────────────────────────────────

_feed: MoexFeed | None = None


def _get_feed() -> MoexFeed:
    global _feed
    if _feed is None:
        _feed = MoexFeed()
    return _feed


def dividend_yield(ticker: str) -> dict[str, Any]:
    return _get_feed().dividend_yield(ticker)


def next_dividends(watchlist: list[str]) -> list[dict[str, Any]]:
    return _get_feed().next_dividends(watchlist)


def top_by_yield(watchlist: list[str]) -> list[dict[str, Any]]:
    return _get_feed().top_by_yield(watchlist)
