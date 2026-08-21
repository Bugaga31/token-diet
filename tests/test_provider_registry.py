"""Tests for provider_registry — auto-discovery of LLM providers from env."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.provider_registry import (  # noqa: E402
    available_providers,
    detect_providers,
)


class TestDiscovery:
    def test_finds_nvidia(self):
        prov = detect_providers()
        assert "nvidia" in prov
        assert prov["nvidia"]["key"].startswith("nvapi-")

    def test_finds_gemini(self):
        prov = detect_providers()
        assert "gemini" in prov
        assert prov["gemini"]["key"]

    def test_nvidia_variants_have_default_models(self):
        prov = detect_providers()
        for name in ("nvidia-ultra", "nvidia-llama", "nvidia-gpt-oss"):
            assert name in prov, f"{name} не обнаружен"
            assert "default_model" in prov[name]

    def test_available_sorted(self):
        lst = available_providers()
        assert lst == sorted(lst)
        assert lst  # хотя бы один есть

    def test_no_keys_in_code(self):
        # ни один РЕАЛЬНЫЙ ключ не должен быть захардкожен в модуле
        # (префиксы-детекторы nvapi-/ov_sk_live_ — это паттерны, не секреты)
        import token_diet.provider_registry as pr
        import inspect

        src = inspect.getsource(pr)
        for secret in ("sk-WYC4xiKpu", "ov_sk_live_37t", "nvapi-QjLi"):
            assert secret not in src, f"секрет {secret[:12]}... в коде!"
        # а вот полные значения из env не должны светиться
        import os

        for k in ("NVIDIA_API_KEY", "OPENVECTA_API_KEY"):
            v = os.environ.get(k, "")
            if v and len(v) > 15:
                assert v not in src, f"env-ключ {k} захардкожен в коде!"
