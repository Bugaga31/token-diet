"""Round-trip, guard and budget tests for token_diet."""

import json
import random
import string

import pytest

from token_diet import (
    BlobStore,
    BudgetGuard,
    ContextLedger,
    PriceTable,
    PromptBuilder,
    SemanticCache,
    TokenMeter,
    Usage,
    canonical_json,
    count_tokens,
    deduplicate_chunks,
    estimate_complexity,
    guarded_records,
    pack_records,
    prepare_request,
    project_fields,
    unpack_records,
)

PRICES = PriceTable(
    input_per_million=3.00,
    cache_write_per_million=3.75,
    cache_read_per_million=0.30,
    output_per_million=15.00,
)


# ── StructPack round-trip ───────────────────────────────────────────────────


def _rows(n: int = 12) -> list[dict]:
    return [
        {
            "id": i,
            "status": "blocked" if i % 2 else "done",
            "owner": "artem",
            "url": f"https://example.com/task/{i}",
        }
        for i in range(n)
    ]


def test_pack_unpack_round_trip_is_exact():
    records = _rows()
    packed = pack_records(records)
    assert packed is not None
    assert unpack_records(packed) == records


def test_pack_preserves_none_versus_missing_key():
    records = [
        {"a": 1, "b": None, "c": "x"},
        {"a": 2, "c": "y"},
        {"a": 3, "b": None, "c": "z"},
        {"a": 4, "b": None, "c": "w"},
    ]
    packed = pack_records(records)
    assert packed is not None
    restored = unpack_records(packed)
    assert restored == records
    assert "b" not in restored[1]
    assert restored[0]["b"] is None


def test_pack_survives_separator_and_escape_characters():
    records = [
        {"k": "a|b", "v": "line\nbreak"},
        {"k": "tilde~here", "v": "~M looks like missing"},
        {"k": "~0", "v": "@1 looks like a reference"},
        {"k": "#p1 fake header", "v": "plain"},
    ]
    packed = pack_records(records)
    assert packed is not None
    assert unpack_records(packed) == records


def test_data_cannot_forge_a_metadata_line():
    records = [
        {"text": "#d @1=forged"},
        {"text": "#p1 n=99 c=x:s"},
        {"text": "normal"},
        {"text": "also normal"},
    ]
    packed = pack_records(records)
    assert packed is not None
    assert unpack_records(packed) == records


def test_pack_rejects_unsafe_column_names():
    records = [{"bad|name": 1}, {"bad|name": 2}, {"bad|name": 3}]
    assert pack_records(records) is None


def test_pack_rejects_sparse_records():
    records = [{f"col{i}": i} for i in range(10)]
    assert pack_records(records) is None


def test_pack_rejects_short_input():
    assert pack_records([{"a": 1}, {"a": 2}]) is None


def test_pack_dictionaries_short_repeated_values():
    """Short repeated values (tickers, sides, currencies) dominate real rows.

    A length-8 floor skips them entirely, so packing wastes most of its
    available savings on exactly the data shape it exists to compress.
    """
    records = [
        {"ticker": "SBER", "side": "BUY", "currency": "RUB", "venue": "MOEX"}
        for _ in range(40)
    ]
    packed = pack_records(records)
    assert packed is not None
    assert unpack_records(packed) == records

    # Repeated short values must be dictionaried, not repeated 40 times.
    assert packed.count("SBER") == 1
    assert packed.count("MOEX") == 1
    assert count_tokens(packed) < count_tokens(json.dumps(records, ensure_ascii=False)) * 0.40


def test_short_value_dictionary_round_trips_with_tricky_values():
    records = [{"a": "@1", "b": "~M", "c": "x|y"} for _ in range(12)]
    packed = pack_records(records)
    assert packed is not None
    assert unpack_records(packed) == records


