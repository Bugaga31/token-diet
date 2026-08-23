"""Omni Eyes — «глаза» через OmniRoute vision-модели.

Скриншот экрана → base64 → vision-модель (Claude Opus 5 и др. через роутер)
→ описание / ответ на вопрос. Нейронка получает зрение без браузерных кук.

Модели (fallback-цепочка):
  1. agentrouter/claude-opus-5        — самые точные глаза (по умолчанию)
  2. agentrouter/claude-opus-5-low    — быстрый режим
  3. agy/gemini-3.6-flash-medium      — резерв (если OAuth починят)

Использование:
  from token_diet.omni_eyes import see, ask_about_screen
  print(see())                    # «что на экране» — краткое описание
  print(ask_about_screen("какая цена на графике?"))
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

OMNI_BASE = os.environ.get("OMNIROUTE_BASE_URL", "http://localhost:20128/v1").rstrip("/")

# Модели в порядке приоритета: (имя, метка)
VISION_MODELS = [
    ("agentrouter/claude-opus-5", "Claude Opus 5 (точные глаза)"),
    ("agentrouter/claude-opus-5-low", "Claude Opus 5 Low (быстрый)"),
    ("agy/gemini-3.6-flash-medium", "Gemini 3.6 Flash (резерв)"),
]

SCREENSHOT_TOOLS = [
    # (команда, путь вывода) — первая сработавшая побеждает
    ("import", "-window", "root", "{path}"),          # ImageMagick
    ("scrot", "{path}"),                               # scrot
    ("gnome-screenshot", "-f", "{path}"),              # GNOME
    ("spectacle", "-b", "-n", "-o", "{path}"),         # KDE
    ("xwd", "-root", "-out", "{path}"),                # X11 xwd (raw — конвертим)
]

MAX_IMAGE_BYTES = 4_000_000  # если скриншот больше — сжимаем
MAX_SIDE = 1200              # максимальная сторона после ресайза (ревью модели)

# Кэш «глаз»: хеш скриншота → base64. Если экран не изменился — отдаём кеш,
# не тратим ни токены, ни сеть (ревью модели: «−100% повторных вызовов»).
_SCREEN_CACHE: dict[str, tuple[float, str, str]] = {}  # hash -> (t, b64, mime)
_SCREEN_CACHE_TTL = 5.0  # секунд — экран живёт недолго, но повторные вызовы часты


def _find_screenshot_tool() -> Optional[tuple[list[str], bool]]:
    """Найти доступный инструмент скриншота. Возвращает (cmd, нужен ли convert)."""
    for tool in SCREENSHOT_TOOLS:
        name = tool[0]
        if shutil.which(name):
            return list(tool), (name == "xwd")
    return None


def take_screenshot(path: Optional[str] = None) -> Optional[str]:
    """Сделать скриншот всего экрана. Возвращает путь к PNG или None."""
    path = path or os.path.join(tempfile.gettempdir(), "omni_eye.png")
    found = _find_screenshot_tool()
    if not found:
        # последний шанс — скрипты screen-control скилла
        for script in (
            "/home/ro/.agents/skills/screen-control/scripts/screenshot.sh",
            "/home/ro/.agents/skills/screen-control/scripts/scr.sh",
        ):
            if os.path.exists(script):
                try:
                    subprocess.run(["bash", script, path], timeout=15, check=False)
                    if os.path.exists(path) and os.path.getsize(path) > 1000:
                        return path
                except Exception:
                    pass
        return None

    cmd_template, needs_convert = found
    cmd = [c.replace("{path}", path) for c in cmd_template]
    try:
        subprocess.run(cmd, timeout=15, check=False)
    except Exception:
        return None

    if needs_convert and shutil.which("convert"):
        # xwd даёт raw — конвертим в PNG
        tmp = path + ".xwd"
        if os.path.exists(tmp):
            subprocess.run(["convert", tmp, path], timeout=20, check=False)
            os.remove(tmp)

    if not os.path.exists(path) or os.path.getsize(path) < 1000:
        return None
    return _maybe_shrink(path)


def _maybe_shrink(path: str) -> str:
    """Сжать большой скриншот (re-save/ресайз через ImageMagick или PIL)."""
    size = os.path.getsize(path)
    if size <= MAX_IMAGE_BYTES:
        return path
    if shutil.which("convert"):
        small = path.replace(".png", "_small.png")
        subprocess.run(["convert", path, "-resize", f"{MAX_SIDE}x", "-quality", "85",
                        small], timeout=30, check=False)
        if os.path.exists(small) and os.path.getsize(small) < size:
            return small
    try:
        from PIL import Image
        img = Image.open(path)
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        small = path.replace(".png", "_small.png")
        img.save(small, "PNG", optimize=True)
        return small
    except ImportError:
        pass
    return path


def _file_hash(path: str) -> Optional[str]:
    """Быстрый хеш файла (первые + последние 64KB — достаточно для кэша)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size <= 131_072:
                return hashlib.sha1(f.read()).hexdigest()
            head = f.read(65_536)
            f.seek(-65_536, os.SEEK_END)
            tail = f.read(65_536)
            return hashlib.sha1(head + tail).hexdigest()
    except OSError:
        return None


