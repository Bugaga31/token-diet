"""telegram_monitor — get Telegram information BEFORE everyone else.

Two modes:
  collect  — one-shot: grab the latest posts from top channels NOW.
  watch    — long-running daemon: Telegram pushes new posts to us the
             moment they appear (~1s latency), we filter by importance
             and save to ~/FRESH_NEWS.md + the Library (Kingston).

The whole point (owner's goal): speed. Information that arrives before
everyone else's is worth the most. No SMS codes — live session first
(lesson from Obsidian memory).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from telethon import TelegramClient, events
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False

# Живые сессии (урок из памяти: сначала сессии, потом SMS)
SESSION_CANDIDATES = [
    Path.home() / ".telegram-mcp" / "telegram_live.session",
    Path.home() / ".mcp-telegram" / "userbot_session.session",
    Path.home() / ".telegram-mcp" / "telegram_default.session",
]
CREDS_PATH = Path.home() / "221099698.json"

# Куда пишем свежак — плоский файл, читается в любом разговоре
FRESH_FILE = Path.home() / "FRESH_NEWS.md"

# ── Каналы-источники: фрагменты названий для матча по диалогам аккаунта ─
# (username угадывать нельзя — берём каналы, на которые уже подписан
# владелец, по фрагменту названия. Надёжно и без лишних поисков.)
INVEST_CHANNEL_FRAGMENTS = [
    "банк росси", "moex", "минфин", "marketmaker", "bonds lab",
    "коган", "smart-lab", "смартлаб", "invest heroes", "рынкиденьгивласть",
    "markettwits", "рбк инвестиции", "бкс экспресс", "т-инвестиции",
    "финам инвестиции",
]

AI_CHANNEL_FRAGMENTS = [
    "claude-api", "промптологи", "нейробаза", "ai-агент", "максим скороход",
    "нейро", "ии ", "gpt", "промпт",
]


async def _resolve_dialogs(client, fragments: list[str]) -> list[tuple]:
    """Match subscribed dialogs by name fragments. Returns [(entity, name)].

    Raises on network errors — the caller retries. Silent swallowing hid a
    connection flakiness bug before (lesson: don't hide real errors).
    """
    found: list[tuple] = []
    lower = [f.lower() for f in fragments]
    async for dialog in client.iter_dialogs():
        name = (dialog.name or "").strip()
        if not name or not (dialog.is_channel or dialog.is_group):
            continue
        low = name.lower()
        if any(f in low for f in lower):
            found.append((dialog.entity, name))
    return found

# ── Фильтр важности: если в посте есть хоть одно слово → он ценен ─────────
IMPORTANT_KEYWORDS = [
    # рынок РФ / ставка
    "цб", "ставк", "ключев", "инфляци", "курс рубл", "рубль",
    # бумаги и события
    "полюс", "plzl", "сбер", "sber", "газпром", "яндекс", "облигаци",
    "дивиденд", "размещени", "выкуп", "сплит", "отсечк", "доходност",
    # макро-события
    "трамп", "санкци", "нефть", "золото", "геополит",
    # нейросети/ИИ
    "claude", "opus", "sonnet", "chatgpt", "gpt", "deepseek", "нейросет",
    "модел", "антропик", "openai", "ai ", "ии ",
    # срочное
    "срочно", "экстренн", "авария", "сбой", "запрет",
]


def _load_creds() -> tuple[int, str] | None:
    try:
        d = json.loads(CREDS_PATH.read_text(encoding="utf-8"))
        api_id, api_hash = d.get("app_id"), d.get("app_hash")
        if api_id and api_hash:
            return int(api_id), str(api_hash)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return None


def _find_live_session() -> Path | None:
    creds = _load_creds()
    if not creds or not HAS_TELETHON:
        return None
    api_id, api_hash = creds
    for path in SESSION_CANDIDATES:
        if not path.exists():
            continue
        try:
            async def _check():
                client = TelegramClient(str(path), api_id, api_hash)
                await client.connect()
                try:
                    return bool(await client.is_user_authorized())
                finally:
                    await client.disconnect()
            if asyncio.run(_check()):
                return path
        except Exception:
            continue
    return None


def _is_important(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in IMPORTANT_KEYWORDS)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _append_fresh(title: str, text: str, channel: str) -> None:
    """Append one post to FRESH_NEWS.md (keep last ~200 entries)."""
    try:
        lines = FRESH_FILE.read_text(encoding="utf-8").splitlines() \
            if FRESH_FILE.exists() else []
        entry = [
            f"\n## [{_stamp()}] {channel} — {title}",
            text[:600],
        ]
        # простой лимит: оставляем хвост ~200 строк блока
        lines += entry
        if len(lines) > 2600:
            lines = lines[-2600:]
        FRESH_FILE.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def _save_library(title: str, text: str, source: str) -> None:
    try:
        from .library import Library
        Library().add(
            f"TG-свежак: {title}",
            f"{source}\n\n{text[:1500]}",
            source=f"Telegram live ({source})",
            kind="knowledge",
        )
    except Exception:
        pass


# ── 1. Разовый сбор свежайшего ────────────────────────────────────────────
async def _collect_impl(client, fragments: list[str], per_channel: int) -> list[dict]:
    out: list[dict] = []
    for entity, name in await _resolve_dialogs(client, fragments):
        try:
            async for msg in client.iter_messages(entity, limit=per_channel):
                text = (msg.text or "").strip()
                if not text:
                    continue
                out.append({
                    "channel": name,
                    "date": str(msg.date),
                    "text": text[:400],
                    "important": _is_important(text),
                })
        except Exception:
            continue
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def collect_fresh(per_channel: int = 3, include_ai: bool = True) -> dict:
    """One-shot: latest posts from top invest + AI channels."""
    if not HAS_TELETHON:
        return {"status": "error", "error": "telethon не установлен"}
    creds = _load_creds()
    if not creds:
        return {"status": "error", "error": "нет api_id/api_hash"}
    session = _find_live_session()
    if session is None:
        return {"status": "no_session", "error": "нет живой сессии"}

    fragments = list(INVEST_CHANNEL_FRAGMENTS)
    if include_ai:
        fragments += AI_CHANNEL_FRAGMENTS

    api_id, api_hash = creds
    results: list[dict] = []
    for attempt in range(1, 4):
        try:
            async def _run():
                client = TelegramClient(str(session), api_id, api_hash)
                await client.connect()
                try:
                    return await _collect_impl(client, fragments, per_channel)
                finally:
                    await client.disconnect()
            results = asyncio.run(_run())
            break
        except Exception as e:
            if attempt == 3:
                return {"status": "error",
                        "error": f"сеть Telegram нестабильна: {str(e)[:120]}"}
            time.sleep(2 * attempt)

    return {"status": "ok", "session": str(session),
            "found": len(results), "results": results}


# ── 2. Постоянный демон: ловим новое мгновенно ────────────────────────────
async def _watch_impl(client, fragments: list[str], save_to_lib: bool) -> None:
    # Ретрай резолва — сеть Telegram капризна, с первого раза может не дать
    # список диалогов (урок: раньше молча возвращали пусто).
    resolved: list[tuple] = []
    for _ in range(3):
        try:
            resolved = await _resolve_dialogs(client, fragments)
            if resolved:
                break
        except Exception:
            pass
        await asyncio.sleep(4)
    if not resolved:
        print("⚠️ Каналы не найдены: проверьте сеть и подписки, затем перезапустите.",
              flush=True)
        return
    entities = [e for e, _ in resolved]
    print(f"📡 Слушаю {len(entities)} каналов. Новое появляется мгновенно.",
          flush=True)
    print(f"   Свежак пишется в {FRESH_FILE}", flush=True)

    async def handler(event):
        msg = event.message
        text = (msg.text or "").strip()
        if not text:
            return
        try:
            channel_name = (await event.get_chat()).title or "?"
        except Exception:
            channel_name = "?"
        title = text.splitlines()[0][:80]
        if _is_important(text):
            _append_fresh(title, text, channel_name)
            if save_to_lib:
                _save_library(title, text, channel_name)
            print(f"⚡ [{_stamp()}] {channel_name}: {title}", flush=True)
        else:
            # не важное — только в память процесса, не в файл
            print(f"· [{_stamp()}] {channel_name}: {title}", flush=True)

    async def heartbeat():
        """Показывает живость демона каждые 5 минут."""
        n = 0
        while True:
            await asyncio.sleep(300)
            n += 1
            print(f"💓 [{_stamp()}] демон жив ({n * 5} мин на связи, "
                  f"каналов: {len(entities)})", flush=True)

    client.add_event_handler(handler, events.NewMessage(chats=entities))
    asyncio.ensure_future(heartbeat())
    try:
        await client.run_until_disconnected()
    finally:
        await client.disconnect()


def watch(save_to_lib: bool = True) -> dict:
    """Long-running daemon. Call from CLI; Ctrl+C / kill to stop."""
    if not HAS_TELETHON:
        return {"status": "error", "error": "telethon не установлен"}
    creds = _load_creds()
    if not creds:
        return {"status": "error", "error": "нет api_id/api_hash"}
    session = _find_live_session()
    if session is None:
        return {"status": "no_session", "error": "нет живой сессии"}

    api_id, api_hash = creds
    fragments = list(INVEST_CHANNEL_FRAGMENTS) + AI_CHANNEL_FRAGMENTS

    # Демон с авто-переподключением при обрывах.
    # ВАЖНО: клиент создаётся ВНУТРИ asyncio.run — Telethon привязывает
    # клиента к event loop, создание вне loop ломает чтение диалогов.
    while True:
        try:
            async def _run_watch():
                client = TelegramClient(str(session), api_id, api_hash)
                await client.connect()
                try:
                    await _watch_impl(client, fragments, save_to_lib)
                finally:
                    await client.disconnect()
            asyncio.run(_run_watch())
            break  # штатный выход
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"⚠️ обрыв: {str(e)[:100]}. Переподключаюсь через 10 c...")
            time.sleep(10)
    return {"status": "stopped"}


__all__ = ["collect_fresh", "watch", "INVEST_CHANNEL_FRAGMENTS",
           "AI_CHANNEL_FRAGMENTS", "FRESH_FILE"]
