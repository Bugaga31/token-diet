"""Tests for web_agent — offline logic only (no network in tests)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.web_agent import _extract_text, _find_chrome


def test_find_chrome_returns_existing_path():
    chrome = _find_chrome()
    assert chrome is None or Path(chrome).exists()


def test_extract_text_handles_empty():
    class FakePage:
        def inner_text(self, sel):
            raise Exception("no body")

        def evaluate(self, expr):
            return "hello from js"

    assert _extract_text(FakePage()) == "hello from js"


def test_extract_text_returns_body_text():
    class FakePage:
        def inner_text(self, sel):
            return "  Привет  мир  "

    assert _extract_text(FakePage()) == "  Привет  мир  "
