"""Тесты модулей из реверс-инжиниринга SonicJS плагинов (16.08).

AI Search → hybrid_search.py
Security Audit → security_audit.py
Global Variables → global_vars.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from token_diet.global_vars import GlobalVars
from token_diet.hybrid_search import (
    autocomplete,
    cache_stats,
    hybrid_search,
    rank,
    rerank_telegram,
    search_vault,
)
from token_diet.security_audit import SecurityAudit

DOCS = [
    {"title": "Стоп по Сургуту", "body": "SNGSP 200 шт, стоп 39.5, тейк 43.5"},
    {"title": "Полюс дивиденды", "body": "PLZL дивиденды ~406 руб ушли 17.08"},
    {"title": "Как запустить армию", "body": "token-diet army 'вопрос' — AnyModel DeepSeek"},
]


# ── hybrid_search ──────────────────────────────────────────────────────────
class TestHybridSearch:
    def test_rank_puts_relevant_first(self):
        r = rank("сургут стоп", DOCS, limit=3)
        assert r and r[0]["title"] == "Стоп по Сургуту"

    def test_rank_empty_query_returns_all(self):
        r = rank("", DOCS, limit=2)
        assert len(r) == 2

    def test_rank_empty_docs(self):
        assert rank("x", []) == []

    def test_autocomplete_partial(self):
        hints = autocomplete("стоп", DOCS, limit=3)
        assert "Стоп по Сургуту" in hints

    def test_autocomplete_short_query(self):
        assert autocomplete("с", DOCS) == []

    def test_hybrid_search_fallback_chain(self):
        sources = [
            {"name": "empty", "docs": []},
            {"name": "vault", "docs": DOCS},
        ]
        out = hybrid_search("дивиденды", sources, limit=2)
        assert out["source"] == "vault"
        assert out["results"]

    def test_hybrid_search_none(self):
        out = hybrid_search("x", [{"name": "a", "docs": []}])
        assert out == {"source": "none", "results": []}

    def test_rerank_telegram(self):
        r = rerank_telegram("полюс", DOCS, top_k=2)
        assert r and r[0]["title"] == "Полюс дивиденды"

    def test_search_vault_tmp(self, tmp_path):
        (tmp_path / "test_note.md").write_text(
            "---\ntitle: Тестовая заметка\n---\nтело про стоп сургут\n",
            encoding="utf-8",
        )
        r = search_vault("сургут", tmp_path, limit=3, use_cache=False)
        assert r and r[0]["title"] == "Тестовая заметка"

    def test_cache_stats(self):
        assert "entries" in cache_stats()


# ── security_audit ─────────────────────────────────────────────────────────
class TestSecurityAudit:
    def test_allowed_by_default(self, tmp_path):
        a = SecurityAudit(log_file=tmp_path / "audit.jsonl")
        assert a.check("user1") is True

    def test_lockout_after_failures(self, tmp_path):
        a = SecurityAudit(max_failures=3, window_seconds=60,
                          lockout_seconds=900, log_file=tmp_path / "audit.jsonl")
        assert a.fail("bot") is False
        assert a.fail("bot") is False
        assert a.check("bot") is True
        assert a.fail("bot") is True  # третья — бан
        assert a.check("bot") is False

    def test_success_resets(self, tmp_path):
        a = SecurityAudit(max_failures=2, window_seconds=60,
                          lockout_seconds=900, log_file=tmp_path / "audit.jsonl")
        a.fail("u")
        a.success("u")
        assert a.check("u") is True

    def test_window_expiry(self, tmp_path):
        # Окно = только для подсчёта неудач. Бан держится lockout_seconds.
        a = SecurityAudit(max_failures=2, window_seconds=1,
                          lockout_seconds=900, log_file=tmp_path / "audit.jsonl")
        a.fail("u")
        import time
        time.sleep(1.1)
        a._prune("u")
        # старая неудача выпала из окна: 1 новая < max_failures=2 → не бан
        assert a.fail("u") is False
        assert a.check("u") is True
        # две СВЕЖИЕ неудачи подряд — бан
        assert a.fail("u") is True
        assert a.check("u") is False

    def test_status_report(self, tmp_path):
        a = SecurityAudit(log_file=tmp_path / "audit.jsonl")
        s = a.status("u")
        assert "allowed" in s and "failures" in s


# ── global_vars ────────────────────────────────────────────────────────────
class TestGlobalVars:
    def test_set_get(self, tmp_path):
        g = GlobalVars(tmp_path / "vars.json")
        g.set("stop", "39.5")
        assert g.get("stop") == "39.5"

    def test_expand(self, tmp_path):
        g = GlobalVars(tmp_path / "vars.json")
        g.set_many({"stop": "39.5", "target": "43.5"})
        out = g.expand("стоп {stop}, тейк {target}")
        assert out == "стоп 39.5, тейк 43.5"

    def test_expand_unknown_key_untouched(self, tmp_path):
        g = GlobalVars(tmp_path / "vars.json")
        assert g.expand("значение {unknown_key}") == "значение {unknown_key}"

    def test_expand_many(self, tmp_path):
        g = GlobalVars(tmp_path / "vars.json")
        g.set("ticker", "SNGSP")
        out = g.expand_many(["{ticker} стоп", "{ticker} тейк"])
        assert out == ["SNGSP стоп", "SNGSP тейк"]

    def test_persistence(self, tmp_path):
        f = tmp_path / "vars.json"
        g1 = GlobalVars(f)
        g1.set("x", "1")
        g2 = GlobalVars(f)  # новый инстанс — читает с диска
        assert g2.get("x") == "1"

    def test_keys(self, tmp_path):
        g = GlobalVars(tmp_path / "vars.json")
        g.set_many({"b": "2", "a": "1"})
        assert g.keys() == ["a", "b"]
