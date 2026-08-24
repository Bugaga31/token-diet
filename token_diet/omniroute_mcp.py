"""OmniRouter MCP Server — маршрутизация моделей для Claude Code.

Подключает OmniRouter (саб-агентная маршрутизация) к Claude Code
через MCP stdio. Модели AnyModel (deepseek-v4-pro, glm-5.2,
minimax-m3) уже проверены — роутер сам выбирает лучшую под задачу.

Установка для Claude Code:
  claude mcp add omnirouter -- python3 /tmp/token-diet-clone/token_diet/omniroute_mcp.py

Инструменты:
  route_task   — отправить задачу на лучшую модель по стратегии
                 (auto/coding, auto/smart, auto/fast, auto/cheap)
  orchestrate  — сложную задачу разбить на саб-агентов и собрать итог
  army_verdict — спросить несколько моделей и собрать голоса
"""

from __future__ import annotations

import json
import sys
import traceback


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _read() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception:
        return None


def _route_task(args: dict) -> dict:
    from token_diet.omniroute import DEFAULT_PROVIDERS, OmniRouter
    router = OmniRouter(providers=DEFAULT_PROVIDERS)
    task = args.get("task", "")
    strategy = args.get("strategy", "auto/balanced")
    system = args.get("system")
    max_tokens = int(args.get("max_tokens", 900))
    result = router.subagent(task, strategy=strategy, context=system or "",
                             max_tokens=max_tokens)
    return {
        "provider": result.provider,
        "model": result.model,
        "strategy": result.strategy,
        "summary": result.summary,
        "ok": result.ok,
        "error": result.error,
    }


def _orchestrate(args: dict) -> dict:
    from token_diet.omniroute import DEFAULT_PROVIDERS, OmniRouter
    router = OmniRouter(providers=DEFAULT_PROVIDERS)
    task = args.get("task", "")
    strategy = args.get("strategy", "auto/balanced")
    result = router.orchestrate(task, strategy=strategy)
    parts = []
    for r in result.get("subresults", []):
        parts.append(f"[{r.provider}] {r.summary}")
    return {
        "task": task,
        "final": result.get("final", ""),
        "subresults": parts,
    }


def _army_verdict(args: dict) -> dict:
    from token_diet.model_army import army_verdict
    votes = army_verdict(args.get("task", ""), system=args.get("system"))
    return {"votes": votes}


HANDLERS = {
    "route_task": _route_task,
    "orchestrate": _orchestrate,
    "army_verdict": _army_verdict,
}

TOOLS = [
    {
        "name": "route_task",
        "description": "Отправить задачу на лучшую модель (deepseek-v4-pro, glm-5.2, "
                       "minimax-m3) по стратегии: auto/coding, auto/smart, auto/fast, auto/cheap",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Задача для модели"},
                "strategy": {"type": "string", "enum": ["auto/coding", "auto/smart", "auto/fast", "auto/cheap", "auto/balanced"]},
                "system": {"type": "string", "description": "Системный промпт (опционально)"},
                "max_tokens": {"type": "integer"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "orchestrate",
        "description": "Разбить сложную задачу на саб-агентов, каждый на своей модели, собрать итог",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "strategy": {"type": "string"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "army_verdict",
        "description": "Спросить всю LLM-армию (4 модели) и собрать голоса",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "system": {"type": "string"},
            },
            "required": ["task"],
        },
    },
]


def main() -> None:
    while True:
        msg = _read()
        if msg is None:
            break
        method = msg.get("method", "")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": msg.get("id"),
                   "result": {"protocolVersion": "2024-11-05",
                              "serverInfo": {"name": "omnirouter", "version": "1.0"},
                              "capabilities": {"tools": {}}}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = msg.get("params", {}).get("name", "")
            args = msg.get("params", {}).get("arguments", {}) or {}
            handler = HANDLERS.get(name)
            if handler:
                try:
                    result = handler(args)
                    _send({"jsonrpc": "2.0", "id": msg.get("id"),
                           "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})
                except Exception as e:
                    _send({"jsonrpc": "2.0", "id": msg.get("id"),
                           "result": {"content": [{"type": "text", "text": f"ERR: {e}\n{traceback.format_exc()[-300:]}"}]}})
            else:
                _send({"jsonrpc": "2.0", "id": msg.get("id"),
                       "result": {"content": [{"type": "text", "text": f"нет инструмента {name}"}]}})
        elif method == "notifications/initialized":
            pass
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}})


if __name__ == "__main__":
    main()
