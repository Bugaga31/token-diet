"""Telegram Commander — «вы» в Telegram. Бот-командир портфеля.

Запускает самого себя как Telegram-бота: отвечает на команды владельца,
показывает портфель, проверяет стопы/тейки, даёт полную картину по любому
тикеру и болтает через DeepSeek с контекстом портфеля.

Безопасность (железные правила):
  - Токен бота ТОЛЬКО из локального файла ~/.token-diet/telegram_bot_token
    (или env TELEGRAM_BOT_TOKEN). В коде/релизе токена нет и не будет.
  - Бот отвечает ТОЛЬКО владельцу (owner_id) — чужие игнорирует.
  - Реальные ордера бот НЕ ставит. Стопы показывает, но исполняет человек.
  - Ключ DeepSeek — из env DEEPSEEK_API_KEY или ~/.invest_bot/secrets.env.
    Без ключа бот работает командами, чат просто честно скажет «нет ключа».

Ноль зависимостей: чистый HTTP polling через urllib. Работает везде.

Usage:
    python3 -m token_diet.telegram_commander
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
import os as _os

# Владелец бота — только он получает ответы.
# БЕРЁТСЯ ИЗ ОКРУЖЕНИЯ (не хардкодится!): TELEGRAM_OWNER_ID.
# Если не задан — бот отвечает всем (безопасно по умолчанию).
OWNER_ID = int(_os.environ.get("TELEGRAM_OWNER_ID", "0") or 0)

TOKEN_FILE = Path.home() / ".token-diet" / "telegram_bot_token"
DEEPSEEK_KEY_FILE = Path.home() / ".token-diet" / "deepseek_key"
SECRETS_FILE = Path.home() / ".invest_bot" / "secrets.env"

API = "https://api.telegram.org/bot{token}/{method}"


# ─────────────────────────────────────────────────────────────────────────────
# Токен и секреты (никогда не печатаются)
# ─────────────────────────────────────────────────────────────────────────────

def load_token() -> str:
    """Токен бота: env TELEGRAM_BOT_TOKEN → ~/.token-diet/telegram_bot_token."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if tok and "PASTE" not in tok:
        return tok
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text().strip()
        if tok and "PASTE" not in tok:
            return tok
    return ""


def load_deepseek_key() -> str:
    """Ключ DeepSeek: env → ~/.token-diet/deepseek_key → secrets.env.

    Никогда не печатается и не попадает в git.
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key and "PASTE" not in key:
        return key
    if DEEPSEEK_KEY_FILE.exists():
        v = DEEPSEEK_KEY_FILE.read_text().strip()
        if v and "PASTE" not in v:
            return v
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v and "PASTE" not in v:
                    return v
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Telegram Bot API (чистый HTTP, zero-deps)
# ─────────────────────────────────────────────────────────────────────────────

def _call(method: str, payload: dict[str, Any], timeout: int = 25) -> dict[str, Any]:
    """Вызвать метод Bot API. Всегда возвращает dict, никогда не бросает."""
    token = load_token()
    if not token:
        return {"ok": False, "error": "нет токена бота"}
    url = API.format(token=token, method=method)
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def get_updates(offset: int = 0, timeout: int = 25) -> list[dict[str, Any]]:
    """Длинный polling. Возвращает список обновлений ([] при ошибке/таймауте)."""
    r = _call("getUpdates", {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message"],
    })
    return r.get("result", []) if r.get("ok") else []


def send_message(chat_id: int, text: str) -> dict[str, Any]:
    """Отправить сообщение. Режет текст до 4000 символов (лимит Telegram)."""
    text = text.strip()
    if not text:
        return {"ok": False, "error": "пустой текст"}
    if len(text) > 4000:
        text = text[:3990] + "\n…(обрезано)"
    return _call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Команды — «мозг» бота
# ─────────────────────────────────────────────────────────────────────────────

HELP = """🫡 <b>Командир на связи</b>

