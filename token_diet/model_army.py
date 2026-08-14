"""Model Army — армия LLM-моделей AnyModel с распределением задач.

ГЕНЕРАЛ ПРОВЕРИЛ (14.08.2026) — все модели живые через прокси Karing:
  am/deepseek-v4-pro:      10.9с → главный мозг (сложный анализ)
  am/glm-5.2:              21.6с → сильный аналитик (глубокие разборы)
  am/minimax-m3:            1.3с → скоростной (быстрые ответы, классификация)
  am/diffusiongemma-26b:    8.4с → генерация/креатив

Роли в армии:
- ask_brain()    → deepseek-v4-pro: сложные решения, анализ
- ask_analyst()  → glm-5.2: глубокие разборы, отчёты
- ask_fast()     → minimax-m3: быстрая классификация, короткие ответы
- ask_generator()-> diffusiongemma: креатив, генерация

Все запросы через прокси Karing (127.0.0.1:3066) — напрямую SSL режет DPI.
Ключ из .env (ANYMODEL_API_KEY) — НЕ захардкожен.
Ретраи до 3 раз — сеть капризничает.
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
    "fast": "am/minimax-m3",            # скоростной
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


def _proxy_socket():
    """Подключить глобальный сокет к прокси Karing."""
    import socks
    import socket
    socks.set_default_proxy(socks.SOCKS5, PROXY_HOST, PROXY_PORT)
    socket.socket = socks.socksocket


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

    _proxy_socket()

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()

    import urllib.request

    last_err = ""
    for attempt in range(retries):
        req = urllib.request.Request(API_URL, data=body, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                msg = json.loads(e.read()).get("error", {}).get("message", "")[:80]
            except Exception:
                pass
            last_err = f"HTTP {e.code} {msg}"
        except Exception as e:
            last_err = str(e)[:80]
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
