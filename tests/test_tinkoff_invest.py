"""Tests for tinkoff_invest.py — token security + API integration."""

import os
import pytest
from pathlib import Path

from token_diet.tinkoff_invest import (
    KNOWN_FIGI,
    TinkoffInvest,
    TinkoffQuote,
    TinkoffCandle,
    PortfolioPosition,
    get_token,
    resolve_figi,
    save_token,
    status,
    figi_unknown,
)


class TestTokenSecurity:
    def test_get_token_from_env(self, monkeypatch):
        monkeypatch.setenv("TINKOFF_TOKEN", "secret-token-123")
        assert get_token() == "secret-token-123"

    def test_get_token_priority_param(self, monkeypatch):
        monkeypatch.setenv("TINKOFF_TOKEN", "env-token")
        assert get_token("param-token") == "param-token"

    def test_get_token_no_source(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        assert get_token() is None

    def test_save_token_file(self, tmp_path):
        path = tmp_path / "token"
        save_token("my-secret-token", str(path))
        assert path.exists()
        assert path.read_text() == "my-secret-token"
        # Permissions should be 600
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_get_token_from_file(self, tmp_path, monkeypatch):
        path = tmp_path / "token"
        path.write_text("file-token")
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        # Monkeypatch TOKEN_FILE
        import token_diet.tinkoff_invest as ti
        ti.TOKEN_FILE = path
        assert get_token() == "file-token"


class TestTickers:
    def test_known_figi_plzl(self):
        assert KNOWN_FIGI.get("PLZL") is not None

    def test_resolve_figi(self):
        assert resolve_figi("SBER") == KNOWN_FIGI["SBER"]
        assert resolve_figi("plzl") == KNOWN_FIGI["PLZL"]

    def test_resolve_unknown(self):
        assert resolve_figi("UNKNOWN_TICKER") is None

    def test_figi_unknown(self):
        assert figi_unknown("BBG004730N88") == "SBER"
        assert figi_unknown("XXXXXXX") == "UNKNOWN"


class TestClientInit:
    def test_not_available_without_sdk_or_token(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        tink = TinkoffInvest()
        # Even without SDK, class constructs safely
        assert tink is not None

    def test_available_flag_without_token(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        tink = TinkoffInvest()
        assert tink.available is False

    def test_quote_returns_none_without_credentials(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        tink = TinkoffInvest()
        assert tink.get_quote("PLZL") is None

    def test_candles_empty_without_credentials(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        tink = TinkoffInvest()
        assert tink.get_candles("PLZL") == []

    def test_signal_none_without_credentials(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        tink = TinkoffInvest()
        assert tink.get_signal("PLZL") is None


class TestStatus:
    def test_status_report(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        s = status()
        assert "token_found" in s
        assert "known_tickers" in s
        # Must NEVER leak the token value
        assert "secret" not in str(s)
