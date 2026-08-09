"""investment_analyzer: honest, trap-aware market analysis.

Reverse-engineered best practices (DarkBit's SmartCommittee) fused with the
hard-won lesson from live testing:

    1. CHECK the dividend calendar  -> ex-div dates are GAP-DOWN traps
    2. CHECK if the news is already priced in -> a stock that FELL on a
       "great report" is not a buy for tomorrow
    3. CHECK forums / sentiment      -> crowd knows what the feed doesn't
    4. ONLY THEN give a verdict, with honest confidence and no guarantees

Everything here is deterministic and algorithmic — zero neural models, so it
costs ~0 tokens to run and never hallucinates a "guaranteed +2%".

For the people. For the planet. Honesty is cheaper than regret.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# 1. Trap detection — dividend calendar
# ─────────────────────────────────────────────────────────────────────────────

_EX_DIV_TRAP_WINDOW_DAYS = 3          # ex-div within this many calendar days
_RECENT_NEWS_WINDOW_DAYS = 5          # news this old still moves "tomorrow"
_PRICED_IN_DROP_THRESHOLD = 0.01      # fell >= 1% on the news day -> priced in

_TICKER_RE = re.compile(r"^[A-Z0-9._-]{1,10}$")


@dataclass(frozen=True)
class DividendEvent:
    """A known ex-dividend date for a ticker.

    On the ex-div date (T-1 in MOEX terms) the share opens with a gap DOWN of
    roughly the dividend amount. Buying the day before "to catch the div" is
    the classic retail trap.
    """

    ticker: str
    dividend_rub: float
    ex_div_date: date          # the day the price gaps down
    record_date: date | None = None

    def render(self) -> str:
        extra = f" (реестр {self.record_date.isoformat()})" if self.record_date else ""
        return (
            f"{self.ticker}: дивиденд {self.dividend_rub:.2f}₽, "
            f"гэп вниз {self.ex_div_date.isoformat()}{extra}"
        )


@dataclass(frozen=True)
class NewsItem:
    """A news headline/snippet about a ticker with its publication date."""

    ticker: str
    snippet: str
    published: date

    def render(self) -> str:
        return f"{self.published.isoformat()} {self.ticker}: {self.snippet[:120]}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Already-priced-in detection
# ─────────────────────────────────────────────────────────────────────────────

# Words that signal a *negative* or *disappointing* market reaction
# Words match by PREFIX; Russian case forms are allowed to continue
# (рекорд -> рекорде/рекордом/рекорда, разочар -> разочаровал), but a
# following Latin letter breaks the match to avoid cross-language noise.
_WORD_END = r"(?![a-zA-Z])"

_NEGATIVE_REACTION = re.compile(
    r"\b(упал|снизил|снизились|падение|просела|просел|разочар|продаж|"
    r"распродаж|коррекци|откат|откатил|инсайдер|продавал|не оправд|"
    r"ниже прогноз|слаб|убыточ|долг растёт|свободный денежный поток страдает)"
    + _WORD_END,
    re.IGNORECASE,
)
_POSITIVE_REACTION = re.compile(
    r"\b(вырос|рост|рекорд|максимум|позитивн|превзо|лучше прогноз|дивид|"
    r"выкупает|buyback|байбэк|целевая цена|повыш|апгрейд|обнов|превыс)"
    + _WORD_END,
    re.IGNORECASE,
)


@dataclass
class PricedInAssessment:
    """Was the news already consumed by the market?"""

    is_priced_in: bool
    signal: str            # "neutral" | "positive" | "negative"
    reason: str
    weight: float          # 0.0..1.0 how strongly priced in

    def render(self) -> str:
        flag = "ОТЫГРАНО" if self.is_priced_in else "НЕ отыграно"
        return f"[{flag}] {self.reason}"


def assess_news_priced_in(
    items: list[NewsItem],
    today: date,
    price_change_pct: float = 0.0,
    window_days: int = _RECENT_NEWS_WINDOW_DAYS,
) -> PricedInAssessment:
    """Judge whether recent news has already moved the price.

    Rules (learned the hard way with Yandex — stock FELL 2.23% on a record
    report, so "buy it because of the report" was wrong):

    1. Any news older than `window_days` -> not a fresh catalyst.
    2. Negative reaction words in fresh news -> priced-in (negative signal).
    3. Price fell >= `_PRICED_IN_DROP_THRESHOLD` on the news day -> priced in.
    4. A fresh positive catalyst with NO negative signal and NO price drop
       -> not yet priced in (the only case worth acting on).
    """
    fresh = [n for n in items if (today - n.published).days <= window_days]
    if not fresh:
        return PricedInAssessment(
            is_priced_in=True,
            signal="neutral",
            reason="Свежих новостей в окне нет — катализатора на завтра нет.",
            weight=0.3,
        )

    text = " ".join(n.snippet for n in fresh)
    neg = bool(_NEGATIVE_REACTION.search(text))
    pos = bool(_POSITIVE_REACTION.search(text))

    if price_change_pct <= -_PRICED_IN_DROP_THRESHOLD:
        return PricedInAssessment(
            is_priced_in=True,
            signal="negative",
            reason=(
                f"Цена упала на {abs(price_change_pct):.2f}% в день новости — "
                "рынок уже проголосовал против."
            ),
            weight=0.95,
        )
    if neg:
        return PricedInAssessment(
            is_priced_in=True,
            signal="negative",
            reason="Свежие новости содержат негатив/разочарование — роста ждать не на чем.",
            weight=0.8,
        )
    if pos:
        return PricedInAssessment(
            is_priced_in=False,
            signal="positive",
            reason="Свежий позитивный катализатор, рынок его ещё не отыграл.",
            weight=0.6,
        )
    return PricedInAssessment(
        is_priced_in=True,
        signal="neutral",
        reason="Новости есть, но ни положительного, ни отрицательного сигнала нет.",
        weight=0.4,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Honest verdict builder
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InvestmentVerdict:
    """One honest, trap-aware verdict for a ticker."""

    ticker: str
    action: str                    # "buy" | "avoid" | "neutral" | "no_data"
    confidence: float              # 0.0..1.0 — honest, capped
    traps: list[str] = field(default_factory=list)
    catalysts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    priced_in: PricedInAssessment | None = None
    committee: "CommitteeVerdict | None" = None
    disclaimer: str = (
        "Никто не может гарантировать движение за один день. "
        "Это оценка вероятности, не обещание."
    )

    def is_actionable(self) -> bool:
        return self.action == "buy" and self.confidence >= 0.5 and not self.traps

    def render(self, lang: str = "ru") -> str:
        lines = [f"🎯 {self.ticker} → {self.action.upper()} "
                 f"(уверенность {self.confidence:.0%})"]
        for t in self.traps:
            lines.append(f"  🚫 ЛОВУШКА: {t}")
        for c in self.catalysts:
            lines.append(f"  ⬆️ {c}")
        for r in self.risks:
            lines.append(f"  ⚠️ {r}")
        if self.priced_in and not self.traps:
            lines.append(f"  {self.priced_in.render()}")
        if self.committee:
            lines.append(self.committee.render())
        lines.append(f"  ℹ️ {self.disclaimer}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Committee (reverse-engineered from DarkBit's SmartCommittee)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CommitteeVote:
    """One member's opinion: role + sentiment + confidence + weight."""

    role: str                    # analyst | trader | critic | researcher
    sentiment: str               # bullish | bearish | neutral
    confidence: float            # 0.0..1.0
    weight: float                # 0.0..1.0
    opinion: str = ""


