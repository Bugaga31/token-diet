"""Tests for open_generative — gateway with failover chains and cache."""
from token_diet.open_generative import (
    GenerativeGateway,
    Provider,
    estimate_gateway_savings,
    normalize_payload,
    payload_key,
)


def test_normalize_payload():
    out = normalize_payload({"model_name": "gpt", "temp": 0.2, "max_output_tokens": 100, "prompt": "hi"})
    assert out["model"] == "gpt"
    assert out["temperature"] == 0.2
    assert out["max_tokens"] == 100
    assert out["prompt"] == "hi"


def test_payload_key_deterministic():
    assert payload_key({"a": 1, "b": 2}) == payload_key({"b": 2, "a": 1})
    assert payload_key({"a": 1}) != payload_key({"a": 2})


def test_failover_on_429():
    gw = GenerativeGateway()
    gw.add(Provider("a", handler=lambda p: {"status": 429}))
    gw.add(Provider("b", handler=lambda p: {"status": 200, "data": {"text": "ok"}}))
    res = gw.generate({"prompt": "hi"})
    assert res.ok is True
    assert res.provider == "b"
    assert res.attempts == 2


def test_cache_hit():
    gw = GenerativeGateway()
    gw.add(Provider("a", handler=lambda p: {"status": 200, "data": "first"}))
    r1 = gw.generate({"prompt": "same"}, use_cache=True)
    r2 = gw.generate({"prompt": "same"}, use_cache=True)
    assert r1.cached is False
    assert r2.cached is True
    assert gw.summary()["cache_hits"] == 1


def test_all_retryable_fails():
    gw = GenerativeGateway()
    gw.add(Provider("a", handler=lambda p: {"status": 503}))
    gw.add(Provider("b", handler=lambda p: {"status": 429}))
    res = gw.generate({"prompt": "hi"})
    assert res.ok is False
    assert res.attempts == 2


def test_choose_cheapest():
    gw = GenerativeGateway()
    gw.add(Provider("premium", cost_per_1m_input=3.0, cost_per_1m_output=15.0))
    gw.add(Provider("budget", cost_per_1m_input=0.14, cost_per_1m_output=0.28))
    assert gw.choose_cheapest(1000, 500) == "budget"


def test_estimate_savings():
    s = estimate_gateway_savings(1_000_000, 0.5, 2000, 0.14)
    assert s["cache_hit_rate"] == 0.5
    assert s["saved_tokens"] == 1_000_000_000
    assert s["saved_usd"] > 0
