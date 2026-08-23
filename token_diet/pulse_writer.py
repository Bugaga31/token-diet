"""PulseWriter — писать в Пульс Т-Банка (посты, лайки, комментарии).

ВАЖНОЕ РАЗЛИЧИЕ:
- ЧТЕНИЕ Пульса — публично, без авторизации (см. pulse_reader)
- ЗАПИСЬ (пост/лайк/комментарий) требует СЕССИОННЫХ КУК веб-банка.
  Токен Invest API (t.xxx) для соцсети НЕ работает — это другой контур.

Как активировать (один раз):
1. Войти в https://www.tbank.ru/invest/ в Firefox на этой машине
2. load_cookies_from_firefox() вытащит куки из cookies.sqlite
   (в Firefox куки не зашифрованы) — ИЛИ передать файл cookies.txt/JSON
3. post_text() по умолчанию работает в DRY_RUN: показывает, что пошлёт,
   ничего не отправляя

Guardrails (жёсткие, не отключаются случайно):
- dry_run=True по умолчанию — реальная отправка только с confirm=True
- дедуп: одинаковый текст не публикуется дважды (hash-кэш)
- анти-спам: пауза между действиями + лимит действий в минуту
- текст без ссылок на сторонние ресурсы (анти-модерация Пульса)
- контракт API реверс-инжиниринг (неофициальный) — первый реальный пост
  всегда после dry-run и проверки

Только stdlib (urllib, sqlite3, json).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOCIAL_BASE = "https://www.tinkoff.ru/api/invest-gw/social/v1"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_LINK_RE = re.compile(r"https?://\S+", re.I)
_TICKER_RE = re.compile(r"\{\$[A-Z0-9_]+\}")


@dataclass
class WriteState:
    """Состояние анти-спама и дедупа (в памяти процесса)."""

    posted_hashes: set[str] = field(default_factory=set)
    last_action_ts: float = 0.0
    actions_in_window: int = 0
    window_start: float = 0.0
    min_interval: float = 5.0  # сек между действиями
    max_per_minute: int = 3


_STATE = WriteState()


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ═══════════════════════════════════════════════════════════════════════════════
# Загрузка кук
# ═══════════════════════════════════════════════════════════════════════════════

def _read_tbank_cookies(db: Path) -> list[str]:
    """Прочитать tbank-куки из cookies.sqlite, переживая блокировку Firefox.

    Firefox держит базу в WAL-режиме: при запущенном браузере прямой
    read-only коннект падает с 'database is locked'. Решение — скопировать
    db + wal + shm во временную папку и читать копию (WAL подхватит
    незакоммиченные сессионные куки, которые Firefox ещё не сбросил
    на диск).
    """
    attempts = [db]
    if db.exists():
        import shutil
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="fx_cookies_"))
        try:
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(db) + suffix)
                if src.exists():
                    shutil.copy2(src, tmp / src.name)
            copy = tmp / db.name
            if copy.exists():
                attempts.insert(0, copy)
        except OSError:
            pass

    for candidate in attempts:
        try:
            con = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT name, value FROM moz_cookies "
                "WHERE (host LIKE '%tinkoff.ru' OR host LIKE '%tbank.ru') "
                "AND name IS NOT NULL AND value IS NOT NULL AND value != ''"
            ).fetchall()
            con.close()
            if rows:
                return [f"{n}={v}" for n, v in rows]
        except sqlite3.Error:
            continue
    return []


def load_cookies_from_firefox(profile_dir: str | None = None) -> str:
    """Вытащить Cookie-заголовок для tinkoff.ru/tbank.ru из Firefox.

    В Firefox куки хранятся в cookies.sqlite БЕЗ шифрования (в отличие
    от Chromium). Автоматически выбирает профиль с НАИБОЛЬШИМ числом
    tbank-кук (сессия обычно в активном default-release, а не в первом
    по алфавиту) и читает через WAL-копию, если Firefox запущен.

    Возвращает строку 'name=value; name2=value2'.
    """
    base = Path.home() / ".mozilla" / "firefox"
    profiles = []
    if profile_dir is not None:
        profiles.append(Path(profile_dir))
    elif base.is_dir():
        for d in base.iterdir():
            if d.is_dir() and (d / "cookies.sqlite").exists():
                profiles.append(d)

    best: tuple[int, str] = (0, "")
    for p in profiles:
        try:
            cookies = _read_tbank_cookies(p / "cookies.sqlite")
        except Exception:
            cookies = []
        if len(cookies) > best[0]:
            best = (len(cookies), "; ".join(cookies))

    if not best[1]:
        raise RuntimeError(
            "кук tinkoff/tbank не найдено ни в одном профиле Firefox — "
            "сначала войди в https://www.tbank.ru/invest/ в Firefox"
        )
    return best[1]


def load_cookies_from_file(path: str) -> str:
    """Куки из файла: Netscape cookies.txt (.txt) или JSON-список кук."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    text = p.read_text(encoding="utf-8", errors="replace")

    if p.suffix.lower() == ".json" or text.lstrip().startswith("["):
        data = json.loads(text)
        parts = [
            f"{c['name']}={c['value']}"
            for c in data
            if c.get("name") and c.get("value")
        ]
        return "; ".join(parts)

    # Netscape format: domain, flag, path, secure, expiry, name, value
    parts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            parts.append(f"{fields[5]}={fields[6]}")
    return "; ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════════════════════════════════

