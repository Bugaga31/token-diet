"""Provider Registry — авто-обнаружение LLM-провайдеров из env + универсальный вызов.

УРОК 16.08.2026 (генерал: «добавляй через куки/просто API — кучу моделей»):
у генерала в окружении лежит целый арсенал ключей — NVIDIA (nvapi-),
OpenVecta (ov_sk_live_), FreeTheAI (ck_), Gemini, Anthropic, OpenRouter.
Раньше каждый провайдер надо было кодить руками. Теперь — реестр:
сканируем env, определяем провайдера по префиксу ключа и известному
эндпоинту, и любой провайдер можно спросить через единый ask_any().

Принцип: НИ ОДИН ключ не хардкодится — всё из env/.env. Список эндпоинтов —
только публичные API-базы (без ключей).
"""

from __future__ import annotations

import json
import os
import time

# Провайдер → (детектор по префиксу ключа, базовый URL, имя для логов)
_PROVIDERS: dict[str, dict] = {
    "nvidia": {
        "key_prefix": "nvapi-",
        "env_key": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "label": "NVIDIA NIM",
        "default_model": "deepseek-ai/deepseek-v4-flash-0731",
    },
    "nvidia-ultra": {
        "key_prefix": "nvapi-",
        "env_key": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "label": "NVIDIA Nemotron Ultra 550B",
        "default_model": "nvidia/nemotron-3-ultra-550b-a55b",
    },
    "nvidia-llama": {
        "key_prefix": "nvapi-",
        "env_key": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "label": "NVIDIA Llama 3.3 70B",
        "default_model": "meta/llama-3.3-70b-instruct",
    },
    "nvidia-gpt-oss": {
        "key_prefix": "nvapi-",
        "env_key": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "label": "NVIDIA GPT-OSS 120B",
        "default_model": "openai/gpt-oss-120b",
    },
    "openvecta": {
        "key_prefix": "ov_sk_live_",
        "env_key": "OPENVECTA_API_KEY",
        "base_url": "https://api.openvecta.com/v1",
        "label": "OpenVecta",
    },
    "gemini": {
        "key_prefix": "",
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "label": "Gemini",
    },
    "openai": {
        "key_prefix": "sk-",
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "label": "OpenAI",
    },
    "anthropic": {
        "key_prefix": "sk-ant-",
        "env_key": "ANTHROPIC_AUTH_TOKEN",
        "base_url": "https://api.anthropic.com/v1",
        "label": "Anthropic",
    },
    "openrouter": {
        "key_prefix": "sk-or-",
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "label": "OpenRouter",
    },
    "omniroute": {
        "key_prefix": "sk-",
        "env_key": "OMNIROUTE_API_KEY",
        "base_url": os.environ.get("OMNIROUTE_BASE_URL", "http://localhost:20128/v1"),
        "label": "OmniRoute (локальный)",
    },
}


def _env(key: str) -> str:
    """Значение ключа из env или .env проекта."""
    v = os.environ.get(key, "")
    if v:
        return v
    for path in (".env", os.path.expanduser("~/.env")):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith(f"{key}="):
                            return line.split("=", 1)[1].strip()
        except Exception:
            continue
    return ""


def detect_providers() -> dict[str, dict]:
    """Возвращает {имя: {key, base_url, label}} для провайдеров с живым ключом."""
    found: dict[str, dict] = {}
    for name, cfg in _PROVIDERS.items():
        key = _env(cfg["env_key"])
        if not key:
            continue
        entry: dict = {"key": key, "base_url": cfg["base_url"], "label": cfg["label"]}
        if cfg.get("default_model"):
            entry["default_model"] = cfg["default_model"]
        found[name] = entry
    return found


def available_providers() -> list[str]:
    return sorted(detect_providers())


def ask_any(
    provider: str,
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    max_tokens: int = 1000,
    temperature: float = 0.7,
    retries: int = 2,
    timeout: int = 120,
) -> str:
    """Универсальный вызов любого провайдера (OpenAI-совместимый /chat/completions).

    model можно опустить — тогда спросим /models и возьмём первую chat-модель.
    """
    providers = detect_providers()
    if provider not in providers:
        return f"ERR: провайдер {provider!r} не найден. Есть: {', '.join(available_providers())}"
    cfg = providers[provider]
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {cfg['key']}"}

    # модель не дана — берём default_model провайдера или первую из /models
    if not model:
        model = cfg.get("default_model", "")
    if not model:
        try:
            import requests
            r = requests.get(cfg["base_url"].rstrip("/") + "/models",
                             headers={"Authorization": f"Bearer {cfg['key']}"}, timeout=15)
            if r.status_code == 200:
                models = [m.get("id", "") for m in r.json().get("data", [])
                          if m.get("id") and not m["id"].endswith(("-embedding", "-rerank"))]
                if models:
                    model = models[0]
        except Exception:
            pass
    if not model:
        return f"ERR: не удалось определить модель для {provider}"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature}

    import requests
    last_err = ""
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            r.encoding = "utf-8"
            if r.status_code == 200:
                # SSE?
                if "data: " in r.text:
                    parts = []
                    for line in r.text.splitlines():
                        line = line.strip()
                        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                            try:
                                chunk = json.loads(line[6:])
                                d = (chunk.get("choices") or [{}])[0].get("delta", {})
                                if d.get("content"):
                                    parts.append(d["content"])
                            except json.JSONDecodeError:
                                continue
                    return "".join(parts).strip() or "(пустой ответ)"
                data = r.json()
                return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
            last_err = r.text[:150]
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:120]
        time.sleep(2 * (attempt + 1))
    return f"ERR: {last_err} ({cfg['label']} / {model})"


def ask_all(prompt: str, system: str | None = None, max_tokens: int = 500) -> dict[str, str]:
    """Спросить ВСЕХ живых провайдеров. Возвращает {провайдер: ответ}."""
    out: dict[str, str] = {}
    for name in available_providers():
        out[name] = ask_any(name, prompt, system=system, max_tokens=max_tokens)
    return out


if __name__ == "__main__":
    print("Провайдеры:", available_providers())
