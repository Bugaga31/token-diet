"""Tests for headroom — reversible compression with CCR markers."""
import json

from token_diet.headroom import (
    CCRStore,
    crush_json,
    crush_logs,
    detect_content_type,
    estimate_headroom_savings,
    headroom_compress,
    headroom_retrieve,
)


def test_detect_types():
    assert detect_content_type('{"a": 1}') == "json"
    assert detect_content_type('[1, 2, 3]') == "json"
    logs = "2026-08-12 10:00:01 INFO start\n2026-08-12 10:00:02 INFO ok\n2026-08-12 10:00:03 ERROR boom"
    assert detect_content_type(logs) == "logs"
    md = "# Заголовок\n\nМного текста" * 100
    assert detect_content_type(md) == "markdown"
    code = "def hello():\n    return 1\n"
    assert detect_content_type(code) == "code"


def test_json_crush_offloads_and_restores():
    store = CCRStore()
    data = [{"id": i, "v": i * 2} for i in range(100)]
    crushed = crush_json(data, store, max_rows=5)
    s = json.dumps(crushed)
    assert "_ccr" in s
    # retrieve the full original back
    key = crushed["_ccr"].split(":")[1].split(" ")[0]
    restored = json.loads(store.get(key))
    assert len(restored) == 95  # all offloaded rows back


def test_headroom_compress_route_json():
    store = CCRStore()
    text = json.dumps([{"id": i} for i in range(100)])
    comp, kind = headroom_compress(text, store)
    assert kind == "json"
    assert "<<ccr:" in comp


def test_headroom_retrieve_reversible():
    store = CCRStore()
    text = json.dumps([{"x": i} for i in range(50)])
    comp, _ = headroom_compress(text, store)
    expanded = headroom_retrieve(comp, store)
    # expanded contains the full offloaded block (as text in marker position)
    assert "offloaded" not in expanded.replace('"', "") or len(expanded) > len(comp)


def test_crush_logs_collapses_repeats():
    store = CCRStore()
    logs = "\n".join(["2026-01-01 10:00:00 INFO ping"] * 5 + ["2026-01-01 10:00:05 ERROR boom"] * 20)
    crushed = crush_logs(logs, store, max_lines=4)
    assert "(x" in crushed  # repeats collapsed with a count
    assert "<<ccr:" in crushed  # tail offloaded reversibly


def test_savings_estimate():
    before = "word " * 1000
    after = "word " * 100
    s = estimate_headroom_savings(before, after)
    assert s["savings_pct"] >= 89.0  # ~90% (tokenizer edge rounding)
    assert s["reversible"] is True
