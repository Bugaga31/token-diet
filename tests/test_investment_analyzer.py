"""Tests for InvestmentAnalyzer — the lessons we learned live on MOEX.

- Polyus: gold catalyst, no calendar trap      -> buy, honest confidence
- T-Technologies: ex-div gap-down trap         -> avoid
- Akron: ex-div gap-down trap                  -> avoid
- Yandex: record report but stock FELL 2.23%   -> already priced in -> avoid
"""

from datetime import date

import pytest

from token_diet.investment_analyzer import (
    CommitteeVerdict,
    CommitteeVote,
    DividendEvent,
    InvestmentAnalyzer,
    NewsItem,
    assess_news_priced_in,
    build_events_from_calendar_rows,
    parse_date,
)

TODAY = date(2026, 8, 10)  # Monday


def _polyus_calendar() -> list[DividendEvent]:
    # Polyus has NO events this week -> no trap
    return []


def _moex_calendar() -> list[DividendEvent]:
    # Real events from smart-lab on 10.08.2026
    return [
        DividendEvent(ticker="T", dividend_rub=4.6, ex_div_date=date(2026, 8, 10)),
        DividendEvent(ticker="AKRN", dividend_rub=235.0, ex_div_date=date(2026, 8, 10)),
        DividendEvent(ticker="YDEX", dividend_rub=110.0, ex_div_date=date(2026, 9, 21)),
    ]


def _yandex_news() -> list[NewsItem]:
    # The real case: record report, but insiders sold and price fell
    return [
        NewsItem(
            ticker="YDEX",
            snippet="Яндекс в первом полугодии 2026: рекордная маржинальность",
            published=date(2026, 8, 7),
        ),
        NewsItem(
            ticker="YDEX",
            snippet="Инсайдеры продавали акции, свободный денежный поток страдает",
            published=date(2026, 8, 7),
        ),
    ]


def _polyus_news() -> list[NewsItem]:
    # Gold broke record on Friday; Polyus itself has no fresh news
    return [
        NewsItem(
            ticker="PLZL",
            snippet="Золото превысило $4400 за унцию, рекорд, Китай скупает",
            published=date(2026, 8, 7),
        ),
    ]


# ── parse_date ───────────────────────────────────────────────────────────────


def test_parse_date_accepts_iso_and_russian_formats():
    assert parse_date("2026-08-10") == TODAY
    assert parse_date("10.08.2026") == TODAY
    assert parse_date(TODAY) == TODAY
    with pytest.raises(ValueError):
        parse_date("not-a-date")


def test_build_events_from_calendar_rows_skips_garbage():
    rows = [
        {"ticker": "T", "dividend": 4.6, "ex_div_date": "10.08.2026"},
        {"ticker": "YDEX", "dividend": 110, "ex_date": "2026-09-21"},
        {"ticker": "BAD TICKER!", "dividend": 1, "ex_div_date": "2026-08-10"},
        {"ticker": "NOEVENT", "dividend": 5},
    ]
    events = build_events_from_calendar_rows(rows)
    tickers = {e.ticker for e in events}
    assert tickers == {"T", "YDEX"}


# ── The Polyus lesson: no trap + catalyst -> buy ────────────────────────────


def test_polyus_case_buy_no_trap():
    analyzer = InvestmentAnalyzer(
        events=_polyus_calendar(),
        news=_polyus_news(),
        today=TODAY,
    )
    v = analyzer.analyze("PLZL")
    assert v.action == "buy"
    assert v.traps == []
    assert v.is_actionable()
    assert v.confidence <= analyzer.max_confidence  # honesty cap
    assert v.confidence >= 0.5


# ── The T-Technologies lesson: ex-div trap -> avoid ─────────────────────────


def test_t_technologies_exdiv_trap_avoid():
    analyzer = InvestmentAnalyzer(events=_moex_calendar(), today=TODAY)
    v = analyzer.analyze("T")
    assert v.action == "avoid"
    assert any("гэп вниз" in t for t in v.traps)


def test_akron_exdiv_trap_avoid():
    analyzer = InvestmentAnalyzer(events=_moex_calendar(), today=TODAY)
    v = analyzer.analyze("AKRN")
    assert v.action == "avoid"
    assert v.traps


# ── The Yandex lesson: record report but priced-in -> avoid ─────────────────


def test_yandex_record_report_but_fell_is_priced_in():
    analyzer = InvestmentAnalyzer(
        events=_moex_calendar(),
        news=_yandex_news(),
        today=TODAY,
    )
    v = analyzer.analyze("YDEX", price_change_pct=-2.23)
    assert v.priced_in is not None
    assert v.priced_in.is_priced_in
    assert v.priced_in.signal == "negative"
    assert v.action == "avoid"


