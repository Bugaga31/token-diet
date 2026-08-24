"""Финансовые агенты — реверс-инжиниринг Anthropic financial-services (PDF-гайд, 2026).

Anthropic выпустили 10 готовых финансовых агентов для Claude:
  1. Market Researcher — отчёт по компании/сектору за минуты
  2. Model Builder — DCF-модель в Excel с прогнозами и рисками
  3. Earnings Reviewer — выжимка из квартальных отчётов и звонков с менеджментом

Мы реализуем их УМЕНИЯ алгоритмически (без платных планов Claude):
  - market_research_report()  — собрать отчёт по компании из данных
  - build_dcf_model()         — DCF-модель с чувствительностью
  - earnings_summary()        — выжимка квартального отчёта по чеклисту
  - stock_screener()          — скринер идей по критериям

Данные берём из T-Invest API (tinkoff_invest.py) и Telegram-разведки.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Модели данных
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CompanyData:
    """Собранные данные о компании."""
    ticker: str
    name: str = ""
    sector: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    market_cap: float = 0.0
    revenue_ttm: float = 0.0
    net_income_ttm: float = 0.0
    dividend_yield: float = 0.0
    p_e: float = 0.0
    p_b: float = 0.0
    roe: float = 0.0
    debt_equity: float = 0.0
    news: list[str] = field(default_factory=list)
    telegram_signals: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


@dataclass
class DCFResult:
    """Результат DCF-модели."""
    fair_value: float = 0.0
    current_price: float = 0.0
    upside_pct: float = 0.0
    verdict: str = ""
    wacc: float = 0.0
    growth_rate: float = 0.0
    years: int = 5
    sensitivity: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class CompanyReport:
    """Готовый отчёт по компании (как Market Researcher у Anthropic)."""
    ticker: str
    summary: str = ""
    market_overview: str = ""
    competitors: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    ideas: list[str] = field(default_factory=list)
    data: CompanyData | None = None
    generated_at: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# 1. Market Researcher — отчёт по компании
# ─────────────────────────────────────────────────────────────────────────────

def market_research_report(ticker: str, data: CompanyData | None = None,
                           news: list[str] | None = None,
                           telegram_signals: list[str] | None = None) -> CompanyReport:
    """Собрать единый отчёт по компании: обзор, конкуренты, риски, идеи.

    Как в гайде Anthropic: «называешь компанию — получаешь готовый отчёт».
    """
    report = CompanyReport(ticker=ticker.upper())
    report.generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    if data is not None:
        report.data = data
        report.summary = _build_summary(data)
        report.market_overview = _build_market_overview(data)
        report.risks = _assess_risks(data)
        report.ideas = _generate_ideas(data)

    if news:
        # ключевые риски из новостей
        risk_keywords = ["сниж", "сниз", "убыт", "штраф", "санкц", "пад", "проблем", "иск", "долг"]
        for n in news:
            if any(k in n.lower() for k in risk_keywords):
                report.risks.append(f"Новость: {n[:120]}")

    if telegram_signals:
        for s in telegram_signals[:5]:
            report.ideas.append(f"ТГ-сигнал: {s[:120]}")

    return report


def _build_summary(d: CompanyData) -> str:
    parts = [f"{d.name or d.ticker} ({d.ticker}) — сектор: {d.sector or 'н/д'}."]
    if d.price:
        ch = f"+{d.change_pct:.1f}%" if d.change_pct >= 0 else f"{d.change_pct:.1f}%"
        parts.append(f"Цена: {d.price:.1f}₽ ({ch}).")
    if d.market_cap:
        parts.append(f"Капитализация: {d.market_cap/1e9:.1f} млрд ₽.")
    if d.p_e:
        parts.append(f"P/E: {d.p_e:.1f}.")
    if d.dividend_yield:
        parts.append(f"Дивиденды: {d.dividend_yield:.1f}%.")
    return " ".join(parts)


def _build_market_overview(d: CompanyData) -> str:
    """Обзор рынка: где компания, тренды, сравнение."""
    lines = []
    if d.sector:
        lines.append(f"Компания работает в секторе «{d.sector}».")
    if d.roe:
        quality = "высокая" if d.roe > 20 else ("средняя" if d.roe > 10 else "низкая")
        lines.append(f"ROE {d.roe:.1f}% — {quality} рентабельность капитала.")
    if d.debt_equity:
        leverage = "высокая" if d.debt_equity > 1.5 else "умеренная"
        lines.append(f"Долг/Собственный капитал: {d.debt_equity:.2f} — {leverage} нагрузка.")
    if not lines:
        lines.append("Данных пока мало — нужен ручной анализ отчётности.")
    return " ".join(lines)


def _assess_risks(d: CompanyData) -> list[str]:
    risks = []
    if d.debt_equity and d.debt_equity > 2.0:
        risks.append(f"Высокий долг (Д/СК = {d.debt_equity:.1f}) — риск при росте ставки ЦБ.")
    if d.p_e and d.p_e > 30:
        risks.append(f"Оценка дорогая (P/E = {d.p_e:.1f}) — чувствительна к ожиданиям.")
    if d.change_pct < -2:
        risks.append(f"Снижение за день ({d.change_pct:.1f}%) — возможно давление продавцов.")
    if not risks:
        risks.append("Явных красных флагов по данным нет (проверь отчётность вручную).")
    return risks


def _generate_ideas(d: CompanyData) -> list[str]:
    ideas = []
    if d.dividend_yield and d.dividend_yield > 8:
        ideas.append(f"Высокая дивдоходность ({d.dividend_yield:.1f}%) — привлекательна на падении.")
    if d.p_e and 5 < d.p_e < 15:
        ideas.append(f"P/E {d.p_e:.1f} — дешёвый сегмент, если нет деградации бизнеса.")
    if not ideas:
        ideas.append("Идей по фундаменталу нет — следи за новостями и стаканом.")
    return ideas


# ─────────────────────────────────────────────────────────────────────────────
# 2. Model Builder — DCF-модель
# ─────────────────────────────────────────────────────────────────────────────

def build_dcf_model(price: float, revenue_ttm: float, net_income_ttm: float,
                    growth_rate: float = 0.10, wacc: float = 0.18,
                    years: int = 5, terminal_growth: float = 0.03,
                    shares_outstanding: float | None = None) -> DCFResult:
    """Построить DCF-модель (как Model Builder у Anthropic).

    Простая версия: FCF ≈ net_income * 0.8 (консервативная конверсия),
    дисконтируем по WACC, терминальная стоимость Гордона.
    """
    r = DCFResult()
    r.current_price = price
    r.wacc = wacc
    r.growth_rate = growth_rate
    r.years = years

    if net_income_ttm <= 0 or price <= 0:
        r.verdict = "Недостаточно данных (нет прибыли или цены)."
        return r

    fcf0 = net_income_ttm * 0.8
    pv_sum = 0.0
    fcf = fcf0
    for y in range(1, years + 1):
        fcf *= (1 + growth_rate)
        pv_sum += fcf / ((1 + wacc) ** y)

    terminal = fcf * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal / ((1 + wacc) ** years)
    enterprise_value = pv_sum + pv_terminal

    # Если нет числа акций — грубая оценка из цены и прибыли
    if not shares_outstanding:
        shares_outstanding = revenue_ttm / price if price else 1
        # запасной путь: если revenue странная, оцениваем акции через P/E ~ 10
        if shares_outstanding <= 0 or shares_outstanding > 1e12:
            shares_outstanding = net_income_ttm * 10 / price if price else 1

    fair_value = enterprise_value / shares_outstanding
    r.fair_value = fair_value
    if price:
        r.upside_pct = (fair_value / price - 1) * 100
        r.verdict = (
            f"Справедливая цена ≈ {fair_value:,.0f}₽ "
            f"({r.upside_pct:+.0f}% к текущей {price:,.0f}₽). "
            + ("Нед оценена — потенциал роста." if r.upside_pct > 10 else
               ("Переоценена — осторожно." if r.upside_pct < -10 else
                "Около справедливой — жди сигнала."))
        )
    else:
        r.verdict = f"Справедливая цена ≈ {fair_value:,.0f}₽ (нет текущей цены)."

    # Анализ чувствительности: growth × wacc
    for g in [growth_rate - 0.05, growth_rate, growth_rate + 0.05]:
        for w in [wacc - 0.03, wacc, wacc + 0.03]:
            if g <= terminal_growth or w <= terminal_growth:
                continue
            f = fcf0
            s = 0.0
            for y in range(1, years + 1):
                f *= (1 + g)
                s += f / ((1 + w) ** y)
            t = f * (1 + terminal_growth) / (w - terminal_growth)
            s += t / ((1 + w) ** years)
            r.sensitivity.setdefault(f"g={g:.0%}", {})[f"wacc={w:.0%}"] = round(s / shares_outstanding, 0)

    return r


# ─────────────────────────────────────────────────────────────────────────────
# 3. Earnings Reviewer — выжимка квартального отчёта
# ─────────────────────────────────────────────────────────────────────────────

def earnings_summary(text: str, ticker: str = "") -> dict[str, Any]:
    """Выжимка квартального отчёта/пресс-релиза по чеклисту Anthropic.

    Ключевые метрики, изменения прогнозов, главное из слов менеджмента.
    """
    result: dict[str, Any] = {
        "ticker": ticker,
        "metrics": {},
        "guidance_changes": [],
        "management_highlights": [],
        "risks": [],
        "one_liner": "",
    }

    if not text:
        result["one_liner"] = "Пустой текст отчёта."
        return result

    # Ищем числа с % и ₽/млн/млрд — ключевые метрики
    patterns = [
        (r"выручк\w+[^%]{0,60}([\d\s.,]+)\s*(млрд|млн|тыс)?", "выручка"),
        (r"чистая прибыл\w+[^%]{0,60}([\d\s.,]+)\s*(млрд|млн)?", "чистая прибыль"),
        (r"EBITDA[^%]{0,60}([\d\s.,]+)\s*(млрд|млн)?", "EBITDA"),
        (r"рентабельност\w+[^%]{0,40}([\d\s.,]+)\s*%", "рентабельность"),
        (r"долг\w*[^%]{0,60}([\d\s.,]+)\s*(млрд|млн)?", "долг"),
    ]
    for pat, label in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            result["metrics"][label] = m.group(0)[:120]

    # Изменения прогнозов / ориентиры
    for kw in ["прогноз", "ориентир", "ожидаем", "планируем", "target", "guidance"]:
        for m in re.finditer(rf".{{0,60}}{kw}.{{0,100}}", text, re.IGNORECASE):
            snippet = m.group(0).strip()
            if len(snippet) > 20:
                result["guidance_changes"].append(snippet[:150])
                break  # по одному на ключевое слово

    # Слова менеджмента
    for m in re.finditer(r"(генеральн\w+ директор|CEO|мы видим|мы ожидаем|мы уверены).{0,150}", text, re.IGNORECASE):
        s = m.group(0).strip()
        if len(s) > 25:
            result["management_highlights"].append(s[:170])
        if len(result["management_highlights"]) >= 3:
            break

    # Риски
    for kw in ["риск", "снижение", "падение", "давление", "неопределён", "санкц", "ставка"]:
        for m in re.finditer(rf".{{0,50}}{kw}.{{0,90}}", text, re.IGNORECASE):
            s = m.group(0).strip()
            if len(s) > 25 and s not in result["risks"]:
                result["risks"].append(s[:140])
            if len(result["risks"]) >= 3:
                break

    result["one_liner"] = (
        f"Отчёт {ticker or 'компании'}: "
        + (f"{len(result['metrics'])} метрик, " if result["metrics"] else "метрик не распознано, ")
        + (f"{len(result['guidance_changes'])} изменений прогнозов, "
           if result["guidance_changes"] else "без изменений прогнозов, ")
        + f"{len(result['risks'])} рисков."
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 4. Stock Screener — скринер идей
# ─────────────────────────────────────────────────────────────────────────────

def stock_screener(companies: list[CompanyData],
                   max_pe: float = 15.0, min_dividend: float = 5.0,
                   max_debt_equity: float = 2.0) -> list[CompanyData]:
    """Отфильтровать компании по критериям стоимости (скринер).

    Критерии: дешёвая оценка (P/E), дивиденды, умеренный долг.
    """
    hits = []
    for c in companies:
        score = 0
        if c.p_e and 0 < c.p_e < max_pe:
            score += 2
        if c.dividend_yield and c.dividend_yield >= min_dividend:
            score += 2
        if c.debt_equity and c.debt_equity <= max_debt_equity:
            score += 1
        if c.roe and c.roe > 15:
            score += 1
        if score >= 3:
            hits.append(c)
    return sorted(hits, key=lambda c: -c.dividend_yield)


# ─────────────────────────────────────────────────────────────────────────────
# CLI-точка входа
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Финансовые агенты (реверс Anthropic)")
    sub = p.add_subparsers(dest="cmd")

    p_dcf = sub.add_parser("dcf", help="DCF-модель")
    p_dcf.add_argument("--price", type=float, required=True)
    p_dcf.add_argument("--revenue", type=float, default=0)
    p_dcf.add_argument("--profit", type=float, required=True)
    p_dcf.add_argument("--growth", type=float, default=0.10)
    p_dcf.add_argument("--wacc", type=float, default=0.18)

    p_earn = sub.add_parser("earnings", help="Выжимка отчёта")
    p_earn.add_argument("--text", required=True)
    p_earn.add_argument("--ticker", default="")

    p_screen = sub.add_parser("screener", help="Скринер")
    p_screen.add_argument("--json", required=True, help="JSON массив компаний")

    args = p.parse_args()

    if args.cmd == "dcf":
        r = build_dcf_model(args.price, args.revenue, args.profit, args.growth, args.wacc)
        print(r.verdict)
        if r.sensitivity:
            print("Чувствительность (справедливая цена):")
            for g, row in r.sensitivity.items():
                print(f"  {g}: " + ", ".join(f"{w}={v:,.0f}₽" for w, v in row.items()))
    elif args.cmd == "earnings":
        r = earnings_summary(args.text, args.ticker)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.cmd == "screener":
        data = json.loads(args.json)
        comps = [CompanyData(**d) for d in data]
        hits = stock_screener(comps)
        print(f"Найдено кандидатов: {len(hits)}")
        for c in hits:
            print(f"  {c.ticker}: P/E={c.p_e:.1f} див={c.dividend_yield:.1f}% ROE={c.roe:.1f}%")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
