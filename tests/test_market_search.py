"""Tests for market_search — classification logic (offline)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.market_search import classify


def test_classify_isin():
    assert classify("RU000A10BZJ4")["type"] == "bond_isin"
    assert classify("ru000a10bzj4")["type"] == "bond_isin"  # lowercase


def test_classify_ticker():
    assert classify("GMKN")["type"] == "ticker"
    assert classify("SBER")["type"] == "ticker"
    assert classify("YDEX")["type"] == "ticker"


def test_classify_name():
    assert classify("Балтийский лизинг")["type"] == "name"
    assert classify("Норникель")["type"] == "name"
    assert classify("")["type"] == "name"


def test_classify_rejects_fake_isin():
    # слишком короткий / с цифрой в начале буквенной части
    assert classify("RU123")["type"] != "bond_isin"
