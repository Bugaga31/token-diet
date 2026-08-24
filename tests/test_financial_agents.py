"""Тесты финансовых агентов (реверс Anthropic financial-services)."""


from token_diet.financial_agents import (
    CompanyData,
    build_dcf_model,
    earnings_summary,
    market_research_report,
    stock_screener,
)


def test_dcf_undervalued():
    r = build_dcf_model(price=100, revenue_ttm=5e9, net_income_ttm=1.2e9,
                        growth_rate=0.10, wacc=0.18)
    assert r.fair_value > 0
    assert "Справедливая цена" in r.verdict
    assert len(r.sensitivity) > 0


def test_dcf_no_profit():
    r = build_dcf_model(price=100, revenue_ttm=0, net_income_ttm=0)
    assert "Недостаточно данных" in r.verdict


def test_earnings_summary_finds_metrics():
    text = ("Выручка выросла на 15% до 42 млрд рублей. Чистая прибыль 8 млрд. "
            "CEO заявил: мы уверены в росте. Риск: высокая ставка ЦБ давит на спрос.")
    r = earnings_summary(text, "TEST")
    assert r["ticker"] == "TEST"
    assert len(r["metrics"]) >= 1
    assert len(r["management_highlights"]) >= 1
    assert len(r["risks"]) >= 1
    assert "Отчёт TEST" in r["one_liner"]


def test_earnings_summary_empty():
    r = earnings_summary("", "X")
    assert "Пустой текст" in r["one_liner"]


def test_market_report_risks_from_news():
    rep = market_research_report("TEST", news=["Компания снизила прогноз на год"])
    assert len(rep.risks) >= 1
    assert rep.ticker == "TEST"


def test_market_report_data():
    d = CompanyData(ticker="PLZL", name="Полюс", price=1330, change_pct=-1.2,
                    market_cap=250e9, p_e=9.5, dividend_yield=6.0, roe=18.0)
    rep = market_research_report("PLZL", data=d)
    assert "Полюс" in rep.summary
    assert len(rep.risks) >= 1
    assert len(rep.ideas) >= 1


def test_screener_filters():
    comps = [
        CompanyData(ticker="A", p_e=8, dividend_yield=9, debt_equity=0.5, roe=20),
        CompanyData(ticker="B", p_e=50, dividend_yield=0.5, debt_equity=3.0, roe=5),
        CompanyData(ticker="C", p_e=12, dividend_yield=6, debt_equity=1.0, roe=12),
    ]
    hits = stock_screener(comps)
    assert len(hits) == 2
    assert hits[0].ticker in ("A", "C")
