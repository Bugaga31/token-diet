"""Tests for technical_indicators — Stochastic, OBV, MFI, Williams %R."""
from token_diet.technical_indicators import (
    mfi,
    obv,
    obv_trend,
    snapshot_block,
    stochastic,
    technical_snapshot,
    williams_r,
)

CLOSES = [100, 101, 102, 101, 103, 104, 103, 105, 106, 107,
          108, 107, 109, 110, 111, 112, 111, 113, 114, 115]
HIGHS = [c + 1 for c in CLOSES]
LOWS = [c - 1 for c in CLOSES]
VOLS = [1000] * len(CLOSES)


def test_stochastic_bounds():
    k, d = stochastic(HIGHS, LOWS, CLOSES)
    assert k[-1] is not None and 0 <= k[-1] <= 100
    assert d[-1] is not None


def test_obv_accumulates():
    o = obv([100, 101, 100, 102], [10, 5, 3, 7])
    assert o == [10, 15, 12, 19]


def test_obv_trend():
    assert obv_trend([1, 2, 3, 4, 5]) == "rising"
    assert obv_trend([5, 4, 3, 2, 1]) == "falling"
    assert obv_trend([1, 1, 1]) == "flat"


def test_williams_r_range():
    wr = williams_r(HIGHS, LOWS, CLOSES)
    assert wr[-1] is not None and -100 <= wr[-1] <= 0


def test_mfi_range():
    m = mfi(HIGHS, LOWS, CLOSES, VOLS)
    assert m[-1] is not None and 0 <= m[-1] <= 100


def test_snapshot():
    snap = technical_snapshot(HIGHS, LOWS, CLOSES, VOLS)
    assert "rsi" in snap
    assert "obv_trend" in snap
    assert "atr_pct" in snap


def test_snapshot_block():
    block = snapshot_block(HIGHS, LOWS, CLOSES, VOLS)
    assert block.startswith("[Technical]")
    assert "RSI" in block
