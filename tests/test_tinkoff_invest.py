"""Tests for tinkoff_invest.py — token security + API integration."""

from pathlib import Path

from token_diet.tinkoff_invest import (
    KNOWN_FIGI,
    TinkoffInvest,
    figi_unknown,
    get_token,
    resolve_figi,
    save_token,
    status,
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
        import token_diet.tinkoff_invest as ti
        monkeypatch.setattr(ti, "TOKEN_FILE", Path("/nonexistent/token-file"))
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
        assert figi_unknown("BBG004S681M2") == "SNGSP"  # Сургут-п (в портфеле)
        assert figi_unknown("XXXXXXX") == "UNKNOWN"


class TestClientInit:
    def test_not_available_without_sdk_or_token(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        tink = TinkoffInvest()
        # Even without SDK, class constructs safely
        assert tink is not None

    def test_available_flag_without_token(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        import token_diet.tinkoff_invest as ti
        monkeypatch.setattr(ti, "TOKEN_FILE", Path("/nonexistent/token-file"))
        tink = TinkoffInvest()
        assert tink.available is False

    def test_quote_returns_none_without_credentials(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        import token_diet.tinkoff_invest as ti
        monkeypatch.setattr(ti, "TOKEN_FILE", Path("/nonexistent/token-file"))
        tink = TinkoffInvest()
        assert tink.get_quote("PLZL") is None

    def test_candles_empty_without_credentials(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        import token_diet.tinkoff_invest as ti
        monkeypatch.setattr(ti, "TOKEN_FILE", Path("/nonexistent/token-file"))
        tink = TinkoffInvest()
        assert tink.get_candles("PLZL") == []

    def test_signal_none_without_credentials(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        import token_diet.tinkoff_invest as ti
        monkeypatch.setattr(ti, "TOKEN_FILE", Path("/nonexistent/token-file"))
        tink = TinkoffInvest()
        assert tink.get_signal("PLZL") is None


class TestStatus:
    def test_status_report(self, monkeypatch):
        monkeypatch.delenv("TINKOFF_TOKEN", raising=False)
        import token_diet.tinkoff_invest as ti
        monkeypatch.setattr(ti, "TOKEN_FILE", Path("/nonexistent/token-file"))
        s = status()
        assert "token_found" in s
        assert "known_tickers" in s
        # Must NEVER leak the token value
        assert "secret" not in str(s)


class TestStopOrderConfirmMargin:
    """УРОК 16.08: без confirmMarginTrade API отвечает 30240."""

    def test_post_stop_order_sends_confirm_margin(self, monkeypatch):
        import token_diet.tinkoff_invest as ti
        captured = {}

        def fake_rpc(self, name, body):
            captured["body"] = body
            return {"stopOrderId": "test-id", "status": "ok"}

        monkeypatch.setattr(ti.TinkoffInvest, "_rpc", fake_rpc)
        tink = TinkoffInvest(token="fake-token")
        monkeypatch.setattr(tink, "available", True)
        monkeypatch.setattr(tink, "find_figi", lambda ticker: "BBG004S681M2")
        monkeypatch.setattr(tink, "_account_id", lambda: "acc-1")
        r = tink.post_stop_order("SNGSP", quantity=200, stop_price=39.5,
                                 direction="sell", figi="BBG004S681M2")
        assert r == {"stop_order_id": "test-id", "status": "ok"}
        assert captured["body"].get("confirmMarginTrade") is True
        assert captured["body"].get("stop_price") == {"units": 39, "nano": 500000000}
        assert captured["body"].get("quantity") == 200
