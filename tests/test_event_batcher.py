"""Тесты батчинга событий (из Buzz, buzz-acp queue.rs).

Проверяем главное: N событий -> 1 промпт, dedup, backoff с dead-letter,
честность FIFO, авто-освобождение зависших каналов.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.event_batcher import (  # noqa: E402
    MAX_BATCH_EVENTS,
    MAX_PENDING_PER_CHANNEL,
    MAX_RETRIES,
    EventBatcher,
)


# ── батчинг ──────────────────────────────────────────────────────────────
def test_all_events_drain_into_one_batch():
    b = EventBatcher()
    for i in range(5):
        assert b.push("news", f"event {i}")
    batch = b.flush_next()
    assert batch is not None
    assert len(batch.events) == 5
    assert [e.payload for e in batch.events] == [f"event {i}" for i in range(5)]
    assert batch.channel == "news"
    assert not b.has_flushable_work()


def test_batch_cap_at_max_events():
    b = EventBatcher()
    for i in range(MAX_BATCH_EVENTS + 10):
        b.push("news", f"e{i}")
    batch = b.flush_next()
    assert len(batch.events) == MAX_BATCH_EVENTS
    # остаток остался в очереди
    assert b.queued_count("news") == 10


def test_fifo_fairness_oldest_channel_first():
    b = EventBatcher()
    b.push("slow", "old")          # старейшее
    b.push("fast", "newer")
    batch = b.flush_next()
    assert batch.channel == "slow"  # канал со старейшим head-событием


# ── dedup режимы ─────────────────────────────────────────────────────────
def test_drop_mode_drops_while_in_flight():
    b = EventBatcher(dedup_mode="drop")
    b.push("c", "a")
    batch = b.flush_next()
    assert batch is not None
    assert not b.push("c", "b")  # канал в полёте -> дроп
    b.mark_complete("c")
    assert b.push("c", "b")      # после завершения -> принято


def test_queue_mode_accumulates_while_in_flight():
    b = EventBatcher(dedup_mode="queue")
    b.push("c", "a")
    batch = b.flush_next()
    assert batch is not None
    assert b.push("c", "b")      # в queue-режиме копится
    assert b.push("c", "d")
    b.mark_complete("c")
    batch2 = b.flush_next()
    assert len(batch2.events) == 2  # оба события одним батчем


def test_depth_cap_drops_oldest():
    b = EventBatcher()
    for i in range(MAX_PENDING_PER_CHANNEL + 3):
        b.push("c", f"e{i}")
    assert b.queued_count("c") == MAX_PENDING_PER_CHANNEL
    batch = b.flush_next()
    # старейшие 3 вытеснены, остались e3..e502
    assert batch.events[0].payload == "e3"


# ── retry / backoff / dead-letter ────────────────────────────────────────
def test_requeue_preserves_order_and_sets_backoff():
    b = EventBatcher()
    for i in range(3):
        b.push("c", f"e{i}")
    batch = b.flush_next()
    assert batch is not None
    assert b.requeue(batch) is None  # не dead-letter
    assert b.retry_counts["c"] == 1
    assert b.retry_after["c"] > 0    # троссель выставлен
    b.mark_complete("c")             # канал больше не в полёте
    # события вернулись в очередь, но канал под тросселем — работа есть
    assert b.has_undispatched_work()


def test_dead_letter_after_max_retries():
    b = EventBatcher()
    for i in range(2):
        b.push("c", f"e{i}")
    batch = b.flush_next()
    # уже было MAX_RETRIES попыток — следующая dead-letter
    b.retry_counts["c"] = MAX_RETRIES
    b.retry_after.pop("c", None)
    dead = b.requeue(batch)
    assert dead is not None  # dead-letter вернул батч
    assert dead.channel == "c"


def test_mark_complete_resets_retry_state():
    b = EventBatcher()
    b.push("c", "a")
    b.flush_next()
    assert b.is_channel_in_flight("c")
    b.mark_complete("c")
    assert not b.is_channel_in_flight("c")
    assert "c" not in b.retry_counts


# ── in-flight дедлайн ────────────────────────────────────────────────────
def test_stuck_in_flight_auto_released():
    b = EventBatcher(in_flight_deadline=0.001)
    b.push("c", "a")
    batch = b.flush_next()
    assert batch is not None
    assert b.has_in_flight()
    import time
    time.sleep(0.01)
    # авто-экспайр срабатывает при следующем тике (как в Buzz)
    assert not b.has_flushable_work()  # триггерит экспайр
    assert not b.has_in_flight()
    assert b.has_flushable_work() is False  # очередь пуста


def test_cancel_merge():
    b = EventBatcher()
    b.push("c", "a")
    batch = b.flush_next()
    b.requeue_as_cancelled(batch, "steer")
    b.mark_complete("c")
    b.push("c", "b")  # новое событие
    batch2 = b.flush_next()
    assert batch2.cancelled_events  # старое — как отменённое
    assert batch2.cancel_reason == "steer"


# ── экономия ─────────────────────────────────────────────────────────────
def test_estimate_savings():
    est = EventBatcher.estimate_savings(10, per_prompt_overhead_tokens=400)
    assert est["prompts_before"] == 10
    assert est["prompts_after"] == 1
    assert est["saved_tokens"] == 9 * 400
    assert est["saved_percent"] > 80


def test_estimate_savings_single_event_no_savings():
    est = EventBatcher.estimate_savings(1, per_prompt_overhead_tokens=400)
    assert est["saved_tokens"] == 0


def test_compact_expired_state():
    b = EventBatcher()
    b.push("c", "a")
    batch = b.flush_next()
    b.requeue(batch)
    b.retry_after["c"] = -1  # протух троссель
    b.retry_counts["old_dead"] = 5  # мусорный счётчик без очереди
    b.compact_expired_state()
    assert "old_dead" not in b.retry_counts