def test_yandex_no_price_drop_but_negative_words_priced_in():
    # Even without a price number, "инсайдеры продавали" is a red flag
    assessment = assess_news_priced_in(_yandex_news(), TODAY)
    assert assessment.is_priced_in


def test_positive_unpriced_catalyst_is_actionable():
    items = [
        NewsItem(
            ticker="X",
            snippet="Выручка +19%, дивиденды рекомендованы, целевая цена повышена",
            published=date(2026, 8, 9),
        )
    ]
    assessment = assess_news_priced_in(items, TODAY, price_change_pct=0.5)
    assert not assessment.is_priced_in
    assert assessment.signal == "positive"


def test_old_news_is_not_a_fresh_catalyst():
    items = [
        NewsItem(
            ticker="X",
            snippet="Рекордный отчёт",
            published=date(2026, 7, 1),  # > 5 days ago
        )
    ]
    assessment = assess_news_priced_in(items, TODAY)
    assert assessment.is_priced_in  # nothing fresh to act on


# ── No data -> honest neutral, no fabrication ───────────────────────────────


def test_no_data_is_neutral_not_buy():
    analyzer = InvestmentAnalyzer(today=TODAY)
    v = analyzer.analyze("UNKNOWN")
    assert v.action == "neutral"
    assert not v.is_actionable()
    assert v.confidence < 0.5


# ── Committee consensus (reverse-engineered from DarkBit) ───────────────────


def test_committee_bullish_consensus_raises_confidence():
    votes = [
        CommitteeVote("analyst", "bullish", 0.8, 1.0, "золото растёт"),
        CommitteeVote("trader", "bullish", 0.7, 0.8, "катализатор свежий"),
        CommitteeVote("critic", "neutral", 0.5, 0.6, "риск коррекции"),
    ]
    committee = CommitteeVerdict.from_votes(votes)
    assert committee.consensus == "bullish"
    assert committee.score > 0

    analyzer = InvestmentAnalyzer(
        events=_polyus_calendar(), news=_polyus_news(), today=TODAY
    )
    v = analyzer.analyze("PLZL", votes=votes)
    assert v.action == "buy"
    assert v.committee is not None
    assert v.committee.consensus == "bullish"


def test_committee_bearish_overrides_positive_news():
    votes = [
        CommitteeVote("analyst", "bearish", 0.9, 1.0, "перегрета"),
        CommitteeVote("critic", "bearish", 0.8, 0.6, "инсайдеры продают"),
    ]
    committee = CommitteeVerdict.from_votes(votes)
    assert committee.consensus == "bearish"

    analyzer = InvestmentAnalyzer(
        events=_polyus_calendar(), news=_polyus_news(), today=TODAY
    )
    v = analyzer.analyze("PLZL", votes=votes)
    # bearish committee outweighs the positive gold news -> not a buy
    assert v.action != "buy"


def test_empty_committee_is_neutral():
    committee = CommitteeVerdict.from_votes([])
    assert committee.consensus == "neutral"
    assert committee.score == 0.0


# ── Honesty guarantees ──────────────────────────────────────────────────────


def test_confidence_never_exceeds_honesty_cap():
    analyzer = InvestmentAnalyzer(
        events=_polyus_calendar(),
        news=_polyus_news(),
        today=TODAY,
        max_confidence=0.75,
    )
    strong_votes = [
        CommitteeVote("analyst", "bullish", 1.0, 1.0),
        CommitteeVote("trader", "bullish", 1.0, 1.0),
        CommitteeVote("researcher", "bullish", 1.0, 1.0),
        CommitteeVote("critic", "bullish", 1.0, 1.0),
    ]
    v = analyzer.analyze("PLZL", votes=strong_votes)
    assert v.confidence <= 0.75


def test_render_contains_disclaimer():
    analyzer = InvestmentAnalyzer(
        events=_polyus_calendar(), news=_polyus_news(), today=TODAY
    )
    v = analyzer.analyze("PLZL")
    text = v.render()
    assert "ПОКУПКА" in text or "BUY" in text
    assert "не обещание" in text or "гарантировать" in text


# ── Batch / best pick ───────────────────────────────────────────────────────


def test_best_pick_skips_traps_and_finds_polyus():
    analyzer = InvestmentAnalyzer(
        events=_moex_calendar(),
        news=_polyus_news(),
        today=TODAY,
    )
    best = analyzer.best_pick(["T", "AKRN", "YDEX", "PLZL"])
    assert best is not None
    assert best.ticker == "PLZL"
    assert best.action == "buy"


def test_best_pick_returns_none_when_everything_is_trapped():
    analyzer = InvestmentAnalyzer(events=_moex_calendar(), today=TODAY)
    best = analyzer.best_pick(["T", "AKRN"])
    assert best is None
