"""Тесты ollama_bridge: детект хоста, модели, generate/embed, интеграция.

Живой сервер не нужен: транспорт (_post/_probe) подменяется.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet import ollama_bridge as ob  # noqa: E402
from token_diet.ollama_bridge import (  # noqa: E402
    OllamaBridge,
    OllamaModel,
    _l2_normalize,
    _normalize_host,
    neural_status_block,
    ollama_embed_fn,
)


class TestHostNormalize:
    def test_adds_scheme(self):
        assert _normalize_host("127.0.0.1:8080") == "http://127.0.0.1:8080"

    def test_keeps_full_url_and_strips_slash(self):
        assert _normalize_host("http://h:9/") == "http://h:9"


class TestL2:
    def test_normalizes(self):
        v = _l2_normalize([3.0, 4.0])
        assert abs(sum(x * x for x in v) - 1.0) < 1e-12

    def test_zero_vector_safe(self):
        assert _l2_normalize([0.0, 0.0]) == [0.0, 0.0]


def _bridge_with(monkeypatch, models, responses=None, host="http://fake:11434"):
    """Мост с подменённым транспортом."""
    br = OllamaBridge(host=host)
    monkeypatch.setattr(br, "_post", lambda path, payload, timeout=None:
                        (responses or {}).get(path))
    monkeypatch.setattr(br, "list_models", lambda: models)
    return br


class TestDiscovery:
    def test_unavailable_when_no_host(self):
        br = OllamaBridge(host="")
        assert not br.available
        assert br.list_models() == []

    def test_probe_scans_candidates(self, monkeypatch):
        hits = []
        monkeypatch.setattr(ob, "_CANDIDATE_HOSTS",
                            ("http://dead:1", "http://alive:2"))
        monkeypatch.setattr(ob, "_normalize_host", lambda h: h)

        def fake_probe(base):
            hits.append(base)
            return base == "http://alive:2"

        monkeypatch.setattr(ob, "_probe", fake_probe)
        br = OllamaBridge()
        assert br.host == "http://alive:2"
        assert hits == ["http://dead:1", "http://alive:2"]

    def test_no_server_anywhere(self, monkeypatch):
        monkeypatch.setattr(ob, "_CANDIDATE_HOSTS", ("http://dead:1",))
        monkeypatch.setattr(ob, "_probe", lambda base: False)
        assert OllamaBridge().host == ""


class TestModels:
    _MODELS = [
        OllamaModel("qwen3:1.7b", 1_400_000_000),
        OllamaModel("deepseek-r1:7b", 4_700_000_000),
        OllamaModel("nomic-embed-text:latest", 300_000_000),
    ]

    def test_pick_smallest_chat(self, monkeypatch):
        br = _bridge_with(monkeypatch, self._MODELS)
        assert br.pick_model("chat") == "qwen3:1.7b"

    def test_pick_embedding_model(self, monkeypatch):
        br = _bridge_with(monkeypatch, self._MODELS)
        assert br.pick_model("embedding") == "nomic-embed-text:latest"

    def test_empty_registry_returns_none(self, monkeypatch):
        br = _bridge_with(monkeypatch, [])
        assert br.pick_model("chat") is None


class TestGenerateEmbed:
    def test_generate_uses_picked_model(self, monkeypatch):
        seen = {}
        br = _bridge_with(monkeypatch, TestModels._MODELS)
        br._post = lambda path, payload, timeout=None: (
            seen.update(payload=payload, path=path) or {"response": "привет"}
        )
        out = br.generate("вопрос")
        assert out == "привет"
        assert seen["path"] == "generate" and seen["payload"]["stream"] is False

    def test_generate_none_when_down(self):
        br = OllamaBridge(host="")
        assert br.generate("x") is None

    def test_embed_caches_and_normalizes(self, monkeypatch):
        br = _bridge_with(monkeypatch, TestModels._MODELS)
        calls = []
        raw = [3.0, 4.0]

        def fake_post(path, payload, timeout=None):
            calls.append(1)
            return {"embeddings": [raw]}

        monkeypatch.setattr(br, "_post", fake_post)
        v1 = br.embed("текст")
        v2 = br.embed("текст")
        assert len(calls) == 1                      # второй раз из кэша
        assert abs(sum(x * x for x in v1) - 1.0) < 1e-12
        assert v1 == v2


class TestEmbedFnIntegration:
    def test_embed_fn_none_when_offline(self):
        assert ollama_embed_fn(bridge=OllamaBridge(host="")) is None

    def test_embed_fn_compatible_with_embed_cache(self, monkeypatch):
        from token_diet.embed_cache import EmbeddingCache

        br = OllamaBridge(host="http://fake")
        monkeypatch.setattr(
            br, "embed",
            lambda text, model=None: (
                [1.0, 0.0] if "акции" in text else [0.0, 1.0]
            ),
        )
        fn = ollama_embed_fn(bridge=br)
        cache = EmbeddingCache(embed=fn, similarity_threshold=0.5,
                               min_confidence=0.5)
        assert cache.put("Стоит ли покупать акции?", "Да, но это не финсовет")
        hit = cache.lookup("Стоит ли покупать акции?")
        assert hit.action == "EXACT"

        miss = cache.lookup("Погода в Москве сейчас")   # другой смысл + волатильно
        assert miss.action != "EXACT"


class TestStatusBlock:
    def test_offline_message(self, monkeypatch):
        monkeypatch.setattr(ob, "OllamaBridge",
                            lambda **kw: OllamaBridge(host=""))
        assert "не найден" in neural_status_block()

    def test_online_lists_models(self, monkeypatch):
        br = OllamaBridge(host="http://x")
        monkeypatch.setattr(br, "list_models",
                            lambda: [OllamaModel("m:7b", 4_700_000_000)])
        monkeypatch.setattr(br, "pick_model", lambda kind: "m:7b")
        monkeypatch.setattr(ob, "OllamaBridge", lambda **kw: br)
        text = neural_status_block()
        assert "ollama 1 моделей" in text and "m:7b" in text and "бесплатно" in text
