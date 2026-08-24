"""Тесты network_control: rate limiter, polite_fetch, бюджет домена."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from token_diet.network_control import (  # noqa: E402
    DomainBudget,
    FetchError,
    RateLimiter,
    domain_of,
    polite_fetch,
)


class TestRateLimiter:
    def test_first_call_free(self):
        rl = RateLimiter(rate=10)
        assert rl.reserve("a", now=100.0) == 0.0

    def test_second_call_waited(self):
        rl = RateLimiter(rate=1.0)
        assert rl.reserve("d", now=100.0) == 0.0
        wait = rl.reserve("d", now=100.4)
        assert abs(wait - 0.6) < 1e-9

    def test_domains_independent(self):
        rl = RateLimiter(rate=1.0)
        assert rl.reserve("x.com", now=1.0) == 0.0
        assert rl.reserve("y.com", now=1.0) == 0.0


class TestDomainOf:
    def test_parse(self):
        assert domain_of("https://www.reddit.com/r/a.json") == "www.reddit.com"
        assert domain_of("not a url") == ""


class TestPoliteFetch:
    @staticmethod
    def ok_fetcher(req, timeout):
        return 200, b"hello"

    def test_success_returns_status_body(self):
        status, body = polite_fetch("https://ex.com/x", fetcher=self.ok_fetcher)
        assert (status, body) == (200, b"hello")

    def test_retries_on_5xx_then_succeeds(self):
        calls = []

        def flaky(req, timeout):
            calls.append(1)
            return (503, b"") if len(calls) < 3 else (200, b"ok")

        status, body = polite_fetch(
            "https://ex.com/y", fetcher=flaky, retries=3, backoff=0.01,
        )
        assert status == 200 and body == b"ok" and len(calls) == 3

    def test_client_error_no_retry(self):
        calls = []

        def client_err(req, timeout):
            calls.append(1)
            return 404, b""

        with pytest.raises(FetchError):
            polite_fetch("https://e.com/z", fetcher=client_err, retries=3)
        assert len(calls) == 1

    def test_exhausted_raises(self):
        def always_500(req, timeout):
            return 500, b""

        with pytest.raises(FetchError, match="попыток"):
            polite_fetch("https://e.com/q", fetcher=always_500, retries=2, backoff=0.001)

    def test_user_agent_sent(self):
        seen = {}

        def spy(req, timeout):
            seen["ua"] = req.headers.get("User-agent") or req.headers.get("User-agent")
            return 200, b""

        polite_fetch("https://e.com/", fetcher=spy)
        assert "token-diet" in (seen.get("ua") or "")


class TestDomainBudget:
    def test_spend_until_exhausted(self):
        budget = DomainBudget(max_per_domain=2)
        url = "https://site.com/a"
        assert budget.spend(url) and budget.spend(url)
        assert not budget.spend(url)

    def test_per_domain_isolation(self):
        budget = DomainBudget(max_per_domain=1)
        assert budget.spend("https://a.com/1")
        assert budget.spend("https://b.com/1")
        assert not budget.spend("https://a.com/2")
