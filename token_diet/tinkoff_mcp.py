"""tinkoff_mcp — официальный MCP-клиент Т-Инвестиций для token-diet.

Подключается к официальному MCP-серверу T-Bank (invest-public-api.tbank.ru/mcp)
через Streamable HTTP transport и даёт доступ ко ВСЕМ 68 инструментам:
  - рыночные данные (котировки, свечи, стаканы, последние сделки)
  - новости и аналитика (invest_get_news, консенсус-прогнозы, сигналы, стратегии)
  - инсайдерские сделки, отчётность, дивиденды, фундаментальные показатели
  - портфель, позиции, операции, маржинальные показатели
  - торговля (заявки, стоп-заявки, отмена) — только read-only по умолчанию

Зачем это token-diet:
  Раньше мы читали котировки через REST (tinkoff_invest) — но у MCP есть то,
  чего не было: ОФИЦИАЛЬНЫЕ новости, прогнозы аналитиков, торговые сигналы,
  стратегии и сделки инсайдеров. Это усиливает InvestHub новым слоем данных.

Протокол (Streamable HTTP MCP, без внешних зависимостей):
  1. POST initialize → берём Mcp-Session-Id из заголовков ответа
  2. POST tools/call с этим session id → ответ в SSE (text/event-stream)
  3. SSE парсим вручную (JSON может быть многострочным)

Безопасность:
  - Токен НИКОГДА не печатается, берётся из get_token() (env/файл ~/.tinkoff/token)
  - По умолчанию READ-ONLY: торговые инструменты вызываются только если
    явно передать allow_trading=True
  - Никогда не бросает исключений — на сбой возвращает {"error": ...}

For the people. For the planet. Honesty is cheaper than regret.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
import urllib.error
from typing import Any

from .tinkoff_invest import get_token

MCP_URL = "https://invest-public-api.tbank.ru/mcp"
MCP_PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "token-diet"
CLIENT_VERSION = "3.4.0"

# Инструменты, которые МЕНЯЮТ состояние (требуют allow_trading=True)
_WRITE_TOOLS = frozenset({
    "invest_create_favorite_group",
    "invest_delete_favorite_group",
    "invest_edit_favorite_instruments",
    "invest_cancel_order",
    "invest_create_order",
    "invest_replace_order",
    "invest_cancel_stoporder",
    "invest_create_stoporder",
    "invest_deposit_broker_account",
    "invest_transfer_broker_accounts",
})


class TinkoffMCPError(Exception):
    """Ошибка MCP-вызова."""


class TinkoffMCP:
    """Официальный MCP-клиент Т-Инвестиций.

    Usage:
        mcp = TinkoffMCP()                    # токен из env/файла
        news = mcp.call("invest_get_news", {"limit": 5})
        pf   = mcp.call("invest_get_portfolio", {"accountId": ...})
        book = mcp.call("invest_get_orderbook", {...})
    """

    def __init__(
        self,
        token: str | None = None,
        url: str = MCP_URL,
        timeout: int = 60,
        allow_trading: bool = False,
        ssl_verify: bool = False,
    ):
        self.token = token or get_token()
        self.url = url
        self.timeout = timeout
        self.allow_trading = allow_trading
        self.available = bool(self.token)
        self._session_id: str | None = None
        self._ctx = ssl.create_default_context()
        if not ssl_verify:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    # ── низкоуровневый POST ──────────────────────────────────────────────
    def _post(self, body: dict, headers: dict | None = None) -> tuple[dict, str]:
        """POST JSON-RPC, возвращает (заголовки, сырое тело)."""
        h = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if headers:
            h.update(headers)
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), headers=h, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                return dict(resp.headers), resp.read().decode()
        except urllib.error.HTTPError as e:
            raise TinkoffMCPError(f"HTTP {e.code}: {e.read().decode()[:300]}")
        except Exception as e:  # network, ssl, timeout
            raise TinkoffMCPError(f"{type(e).__name__}: {e}")

    @staticmethod
    def _parse_sse(raw: str) -> list[dict]:
        """Парсер SSE: накапливает data:-строки до пустой строки (JSON может быть многострочным)."""
        messages: list[dict] = []
        buf: list[str] = []
        for line in raw.split("\n"):
            if line.startswith("data:"):
                buf.append(line[5:].strip())
            elif not line.strip() and buf:
                try:
                    messages.append(json.loads("\n".join(buf)))
                except json.JSONDecodeError:
                    pass
                buf = []
        if buf:
            try:
                messages.append(json.loads("\n".join(buf)))
            except json.JSONDecodeError:
                pass
        return messages

    # ── инициализация сессии ─────────────────────────────────────────────
    def _ensure_session(self) -> str:
        """Initialize + вернуть Mcp-Session-Id (кэшируется)."""
        if self._session_id:
            return self._session_id
        if not self.available:
            raise TinkoffMCPError("нет токена Т-Инвестиций")
        body = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        }
        headers, raw = self._post(body)
        sid = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
        if not sid:
            raise TinkoffMCPError("сервер не вернул Mcp-Session-Id")
        self._session_id = sid
        return sid

    # ── список инструментов ──────────────────────────────────────────────
    def list_tools(self) -> list[dict]:
        """Полный список инструментов MCP-сервера (с пагинацией).

        Возвращает [] на сбой — никогда не бросает (как и call()).
        """
        try:
            return self._list_tools_unsafe()
        except Exception:
            return []

    def _list_tools_unsafe(self) -> list[dict]:
        sid = self._ensure_session()
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": params}
            _, raw = self._post(body, headers={"Mcp-Session-Id": sid})
            msgs = self._parse_sse(raw)
            if not msgs:
                break
            r = msgs[-1].get("result", {})
            tools.extend(r.get("tools", []))
            cursor = r.get("nextCursor")
            if not cursor:
                break
        return tools

    # ── вызов инструмента ────────────────────────────────────────────────
    def call(self, name: str, arguments: dict | None = None) -> dict:
        """Вызвать MCP-инструмент и вернуть структурированный результат.

        Защита:
          - write-инструменты (торговля, переводы) требуют allow_trading=True
          - никогда не бросает: возвращает {"error": ...} на любой сбой
        """
        if not self.available:
            return {"error": "нет токена Т-Инвестиций"}
        if name in _WRITE_TOOLS and not self.allow_trading:
            return {
                "error": f"{name} — торговая операция. Создайте TinkoffMCP(allow_trading=True) "
                         f"и подтвердите операцию явно."
            }
        try:
            sid = self._ensure_session()
            body = {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
            _, raw = self._post(body, headers={"Mcp-Session-Id": sid})
            msgs = self._parse_sse(raw)
            if not msgs:
                return {"error": "пустой ответ MCP-сервера"}
            resp = msgs[-1]
            if "error" in resp:
                return {"error": str(resp["error"])}
            result = resp.get("result", {})
            if isinstance(result, dict) and "content" in result:
                return self._extract_content(result["content"])
            return result
        except TinkoffMCPError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _extract_content(content: list) -> dict:
        """Извлечь содержимое MCP-ответа (text / structuredContent / resource)."""
        out: dict[str, Any] = {}
        texts = []
        for c in content or []:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type")
            if ctype == "text":
                texts.append(c.get("text", ""))
            elif ctype == "resource":
                out["resource"] = c.get("resource", {})
            sc = c.get("structuredContent")
            if isinstance(sc, dict):
                out["data"] = sc
        if texts:
            out["text"] = "\n".join(texts)
        # structuredContent > text (богаче для машинного анализа)
        if "data" not in out and texts:
            try:
                out["data"] = json.loads(texts[0])
            except json.JSONDecodeError:
                pass
        return out

    # ── удобные обёртки ──────────────────────────────────────────────────
    def news(self, limit: int = 10) -> dict:
        """Официальные новости Т-Инвестиций."""
        return self.call("invest_get_news", {"limit": limit})

    def portfolio(self, account_id: str | None = None) -> dict:
        """Портфель (нужен accountId; если не передан — найдём первый)."""
        account_id = account_id or self._first_account_id()
        if not account_id:
            return {"error": "нет брокерских счетов"}
        return self.call("invest_get_portfolio", {"accountId": account_id})

    def orderbook(self, instrument_id: str, depth: int = 10) -> dict:
        """Стакан по instrumentId."""
        return self.call("invest_get_orderbook", {"instrumentId": instrument_id, "depth": depth})

    def candles(self, instrument_id: str, interval: str = "CANDLE_INTERVAL_DAY",
                count: int = 30) -> dict:
        """Свечи."""
        return self.call("invest_get_candles", {
            "instrumentId": instrument_id, "interval": interval, "count": count,
        })

    def forecast(self, instrument_id: str) -> dict:
        """Прогнозы аналитиков по инструменту (UID)."""
        return self.call("invest_get_forecast", {"uid": instrument_id})

    def signals(self, limit: int = 20) -> dict:
        """Торговые сигналы Т-Инвестиций."""
        return self.call("invest_list_signals", {"limit": limit})

    def _first_account_id(self) -> str | None:
        """Первый брокерский счёт (для удобных обёрток)."""
        r = self.call("invest_list_broker_accounts", {})
        data = r.get("data") or {}
        accounts = data.get("accounts") or []
        if isinstance(accounts, list) and accounts:
            first = accounts[0]
            if isinstance(first, dict):
                return str(first.get("id") or first.get("accountId") or "")
        return None

    # ── статус ────────────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        """Проверка доступности (без раскрытия токена)."""
        if not self.available:
            return {"available": False, "reason": "нет токена"}
        try:
            tools = self.list_tools()
            return {
                "available": True,
                "server": "t-invest-mcp",
                "tools_count": len(tools),
                "trading_enabled": self.allow_trading,
            }
        except Exception as e:
            return {"available": False, "reason": str(e)[:120]}


__all__ = ["TinkoffMCP", "TinkoffMCPError"]
