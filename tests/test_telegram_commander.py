"""Tests for telegram_commander.py — the bot-commander safety and routing."""

import json
import urllib.request
from pathlib import Path

import token_diet.telegram_commander as tc


class TestTokenSafety:
    def test_no_token_in_module_source(self):
        src = Path(tc.__file__).read_text()
        assert "TELEGRAM_BOT_TOKEN_REVOKED" not in src
        assert "TELEGRAM_BOT_ID_REVOKED" not in src or "OWNER_ID = TELEGRAM_OWNER_ID_FROM_ENV" not in src

    def test_load_token_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setattr(tc, "TOKEN_FILE", Path("/nonexistent/token"))
        assert tc.load_token() == ""

    def test_load_token_from_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        assert tc.load_token() == "123:abc"

    def test_load_token_rejects_paste_placeholder(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "PASTE_TOKEN_HERE")
        monkeypatch.setattr(tc, "TOKEN_FILE", Path("/nonexistent/token"))
        assert tc.load_token() == ""


class TestRouting:
    def test_help_command(self):
        r = tc._handle_command("/help")
        assert "Командир" in r
        assert "/портфель" in r
        assert "Командир" in tc._handle_command("/помощь")
        assert "Командир" in tc._handle_command("/start")

    def test_unknown_short_alpha_is_ticker(self):
        # без токена бота/сети вернёт картину с ошибкой, но не упадёт
        r = tc._handle_command("/sber")
        assert isinstance(r, str)

    def test_ticker_alias(self):
        assert tc._resolve("полюс") == "PLZL"
        assert tc._resolve("/sber") == "SBER"
        assert tc._resolve("газпром") == "GAZP"

    def test_chat_without_key_is_honest(self, monkeypatch):
        monkeypatch.setattr(tc, "load_deepseek_key", lambda: "")
        r = tc._handle_command("привет, как дела?")
        assert "DEEPSEEK_API_KEY" in r

    def test_chat_with_key_calls_deepseek(self, monkeypatch):
        monkeypatch.setattr(tc, "load_deepseek_key", lambda: "sk-test")
        calls = {}

        def fake_urlopen(req, timeout=40):
            calls["url"] = req.full_url
            json.loads(req.data.decode())

            class FakeResp:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return json.dumps({
                        "choices": [{"message": {"content": "Держим Полюс!"}}]
                    }).encode()

            return FakeResp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        r = tc._handle_command("что делаем с полюсом?")
        assert r == "Держим Полюс!"
        assert "api.deepseek.com" in calls["url"]


class TestOwnerWhitelist:
    def test_run_rejects_stranger(self, monkeypatch):
        sent = {}

        def fake_send(chat_id, text):
            sent[chat_id] = text
            return {"ok": True}

        monkeypatch.setattr(tc, "send_message", fake_send)
        monkeypatch.setattr(tc, "load_token", lambda: "tok")
        monkeypatch.setattr(tc, "get_updates", lambda *a, **k: [{
            "update_id": 1,
            "message": {
                "chat": {"id": 999},
                "from": {"id": 999},
                "text": "/портфель",
            },
        }])
        monkeypatch.setattr(tc, "time", type("T", (), {"sleep": staticmethod(lambda s: None)})())
        tc.run(once=True)
        assert sent.get(999) and "владельца" in sent[999]

    def test_run_answers_owner(self, monkeypatch):
        sent = {}

        def fake_send(chat_id, text):
            sent[chat_id] = text
            return {"ok": True}

        monkeypatch.setattr(tc, "send_message", fake_send)
        monkeypatch.setattr(tc, "load_token", lambda: "tok")
        monkeypatch.setattr(tc, "get_updates", lambda *a, **k: [{
            "update_id": 2,
            "message": {
                "chat": {"id": 100},
                "from": {"id": tc.OWNER_ID},
                "text": "/помощь",
            },
        }])
        tc.run(once=True)
        assert sent.get(100) and "Командир" in sent[100]