@dataclass
class CommitteeVerdict:
    """Weighted consensus across committee members."""

    votes: list[CommitteeVote]
    consensus: str               # bullish | bearish | neutral
    score: float                 # -1.0..1.0 net weighted sentiment

    @classmethod
    def from_votes(cls, votes: list[CommitteeVote]) -> "CommitteeVerdict":
        if not votes:
            return cls(votes=[], consensus="neutral", score=0.0)
        score = sum(
            v.weight * v.confidence * {"bullish": 1.0, "bearish": -1.0}.get(v.sentiment, 0.0)
            for v in votes
        )
        total = sum(v.weight for v in votes)
        norm = score / total if total else 0.0
        consensus = "neutral"
        if norm >= 0.2:
            consensus = "bullish"
        elif norm <= -0.2:
            consensus = "bearish"
        return cls(votes=votes, consensus=consensus, score=norm)

    def render(self) -> str:
        parts = [f"комитет → {self.consensus} (score {self.score:+.2f})"]
        for v in self.votes:
            parts.append(
                f"  • {v.role}: {v.sentiment} ({v.confidence:.0%}, вес {v.weight:.0%})"
            )
        return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 5. The analyzer itself
# ─────────────────────────────────────────────────────────────────────────────

class InvestmentAnalyzer:
    """Pipeline: dividend calendar -> news priced-in -> committee -> verdict.

    Zero LLM calls. Pure rules. Same info, fewer tokens — and far fewer
    expensive "I told you so" mistakes.
    """

    def __init__(
        self,
        events: list[DividendEvent] | None = None,
        news: list[NewsItem] | None = None,
        today: date | None = None,
        trap_window_days: int = _EX_DIV_TRAP_WINDOW_DAYS,
        max_confidence: float = 0.75,
    ):
        self.events = events or []
        self.news = news or []
        self.today = today or date.today()
        self.trap_window_days = trap_window_days
        self.max_confidence = max_confidence  # honesty cap: never 100%

    # ── calendar check ──
    def find_traps(self, ticker: str) -> list[str]:
        traps: list[str] = []
        for ev in self.events:
            if ev.ticker.upper() != ticker.upper():
                continue
            days = (ev.ex_div_date - self.today).days
            if 0 <= days <= self.trap_window_days:
                traps.append(
                    f"гэп вниз {ev.ex_div_date.isoformat()} "
                    f"(дивиденд {ev.dividend_rub:.2f}₽, через {days} дн.)"
                )
            elif days < 0:
                traps.append("отсечка уже прошла — ловить гэп поздно")
        return traps

    # ── news check ──
    def news_for(self, ticker: str) -> list[NewsItem]:
        return [n for n in self.news if n.ticker.upper() == ticker.upper()]

    # ── public API ──
    def analyze(
        self,
        ticker: str,
        price_change_pct: float = 0.0,
        votes: list[CommitteeVote] | None = None,
    ) -> InvestmentVerdict:
        ticker = ticker.strip().upper()
        traps = self.find_traps(ticker)
        priced = assess_news_priced_in(
            self.news_for(ticker), self.today, price_change_pct
        )
        committee = CommitteeVerdict.from_votes(votes or [])

        # ── decision logic (learned live: Polyus yes, T/Yandex no) ──
        catalysts: list[str] = []
        risks: list[str] = []

        if priced.signal == "positive" and not priced.is_priced_in:
            catalysts.append("Свежий позитивный катализатор, не отыгран рынком")

        if committee.consensus == "bullish":
            catalysts.append(f"Комитет бычий (score {committee.score:+.2f})")
        elif committee.consensus == "bearish":
            risks.append(f"Комитет медвежий (score {committee.score:+.2f})")

        if not catalysts and not traps:
            risks.append("Конкретного катализатора на завтра не найдено")

        # ── action ──
        if traps:
            action = "avoid"
        elif priced.signal == "negative":
            action = "avoid"
        elif catalysts and not risks:
            action = "buy"
        else:
            action = "neutral"

        # ── honest confidence (capped) ──
        confidence = 0.0
        if action == "buy":
            confidence = 0.4 + 0.2 * priced.weight + 0.2 * max(committee.score, 0.0)
        elif action == "neutral":
            confidence = 0.3
        elif action == "avoid":
            confidence = 0.8 if traps else 0.6
        confidence = max(0.0, min(self.max_confidence, confidence))

        return InvestmentVerdict(
            ticker=ticker,
            action=action,
            confidence=confidence,
            traps=traps,
            catalysts=catalysts,
            risks=risks,
            priced_in=priced,
            committee=committee,
        )

    # ── convenience ──
    def analyze_batch(
        self,
        tickers: list[str],
        price_changes: dict[str, float] | None = None,
        votes_by_ticker: dict[str, list[CommitteeVote]] | None = None,
    ) -> list[InvestmentVerdict]:
        """Analyze several tickers; returns verdicts sorted actionable-first."""
        price_changes = price_changes or {}
        votes_by_ticker = votes_by_ticker or {}
        verdicts = [
            self.analyze(
                t,
                price_change_pct=price_changes.get(t.upper(), 0.0),
                votes=votes_by_ticker.get(t.upper()),
            )
            for t in tickers
        ]
        return sorted(
            verdicts,
            key=lambda v: (v.action == "buy", v.confidence),
            reverse=True,
        )

    def best_pick(self, tickers: list[str], **kw) -> InvestmentVerdict | None:
        """The single best actionable pick, or None if none is safe."""
        verdicts = self.analyze_batch(tickers, **kw)
        for v in verdicts:
            if v.is_actionable():
                return v
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Tiny helpers for real usage
# ─────────────────────────────────────────────────────────────────────────────

