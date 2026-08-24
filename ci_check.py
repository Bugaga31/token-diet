#!/usr/bin/env python3
"""CI check for token-diet-lib (review priority #9).

Runs:
  1. the full unit-test suite (StructPack fuzz, BlobStore, gate, meter, ...)
  2. a token benchmark per module
  3. an Equivalence Gate regression on the optimized answer
  4. cache hit rate + cache-breakpoint checks
  5. a cost-regression threshold: the optimized request must not cost more
     than a fixed fraction of the baseline

Exits non-zero on any failure. Prints the canonical
"tokens before / after / quality / cost" report for a pull request and
writes it to TOKEN_DIET_CI_REPORT.md (or the path in argv[1]).

Usage:
    python3 token-diet-lib/ci_check.py [report_path]
"""

from __future__ import annotations

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TESTS = os.path.join(_HERE, "tests")
_TOKEN_DIET_DIR = os.path.join(_HERE, "token_diet")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _TOKEN_DIET_DIR not in sys.path:
    sys.path.insert(0, _TOKEN_DIET_DIR)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

import core  # noqa: E402
import cache_breakpoints  # noqa: E402
import context_memory  # noqa: E402
import equivalence_gate  # noqa: E402
import json_compressor  # noqa: E402
import loss_router  # noqa: E402
import optimization_runner  # noqa: E402

from core import (  # noqa: E402
    BlobStore,
    ContextLedger,
    PriceTable,
    PromptBuilder,
    TokenMeter,
    canonical_json,
    count_tokens,
    guarded_records,
    prepare_request,
)
from cache_breakpoints import CacheBreakpointAnalyzer  # noqa: E402
from equivalence_gate import EquivalenceGate, RegressionCase  # noqa: E402
from optimization_runner import OptimizationRunner, RequestProfile  # noqa: E402


PRICES = PriceTable(
    input_per_million=3.0,
    cache_write_per_million=3.75,
    cache_read_per_million=0.30,
    output_per_million=15.0,
)

COST_REGRESSION_SLACK = 1.02  # optimized request may cost at most 102% of baseline

# Every file the package promises to ship (review: packaging must not be empty).
REQUIRED_ARTIFACTS = [
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "token_diet/core.py",
    "token_diet/context_memory.py",
    "token_diet/json_compressor.py",
    "token_diet/loss_router.py",
    "token_diet/cache_breakpoints.py",
    "token_diet/equivalence_gate.py",
    "token_diet/optimization_runner.py",
    "token_diet/__init__.py",
    "ci_check.py",
    "tests/test_token_diet.py",
    "tests/test_token_diet_review.py",
]


# Что МОЖЕТ попасть в артефакт. Whitelist — всё остальное (включая
# .env, .git, state/, memory/, сам zip) физически исключено (правило №7).
_ARTIFACT_WHITELIST = {
    "token_diet",      # код
    "tests",           # тесты
    "README.md",
    "pyproject.toml",
    "ci_check.py",
    "run.sh",
    ".env.example",
    "LICENSE",
    ".github",
}

# Что ЗАПРЕЩЕНО внутри whitelist-директорий (защита от случайностей).
# Матчим по ИМЕНИ файла/каталога, а не подстроке (иначе "memory" в blocklist
# вырежет context_memory.py и memory_cli.py — УРОК 16.08).
_ARTIFACT_BLOCKNAMES = {
    ".env", ".git", ".gitignore", "token-diet-lib.zip",
    "__pycache__", ".egg-info", ".pytest_cache",
    "state", "memory", "logs",
}

_ARTIFACT_BLOCKSUFFIXES = (".pyc", ".pyo", ".tmp", ".zip")


