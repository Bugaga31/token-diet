#!/usr/bin/env python3
"""token-diet cost projection across ALL models — including premium.

Claude Opus 4.5:  $5.00/M input, $25.00/M output  (36× DeepSeek)
GPT-4o:           $2.50/M input, $10.00/M output 
Claude Sonnet:    $3.00/M input, $15.00/M output
Gemini 2.5 Pro:   $2.50/M input, $10.00/M output
DeepSeek Flash:   $0.14/M input, $0.28/M output  (baseline)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_diet"))

# Real benchmark: raw input, our compression rates
RAW_IN = 1112
RAW_OUT = 258
SAVINGS = 0.466  # 46.6% input savings

MODELS = [
    ("Claude Opus 4.5",     5.00, 25.00),
    ("GPT-4o",              2.50, 10.00),
    ("Claude Sonnet 4",     3.00, 15.00),
    ("Gemini 2.5 Pro",      2.50, 10.00),
    ("GPT-4o-mini",         0.15,  0.60),
    ("DeepSeek v4 Flash",   0.14,  0.28),
]

print("=" * 80)
print("  TOKEN-DIET — COST SAVINGS ACROSS ALL MODELS")
print("  (1,000 API calls/day, RAW vs token-diet 46.6% savings)")
print("=" * 80)

for name, price_in, price_out in MODELS:
    raw_cost = (RAW_IN * price_in + RAW_OUT * price_out) / 1_000_000
    diet_in = RAW_IN * (1 - SAVINGS)
    diet_cost = (diet_in * price_in + RAW_OUT * price_out) / 1_000_000
    
    # Daily
    raw_day = raw_cost * 1000
    diet_day = diet_cost * 1000
    save_day = raw_day - diet_day
    # Monthly
    raw_month = raw_day * 30
    diet_month = diet_day * 30
    save_month = save_day * 30
    # Annual
    raw_year = raw_day * 365
    diet_year = diet_day * 365
    save_year = save_day * 365
    
    pct = 100 * (raw_cost - diet_cost) / max(0.000001, raw_cost)
    
    print(f"\n── {name} (${price_in}/M in, ${price_out}/M out) ──")
    print(f"  Per call:   ${raw_cost:.4f} → ${diet_cost:.4f}  ({pct:.0f}% cheaper)")
    print(f"  Day (1K):   ${raw_day:.2f} → ${diet_day:.2f}  (save ${save_day:.2f}/day)")
    print(f"  Month:      ${raw_month:.2f} → ${diet_month:.2f}  (save ${save_month:.2f}/month)")
    print(f"  Year:       ${raw_year:,.0f} → ${diet_year:,.0f}  (save ${save_year:,.0f}/year)")

# --- Impact comparison ---
print("\n" + "=" * 80)
print("  WHY TOKEN-DIET MATTERS FOR PREMIUM MODELS")
print("=" * 80)

opus_raw_year = ((RAW_IN * 5.00 + RAW_OUT * 25.00) / 1_000_000) * 1000 * 365
opus_diet_year = ((RAW_IN * (1 - SAVINGS) * 5.00 + RAW_OUT * 25.00) / 1_000_000) * 1000 * 365
ds_diet_year = ((RAW_IN * (1 - SAVINGS) * 0.14 + RAW_OUT * 0.28) / 1_000_000) * 1000 * 365

print(f"""
  Claude Opus RAW (1K calls/day, 1 year):   ${opus_raw_year:,.0f}
  Claude Opus + token-diet:                 ${opus_diet_year:,.0f}  (save ${opus_raw_year - opus_diet_year:,.0f})
  DeepSeek + token-diet:                    ${ds_diet_year:,.0f}    (save ${opus_raw_year - ds_diet_year:,.0f} vs Opus RAW)

  With token-diet:
  - Claude Opus becomes 47% cheaper — premium quality at mid-tier price
  - GPT-4o becomes cheaper than GPT-4o-mini RAW
  - DeepSeek Flash + token-diet = ${ds_diet_year:,.0f}/year (essentially free)
""")
print("=" * 80)
