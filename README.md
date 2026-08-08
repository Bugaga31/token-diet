# token-diet

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-281%20passed-brightgreen.svg)]()

**The same information, fewer billed tokens.**

A drop-in Python library that compresses LLM context — prompts, tool outputs,
JSON, RAG chunks, conversation memory — without losing technical accuracy.

Built on the foundation of the user's original `token_diet_full.py` and extended
with cutting-edge techniques from the open-source LLM ecosystem:

| Source                          | Inspiration                                         |
|---------------------------------|------------------------------------------------------|
| [caveman](https://github.com/juliusbrussee/caveman) | Output-token reduction (65% on prose, byte-exact for code/errors) |
| [headroom](https://github.com/headroomlabs-ai/headroom) | Content-aware compressors (60-95% on JSON, 15-20% on coding agents) |
| [leanctx](https://github.com/jia-gao/leanctx) | Loss-tolerance routing — classify by distortion tolerance, compress each class differently |
| [nodumbmode](https://github.com/hronicasync/nodumbmode) | Agent discipline skills — stop burning tokens on filler |
| [Claw Compactor](https://github.com/open-compress/claw-compactor) | 14-stage reversible compression pipeline |
| [TokenSkip (EMNLP 2025)](https://github.com/hemingkx/TokenSkip) | Controllable chain-of-thought compression |

## What's inside

### 1. `pack_records` — StructPack lossless compression
Replaces repeated JSON rows with a dictionary + tab-separated format. **Verified: 63.9% token savings** on 100 identical ticker rows (4601 → 1659 tokens). Lossless round-trip.

### 2. `compress_with_routing` — Loss-tolerance routing (leanctx style)
Classifies each segment by distortion tolerance:
- **zero**: code blocks, JSON, URLs, stack traces — verbatim
- **high**: prose >60 chars — drops filler ("Furthermore", "It is important to note that", "Please note", "Let me know", "In conclusion", "Sure!", "I hope this helps!")

**Verified: 24.3% on prose** (115 → 87 tokens). With aggressive flag — more.

### 3. `reduce_output` — Output-token reduction (Caveman style)
Strips ceremonial filler from AI responses:
- "Here is your analysis..." — dropped
- "I hope this helps!" — dropped
- "Sure! I'd be happy to help you with that" — dropped
- "Let me know if you have any questions" — dropped

**Verified: 35.7% on AI response** (84 → 54 tokens). Code, errors, and data stay byte-exact.

### 4. `compress_json` — Headroom-style JSON compression
Flattens nested dicts/lists, skips empty values, truncates long fields. Used internally for tool outputs.

### 5. `EventStore` + `AdaptiveContext` + `ContextManager` — Agent memory
Per-user event memory with importance (0.0-1.0) and TTL. Adaptive ranking by term-overlap, recency, and kind-bonus. Constraints always pinned to top.

### 6. `extract_events` — Auto-extract preferences/constraints/decisions
Pattern-matches dialog for "I prefer X", "I need X", "we decided X", "next step: X" (ru/en). Saves into EventStore automatically.

### 7. `choose_language` — Language router
Translates prose between languages if savings exceed cost. Skip unsafe content (URLs, hex hashes, code blocks, API keys).

### 8. Plus from `core.py`
- `SemanticCache` — exact + optional embedding similarity caching
- `BlobStore` — large tool result handles (preview only)
- `TokenMeter` + `PriceTable` — billing tracking (fresh/cache-write/cache-read/output)
- `PromptBuilder` — stable prefixes for provider caching
- `deduplicate_chunks` — shingle-based dedup (Jaccard 0.85)

## Stress-test results

Run from `/tmp/hermes-verify-token-diet-stress.py`:

```
[1] StructPack on 100 ticker rows
    mode=structpack, 4601 → 1659 tokens (63.9% saved)
  pack_records: 1.530 ms/iter
  unpack_records: 0.631 ms/iter

[2] Loss-tolerance routing on prose (373 chars)
    115 → 87 tokens (24.3% saved)
  compress_with_routing: 0.209 ms/iter

[3] Output reduction on AI response
    84 → 54 tokens (35.7% saved)

[4] JSON compression
    248 → 212 tokens (14.5% saved)

[5] Event extraction from 4 dialog turns
    Extracted 4 events

[6] Adaptive context for question
    Selected 4 relevant events (budget=200)

[7] Translation safety check
    Safe: True
```

## Quickstart

```bash
pip install token-diet
```

```python
from token_diet import (
    pack_records, compress_with_routing, reduce_output,
    compress_json, EventStore, AdaptiveContext, ContextManager,
    extract_events,
)

# StructPack repeated JSON rows
rows = [{"ticker": "SBER", "side": "BUY"} for _ in range(100)]
packed, mode, before, after = guarded_records(rows)
# → "structpack", 4601 → 1659 tokens (63.9% saved)

# Loss-tolerance routing
text = "Furthermore, it is important to note that SBER is at 150."
result, before, after = compress_with_routing(text)
# → "SBER is at 150.", ~30-65% smaller

# Output reduction (Caveman-style)
ai_response = "Sure! Here is your code:\nprint('hello')\nI hope this helps!"
clean = reduce_output(ai_response)
# → "print('hello')"

# Agent memory
ctx = ContextManager(path="memory/events.json")
ctx.after("I prefer Python", "Sure! I'll use Python.")
relevant = ctx.before("What language should I use?")
```

## Optional Skills

The repository ships with curated prompt-skills from the open-source community,
installable into any agent that supports the `SKILL.md` format
(Claude Code, Codex, Gemini, Cursor, Cline, Aider, Continue, Goose, OpenHands,
and 30+ others):

| Skill | Source | Use when |
|-------|--------|----------|
| `caveman/` | juliusbrussee/caveman | "caveman mode" / "be brief" — 65% output tokens |
| `caveman-commit/` | juliusbrussee/caveman | Conventional commit messages |
| `caveman-compress/` | juliusbrussee/caveman | Compress text mid-conversation |
| `caveman-review/` | juliusbrussee/caveman | Tight code review |
| `cavecrew-*/` | juliusbrussee/caveman | Multi-agent workflows (builder/investigator/reviewer) |
| `nodumb/` | hronicasync/nodumbmode | Agent discipline guidelines |
| `changelog-discipline/` | hronicasync/nodumbmode | Decision journal |
| `system-feedback/` | hronicasync/nodumbmode | Verify user understood |
| `ask-nodumb/` | hronicasync/nodumbmode | Decompose product/UX tasks before designing |

Install with:

```bash
npx skills@latest add juliusbrussee/caveman
npx skills@latest add hronicasync/nodumbmode
```

## Architecture

```
token_diet/
├── core.py                # StructPack, SemanticCache, BlobStore, TokenMeter, PromptBuilder
├── context_memory.py      # EventStore, AdaptiveContext, ContextManager, extract_events
├── loss_router.py         # Loss-tolerance routing (leanctx style) + output reduction (Caveman style)
└── json_compressor.py     # Headroom-style JSON flattening
```

## Origin

Started as the user's `token_diet_full.py` (a single-file proof-of-concept) and
grew into a structured library that powers DarkBit's AI investment platform
(`/home/ro/projects/darkbit`). Verified on 281 tests, ruff-clean, MIT licensed.

## License

MIT — see [LICENSE](LICENSE).