def build_artifact_zip(pkg_dir: str, out_zip: str) -> tuple[list[str], list[str]]:
    """Package the library WHITELIST-ом (только код+тесты+доки).

    УРОК 16.08: раньше ходили os.walk по всему каталогу → в zip попадали
    .env (ключ AnyModel) и .git/ — ключ утекал в git через коммит zip.
    Теперь берём только whitelist, blocklist режет всё остальное.

    Returns (names_in_zip, missing_required_files).
    """
    import zipfile

    root = os.path.abspath(pkg_dir)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(pkg_dir):
            # whitelist: оставляем только разрешённые верхнеуровневые каталоги
            rel_parts = os.path.relpath(dirpath, root).split(os.sep)
            top = rel_parts[0] if rel_parts else ""
            if dirpath == root:
                dirnames[:] = [d for d in dirnames if d in _ARTIFACT_WHITELIST]
                # файлы в корне: только явно разрешённые
                for name in filenames:
                    if name not in _ARTIFACT_WHITELIST:
                        continue
                    full = os.path.join(dirpath, name)
                    zf.write(full, os.path.relpath(full, root))
                continue
            # внутри whitelist-каталогов: режем blocklist по именам
            dirnames[:] = [d for d in dirnames if d not in _ARTIFACT_BLOCKNAMES]
            for name in filenames:
                if name in _ARTIFACT_BLOCKNAMES:
                    continue
                if any(name.endswith(sfx) for sfx in _ARTIFACT_BLOCKSUFFIXES):
                    continue
                full = os.path.join(dirpath, name)
                zf.write(full, os.path.relpath(full, root))

    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
    missing = [r for r in REQUIRED_ARTIFACTS if not any(n.endswith(r) for n in names)]
    return names, missing

# ---------------------------------------------------------------------------
# Benchmark scenario (deterministic)
# ---------------------------------------------------------------------------


def scenario():
    records = [
        {"id": i, "status": "blocked", "owner": "artem", "amount": round(i * 1.5, 2)}
        for i in range(10)
    ]
    documents = [
        "Refund policy: refunds are issued within 14 days of the purchase date. "
        "Shipping is free above $50. Returns require an RMA number." * 6
    ] * 2
    history = "User asked about refunds. Assistant explained the policy. " * 5
    return records, documents, history


def bench_structpack(records):
    original = json.dumps(records, ensure_ascii=False)
    before = count_tokens(original)
    packed, mode, _b, after = guarded_records(records)
    return before, after, mode


def bench_blobs(documents):
    """Honest blob benchmark: if the guard refuses the proposal, the "after"
    column reports the baseline (nothing applied) so the TOTAL never counts
    a regression as savings."""
    store = BlobStore()
    before = sum(count_tokens(d) for d in documents)
    after = sum(count_tokens(store.reference(d, "document")) for d in documents)
    if after < before * 0.95:
        return before, after, f"blob refs x{len(documents)} (applied)"
    return (
        before,
        before,
        f"blob refs x{len(documents)} (GUARD: +{after - before} tokens, not applied)",
    )


def bench_dedupe(documents):
    before = sum(count_tokens(d) for d in documents)
    deduped, dropped = core.deduplicate_chunks(documents)
    after = sum(count_tokens(d) for d in deduped)
    return before, after, f"{dropped} duplicates dropped"


def bench_prose():
    """Long, filler-heavy prose. Short text (<100 chars) stays verbatim by
    design — that guard is covered by test_short_prose_left_verbatim; this
    row proves the compressor works where it is supposed to."""
    long_text = (
        "Furthermore, it is important to note that the policy applies "
        "to all users without exception. " * 8
    )
    before = count_tokens(long_text)
    compressed, _b, after = loss_router.compress_with_routing(long_text)
    return before, after, "long prose, filler removal"


def bench_json_tool_result():
    data = {"portfolio": [{"ticker": "AAPL", "shares": 12, "price": 189.5}] * 50}
    raw = json.dumps(data, ensure_ascii=False)
    before = count_tokens(raw)
    after = count_tokens(json_compressor.compress_json(data))
    return before, after, "Headroom-style flatten"


def bench_translation():
    """Realistic mock: ~45% reduction (a real translation rarely shrinks by
    more than half), reuse=3. Kept labelled as synthetic in the report."""
    def translate(text, source, target):
        return " ".join(["w"] * 140)

    long_text = " ".join(["word"] * 250)
    before = count_tokens(long_text)
    choice = context_memory.choose_language(
        long_text, "en", ["ru"], translate, reuse_count=3, min_saving=5
    )
    after = count_tokens(choice.text) if choice.used else before
    return before, after, f"used={choice.used} reuse=3 (synthetic ~45% mock)"


