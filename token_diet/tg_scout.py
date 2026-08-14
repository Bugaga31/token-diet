"""TG Scout — живая охота за Telegram-каналами.

Не «сидим на готовом списке»: scout() сам ищет каналы по теме через
глобальный поиск, для каждого найденного канала достаёт метаданные
(подписчики, username, описание, активность) через get_entity,
оценивает качество по формуле и возвращает рейтинг.

Главное: лучшие каналы АВТОМАТИЧЕСКИ добавляются в USERNAME_CHANNELS
(файл telegram_monitor.py), чтобы демон и by_username читали их дальше
без участия человека. Это и есть «сам искал, сам читал, сам запомнил».

Безопасно: всё через try/except, сеть через DPI может молчать — ничего
не висит, каждый вызов с таймаутом.

Команда:
    python3 -m token_diet.memory_cli tg-scout "инвестиции облигации" --limit 8
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

logging.getLogger("telethon").setLevel(logging.CRITICAL)

from .telegram_monitor import USERNAME_CHANNELS, global_search

# Базовый файл, куда пишем новые каналы (для авто-регистрации):
# telegram_monitor.py — там живёт USERNAME_CHANNELS
_CHANNELS_FILE = Path(__file__).resolve().parent / "telegram_monitor.py"


def _run_with_timeout(fn: Callable[[], Any], seconds: float) -> Any:
    """Сетевой вызов с жёстким таймаутом — сеть ТГ может молчать."""
    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["res"] = fn()
        except Exception as exc:
            box["err"] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=seconds)
    if t.is_alive():
        return None
    if "err" in box:
        return None
    return box.get("res")


def _score_channel(channel: dict[str, Any]) -> float:
    """Оценить канал: подписчики + активность + важность постов.

    0..100. Подписчики лог-шкала (10K → ~40 баллов, 100K → ~55),
    свежесть постов +30, важность контента +30. Без сети не падает.
    """
    members = channel.get("members") or 0
    score = 0.0
    # лог-шкала подписчиков: 1K=30, 10K=40, 100K=50, 1M=60
    if members > 0:
        import math

        score += 30 + min(30, math.log10(members) * 10)

    # свежесть: канал с недавними постами — живой
    fresh = 0
    for r in channel.get("posts", [])[:5]:
        date = str(r.get("date", ""))
        if date and "2026-08" in date:  # последний месяц
            fresh += 1
    score += min(15, fresh * 3)

    # важность контента (телеграфный стиль: цифры, тикеры, $)
    important = sum(1 for r in channel.get("posts", [])[:5]
                    if r.get("important"))
    score += min(15, important * 3)

    return round(min(100, score), 1)


async def _collect_channel_meta(client: Any, channel: dict[str, Any]) -> dict[str, Any] | None:
    """Достать полные метаданные канала через get_entity (async).

    Надёжнее всего по username (так работает by_username);
    если username нет — пробуем по id через PeerChannel.
    """
    from telethon.tl.types import PeerChannel

    entity = None
    uname = channel.get("username")
    if uname:
        try:
            entity = await client.get_entity(uname)
        except Exception:
            entity = None
    if entity is None and channel.get("channel_id"):
        try:
            entity = await client.get_entity(PeerChannel(channel["channel_id"]))
        except Exception:
            entity = None
    if entity is None:
        return None
    username = getattr(entity, "username", None)
    members = getattr(entity, "participants_count", None)
    title = getattr(entity, "title", None) or "?"
    about = getattr(entity, "about", None) or ""
    channel_id = getattr(entity, "id", None) or channel.get("channel_id")

    # полные метаданные (подписчики, описание) — get_entity даёт не всё
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest

        full = await client(GetFullChannelRequest(entity))
        fc = full.full_chat
        if members is None:
            members = getattr(fc, "participants_count", None)
        if not about:
            about = getattr(fc, "about", None) or ""
    except Exception:
        pass

    # активность: последние 5 постов
    posts: list[dict] = []
    try:
        async for msg in client.iter_messages(entity, limit=5):
            text = (getattr(msg, "message", None) or "").strip()
            if text:
                posts.append({
                    "date": str(msg.date),
                    "text": text[:300],
                    "important": bool(
                        any(k in text.lower() for k in
                            ("$", "%", "руб", "млн", "млрд", "отчет", "отчёт",
                             "дивиденд", "ставка", "рейтинг", "сделка"))
                    ),
                })
    except Exception:
        pass

    return {
        "channel_id": channel_id,
        "title": title,
        "username": username,
        "members": members,
        "about": about[:200],
        "posts": posts,
    "paragonzone": "Paragon",  # авто: 2,030 подп.
    "centralbank_russia": "Банк России",  # авто: 245,104 подп.
    "rian_ru": "РИА Новости",  # авто: 3,027,350 подп.
    "forbesrussia": "Forbes Russia",  # авто: 235,042 подп.
    "vibecoding_anymodel": "AnyModel чат вайбкодеров",  # авто: 3,771 подп.
    "banki_economy": "Русская экономика",  # авто: 1,059,575 подп.
    }


def _register_channels(new_channels: list[dict[str, Any]]) -> int:
    """Добавить новые каналы в USERNAME_CHANNELS (файл telegram_monitor.py).

    Не трогаем существующие. Пишем перед закрывающей скобкой словаря.
    Возвращает, сколько добавили.
    """
    import re
    from pathlib import Path

    added = 0
    lines = Path(_CHANNELS_FILE).read_text(encoding="utf-8").splitlines()
    # найдём строку с закрывающей скобкой USERNAME_CHANNELS (отступ 0)
    close_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "}":
            close_idx = i
            break
    if close_idx is None:
        return 0

    insert: list[str] = []
    for ch in new_channels:
        uname = ch.get("username")
        title = ch.get("title", "?")
        if not uname:
            continue
        if uname in USERNAME_CHANNELS:
            continue
        members = ch.get("members") or 0
        comment = f"  # авто: {members:,} подп." if members else ""
        insert.append(f'    "{uname}": "{title}",{comment}')
        added += 1

    if not insert:
        return 0
    lines[close_idx:close_idx] = insert
    Path(_CHANNELS_FILE).write_text("\n".join(lines), encoding="utf-8")
    return added


def scout(topic: str, limit: int = 8, register: bool = True) -> dict[str, Any]:
    """Найти каналы по теме, оценить, (опционально) авто-зарегистрировать.

    Returns {"status", "topic", "found", "channels", "registered"}.
    channels: список {"channel_id", "title", "username", "members",
                       "score", "about", "posts"}.
    """
    res = _run_with_timeout(lambda: global_search(topic, limit=limit * 4),
                            seconds=40)
    if not isinstance(res, dict) or res.get("status") != "ok":
        return {"status": "error",
                "error": "глобальный поиск не ответил (сеть ТГ нестабильна)",
                "topic": topic}

    # собираем уникальные каналы из результатов
    by_id: dict[int, dict] = {}
    for r in res.get("results", []):
        cid = r.get("channel_id")
        if cid is None:
            continue
        by_id.setdefault(cid, {
            "channel_id": cid,
            "title": r.get("channel", "?"),
            "username": r.get("username"),
            "members": r.get("members"),
            "posts": [r],
        })["posts"].append(r)

    if not by_id:
        return {"status": "ok", "topic": topic, "found": 0,
                "channels": [], "registered": 0}

    # достаём полные метаданные по каждому каналу (с таймаутом на канал)
    from .telegram_monitor import _load_creds, _find_live_session, _usable_session
    from telethon import TelegramClient

    creds = _load_creds()
    session = _find_live_session()
    if not creds or session is None:
        # без живой сессии — только то, что уже есть из поиска
        channels = []
        for c in by_id.values():
            channels.append({
                "channel_id": c["channel_id"],
                "title": c["title"],
                "username": c["username"],
                "members": c["members"],
                "score": _score_channel(c),
                "about": "",
                "posts": c["posts"][:5],
            })
        channels.sort(key=lambda x: -x["score"])
        return {"status": "ok", "topic": topic, "found": len(channels),
                "channels": channels[:limit], "registered": 0}

    api_id, api_hash = creds
    usable = _usable_session(session)

    def _fetch_meta() -> list[dict[str, Any]]:
        import asyncio

        async def _run() -> list[dict[str, Any]]:
            client = TelegramClient(str(usable), api_id, api_hash)
            await client.connect()
            try:
                metas = []
                for cid, ch in list(by_id.items()):
                    meta = await _collect_channel_meta(client, ch)
                    if meta:
                        metas.append(meta)
                return metas
            finally:
                await client.disconnect()

        return asyncio.run(_run())

    metas = _run_with_timeout(_fetch_meta, seconds=45) or []

    channels: list[dict[str, Any]] = []
    for m in metas:
        base = by_id.get(m["channel_id"], {})
        base.update(m)
        base["score"] = _score_channel(base)
        channels.append(base)
    # те, что не получили мету (get_entity не сработал) — тоже учитываем
    got = {c["channel_id"] for c in channels}
    for cid, c in by_id.items():
        if cid not in got:
            c["score"] = _score_channel(c)
            channels.append(c)

    channels.sort(key=lambda x: -x.get("score", 0))

    registered = _register_channels(channels) if register else 0
    return {"status": "ok", "topic": topic, "found": len(channels),
            "channels": channels[:limit], "registered": registered}


def scout_block(topic: str, limit: int = 8) -> str:
    """Компактный текстовый блок рейтинга каналов для промпта/человека."""
    r = scout(topic, limit=limit)
    if r["status"] != "ok":
        return f"(охота за каналами: {r.get('error', 'недоступно')})"
    if not r["channels"]:
        return f"(по теме «{topic}» каналов не найдено)"
    lines = [f"=== ОХОТА ЗА КАНАЛАМИ: {topic} ==="]
    for ch in r["channels"]:
        mem = f"{ch.get('members', 0):,}" if ch.get("members") else "?"
        lines.append(
            f"{ch.get('score', 0):>5.1f}/100  {ch.get('title', '?')} "
            f"(👥{mem}) @{ch.get('username') or '—'}"
        )
        about = (ch.get("about") or "").strip()
        if about:
            lines.append(f"        {about[:110]}")
    if r.get("registered"):
        lines.append(f"\n➕ авто-зарегистрировано каналов: {r['registered']}")
    return "\n".join(lines)


__all__ = ["scout", "scout_block", "_score_channel", "_register_channels"]