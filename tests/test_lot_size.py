import sys

sys.path.insert(0, ".")

from token_diet.tinkoff_invest import TinkoffInvest


def _inv_with_lot(lot: int) -> TinkoffInvest:
    """TinkoffInvest с подменённым lot_size (без сети)."""
    inv = TinkoffInvest.__new__(TinkoffInvest)
    inv._lot_cache = {"TEST": lot}
    return inv


def test_lot_size_cache():
    inv = _inv_with_lot(10)
    assert inv.lot_size("TEST") == 10


def test_buy_shares_converts_to_lots():
    inv = _inv_with_lot(10)
    # 20 акций при лоте 10 → 2 лота
    calls = []
    inv.post_order = lambda ticker, quantity, direction, order_type="market", **kw: calls.append((ticker, quantity, direction))
    inv.buy_shares("TEST", 20)
    assert calls == [("TEST", 2, "buy")]


def test_sell_shares_rounds_down():
    inv = _inv_with_lot(10)
    calls = []
    inv.post_order = lambda ticker, quantity, direction, order_type="market", **kw: calls.append((ticker, quantity, direction))
    # 25 акций → 2 лота (округление вниз)
    inv.sell_shares("TEST", 25)
    assert calls == [("TEST", 2, "sell")]


def test_buy_shares_too_small_returns_error():
    inv = _inv_with_lot(10)
    r = inv.buy_shares("TEST", 5)  # меньше 1 лота
    assert r is not None and "error" in r


def test_buy_shares_exact_lot():
    inv = _inv_with_lot(10)
    calls = []
    inv.post_order = lambda ticker, quantity, direction, order_type="market", **kw: calls.append((ticker, quantity, direction))
    inv.buy_shares("TEST", 10)
    assert calls == [("TEST", 1, "buy")]
