"""Тесты новых техник сжатия (v3.19): JSON-схема, CSV-компакт, TOC."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from token_diet.compression_arsenal import (
    compress_csv,
    compress_json_schema,
    make_document_toc,
)

# ── JSON schema collapse ─────────────────────────────────────────────

def test_json_schema_compresses():
    j = '[{"id": 1, "name": "alpha", "status": "ok"},' \
        '{"id": 2, "name": "beta", "status": "ok"},' \
        '{"id": 3, "name": "gamma", "status": "ok"}]'
    result, f = compress_json_schema(j)
    assert result != j
    assert "id|name|status" in result
    assert "1|alpha|ok" in result


def test_json_schema_keeps_all_data():
    j = '[{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]'
    result, _ = compress_json_schema(j)
    for marker in ("1", "alpha", "2", "beta"):
        assert marker in result


def test_json_invalid_returns_original():
    text = "это не json"
    result, f = compress_json_schema(text)
    assert result == text


def test_json_non_list_returns_original():
    text = '{"single": "object"}'
    result, _ = compress_json_schema(text)
    assert result == text


def test_json_mixed_keys_returns_original():
    """Если ключи различаются — сжатие НЕбезопасно, возвращаем оригинал."""
    j = '[{"id": 1, "name": "a"}, {"id": 2, "other": "b"}]'
    result, _ = compress_json_schema(j)
    assert result == j


# ── CSV compact ──────────────────────────────────────────────────────

def test_csv_removes_empty_columns():
    c = "id; name; price; note\n1; Apple; 100.50; \n2; Banana; 200.00; extra\n"
    result, _ = compress_csv(c)
    assert "1,Apple,100.50" in result
    assert "note" in result  # шапка сохраняется


def test_csv_strips_leading_zeros():
    c = "id; value\n1; 00042\n"
    result, _ = compress_csv(c)
    assert ",42" in result
    assert "00042" not in result


def test_csv_single_line_unchanged():
    c = "одна строка"
    result, _ = compress_csv(c)
    assert result == c


# ── Document TOC ─────────────────────────────────────────────────────

def _big_doc() -> str:
    """Документ с большими секциями — TOC должен сократить."""
    parts = []
    for i in range(10):
        parts.append(f"# Глава {i}")
        for _ in range(20):
            parts.append(f"Подробный текст главы {i} с деталями и цифрами 12345.")
    return "\n".join(parts)


def test_toc_compresses_big_doc():
    doc = _big_doc()
    result = make_document_toc(doc)
    assert len(result) < len(doc)
    assert "Оглавление" in result


def test_toc_keeps_section_titles():
    doc = _big_doc()
    result = make_document_toc(doc)
    assert "Глава 1" in result
    assert "Глава 9" in result


def test_toc_short_doc_unchanged():
    short = "# Один\nпара строк\n"
    result = make_document_toc(short)
    assert result == short
