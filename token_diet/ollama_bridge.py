"""Ollama Bridge — бесплатные локальные нейросети для token-diet.

На машине уже стоят Ollama-модели (qwen3, deepseek-r1, nomic-embed-text).
Это нулевая стоимость за токены, ноль API-ключей, ноль сети наружу.
Мост даёт три вещи:

1. ``OllamaBridge`` — авто-детект сервера (OLLAMA_HOST → 11434 → 8080),
   список моделей, generate/chat, embeddings.
2. ``ollama_embed_fn`` — эмбеддер-функция, совместимая с
   ``embed_cache.EmbeddingCache(embed=...)``: L2-нормализация на месте,
   кэш в памяти повторных вызовов.
3. ``neural_status_block`` — что доступно локально прямо сейчас.

Деградация тихая: сервер не поднялся — функции возвращают None/[] и не
роняют вызывающий код. Никакой магии: чистый urllib + JSON.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

_DEFAULT_TIMEOUT = 120.0
_CANDIDATE_HOSTS = (
    os.environ.get("OLLAMA_HOST"),
    "http://127.0.0.1:11434",
    "http://127.0.0.1:8080",
)


@dataclass
class OllamaModel:
    """Модель в локальном реестре Ollama."""

    name: str
    size_bytes: int = 0

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / 1e9, 1)

    @property
    def is_embedding(self) -> bool:
        return "embed" in self.name.lower()


@dataclass
class OllamaBridge:
    """Мост к локальному серверу Ollama."""

    host: str | None = None
    timeout: float = _DEFAULT_TIMEOUT
    _embed_cache: dict[str, list[float]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.host is None:
            for candidate in _CANDIDATE_HOSTS:
                if not candidate:
                    continue
                base = _normalize_host(candidate)
                if _probe(base):
                    self.host = base
                    return
            self.host = ""

    # ── транспорт ───────────────────────────────────────────────

    def _post(self, path: str, payload: dict[str, Any],
              timeout: float | None = None,
              method: str = "POST") -> dict[str, Any] | None:
        """HTTP-запрос к /api/<path>; None если сервер недоступен.

        tags/version — GET, generate/embed/chat — POST.
        """
        if not self.host:
            return None
        kwargs: dict[str, Any] = {
            "headers": {"Content-Type": "application/json"},
        }
        if method == "POST":
            kwargs["data"] = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.host}/api/{path.lstrip('/')}", **kwargs,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None

    # ── публичный API ───────────────────────────────────────────

    @property
    def available(self) -> bool:
        return bool(self.host)

    def list_models(self) -> list[OllamaModel]:
        """Локальные модели; [] если сервер недоступен."""
        data = self._post("tags", {}, timeout=5, method="GET")
        if not data:
            return []
        return [
            OllamaModel(name=m.get("name", ""), size_bytes=int(m.get("size", 0)))
            for m in data.get("models") or []
        ]

    def pick_model(self, kind: str = "chat") -> str | None:
        """Самая маленькая подходящая модель; ':cloud' — только как fallback."""
        want_embeddings = kind == "embedding"
        pool = [
            m for m in self.list_models()
            if m.is_embedding == want_embeddings
        ]
        if not pool:
            return None
        local = [m for m in pool if not m.name.endswith(":cloud")]
        return min(
            (local or pool),
            key=lambda m: m.size_bytes if m.size_bytes else 10**18,
        ).name

    def generate(
        self, prompt: str, model: str | None = None,
        system: str = "", stream: bool = False,
    ) -> str | None:
        """Ответ локальной LLM; None при недоступности."""
        model = model or self.pick_model("chat")
        if not model:
            return None
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        data = self._post("generate", payload)
        return (data or {}).get("response")

    def embed(self, text: str, model: str | None = None) -> list[float] | None:
        """Эмбеддинг текста (кэшируется по паре модель+текст)."""
        model = model or self.pick_model("embedding")
        if not model:
            return None
        key = f"{model}\x00{text}"
        if key in self._embed_cache:
            return self._embed_cache[key]
        data = self._post("embed", {"model": model, "input": text}, timeout=60)
        vecs = (data or {}).get("embeddings")
        if not vecs:
            return None
        vec = _l2_normalize(vecs[0])
        self._embed_cache[key] = vec
        return vec


def _normalize_host(host: str) -> str:
    """Гарантировать схему: '127.0.0.1:8080' → 'http://127.0.0.1:8080'."""
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


def _probe(base: str) -> bool:
    try:
        req = urllib.request.Request(f"{base}/api/version")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return b"version" in resp.read(200)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


# ── Интеграция с embed_cache ────────────────────────────────────────


def ollama_embed_fn(model: str | None = None,
                    bridge: OllamaBridge | None = None):
    """Эмбеддер для ``EmbeddingCache(embed=...)`` на локальной нейронке.

    Returns callable или None (сервера нет — используйте встроенный
    HashedNGramEmbedder).
    """
    br = bridge or OllamaBridge()
    if not br.available:
        return None

    def _embed(text: str) -> list[float]:
        vec = br.embed(text, model=model)
        if vec is None:
            raise RuntimeError("ollama недоступен посреди работы кэша")
        return vec

    return _embed


def neural_status_block() -> str:
    """Однострочный отчёт о локальных нейронках для doctor/статуса."""
    br = OllamaBridge()
    if not br.available:
        return "[neural] ollama не найден — только встроенные методы"
    models = br.list_models()
    chat = br.pick_model("chat")
    emb = br.pick_model("embedding")
    lines = [f"[neural] ollama {len(models)} моделей на {br.host}"]
    if chat:
        lines.append(f"[neural]   chat: {chat}")
    if emb:
        lines.append(f"[neural]   embedding: {emb}")
    total_gb = sum(m.size_gb for m in models)
    lines.append(f"[neural]   суммарно {total_gb:.1f} GB, всё бесплатно")
    return "\n".join(lines)
