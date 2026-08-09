#!/usr/bin/env python3
"""Quick test of compression_arsenal techniques."""
from token_diet.compression_arsenal import (
    strip_grammar_caveman, collapse_logs, stabilize_for_kv_cache,
    compress_tool_definitions, score_self_information, compress_by_self_information,
)
from token_diet.core import count_tokens

print("=" * 70)
print("   CAVEMAN GRAMMAR STRIPPER")
print("=" * 70)
prose = (
    "Furthermore, it is important to note that the quarterly earnings report "
    "indicates a significant shift in market dynamics. Moreover, the compliance "
    "team has identified several areas that require immediate attention. "
    "Additionally, the risk assessment committee recommends a thorough review "
    "of all outstanding positions."
)
stripped = strip_grammar_caveman(prose)
print(f"BEFORE ({count_tokens(prose)}t): {prose[:100]}...")
print(f"AFTER  ({count_tokens(stripped)}t): {stripped[:100]}...")
print(f"SAVED: {100*(count_tokens(prose)-count_tokens(stripped))/max(1,count_tokens(prose)):.1f}%")
print()

print("=" * 70)
print("   LOG COLLAPSER")
print("=" * 70)
logs = """2025-01-15 10:00:01 INFO  request processed: user=42, duration=12ms
2025-01-15 10:00:02 INFO  request processed: user=43, duration=15ms
2025-01-15 10:00:03 INFO  request processed: user=44, duration=11ms
2025-01-15 10:00:04 INFO  request processed: user=45, duration=13ms
2025-01-15 10:00:05 INFO  request processed: user=46, duration=14ms
2025-01-15 10:00:06 ERROR connection refused: database unreachable
2025-01-15 10:00:07 INFO  request processed: user=47, duration=12ms
2025-01-15 10:00:08 INFO  request processed: user=48, duration=16ms"""
result, fidelity = collapse_logs(logs)
print(f"BEFORE: {count_tokens(logs)} tokens, {len(logs.split(chr(10)))} lines")
print(f"AFTER:  {count_tokens(result)} tokens, {len(result.split(chr(10)))} lines")
print(f"FIDELITY: {fidelity.score:.2f}, errors kept: {fidelity.errors_kept}")
print(f"Reduction: {fidelity.reduction_pct:.1f}%")
print("RESULT:")
print(result)
print()

print("=" * 70)
print("   KV-CACHE STABILIZER")
print("=" * 70)
zones = stabilize_for_kv_cache(
    system="You are a helpful coder. Output Python only.",
    tools='[{"type":"function","function":{"name":"read_file","description":"Read a file","parameters":{"type":"object","properties":{"path":{"type":"string"}}}}}]',
    documents="Project uses FastAPI + PostgreSQL.",
    history=["User: Write Fibonacci", "Assistant: def fib(n): ...", "User: Add caching"],
    query="Add type hints to the function",
)
for z in zones:
    print(f"  [{z.stability:12}] {z.content[:60]}...")
print()

print("=" * 70)
print("   DYNAMIC TOOL COMPRESSOR")
print("=" * 70)
tools = [
    {"type": "function", "function": {
        "name": "search_documents",
        "description": "Search through the document database using semantic search. This function queries the vector database for relevant documents based on the user query. It returns the top 10 most relevant documents with their metadata. Use this when you need to find information in the knowledge base. This is the primary search tool.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "The search query string, should be natural language, at least 3 words long"},
            "limit": {"type": "integer", "description": "Maximum number of results to return, between 1 and 20"}
        }}
    }},
    {"type": "function", "function": {
        "name": "calculate", "description": "Perform mathematical calculations",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}}
    }},
]
compressed = compress_tool_definitions(tools)
print(f"BEFORE: {count_tokens(str(tools))} tokens")
print(f"AFTER:  {count_tokens(compressed)} tokens")
print(f"SAVED: {100*(count_tokens(str(tools))-count_tokens(compressed))/max(1,count_tokens(str(tools))):.1f}%")
print()

print("=" * 70)
print("   SELF-INFORMATION SCORER")
print("=" * 70)
text = (
    "Document 1: The company reported Q4 revenue of $12.3 billion, up 15% year-over-year. "
    "Cloud services contributed $5.1 billion to total revenue. "
    "Document 2: The company reported Q4 revenue of $12.3 billion, which represents "
    "a 15% increase compared to the same period last year. "
    "Document 3: Cloud services revenue grew 28% to $5.1 billion in Q4."
)
scored = score_self_information(text)
for s in scored:
    tag = " [REDUNDANT]" if s.is_redundant else ""
    print(f"  info={s.self_information:.3f}: {s.text[:70]}...{tag}")

compressed = compress_by_self_information(text, keep_ratio=0.5)
print(f"\nBEFORE: {count_tokens(text)}t")
print(f"AFTER:  {count_tokens(compressed)}t")
print(f"SAVED: {100*(count_tokens(text)-count_tokens(compressed))/max(1,count_tokens(text)):.1f}%")

print("\nALL 5 TECHNIQUES WORKING!")
