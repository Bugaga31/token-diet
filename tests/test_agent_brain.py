"""Тесты agent_brain: реестр, исполнение, цикл tool-calling, дефолтные инструменты."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.agent_brain import (  # noqa: E402
    AgentBrain,
    ToolSpec,
    _jsonable,
    default_tools,
)
from token_diet.ollama_bridge import OllamaBridge  # noqa: E402


def _mini_brain(**kw) -> AgentBrain:
    brain = AgentBrain(**kw)
    brain.register(ToolSpec(
        name="add", description="сложить",
        parameters={"type": "object",
                    "properties": {"a": {"type": "number"},
                                   "b": {"type": "number"}}},
        handler=lambda a, b: {"sum": a + b},
    ))
    return brain


class TestRegistry:
    def test_register_and_schemas(self):
        brain = _mini_brain()
        assert list(brain.tools) == ["add"]
        schema = brain.openai_tools()[0]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "add"

    def test_unregister(self):
        brain = _mini_brain()
        brain.unregister("add")
        assert brain.openai_tools() == []

    def test_danger_filter(self):
        brain = AgentBrain()
        brain.register(ToolSpec("safe", "", {}, lambda: 1))
        brain.register(ToolSpec("netty", "", {}, lambda: 1, danger="net"))
        names = [t["function"]["name"]
                 for t in brain.openai_tools(include_danger="net")]
        assert names == ["netty"]


class TestExecute:
    def test_happy_path_jsonable(self):
        out = _mini_brain().execute("add", {"a": 2, "b": 3})
        assert out["ok"] and out["result"] == {"sum": 5}
        assert "elapsed_s" in out

    def test_string_arguments_parsed(self):
        out = _mini_brain().execute("add", '{"a": 1, "b": 1}')
        assert out["ok"] and out["result"]["sum"] == 2

    def test_unknown_tool_rejected(self):
        out = _mini_brain().execute("nuke", {})
        assert not out["ok"] and "нет инструмента" in out["error"]

    def test_bad_json_arguments_rejected(self):
        out = _mini_brain().execute("add", "{broken")
        assert not out["ok"]

    def test_non_dict_arguments_rejected(self):
        out = _mini_brain().execute("add", "[1, 2]")
        assert not out["ok"]

    def test_handler_exception_caught(self):
        brain = AgentBrain()
        brain.register(ToolSpec("boom", "", {},
                                lambda: (_ for _ in ()).throw(ValueError("x"))))
        out = brain.execute("boom", {})
        assert not out["ok"] and "ValueError" in out["error"]

    def test_audit_written(self, tmp_path):
        path = tmp_path / "a.jsonl"
        brain = _mini_brain(audit_path=str(path))
        brain.execute("add", {"a": 1, "b": 1})
        rec = json.loads(path.read_text(encoding="utf-8").strip())
        assert rec["tool"] == "add" and rec["ok"] is True


class TestJsonable:
    def test_dataclass_to_dict(self):
        from dataclasses import dataclass

        @dataclass
        class P:
            x: int
            y: str

        assert _jsonable(P(1, "a")) == {"x": 1, "y": "a"}

    def test_nested(self):
        assert _jsonable({"a": (1, 2)}) == {"a": [1, 2]}


class TestRunLocalLoop:
    def _scripted_bridge(self, script):
        """Мост, возвращающий заготовленные ответы /api/chat."""
        br = OllamaBridge(host="http://fake")
        queue = list(script)

        def fake_chat(messages, model=None, tools=None, keep_alive=None):
            return queue.pop(0)

        br.chat = fake_chat
        return br

    def test_direct_answer_no_tools(self):
        brain = _mini_brain(
            bridge=self._scripted_bridge(
                [{"message": {"content": "ответ без инструментов"}}],
            ),
        )
        assert brain.run_local("привет") == "ответ без инструментов"

    def test_tool_call_then_answer(self):
        br = self._scripted_bridge([
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "add",
                              "arguments": '{"a": 2, "b": 40}'}}]}},
            {"message": {"content": "сумма 42"}},
        ])
        brain = _mini_brain(bridge=br)
        assert brain.run_local("сколько 2+40") == "сумма 42"

    def test_max_steps_guard(self):
        call = {"message": {"content": "", "tool_calls": [
            {"function": {"name": "add", "arguments": "{}"}}]}}
        brain = _mini_brain(bridge=self._scripted_bridge([call] * 10),
                            max_steps=3)
        assert brain.run_local("x") == "лимит шагов исчерпан — задача слишком большая"

    def test_offline_returns_none(self):
        brain = _mini_brain(bridge=OllamaBridge(host=""))
        assert brain.run_local("x") is None


class TestDefaultTools:
    def test_registry_populated_and_executes(self):
        brain = default_tools()
        names = set(brain.tools)
        assert {"count_tokens", "compress_text", "forecast_spend",
                "rebalance_plan", "stop_bracket", "neural_status"} <= names

    def test_count_tokens_live(self):
        brain = default_tools()
        out = brain.execute("count_tokens", {"text": "привет мир как дела"})
        assert out["ok"] and out["result"]["tokens"] > 0

    def test_stop_bracket_math(self):
        brain = default_tools()
        out = brain.execute("stop_bracket", {
            "ticker": "GAZP", "entry": 130.0, "atr_value": 4.0,
        })
        assert out["ok"]
        (plan,) = out["result"]
        assert plan["stop_loss"] == 120.0 and plan["take_profit"] == 150.0

    def test_prompt_block_compact(self):
        block = default_tools().prompt_block()
        assert "count_tokens(text)" in block
        assert len(block.splitlines()) > 10
