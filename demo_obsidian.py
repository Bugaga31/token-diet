#!/usr/bin/env python3
"""Quick demo: ObsidianMemoryStore — linked-note knowledge base for LLMs."""

from token_diet.obsidian_memory import ObsidianMemoryStore, estimate_memory_savings
from token_diet.core import count_tokens

store = ObsidianMemoryStore()

# Build a knowledge base like Obsidian
store.remember(
    "Refund Policy",
    "Refunds within 14 calendar days. 15% restocking fee for opened items. "
    "Shipping non-refundable. RMA number required. See [[Compliance Rules]].",
    kind="fact", tags=["policy", "refunds"], importance=0.9
)
store.remember(
    "Compliance Rules",
    "T+1 reporting for all trades. 0.25% penalty per day late. "
    "Blocked orders audited within 48 hours. See [[Audit Checklist]].",
    kind="constraint", tags=["compliance"], importance=0.95
)
store.remember(
    "Audit Checklist",
    "1) Verify RMA status. 2) Check counterparty rating. "
    "3) Confirm trade timestamp. 4) Validate settlement currency.",
    kind="reference", tags=["audit"], importance=0.7
)
store.remember(
    "Shipping Policy",
    "Free shipping over $50. Express $15 flat rate. "
    "International shipping available to 40 countries.",
    kind="fact", tags=["shipping"], importance=0.4
)
store.remember(
    "API Keys",
    "Store all API keys in .env file. Never commit to git. "
    "Rotate keys every 90 days. See [[Security Checklist]].",
    kind="constraint", tags=["security", "api"], importance=0.85
)

print("=" * 70)
print("  ObsidianMemoryStore — Linked-note knowledge base")
print("=" * 70)

# Show stats
stats = store.stats()
print(f"\n  📊 Stats: {stats['total_notes']} notes, {stats['total_links']} links")
print(f"  By kind: {stats['by_kind']}")
print(f"  Top tags: {stats['top_tags']}")

# 1. Direct retrieval
print("\n  ── Direct retrieval: 'refund policy' ──")
for r in store.retrieve("refund policy compliance"):
    print(f"    {r.note.title} (score={r.score:.2f}, {r.match_type})")

# 2. Graph traversal
print("\n  ── Graph traversal from 'Refund Policy' (depth 2) ──")
for r in store.graph("Refund Policy", depth=2):
    print(f"    {'  ' * r.graph_distance}↳ {r.note.title} (dist={r.graph_distance}, path={' → '.join(r.path)})")

# 3. Backlinks
print("\n  ── Backlinks to 'Compliance Rules' ──")
for note in store.backlinks("Compliance Rules"):
    print(f"    ← {note.title}")

# 4. Hybrid search
print("\n  ── Hybrid search: 'audit compliance' ──")
for r in store.search_hybrid("audit compliance", start_title="Refund Policy"):
    kind_icon = {"fact": "📋", "constraint": "🔒", "reference": "📖"}.get(r.note.kind, "📝")
    print(f"    {kind_icon} {r.note.title} (score={r.score:.2f}, dist={r.graph_distance})")

# 5. Context for prompt
print("\n  ── Context for LLM prompt ──")
context = store.context_for_prompt("refund policy compliance", start_title="Refund Policy")
print(context)

# 6. Token savings
full_context = " ".join(n.content for n in store.notes.values())
full_tokens = count_tokens(full_context)
context_tokens = count_tokens(context)
savings = estimate_memory_savings(full_tokens, context_tokens)

print(f"\n  ── Token Savings ──")
print(f"  Full context:     {full_tokens} tokens")
print(f"  Retrieved notes:  {context_tokens} tokens")
print(f"  Saved:            {savings['saved_tokens']} tokens ({savings['savings_pct']}%)")
print(f"  CO₂ per 1M calls: {savings['co2_kg_per_1M_calls']} kg")
print(f"\n  💡 Instead of {full_tokens}t of context → only {context_tokens}t relevant linked notes")
print(f"     Model gets smarter context for {savings['savings_pct']}% less tokens.")