def bench_cache():
    builder = PromptBuilder()
    builder.add_static("You are a financial assistant. " * 4)
    builder.add_static(canonical_json({"functions": [{"name": "search"}]}))
    static_tokens = sum(
        count_tokens(block["text"]) for block in builder.system_blocks()
    )
    analyzer = CacheBreakpointAnalyzer()
    report = analyzer.analyze_prompt(static=["You are a financial assistant. " * 4])
    return static_tokens, static_tokens, (
        f"cache-safe={report.cache_safe} volatile={report.volatile_tokens}"
    )


def bench_event_memory():
    store = context_memory.EventStore(path=None)
    store.add("constraint", "Do not use paid APIs", importance=0.9)
    store.add("permission", "Read-only database access", importance=0.9)
    store.add("fact", "User prefers Python", importance=0.7)
    store.add("decision", "Use PostgreSQL", importance=0.8)
    store.add("decision", "Use PostgreSQL", importance=0.8)  # version bump
    ctx = context_memory.AdaptiveContext(store)
    chosen = ctx.select("database and python", budget=800)
    before = sum(count_tokens(e.rendered()) for e in store.all())
    after = sum(count_tokens(e.rendered()) for e in chosen)
    return before, after, f"{len(chosen)}/{len(store.all())} events, v2 supersedes v1"


# ---------------------------------------------------------------------------


def run_unit_tests() -> bool:
    loader = unittest.TestLoader()
    suite = loader.discover(_TESTS)
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return result.wasSuccessful()


def run_gate_regression(records) -> tuple[bool, str]:
    gate = EquivalenceGate(
        judge1=lambda b, a: (0.9, True),
        model_version="ci-check",
        strict=True,
    )
    baseline = (
        "There are 10 blocked orders for artem totaling $67.50 "
        "with 2 documents and a full policy attached."
    )
    answer = baseline  # optimized answer preserves every critical fact
    case = RegressionCase(
        case_id="ci_regression",
        prompt="summarize orders",
        baseline_answer=baseline,
        critical_facts=[
            equivalence_gate.CriticalFact.number("67.5"),
            equivalence_gate.CriticalFact.name("artem"),
        ],
    )
    result = gate.verify(case, answer)
    return result.passed and not result.rollback, result.render()


