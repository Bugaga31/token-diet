"""Tests for IntelligenceBooster — reinvesting savings into richer context."""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
_TOKEN_DIET = os.path.join(_PKG, "token_diet")
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)
if _TOKEN_DIET not in sys.path:
    sys.path.insert(0, _TOKEN_DIET)


from context_memory import EventStore
from core import BlobStore, PriceTable
from intelligence_booster import IntelligenceBooster
from optimization_runner import OptimizationRunner, RequestProfile

PRICES = PriceTable(3.0, 3.75, 0.30, 15.0)


def _make_profile(**kw):
    defaults = dict(
        task_id="test",
        system_prompt="You are a helpful assistant.",
        records=[{"id": i, "status": "blocked", "owner": "artem"} for i in range(6)],
        question="Summarize the blocked orders for artem.",
    )
    return RequestProfile(**{**defaults, **kw})


def test_booster_returns_tokens_and_allocation():
    profile = _make_profile()
    blobs = BlobStore()
    store = EventStore(path=None)
    store.add("constraint", "Use free APIs only", 0.9)
    store.add("fact", "Artem prefers Python", 0.7)

    runner = OptimizationRunner(PRICES, blobs=blobs)
    booster = IntelligenceBooster(
        runner,
        max_budget=5000,
        extra_documents=[("extra", "This is an extra compliance document. " * 5)],
        event_store=store,
        blobs=blobs,
    )
    result = booster.boost(profile)
    assert result.allocation.extra_documents >= 0
    assert result.budget_used <= result.budget
    assert result.runner_report.savings_tokens > 0
    assert result.iq_density > 0


def test_booster_without_event_store_and_blobs():
    profile = _make_profile()
    runner = OptimizationRunner(PRICES)
    booster = IntelligenceBooster(runner, max_budget=5000)
    result = booster.boost(profile)
    assert result.budget_remaining >= 0
    assert result.allocation.extra_memory_events == 0


def test_booster_summary_readable():
    profile = _make_profile()
    runner = OptimizationRunner(PRICES)
    booster = IntelligenceBooster(runner, max_budget=3000)
    result = booster.boost(profile)
    text = result.summary()
    assert "budget:" in text
    assert "reinvested:" in text
    assert "IQ density" in text


def test_booster_extra_docs_added_to_profile():
    profile = _make_profile(documents=[])
    runner = OptimizationRunner(PRICES)
    booster = IntelligenceBooster(
        runner, max_budget=5000,
        extra_documents=[("policy", "Refund policy text. " * 10)],
    )
    result = booster.boost(profile)
    # boosted profile should have more docs than original
    assert len(result.boosted_profile.documents) > len(profile.documents)
