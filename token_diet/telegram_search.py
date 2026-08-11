"""telegram_search — search Telegram channels/groups and save findings.

The owner wanted a GOOD search across Telegram channels. This module:
  1. Connects via the live session (telegram_live.session — Артём,
     +PHONE_FROM_ENV), falling back to other local sessions.
  2. Searches ALL dialogs (or a curated subset) for a query using
     Telegram's native search (fast, server-side).
  3. Collects the best messages (title, channel, date, text).
  4. Optionally stores the digest into the Library (RAG vault on the
     Kingston disk) so the knowledge is permanent.

LESSON (from Obsidian memory): NEVER send SMS codes first — check live
sessions. The live session lives at ~/.telegram-mcp/telegram_live.session.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

try:
    from telethon import TelegramClient
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False

# Живые сессии (в порядке предпочтения) — УРОК из памяти:
# сначала проверяем сессии, только потом думаем про SMS.
SESSION_CANDIDATES = [
    Path.home() / ".telegram-mcp" / "telegram_live.session",
    Path.home() / ".mcp-telegram" / "userbot_session.session",
    Path.home() / ".telegram-mcp" / "telegram_default.session",
]

CREDS_PATH = Path.home() / "221099698.json"


def _load_creds() -> tuple[int, str] | None:
    """api_id + api_hash from the local creds file. Never prints them."""
    try:
        d = json.loads(CREDS_PATH.read_text(encoding="utf-8"))
        api_id = d.get("app_id")
        api_hash = d.get("app_hash")
        if api_id and api_hash:
            return int(api_id), str(api_hash)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return None


def _find_live_session() -> Path | None:
    """Return the first session that actually authorizes. No SMS needed."""
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


async def _search_impl(
    query: str,
    session_path: Path,
    api_id: int,
    api_hash: str,
    limit_per_dialog: int = 5,
    max_dialogs: int = 50,
    dialog_filter: list[str] | None = None,
) -> list[dict]:
    """Search every dialog for `query`, collect top messages."""
    client = TelegramClient(str(session_path), api_id, api_hash)
    results: list[dict] = []
    try:
        await client.connect()

        # Собираем диалоги и ставим каналы/группы вперёд — в личных
        # чатах искать новости бессмысленно.
        dialogs = [d async for d in client.iter_dialogs()]

        def _rank(d):
            name = (d.name or "").lower()
            is_channel = 1 if d.is_channel else 0
            # Каналы про ИИ/нейросети/финансы — в самый верх
            ai_hint = any(k in name for k in ["нейро", "ии", "ai", "gpt", "claude", "промпт", "llm", "бизнес", "инвест", "market"])
            return (is_channel * 10 + (2 if ai_hint else 0), d.date or 0)

        dialogs.sort(key=_rank, reverse=True)

        scanned = 0
        for dialog in dialogs:
            if scanned >= max_dialogs:
                break
            name = (dialog.name or "").strip()
            if not name:
                continue
            # optional filter: only search named dialogs
            if dialog_filter:
                if not any(f.lower() in name.lower() for f in dialog_filter):
                    continue
            scanned += 1
            try:
                async for msg in client.iter_messages(
                    dialog.id, search=query, limit=limit_per_dialog,
                ):
                    text = (msg.text or "").strip()
                    if not text:
                        continue
                    results.append({
                        "dialog": name,
                        "id": dialog.id,
                        "date": str(msg.date),
                        "text": text[:500],
                        "sender": (msg.sender_id or ""),
                    })
            except Exception:
                continue
        results.sort(key=lambda r: r["date"], reverse=True)
    finally:
        await client.disconnect()
    return results


def search_telegram(
    query: str,
    limit_per_dialog: int = 5,
    max_dialogs: int = 50,
    dialog_filter: list[str] | None = None,
    save: bool = False,
) -> dict:
    """Public API: search Telegram, optionally save to Library.

    Returns {"status", "session", "query", "found": n, "results": [...]}.
    Honest: returns status="no_session" if nothing authorizes — without
    ever prompting for an SMS code.
    """
    if not HAS_TELETHON:
        return {"status": "error", "error": "telethon не установлен"}
    creds = _load_creds()
    if not creds:
        return {"status": "error", "error": "нет api_id/api_hash (221099698.json)"}
    session_path = _find_live_session()
    if session_path is None:
        return {
            "status": "no_session",
            "error": ("нет живой сессии. Живые: ~/.telegram-mcp/telegram_live.session. "
                      "SMS-код слать не буду — сначала поправьте сессию."),
        }
    api_id, api_hash = creds
    # Telegram любит рвать соединение при частых запросах — ретраим с паузой.
    results: list[dict] = []
    for attempt in range(1, 4):
        try:
            results = asyncio.run(_search_impl(
                query, session_path, api_id, api_hash,
                limit_per_dialog=limit_per_dialog,
                max_dialogs=max_dialogs,
                dialog_filter=dialog_filter,
            ))
            break
        except Exception as e:
            if attempt == 3:
                return {
                    "status": "error",
                    "error": f"сеть Telegram нестабильна ({type(e).__name__}): "
                             f"{str(e)[:120]}. Попробуйте ещё раз через минуту.",
                }
            time.sleep(2 * attempt)
    out = {
        "status": "ok",
        "session": str(session_path),
        "query": query,
        "found": len(results),
        "results": results,
    }
    if save and results:
        _save_to_library(query, results)
        out["saved"] = True
    return out


def _save_to_library(query: str, results: list[dict]) -> None:
    """Persist findings into the Library (RAG vault on Kingston)."""
    from .library import Library

    lib = Library()
    body_lines = [f"ЗАПРОС: {query}", f"НАЙДЕНО: {len(results)}", ""]
    for r in results[:30]:
        body_lines.append(
            f"### [{r['dialog']}] {r['date']}\n{r['text']}\n"
        )
    lib.add(
        f"Telegram поиск: {query}",
        "\n".join(body_lines),
        source="Telegram search",
        kind="knowledge",
    )


__all__ = ["search_telegram"]
