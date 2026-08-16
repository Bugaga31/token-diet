# Token Diet — CI report

**result:** PASS

## Tokens per module (before → after)

| module | before | after | saved | % | notes |
|---|---|---|---|---|---|
| structpack | 371 | 162 | 209 | 56.3% | structpack |
| blob_reference | 456 | 456 | 0 | 0.0% | blob refs x2 (GUARD: +90 tokens, not applied) |
| dedupe_chunks | 456 | 228 | 228 | 50.0% | 1 duplicates dropped |
| prose_compression | 217 | 120 | 97 | 44.7% | long prose, filler removal |
| json_tool_result | 1359 | 1272 | 87 | 6.4% | Headroom-style flatten |
| translation_router | 250 | 140 | 110 | 44.0% | used=True reuse=3 (synthetic ~45% mock) |
| cache_static_block | 61 | 61 | 0 | 0.0% | cache-safe=True volatile=0 |
| event_memory | 38 | 38 | 0 | 0.0% | 4/4 events, v2 supersedes v1 |
| **TOTAL** | **3208** | **2477** | **731** | **22.8%** | |

## End-to-end

# Optimization report — ci-e2e

- **tokens before**: 1019
- **tokens after**: 582
- **saved**: 437 (42.9%)

## Proposals
- **dedupe_chunks** → `retrieved_context_docs`: 456 → 228 tokens (risk none): 1 near-duplicate chunk(s) removed
- **structpack** → `retrieved_context`: 371 → 162 tokens (risk none): lossless column packing, mode=structpack; round-trip safe=True
- **compress_prose_aggressive** → `history`: 91 → 45 tokens (risk medium): sentence-level filtering: dropped 46 tokens; numbers/dates/entities/negations preserved

## Rejected (guard reasons)
- **obsidian_memory** → `history`: no ObsidianMemoryStore configured
- **neural_noise_filter** → `history`: Neural Scorer not available
- **blob_reference** → `retrieved_context_docs`: GUARD: blob ref would cost +90 tokens (need <5% savings)

baseline cost: $0.0031

optimized cost: $0.0017

cost regression: OK

equivalence gate: PASS

```
case=ci_regression decision=PASS similarity=0.900 (1 samples)
  judge1: ok
  all 2 critical facts preserved; similarity ok
```

## Packaging

artifact: `/home/ro/token-diet/token-diet-lib.zip` (235 files)

artifact complete: all promised modules + tests + README present.
