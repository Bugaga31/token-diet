"""EventBatcher — батчинг событий в ОДИН промпт (реверс-инжиниринг block/buzz).

Взято из Buzz (Block Inc., Apache 2.0), крейт buzz-acp, модуль queue.rs.

Идея (экономия токенов): вместо N отдельных промптов на N событий — очередь
копит события по каналу и сливает их ВСЕ в один батч. Оверхед промпта
(system prompt + рамка + инструкции) платится ОДИН раз, а не N раз.

Механики, перенесённые из Buzz:
- per-channel очереди с капом глубины (500) — переполнение: дроп старейшего;
- MAX_BATCH_EVENTS (50) за один флаш;
- dedup-режимы: Drop (пока канал в полёте — новые молча дропаются) / Queue;
- in-flight с дедлайном и авто-экспайром (зависший канал освобождается);
- retry с экспоненциальным backoff + джиттер (±20%) и dead-letter после 10;
- честность FIFO: выбирается канал со старейшим событием;
- requeue сохраняет исходный received_at (не наказываем канал за ретрай);
- compact_expired_state() — чистит протухшие метаданные (не даёт росту карт).

100% офлайн, стандартная библиотека, 0 LLM-вызовов.
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

MAX_PENDING_PER_CHANNEL = 500   # кап глубины очереди канала
MAX_BATCH_EVENTS = 50           # максимум событий в одном батче
MAX_RETRIES = 10                # dead-letter после 10 попыток
BASE_RETRY_DELAY_SECS = 5.0     # базовый backoff
MAX_RETRY_DELAY_SECS = 300.0    # потолок backoff
IN_FLIGHT_DEADLINE_SECS = 7300.0  # дедлайн зависшего in-flight (как в buzz)


@dataclass
class QueuedEvent:
    channel: str
    payload: str
    received_at: float  # time.monotonic() — честность FIFO
    tag: str = "default"


@dataclass
class BatchEvent:
    channel: str
    payload: str
    received_at: float
    tag: str = "default"


class CancelReason:
    INTERRUPT = "interrupt"   # новый запрос отменяет прерванную работу
    STEER = "steer"           # новое сообщение во время работы — продолжить


@dataclass
class FlushBatch:
    channel: str
    events: list[BatchEvent] = field(default_factory=list)
    cancelled_events: list[BatchEvent] = field(default_factory=list)
    cancel_reason: Optional[str] = None


class EventBatcher:
    """Per-channel очередь событий с батчингом, dedup и backoff."""

    def __init__(self, dedup_mode: str = "queue", in_flight_deadline: float = IN_FLIGHT_DEADLINE_SECS):
        assert dedup_mode in ("drop", "queue"), f"dedup_mode: {dedup_mode}"
        self.dedup_mode = dedup_mode
        self.in_flight_deadline = in_flight_deadline
        self.queues: dict[str, deque[QueuedEvent]] = {}
        self.in_flight: set[str] = set()
        self.in_flight_deadlines: dict[str, float] = {}
        self.in_flight_batch_sizes: dict[str, int] = {}
        self.retry_after: dict[str, float] = {}
        self.retry_counts: dict[str, int] = {}
        self.cancelled_batches: dict[str, list[BatchEvent]] = {}
        self.cancel_reasons: dict[str, str] = {}

    # ── запись ────────────────────────────────────────────────────────────
    def push(self, channel: str, payload: str, tag: str = "default") -> bool:
        """Добавить событие в очередь канала. False — событие дропнуто."""
        if self.dedup_mode == "drop" and channel in self.in_flight:
            return False  # канал в полёте — молча дропаем (как Buzz)
        q = self.queues.setdefault(channel, deque())
        if len(q) >= MAX_PENDING_PER_CHANNEL:
            q.popleft()  # кап глубины — дроп старейшего
        q.append(QueuedEvent(channel, payload, time.monotonic(), tag))
        return True

    # ── флаш ──────────────────────────────────────────────────────────────
    def _expire_stuck_in_flight(self, now: float) -> None:
        """Авто-освобождение зависших каналов (пропущен mark_complete)."""
        expired = [c for c, d in self.in_flight_deadlines.items() if now >= d]
        for c in expired:
            self.in_flight.discard(c)
            self.in_flight_deadlines.pop(c, None)
            self.in_flight_batch_sizes.pop(c, None)

    def flush_next(self) -> Optional[FlushBatch]:
        """Слить следующий батч: канал со старейшим событием, до 50 событий."""
        now = time.monotonic()
        self._expire_stuck_in_flight(now)

        # канал со старейшим head-событием среди готовых (не in-flight, не throttled)
        candidates = [
            (c, q[0].received_at) for c, q in self.queues.items()
            if q and c not in self.in_flight
            and self.retry_after.get(c, 0) <= now
        ]
        if not candidates:
            # fallback: ждут отменённые батчи (пере-диспатч без новых событий)
            for c in list(self.cancelled_batches):
                if c not in self.in_flight:
                    cancelled = self.cancelled_batches.pop(c)
                    reason = self.cancel_reasons.pop(c, None)
                    self.in_flight.add(c)
                    self.in_flight_deadlines[c] = now + self.in_flight_deadline
                    self.in_flight_batch_sizes[c] = len(cancelled)
                    return FlushBatch(c, list(cancelled), [], reason)
            return None

        channel = min(candidates, key=lambda t: t[1])[0]
        q = self.queues[channel]
        drain = min(MAX_BATCH_EVENTS, len(q))
        events: list[BatchEvent] = []
        for _ in range(drain):
            e = q.popleft()
            events.append(BatchEvent(e.channel, e.payload, e.received_at, e.tag))
        if not q:
            del self.queues[channel]

        # стабильная сортировка: последний в батче — самый новый (как Buzz)
        events.sort(key=lambda e: e.received_at)

        self.in_flight.add(channel)
        self.in_flight_deadlines[channel] = now + self.in_flight_deadline
        self.in_flight_batch_sizes[channel] = len(events)

        cancelled = self.cancelled_batches.pop(channel, [])
        reason = self.cancel_reasons.pop(channel, None) if cancelled else None
        return FlushBatch(channel, events, cancelled, reason)

    def mark_complete(self, channel: str) -> None:
        """Успешное завершение: снять in-flight, сбросить счётчик ретраев."""
        self.in_flight.discard(channel)
        self.in_flight_deadlines.pop(channel, None)
        self.in_flight_batch_sizes.pop(channel, None)
        if self.retry_after.get(channel, 0) <= time.monotonic():
            self.retry_after.pop(channel, None)
        self.retry_counts.pop(channel, None)

    # ── ретраи ────────────────────────────────────────────────────────────
    def _backoff_delay(self, attempt: int) -> float:
        """Экспоненциальный backoff 5*2^(n-1), потолок 300с, джиттер ±20%."""
        base = BASE_RETRY_DELAY_SECS * (2 ** min(attempt - 1, 6))
        capped = min(base, MAX_RETRY_DELAY_SECS)
        return capped * random.uniform(0.8, 1.2)

    def requeue(self, batch: FlushBatch) -> Optional[FlushBatch]:
        """Пере-поставить батч с backoff. Вернёт батч при dead-letter."""
        attempt = self.retry_counts.get(batch.channel, 0) + 1
        self.retry_counts[batch.channel] = attempt
        if attempt > MAX_RETRIES:
            self.retry_counts.pop(batch.channel, None)
            self.retry_after.pop(batch.channel, None)
            return batch  # dead-letter: вернуть вызывающему
        delay = self._backoff_delay(attempt)
        q = self.queues.setdefault(batch.channel, deque())
        # в начало, в обратном порядке — исходный порядок сохраняется,
        # received_at не трогаем (не наказываем канал за ретрай)
        for e in reversed(batch.events):
            q.appendleft(QueuedEvent(e.channel, e.payload, e.received_at, e.tag))
        while len(q) > MAX_PENDING_PER_CHANNEL:
            q.pop()  # кап глубины при переполнении ретраями
        self.retry_after[batch.channel] = time.monotonic() + delay
        return None

    def requeue_as_cancelled(self, batch: FlushBatch, reason: str) -> None:
        """Отменённый батч -> сольётся в следующий флаш как cancelled_events."""
        entry = self.cancelled_batches.setdefault(batch.channel, [])
        entry.extend(batch.cancelled_events)
        entry.extend(batch.events)
        self.cancel_reasons[batch.channel] = reason

    # ── статус ────────────────────────────────────────────────────────────
    def has_in_flight(self) -> bool:
        return bool(self.in_flight)

    def is_channel_in_flight(self, channel: str) -> bool:
        return channel in self.in_flight

    def has_flushable_work(self) -> bool:
        now = time.monotonic()
        self._expire_stuck_in_flight(now)
        return any(
            q and c not in self.in_flight and self.retry_after.get(c, 0) <= now
            for c, q in self.queues.items()
        ) or any(c not in self.in_flight for c in self.cancelled_batches)

    def has_undispatched_work(self) -> bool:
        """Работа есть, даже если она под backoff-тросселем."""
        return any(q and c not in self.in_flight for c, q in self.queues.items()) \
            or any(c not in self.in_flight for c in self.cancelled_batches)

    def pending_channels(self) -> int:
        return len(self.queues)

    def queued_count(self, channel: str) -> int:
        return len(self.queues.get(channel, ()))

    def compact_expired_state(self) -> None:
        """Чистка протухших метаданных — не дать картам расти бесконечно."""
        now = time.monotonic()
        self.retry_after = {c: d for c, d in self.retry_after.items() if d > now}
        self.retry_counts = {
            c: n for c, n in self.retry_counts.items()
            if c in self.retry_after
            or self.queues.get(c) and len(self.queues[c]) > 0
            or c in self.in_flight
        }

    # ── экономия ──────────────────────────────────────────────────────────
    @staticmethod
    def estimate_savings(batch_size: int, per_prompt_overhead_tokens: int,
                         merge_cost_tokens: int = 0) -> dict:
        """Сколько токенов экономит батчинг N событий в 1 промпт.

        Без батчинга: N промптов, каждый платит overhead.
        С батчингом: 1 промпт платит overhead + merge_cost.
        Экономия = (N-1) * overhead - merge_cost.
        """
        n = max(1, batch_size)
        saved = (n - 1) * per_prompt_overhead_tokens - merge_cost_tokens
        return {
            "events": n,
            "prompts_before": n,
            "prompts_after": 1,
            "per_prompt_overhead": per_prompt_overhead_tokens,
            "saved_tokens": max(0, saved),
            "saved_percent": round(saved / max(1, n * per_prompt_overhead_tokens) * 100, 1),
        }

    def demo(self) -> str:
        """Живая демонстрация: 3 события -> 1 батч -> экономия."""
        for i in range(3):
            self.push("news", f"событие {i + 1}", tag="market")
        batch = self.flush_next()
        est = self.estimate_savings(len(batch.events) if batch else 0,
                                    per_prompt_overhead_tokens=400)
        lines = [
            "=== EVENT BATCHER (из Buzz) ===",
            f"3 события в очереди -> 1 батч из {len(batch.events) if batch else 0}:",
        ]
        if batch:
            for e in batch.events:
                lines.append(f"  • [{e.tag}] {e.payload}")
        lines.append(f"Экономия: {est['saved_tokens']} токенов "
                     f"({est['saved_percent']}% оверхеда) — 1 промпт вместо "
                     f"{est['prompts_before']}")
        return "\n".join(lines)
