"""Tests for agent_context, dynamic_ratio, and competitor_benchmark."""

import json
import pytest

from token_diet.agent_context import (
    AgentContextResult,
    CompressedTurn,
    TurnKind,
    _compress_code_turn,
    _compress_prose_light,
    _compress_tool_call,
    _compress_tool_result,
    classify_turn,
    compress_agent_context,
    render_compressed_context,
)
from token_diet.competitor_benchmark import (
    TEST_PROMPTS,
    BenchmarkEntry,
    CompetitorBenchmark,
)
from token_diet.dynamic_ratio import (
    ContentSegment,
    DEFAULT_RATIOS,
    DynamicRatioResult,
    _detect_content_type,
    classify_and_compress,
    dynamic_pipeline,
)


# ── Agent Context Manager tests ──────────────────────────────────────────────


class TestClassifyTurn:
    def test_user_turn(self):
        assert classify_turn("user", "What is the weather?") == TurnKind.USER

    def test_tool_call_turn(self):
        text = "<tool_call>{\"name\":\"get_weather\"}</tool_call>"
        assert classify_turn("assistant", text) == TurnKind.ASSISTANT_TOOL_CALL

    def test_tool_result_turn(self):
        text = "<tool_result>{\"temp\": 18}</tool_result>"
        assert classify_turn("tool", text) == TurnKind.TOOL_RESULT

    def test_error_turn(self):
        text = "Traceback (most recent call last): Error: something went wrong"
        assert classify_turn("assistant", text) == TurnKind.ERROR

    def test_code_turn(self):
        text = "Here's the fix:\n```python\ndef foo(): pass\n```"
        assert classify_turn("assistant", text) == TurnKind.ASSISTANT_CODE

    def test_plain_turn(self):
        text = "That's a great question. Let me explain."
        assert classify_turn("assistant", text) == TurnKind.ASSISTANT_PLAIN


class TestCompressToolCall:
    def test_preserves_function_name(self):
        text = (
            "I'll check the weather.\n"
            '<tool_call>{"name":"get_weather","args":{"city":"London"}}</tool_call>'
        )
        result = _compress_tool_call(text)
        assert "get_weather" in result
        assert "London" in result


class TestCompressToolResult:
    def test_long_result_summarized(self):
        # Build text > 300 chars to trigger summarization
        long_data = json.dumps([{"day": "Mon"}] * 30)
        text = (
            '{"temperature": 18, "condition": "cloudy", "humidity": 72, '
            '"wind_speed": 12, "forecast": ' + long_data + "}"
        )
        assert len(text) > 300, f"test text too short: {len(text)}"
        result = _compress_tool_result(text)
        assert len(result) < len(text) * 0.95  # at least some compression

    def test_short_result_verbatim(self):
        text = '{"temp": 18}'
        result = _compress_tool_result(text)
        assert result == text


class TestCompressCodeTurn:
    def test_preserves_code_block(self):
        text = "Here's the code:\n```python\ndef foo():\n    return 42\n```\nThis is great code."
        result = _compress_code_turn(text)
        assert "```python" in result
        assert "def foo():" in result
        assert "return 42" in result

    def test_plain_text_compressed(self):
        text = "Sure! I'll go ahead and write that function for you. Let me start now. " * 3
        result = _compress_code_turn(text)
        # At minimum, result is not larger
        assert len(result) <= len(text)


class TestCompressProseLight:
    def test_removes_filler(self):
        # Need > 80 chars to trigger compression
        text = "Sure! I'll go ahead and do that for you. Let me start working on it right away."
        result = _compress_prose_light(text)
        assert len(result) <= len(text)

    def test_short_text_verbatim(self):
        text = "OK"
        result = _compress_prose_light(text)
        assert result == text


class TestCompressAgentContext:
    def test_small_context_unchanged(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = compress_agent_context(msgs)
        assert result.savings_pct == 0.0
        assert result.turns_compressed == 0

    def test_user_never_compressed(self):
        msgs = [
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "Paris."},
            {"role": "user", "content": "And of Germany?"},
            {"role": "assistant", "content": "Berlin."},
            {"role": "user", "content": "Great, thanks!"},
        ]
        result = compress_agent_context(msgs)
        for turn in result.turns:
            if turn.role == "user":
                assert turn.original_tokens == turn.compressed_tokens

    def test_agent_with_tools_compressed(self):
        msgs = [
            {"role": "user", "content": "Find weather in London"},
            {
                "role": "assistant",
                "content": '<tool_call>{"name":"get_weather","args":{"city":"London"}}</tool_call>',
            },
            {
                "role": "tool",
                "content": '{"temp": 18, "condition": "cloudy", "humidity": 72}',
            },
            {"role": "assistant", "content": "The weather in London is cloudy, 18°C."},
            {"role": "user", "content": "Should I bring an umbrella?"},
            # Add extra turns to trigger compression:
            {"role": "assistant", "content": "Based on the weather data, you should bring an umbrella because it might rain."},
            {"role": "user", "content": "Thanks! What about tomorrow?"},
            {"role": "assistant", "content": "Tomorrow looks sunny with a high of 22°C."},
        ]
        result = compress_agent_context(msgs)
        assert result.total_after <= result.total_before

    def test_render_compressed_context(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello! How can I help?"},
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
            {"role": "user", "content": "Version?"},
        ]
        result = compress_agent_context(msgs)
        rendered = render_compressed_context(result)
        assert len(rendered) <= len(msgs)
        assert all("role" in m and "content" in m for m in rendered)

    def test_agent_code_with_history(self):
        msgs = [
            {"role": "user", "content": "Add error handling to login"},
            {
                "role": "assistant",
                "content": "Here's the code:\n```python\ndef login():\n    try:\n        ...\n    except:\n        raise\n```",
            },
            {"role": "user", "content": "Now add rate limiting"},
        ]
        result = compress_agent_context(msgs)
        # Code must be preserved
        combined = " ".join(t.content for t in result.turns)
        assert "def login" in combined
        assert "try:" in combined


