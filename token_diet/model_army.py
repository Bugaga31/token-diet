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

# ─── OmniRoute (локальный роутер генерала, 607 моделей, 1M контекст) ────────
# УРОК 16.08.2026: у генерала крутится OmniRoute на localhost:20128 — локальный
# AI-роутер с 607 моделями (Claude Opus 5, Sonnet 5, GPT-4o, DeepSeek...) и
# контекстом до 1M токенов. Роутер сам выбирает лучшую модель под задачу
# (x-omniroute-decision) и отдаёт ответ SSE-чанками (data: {...}).
# Комбо-маршруты: auto/best-reasoning, auto/best-coding, auto/best-fast,
# auto/claude-opus, auto/gemini, auto/glm и т.д.
OMNI_URL = os.environ.get("OMNIROUTE_BASE_URL", "http://localhost:20128/v1").rstrip("/")
OMNI_CHAT_URL = f"{OMNI_URL}/chat/completions"

# Роли → комбо-маршруты OmniRoute
OMNI_ROLES = {
    "brain": "auto/best-reasoning",   # глубокий анализ
    "coding": "auto/best-coding",     # код
    "fast": "auto/best-fast",         # быстрые ответы
    "claude": "auto/claude-opus",     # Claude-семейство (авто-выбор)
    "gemini": "auto/gemini",          # Gemini-семейство
}

# УРОК 16.08.2026: прямой доступ к Claude Opus 5 — только через
# agentrouter/claude-opus-5-high (проверено живьём: ответил сам Opus 5).
# gh/ и opencode/ отдают 400/401 (модель не поддерживается/нет ключа),
# а auto/claude-opus роутер переводит на gpt-4o (экономия).
CLAUDE_OPUS_5 = "agentrouter/claude-opus-5-high"


def _omni_key() -> str:
    """Ключ OmniRoute из .env (для локального роутера можно пустой)."""
    k = os.environ.get("OMNIROUTE_API_KEY", "")
    if k:
        return k
    for path in (".env", os.path.expanduser("~/.env")):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("OMNIROUTE_API_KEY="):
                            return line.split("=", 1)[1].strip()
        except Exception:
            continue
    return ""


def _parse_sse(resp_text: str) -> str:
    """Собирает ответ из SSE-чанков (data: {...}). Не-SSE — берём как есть."""
    content_parts: list[str] = []
    for line in resp_text.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        txt = delta.get("content") or ""
        if txt:
            content_parts.append(txt)
        # не-SSE-объект с готовым message
        msg = choices[0].get("message") or {}
        if msg.get("content"):
            content_parts.append(msg["content"])
    joined = "".join(content_parts).strip()
    if joined:
        return joined
    # не SSE — обычный JSON-ответ
    try:
        data = json.loads(resp_text)
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content", "").strip()
    except json.JSONDecodeError:
        # SSE без контента (пустой выбор) → пусто, чтобы ask_omni попробовал иначе
        if "data:" in resp_text:
            return ""
        return resp_text.strip()


def ask_omni(
    prompt: str,
    role: str = "brain",
    system: str | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    retries: int = 3,
    model: str | None = None,
) -> str:
    """Спросить OmniRoute (локальный роутер). role: brain/coding/fast/claude/gemini.

    model можно передать напрямую (напр. 'auto/best-reasoning' или 'gh/claude-fable-5').
    Отвечает SSE — парсим чанки.
    """
    m = model or OMNI_ROLES.get(role, OMNI_ROLES["brain"])
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": m,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    headers = {"Content-Type": "application/json"}
    key = _omni_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["x-api-key"] = key

    import requests
    last_err = ""
    for attempt in range(retries):
        try:
            r = requests.post(OMNI_CHAT_URL, json=payload, headers=headers, timeout=180)
            r.encoding = "utf-8"  # иначе кириллица читается как Latin-1
            if r.status_code == 200:
                out = _parse_sse(r.text)
                if out.strip():
                    return out
                # SSE вернул пусто (роутер выбрал модель без контента) — не-SSE
                payload["stream"] = False
                r = requests.post(OMNI_CHAT_URL, json=payload, headers=headers, timeout=180)
                if r.status_code == 200:
                    return _parse_sse(r.text)
            err = r.text[:150]
            # локальный роутер может требовать ключ — пробуем повторно
            if "credentials" in err and attempt == 0:
                payload["stream"] = False
                r = requests.post(OMNI_CHAT_URL, json=payload, headers=headers, timeout=180)
                if r.status_code == 200:
                    return _parse_sse(r.text)
            raise RuntimeError(f"HTTP {r.status_code} {err}")
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:120]
        time.sleep(2 * (attempt + 1))
    return f"ERR: {last_err} (omni {m})"


