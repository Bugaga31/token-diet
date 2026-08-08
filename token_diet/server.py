"""token-diet Server — OpenAI-compatible proxy with live dashboard.

One command: token-diet serve
→ Opens http://localhost:8080 with live savings counter
→ Acts as transparent proxy for any OpenAI-compatible API
→ Applies all token-diet optimizations automatically

Compatible with: Claude API, OpenAI, DeepSeek, OpenRouter, anymodel.org,
Ollama, LM Studio, and any other OpenAI-compatible endpoint.
"""

from __future__ import annotations

import json
import os
import sys
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOKEN_DIET_DIR = _HERE.parent if (_HERE / "core.py").exists() else _HERE
if str(_TOKEN_DIET_DIR) not in sys.path:
    sys.path.insert(0, str(_TOKEN_DIET_DIR))

try:
    from token_diet.core import count_tokens, PriceTable
    from token_diet.green_calculator import GreenCalculator, GreenMetrics
    from token_diet.optimization_runner import OptimizationRunner, RequestProfile
except ImportError:
    from core import count_tokens, PriceTable
    from green_calculator import GreenCalculator, GreenMetrics
    from optimization_runner import OptimizationRunner, RequestProfile

# ── Global state ──────────────────────────────────────────────────────────────

@dataclass
class ServerStats:
    """Live server statistics."""
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
        calc = GreenCalculator()
        return {
            "requests": self.requests,
            "tokens_saved": self.tokens_saved,
            "dollars_saved": round(self.dollars_saved, 6),
            "co2_kg": round(self.green.kg_co2_saved, 4),
            "water_liters": round(self.green.liters_water_saved, 1),
            "ice_creams": int(self.dollars_saved / 3),
            "park_trips": int(self.dollars_saved / 10),
            "trees": round(self.green.trees_equivalent, 2),
            "uptime_seconds": int(time.time() - self.uptime_start),
        }


_stats = ServerStats()


