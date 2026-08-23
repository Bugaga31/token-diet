"""Tests for InvestHub — the unified investment hub."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.invest_hub import InvestHub, _quotation_to_float


# ── helpers ──────────────────────────────────────────────────────────────

def _hub(news_enabled=False):
    """Hub with Telegram news disabled so tests don't depend on network/session."""
    return InvestHub(news_enabled=news_enabled)


# ── quotation helper ─────────────────────────────────────────────────────

def test_quotation_to_float():
    assert _quotation_to_float({"units": "1335", "nano": 200000000}) == 1335.2
    assert _quotation_to_float({"units": 1335, "nano": 0}) == 1335.0
    assert _quotation_to_float(None) == 0.0
    assert _quotation_to_float({}) == 0.0
    assert _quotation_to_float("junk") == 0.0


# ── status ───────────────────────────────────────────────────────────────

def test_status_shape():
    s = _hub().status()
    assert "tinkoff_token" in s
    assert "sources" in s
    assert "tinkoff" in s["sources"]
    assert "moex" in s["sources"]
    assert s["sources"]["moex"] == "free"


# ── quote ────────────────────────────────────────────────────────────────

def test_quote_shape():
    q = _hub().quote("PLZL")
    assert q["ticker"] == "PLZL"
    # MOEX is free and always attempted; at minimum one source should exist
    assert q["tinkoff"] is not None or q["moex"] is not None
    assert "price" in q


def test_quote_unknown_ticker_does_not_raise():
    q = _hub().quote("ZZZZ_NOT_A_TICKER")
    # must not raise; price may be None or 0
    assert q["ticker"] == "ZZZZ_NOT_A_TICKER"


# ── candles ──────────────────────────────────────────────────────────────

def test_candles_shape():
    c = _hub().candles("PLZL", days=30)
    # may be empty offline, but if present must have expected keys
    if c:
        first = c[0]
        for key in ("time", "open", "high", "low", "close", "volume"):
            assert key in first


# ── technical ────────────────────────────────────────────────────────────

def test_technical_too_few_data():
    t = _hub().technical("PLZL", days=5)
    assert "error" in t  # honest: not enough data, no fake signals


def test_technical_shape_with_data():
    t = _hub().technical("PLZL", days=120)
    if "error" in t:
        return  # offline — skip
    assert t["ticker"] == "PLZL"
    assert "verdict" in t
    assert "confidence" in t
    assert "signals" in t


# ── robot ────────────────────────────────────────────────────────────────

def test_robot_shape_with_data():
    r = _hub().robot("PLZL", days=120)
    if "error" in r:
        return  # offline — skip
    assert "strategies" in r
    assert "combined_lean" in r["strategies"]


# ── orderbook ────────────────────────────────────────────────────────────

def test_orderbook_no_token_graceful():
    hub = _hub()
    # simulate no token
    hub.tinkoff.available = False
    book = hub.orderbook("PLZL")
    assert "error" in book


def test_orderbook_shape_with_token():
    book = _hub().orderbook("PLZL", depth=5)
    if "error" in book:
        return  # offline/no token — skip
    assert "asks" in book and "bids" in book
    assert "pressure_ask_over_bid" in book
    assert "interpretation" in book
    assert book["interpretation"] in ("bullish", "bearish", "neutral")


# ── portfolio ────────────────────────────────────────────────────────────

def test_portfolio_graceful_without_token():
    hub = _hub()
    hub.tinkoff.available = False
    p = hub.portfolio()
    assert "error" in p


def test_portfolio_shape_with_token():
    p = _hub().portfolio()
    if "error" in p:
        return  # offline — skip
    assert "positions" in p
    assert "total_profit_rub" in p
    assert "count" in p


# ── fundamentals ─────────────────────────────────────────────────────────

def test_fundamentals_shape():
    f = _hub().fundamentals("PLZL")
    assert f["ticker"] == "PLZL"
    assert "yield" in f
    assert "history" in f
    assert "next" in f


# ── verdict ──────────────────────────────────────────────────────────────

def test_verdict_graceful():
    v = _hub().verdict("PLZL")
    if "error" in v:
        return  # offline — skip
    assert "action" in v
    assert v["action"] in ("buy", "avoid", "neutral", "no_data")
    assert "confidence" in v


# ── full_picture ─────────────────────────────────────────────────────────

def test_full_picture_never_raises():
    pic = _hub().full_picture("PLZL", include_news=False, include_orderbook=False)
    assert pic["ticker"] == "PLZL"
    assert "quote" in pic
    assert "date" in pic
    assert "verdict" in pic
    assert "generated_at" in pic
    # every section is a dict or absent — never an exception
    assert isinstance(pic["date"], dict)


def test_full_picture_with_news_disabled():
    hub = InvestHub(news_enabled=False)
    pic = hub.full_picture("PLZL", include_news=True)
    assert "news" not in pic or pic.get("news", {}).get("enabled") is False or \
           not pic.get("news", {}).get("messages")


# ── render ───────────────────────────────────────────────────────────────

def test_render_never_raises():
    pic = _hub().full_picture("PLZL", include_news=False, include_orderbook=False)
    out = InvestHub.render(pic)
    assert isinstance(out, str)
    assert "PLZL" in out


def test_render_handles_empty():
    out = InvestHub.render({})
    assert isinstance(out, str)


# ── watchlist ────────────────────────────────────────────────────────────

def test_watchlist_shape():
    wl = _hub().watchlist(["PLZL", "SBER"])
    assert len(wl) == 2
    for row in wl:
        assert "ticker" in row
        assert "quote" in row
        assert "signal" in row
        assert "verdict" in row
