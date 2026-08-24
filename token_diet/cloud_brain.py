"""Cloud Brain — бесплатный облачный саб-агент для слабых компьютеров.

Проблема: Ollama тянет не каждый ноутбук. Решение: мозг в облаке,
инструменты локально. Любой OpenAI-совместимый провайдер с ключом
в ``.env`` становится саб-агентом, который владеет всеми инструментами
``agent_brain``:

Цепочка провайдеров (первый найденный ключ побеждает):

- ``DEEPSEEK_API_KEY``      → api.deepseek.com (копейки)
- ``OPENROUTER_API_KEY``    → openrouter.ai, модель ``:free`` по умолчанию
- ``GROQ_API_KEY``          → api.groq.com (бесплатный тариф)
- ``ANYMODEL_API_KEY`` + ``ANYMODEL_BASE_URL`` → любой шлюз

Ключей нет? ``setup_hint()`` печатает ровно то, что нужно сделать.
Транспорт — urllib, таймауты, никаких SDK.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .agent_brain import AgentBrain, default_tools


@dataclass(frozen=True)
class Provider:
    """OpenAI-совместимый провайдер."""

    name: str
    base_url: str
    model: str
    key_env: str
    extra_env: tuple[tuple[str, str], ...] = ()   # (env, ключ словаря)


_CHAIN: tuple[Provider, ...] = (
    Provider("deepseek", "https://api.deepseek.com", "deepseek-chat",
             "DEEPSEEK_API_KEY"),
    Provider("openrouter", "https://openrouter.ai/api/v1",
             "meta-llama/llama-3.3-70b-instruct:free",
             "OPENROUTER_API_KEY"),
    Provider("groq", "https://api.groq.com/openai/v1",
             "llama-3.3-70b-versatile", "GROQ_API_KEY"),
)


@dataclass
class CloudBrain:
    """Саб-агент: облачная нейронка + локальные инструменты."""

    brain: AgentBrain = field(default_factory=default_tools)
    provider_name: str | None = None
    timeout: float = 120.0

    def __post_init__(self) -> None:
        custom_key = os.environ.get("ANYMODEL_API_KEY")
        if custom_key:
            self._provider = {
                "name": "anymodel",
                "base_url": os.environ.get(
                    "ANYMODEL_BASE_URL", "https://api.anymodel.dev/v1",
                ).rstrip("/"),
                "model": os.environ.get("ANYMODEL_MODEL", "default"),
                "key": custom_key,
            }
            self.provider_name = "anymodel"
            return
        for p in _CHAIN:
            key = os.environ.get(p.key_env)
            if key:
                self._provider = {"name": p.name, "base_url": p.base_url.rstrip("/"),
                                  "model": p.model, "key": key}
                self.provider_name = p.name
                return
        self._provider = None

    # ── состояние ───────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._provider is not None

    def info(self) -> dict[str, str]:
        if not self.available:
            return {}
        return {k: self._provider[k] for k in ("name", "base_url", "model")}

    # ── транспорт ───────────────────────────────────────────────

    def _chat_completions(
        self, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /chat/completions; RuntimeError с человеческой причиной."""
        payload: dict[str, Any] = {
            "model": self._provider["model"],
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        req = urllib.request.Request(
            f"{self._provider['base_url']}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._provider['key']}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except OSError:
                pass
            raise RuntimeError(f"{self.provider_name}: HTTP {e.code} {body}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"{self.provider_name}: сеть недоступна ({e})") from e

    @staticmethod
    def _assistant_message(response: dict[str, Any]) -> dict[str, Any]:
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        out: dict[str, Any] = {
            "role": "assistant",
            "content": msg.get("content"),
        }
        calls = msg.get("tool_calls")
        if calls:
            out["tool_calls"] = calls
        return out

    # ── цикл tool-calling ───────────────────────────────────────

    def run(self, user_prompt: str, max_steps: int | None = None) -> str:
        """Задача → облачная нейронка → инструменты → финальный ответ."""
        if not self.available:
            raise RuntimeError(setup_hint())
        steps = max_steps or self.brain.max_steps
        messages: list[dict[str, Any]] = [
            {"role": "system", "content":
                "Ты агент token-diet. Используй инструменты для фактов. "
                "Отвечай кратко, по-русски."},
            {"role": "user", "content": user_prompt},
        ]
        tools = self.brain.openai_tools()
        for _ in range(steps):
            resp = self._chat_completions(messages, tools=tools or None)
            msg = self._assistant_message(resp)
            calls = msg.get("tool_calls") or []
            if not calls:
                return msg.get("content") or ""
            messages.append(msg)
            for call in calls:
                fn = call.get("function") or {}
                result = self.brain.execute(
                    fn.get("name", ""), fn.get("arguments", {}),
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False),
                })
        return "лимит шагов исчерпан — задача слишком большая"


def setup_hint() -> str:
    """Что именно человеку сделать, чтобы появился облачный мозг."""
    lines = [
        "[cloud] облачный саб-агент не настроен — выбери один вариант:",
        "[cloud]   1) DEEPSEEK_API_KEY  → platform.deepseek.com (почти бесплатно)",
        "[cloud]   2) OPENROUTER_API_KEY → openrouter.ai (есть :free модели)",
        "[cloud]   3) GROQ_API_KEY     → console.groq.com (бесплатный тариф)",
        "[cloud]   впиши ключ в .env рядом с проектом и перезапусти",
    ]
    return "\n".join(lines)