def parse_date(value: str | date) -> date:
    """Accept 'YYYY-MM-DD', 'DD.MM.YYYY' or a date object."""
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y%m%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"не могу разобрать дату: {value!r}")


def build_events_from_calendar_rows(rows: list[dict]) -> list[DividendEvent]:
    """Build DividendEvents from generic calendar rows.

    Each row may use keys: ticker, dividend, ex_div_date (or ex_date),
    record_date. Unknown rows are skipped.
    """
    events: list[DividendEvent] = []
    for r in rows:
        ticker = str(r.get("ticker", "")).strip().upper()
        if not _TICKER_RE.match(ticker):
            continue
        ex = r.get("ex_div_date") or r.get("ex_date")
        if not ex:
            continue
        try:
            ex_d = parse_date(ex)
        except ValueError:
            continue
        rec_d = None
        if r.get("record_date"):
            try:
                rec_d = parse_date(r["record_date"])
            except ValueError:
                rec_d = None
        try:
            div = float(r.get("dividend", r.get("dividend_rub", 0.0)))
        except (TypeError, ValueError):
            div = 0.0
        events.append(
            DividendEvent(ticker=ticker, dividend_rub=div, ex_div_date=ex_d, record_date=rec_d)
        )
    return events


def assess_news_from_snippets(
    items: list[NewsItem],
    today: date | None = None,
) -> PricedInAssessment:
    """Convenience wrapper for news-only analysis."""
    return assess_news_priced_in(items, today or date.today())


__all__ = [
    "CommitteeVerdict",
    "CommitteeVote",
    "DividendEvent",
    "InvestmentAnalyzer",
    "InvestmentVerdict",
    "NewsItem",
    "PricedInAssessment",
    "assess_news_from_snippets",
    "assess_news_priced_in",
    "build_events_from_calendar_rows",
    "parse_date",
]
