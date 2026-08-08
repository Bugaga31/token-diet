#!/usr/bin/env python3
"""Quick cost projection: RAW vs token-diet on DeepSeek and OpenAI."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_diet"))

# Real benchmark numbers from DeepSeek Flash
raw_in, raw_out = 1112, 258
diet_in, diet_out = 621, 300
x5_in, x5_out = 2085, 300

# With all optimizations (46.6% savings on input, same output)
best_in = int(raw_in * 0.534)  # 46.6% input savings
best_out = raw_out  # same output quality

print("=" * 72)
print("  TOKEN-DIET — REAL COST PROJECTIONS")
print("=" * 72)

# DeepSeek Flash prices
def cost_ds(t_in, t_out):
    return t_in * 0.14 / 1_000_000 + t_out * 0.28 / 1_000_000

# OpenAI GPT-4o-mini prices
def cost_openai(t_in, t_out):
    return t_in * 0.15 / 1_000_000 + t_out * 0.60 / 1_000_000

for provider, cost_fn in [("DeepSeek Flash", cost_ds), ("GPT-4o-mini", cost_openai)]:
    print(f"\n── {provider} ──")
    raw_1 = cost_fn(raw_in, raw_out)
    diet_1 = cost_fn(diet_in, diet_out)
    best_1 = cost_fn(best_in, best_out)
    
    print(f"  Per call:")
    print(f"    RAW:      ${raw_1:.6f}")
    print(f"    DIET:     ${diet_1:.6f}  ({100*(raw_1-diet_1)/raw_1:.0f}% cheaper)")
    print(f"    ALL-OPTS: ${best_1:.6f}  ({100*(raw_1-best_1)/raw_1:.0f}% cheaper)")
    
    for calls in [100, 1000, 10000, 100000]:
        raw = raw_1 * calls
        diet = diet_1 * calls
        best = best_1 * calls
        saved_vs_raw = raw - diet
        saved_best = raw - best
        print(f"  {calls:>6} calls/day:")
        print(f"    RAW:      ${raw:.2f}")
        print(f"    DIET:     ${diet:.2f}  (save ${saved_vs_raw:.2f})")
        print(f"    ALL-OPTS: ${best:.2f}  (save ${saved_best:.2f})")
    
    # Monthly
    raw_m = raw_1 * 1000 * 30
    diet_m = diet_1 * 1000 * 30
    best_m = best_1 * 1000 * 30
    print(f"  Monthly (1K calls/day):")
    print(f"    RAW:      ${raw_m:.2f}")
    print(f"    DIET:     ${diet_m:.2f}  (save ${raw_m-diet_m:.2f}/month)")
    print(f"    ALL-OPTS: ${best_m:.2f}  (save ${raw_m-best_m:.2f}/month)")

# Annual projection
print(f"\n── ANNUAL (1K calls/day, DeepSeek) ──")
raw_y = cost_ds(raw_in, raw_out) * 1000 * 365
diet_y = cost_ds(diet_in, diet_out) * 1000 * 365
best_y = cost_ds(best_in, best_out) * 1000 * 365
print(f"  RAW:      ${raw_y:.2f}/year")
print(f"  DIET:     ${diet_y:.2f}/year  (save ${raw_y-diet_y:.2f})")
print(f"  ALL-OPTS: ${best_y:.2f}/year  (save ${raw_y-best_y:.2f})")
print("=" * 72)
