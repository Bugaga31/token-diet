"""Tests for tg_intel — unified Telegram intelligence (pure logic only)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.tg_intel import merge_and_rank


def _msg(text: str, channel: str = "chan", date: str = "2026-08-13 10:00:00",
         important: bool = False) -> dict:
    return {"channel": channel, "date": date, "text": text,
            "important": important}


def test_merge_normalizes_both_sources():
    global_results = [_msg("ГМК Норникель отчитался", channel="G") ]
    dialog_results = [_msg("Палладий растёт", channel="D")]
    out = merge_and_rank("норникель", global_results, dialog_results, top_k=10)
    assert len(out) == 2
    sources = sorted(r["source"] for r in out)
    assert sources == ["dialogs", "global"]


def test_dedupe_removes_reposts():
    text = "Норникель удвоил прибыль за полугодие"
    global_results = [_msg(text, channel="Канал А"), _msg(text, channel="Канал Б")]
    out = merge_and_rank("норникель", global_results, [], top_k=10)
    assert len(out) == 1


def test_bm25_ranks_relevant_first():
    results = [
        _msg("Совсем не про нашу тему: рецепт борща", channel="А"),
        _msg("Норникель: палладий вырос на 60% за полугодие", channel="Б"),
        _msg("Ещё про Норникель и никель на Мосбирже", channel="В"),
    ]
    out = merge_and_rank("норникель палладий", results, [], top_k=3)
    # самый релевантный запросу — про палладий
    assert out[0]["text"].startswith("Норникель: палладий")
    assert all("relevance" in r for r in out)


def test_empty_inputs_ok():
    assert merge_and_rank("что-то", [], None, top_k=5) == []
    assert merge_and_rank("", None, None, top_k=5) == []
