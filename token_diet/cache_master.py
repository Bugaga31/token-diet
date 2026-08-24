"""Cache Master — выравнивание промпта по границам кэш-блоков провайдера.

ИДЕЯ (из аудита Gemini 16.08.2026): провайдеры кэшируют вход по СТАБИЛЬНОМУ
ПРЕФИКСУ, но кэш-read тарифицируется блоками фиксированной длины:
  - Anthropic:  1024 / 4096 / 32768 токенов (блок = кратное 1024)
  - OpenAI:     128 токенов (блок = кратное 128)
  - Gemini:     1024 токенов (блок = кратное 1024)
Если статический префикс НЕ кратен блоку — следующий запрос перечитывает
хвост префикса по дорогой цене cache_write/input. Плюс volatile-хвост
(дата, request_id) ломает кэш целиком.

Что делает модуль (всё детерминированно, 0 LLM-вызовов):
1. align_prefix()  — подрезает/дополняет статический префикс до кратного блока.
2. mark_static()   — вставляет маркеры <<CACHE:STATIC>>/<<CACHE:VOLATILE>>.
3. plan_cache()    — отчёт: токенов в статике, блоков покрыто, экономия $/запрос.
4. relocate()      — выносит volatile-значение (дата/UUID) из статики в хвост.

Совместимо с cache_breakpoints.py: он находит breakpoints, мы их ЧИНИМ.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

try:
    from .core import count_tokens
except ImportError:  # standalone use
    from core import count_tokens  # type: ignore[no-redef]


class Provider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"


# Размер блока кэша (токенов) на провайдера
BLOCK_SIZE = {
    Provider.ANTHROPIC: 1024,
    Provider.OPENAI: 128,
    Provider.GEMINI: 1024,
}

# Розничные цены $/1M input токенов (черновик; берём консервативные)
INPUT_PRICE_PER_M = {
    Provider.ANTHROPIC: 3.00,   # claude-sonnet input
    Provider.OPENAI: 2.50,      # gpt-4o-mini input
    Provider.GEMINI: 1.25,      # gemini-2.5-flash input
}


@dataclass
class CachePlan:
    """Результат планирования кэш-раскладки промпта."""

    provider: Provider
    block: int
    static_tokens: int
    static_blocks: int
    volatile_tokens: int
    wasted_tokens: int = 0        # хвост статики, который перечитывается
    saved_per_request: float = 0.0  # $ экономии на каждом запросе

    def summary(self) -> str:
        return (
            f"{self.provider.value}: статик {self.static_tokens} токенов → "
            f"{self.static_blocks} блоков × {self.block}; "
            f"volatile {self.volatile_tokens} токенов изолировано; "
            f"экономия ~${self.saved_per_request:.4f}/запрос"
        )


@dataclass
class MarkedPrompt:
    """Промпт с маркерами границ кэш-зон."""

    text: str
    static_tokens: int = 0
    volatile_tokens: int = 0


_MARK_STATIC = "<<CACHE:STATIC>>"
_MARK_VOLATILE = "<<CACHE:VOLATILE>>"


def _token_len(text: str) -> int:
    """Длина в токенах (через count_tokens, fallback — грубая оценка)."""
    try:
        return count_tokens(text)
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


def align_prefix(prefix: str, provider: Provider = Provider.ANTHROPIC) -> str:
    """Подрезает статический префикс до кратного блоку размера.

    Anthropic и Gemini кэшируют блоки по 1024 токена — ровно 1024/2048/... токенов
    стоят как cache_read, остаток (например 1003) — как обычный input каждый раз.
    """
    block = BLOCK_SIZE[provider]
    tokens = _token_len(prefix)
    if tokens == 0:
        return prefix
    keep = (tokens // block) * block
    if keep == 0:
        # статика меньше блока — не пытаемся резать, кэш и так не включится
        return prefix
    if keep == tokens:
        return prefix
    # режем до <= keep — бинарный поиск по префиксу (было cur[:-1] по 1 символу → 3k итераций)
    if _token_len(prefix) <= keep:
        return prefix
    lo, hi = 0, len(prefix)
    best = prefix[:0]
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cand = prefix[:mid]
        if _token_len(cand) <= keep:
            best = cand
            lo = mid
        else:
            hi = mid - 1
        if hi - lo <= 1:
            # добьём линейно не более 2 шагов
            while hi > lo and _token_len(prefix[:hi]) > keep:
                hi -= 1
            return prefix[:hi]
    return best


def mark_static(prompt: str, static_prefix: str,
                provider: Provider = Provider.ANTHROPIC) -> MarkedPrompt:
    """Вставляет маркеры границ в промпт вокруг статического префикса."""
    if not static_prefix:
        return MarkedPrompt(text=prompt)
    if static_prefix in prompt:
        head, tail = prompt.split(static_prefix, 1)
        text = f"{head}{_MARK_STATIC}{static_prefix}{_MARK_VOLATILE}{tail}"
        return MarkedPrompt(
            text=text,
            static_tokens=_token_len(static_prefix),
            volatile_tokens=_token_len(tail),
        )
    # префикс не найден дословно — вставляем маркер в начало
    return MarkedPrompt(
        text=f"{_MARK_STATIC}{prompt}",
        static_tokens=_token_len(prompt),
        volatile_tokens=0,
    )


def plan_cache(static_prefix: str, volatile_tail: str = "",
               provider: Provider = Provider.ANTHROPIC,
               requests_per_day: int = 100) -> CachePlan:
    """Оценивает кэш-раскладку и экономию $ на запрос/день."""
    block = BLOCK_SIZE[provider]
    static_tokens = _token_len(static_prefix)
    volatile_tokens = _token_len(volatile_tail)

    aligned = (static_tokens // block) * block
    static_blocks = aligned // block if aligned else 0
    # невыровненный хвост перечитывается по полной цене input каждый раз
    wasted = static_tokens - aligned

    # Экономия: aligned токенов читаются как cache_read вместо input.
    # Cache-read обычно ~0.1× цены input (консервативно берём 0.5× выгоды).
    price_in = INPUT_PRICE_PER_M[provider]
    gain_per_m = price_in * 0.5  # половина цены input — выгода cache_read
    saved_per_request = (aligned / 1_000_000) * gain_per_m

    return CachePlan(
        provider=provider,
        block=block,
        static_tokens=static_tokens,
        static_blocks=static_blocks,
        volatile_tokens=volatile_tokens,
        wasted_tokens=wasted,
        saved_per_request=round(saved_per_request, 6),
    )


def relocate(static_prefix: str, volatile_value: str,
             provider: Provider = Provider.ANTHROPIC) -> str:
    """Выносит volatile-значение ИЗ статики в хвост (чинит breakpoint).

    Пример: система с "Сегодня: 2026-08-16" — дата в начале убивает кэш.
    Переносим дату в самый конец префикса, чтобы стабильная часть была
    максимально длинной и кратной блоку.
    """
    if volatile_value not in static_prefix:
        return static_prefix
    cleaned = static_prefix.replace(volatile_value, "").strip()
    # подрезаем до кратного блоку и приклеиваем volatile в конец
    aligned = align_prefix(cleaned, provider)
    return f"{aligned} {volatile_value}".strip()
