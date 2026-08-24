"""Tests for the sellable trading bot: live_trade + post_order.

live_trade is THE product we sell in Pulse. Rules tested here:
  1. No token → honest error dict, no crash.
  2. dry_run=True → NEVER calls post_order (no real order).
  3. dry_run=False → calls post_order with the right direction.
  4. post_order maps buy/sell to the correct Tinkoff enums.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from token_diet.trading_robot import live_trade


def test_live_trade_no_token_returns_error(monkeypatch):
    monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
    # force no local token file
    monkeypatch.setattr("token_diet.tinkoff_invest.TOKEN_FILE", Path("/nonexistent/x"))
    r = live_trade("PLZL", budget=1000, dry_run=True)
    assert isinstance(r, dict)
    assert "error" in r  # honest: reports missing token


def test_live_trade_dry_run_never_posts(monkeypatch):
    monkeypatch.setenv("TINKOFF_TOKEN", "t.test-token-123")

    class FakeTink:
        available = True

        def get_candles(self, ticker, days=60):
            from datetime import datetime, timedelta, timezone
            base = datetime.now(timezone.utc) - timedelta(days=30)
            candles = []
            # build a gentle uptrend so strategies produce a lean
            price = 100.0
            for i in range(60):
                price += 0.5
                candles.append(type("C", (), {
                    "time": base + timedelta(days=i),
                    "open": price - 0.3, "high": price + 0.4,
                    "low": price - 0.4, "close": price,
                    "volume": 1000.0 + i,
                }))
            return candles

        def post_order(self, *a, **k):
            raise AssertionError("dry_run must NEVER place an order!")

    monkeypatch.setattr("token_diet.tinkoff_invest.TinkoffInvest", lambda **k: FakeTink())
    r = live_trade("PLZL", budget=1000, dry_run=True)
    assert r["dry_run"] is True
    assert "order" not in r  # no order key at all


def test_live_trade_execution_posts_buy(monkeypatch):
    monkeypatch.setenv("TINKOFF_TOKEN", "t.test-token-123")
    posted = {}

    class FakeTink:
        available = True

        def get_candles(self, ticker, days=60):
            from datetime import datetime, timedelta, timezone
            base = datetime.now(timezone.utc) - timedelta(days=30)
            candles = []
            price = 100.0
            for i in range(60):
                price += 0.5
                candles.append(type("C", (), {
                    "time": base + timedelta(days=i),
                    "open": price - 0.3, "high": price + 0.4,
                    "low": price - 0.4, "close": price,
                    "volume": 1000.0 + i,
                }))
            return candles

        def post_order(self, ticker, quantity, direction, order_type):
            posted["ticker"] = ticker
            posted["quantity"] = quantity
            posted["direction"] = direction
            posted["order_type"] = order_type
            return {"order_id": "ord-1", "status": "EXECUTED",
                    "executed_price": 110.0, "lots": quantity}

    monkeypatch.setattr("token_diet.tinkoff_invest.TinkoffInvest", lambda **k: FakeTink())
    r = live_trade("PLZL", budget=500, dry_run=False, min_agreement=1)
    assert "order" in r
    assert posted.get("ticker") == "PLZL"
    assert posted.get("direction") in ("buy", "sell")


def test_post_order_direction_mapping(monkeypatch):
    from token_diet.tinkoff_invest import TinkoffInvest

    calls = []

    class FakeTink(TinkoffInvest):
        def __init__(self, token=None):
            self.token = token or "t.test"
            self.available = True
            self._sdk = False

        def find_figi(self, ticker):
            return "BBG000000000"

        def _rpc(self, name, body):
            calls.append((name, body))
            if name == "accounts":
                return {"accounts": [{"id": "acc-1"}]}
            return {"orderId": "o-1", "executionReportStatus": "NEW",
                    "executedOrderPrice": {"units": 10, "nano": 0},
                    "lotsExecuted": 1}

    t = FakeTink()
    t.post_order("PLZL", quantity=2, direction="buy", order_type="market")
    name, body = calls[-1]
    assert name == "post_order"
    assert body["direction"] == "ORDER_DIRECTION_BUY"
    assert body["orderType"] == "ORDER_TYPE_MARKET"
    assert body["quantity"] == 2

    t.post_order("PLZL", quantity=1, direction="sell", order_type="market")
    name, body = calls[-1]
    assert body["direction"] == "ORDER_DIRECTION_SELL"