def _image_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _cached_image_base64(path: str) -> Optional[tuple[str, str]]:
    """base64 из кэша по хешу файла (ревью модели: экономия на повторах).

    Возвращает (b64, mime) или None. TTL короткий — экран меняется.
    """
    if not path or not os.path.exists(path) or os.path.getsize(path) < 20:
        return None
    h = _file_hash(path)
    now = time.time()
    if h:
        cached = _SCREEN_CACHE.get(h)
        if cached and now - cached[0] < _SCREEN_CACHE_TTL:
            return cached[1], cached[2]
    b64 = _image_to_base64(path)
    mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
    if h:
        _SCREEN_CACHE[h] = (now, b64, mime)
    return b64, mime


def _safe_image_base64(path: str) -> Optional[str]:
    """base64 изображения или None, если файл недоступен."""
    try:
        got = _cached_image_base64(path)
        return got[0] if got else None
    except OSError:
        return None


def ask_vision(question: str, image_path: Optional[str] = None,
               model: Optional[str] = None, max_tokens: int = 300,
               timeout: int = 120) -> dict:
    """Задать вопрос vision-модели про изображение (экран).

    Возвращает {"ok": bool, "answer": str, "model": str, "error": str|None}
    """
    if not HAS_REQUESTS:
        return {"ok": False, "answer": "", "model": model or "",
                "error": "нет requests"}

    path = image_path or take_screenshot()
    if not path:
        return {"ok": False, "answer": "", "model": model or "",
                "error": "не удалось сделать скриншот (нет инструмента X11)"}

    got = _cached_image_base64(path)
    if not got:
        return {"ok": False, "answer": "", "model": model or "",
                "error": f"не удалось прочитать изображение: {path}"}
    b64, mime = got

    models = [model] if model else [m[0] for m in VISION_MODELS]
    last_err = ""
    for attempt, m in enumerate(models):
        if not m:
            continue
        payload = {
            "model": m,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            r = requests.post(f"{OMNI_BASE}/chat/completions", json=payload,
                              timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                return {"ok": True, "answer": content, "model": m, "error": None}
            if r.status_code == 429:
                # Ревью модели: exponential backoff на rate-limit, потом след. модель
                time.sleep(min(2 ** attempt, 8))
                last_err = f"{m}: 429 rate-limit (backoff {2 ** attempt}s)"
                continue
            last_err = f"{m}: HTTP {r.status_code} {r.text[:150]}"
        except Exception as e:
            last_err = f"{m}: {e}"
    return {"ok": False, "answer": "", "model": model or "",
            "error": last_err or "все vision-модели недоступны"}


def see(question: str = "Опиши кратко, что сейчас на экране. 2-3 предложения.",
        model: Optional[str] = None) -> str:
    """«Посмотреть» на экран — вернуть описание или ответ на вопрос."""
    res = ask_vision(question, model=model)
    if res["ok"]:
        return res["answer"]
    return f"[глаза не видят: {res['error']}]"


def ask_about_screen(question: str, model: Optional[str] = None) -> str:
    """Спросить конкретное про текущий экран (например: «какой там текст?»)."""
    return see(question, model=model)


def see_image(image_path: str, question: str = "Опиши, что на изображении.",
              model: Optional[str] = None) -> str:
    """Посмотреть на произвольное изображение (файл)."""
    res = ask_vision(question, image_path=image_path, model=model)
    if res["ok"]:
        return res["answer"]
    return f"[глаза не видят: {res['error']}]"


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Omni Eyes — зрение через OmniRoute")
    p.add_argument("question", nargs="?", default="Опиши кратко, что сейчас на экране. 2-3 предложения.",
                   help="вопрос про экран (по умолчанию — описание)")
    p.add_argument("--model", default=None, help="конкретная модель (например agentrouter/claude-opus-5)")
    p.add_argument("--image", default=None, help="файл изображения вместо скриншота")
    p.add_argument("--models", action="store_true", help="показать доступные vision-модели")
    args = p.parse_args()

    if args.models:
        print("Vision-модели через OmniRoute:")
        for m, label in VISION_MODELS:
            print(f"  {m}  — {label}")
        return

    if args.image:
        print(see_image(args.image, args.question, args.model))
    else:
        print(see(args.question, args.model))


if __name__ == "__main__":
    main()
