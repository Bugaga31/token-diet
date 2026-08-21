"""Tests for cache_master — provider-aware cache prefix alignment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.cache_master import (  # noqa: E402
    BLOCK_SIZE,
    Provider,
    align_prefix,
    mark_static,
    plan_cache,
    relocate,
)


def _long_text(n: int) -> str:
    """Text of EXACTLY n tokens (по реальному count_tokens)."""
    from token_diet.core import count_tokens

    # набираем с запасом, потом режем по одному токену до точного N
    parts = [f"слово{i}" for i in range(n * 3)]
    text = " ".join(parts)
    while count_tokens(text) > n:
        text = text.rsplit(" ", 1)[0]
    # если недошли (нечётная токенизация) — добавляем по слову
    while count_tokens(text) < n:
        text += " 1"
    return text


class TestAlignPrefix:
    def test_aligned_prefix_unchanged(self):
        prefix = _long_text(1024)
        out = align_prefix(prefix, Provider.ANTHROPIC)
        # ровно 1024 токена — не трогаем
        assert out == prefix

    def test_non_aligned_cut_to_block(self):
        prefix = _long_text(1100)  # ~1100 токенов > 1024
        out = align_prefix(prefix, Provider.ANTHROPIC)
        from token_diet.core import count_tokens

        assert count_tokens(out) <= 1024
        assert count_tokens(out) > 900  # не обрезаем слишком агрессивно

    def test_short_prefix_untouched(self):
        prefix = "короткий префикс"
        assert align_prefix(prefix) == prefix

    def test_openai_block_128(self):
        prefix = _long_text(200)
        out = align_prefix(prefix, Provider.OPENAI)
        from token_diet.core import count_tokens

        assert count_tokens(out) <= 128

    def test_empty(self):
        assert align_prefix("") == ""


class TestMarkStatic:
    def test_markers_inserted(self):
        static = "СИСТЕМА: ты помощник."
        tail = " вопрос пользователя"
        out = mark_static(static + tail, static)
        assert "<<CACHE:STATIC>>" in out.text
        assert "<<CACHE:VOLATILE>>" in out.text
        assert out.static_tokens > 0
        assert out.volatile_tokens > 0

    def test_no_static_prefix(self):
        out = mark_static("только хвост", "")
        assert out.text == "только хвост"
        assert out.static_tokens == 0


class TestPlanCache:
    def test_aligned_plan(self):
        prefix = _long_text(1024)
        plan = plan_cache(prefix, provider=Provider.ANTHROPIC)
        assert plan.static_blocks == 1
        assert plan.wasted_tokens == 0
        assert plan.saved_per_request > 0

    def test_non_aligned_wasted(self):
        prefix = _long_text(1100)
        plan = plan_cache(prefix, provider=Provider.ANTHROPIC)
        assert plan.wasted_tokens > 0
        assert plan.static_blocks == 1

    def test_openai_blocks(self):
        prefix = _long_text(300)
        plan = plan_cache(prefix, provider=Provider.OPENAI)
        assert plan.block == 128
        assert plan.static_blocks >= 2

    def test_volatile_counted(self):
        plan = plan_cache("статик", "2026-08-16")
        assert plan.volatile_tokens > 0


class TestRelocate:
    def test_date_moved_to_tail(self):
        prefix = "Система: ты ИИ. Сегодня 2026-08-16. Помогай."
        out = relocate(prefix, "2026-08-16")
        assert "2026-08-16" in out
        # дата должна быть в конце, не в середине
        assert out.rstrip().endswith("2026-08-16")

    def test_not_present_unchanged(self):
        prefix = "без даты"
        assert relocate(prefix, "2026-08-16") == prefix
