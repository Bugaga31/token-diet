"""End-to-end organism test: the whole token-diet works as ONE body.

Pipeline under test (no network, fully deterministic):

    TelegramMarketFeed (feed_to_news_items)
        -> InvestmentAnalyzer (trap-aware verdicts)
            -> ObsidianVault (persistent memory)
                -> context_for_prompt (compact memory for the model)

If this test passes, the organs communicate: news becomes analysis, analysis
becomes memory, memory becomes context. One organism, zero neural models.
"""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from token_diet.investment_analyzer import (
    DividendEvent,
    InvestmentAnalyzer,
    NewsItem,
)
from token_diet.obsidian_vault import ObsidianVault
from token_diet.telegram_market_feed import (
    RawMessage,
    compact_digest,
    feed_to_news_items,
)


def _msg(text: str, channel: str = "СМАРТЛАБ") -> RawMessage:
    return RawMessage(channel=channel, text=text, published=datetime(2026, 8, 10, 9, 0))


def test_full_organism_pipeline(tmp_path):
    # ── Organ 1: Telegram feed (raw messages from our curated channels) ──
    feed = [
        _msg("Полюс PLZL: золото на рекорде, аналитики повышают целевую цену", "СМАРТЛАБ"),
        _msg("T: отсечка по дивидендам 10 августа, гэп вниз 4.60₽", "РБК Инвестиции"),
        _msg("Сбер: рекордная маржа, но инсайдеры продавали", "РБК Инвестиции"),
        _msg("Газпром: совет директоров по дивидендам завтра", "БКС Экспресс"),
    ]
    digest = compact_digest(feed, max_lines=10)
    assert "PLZL" in digest
    assert len(digest.splitlines()) == 4

    news: list[NewsItem] = feed_to_news_items(feed)
    assert len(news) >= 4

    # ── Organ 2: InvestmentAnalyzer (trap-aware verdicts) ──
    calendar = [
        DividendEvent(ticker="T", dividend_rub=4.60, ex_div_date=date(2026, 8, 10)),
    ]
    analyzer = InvestmentAnalyzer(
        events=calendar,
        news=news,
        today=date(2026, 8, 10),
    )
    verdicts = analyzer.analyze_batch(["PLZL", "T", "SBER", "GAZP"])
    by_ticker = {v.ticker: v for v in verdicts}

    # PLZL — позитивный катализатор, не отыгран, ловушек нет -> buy
    assert by_ticker["PLZL"].action == "buy", "Полюс должен быть buy"
    # T — дивидендная отсечка -> ловушка -> avoid
    assert by_ticker["T"].action == "avoid", "T должен быть avoid (гэп вниз)"
    # SBER — негатив (инсайдеры продавали) -> avoid
    assert by_ticker["SBER"].action == "avoid", "Сбер должен быть avoid"
    # GAZP — совет директоров завтра (событие), нет явного позитива/негатива
    assert by_ticker["GAZP"].action in ("neutral", "buy", "avoid")

    # Честный потолок уверенности
    assert all(v.confidence <= 0.75 for v in verdicts), "уверенность никогда не > 75%"

    # ── Organ 3: ObsidianVault (persistent memory) ──
    vault = ObsidianVault(tmp_path)
    vault.write(
        "Рынок 10.08.2026",
        "Полюс buy (золото), T avoid (отсечка), Сбер avoid (инсайдеры).",
        tags=["market", "verdict"],
    )
    assert vault.read("Рынок 10.08.2026") is not None

    # ── Organ 4: memory feeds the model back ──
    ctx = vault.context_for_prompt("что покупать на рынке РФ")
    assert "Полюс" in ctx
    assert "отсечка" in ctx

    # ── The whole organism: verdicts persisted, memory retrievable ──
    for v in verdicts:
        vault.write(f"Вердикт {v.ticker}", v.render(), tags=["verdict"])
    # каждый вердикт сохранён и читается по имени (персистентность)
    assert vault.read("Вердикт T") is not None
    assert vault.read("Вердикт PLZL") is not None
    assert "гэп" in vault.read("Вердикт T")
    # контекст для модели непустой и содержит вердикты
    mem = vault.context_for_prompt("вердикты рынка")
    assert "уверенность" in mem
    assert vault.stats()["notes"] >= 5


def test_organism_degrades_gracefully():
    """If Telegram is offline, the rest of the body still works."""
    # no raw messages -> empty digest, no news items, analyzer still honest
    assert "Пусто" in compact_digest([])
    assert feed_to_news_items([]) == []
    analyzer = InvestmentAnalyzer(today=date(2026, 8, 10))
    v = analyzer.analyze("SBER")
    assert v.action in ("neutral", "avoid")
    assert v.confidence <= 0.75


def test_memory_is_persistent_across_instances(tmp_path):
    """Memory written by one instance is readable by another (it's a file)."""
    ObsidianVault(tmp_path).write("Факт", "Золото на рекорде $4400.", tags=["market"])
    v2 = ObsidianVault(tmp_path)
    body = v2.read("Факт")
    assert body is not None
    assert "золото" in body.lower() or "Золото" in body
