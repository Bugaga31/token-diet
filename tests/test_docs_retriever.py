"""Tests for docs_retriever — Context7-style docs chunk retrieval."""
import tempfile
from pathlib import Path

from token_diet.docs_retriever import DocsRetriever, estimate_docs_savings

FASTAPI = """
FastAPI is a modern web framework for building APIs with Python.
FastAPI is based on standard Python type hints. It uses Pydantic
for data validation. Path parameters are declared in the path
using curly braces like /items/{item_id}. Query parameters are
declared as function arguments. Request bodies are declared using
Pydantic models. Response models are declared with the response_model
parameter. Dependencies are declared with the Depends function.
FastAPI supports WebSockets, background tasks, and CORS. It generates
OpenAPI documentation automatically at /docs.
"""

REQUESTS = """
The requests library is the standard HTTP client for Python.
Use requests.get(url, params=...) to make a GET request.
Use requests.post(url, json=...) to send JSON data. Sessions
reuse TCP connections and cookies across requests. Headers can be
set per-request or per-session. Timeouts are critical to avoid hangs.
"""


def test_add_and_stats():
    dr = DocsRetriever()
    dr.add("fastapi", FASTAPI)
    dr.add("requests", REQUESTS)
    st = dr.stats()
    assert st["sources"] == 2
    assert st["chunks"] >= 2


def test_retrieve_relevant_fragment():
    dr = DocsRetriever()
    dr.add("fastapi", FASTAPI)
    dr.add("requests", REQUESTS)
    hits = dr.retrieve("path parameters", top_k=2)
    assert hits
    assert hits[0].title == "fastapi"


def test_context_block():
    dr = DocsRetriever()
    dr.add("fastapi", FASTAPI)
    block = dr.context_for_prompt("how to declare query parameters", max_tokens=300)
    assert block.startswith("[Docs context")
    assert "query" in block.lower()


def test_no_results():
    dr = DocsRetriever()
    assert dr.context_for_prompt("anything") == ""


def test_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "index.json"
        dr = DocsRetriever(path)
        dr.add("fastapi", FASTAPI)
        dr2 = DocsRetriever(path)
        assert dr2.stats()["sources"] == 1


def test_estimate_savings():
    s = estimate_docs_savings(30000, 600)
    assert s["savings_pct"] == 98.0
    assert s["saved_tokens"] == 29400
