"""Unified OptimizationRunner (review priority #1).

Single pipeline:

    collect metrics -> find most expensive section -> propose optimization
    -> estimate savings -> run Equivalence Gate -> report

The runner never proposes every optimization at once: it measures the real
prompt, picks the most expensive sections by actual token data, and only
then suggests the cheapest-risk change. Every proposal carries an estimated
saving in tokens and dollars, a risk level, and a reference to the section
it targets, so the caller (or an agent) can decide what to apply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

try:from .core import (
    BlobStore,
    PriceTable,
    SemanticCache,
    Usage,
    canonical_json,
    count_tokens,
    deduplicate_chunks,
    guarded_records,
    structpack_roundtrip_safe,
)
except ImportError:  # standalone use
    from core import (  # type: ignore[no-redef]
        BlobStore,
        PriceTable,
        SemanticCache,
        Usage,
        canonical_json,
        count_tokens,
        deduplicate_chunks,
        guarded_records,
        structpack_roundtrip_safe,
    )

try:
    from .equivalence_gate import EquivalenceGate, GateResult, RegressionCase
    from .tool_schema_compressor import guarded_tool_schemas
    from .question_normalizer import normalize_question
except ImportError:
    from equivalence_gate import EquivalenceGate, GateResult, RegressionCase  # type: ignore[no-redef]
    from tool_schema_compressor import guarded_tool_schemas  # type: ignore[no-redef]
    from question_normalizer import normalize_question  # type: ignore[no-redef]

try:
    from .cache_breakpoints import CacheBreakpointAnalyzer
except ImportError:
    from cache_breakpoints import CacheBreakpointAnalyzer  # type: ignore[no-redef]

try:
    from .loss_router import compress_prose_aggressive, compress_with_routing
except ImportError:
    from loss_router import compress_prose_aggressive, compress_with_routing  # type: ignore[no-redef]

Counter = Callable[[str], int]


# ---------------------------------------------------------------------------
# Unified metric format
# ---------------------------------------------------------------------------


@dataclass
class SectionMetrics:
    name: str
    tokens: int = 0
    share: float = 0.0  # 0..1 of total input

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tokens": self.tokens, "share": round(self.share, 4)}


@dataclass
class TaskMetrics:
    task_id: str
    sections: list[SectionMetrics] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost: float = 0.0
    cache_hit_rate: float = 0.0
    call_types: dict[str, int] = field(default_factory=dict)

    def most_expensive(self) -> SectionMetrics | None:
        if not self.sections:
            return None
        return max(self.sections, key=lambda s: s.tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sections": [s.to_dict() for s in self.sections],
            "usage": {
                "input": self.usage.input_tokens,
                "cache_write": self.usage.cache_write_tokens,
                "cache_read": self.usage.cache_read_tokens,
                "output": self.usage.output_tokens,
            },
            "cost": round(self.cost, 6),
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "call_types": self.call_types,
        }


@dataclass
class OptimizationProposal:
    name: str
    target_section: str
    tokens_before: int = 0
    tokens_after: int = 0
    risk: str = "none"  # none | low | medium | high
    applicable: bool = True
    details: str = ""

    @property
    def savings_tokens(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def savings_pct(self) -> float:
        if not self.tokens_before:
            return 0.0
        return 100.0 * self.savings_tokens / self.tokens_before

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "target_section": self.target_section,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "savings_tokens": self.savings_tokens,
            "savings_pct": round(self.savings_pct, 2),
            "risk": self.risk,
            "details": self.details,
        }


@dataclass
class OptimizationReport:
    task_id: str = ""
    baseline_tokens: int = 0
    optimized_tokens: int = 0
    proposals: list[OptimizationProposal] = field(default_factory=list)
    rejected: list[OptimizationProposal] = field(default_factory=list)
    gate: GateResult | None = None
    breakpoints: list[Any] = field(default_factory=list)

    @property
    def savings_tokens(self) -> int:
        return max(0, self.baseline_tokens - self.optimized_tokens)

    def to_markdown(self) -> str:
        lines = [
            f"# Optimization report — {self.task_id or 'request'}",
            "",
            f"- **tokens before**: {self.baseline_tokens}",
            f"- **tokens after**: {self.optimized_tokens}",
            f"- **saved**: {self.savings_tokens} "
            f"({100 * self.savings_tokens / max(1, self.baseline_tokens):.1f}%)",
            "",
            "## Proposals",
        ]
        if not self.proposals:
            lines.append("(none applicable)")
        for p in sorted(self.proposals, key=lambda p: -p.savings_tokens):
            lines.append(
                f"- **{p.name}** → `{p.target_section}`: "
                f"{p.tokens_before} → {p.tokens_after} tokens "
                f"(risk {p.risk}): {p.details}"
            )
        if self.rejected:
            lines.append("")
            lines.append("## Rejected (guard reasons)")
            for p in self.rejected:
                lines.append(f"- **{p.name}** → `{p.target_section}`: {p.details}")
        if self.breakpoints:
            lines.append("")
            lines.append("## Cache breakpoints")
            for b in self.breakpoints:
                lines.append(f"- [{b.severity}] {b.kind}: {b.snippet[:60]!r}")
        if self.gate is not None:
            lines.append("")
            lines.append("## Equivalence Gate")
            lines.append("```")
            lines.append(self.gate.render())
            lines.append("```")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Request profile: what a single user result consumes
# ---------------------------------------------------------------------------


@dataclass
class RequestProfile:
    task_id: str
    system_prompt: str = ""
    tools: dict[str, Any] | None = None
    history_text: str = ""
    records: list[dict[str, Any]] | None = None
    documents: list[str] = field(default_factory=list)
    question: str = ""
    output_tokens: int = 0
    counter: Counter = count_tokens

    def section_texts(self) -> dict[str, str]:
        texts: dict[str, str] = {}
        if self.system_prompt:
            texts["system_prompt"] = self.system_prompt
        if self.tools:
            texts["tools"] = canonical_json(self.tools)
        if self.history_text:
            texts["history"] = self.history_text
        if self.records is not None:
            texts["retrieved_context"] = json.dumps(self.records, ensure_ascii=False)
        if self.documents:
            texts["retrieved_context_docs"] = "\n\n".join(self.documents)
        if self.question:
            texts["question"] = self.question
        return texts

    def measure(self) -> TaskMetrics:
        texts = self.section_texts()
        total_input = sum(self.counter(text) for text in texts.values()) + self.output_tokens
        sections = [
            SectionMetrics(
                name=name,
                tokens=self.counter(text),
                share=self.counter(text) / max(1, total_input),
            )
            for name, text in texts.items()
        ]
        usage = Usage(
            input_tokens=sum(s.tokens for s in sections),
            output_tokens=self.output_tokens,
        )
        return TaskMetrics(task_id=self.task_id, sections=sections, usage=usage)


# ---------------------------------------------------------------------------
# Built-in optimizers. Each is a pure function: profile -> proposal (or None).
# ---------------------------------------------------------------------------


def _structpack_proposal(profile: RequestProfile) -> OptimizationProposal:
    section = "retrieved_context"
    if not profile.records:
        return OptimizationProposal(name="structpack", target_section=section, applicable=False)
    original = json.dumps(profile.records, ensure_ascii=False)
    before = profile.counter(original)
    packed, mode, _before, after = guarded_records(profile.records)
    return OptimizationProposal(
        name="structpack",
        target_section=section,
        tokens_before=before,
        tokens_after=after,
        risk="none",
        applicable=mode == "structpack",
        details=(
            f"lossless column packing, mode={mode}; "
            f"round-trip safe={structpack_roundtrip_safe(profile.records)}"
        ),
    )


def _blob_proposal(profile: RequestProfile, blobs: BlobStore | None = None) -> OptimizationProposal:
    """Measure blob-reference savings on a THROWAWAY store.

    Putting blobs into the runner's real store during propose() would be a
    side effect (and duplicate entries on repeated propose calls); the real
    store is populated only when the caller actually applies the proposal.
    """
    section = "retrieved_context_docs"
    if not profile.documents:
        return OptimizationProposal(name="blob_reference", target_section=section, applicable=False)
    measure = blobs or BlobStore()
    before = sum(profile.counter(doc) for doc in profile.documents)
    after = sum(profile.counter(measure.reference(doc, "document")) for doc in profile.documents)
    # Guard: a blob reference only helps when the doc is large enough for the
    # handle overhead to pay off. Require at least 5% real savings, otherwise
    # the proposal is refused (a short doc wrapped in a handle costs MORE).
    applicable = after < before * 0.95
    return OptimizationProposal(
        name="blob_reference",
        target_section=section,
        tokens_before=before,
        tokens_after=after,
        risk="low",
        applicable=applicable,
        details=(
            "large docs replaced by compact blob handles; body fetched on demand"
            if applicable
            else f"GUARD: blob ref would cost +{after - before} tokens (need <5% savings)"
        ),
    )


def _dedupe_proposal(profile: RequestProfile) -> OptimizationProposal:
    section = "retrieved_context_docs"
    if len(profile.documents) < 2:
        return OptimizationProposal(name="dedupe_chunks", target_section=section, applicable=False)
    before = sum(profile.counter(doc) for doc in profile.documents)
    deduped, dropped = deduplicate_chunks(profile.documents)
    after = sum(profile.counter(doc) for doc in deduped)
    return OptimizationProposal(
        name="dedupe_chunks",
        target_section=section,
        tokens_before=before,
        tokens_after=after,
        risk="none",
        applicable=dropped > 0 and after < before,
        details=f"{dropped} near-duplicate chunk(s) removed",
    )


def _aggressive_prose_proposal(profile: RequestProfile) -> OptimizationProposal:
    section = "history"
    if not profile.history_text:
        return OptimizationProposal(
            name="compress_prose_aggressive", target_section=section, applicable=False
        )
    before = profile.counter(profile.history_text)
    after = profile.counter(compress_prose_aggressive(profile.history_text))
    return OptimizationProposal(
        name="compress_prose_aggressive",
        target_section=section,
        tokens_before=before,
        tokens_after=after,
        risk="medium",
        applicable=after < before,
        details=(
            f"sentence-level filtering: dropped {before - after} tokens; "
            f"numbers/dates/entities/negations preserved"
        ),
    )


def _tool_schema_proposal(profile: RequestProfile) -> OptimizationProposal:
    section = "tools"
    if not profile.tools:
        return OptimizationProposal(name="strip_tool_descriptions", target_section=section, applicable=False)
    _compressed, before, after, applied = guarded_tool_schemas(profile.tools)
    return OptimizationProposal(
        name="strip_tool_descriptions",
        target_section=section,
        tokens_before=before,
        tokens_after=after,
        risk="none",
        applicable=applied,
        details=f"tool descriptions stripped; {before} → {after} tokens" if applied else "no descriptions to strip",
    )


def _question_normalizer_proposal(profile: RequestProfile) -> OptimizationProposal:
    section = "question"
    if not profile.question:
        return OptimizationProposal(name="normalize_question", target_section=section, applicable=False)
    normalized = normalize_question(profile.question)
    if normalized == profile.question:
        return OptimizationProposal(name="normalize_question", target_section=section, applicable=False, details="no filler detected")
    before = profile.counter(profile.question)
    after = profile.counter(normalized)
    return OptimizationProposal(
        name="normalize_question",
        target_section=section,
        tokens_before=before,
        tokens_after=after,
        risk="low",
        applicable=after < before,
        details=f"politeness filler stripped: {profile.question[:30]!r} → {normalized[:30]!r}",
    )


def _cache_hit_proposal(profile: RequestProfile, cache: SemanticCache | None) -> OptimizationProposal:
    section = "output"
    if cache is None:
        return OptimizationProposal(name="semantic_cache", target_section=section, applicable=False, details="no cache configured")
    if cache.get(profile.question):
        return OptimizationProposal(
            name="semantic_cache",
            target_section=section,
            tokens_before=profile.output_tokens,
            tokens_after=0,
            risk="none",
            applicable=True,
            details="cache HIT — answer served for 0 tokens",
        )
    return OptimizationProposal(name="semantic_cache", target_section=section, applicable=False, details="cache MISS")


def _prose_proposal(profile: RequestProfile) -> OptimizationProposal:
    section = "history"
    if not profile.history_text:
        return OptimizationProposal(name="compress_prose", target_section=section, applicable=False)
    before = profile.counter(profile.history_text)
    compressed, before_t, after_t = compress_with_routing(profile.history_text)
    return OptimizationProposal(
        name="compress_prose",
        target_section=section,
        tokens_before=before_t,
        tokens_after=after_t,
        risk="medium",
        applicable=after_t < before_t,
        details="ceremony/filler phrases removed from history prose",
    )
    section = "history"
    if not profile.history_text:
        return OptimizationProposal(name="compress_prose", target_section=section, applicable=False)
    before = profile.counter(profile.history_text)
    compressed, before_t, after_t = compress_with_routing(profile.history_text)
    return OptimizationProposal(
        name="compress_prose",
        target_section=section,
        tokens_before=before_t,
        tokens_after=after_t,
        risk="medium",
        applicable=after_t < before_t,
        details="ceremony/filler phrases removed from history prose",
    )


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


class OptimizationRunner:
    """Measure, pick the most expensive section, propose, verify, report."""

    def __init__(
        self,
        prices: PriceTable,
        blobs: BlobStore | None = None,
        gate: EquivalenceGate | None = None,
        cache: SemanticCache | None = None,
        max_proposals: int = 3,
        max_sections_considered: int = 4,
        counter: Counter = count_tokens,
    ):
        self.prices = prices
        self.blobs = blobs or BlobStore()
        self.gate = gate
        self.cache = cache
        self.max_proposals = max_proposals
        self.max_sections_considered = max_sections_considered
        self.counter = counter
        self.cache_analyzer = CacheBreakpointAnalyzer()

    # -- step 1: metrics ------------------------------------------------------

    def collect(self, profile: RequestProfile) -> TaskMetrics:
        return profile.measure()

    def find_most_expensive(self, metrics: TaskMetrics) -> SectionMetrics | None:
        return metrics.most_expensive()

    # -- step 2: propose ------------------------------------------------------

    def propose(self, profile: RequestProfile) -> list[OptimizationProposal]:
        """Only the most expensive sections get proposals, cheapest risk first."""
        metrics = profile.measure()
        ranked = sorted(metrics.sections, key=lambda s: -s.tokens)
        considered = ranked[: self.max_sections_considered]

        candidates: list[OptimizationProposal] = []
        targets = {s.name for s in considered}

        if "retrieved_context" in targets:
            candidates.append(_structpack_proposal(profile))
        if "retrieved_context_docs" in targets or profile.documents:
            candidates.append(_blob_proposal(profile))
            candidates.append(_dedupe_proposal(profile))
        if "history" in targets:
            candidates.append(_prose_proposal(profile))
            candidates.append(_aggressive_prose_proposal(profile))
        if "tools" in targets:
            candidates.append(_tool_schema_proposal(profile))
        if "question" in targets:
            candidates.append(_question_normalizer_proposal(profile))
            candidates.append(_cache_hit_proposal(profile, self.cache))

        applicable = [p for p in candidates if p.applicable and p.savings_tokens > 0]
        rejected = [p for p in candidates if p not in applicable]
        risk_order = {"none": 0, "low": 1, "medium": 2, "high": 3}
        applicable.sort(key=lambda p: (-p.savings_tokens, risk_order[p.risk]))
        rejected.sort(key=lambda p: (-p.savings_tokens, risk_order[p.risk]))
        # Applicable first (capped), then rejected ones with their guard
        # reason so the report can show WHY an optimization was refused.
        return applicable[: self.max_proposals] + rejected[: self.max_proposals]

    # -- step 3: estimate -----------------------------------------------------

    def estimate_savings(
        self, proposals: list[OptimizationProposal], prices: PriceTable | None = None
    ) -> tuple[int, float]:
        prices = prices or self.prices
        total_tokens = sum(p.savings_tokens for p in proposals)
        cost = total_tokens * prices.input_per_million / 1_000_000
        return total_tokens, cost

    # -- step 4: gate ---------------------------------------------------------

    def verify_equivalence(
        self,
        case: RegressionCase,
        new_answer: str,
        similarity: float | None = None,
    ) -> GateResult | None:
        if self.gate is None:
            return None
        return self.gate.verify(case, new_answer, similarity=similarity)

    # -- full pipeline --------------------------------------------------------

    def run(
        self,
        profile: RequestProfile,
        baseline_answer: str = "",
        optimized_answer: str = "",
        baseline_similarity: float | None = None,
    ) -> OptimizationReport:
        metrics = profile.measure()
        proposals = self.propose(profile)
        baseline_tokens = sum(s.tokens for s in metrics.sections)

        # Apply at most one proposal per section (the best one): proposals that
        # target the same section (e.g. dedupe_chunks + blob_reference on
        # documents) were each estimated against the ORIGINAL profile, so
        # summing them would double-count the savings.
        best_per_section: dict[str, OptimizationProposal] = {}
        for proposal in proposals:
            if not proposal.applicable or proposal.risk not in {"none", "low"}:
                continue
            previous = best_per_section.get(proposal.target_section)
            if previous is None or proposal.savings_tokens > previous.savings_tokens:
                best_per_section[proposal.target_section] = proposal
        applied = list(best_per_section.values())
        optimized_tokens = baseline_tokens - sum(p.savings_tokens for p in applied)
        # Presentation: a proposal is either applicable (listed in "Proposals")
        # or refused by a guard (listed only in "Rejected") — never both.
        report_proposals = [p for p in proposals if p.applicable]
        rejected = [p for p in proposals if not p.applicable]

        gate_result = None
        if baseline_answer and optimized_answer and self.gate is not None:
            case = RegressionCase(
                case_id=profile.task_id,
                prompt=profile.question,
                baseline_answer=baseline_answer,
            )
            gate_result = self.gate.verify(case, optimized_answer, similarity=baseline_similarity)

        breakpoints = self.cache_analyzer.analyze(profile.system_prompt + canonical_json(profile.tools or {}))

        return OptimizationReport(
            task_id=profile.task_id,
            baseline_tokens=baseline_tokens,
            optimized_tokens=optimized_tokens,
            proposals=report_proposals,
            rejected=rejected,
            gate=gate_result,
            breakpoints=breakpoints,
        )
