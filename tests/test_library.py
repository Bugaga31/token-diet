"""Tests for Library — full-text storage + chunk retrieval (RAG-style).

Verifies the core claim: the model does NOT need to memorize whole
books; the full text lives on disk, and a query retrieves only the
relevant chunks.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.library import Library, chunk_text


def make_book() -> str:
    """A fake book of ~6000 chars with distinct topics per section."""
    parts = []
    parts.append("АВТОМОБИЛИ: история автомобилей началась с паровых машин. "
                 "Первый бензиновый автомобиль построил Карл Бенц. "
                 "Сегодня электромобили меняют индустрию. ")
    parts.append("КОСМОС: полет человека в космос начался с Гагарина. "
                 "Международная космическая станция на орбите Земли. "
                 "Марсоходы исследуют поверхность Марса. ")
    parts.append("ОКЕАН: океан покрывает семьдесят процентов планеты. "
                 "Глубоководные впадины полны неизученной жизни. "
                 "Коралловые рифы — важнейшие экосистемы. ")
    parts.append("ДИВИДЕНДЫ: дивиденды — часть прибыли компании. "
                 "Акционеры получают выплаты дважды в год. "
                 "Дивидендная доходность сравнивается с вкладами. ")
    # repeat so the text is long enough to split into several chunks
    return " ".join(parts * 20)


def test_chunk_text_splits_long_text():
    chunks = chunk_text(make_book(), size=1500)
    assert len(chunks) >= 3
    # each chunk within size + a bit
    for c in chunks:
        assert len(c.split()) <= 1600


def test_add_and_stats(tmp_path):
    lib = Library(tmp_path)
    meta = lib.add("Моя книга", make_book(), source="test.txt", kind="book")
    assert meta["chars"] == len(make_book())
    assert meta["chunks"] > 0
    stats = lib.stats()
    assert stats["sources"] == 1
    assert stats["total_chars"] == len(make_book())


def test_search_finds_relevant_chunk(tmp_path):
    lib = Library(tmp_path)
    lib.add("Моя книга", make_book(), source="test.txt", kind="book")

    # query about dividends must hit the dividends section
    hits = lib.search("как выплачивают дивиденды акционерам", limit=3)
    assert hits, "ничего не найдено"
    top = hits[0]
    text = top["text"].lower()
    assert "дивиденд" in text or "акционер" in text


def test_search_returns_limited_context(tmp_path):
    lib = Library(tmp_path)
    lib.add("Моя книга", make_book(), source="test.txt", kind="book")
    block = lib.context_for_prompt("космос станция марс", max_chars=800)
    assert "Library context" in block
    assert "End library context" in block
    # context must be small: we retrieve, not dump the whole book
    assert len(block) <= 3000


def test_search_empty_query_returns_nothing(tmp_path):
    lib = Library(tmp_path)
    lib.add("Моя книга", make_book(), source="test.txt", kind="book")
    assert lib.search("") == []


def test_add_replaces_same_title(tmp_path):
    lib = Library(tmp_path)
    lib.add("Одна", "первый текст про python и парсеры", source="a")
    lib.add("Одна", "второй текст про javascript и браузер", source="b")
    stats = lib.stats()
    assert stats["sources"] == 1  # same slug → replaced
