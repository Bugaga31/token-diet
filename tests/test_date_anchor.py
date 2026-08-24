"""Tests for date_anchor.py — the date anchor module (never guess dates)."""

from datetime import date

from token_diet.date_anchor import (
    date_anchor_block,
    describe_horizon,
    format_date_iso,
    format_date_ru,
    format_date_short,
    full_date_context,
    inject_date_anchor,
    is_trading_day,
    is_weekend,
    n_trading_days_ahead,
    next_trading_day,
    today,
    trading_days_between,
    weekday_ru,
)


class TestFormatting:
    def test_format_iso(self):
        d = date(2026, 8, 10)
        assert format_date_iso(d) == "2026-08-10"

    def test_format_short(self):
        d = date(2026, 8, 10)
        assert format_date_short(d) == "10.08.2026"

    def test_format_ru(self):
        d = date(2026, 8, 10)
        assert format_date_ru(d) == "10 августа 2026"

    def test_weekday_ru(self):
        # 2026-08-10 is a Monday
        d = date(2026, 8, 10)
        assert weekday_ru(d) == "понедельник"

    def test_today_returns_system_date(self):
        """Must come from system clock, not guessed."""
        assert today() == date.today()


class TestWeekend:
    def test_saturday(self):
        # 2026-08-08 is a Saturday
        assert is_weekend(date(2026, 8, 8))

    def test_sunday(self):
        # 2026-08-09 is a Sunday
        assert is_weekend(date(2026, 8, 9))

    def test_monday_not_weekend(self):
        assert not is_weekend(date(2026, 8, 10))


class TestTradingDays:
    def test_is_trading_day_monday(self):
        assert is_trading_day(date(2026, 8, 10))

    def test_is_trading_day_saturday(self):
        assert not is_trading_day(date(2026, 8, 8))

    def test_is_trading_day_holiday(self):
        # Jan 1 2026 is a holiday
        assert not is_trading_day(date(2026, 1, 1))

    def test_next_trading_day_from_friday(self):
        # Friday 2026-08-14 -> Monday 2026-08-17
        friday = date(2026, 8, 14)
        assert next_trading_day(friday) == date(2026, 8, 17)

    def test_next_trading_day_from_weekend(self):
        # Saturday -> Monday
        sat = date(2026, 8, 8)
        assert next_trading_day(sat) == date(2026, 8, 10)

    def test_n_trading_days_ahead(self):
        # From Monday Aug 10: +1 td = Tuesday Aug 11
        assert n_trading_days_ahead(1, date(2026, 8, 10)) == date(2026, 8, 11)

    def test_n_trading_days_skips_weekend(self):
        # From Friday Aug 14: +1 td = Monday Aug 17
        assert n_trading_days_ahead(1, date(2026, 8, 14)) == date(2026, 8, 17)

    def test_trading_days_between(self):
        # Mon to Fri = 4 trading days (Tue, Wed, Thu, Fri)
        assert trading_days_between(date(2026, 8, 10), date(2026, 8, 14)) == 4


class TestHorizon:
    def test_today(self):
        assert describe_horizon(0) == "сегодня"

    def test_tomorrow(self):
        assert describe_horizon(1) == "завтра"

    def test_day_after(self):
        assert describe_horizon(2) == "послезавтра"

    def test_this_week(self):
        assert describe_horizon(4) == "на этой неделе"

    def test_month(self):
        assert describe_horizon(20) == "в течение месяца"

    def test_long_term(self):
        assert describe_horizon(100) == "в долгосрочной перспективе"


class TestPromptInjection:
    def test_date_anchor_block(self):
        block = date_anchor_block(date(2026, 8, 10))
        assert "CURRENT DATE ANCHOR" in block
        assert "2026-08-10" in block
        assert "понедельник" in block

    def test_inject_empty(self):
        result = inject_date_anchor("", date(2026, 8, 10))
        assert "CURRENT DATE ANCHOR" in result

    def test_inject_into_prompt(self):
        result = inject_date_anchor("You are helpful.", date(2026, 8, 10))
        assert "CURRENT DATE ANCHOR" in result
        assert "You are helpful" in result

    def test_no_double_inject(self):
        result = inject_date_anchor("You are helpful.", date(2026, 8, 10))
        result2 = inject_date_anchor(result, date(2026, 8, 10))
        assert result2.count("CURRENT DATE ANCHOR") == 1

    def test_full_context(self):
        ctx = full_date_context(date(2026, 8, 10))
        assert ctx["today"] == "2026-08-10"
        assert ctx["is_trading_day"] is True
        assert ctx["weekday"] == "понедельник"


class TestIntegrationChain:
    def test_chain_injects_date(self):
        """IntelligenceChain must inject the date anchor into prompts."""
        from token_diet.intelligence_chain import IntelligenceChain
        chain = IntelligenceChain(model="deepseek", anchor_date=True)
        result = chain.prepare(task="Что будет завтра?", system="Будь полезным.")
        assert "CURRENT DATE ANCHOR" in result.enhanced_prompt
        assert format_date_iso() in result.enhanced_prompt
