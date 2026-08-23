import sys
from dataclasses import dataclass

sys.path.insert(0, ".")

from token_diet.status import full_status, render_status


@dataclass
class FakePos:
    ticker: str
    figi: str = ""
    quantity: float = 0.0
    current_price: float = 0.0


class FakeInv:
    def __init__(self):
        self._p = [
            FakePos(ticker="GMKN", figi="BBG", quantity=20, current_price=120),
            FakePos(ticker="uid:x", figi="RUB000UTSTOM", quantity=15738, current_price=1),
        ]

    def get_portfolio(self):
        return self._p

    def full_signal(self, ticker):
        return {
            "ticker": ticker, "price": 120.0, "lean": 0.3,
            "verdict": "BUY", "technical": "BUY", "sentiment": "bullish",
            "sentiment_score": 0.5, "sentiment_texts": 3,
        }

    def lot_size(self, ticker):
        return 10


def test_full_status_structure(tmp_path):
    st = full_status(FakeInv(), ["GMKN"], target=20100, journal_path=tmp_path / "j.jsonl")
    assert st["portfolio"]["total"] == 15738 + 20 * 120
    assert st["decision"].action == "buy"
    assert st["decision"].ticker == "GMKN"
    assert len(st["top_signals"]) >= 1


def test_render_status_contains_key_parts(tmp_path):
    st = full_status(FakeInv(), ["GMKN"], target=20100, journal_path=tmp_path / "j.jsonl")
    text = render_status(st)
    assert "СТАТУС" in text
    assert "GMKN" in text
    assert "Решение: BUY" in text
    assert "Тревога" in text