# ── Dashboard HTML ────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>token-diet — Track. Optimize. Achieve.</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0a0a0a; color: #e0e0e0; font-family: 'Segoe UI', system-ui, sans-serif;
         min-height: 100vh; display: flex; flex-direction: column; align-items: center; }
  .container { max-width: 900px; width: 100%; padding: 40px 20px; }
  .logo { display: flex; align-items: center; gap: 16px; margin-bottom: 8px; }
  .logo-icon { width: 56px; height: 56px; border-radius: 14px;
               background: linear-gradient(135deg, #22c55e, #16a34a);
               display: flex; align-items: center; justify-content: center; font-size: 30px; }
  .logo-text { font-size: 32px; font-weight: 800; }
  .logo-text .white { color: #fff; }
  .logo-text .green { color: #22c55e; }
  .tagline { color: #6b7280; font-size: 14px; margin-bottom: 32px; letter-spacing: 1px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; padding: 20px; text-align: center; }
  .card .value { font-size: 28px; font-weight: 700; color: #22c55e; }
  .card .label { font-size: 12px; color: #6b7280; margin-top: 4px; text-transform: uppercase; letter-spacing: 1px; }
  .card.ice .value { color: #f59e0b; }
  .card.co2 .value { color: #ef4444; }
  .section-title { font-size: 14px; color: #6b7280; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 12px; }
  .endpoints { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
  .endpoints code { color: #22c55e; background: #0a0a0a; padding: 3px 8px; border-radius: 4px; font-size: 13px; }
  .endpoints .row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #222; }
  .endpoints .row:last-child { border-bottom: none; }
  .footer { text-align: center; color: #374151; font-size: 12px; margin-top: 40px; }
  .pulse { animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
  .live-dot { display: inline-block; width: 8px; height: 8px; background: #22c55e; border-radius: 50%; margin-right: 6px; }
</style>
</head>
<body>
<div class="container">
  <div class="logo">
    <div class="logo-icon">🌱</div>
    <div>
      <div class="logo-text"><span class="white">token</span><span class="green">-diet</span></div>
      <div class="tagline">Track. Optimize. Achieve.</div>
    </div>
  </div>

  <div class="section-title"><span class="live-dot pulse"></span>Live Savings</div>
  <div class="cards">
    <div class="card">
      <div class="value" id="tokens">0</div>
      <div class="label">Tokens Saved</div>
    </div>
    <div class="card">
      <div class="value" id="dollars">$0.00</div>
      <div class="label">Money Saved</div>
    </div>
    <div class="card co2">
      <div class="value" id="co2">0 kg</div>
      <div class="label">CO₂ Prevented</div>
    </div>
    <div class="card">
      <div class="value" id="water">0 L</div>
      <div class="label">Water Saved</div>
    </div>
    <div class="card ice">
      <div class="value" id="icecream">0</div>
      <div class="label">Ice Creams</div>
    </div>
    <div class="card">
      <div class="value" id="requests">0</div>
      <div class="label">Requests</div>
    </div>
  </div>

  <div class="section-title">API Endpoints</div>
  <div class="endpoints">
    <div class="row">
      <span>Chat Completions</span>
      <code>POST /v1/chat/completions</code>
    </div>
    <div class="row">
      <span>Stats</span>
      <code>GET /v1/stats</code>
    </div>
    <div class="row">
      <span>Health</span>
      <code>GET /health</code>
    </div>
  </div>

  <div class="section-title">Quick Start</div>
  <div class="endpoints">
    <pre style="color:#9ca3af;font-size:13px;line-height:1.6">
<span style="color:#6b7280"># Set your upstream provider (any OpenAI-compatible API)</span>
<span style="color:#22c55e">export</span> UPSTREAM_URL=https://api.deepseek.com
<span style="color:#22c55e">export</span> UPSTREAM_KEY=sk-your-key

<span style="color:#6b7280"># Or use anymodel.org for wholesale prices:</span>
<span style="color:#22c55e">export</span> UPSTREAM_URL=https://anymodel.org/v1
<span style="color:#22c55e">export</span> UPSTREAM_KEY=your-anymodel-key

<span style="color:#6b7280"># All OpenAI SDKs work out of the box:</span>
<span style="color:#6b7280"># client = OpenAI(base_url="http://localhost:8080/v1")</span>
    </pre>
  </div>

  <div class="footer">
    🌍 token-diet v2.2.0 — For the planet. For the people.<br>
    Saving tokens, energy, CO₂, water — one API call at a time.
  </div>
</div>

<script>
  async function refresh() {
    try {
      const r = await fetch('/v1/stats');
      const s = await r.json();
      document.getElementById('tokens').textContent = s.tokens_saved.toLocaleString();
      document.getElementById('dollars').textContent = '$' + s.dollars_saved.toFixed(4);
      document.getElementById('co2').textContent = s.co2_kg.toFixed(3) + ' kg';
      document.getElementById('water').textContent = s.water_liters.toFixed(1) + ' L';
      document.getElementById('icecream').textContent = s.ice_creams.toLocaleString();
      document.getElementById('requests').textContent = s.requests.toLocaleString();
    } catch(e) { console.error(e); }
  }
  refresh();
  setInterval(refresh, 2000);
</script>
</body>
</html>"""


# ── Token-diet proxy logic ────────────────────────────────────────────────────

def _apply_token_diet(messages: list[dict], model: str) -> tuple[list[dict], int]:
    """Apply token-diet optimizations to messages. Returns (optimized_messages, tokens_saved)."""
    if not messages:
        return messages, 0

    total_before = 0
    total_after = 0

    optimized = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            before = count_tokens(content)

            # Apply light compression (system prompt distilled, question normalized)
            role = msg.get("role", "")
            if role == "system":
                # Keep system prompts light
                optimized.append(msg)
                total_before += before
                total_after += before
            elif role == "user":
                # Compress user messages
                from token_diet.loss_router import compress_with_routing
                try:
                    compressed, _, after_t = compress_with_routing(content)
                    after = after_t
                    optimized.append({**msg, "content": compressed})
                except Exception:
                    optimized.append(msg)
                    after = before
                total_before += before
                total_after += after
            else:
                optimized.append(msg)
                total_before += before
                total_after += before
        else:
            optimized.append(msg)

    saved = max(0, total_before - total_after)
    return optimized, saved


# ── FastAPI app ───────────────────────────────────────────────────────────────

def create_app():
    """Create the FastAPI application (lazy import to avoid hard dependency)."""
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import HTMLResponse, JSONResponse
        import httpx
    except ImportError:
        print("Install dependencies: pip install fastapi uvicorn httpx")
        sys.exit(1)

    app = FastAPI(
        title="token-diet",
        description="Track. Optimize. Achieve. — AI cost optimization proxy",
        version="2.2.0",
    )

    UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "")
    UPSTREAM_KEY = os.environ.get("UPSTREAM_KEY", "")
    PROXY_ENABLED = bool(UPSTREAM_URL)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/health")
    async def health():
        return {"status": "ok", "proxy_enabled": PROXY_ENABLED, "version": "2.2.0"}

    @app.get("/v1/stats")
    async def stats():
        return _stats.snapshot()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        messages = body.get("messages", [])
        model = body.get("model", "unknown")

        # Apply token-diet optimizations
        optimized_messages, tokens_saved = _apply_token_diet(messages, model)

        # Proxy to upstream if configured
        if PROXY_ENABLED:
            async with httpx.AsyncClient(timeout=120) as client:
                headers = {"Authorization": f"Bearer {UPSTREAM_KEY}", "Content-Type": "application/json"}
                proxy_body = {**body, "messages": optimized_messages}
                upstream_resp = await client.post(
                    f"{UPSTREAM_URL}/chat/completions",
                    json=proxy_body,
                    headers={k: v for k, v in headers.items() if v},
                )
                upstream_data = upstream_resp.json()

                # Calculate cost savings
                cost_saved = tokens_saved * 0.14 / 1_000_000  # Default to DeepSeek pricing
                _stats.add_savings(tokens_saved, cost_saved)

                return JSONResponse(upstream_data)
        else:
            # No upstream: return simulated response
            cost_saved = tokens_saved * 0.14 / 1_000_000
            _stats.add_savings(tokens_saved, cost_saved)

            return JSONResponse({
                "id": "token-diet-sim",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"[token-diet: {tokens_saved} tokens saved, ${cost_saved:.6f} cheaper]"
                    },
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            })

    return app


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    """token-diet serve — start the proxy server."""
    import argparse

    parser = argparse.ArgumentParser(description="token-diet — Track. Optimize. Achieve.")
    parser.add_argument("command", nargs="?", default="serve", help="Command: serve")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--upstream-url", default="", help="Upstream OpenAI-compatible API URL")
    parser.add_argument("--upstream-key", default="", help="Upstream API key")
    args = parser.parse_args()

    if args.upstream_url:
        os.environ["UPSTREAM_URL"] = args.upstream_url
    if args.upstream_key:
        os.environ["UPSTREAM_KEY"] = args.upstream_key

    app = create_app()

    try:
        import uvicorn
    except ImportError:
        print("Install: pip install uvicorn")
        sys.exit(1)

    upstream = os.environ.get("UPSTREAM_URL", "")
    print(f"""
🌱 token-diet v2.2.0 — Track. Optimize. Achieve.
   Dashboard:  http://{args.host}:{args.port}
   API:        http://{args.host}:{args.port}/v1/chat/completions
   Stats:      http://{args.host}:{args.port}/v1/stats
   Upstream:   {upstream if upstream else '(simulation mode — set UPSTREAM_URL)'}

   Compatible with: Claude, OpenAI, DeepSeek, OpenRouter, anymodel, Ollama
   Usage: export UPSTREAM_URL=https://api.deepseek.com
          export UPSTREAM_KEY=sk-your-key
""")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
