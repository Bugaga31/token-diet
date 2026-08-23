"""Tests for OmniRoute integration (model_army.ask_omni + SSE parsing)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.model_army import (  # noqa: E402
    CLAUDE_OPUS_5,
    OMNI_CHAT_URL,
    OMNI_ROLES,
    _parse_sse,
)


class TestSSEParse:
    def test_simple_sse(self):
        text = 'data: {"choices":[{"delta":{"content":"При"}}]}\n\n' \
               'data: {"choices":[{"delta":{"content":"вет"}}]}\n\n' \
               'data: {"choices":[{"delta":{}},"finish_reason":"stop"]}\n\n' \
               'data: [DONE]\n\n'
        assert _parse_sse(text) == "Привет"

    def test_sse_done_stops(self):
        text = 'data: {"choices":[{"delta":{"content":"а"}}]}\n\n' \
               'data: [DONE]\n\n' \
               'data: {"choices":[{"delta":{"content":"б"}}]}\n\n'
        assert _parse_sse(text) == "а"

    def test_non_sse_json(self):
        text = '{"choices":[{"message":{"content":"готовый ответ"}}]}'
        assert _parse_sse(text) == "готовый ответ"

    def test_empty_choices_returns_empty(self):
        assert _parse_sse('data: {"choices":[]}\n\n') == ""

    def test_plain_text(self):
        assert _parse_sse("просто текст") == "просто текст"


class TestOmniConfig:
    def test_roles_have_routes(self):
        for role in ("brain", "coding", "fast", "claude", "gemini"):
            assert role in OMNI_ROLES, f"нет маршрута для роли {role}"
            assert OMNI_ROLES[role].startswith("auto/")

    def test_chat_url(self):
        assert OMNI_CHAT_URL.endswith("/chat/completions")

    def test_exported(self):
        import token_diet as td
        assert hasattr(td, "ask_omni")
        assert callable(td.ask_omni)

    def test_claude_opus_5_route(self):
        # прямой маршрут к Opus 5 — проверен живьём через agentrouter
        assert CLAUDE_OPUS_5 == "agentrouter/claude-opus-5-high"
        assert "claude-opus-5" in CLAUDE_OPUS_5