# ── Dynamic Ratio tests ──────────────────────────────────────────────────────


class TestDetectContentType:
    def test_user_question(self):
        # Plain text without markers: falls back to prose
        assert _detect_content_type("What is the capital of France?") == "prose"
        # With role=user: detected as user
        assert _detect_content_type("Q: What is the answer?", "user") == "user"
        # With explicit marker:
        assert _detect_content_type("User: What is the answer?") == "user"

    def test_code_block(self):
        text = "Here:\n```python\ndef foo(): pass\n```"
        assert _detect_content_type(text) == "code"

    def test_tool_schema(self):
        text = '{"tools": [{"name": "search", "parameters": {"query": "string"}}]}'
        assert _detect_content_type(text) == "tool"

    def test_error(self):
        text = "Traceback (most recent call last):\n  Error: something went wrong"
        assert _detect_content_type(text) == "error"

    def test_retrieval(self):
        text = "Retrieved context:\nDocument 1: The sky is blue."
        assert _detect_content_type(text) == "retrieval"

    def test_system(self):
        text = "System: You are a helpful assistant."
        assert _detect_content_type(text) == "system"

    def test_prose_fallback(self):
        text = "The weather is nice today. Let's go for a walk."
        assert _detect_content_type(text) == "prose"


class TestClassifyAndCompress:
    def test_user_verbatim(self):
        # Short: returns "short" type, verbatim
        text = "What is the capital of France?"
        result, before, after, ctype = classify_and_compress(text, role="user")
        assert before == after  # verbatim for short
        assert ctype in ("short", "user")

    def test_code_verbatim(self):
        # Longer code block to pass the 50-char threshold
        text = "```python\ndef foo():\n    return 42\n    return 43\n    return 44\n```"
        result, before, after, ctype = classify_and_compress(text)
        assert before == after, f"code modified: {before} -> {after}"
        assert ctype == "code"

    def test_prose_compressed(self):
        text = (
            "Furthermore, it is important to note that the policy applies to all. "
            "Additionally, the compliance team has identified several issues."
        )
        result, before, after, ctype = classify_and_compress(text)
        assert after <= before
        assert ctype == "prose"

    def test_short_text_verbatim(self):
        text = "OK"
        result, before, after, ctype = classify_and_compress(text)
        assert before == after


class TestDynamicPipeline:
    def test_pipeline_all_sections(self):
        sections = {
            "question": "What is the capital of France?",
            "system_prompt": "You are a helpful assistant. Be polite. Be concise.",
            "retrieved_context": "Document 1: Paris is the capital of France. "
            "Document 2: Paris is the capital of France with population 2.1M.",
        }
        result = dynamic_pipeline(sections)
        assert result.total_after <= result.total_before
        assert isinstance(result.savings_pct, float)
        assert len(result.segments) == 3

    def test_distribution_tracks_types(self):
        sections = {
            "user": "What is Python?",
            "system": "You are a coding helper. Be helpful.",
        }
        result = dynamic_pipeline(sections)
        # With role detection, should classify these
        assert len(result.content_distribution) > 0


# ── Competitor Benchmark tests ────────────────────────────────────────────────


class TestBenchmark:
    def test_all_prompts_have_required_fields(self):
        for name, prompt in TEST_PROMPTS.items():
            assert "text" in prompt, f"{name} missing text"
            assert "type" in prompt, f"{name} missing type"

    def test_benchmark_runs(self):
        benchmark = CompetitorBenchmark()
        entries = benchmark.run()
        assert len(entries) > 0
        assert benchmark.total_before > 0

    def test_token_diet_always_saves(self):
        benchmark = CompetitorBenchmark()
        benchmark.run()
        # token-diet should save vs raw
        assert benchmark.total_after <= benchmark.total_before

    def test_report_generates(self):
        benchmark = CompetitorBenchmark()
        benchmark.run()
        report = benchmark.report()
        assert "token-diet" in report
        assert "WINNER" in report
        assert "COST" in report
        assert "ENVIRONMENT" in report

    def test_json_report_generates(self):
        benchmark = CompetitorBenchmark()
        benchmark.run()
        report = benchmark.json_report()
        data = json.loads(report)
        assert "summary" in data
        assert "tests" in data
        assert "winner" in data["summary"]
        assert data["summary"]["total_before"] > 0

    def test_entry_savings_positive(self):
        benchmark = CompetitorBenchmark()
        entries = benchmark.run()
        td_entries = [e for e in entries if e.method == "token-diet"]
        for entry in td_entries:
            assert entry.savings_pct >= 0, f"{entry.name}: {entry.savings_pct}"