def _http_post(path: str, payload: dict[str, Any], cookie: str,
               timeout: float = 15.0) -> dict[str, Any] | None:
    url = f"{SOCIAL_BASE}{path}?appName=invest&platform=web"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Cookie": cookie,
        },
    )
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                    return json.loads(resp.read().decode("utf-8", errors="replace"))
            raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
            json.JSONDecodeError):
        return None


def _check_rate_limit() -> dict[str, Any]:
    """Анти-спам: пауза между действиями + лимит в минуту."""
    now = time.time()
    if now - _STATE.window_start > 60:
        _STATE.window_start = now
        _STATE.actions_in_window = 0
    if _STATE.last_action_ts and now - _STATE.last_action_ts < _STATE.min_interval:
        return {"ok": False, "reason": "пауза между действиями ещё не прошла"}
    if _STATE.actions_in_window >= _STATE.max_per_minute:
        return {"ok": False, "reason": "превышен лимит действий в минуту"}
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Действия
# ═══════════════════════════════════════════════════════════════════════════════

def validate_text(text: str) -> dict[str, Any]:
    """Проверка текста: не пустой, без внешних ссылок, не дубликат."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "пустой текст"}
    if len(text) > 3000:
        return {"ok": False, "reason": f"текст слишком длинный ({len(text)} > 3000)"}
    if _LINK_RE.search(text):
        return {"ok": False, "reason": "текст содержит внешнюю ссылку (риск модерации)"}
    h = hashlib.sha256(text.encode()).hexdigest()
    if h in _STATE.posted_hashes:
        return {"ok": False, "reason": "такой текст уже публиковался (дедуп)"}
    return {"ok": True, "hash": h}


def post_text(
    text: str,
    tickers: list[str] | None = None,
    cookie: str | None = None,
    cookie_source: str | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Создать пост в Пульсе.

    dry_run=True (по умолчанию): проверяет и возвращает, что пошлёт,
    НИЧЕГО не отправляя. Реальная отправка только при dry_run=False
    И confirm=True (осознанное действие пользователя).
    """
    v = validate_text(text)
    if not v["ok"]:
        return {"ok": False, "stage": "validate", "reason": v["reason"]}

    payload = {
        "text": text,
        "instruments": tickers or [],
        "images": [],
    }
    if dry_run:
        return {
            "ok": True, "stage": "dry_run",
            "message": "DRY RUN — ничего не отправлено",
            "payload": payload,
            "confirm_required": True,
        }

    if cookie is None:
        if cookie_source:
            cookie = load_cookies_from_file(cookie_source)
        else:
            try:
                cookie = load_cookies_from_firefox()
            except Exception as e:
                return {"ok": False, "stage": "auth",
                        "reason": f"нужны куки сессии: {e}"}

    if not confirm:
        return {
            "ok": False, "stage": "confirm",
            "reason": "нужен confirm=True для реальной отправки",
            "payload": payload,
        }

    rl = _check_rate_limit()
    if not rl["ok"]:
        return {"ok": False, "stage": "rate_limit", "reason": rl["reason"]}

    resp = _http_post("/post", payload, cookie)
    if resp is None:
        return {"ok": False, "stage": "network",
                "reason": "запрос не прошёл (сеть/куки истекли)"}
    _STATE.posted_hashes.add(v["hash"])
    _STATE.last_action_ts = time.time()
    _STATE.actions_in_window += 1
    return {"ok": True, "stage": "posted", "response": resp}


def like_post(post_id: str, cookie: str, dry_run: bool = True,
              confirm: bool = False) -> dict[str, Any]:
    """Лайк поста (только с куками; dry_run по умолчанию)."""
    if dry_run:
        return {"ok": True, "stage": "dry_run",
                "message": f"DRY RUN — лайк поста {post_id} не отправлен"}
    if not confirm:
        return {"ok": False, "stage": "confirm", "reason": "нужен confirm=True"}
    rl = _check_rate_limit()
    if not rl["ok"]:
        return {"ok": False, "stage": "rate_limit", "reason": rl["reason"]}
    resp = _http_post(f"/post/{post_id}/like", {}, cookie)
    if resp is None:
        return {"ok": False, "stage": "network", "reason": "запрос не прошёл"}
    _STATE.last_action_ts = time.time()
    _STATE.actions_in_minute = getattr(_STATE, "actions_in_minute", 0) + 1
    return {"ok": True, "stage": "liked", "response": resp}


def comment(post_id: str, text: str, cookie: str, dry_run: bool = True,
            confirm: bool = False) -> dict[str, Any]:
    """Комментарий к посту (dry_run по умолчанию)."""
    v = validate_text(text)
    if not v["ok"]:
        return {"ok": False, "stage": "validate", "reason": v["reason"]}
    if dry_run:
        return {"ok": True, "stage": "dry_run",
                "message": f"DRY RUN — комментарий к {post_id} не отправлен"}
    if not confirm:
        return {"ok": False, "stage": "confirm", "reason": "нужен confirm=True"}
    rl = _check_rate_limit()
    if not rl["ok"]:
        return {"ok": False, "stage": "rate_limit", "reason": rl["reason"]}
    resp = _http_post(f"/post/{post_id}/comment", {"text": text}, cookie)
    if resp is None:
        return {"ok": False, "stage": "network", "reason": "запрос не прошёл"}
    return {"ok": True, "stage": "commented", "response": resp}


__all__ = [
    "WriteState",
    "comment",
    "like_post",
    "load_cookies_from_file",
    "load_cookies_from_firefox",
    "post_text",
    "validate_text",
]