# ─── Gemini (второй провайдер, OpenAI-совместимый API) ───────────────────────
# УРОК 16.08.2026: у генерала есть живой GEMINI_API_KEY (gemini-2.5-pro/flash).
# Подключаем Gemini как независимое «второе мнение» — та же логика _post,
# другой endpoint + ключ. Работает напрямую (без прокси: Google не режет РФ-ключи
# через свой API), авто-fallback на прокси при ошибке.
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

# Роли → модели Gemini
# УРОК 16.08.2026: на ключе генерала генерация работает только через
# gemini-flash-latest / gemini-flash-lite-latest (2.5-pro/flash отдают 404
# и на OpenAI-совместимом, и на REST-пути). Поэтому — автоподбор при первом
# вызове: пробуем кандидатов по порядку, запоминаем рабочего.
GEMINI_ROLES = {
    "brain": "gemini-flash-latest",
    "fast": "gemini-flash-lite-latest",
}
_GEMINI_CANDIDATES = [
    "gemini-2.5-pro", "gemini-2.5-flash",
    "gemini-flash-latest", "gemini-flash-lite-latest",
]
_GEMINI_WORKING: str | None = None


def _gemini_key() -> str:
    """GEMINI_API_KEY из env (у генерала жив в окружении). Без хардкода."""
    return os.environ.get("GEMINI_API_KEY", "")


def _gemini_pick_working() -> str:
    """Находит первую рабочую модель (кэш в _GEMINI_WORKING). ~1 запрос."""
    global _GEMINI_WORKING
    if _GEMINI_WORKING:
        return _GEMINI_WORKING
    import requests
    key = _gemini_key()
    for m in _GEMINI_CANDIDATES:
        try:
            r = requests.post(
                GEMINI_API_URL,
                json={"model": m, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                headers={"Authorization": f"Bearer {key}"},
                timeout=15,
            )
            if r.status_code == 200:
                _GEMINI_WORKING = m
                return m
        except Exception:  # noqa: BLE001
            continue
    _GEMINI_WORKING = "gemini-flash-latest"  # запасной вариант
    return _GEMINI_WORKING


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


def _post(payload: dict, timeout: int = 60, url: str = API_URL, key: str | None = None,
          use_proxy_first: bool = True) -> dict:
    """POST с авто-fallback: через Karing → напрямую. Возвращает json или бросает.

    - url/key — можно переопределить (Gemini ходит напрямую, без прокси-первым).
    - use_proxy_first=False → сначала прямой запрос, прокси как fallback.
    """
    import requests
    key = key or _key()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    proxies = _proxy_env() if use_proxy_first else {}
    last_err = None
    for attempt in range(2):
        try:
            if proxies:
                r = requests.post(url, json=payload, headers=headers,
                                  proxies=proxies, timeout=timeout)
            else:
                r = requests.post(url, json=payload, headers=headers,
                                  timeout=timeout)
            if r.status_code == 200:
                return r.json()
            err = r.text[:120]
            raise RuntimeError(f"HTTP {r.status_code} {err}")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if proxies:
                proxies = {}          # прокси умер — прямой запрос
            elif not use_proxy_first:
                proxies = _proxy_env()  # прямой не прошёл — пробуем через прокси
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


def ask_gemini(
    prompt: str,
    role: str = "brain",
    system: str | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    retries: int = 3,
) -> str:
    """Второе мнение от Gemini (2.5-pro/fast). Тот же контракт, что ask().

    Идёт напрямую (Google API доступен без прокси), при падении — через Karing.
    Роли: brain → gemini-2.5-pro, fast → gemini-2.5-flash.
    """
    model = _gemini_pick_working()
    key = _gemini_key()
    if not key:
        return "ERR: GEMINI_API_KEY не найден (нет в env)"

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
            data = _post(payload, url=GEMINI_API_URL, key=key, use_proxy_first=False)
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:100]
        time.sleep(2 * (attempt + 1))
    return f"ERR: {last_err} (gemini {model})"


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
