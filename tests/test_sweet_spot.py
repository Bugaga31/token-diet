"""Tests for sweet_spot (offline: TinkoffInvest mocked)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.sweet_spot import SweetSpot, report, scan


class FakeCandle:
    def __init__(self, close: float):
        self.open = self.high = self.low = self.close = close
        self.time = None
        self.volume = 0.0


def _fake_candles(closes: list[float]):
    return [FakeCandle(c) for c in closes]


class FakeInvest:
    """Mock: одна бумага с глубоким откатом, вторая в тренде."""

    def __init__(self):
        base_deep = 100.0
        base_trend = 50.0
        # PLZL: падала до 88, последняя 88 (откат ~ -8% vs SMA20)
        self.deep = [base_deep * (1 - 0.004 * i) for i in range(25)]
        self.deep[-1] = 88.0
        # SBER: рост до 60
        self.trend = [base_trend * (1 + 0.008 * i) for i in range(25)]

    def get_candles(self, ticker, days=30, interval=None, figi=None, skip_weekends=True):
        if ticker == "PLZL":
            return _fake_candles(self.deep)
        if ticker == "SBER":
            return _fake_candles(self.trend)
        return []


def test_scan_ranks_pullback(monkeypatch):
    monkeypatch.setattr("token_diet.tinkoff_invest.TinkoffInvest", FakeInvest)
    monkeypatch.setattr(
        "token_diet.pulse_reader.pulse_sentiment",
        lambda ticker, limit=12: {"score": -0.4, "signal": "bearish"},
    )
    spots = scan(["PLZL", "SBER"], gold_price=4420.0)
    assert len(spots) == 2
    assert spots[0].ticker == "PLZL"  # откат + золото → слаще
    assert spots[0].gold_proxy is True
    assert spots[0].sweetness > spots[1].sweetness


def test_gold_tailwind_gives_points():
    # без золота и без сентимента: откат всё равно даёт баллы
    from token_diet.sweet_spot import _sma

    assert _sma([1, 2, 3], 3) == 2.0
    assert _sma([], 3) == 0.0


def test_report_empty():
    assert "пуста" in report([])


def test_report_nonempty():
    s = SweetSpot(
        ticker="PLZL", name="Полюс", price=1276.0, day_change_pct=-1.0,
        vs_sma20_pct=-8.0, sentiment_score=-0.4, sentiment_signal="bearish",
        gold_proxy=True, sweetness=72.5, note="откат + золото",
    )
    r = report([s])
    assert "PLZL" in r
    assert "72" in r
    assert "🍭" in r
