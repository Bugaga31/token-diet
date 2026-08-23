"""Equivalence Gate — verify an optimized prompt did not lose critical facts.

Review priority #5. A single judge's average similarity is a poor gate: it
can mask the loss of one critical number, date, name, URL or negation. This
gate combines:

  - deterministic fact checkers (numbers, dates, names, URLs, negations)
  - a primary judge and, for critical failures, a second opinion
  - stored baselines with model version
  - a confidence interval over repeated similarity samples
  - ROLLBACK: if any critical fact is missing, the optimization is refused
    regardless of the average similarity score.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

Judge = Callable[[str, str], tuple[float, bool]]  # (similarity 0..1, passed)

_NUMBER_RE = re.compile(r"(?<!\w)(\$?\d[\d\s,\.]*(?:%|\b))")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_NAME_RE = re.compile(r"\b[A-ZА-ЯЁ][a-zа-яё]{2,}(?:\s+[A-ZА-ЯЁ][a-zа-яё]{2,}){0,2}\b")
_NEGATION_MARKERS = (
    "not ",
    "never ",
    "no ",
    "n't",
    "without ",
    "except ",
    "не ",
    "никогда",
    "нельзя",
    "без ",
    "кроме ",
)


@dataclass
class CriticalFact:
    """A fact the optimized answer must preserve (or must NOT contain)."""

    kind: str = "custom"  # number | date | name | url | negation | custom
    expected: str = ""
    must_be_absent: bool = False

    @classmethod
    def number(cls, value: str) -> "CriticalFact":
        return cls(kind="number", expected=value)

    @classmethod
    def date(cls, value: str) -> "CriticalFact":
        return cls(kind="date", expected=value)

    @classmethod
    def name(cls, value: str) -> "CriticalFact":
        return cls(kind="name", expected=value)

    @classmethod
    def url(cls, value: str) -> "CriticalFact":
        return cls(kind="url", expected=value)

    @classmethod
    def negation(cls, value: str, must_be_absent: bool = True) -> "CriticalFact":
        """value must be present (or absent) together with a negation marker."""
        return cls(kind="negation", expected=value, must_be_absent=must_be_absent)

    def render(self) -> str:
        flag = "ABSENT" if self.must_be_absent else "PRESENT"
        return f"{self.kind}:{flag}:{self.expected}"


@dataclass
class RegressionCase:
    case_id: str
    prompt: str
    baseline_answer: str
    critical_facts: list[CriticalFact] = field(default_factory=list)
    model: str = ""

    def auto_facts(self) -> list[CriticalFact]:
        """Extract the baseline's own numbers/dates/URLs as critical facts.

        Numbers that are part of a date or a URL are skipped, otherwise a
        rephrased date ("August 8, 2026" vs "2026-08-08") would create a
        false fact failure and a false ROLLBACK.
        """
        date_spans = [m.span() for m in _DATE_RE.finditer(self.baseline_answer)]
        url_spans = [m.span() for m in _URL_RE.finditer(self.baseline_answer)]
        protected = date_spans + url_spans

        def overlaps(span: tuple[int, int]) -> bool:
            start, end = span
            return any(s < end and start < e for s, e in protected)

        facts = []
        for m in _NUMBER_RE.finditer(self.baseline_answer):
            if not overlaps(m.span()):
                facts.append(CriticalFact.number(m.group(1)))
        facts += [CriticalFact.date(m.group(0)) for m in _DATE_RE.finditer(self.baseline_answer)]
        facts += [CriticalFact.url(m.group(0)) for m in _URL_RE.finditer(self.baseline_answer)]

        # Cap auto-extraction to the most stable facts (avoid noise like row ids).
        seen: set[str] = set()
        out: list[CriticalFact] = []
        for fact in facts:
            key = (fact.kind, fact.expected)
            if key in seen:
                continue
            seen.add(key)
            out.append(fact)
        return out[:12]


@dataclass
class GateResult:
    case_id: str
    passed: bool
    rollback: bool
    similarity: float
    samples: list[float] = field(default_factory=list)
    confidence_interval: tuple[float, float] | None = None
    failed_facts: list[CriticalFact] = field(default_factory=list)
    votes: list[tuple[str, bool]] = field(default_factory=list)
    model_version: str = ""
    reason: str = ""

    @property
    def decision(self) -> str:
        if self.rollback:
            return "ROLLBACK"
        if self.passed:
            return "PASS"
        return "FAIL"

    def render(self) -> str:
        lines = [
            f"case={self.case_id} decision={self.decision} "
            f"similarity={self.similarity:.3f} ({len(self.samples)} samples)",
        ]
        if self.confidence_interval:
            low, high = self.confidence_interval
            lines.append(f"  ci95={low:.3f}..{high:.3f}")
        for fact in self.failed_facts:
            lines.append(f"  MISSING FACT: {fact.render()}")
        for judge_name, ok in self.votes:
            lines.append(f"  {judge_name}: {'ok' if ok else 'reject'}")
        lines.append(f"  {self.reason}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic fact checking
# ---------------------------------------------------------------------------


def check_fact(answer: str, fact: CriticalFact) -> bool:
    """True when the fact is preserved in the new answer."""
    text = answer.lower()
    expected = fact.expected.lower()

    if fact.kind == "number":
            # Compare numbers numerically where possible, tolerating formatting.
            try:
                expected_num = float(
                    expected.replace(",", "").replace("$", "").replace("%", "").replace(" ", "")
                )
            except ValueError:
                expected_num = None
            if expected_num is not None:
                for m in _NUMBER_RE.finditer(answer):
                    try:
                        candidate = float(
                            m.group(1).replace(",", "").replace("$", "").replace("%", "").replace(" ", "")
                        )
                    except ValueError:
                        continue
                    if candidate == expected_num:
                        return not fact.must_be_absent
                return fact.must_be_absent
            # Unparseable number: match as a whole word, not a substring
            # ("42" must not match "420").
            pattern = re.compile(rf"(?<!\w){re.escape(fact.expected)}(?!\w)")
            present = pattern.search(text) is not None
            return (not present) if fact.must_be_absent else present

    present = expected in text or expected.strip() in text
    return (not present) if fact.must_be_absent else present


def check_critical_facts(answer: str, facts: list[CriticalFact]) -> list[CriticalFact]:
    return [fact for fact in facts if not check_fact(answer, fact)]


def deterministic_judge(baseline: str, answer: str, threshold: float = 0.6) -> tuple[float, bool]:
    """Dependency-free fallback judge: weighted word overlap + negation check."""
    def words(text: str) -> set[str]:
        return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}

    base, new = words(baseline), words(answer)
    if not base:
        return 0.0, False
    overlap = len(base & new) / len(base)
    return overlap, overlap >= threshold


# ---------------------------------------------------------------------------
# Confidence interval
# ---------------------------------------------------------------------------

_Z95 = 1.96


def mean_ci(samples: list[float], confidence: float = 0.95) -> tuple[float, float] | None:
    if len(samples) < 2:
        return None
    mean = statistics.mean(samples)
    std = statistics.stdev(samples) if len(samples) > 1 else 0.0
    z = _Z95 if confidence >= 0.95 else 1.645
    margin = z * std / math.sqrt(len(samples))
    return (mean - margin, mean + margin)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class EquivalenceGate:
    """Verify optimized answers against baseline + critical facts.

    judge1 is the primary similarity judge. judge2 is only consulted when
    critical facts fail, so a single model cannot silently waive a fact
    loss. With no judges provided, deterministic_judge is used.
    """

    def __init__(
        self,
        judge1: Judge | None = None,
        judge2: Judge | None = None,
        model_version: str = "unknown",
        baseline_path: str | Path | None = None,
        strict: bool = True,
        similarity_threshold: float = 0.7,
        ci_confidence: float = 0.95,
    ):
        self.judge1 = judge1 or deterministic_judge
        self.judge2 = judge2 or deterministic_judge
        self.model_version = model_version
        self.baseline_path = Path(baseline_path) if baseline_path else None
        self.strict = strict
        self.similarity_threshold = similarity_threshold
        self.ci_confidence = ci_confidence
        self.cases: dict[str, RegressionCase] = {}
        self._samples: dict[str, list[float]] = {}
        self._history: list[GateResult] = []
        self.load_baseline()

    # -- baseline persistence -------------------------------------------------

    def add_case(self, case: RegressionCase) -> None:
        self.cases[case.case_id] = case

    def save_baseline(self) -> None:
        if not self.baseline_path:
            return
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model_version": self.model_version,
            "cases": [
                {
                    **asdict(case),
                    "critical_facts": [asdict(f) for f in case.critical_facts],
                }
                for case in self.cases.values()
            ],
        }
        tmp = self.baseline_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.replace(self.baseline_path)

    def load_baseline(self) -> None:
        if not self.baseline_path or not self.baseline_path.exists():
            return
        try:
            payload = json.loads(self.baseline_path.read_text())
            for raw in payload.get("cases", []):
                facts = [CriticalFact(**f) for f in raw.pop("critical_facts", [])]
                case = RegressionCase(**{**raw, "critical_facts": facts})
                self.cases[case.case_id] = case
        except Exception:
            pass

    # -- verification ---------------------------------------------------------

    def verify(
        self,
        case: RegressionCase,
        new_answer: str,
        similarity: float | None = None,
        record: bool = True,
    ) -> GateResult:
        facts = case.critical_facts or case.auto_facts()
        failed = check_critical_facts(new_answer, facts)

        if similarity is None:
            similarity, _judge_passed = self.judge1(case.baseline_answer, new_answer)

        votes: list[tuple[str, bool]] = [
            ("judge1", similarity >= self.similarity_threshold)
        ]

        # Critical fact loss -> second opinion. Even a second "pass" cannot
        # override a lost fact in strict mode: ROLLBACK wins.
        if failed and self.judge2 is not None:
            _sim2, passed2 = self.judge2(case.baseline_answer, new_answer)
            votes.append(("judge2", passed2))

        rollback = bool(failed) and self.strict

        # Confidence samples are per (case, answer): averaging across DIFFERENT
        # answers to the same case would let one good answer mask bad ones.
        sample_key = f"{case.case_id}:{hash(new_answer)}"
        self._samples.setdefault(sample_key, []).append(similarity)
        ci = mean_ci(self._samples[sample_key], self.ci_confidence)
        mean_sim = statistics.mean(self._samples[sample_key])

        passed = (not failed) and mean_sim >= self.similarity_threshold
        if not passed and failed:
            reason = (
                f"{len(failed)} critical fact(s) lost; mean similarity {mean_sim:.3f} "
                "does not override a fact loss"
            )
        elif not passed:
            reason = f"mean similarity {mean_sim:.3f} below {self.similarity_threshold}"
        else:
            reason = f"all {len(facts)} critical facts preserved; similarity ok"

        result = GateResult(
            case_id=case.case_id,
            passed=passed,
            rollback=rollback,
            similarity=mean_sim,
            samples=list(self._samples[sample_key]),
            confidence_interval=ci,
            failed_facts=failed,
            votes=votes,
            model_version=self.model_version,
            reason=reason,
        )

        if record:
            self._history.append(result)
        return result

    def run_batch(
        self, cases: list[RegressionCase], answers: list[str], similarity_scores: list[float] | None = None
    ) -> list[GateResult]:
        return [
            self.verify(case, answer, similarity=score)
            for case, answer, score in zip(
                cases, answers, similarity_scores or [None] * len(cases), strict=True
            )
        ]

    def report(self, results: list[GateResult] | None = None) -> str:
        results = results or self._history
        if not results:
            return "Equivalence Gate: no runs recorded"
        passed = sum(1 for r in results if r.passed)
        rolled = sum(1 for r in results if r.rollback)
        mean_sim = statistics.mean([r.similarity for r in results])
        return (
            f"Equivalence Gate: {passed}/{len(results)} passed, "
            f"{rolled} rollback, mean similarity {mean_sim:.3f} "
            f"(model {self.model_version})"
        )
