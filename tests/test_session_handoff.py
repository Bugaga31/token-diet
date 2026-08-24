"""Тесты само-суммаризации сессии (session_handoff, из buzz-agent).

Проверяем: выжимка фактов, сжатие истории, решения из hash-chain,
уроки из памяти, экономия места.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.session_handoff import (  # noqa: E402
    build_handoff,
    chain_decisions,
    compress_history,
    estimate_savings,
    extract_facts,
    render_handoff,
)


# ── выжимка фактов ───────────────────────────────────────────────────────
def test_extract_facts_finds_tickers_prices_dates():
    text = (
        "PLZL: 1325.8 ₽ (-1.4%), решили держать до среды.\n"
        "SNGSP докупили 50 шт по 41.20 ₽ (лимит 2100₽).\n"
        "Прогноз на 19.08.2026: рост 3%.\n"
        "просто текст без цифр"
    )
    facts = extract_facts(text)
    joined = " ".join(facts).lower()
    assert "plzl" in joined
    assert "sngsp" in joined
    assert "1325" in joined
    assert "41.20" in joined or "41,20" in joined
    assert "19.08" in joined
    assert "просто текст" not in joined  # шум отсеян


def test_extract_facts_dedup():
    text = "PLZL: 1300 ₽\nPLZL: 1300 ₽\nPLZL: 1300 ₽\n"
    facts = extract_facts(text)
    assert len(facts) == 1


# ── сжатие истории ───────────────────────────────────────────────────────
def test_compress_history_drops_duplicates_and_limits():
    history = "\n".join(["строка A", "строка A", "строка B", "x" * 1000])
    kept = compress_history(history, max_chars=300)
    assert len(kept) <= 3
    assert all(len(ln) <= 220 for ln in kept)  # дампы обрезаны
    assert kept[0] == "строка A"


def test_compress_history_respects_max_chars():
    history = "\n".join(f"строка номер {i} с данными" for i in range(200))
    kept = compress_history(history, max_chars=500)
    total = sum(len(ln) for ln in kept)
    assert total <= 500 + 200  # последняя строка может чуть перескочить


# ── handoff-блок ─────────────────────────────────────────────────────────
def test_build_handoff_with_history(tmp_path):
    history = "\n".join([
        "PLZL: 1325.8 ₽ (-1.4%), держим до среды",
        "SNGSP докупили 50 шт по 41.20 ₽",
    ] * 5)  # много дублей
    h = build_handoff(history, max_history_chars=3000,
                      include_chain=False, include_lessons=False,
                      include_rules=False)
    assert "ФАКТЫ ИЗ ИСТОРИИ" in h["handoff_text"]
    assert h["stats"]["saved_pct"] > 50  # сжатие заметное


def test_build_handoff_empty():
    h = build_handoff(None, include_chain=False, include_lessons=False,
                      include_rules=False)
    assert h["sections"] == {}
    assert "пусто" in h["handoff_text"]


def test_chain_decisions_included(tmp_path):
    from token_diet.audit_chain import AuditChain
    chain = AuditChain(tmp_path / "chain.jsonl")
    chain.predict("SNGSP", 43.5, by="2026-09-01", note="цель")
    chain.decision("докупить на просадке", reason="лестница")
    dec = chain_decisions(tmp_path / "chain.jsonl")
    joined = " ".join(dec).lower()
    assert "prediction_made" in joined
    assert "decision_made" in joined
    assert "sngsp" in joined


def test_vault_lessons(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Урок: первый").write_text("Не врать людям.\n", encoding="utf-8")
    (vault / "Урок: второй").write_text("Хэшируй то, что хранишь.\n", encoding="utf-8")
    (vault / "Заметка").write_text("не урок\n", encoding="utf-8")
    from token_diet.session_handoff import vault_lessons
    lessons = vault_lessons(vault)
    assert len(lessons) == 2
    assert all("Урок" in ln for ln in lessons)


def test_render_handoff_sections():
    text = render_handoff({"A": ["1", "2"], "B": ["3"]})
    assert "## A" in text and "- 1" in text and "- 3" in text


def test_estimate_savings():
    st = estimate_savings(10000, 2000)
    assert st["compressed_to_pct"] == 20.0
    assert st["saved_pct"] == 80.0
