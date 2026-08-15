"""Model Army — армия LLM-моделей AnyModel с распределением задач.

ГЕНЕРАЛ ПРОВЕРИЛ (15.08.2026) — живые модели через прокси Karing:
  am/deepseek-v4-pro:      главный мозг (сложный анализ)
  am/deepseek-v4-flash:    скоростной (быстрая классификация) ✅ проверено
  am/glm-5.2:              сильный аналитик (глубокие разборы)
  am/diffusiongemma-26b:   генерация/креатив

Роли в армии:
- ask_brain()    → deepseek-v4-pro: сложные решения, анализ
- ask_analyst()  → glm-5.2: глубокие разборы, отчёты
- ask_fast()     → deepseek-v4-flash: быстрая классификация, короткие ответы
- ask_generator()-> diffusiongemma: креатив, генерация

⚠️ УРОК 15.08.2026: мини-max-m3 отдавал 404 (модель недоступна на ключе),
а старый urllib-путь с глобальной подменой socket падал с SSL EOF.
Теперь: requests + socks5h через Karing, авто-fallback на прямой запрос
(если прокси лежит), ретраи до 3 раз. Ключ из .env — НЕ захардкожен.
"""

from __future__ import annotations

import json
import os
import time

API_URL = "https://anymodel.org/v1/chat/completions"
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 3066

# Роли → модели
ROLES = {
    "brain": "am/deepseek-v4-pro",      # главный мозг
    "analyst": "am/glm-5.2",            # аналитик
    "fast": "am/deepseek-v4-flash",     # скоростной ✅ (minimax-m3 отдавал 404)
    "generator": "am/diffusiongemma-26b-a4b-it",  # генератор
}


def _key() -> str:
    """Ключ из .env проекта (или env). Без хардкода в коде."""
    # сначала env
    k = os.environ.get("ANYMODEL_API_KEY")
    if k:
        return k
    # потом .env файл
    for path in (".env", os.path.expanduser("~/.env")):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("ANYMODEL_API_KEY="):
                            return line.split("=", 1)[1].strip()
        except Exception:
            continue
    return ""


def _proxy_env() -> dict:
    """Прокси-словарь для requests: socks5h через Karing, если жив."""
    import socket
    try:
        s = socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=2)
        s.close()
        return {"http": f"socks5h://{PROXY_HOST}:{PROXY_PORT}",
                "https": f"socks5h://{PROXY_HOST}:{PROXY_PORT}"}
    except OSError:
        return {}


def _post(payload: dict, timeout: int = 60) -> dict:
    """POST с авто-fallback: через Karing → напрямую. Возвращает json или бросает."""
    import requests
    key = _key()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    proxies = _proxy_env()
    last_err = None
    for attempt in range(2):
        try:
            if proxies:
                r = requests.post(API_URL, json=payload, headers=headers,
                                  proxies=proxies, timeout=timeout)
            else:
                r = requests.post(API_URL, json=payload, headers=headers,
                                  timeout=timeout)
            if r.status_code == 200:
                return r.json()
            err = r.text[:120]
            raise RuntimeError(f"HTTP {r.status_code} {err}")
        except Exception as e:  # noqa: BLE001
            last_err = e
            # прокси умер — падаем на прямой запрос
            if proxies:
                proxies = {}
            else:
                time.sleep(2 * (attempt + 1))
    raise last_err  # type: ignore[misc]


def ask(
    prompt: str,
    role: str = "brain",
    system: str | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    retries: int = 3,
) -> str:
    """Спросить армию. role: brain/analyst/fast/generator.

    Возвращает текст ответа или сообщение об ошибке.
    """
    model = ROLES.get(role, ROLES["brain"])
    key = _key()
    if not key:
        return "ERR: ANYMODEL_API_KEY не найден (нет .env)"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    last_err = ""
    for attempt in range(retries):
        try:
            data = _post(payload)
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:100]
        time.sleep(2 * (attempt + 1))
    return f"ERR: {last_err} (модель {model})"


def ask_brain(prompt: str, system: str | None = None, **kw) -> str:
    """Главный мозг: сложные решения, глубокий анализ."""
    return ask(prompt, role="brain", system=system, **kw)


def ask_analyst(prompt: str, system: str | None = None, **kw) -> str:
    """Аналитик: разборы, отчёты, аргументация."""
    return ask(prompt, role="analyst", system=system, **kw)


def ask_fast(prompt: str, system: str | None = None, **kw) -> str:
    """Скоростной: классификация, короткие ответы, маршрутизация."""
    return ask(prompt, role="fast", system=system, **kw)


def ask_generator(prompt: str, system: str | None = None, **kw) -> str:
    """Генератор: креатив, тексты, идеи."""
    return ask(prompt, role="generator", system=system, **kw)


def army_verdict(prompt: str, system: str | None = None) -> dict:
    """Спросить ВСЮ армию и собрать голоса (многоголосое решение).

    Полезно для важных решений: каждая модель голосует,
    ответ — согласованное мнение армии.
    """
    results = {}
    for role in ROLES:
        results[role] = ask(prompt, role=role, system=system, max_tokens=600)
    return results


if __name__ == "__main__":
    print("🤖 МОДЕЛЬНАЯ АРМИЯ — проверка всех ролей")
    print("=" * 56)
    for role, model in ROLES.items():
        r = ask("Ответь одним словом: жив?", role=role, max_tokens=15)
        print(f"  {role:<10} ({model}): {r}")
