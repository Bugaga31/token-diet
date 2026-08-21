"""Tests for moex_feed.py — free MOEX ISS data (no token)."""

from datetime import date

from token_diet.moex_feed import (
    MoexCandle,
    MoexDividend,
    MoexFeed,
    MoexQuote,
    _as_float,
    _date_days_ago,
)


class TestHelpers:
    def test_as_float(self):
        assert _as_float("12.5") == 12.5
        assert _as_float(None) == 0.0
        assert _as_float("abc") == 0.0
        assert _as_float(7) == 7.0

    def test_date_days_ago(self):
        d = _date_days_ago(30)
        assert len(d) == 10  # YYYY-MM-DD
        assert d[4] == "-" and d[7] == "-"


class TestDataClasses:
    def test_quote_fields(self):
        q = MoexQuote("PLZL", 100.0, 98.0, 2.04, "now", "RUB")
        assert q.ticker == "PLZL"
        assert q.change_pct == 2.04

    def test_candle_fields(self):
        c = MoexCandle(date(2026, 8, 10), 1, 2, 3, 4, 5)
        assert c.close == 4

    def test_dividend_fields(self):
        d = MoexDividend("PLZL", "RU000A0JNAA8", 73.0, "RUB",
                         date(2026, 1, 1))
        assert d.currency == "RUB"


class TestLiveFeed:
    """Live tests against MOEX ISS (network). Skipped if offline."""

    def test_quote_plzl(self):
        q = MoexFeed().get_quote("PLZL")
        if q is None:
            return  # offline — not a failure
        assert q.price > 0
        assert q.ticker == "PLZL"

    def test_candles_plzl(self):
        candles = MoexFeed().get_candles("PLZL", days=15)
        if not candles:
            return
        assert len(candles) >= 5
        assert candles[-1].close > 0

    def test_dividends_plzl(self):
        divs = MoexFeed().get_dividends("PLZL", limit=3)
        if not divs:
            return
        assert divs[0].value > 0

    def test_dividend_yield_returns_dict(self):
        y = MoexFeed().dividend_yield("PLZL")
        assert "yield_pct" in y
        assert "ticker" in y

    def test_next_dividends_structure(self):
        events = MoexFeed().next_dividends(["PLZL", "SBER"])
        assert isinstance(events, list)
        for e in events:
            assert "ticker" in e and "ex_date" in e

    def test_top_by_yield_sorted(self):
        top = MoexFeed().top_by_yield(["PLZL", "SBER", "GAZP"])
        assert isinstance(top, list)
        # sorted desc by yield
        yields = [r.get("yield_pct") or 0 for r in top]
        assert yields == sorted(yields, reverse=True)

    def test_unknown_ticker_returns_none(self):
        q = MoexFeed().get_quote("NONEXISTENT_ZZZ")
        # either None or a valid price — never raises
        assert q is None or q.price > 0
