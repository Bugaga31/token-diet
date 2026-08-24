"""Tests for cache_keepalive (arXiv:2607.19214) and tron_format (arXiv:2605.29676)."""

from __future__ import annotations

import time
import unittest

from token_diet.cache_keepalive import (
    CachePolicy,
    KeepaliveManager,
    estimate_keepalive_savings,
)
from token_diet.tron_format import (
    savings as tron_savings,
)
from token_diet.tron_format import (
    to_tron,
    to_tron_schemas,
    tron_roundtrip_safe,
)

TOOLS = [
    {"name": "get_weather", "description": "Current weather for a city",
     "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}},
    {"name": "get_stock", "description": "Stock price for a ticker",
     "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}}},
    {"name": "send_email", "description": "Send an email message",
     "parameters": {"type": "object", "properties": {"to": {"type": "string"},
                                                     "body": {"type": "string"}}}},
]


class TestCachePolicy(unittest.TestCase):
    def test_optimal_interval(self):
        p = CachePolicy(ttl_seconds=300)
        self.assertEqual(p.optimal_interval, 240)  # TTL - 60

    def test_break_even_horizon(self):
        p = CachePolicy(ttl_seconds=300, warm_read_ratio=0.1)
        # I_max = 240 * (1/0.1 - 1) = 2160s = 36 min
        self.assertEqual(p.break_even_horizon, 2160)


class TestKeepaliveManager(unittest.TestCase):
    def test_no_ping_when_warm(self):
        km = KeepaliveManager()
        now = time.time() + 30   # only 30s idle
        self.assertFalse(km.should_ping(now))

    def test_ping_when_approaching_ttl(self):
        km = KeepaliveManager()
        now = time.time() + 250  # idle > 240s optimum
        self.assertTrue(km.should_ping(now))

    def test_let_die_after_break_even(self):
        km = KeepaliveManager()
        now = time.time() + 4000  # > 2160s break-even
        d = km.decide(now)
        self.assertTrue(d.should_let_die)
        self.assertFalse(d.should_ping)

    def test_record_ping_refreshes(self):
        km = KeepaliveManager()
        km.record_ping(now=time.time())
        now = time.time() + 100  # fresh clock → no ping needed
        self.assertFalse(km.should_ping(now))

    def test_stats(self):
        km = KeepaliveManager()
        km.record_call(cache_warm=True)
        km.record_call(cache_warm=True)
        km.record_call(cache_warm=False)
        self.assertEqual(km.stats.savings_pct, 66.7)
        self.assertIn("Keepalive", km.stats.render())


class TestEstimateSavings(unittest.TestCase):
    def test_positive_savings(self):
        r = estimate_keepalive_savings(n_calls=50, input_tokens=5000)
        self.assertGreater(r["savings_pct"], 50)
        self.assertGreater(r["savings_usd"], 0)
        self.assertEqual(r["optimal_ping_seconds"], 240)


class TestTronFormat(unittest.TestCase):
    def test_class_instances(self):
        tron = to_tron_schemas(TOOLS)
        self.assertIn("CLASS Tool(name,description,parameters):", tron)
        self.assertIn("instance(", tron)
        # each tool appears once as instance
        self.assertEqual(tron.count("instance("), 3)

    def test_saves_tokens(self):
        r = tron_savings(TOOLS)
        self.assertLess(r["tron_tokens"], r["json_tokens"])
        self.assertGreater(r["savings_pct"], 0)

    def test_scalar_dict(self):
        tron = to_tron({"a": 1, "b": "x"})
        self.assertIn("a=1", tron)
        self.assertIn("b=x", tron)

    def test_roundtrip_guard(self):
        chk = tron_roundtrip_safe(TOOLS, parser_ok=True)
        self.assertTrue(chk.ok)
        self.assertGreater(chk.savings_pct, 0)
        bad = tron_roundtrip_safe(TOOLS, parser_ok=False)
        self.assertFalse(bad.ok)

    def test_empty(self):
        self.assertEqual(to_tron_schemas([]), "")


if __name__ == "__main__":
    unittest.main()