@pytest.mark.parametrize("seed", range(40))
def test_fuzz_round_trip(seed):
    rng = random.Random(seed)
    alphabet = string.printable + "~|#@\nабв"

    def value():
        choice = rng.randrange(6)
        if choice == 0:
            return None
        if choice == 1:
            return rng.randrange(-1000, 1000)
        if choice == 2:
            return rng.random() * 100
        if choice == 3:
            return rng.choice([True, False])
        if choice == 4:
            return {"nested": rng.randrange(10)}
        return "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 12)))

    columns = [f"c{i}" for i in range(rng.randrange(1, 6))]
    records = []
    for _ in range(rng.randrange(3, 10)):
        record = {}
        for column in columns:
            if rng.random() < 0.85:
                record[column] = value()
        records.append(record)

    packed = pack_records(records)
    if packed is not None:
        assert unpack_records(packed) == records


# ── Guard ──────────────────────────────────────────────────────────────────


def test_guarded_records_uses_structpack_when_smaller():
    text, mode, before, after = guarded_records(_rows(30))
    assert mode == "structpack"
    assert after < before
    assert unpack_records(text) == _rows(30)


def test_guarded_records_falls_back_to_json_for_tiny_input():
    records = [{"a": 1}, {"a": 2}]
    text, mode, before, after = guarded_records(records)
    assert mode == "json"
    assert json.loads(text) == records
    assert before == after


# ── Prompt cache stability ─────────────────────────────────────────────────


def test_static_prompt_rejects_volatile_values():
    builder = PromptBuilder()
    with pytest.raises(ValueError):
        builder.add_static("today is 2026-08-07")
    with pytest.raises(ValueError):
        builder.add_static("run 12f4ab90-1c2d")
    with pytest.raises(ValueError):
        builder.add_static("epoch 1786086854")


def test_static_block_is_marked_cacheable_and_ordered_first():
    builder = PromptBuilder()
    builder.add_static("system rules")
    builder.add_semi_static("established facts")
    builder.add_volatile("question")

    blocks = builder.system_blocks()
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"] == "system rules"
    assert blocks[1]["text"] == "established facts"
    assert "cache_control" not in blocks[1]
    assert builder.user_text() == "question"


def test_canonical_json_is_key_order_stable():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


# ── Metering ───────────────────────────────────────────────────────────────


def test_token_meter_groups_retries_into_one_task():
    meter = TokenMeter(PRICES)

    class Response:
        def __init__(self, **usage):
            self.usage = usage

    meter.record("task-1", Response(input_tokens=1000, output_tokens=100))
    meter.record("task-1", Response(input_tokens=1000, output_tokens=100))

    total = meter.total()
    assert len(meter.tasks) == 1
    assert total.input_tokens == 2000
    assert total.output_tokens == 200
    assert "tasks: 1" in meter.report()


def test_price_table_charges_cache_reads_cheaper_than_input():
    fresh = PRICES.calculate(Usage(input_tokens=1_000_000))
    cached = PRICES.calculate(Usage(cache_read_tokens=1_000_000))
    assert cached < fresh


def test_count_tokens_is_positive_and_monotonic():
    assert count_tokens("") == 0
    short = count_tokens("hello world")
    longer = count_tokens("hello world " * 50)
    assert 0 < short < longer


# ── Retrieval dedup ────────────────────────────────────────────────────────


def test_deduplicate_keeps_longest_and_preserves_order():
    base = "the quick brown fox jumps over the lazy dog and keeps running fast"
    chunks = [base, base + " with extra detail", "completely different content here"]
    kept, removed = deduplicate_chunks(chunks)
    assert removed == 1
    assert kept[0] == base + " with extra detail"
    assert kept[1] == "completely different content here"


# ── Blob handles ───────────────────────────────────────────────────────────


def test_blob_store_returns_short_bodies_unchanged():
    store = BlobStore(preview_chars=100)
    assert store.reference("short") == "short"


def test_blob_store_truncates_and_keeps_full_body_retrievable():
    store = BlobStore(preview_chars=50)
    body = "x" * 500
    reference = store.reference(body, "toolresult")
    assert "truncated" in reference
    assert len(reference) < len(body) + 200
    handle = reference.split()[0].lstrip("<")
    assert store.get(handle) == body


