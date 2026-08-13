import sys
from dataclasses import dataclass, field

sys.path.insert(0, ".")

from token_diet.pnl_journal import history, record, snapshot


@dataclass
class FakePos:
    ticker: str
    figi: str = ""
    quantity: float = 0.0
    current_price: float = 0.0


class FakeInv:
    def __init__(self, positions):
        self._positions = positions

    def get_portfolio(self):
        return self._positions


def test_snapshot_totals():
    inv = FakeInv([
        FakePos(ticker="GMKN", figi="BBG", quantity=20, current_price=120),
        FakePos(ticker="uid:x", figi="RUB000UTSTOM", quantity=15738, current_price=1),
    ])
    s = snapshot(inv)
    assert s["cash"] == 15738.0
    assert s["total"] == 15738.0 + 20 * 120
    assert s["positions"]["GMKN"]["shares"] == 20


def test_record_and_history(tmp_path):
    p = tmp_path / "j.jsonl"
    inv = FakeInv([
        FakePos(ticker="GMKN", figi="BBG", quantity=20, current_price=100),
        FakePos(ticker="uid:x", figi="RUB000UTSTOM", quantity=1000, current_price=1),
    ])
    record(inv, p)
    # вторая запись — цена выросла
    inv2 = FakeInv([
        FakePos(ticker="GMKN", figi="BBG", quantity=20, current_price=110),
        FakePos(ticker="uid:x", figi="RUB000UTSTOM", quantity=1000, current_price=1),
    ])
    record(inv2, p)

    h = history(p)
    assert h["records"] == 2
    assert h["start"] == 3000.0
    assert h["current"] == 3200.0
    assert h["pnl"] == 200.0


def test_history_recovery_pct(tmp_path):
    p = tmp_path / "j.jsonl"
    inv1 = FakeInv([FakePos(ticker="uid:x", figi="RUB000UTSTOM", quantity=20000, current_price=1)])
    inv2 = FakeInv([FakePos(ticker="uid:x", figi="RUB000UTSTOM", quantity=19000, current_price=1)])
    record(inv1, p)
    record(inv2, p)
    h = history(p, target=20000)
    assert h["to_target"] == 1000.0
    # старт 20000, сейчас 19000 → возврат 0% (мы в минусе)
    assert h["recovery_pct"] == 0.0


def test_history_empty(tmp_path):
    h = history(tmp_path / "none.jsonl")
    assert "error" in h


def test_journal_block_runs():
    from token_diet.pnl_journal import journal_block
    inv = FakeInv([
        FakePos(ticker="GMKN", figi="BBG", quantity=20, current_price=120),
        FakePos(ticker="uid:x", figi="RUB000UTSTOM", quantity=15738, current_price=1),
    ])
    text = journal_block(inv)
    assert "[P&L]" in text
