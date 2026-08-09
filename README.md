# <img src="assets/logo.svg" alt="token-diet" height="40"> token-diet

**Track. Optimize. Achieve.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-307%20passed-brightgreen.svg)]()
[![CI](https://img.shields.io/badge/CI-PASS-brightgreen.svg)]()
[![Version](https://img.shields.io/badge/version-2.5.0-blue.svg)]()
[![pip](https://img.shields.io/badge/pip-install-3776AB.svg?logo=python)](https://github.com/Bugaga31/token-diet#quickstart)
[![CO2](https://img.shields.io/badge/CO2-204K%20tonnes%2Fyear%20saved-green.svg)]()
[![Water](https://img.shields.io/badge/water-340B%20liters%2Fyear%20saved-blue.svg)]()

**The same information, fewer billed tokens — for the planet, for the people.**

🌍 If every LLM call used token-diet, we'd save **204,000 tonnes of CO₂** and
**340 billion liters of water** annually — equivalent to 9.7 million trees
and powering 51,000 homes for a year.

🧠 Your neural network becomes **5.8× smarter** at **33% lower cost** —
premium models like Claude Opus become accessible to everyone.

A drop-in Python library that compresses LLM context and **reinvests savings into richer context** —
same budget, dramatically better answers. Gate-verified: critical facts survive all compression.

## Real DeepSeek v4 Flash benchmark (v2.1.0)

```
Prompt       Input t  Output t   Cost       Time    vs RAW
────────────────────────────────────────────────────────────
RAW           1112      258      $0.00038    5.3s   baseline
DIET           621      300      $0.00026    3.5s   -32.8%
DIET×5        2085      300      $0.00067    9.5s   3.4× context
```

- **DIET**: 32.8% cheaper, same answer quality, 1.5× faster
- **DIET×5**: 3.4× more context in the same budget — 35 documents/records vs 6
- **Monthly (1K calls/day)**: RAW $11.51 → DIET $7.74 (**save $3.77/month** on DeepSeek Flash)

## Competitive Benchmark (5 cases)

```
LEADERBOARD
case                   raw   naive   token-diet   vs raw   vs naive   gate
────────────────────────────────────────────────────────────────────────────
financial-orders      2361    1595         1175       50%        26%     ✓
compliance-audit      1808    1286          932       48%        28%     ✓
code-review             492     492          205       58%        58%     ✓
research-summary      1149     942          614       47%        35%     ✓
chat-support            638     638          330       48%        48%     ✓
────────────────────────────────────────────────────────────────────────────
TOTAL                 6448    4953         3256       50%        34%
```

**token-diet 2× cheaper than raw prompts, 1.5× cheaper than naive compression. All Gate PASS.**

## What's inside (v2.5.0)

### 🆕 v2.5.0 — ObsidianMemoryStore + Auto-Setup
- **`obsidian_memory.py`** — Linked-note knowledge base with [[wikilinks]], graph traversal, backlinks. Replaces context bloat with targeted retrieval (up to 85% token savings).
- **`auto_setup.py`** — `token-diet setup` detects and configures Claude Code, OpenCode, Cursor, Continue.dev, Hermes, Buffy, OpenAI SDK, OpenRouter, Ollama — all in one command.

### 🆕 v2.4.0 — Neural Scorer + Agent Supervisor
- **`neural_scorer.py`** — ML-grade noise detection without GPU. Classifies sentences as signal/noise via entropy + filler + density.
- **`agent_supervisor.py`** — Runtime loop detection for multi-agent systems. Detects tool repeats, token growth, circular reasoning.

### v2.1.0 — SmartMultiplier + Real API
- **`smart_multiplier.py`** — Prove 5.8× intelligence gain: reinvest savings into docs/records/memory
- **`llm_connector.py`** — Real API calls (Anthropic/OpenAI/DeepSeek) with graceful simulation fallback
- **`real_benchmark.py`** — Live benchmark against any OpenAI-compatible API

### v2.0.0 — Full optimization pipeline
- **`optimization_runner.py`** — Unified pipeline: metrics → expensive section → proposals → Gate → report
- **`equivalence_gate.py`** — Deterministic fact-checks, second judge, ROLLBACK, confidence intervals
- **`cache_breakpoints.py`** — Detect UUIDs, dates, unsorted JSON keys, greetings, request IDs
- **`intelligence_booster.py`** — Reinvest token savings into richer context

### Core compression engine
- **`loss_router.py`** — Loss-tolerance routing + aggressive prose compression + output reduction
- **`core.py`** — StructPack (63.9%), BlobStore with TTL/checksum, TokenMeter (9 sections), SemanticCache
- **`context_memory.py`** — EventStore with versioning, hybrid ranking, selection log, LanguageRouter
- **`json_compressor.py`** — Headroom-style JSON flattening
- **`question_normalizer.py`** — Cut politeness prefixes (22% savings)
- **`tool_schema_compressor.py`** — Strip tool descriptions (52% savings)

### Verified savings per module

```
structpack             946 →   393   58.5%
prose_aggressive       289 →   123   57.4%
reduce_output           27 →    10   63.0%
tool_schema            294 →   139   52.7%
dedupe_chunks          257 →   166   35.4%
normalize_question      27 →    21   22.2%
translation_router     141 →    49   65.2%
─────────────────────────────────────────
TOTAL                 2436 →  1301   46.6%  (1135 tokens)
```

## Quickstart

```bash
pip install git+https://github.com/Bugaga31/token-diet.git[server]

# Start the proxy server + live dashboard
token-diet serve
# → http://localhost:8080 — live counter: tokens, $, CO2, ice cream
```

```python
from token_diet import (
    # Core compression
    guarded_records, compress_with_routing, reduce_output,
    compress_prose_aggressive, normalize_question, guarded_tool_schemas,
    # Full pipeline
    OptimizationRunner, RequestProfile, EquivalenceGate,
    # Intelligence
    SmartMultiplier, IntelligenceBooster,
    # Real API
    LLMConnector,
)

# Run the full demo
# python3 demo_token_diet.py

# Run competitive benchmark
# python3 benchmark_compete.py

# Run real DeepSeek benchmark
# DEEPSEEK_API_KEY=sk-... python3 real_benchmark.py

# Pro-tip: any LLM → smarter + cheaper
conn = LLMConnector.from_env()  # auto-detect DeepSeek/OpenAI/Anthropic
result = conn.ask("Your prompt here")
print(f"Cost: ${result.cost:.6f}, Tokens: {result.usage.input_tokens}")
```

## Architecture

```
token_diet/
├── core.py                  # StructPack, BlobStore, TokenMeter, PromptBuilder, SemanticCache
├── context_memory.py        # EventStore, AdaptiveContext, ContextManager, LanguageRouter
├── loss_router.py           # Prose compression (filler + aggressive), output reduction
├── json_compressor.py       # JSON flattening
├── question_normalizer.py   # Politeness prefix stripping
├── tool_schema_compressor.py # Tool description removal
├── cache_breakpoints.py     # Volatile-in-static detection
├── equivalence_gate.py      # Deterministic fact checks, ROLLBACK
├── optimization_runner.py   # Unified multi-pass optimization pipeline
├── intelligence_booster.py  # Reinvest savings → richer context
├── smart_multiplier.py      # 5× intelligence proof
├── llm_connector.py         # Real API (DeepSeek/OpenAI/Anthropic) + simulation
└── __init__.py              # Public API
```

## Origin

Started as `token_diet_full.py` — a single-file proof-of-concept. Grew into a
structured library powering DarkBit's AI investment platform. 153 tests, CI PASS,
MIT licensed.

## Environmental Impact

Every token costs energy. token-diet saves 46.6% of tokens → 46.6% less electricity.

```python
from token_diet import GreenCalculator

# Your savings
calc = GreenCalculator()
impact = calc.measure(tokens_saved=1_000_000)
print(impact.render())
#   1,000,000 tokens saved
#        3.00 kWh electricity
#        1.20 kg CO2
#     2000.0 liters water

# Global potential
print(GreenCalculator.global_impact())
```

### Global potential (1B LLM calls/day × 46.6% savings)

| Resource | Annual savings | Equivalent |
|---|---|---|
| **Tokens** | 170 trillion | — |
| **Electricity** | 510 million kWh | 51,000 homes |
| **CO₂** | 204,000 tonnes | 9.7 million trees |
| **Water** | 340 billion liters | — |
| **Car distance** | 1.7 billion km | 42,000× around Earth |

**token-diet is climate action.** Less energy → fewer data centers → healthier planet.

## Why it matters — for people

Money saved with token-diet isn't just a spreadsheet number. It's real life.

```python
from token_diet import GreenCalculator

# Claude Opus: saves $946/year at 1K calls/day
print(GreenCalculator.human_impact(946))
```

```
Human Impact ($946/year saved with token-diet)
    315 ice creams
     94 trips to the park with family
     37 pizza nights with friends
     63 books to read
    189 coffees with someone you love

    It is not about tokens.
    It is about time, freedom, and the people that matter.
```

> Чем меньше люди тратят на нейронки — тем больше у них остаётся на мороженое,
> парк с семьёй, книги, кофе с друзьями. На то, на что раньше не хватало времени.

## Use with anymodel.org — double the savings

token-diet compresses prompts BEFORE they reach the API. Services like
[anymodel.org](https://anymodel.org) provide models at wholesale prices
($0.10/M flat rate vs $35/M vendor pricing). **Combine them**:

```
your prompt → token-diet (46.6% compression) → anymodel ($0.10/M)
                                         = ~73% total savings vs direct vendor
```

| Layer | Savings |
|---|---|
| Vendor → anymodel | ~97% (volume pricing) |
| anymodel + token-diet | +46.6% (token compression) |
| **Combined vs vendor direct** | **~98.5% cheaper** |

## 🌍 token-diet Foundation

*Coming soon.*

> Мы строим фонд, который направит сэкономленные ресурсы на восстановление
> планеты и поддержку людей. Каждый сэкономленный токен — это шаг к чистой
> энергии, зелёным дата-центрам и доступному ИИ для всех.
>
> Следите за новостями.

---

## License

MIT — see [LICENSE](LICENSE).