<b>Команды:</b>
/портфель — план по всем позициям (стоп/тейк/зоны)
/чек      — сторож: свежие цены vs стопы/тейки
/стопы    — активные стоп-заявки на бирже
/стакан TICKER — стакан со стенами (напр. /стакан PLZL)
/полюс, /сбер, /газпром … — полная картина по любой бумаге
/помощь   — эта справка

<b>Или просто напишите вопрос</b> — отвечу с контекстом портфеля
(нужен DEEPSEEK_API_KEY).

⚠️ Бот ничего не покупает и не продаёт сам. Он командир-советник:
решение и исполнение всегда за вами.
"""

TICKER_ALIAS = {
    "полюс": "PLZL", "сбер": "SBER", "газпром": "GAZP",
    "яндекс": "YDEX", "лукойл": "LKOH", "норникель": "GMKN",
    "росатом": "ROSN", "тинькофф": "T",
}


def _handle_command(text: str) -> str:
    """Разобрать сообщение владельца и вернуть ответ."""
    cmd = text.strip()
    low = cmd.lower()

    if low in ("/start", "/help", "/помощь", "помощь", "help", "/старт"):
        return HELP

    if low in ("/портфель", "портфель", "/plan", "portfolio"):
        from .portfolio_commander import PortfolioCommander
        plan = PortfolioCommander().plan()
        return PortfolioCommander.render_plan(plan)

    if low in ("/чек", "чек", "/watch", "проверь"):
        from .portfolio_commander import PortfolioCommander
        pc = PortfolioCommander()
        out = ["═══ СТОРОЖ ═══"]
        for a in pc.watch().get("alerts", []):
            out.append(f"{a['alert']:12s} {a['ticker']}: {a['message']}")
        return "\n".join(out)

    if low in ("/стопы", "стопы", "стоп", "/stops"):
        from .tinkoff_mcp import TinkoffMCP
        mcp = TinkoffMCP()
        accs = (mcp.call("invest_list_broker_accounts", {})
                .get("data", {}).get("accounts") or [])
        if not accs:
            return "❌ Счета не найдены"
        s = mcp.call("invest_get_stoporders",
                     {"accountId": str(accs[0].get("id"))})
        stops = (s.get("data") or {}).get("stopOrders") or []
        if not stops:
            return "📭 Активных стоп-заявок нет"
        lines = ["═══ АКТИВНЫЕ СТОП-ЗАЯВКИ ═══"]
        for st in stops:
            typ = st.get("orderType", "?").replace("STOP_ORDER_TYPE_", "")
            price = st.get("stopPrice", {}).get("value", "?")
            qty = st.get("lotsRequested", "?")
            lines.append(f"• {typ}: {qty} лотов, цена {price}₽, "
                         f"статус {st.get('status', '?')}")
        return "\n".join(lines)

    # стакан
    if low.startswith("/стакан") or low.startswith("стакан "):
        parts = cmd.split()
        if len(parts) < 2:
            return "❌ Формат: /стакан PLZL"
        ticker = _resolve(parts[1])
        from .invest_hub import InvestHub
        hub = InvestHub()
        book = hub.orderbook(ticker)
        if "error" in book:
            return f"❌ Стакан {ticker}: {book['error']}"
        out = [f"═══ СТАКАН {ticker} ═══",
               f"Интерпретация: {book.get('interpretation')}",
               "ПРОДАЖА (asks):"]
        for a in book.get("asks", [])[:5]:
            out.append(f"  {a['price']:.2f}₽ × {a['quantity']} шт")
        out.append("ПОКУПКА (bids):")
        for b in book.get("bids", [])[:5]:
            out.append(f"  {b['price']:.2f}₽ × {b['quantity']} шт")
        w = book.get("biggest_wall") or {}
        if w.get("bid"):
            out.append(f"Стена покупки: {w['bid']['price']:.2f}₽ × {w['bid']['quantity']} шт")
        if w.get("ask"):
            out.append(f"Стена продажи: {w['ask']['price']:.2f}₽ × {w['ask']['quantity']} шт")
        return "\n".join(out)

    # тикер как команда: /полюс → PLZL; /sber → SBER; и любой 3-5 букв
    bare = low.lstrip("/").strip()
    if bare in TICKER_ALIAS or (bare.isalpha() and 1 <= len(bare) <= 6):
        ticker = _resolve(bare)
        return _full_picture(ticker)

    # свободный текст → DeepSeek
    return _chat(text)


def _resolve(name: str) -> str:
    """Имя → тикер (русские алиасы и приведение к верхнему)."""
    name = name.strip().lstrip("/").lower()
    if name in TICKER_ALIAS:
        return TICKER_ALIAS[name]
    return name.upper()


def _full_picture(ticker: str) -> str:
    """Полная картина по тикеру — командирский стиль."""
    from .invest_hub import InvestHub
    hub = InvestHub()
    pic = hub.full_picture(ticker, include_news=True, include_orderbook=True)
    return InvestHub.render(pic)


def _chat(text: str) -> str:
    """Свободный чат через DeepSeek с контекстом портфеля."""
    key = load_deepseek_key()
    if not key:
        return ("⚠️ Чат недоступен: не найден DEEPSEEK_API_KEY.\n"
                "Команды работают: /портфель, /чек, /стопы, /полюс и т.д.")
    # соберём контекст портфеля, чтобы отвечать по делу
    context = ""
    try:
        from .portfolio_commander import PortfolioCommander
        plan = PortfolioCommander().plan()
        context = PortfolioCommander.render_plan(plan)
    except Exception:
        context = "(портфель недоступен)"

    system = (
        "Ты — Буффи, командир портфеля token-diet. Говоришь с владельцем "
        "в Telegram. Отвечай кратко и по делу, по-русски. Не выдумывай "
        "цифры — опирайся только на контекст портфеля ниже. Помни: "
        "реальные сделки ставит владелец, ты советник.\n\n"
        f"КОНТЕКСТ ПОРТФЕЛЯ:\n{context}"
    )
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text[:2000]},
        ],
        "temperature": 0.4,
        "max_tokens": 900,
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.loads(r.read().decode())
        return d["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"⚠️ DeepSeek не ответил: {type(e).__name__}"


# ─────────────────────────────────────────────────────────────────────────────
# Цикл бота
# ─────────────────────────────────────────────────────────────────────────────

def run(once: bool = False) -> None:
    """Запустить бота (бесконечный polling). once=True — один цикл (тест)."""
    if not load_token():
        print("❌ Нет токена бота. Положи его в ~/.token-diet/telegram_bot_token")
        return
    print(f"🫡 Командир запущен. Владелец: {OWNER_ID}. "
          f"DeepSeek: {'да' if load_deepseek_key() else 'нет'}")
    offset = 0
    while True:
        updates = get_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat_id = msg.get("chat", {}).get("id")
            user_id = msg.get("from", {}).get("id")
            text = msg.get("text", "")
            if not chat_id or not text:
                continue
            if user_id != OWNER_ID:
                send_message(chat_id, "⛔ Доступ только у владельца.")
                continue
            # статус «печатает», затем ответ
            _call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
            try:
                answer = _handle_command(text)
            except Exception as e:
                answer = f"⚠️ Ошибка: {type(e).__name__}: {e}"
            send_message(chat_id, answer)
        if once:
            return
        time.sleep(0.5)


def main() -> None:
    run()


if __name__ == "__main__":
    main()

__all__ = [
    "OWNER_ID", "TOKEN_FILE", "HELP",
    "load_token", "load_deepseek_key",
    "get_updates", "send_message",
    "_handle_command", "_full_picture", "_chat", "run", "main",
]