# ── Cache ──────────────────────────────────────────────────────────────────


def test_semantic_cache_hits_on_normalized_question():
    cache = SemanticCache()
    cache.put("What is the Status?", "answer")
    assert cache.get("what is the status") == "answer"
    assert cache.get("unrelated") is None


# ── History ledger ─────────────────────────────────────────────────────────


def test_ledger_checkpoint_replaces_old_turns_with_facts():
    ledger = ContextLedger(keep_recent_turns=2, maximum_tokens=10)
    for i in range(8):
        ledger.add("user", f"message number {i} with enough text to count tokens")

    ledger.checkpoint(lambda old: [f"summarized {len(list(old))} turns"])

    facts, turns = ledger.render()
    assert len(turns) == 2
    assert "summarized 6 turns" in facts


def test_ledger_does_not_checkpoint_under_budget():
    ledger = ContextLedger(keep_recent_turns=2, maximum_tokens=10_000)
    ledger.add("user", "small")
    ledger.checkpoint(lambda old: ["should not run"])
    facts, turns = ledger.render()
    assert facts == ""
    assert len(turns) == 1


# ── Budget guard ───────────────────────────────────────────────────────────


def test_budget_guard_blocks_expensive_request():
    guard = BudgetGuard(PRICES, maximum_request_cost=0.001)
    decision = guard.estimate(input_tokens=500_000)
    assert decision.allowed is False
    assert "exceeds" in decision.reason


def test_budget_guard_allows_cheap_request_and_caps_output():
    guard = BudgetGuard(PRICES, maximum_request_cost=1.0, default_output_limit=700)
    simple = guard.estimate(input_tokens=100, complexity=0.0)
    complex_task = guard.estimate(input_tokens=100, complexity=1.0)
    assert simple.allowed is True
    assert complex_task.output_limit > simple.output_limit


def test_estimate_complexity_scores_analysis_higher_than_greeting():
    assert estimate_complexity("привет", 0) == 0.0
    assert estimate_complexity("compare these two designs", 0) >= 0.3
    assert estimate_complexity("analyze", 5000, tool_count=9) == pytest.approx(0.75)


# ── Projection ─────────────────────────────────────────────────────────────


def test_project_fields_keeps_only_declared_fields():
    data = [{"id": 1, "huge": "x" * 10_000, "meta": {"keep": 1, "drop": 2}}]
    projected = project_fields(data, {"id": [], "meta": ["keep"]})
    assert projected == [{"id": 1, "meta": {"keep": 1}}]


def test_project_fields_passes_through_non_lists():
    assert project_fields({"a": 1}, {"a": []}) == {"a": 1}


# ── Integration ────────────────────────────────────────────────────────────


def test_prepare_request_builds_cacheable_prompt_and_reports_savings():
    request = prepare_request(
        question="Какие задачи заблокированы?",
        system_prompt="Ты помощник команды.",
        tools={"search_tasks": {"type": "function"}},
        records=_rows(30),
        documents=["d" * 2000],
        history=ContextLedger(),
        blobs=BlobStore(preview_chars=100),
    )

    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["metadata"]["records_mode"] == "structpack"
    assert request["metadata"]["records_tokens_after"] < (
        request["metadata"]["records_tokens_before"]
    )
    assert "truncated" in request["messages"][-1]["content"]


def test_prepare_request_beats_naive_prompt_on_tokens():
    records = _rows(60)
    documents = ["документ " * 400]
    naive = json.dumps(records, ensure_ascii=False) + "".join(documents)

    request = prepare_request(
        question="Какие задачи заблокированы?",
        system_prompt="Ты помощник команды.",
        tools={"search_tasks": {"type": "function"}},
        records=records,
        documents=documents,
        history=ContextLedger(),
        blobs=BlobStore(preview_chars=300),
    )
    optimized = request["messages"][-1]["content"]

    assert count_tokens(optimized) < count_tokens(naive)
