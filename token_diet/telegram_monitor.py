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
    "нейро", "gpt", "промпт",
]

# Мусорные чаты, которые ловятся по фрагментам, но не несут новостной ценности
BLACKLIST_FRAGMENTS = ["комментари", "чат-болт", "флудилк", "отзовик"]


async def _resolve_dialogs(client, fragments: list[str]) -> list[tuple]:
    """Match subscribed dialogs by name fragments. Returns [(entity, name)].

    Raises on network errors — the caller retries. Silent swallowing hid a
    connection flakiness bug before (lesson: don't hide real errors).
    """
    found: list[tuple] = []
    lower = [f.lower() for f in fragments]
    black = [b.lower() for b in BLACKLIST_FRAGMENTS]
    async for dialog in client.iter_dialogs():
        name = (dialog.name or "").strip()
        if not name or not (dialog.is_channel or dialog.is_group):
            continue
        low = name.lower()
        if any(b in low for b in black):
            continue
        if any(f in low for f in lower):
            found.append((dialog.entity, name))
    return found

# ── Темы для глобального поиска по всей платформе ─────────────────────────
GLOBAL_QUERIES = [
    "ставка ЦБ", "ключевая ставка", "инфляция Россия",
    "дивиденды российские акции", "облигации ОФЗ", "Мосбиржа",
    "полюс акции", "сбер акции", "газпром акции", "яндекс акции",
    "нейросеть claude", "chatgpt новая модель", "deepseek",
    "санкции Россия", "курс рубля",
]


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


# ── Каналы по username — get_entity работает для ЛЮБОГО публичного ────────
# канала, даже если на него не подписан. Это даёт охват сверх подписок.
# (username взят из telegram_market_feed.MARKET_CHANNELS — проверенные)
USERNAME_CHANNELS: dict[str, str] = {
    "bankrossii": "Банк России",
    "MoscowExchangeOfficial": "MOEX",
    "minfin": "Минфин России",
    "government_rus": "Правительство России",
    "MMInsights": "MMI",
    "Bonds_lab": "Bonds lab",
    "bitkogan": "Евгений Коган",
    "smartlabnews": "СМАРТЛАБ",
    "newssmartlab": "СМАРТЛАБ НОВОСТИ",
    "bondsmartlab": "СМАРТЛАБ ОБЛИГАЦИИ",
    "invest_heroes": "Invest Heroes",
    "AK47pfl": "РынкиДеньгиВласть",
    "markettwits": "MarketTwits",
    "selfinvestor": "РБК Инвестиции",
    "rbc_invest": "РБК Инвестиции",
    "bcs_express": "БКС Экспресс",
    "t_bank_invest": "Т-Инвестиции",
    "finam_invest": "Финам Инвестиции",
}


async def _by_username_impl(client, limit: int) -> list[dict]:
    """Собрать свежайшее из каналов по username — даже неподписанных.

    get_entity(username) работает для любого публичного канала.
    """
    out: list[dict] = []
    for username, name in USERNAME_CHANNELS.items():
        try:
            entity = await client.get_entity(username)
        except Exception:
            continue  # канал недоступен/приватный — пропускаем
        try:
            async for msg in client.iter_messages(entity, limit=limit):
                text = (msg.text or "").strip()
                if not text:
                    continue
                out.append({
                    "channel": name,
                    "date": str(msg.date),
                    "text": text[:400],
                    "important": _is_important(text),
                    "source": "username",
                })
        except Exception:
            continue
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


async def _global_search_impl(client, query: str, limit: int) -> list[dict]:
    """Глобальный поиск по ВСЕМ публичным сообщениям Telegram.

    SearchGlobalRequest ищет по всей платформе — включая каналы,
    на которые мы не подписаны и про которые даже не знаем.
    """
    from telethon.tl.functions.messages import SearchGlobalRequest
    from telethon.tl.types import InputMessagesFilterEmpty, InputPeerEmpty

    try:
        res = await client(SearchGlobalRequest(
            q=query,
            filter=InputMessagesFilterEmpty(),
            min_date=None, max_date=None, offset_rate=0,
            offset_peer=InputPeerEmpty(), offset_id=0, limit=limit,
        ))
    except Exception:
        return []

    # peer_id → chat: соберём карту по всем chats/пользователям результата
    chats_by_id: dict = {}
    for c in getattr(res, "chats", []) or []:
        chats_by_id[c.id] = c
    for u in getattr(res, "users", []) or []:
        chats_by_id[u.id] = u

    out: list[dict] = []
    for msg in res.messages:
        text = (getattr(msg, "message", "") or "").strip()
        if not text:
            continue
        peer = getattr(msg, "peer_id", None)
        cid = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None) \
            or getattr(peer, "user_id", None)
        chat = chats_by_id.get(cid)
        name = getattr(chat, "title", None) or getattr(chat, "first_name", None) \
            or getattr(chat, "username", None) or "?"
        out.append({
            "channel": name,
            "date": str(msg.date),
            "text": text[:400],
            "important": _is_important(text),
            "source": "global",
        })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