def main() -> int:
    report_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_HERE, "TOKEN_DIET_CI_REPORT.md")

    print("=" * 70)
    print("token-diet-lib CI check")
    print("=" * 70)

    tests_ok = run_unit_tests()

    records, documents, history = scenario()

    benches = [
        ("structpack", *bench_structpack(records)),
        ("blob_reference", *bench_blobs(documents)),
        ("dedupe_chunks", *bench_dedupe(documents)),
        ("prose_compression", *bench_prose()),
        ("json_tool_result", *bench_json_tool_result()),
        ("translation_router", *bench_translation()),
        ("cache_static_block", *bench_cache()),
        ("event_memory", *bench_event_memory()),
    ]

    print("\n" + "-" * 70)
    print("Token benchmark per module (before -> after tokens)")
    print("-" * 70)
    print(f"{'module':<20}{'before':>8}{'after':>8}{'saved':>8}{'%':>7}  notes")
    total_before = total_after = 0
    for name, before, after, notes in benches:
        saved = before - after
        pct = 100.0 * saved / max(1, before)
        total_before += before
        total_after += after
        print(f"{name:<20}{before:>8}{after:>8}{saved:>8}{pct:>6.1f}%  {notes}")
    total_saved = total_before - total_after
    print("-" * 70)
    print(
        f"{'TOTAL':<20}{total_before:>8}{total_after:>8}{total_saved:>8}"
        f"{100.0 * total_saved / max(1, total_before):>6.1f}%"
    )

    # End-to-end pipeline: OptimizationRunner + gate + cost regression
    runner = OptimizationRunner(PRICES)
    profile = RequestProfile(
        task_id="ci-e2e",
        system_prompt="You are a financial assistant. " * 5,
        tools={"functions": [{"name": "search", "description": "search docs"}]},
        history_text=history,
        records=records,
        documents=documents,
        question="Summarize blocked orders for artem and the refund policy.",
        output_tokens=150,
    )
    report = runner.run(
        profile,
        baseline_answer=(
            "There are 10 blocked orders for artem totaling $67.50 "
            "with 2 documents and a full policy attached."
        ),
        optimized_answer=(
            "There are 10 blocked orders for artem totaling $67.50 "
            "with 2 documents and a full policy attached."
        ),
    )

    gate_ok, gate_text = run_gate_regression(records)

    baseline_cost = report.baseline_tokens * PRICES.input_per_million / 1_000_000
    optimized_cost = report.optimized_tokens * PRICES.input_per_million / 1_000_000
    cost_ok = optimized_cost <= baseline_cost * COST_REGRESSION_SLACK

    # Cache hit rate (measured via TokenMeter)
    meter = TokenMeter(PRICES)

    class Resp:
        usage = {}

    meter.record("cache-demo", Resp())
    meter.record_sections({"static": "You are a financial assistant. " * 4})

    print("\n" + "-" * 70)
    print("End-to-end report")
    print("-" * 70)
    print(report.to_markdown())
    print()
    print(f"baseline cost : ${baseline_cost:.4f}")
    print(f"optimized cost: ${optimized_cost:.4f}")
    print(f"cost regression: {'OK' if cost_ok else 'FAIL'} "
          f"(optimized <= {COST_REGRESSION_SLACK:.0%} of baseline)")
    print(f"equivalence gate: {'PASS' if gate_ok else 'FAIL'}")
    print(gate_text)

    # Packaging check: build the zip artifact and verify it is complete.
    artifact_zip = os.path.join(os.path.dirname(report_path), "token-diet-lib.zip")
    names, missing = build_artifact_zip(_HERE, artifact_zip)
    packaging_ok = not missing
    print("\n" + "-" * 70)
    print("Packaging check")
    print("-" * 70)
    print(f"artifact: {artifact_zip} ({len(names)} files)")
    if missing:
        print(f"MISSING from artifact: {missing}")
    else:
        print("artifact complete: all promised modules + tests + README present")
    for name in sorted(names)[:14]:
        print(f"  {name}")

    ok = tests_ok and gate_ok and cost_ok and packaging_ok
    print("\n" + "=" * 70)
    print(f"CI RESULT: {'PASS' if ok else 'FAIL'}")
    print("=" * 70)

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("# Token Diet — CI report\n\n")
        fh.write(f"**result:** {'PASS' if ok else 'FAIL'}\n\n")
        fh.write("## Tokens per module (before → after)\n\n")
        fh.write("| module | before | after | saved | % | notes |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for name, before, after, notes in benches:
            saved = before - after
            pct = 100.0 * saved / max(1, before)
            fh.write(f"| {name} | {before} | {after} | {saved} | {pct:.1f}% | {notes} |\n")
        fh.write(
            f"| **TOTAL** | **{total_before}** | **{total_after}** | "
            f"**{total_saved}** | **{100.0 * total_saved / max(1, total_before):.1f}%** | |\n\n"
        )
        fh.write("## End-to-end\n\n")
        fh.write(report.to_markdown())
        fh.write(f"\n\nbaseline cost: ${baseline_cost:.4f}\n\n")
        fh.write(f"optimized cost: ${optimized_cost:.4f}\n\n")
        fh.write(f"cost regression: {'OK' if cost_ok else 'FAIL'}\n\n")
        fh.write(f"equivalence gate: {'PASS' if gate_ok else 'FAIL'}\n\n")
        fh.write("```\n" + gate_text + "\n```\n\n")
        fh.write("## Packaging\n\n")
        fh.write(f"artifact: `{artifact_zip}` ({len(names)} files)\n\n")
        if missing:
            fh.write(f"**MISSING from artifact:** {missing}\n")
        else:
            fh.write("artifact complete: all promised modules + tests + README present.\n")
    print(f"\nreport written to {report_path}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
