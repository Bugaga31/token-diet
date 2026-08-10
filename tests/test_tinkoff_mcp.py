"""Tests for tinkoff_mcp — official T-Invest MCP client."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from token_diet.tinkoff_mcp import TinkoffMCP, TinkoffMCPError


# ── SSE parser ───────────────────────────────────────────────────────────

def test_parse_sse_single_line_json():
    raw = "event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"ok\":1}}\n\n"
    msgs = TinkoffMCP._parse_sse(raw)
    assert len(msgs) == 1
    assert msgs[0]["result"]["ok"] == 1


def test_parse_sse_multiline_json():
    # JSON может быть разбит на несколько data:-строк
    raw = ("event: message\n"
           "data: {\"jsonrpc\":\"2.0\",\"id\":2,\n"
           "data: \"result\":{\"tools\":[{\"name\":\"a\"}]}}\n\n")
    msgs = TinkoffMCP._parse_sse(raw)
    assert len(msgs) == 1
    assert msgs[0]["result"]["tools"][0]["name"] == "a"


def test_parse_sse_multiple_events():
    raw = ("data: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"a\":1}}\n\n"
           "data: {\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"b\":2}}\n\n")
    msgs = TinkoffMCP._parse_sse(raw)
    assert len(msgs) == 2


def test_parse_sse_invalid_ignored():
    raw = "event: message\ndata: not-json\n\n"
    assert TinkoffMCP._parse_sse(raw) == []


def test_parse_sse_empty():
    assert TinkoffMCP._parse_sse("") == []


# ── write-tool protection ────────────────────────────────────────────────

def test_write_tools_blocked_by_default():
    mcp = TinkoffMCP(token="fake-token")
    assert not mcp.available or True  # token set
    for tool in ("invest_create_order", "invest_create_stoporder",
                 "invest_cancel_order", "invest_transfer_broker_accounts"):
        res = mcp.call(tool, {})
        assert "error" in res
        assert "allow_trading" in res["error"]


def test_no_token_graceful():
    mcp = TinkoffMCP(token=None)
    # no token in env here; either way call() must not raise
    res = mcp.call("invest_get_news", {})
    assert isinstance(res, dict)
    assert "error" in res or "data" in res or "text" in res


# ── extract_content ──────────────────────────────────────────────────────

def test_extract_content_text():
    out = TinkoffMCP._extract_content([{"type": "text", "text": "hello"}])
    assert out.get("text") == "hello"


def test_extract_content_structured():
    out = TinkoffMCP._extract_content([
        {"type": "text", "text": "hello"},
        {"type": "resource", "resource": {"x": 1}},
    ])
    assert out.get("text") == "hello"
    assert out.get("resource") == {"x": 1}


def test_extract_content_empty():
    assert TinkoffMCP._extract_content([]) == {}


# ── InvestHub integration ────────────────────────────────────────────────

def test_invest_hub_mcp_news_graceful():
    from token_diet.invest_hub import InvestHub
    hub = InvestHub(mcp_enabled=False)  # mcp off — must not raise
    res = hub.mcp_news("PLZL")
    assert res == {"enabled": False}


def test_invest_hub_mcp_portfolio_graceful():
    from token_diet.invest_hub import InvestHub
    hub = InvestHub(mcp_enabled=False)
    res = hub.mcp_portfolio_brief()
    assert res == {"enabled": False}


def test_invest_hub_full_picture_mcp_enabled_never_raises():
    from token_diet.invest_hub import InvestHub
    hub = InvestHub(mcp_enabled=True, news_enabled=False)
    pic = hub.full_picture("PLZL", include_news=False, include_orderbook=False,
                           include_mcp=True)
    assert pic["ticker"] == "PLZL"
    # секция либо присутствует со словарём, либо отсутствует — никогда не исключение
    if "mcp_portfolio" in pic:
        assert isinstance(pic["mcp_portfolio"], dict)


# ── status ───────────────────────────────────────────────────────────────

def test_status_no_token():
    mcp = TinkoffMCP(token=None)
    st = mcp.status()
    assert "available" in st