async def _global_searches_impl(client, limit: int) -> list[dict]:
    """Прогнать глобальный поиск по ключевым темам — улов со всей платформы."""
    out: list[dict] = []
    for q in GLOBAL_QUERIES:
        out += await _global_search_impl(client, q, limit)
    return out


def _usable_session(session: Path) -> Path:
    """Always use a temp copy for one-shot commands.

    The watch daemon holds the original session DB open (it writes state
    on every disconnect), so ANY parallel connect to the original risks
    'database is locked'. A copy is always safe and never disturbs the
    daemon. Copy is tiny (~tens of KB) and cheap.
    """
    import shutil
    import tempfile

    tmp = Path(tempfile.gettempdir()) / f"tg_sess_{time.time_ns()}.session"
    try:
        shutil.copy2(session, tmp)
        return tmp
    except OSError:
        return session


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
        for attempt in range(3):  # сеть Telegram капризна — ретраим каждую сессию
            try:
                async def _check():
                    usable = _usable_session(path)
                    client = TelegramClient(str(usable), api_id, api_hash)
                    await client.connect()
                    try:
                        return bool(await client.is_user_authorized())
                    finally:
                        await client.disconnect()
                if asyncio.run(_check()):
                    return path
            except Exception:
                pass
            time.sleep(2 * (attempt + 1))
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
    usable = _usable_session(session)
    results: list[dict] = []
    for attempt in range(1, 4):
        try:
            async def _run():
                client = TelegramClient(str(usable), api_id, api_hash)
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

    async def extra_scan():
        """Периодически: username-каналы + глобальный поиск (неподписанные).

        Live-события ловят только подписанные каналы. А чтобы не упускать
        остальную платформу, каждые 10 минут прогоняем username-каналы
        (get_entity работает для любых публичных) и глобальные темы.
        """
        seen: set[str] = set()
        while True:
            await asyncio.sleep(600)
            try:
                found: list[dict] = []
                found += await _by_username_impl(client, 1)
                found += await _global_searches_impl(client, 2)
                for r in found:
                    if not r.get("important"):
                        continue
                    key = f"{r.get('channel')}|{r.get('text')[:60]}"
                    if key in seen:
                        continue
                    seen.add(key)
                    title = r["text"].splitlines()[0][:80]
                    _append_fresh(title, r["text"], r["channel"])
                    if save_to_lib:
                        _save_library(title, r["text"], r["channel"])
                    print(f"🌐 [{_stamp()}] {r['channel']}: {title}", flush=True)
            except Exception as e:
                print(f"⚠️ доп-скан: {str(e)[:80]}", flush=True)

    client.add_event_handler(handler, events.NewMessage(chats=entities))
    asyncio.ensure_future(heartbeat())
    asyncio.ensure_future(extra_scan())
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


def by_username(limit: int = 3) -> dict:
    """One-shot: latest posts from username-channels (even unsubscribed)."""
    if not HAS_TELETHON:
        return {"status": "error", "error": "telethon не установлен"}
    creds = _load_creds()
    if not creds:
        return {"status": "error", "error": "нет api_id/api_hash"}
    session = _find_live_session()
    if session is None:
        return {"status": "no_session", "error": "нет живой сессии"}

    api_id, api_hash = creds
    usable = _usable_session(session)
    results: list[dict] = []
    for attempt in range(1, 4):
        try:
            async def _run():
                client = TelegramClient(str(usable), api_id, api_hash)
                await client.connect()
                try:
                    return await _by_username_impl(client, limit)
                finally:
                    await client.disconnect()
            results = asyncio.run(_run())
            break
        except Exception as e:
            if attempt == 3:
                return {"status": "error",
                        "error": f"сеть Telegram нестабильна: {str(e)[:120]}"}
            time.sleep(2 * attempt)
    return {"status": "ok", "found": len(results), "results": results}


def global_search(query: str, limit: int = 10) -> dict:
    """One-shot: search ALL public Telegram, including unsubscribed channels."""
    if not HAS_TELETHON:
        return {"status": "error", "error": "telethon не установлен"}
    creds = _load_creds()
    if not creds:
        return {"status": "error", "error": "нет api_id/api_hash"}
    session = _find_live_session()
    if session is None:
        return {"status": "no_session", "error": "нет живой сессии"}

    api_id, api_hash = creds
    usable = _usable_session(session)
    results: list[dict] = []
    for attempt in range(1, 4):
        try:
            async def _run():
                client = TelegramClient(str(usable), api_id, api_hash)
                await client.connect()
                try:
                    return await _global_search_impl(client, query, limit)
                finally:
                    await client.disconnect()
            results = asyncio.run(_run())
            break
        except Exception as e:
            if attempt == 3:
                return {"status": "error",
                        "error": f"сеть Telegram нестабильна: {str(e)[:120]}"}
            time.sleep(2 * attempt)
    return {"status": "ok", "query": query, "found": len(results),
            "results": results}


__all__ = ["collect_fresh", "by_username", "global_search", "watch",
           "INVEST_CHANNEL_FRAGMENTS", "AI_CHANNEL_FRAGMENTS",
           "USERNAME_CHANNELS", "GLOBAL_QUERIES", "FRESH_FILE"]
