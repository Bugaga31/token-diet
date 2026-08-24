"""Тесты: облачный саб-агент, пак утилит, матрица возможностей."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from token_diet.agent_brain import default_tools  # noqa: E402
from token_diet.bootstrap import capability_matrix  # noqa: E402
from token_diet.cloud_brain import CloudBrain, setup_hint  # noqa: E402
from token_diet.plugin_pack import register_pack  # noqa: E402


class TestProviderChain:
    def test_no_keys_not_available(self, monkeypatch):
        for env in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
                    "ANYMODEL_API_KEY"):
            monkeypatch.delenv(env, raising=False)
        assert not CloudBrain().available

    def test_deepseek_wins_when_first(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
        monkeypatch.setenv("GROQ_API_KEY", "gq")
        brain = CloudBrain()
        assert brain.provider_name == "deepseek"

    def test_openrouter_free_model_default(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-x")
        info = CloudBrain().info()
        assert ":free" in info["model"]

    def test_anymodel_custom_gateway(self, monkeypatch):
        monkeypatch.setenv("ANYMODEL_API_KEY", "am")
        monkeypatch.setenv("ANYMODEL_BASE_URL", "http://localhost:20128/v1/")
        brain = CloudBrain()
        assert brain.provider_name == "anymodel"
        assert brain.info()["base_url"] == "http://localhost:20128/v1"


class TestCloudLoop:
    def _brain_with_response(self, responses, monkeypatch):
        """CloudBrain с подменённым HTTP-транспортом."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        cloud = CloudBrain()
        queue = list(responses)
        monkeypatch.setattr(
            cloud, "_chat_completions", lambda messages, tools=None: queue.pop(0),
        )
        return cloud

    def test_tool_call_executed_and_answered(self, monkeypatch):
        cloud = self._brain_with_response([
            {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "count_tokens",
                                          "arguments": '{"text": "привет"}'}}]}}]},
            {"choices": [{"message": {"content": "в тексте N токенов"}}]},
        ], monkeypatch)
        answer = cloud.run("сколько токенов в 'привет'?")
        assert answer == "в тексте N токенов"

    def test_max_steps(self, monkeypatch):
        loop = {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "1", "function": {"name": "count_tokens",
                                     "arguments": "{}"}}]}}]}
        cloud = self._brain_with_response([loop] * 20, monkeypatch)
        assert "лимит шагов" in cloud.run("зациклись")

    def test_no_key_raises_with_hint(self, monkeypatch):
        for env in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
                    "ANYMODEL_API_KEY"):
            monkeypatch.delenv(env, raising=False)
        with pytest.raises(RuntimeError, match="OPENROUTER"):
            CloudBrain().run("привет")


class TestSetupHint:
    def test_mentions_all_options(self):
        hint = setup_hint()
        assert "DEEPSEEK" in hint and "OPENROUTER" in hint and "GROQ" in hint


class TestPluginPack:
    def test_pack_registers(self):
        brain = default_tools()
        before = len(brain.tools)
        register_pack(brain)
        assert len(brain.tools) > before + 15

    @pytest.mark.parametrize("tool,args,check", [
        ("percent_change", {"was": 100, "now": 125}, lambda r: r["change_pct"] == 25.0),
        ("vat_rub", {"amount_with_vat": 120}, lambda r: r["net"] == 100.0
         and r["vat"] == 20.0),
        ("days_between", {"from_date": "2026-08-01", "to_date": "2026-08-24"},
         lambda r: r["days"] == 23),
        ("hash_text", {"text": "abc"}, lambda r: len(r["sha256_32"]) == 32),
        ("slugify", {"text": "Привет, Мир Токенов!"}, lambda r:
         r["slug"] == "privet-mir-tokenov"),
        ("translit_ru", {"text": "Щи да каша"}, lambda r:
         "shch" in r["translit"].lower()),
        ("stats_numbers", {"numbers": [1, 2, 3, 4]}, lambda r:
         r["median"] == 2.5 and r["max"] == 4.0),
        ("flatten_json", {"data": {"a": {"b": 1}}}, lambda r:
         r["flat"] == {"a.b": 1}),
        ("split_bill", {"total": 3000, "people": 3, "tip_pct": 10},
         lambda r: r["per_person"] == 1100.0),
        ("risk_reward", {"entry": 100, "stop": 95, "target": 115},
         lambda r: r["rr"] == 3.0),
        ("compound_interest", {"principal": 1000, "annual_rate_pct": 10,
                               "years": 0, "monthly_add": 100},
         lambda r: r["future_value"] == 1000.0),
    ])
    def test_tools_math(self, tool, args, check):
        brain = default_tools()
        register_pack(brain)
        out = brain.execute(tool, args)
        assert out["ok"], out.get("error")
        assert check(out["result"])

    def test_loan_payment_zero_rate(self):
        brain = default_tools()
        register_pack(brain)
        out = brain.execute("loan_payment", {
            "principal": 120000, "annual_rate_pct": 0, "months": 12})
        assert out["result"]["monthly_payment"] == 10000.0


class TestCapabilityMatrix:
    def test_rows_have_required_fields(self):
        rows = capability_matrix()
        assert rows and all(
            set(r) >= {"capability", "status", "install"} for r in rows
        )

    def test_python_always_ok(self):
        rows = {r["capability"]: r for r in capability_matrix()}
        assert rows["python>=3.10"]["status"] == "OK"
