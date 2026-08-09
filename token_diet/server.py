"""token-diet — CLProxy for AI tools.

    pip install token-diet[server]
    token-diet serve

A transparent OpenAI-compatible proxy that compresses every request.
No website, no dashboard — just a proxy that saves tokens.

Compatible with: Claude Code, OpenCode, Cursor, Hermes, Buffy,
OpenAI SDK, OpenRouter, Ollama, and any OpenAI-compatible endpoint.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOKEN_DIET_DIR = _HERE.parent if (_HERE / "core.py").exists() else _HERE
if str(_TOKEN_DIET_DIR) not in sys.path:
    sys.path.insert(0, str(_TOKEN_DIET_DIR))

from token_diet.core import count_tokens, PriceTable
from token_diet.green_calculator import GreenCalculator, GreenMetrics

# ── Global stats ─────────────────────────────────────────────────────────────


@dataclass
class ServerStats:
    requests: int = 0
    tokens_saved: int = 0
    dollars_saved: float = 0.0
    green: GreenMetrics = field(default_factory=GreenMetrics)
    uptime_start: float = field(default_factory=time.time)

    def add_savings(self, tokens: int, cost: float) -> None:
        self.requests += 1
        self.tokens_saved += tokens
        self.dollars_saved += cost
        self.green = self.green + GreenCalculator().measure(tokens)

    def snapshot(self) -> dict:
        return {
            "requests": self.requests,
            "tokens_saved": self.tokens_saved,
            "dollars_saved": round(self.dollars_saved, 6),
            "co2_kg": round(self.green.kg_co2_saved, 4),
            "water_liters": round(self.green.liters_water_saved, 1),
            "uptime_seconds": int(time.time() - self.uptime_start),
        }


_stats = ServerStats()


# ── Compression ──────────────────────────────────────────────────────────────


def _apply_token_diet(messages: list[dict], model: str) -> tuple[list[dict], int]:
    """Apply FULL pipeline: promptology + loss_router + pattern_collapse."""
    if not messages:
        return messages, 0

    total_before = 0
    total_after = 0
    optimized = []

    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "")

        # Handle tool definitions
        if role == "tool_definitions" or (isinstance(content, list) and all(
            isinstance(t, dict) and ("function" in t or "type" in t) for t in content[:1]
            if isinstance(content, list)
        )):
            try:
                from token_diet.compression_arsenal import compress_tool_definitions
                if isinstance(content, list):
                    tools_json = json.dumps(content)
                    content = compress_tool_definitions(tools_json)
                    content = json.loads(content)  # Parse back to list
            except Exception:
                pass

        if isinstance(content, str) and content:
            before = count_tokens(content)

            # ── System prompt: full promptology rewrite ──
            if role == "system":
                try:
                    from token_diet.promptology import rewrite_system_prompt
                    content = rewrite_system_prompt(content)
                except Exception:
                    pass

            # ── User message: full intelligence pipeline ──
            elif role == "user":
                try:
                    from token_diet.promptology import (
                        rewrite_user_prompt, reframe_positive,
                        remove_russian_filler, adapt_for_language,
                    )
                    content = rewrite_user_prompt(content)
                    content = reframe_positive(content)
                    content = remove_russian_filler(content)
                    content = adapt_for_language(content, "Russian")
                except Exception:
                    pass
                # Self-Discover reasoning blueprint
                try:
                    from token_diet.intelligence_optimizer import inject_reasoning_blueprint
                    content = inject_reasoning_blueprint(content)
                except Exception:
                    pass
                # Token Merger
                try:
                    from token_diet.intelligence_optimizer import merge_tokens
                    content = merge_tokens(content)
                except Exception:
                    pass
                # Caveman grammar stripper
                try:
                    from token_diet.compression_arsenal import strip_grammar_caveman
                    content = strip_grammar_caveman(content)
                except Exception:
                    pass
                # Loss-router compression
                try:
                    from token_diet.loss_router import compress_with_routing
                    content, _, _ = compress_with_routing(content)
                except Exception:
                    pass

            after = count_tokens(content)
            optimized.append({**msg, "content": content})
            total_before += before
            total_after += after
        else:
            optimized.append(msg)
            if isinstance(content, str):
                total_before += count_tokens(content)
                total_after += count_tokens(content)

    saved = max(0, total_before - total_after)
    return optimized, saved


# ── App ──────────────────────────────────────────────────────────────────────


def create_app():
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        import httpx
    except ImportError:
        print("pip install fastapi uvicorn httpx")
        sys.exit(1)

    app = FastAPI(title="token-diet", version="2.5.9")

    UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "")
    UPSTREAM_KEY = os.environ.get("UPSTREAM_KEY", "")
    PROXY_ENABLED = bool(UPSTREAM_URL)

    @app.get("/health")
    async def health():
        return {"status": "ok", "proxy": PROXY_ENABLED}

    @app.get("/v1/stats")
    async def stats():
        return _stats.snapshot()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        messages = body.get("messages", [])
        model = body.get("model", "unknown")

        optimized_messages, tokens_saved = _apply_token_diet(messages, model)

        if PROXY_ENABLED:
            async with httpx.AsyncClient(timeout=120) as client:
                headers = {"Authorization": f"Bearer {UPSTREAM_KEY}",
                           "Content-Type": "application/json"}
                proxy_body = {**body, "messages": optimized_messages}
                upstream_resp = await client.post(
                    f"{UPSTREAM_URL}/chat/completions",
                    json=proxy_body,
                    headers={k: v for k, v in headers.items() if v},
                )
                upstream_data = upstream_resp.json()
                cost_saved = tokens_saved * 0.14 / 1_000_000
                _stats.add_savings(tokens_saved, cost_saved)
                return JSONResponse(upstream_data)
        else:
            cost_saved = tokens_saved * 0.14 / 1_000_000
            _stats.add_savings(tokens_saved, cost_saved)
            return JSONResponse({
                "id": "token-diet",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": f"[token-diet: {tokens_saved}t saved, ${cost_saved:.6f}]"},
                    "finish_reason": "stop"}],
                "usage": {"total_tokens": 0},
            })

    return app


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="token-diet — AI cost optimization proxy")
    parser.add_argument("command", nargs="?", default="serve",
                       help="serve | setup | detect | stats")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--upstream-url", default="")
    parser.add_argument("--upstream-key", default="")
    args = parser.parse_args()

    if args.command in ("setup", "detect", "list"):
        from token_diet.auto_setup import main as setup_main
        import sys as _sys
        _sys.argv = ["token-diet-setup", args.command]
        setup_main()
        return

    if args.command == "stats":
        s = _stats.snapshot()
        print(f"requests={s['requests']} saved={s['tokens_saved']}t "
              f"${s['dollars_saved']:.6f} co2={s['co2_kg']}kg")
        return

    if args.upstream_url:
        os.environ["UPSTREAM_URL"] = args.upstream_url
    if args.upstream_key:
        os.environ["UPSTREAM_KEY"] = args.upstream_key

    app = create_app()

    try:
        import uvicorn
    except ImportError:
        print("pip install uvicorn")
        sys.exit(1)

    upstream = os.environ.get("UPSTREAM_URL", "")
    mode = upstream if upstream else "simulation"
    print(f"token-diet :{args.port}  upstream={mode}  v2.5.9")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
