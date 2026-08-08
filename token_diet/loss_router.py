"""Loss-tolerance routing — классификация сегментов контента по устойчивости к сжатию.

Идея из github.com/jia-gao/leanctx:
  Classify every segment of a prompt by how much distortion it can survive,
  then compress each class differently.

Классы:
  - zero_tolerance: код, stack traces, JSON, tool_use_id — verbatim (0% compression)
  - low_tolerance: числа, тикеры, URL — verbatim
  - high_tolerance: проза, описания, комментарии — агрессивное сжатие (~50%)
  - opt_in: LLM-саммари (если доступен) — ещё больше

Саммари: zero-tolerance (verbatim) + high-tolerance (compressed) → recompose.
Если инварианты нарушены → fallback на original.
"""

from __future__ import annotations

import re
from typing import Any

from .core import count_tokens

# ═══ Классификатор сегментов ═══

# Zero-tolerance: только то, что нельзя терять байт-в-байт
# Числа 4+ цифр и URL — verbatim; код/JSON/stack — verbatim.
# Тикеры НЕ считаем zero-tolerance: они часто шум в прозе ("SBER has")
# и должны жить в high-tolerance сегментах. Конкретное число тикера
# уже покрыто в другой ветке через явные числа.
_ZERO_TOLERANCE_PATTERNS = [
    re.compile(r"```[\s\S]*?```"),  # code blocks
    re.compile(r"\{[^}]{10,}\}"),  # JSON-like
    re.compile(r"https?://\S+"),  # URLs
    re.compile(r"traceback|stack\s*trace|Error:|Exception:", re.IGNORECASE),
    re.compile(r"tool_use_id|tool_call|function_call", re.IGNORECASE),
]

_HIGH_TOLERANCE_MARKERS = [
    re.compile(r"^\s*(?:#|//|/\*|\*)", re.MULTILINE),  # comments
    re.compile(r"^\s*(?:описание|comment|note|remark|внимание|note:)", re.IGNORECASE),
]


def _classify_segment(text: str) -> str:
    """Классифицировать сегмент текста по устойчивости к сжатию.

    Логика: если в сегменте есть zero-tolerance контент → verbatim.
    Иначе если длинный прозовый текст (>60 chars) → high-tolerance.
    Иначе короткий verbatim (числа, имена).
    """
    if not text or not text.strip():
        return "skip"

    # Zero-tolerance контент → verbatim
    for pat in _ZERO_TOLERANCE_PATTERNS:
        if pat.search(text):
            return "zero"

    # Short segments — leave verbatim (числа, тикеры, имена)
    if len(text) < 60:
        return "zero"

    # Длинные prose (>=60 chars без zero-tolerance) → high-tolerance
    return "high"


# ═══ Compressor для high-tolerance сегментов ═══

_FILLER_PATTERNS = [
    (re.compile(r"\b(?:Furthermore|Moreover|Additionally|In\s+addition|Однако|Кроме\s+того|Более\s+того|Тем\s+не\s+менее),?\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:It\s+is\s+important\s+to\s+note|It\s+should\s+be\s+noted|Следует\s+отметить|Важно\s+отметить)\s+that\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:As\s+mentioned\s+earlier|As\s+stated\s+before|Как\s+упоминалось\s+ранее|Как\s+было\s+сказано)\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:Please\s+note|Please\s+be\s+aware|Обратите\s+внимание)\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:In\s+conclusion|To\s+summarize|In\s+summary|В\s+заключение|Подводя\s+итог)\s*", re.IGNORECASE), ""),
    (re.compile(r"\s{2,}"), " "),  # multiple spaces
    (re.compile(r"\n{3,}"), "\n\n"),  # multiple newlines
]

_REPETITIVE_PHRASES = [
    re.compile(r"\b(?:I|we|я|мы)\s+(?:will\s+now|теперь\s+будем|сейчас\s+будем)\s+", re.IGNORECASE),
    re.compile(r"\b(?:Let\s+us| Давайте|Let\s+me|Позвольте\s+мне)\s+", re.IGNORECASE),
]


def _compress_prose(text: str) -> str:
    """Сжать прозу: убрать filler-фразы, повторения, лишние пробелы."""
    result = text
    for pat, replacement in _FILLER_PATTERNS:
        result = pat.sub(replacement, result)
    # Remove repetitive phrases
    for pat in _REPETITIVE_PHRASES:
        result = pat.sub("", result)
    # Collapse extra spaces left after removals
    result = re.sub(r"  +", " ", result)
    result = re.sub(r"\n +", "\n", result)
    return result.strip()


# ═══ Token reduction for AI output ═══
# Идея из Headroom: trims what the model writes back (ceremony, restated code)

_CEREMONY_PATTERNS = [
    re.compile(r"^(?:Here\s+is|Вот|Вот\s+ваш|Ниже\s+приведён)\s+[^.]*\.\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:I\s+hope|Надеюсь)\s+[^.]*\.\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:Let\s+me\s+know|Дайте\s+знать|Если\s+есть\s+вопросы)[^.]*\.\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:Sure!|Of\s+course!|Конечно!|Obviously!)\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:I'll|Я\s+буду|Я\s+сейчас)\s+[^.]*\.\s*", re.IGNORECASE | re.MULTILINE),
]


def reduce_output(text: str) -> str:
    """Обрезать церемониальный шум из ответа AI (Headroom-style output reduction).

    Убирает:
      - "Here is your code..." — пустые вступления
      - "I hope this helps!" — пустые заключения
      - "Let me know if..." — пустые предложения
      - "Sure! Of course!" — пустые подтверждения

    НЕ трогает: код, данные, суть ответа.
    """
    result = text
    for pat in _CEREMONY_PATTERNS:
        result = pat.sub("", result)
    # Cleanup
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


# ═══ Main routing function ═══


def compress_with_routing(
    text: str,
    aggressive: bool = False,
    counter: Any = count_tokens,
) -> tuple[str, int, int]:
    """Сжать текст с loss-tolerance routing.

    Args:
        text: исходный текст
        aggressive: True = сжимать даже low-tolerance сегменты
        counter: функция подсчёта токенов

    Returns:
        (compressed_text, original_tokens, compressed_tokens)
    """
    if not text or len(text) < 100:
        original = counter(text)
        return text, original, original

    original_tokens = counter(text)

    # Разбиваем на сегменты: сначала по параграфам, потом длинные строки тоже
    # пробуем разбить по предложениям, чтобы не потерять prose в одном большом blob.
    # Threshold: всё >120 chars с большой вероятностью — prose (для коротких чисел
    # и имён verbatim не теряем — `_classify_segment` отдаёт их в zero anyway).
    segments: list[str] = []
    for chunk in re.split(r"(\n{2,})", text):
        if not chunk or not chunk.strip():
            continue
        if len(chunk) <= 120:
            segments.append(chunk)
            continue
        # Длинный chunk — разбиваем по предложениям
        for sent in re.split(r"(?<=[.!?])\s+", chunk):
            if sent.strip():
                segments.append(sent)

    compressed_parts: list[str] = []

    for seg in segments:
        if not seg or not seg.strip():
            continue
        cls = _classify_segment(seg)
        if cls == "skip":
            continue
        if cls == "zero":
            compressed_parts.append(seg)
        elif cls == "high":
            compressed = _compress_prose(seg)
            if compressed:
                compressed_parts.append(compressed)
        else:
            compressed_parts.append(seg)

    # Если aggressive — пытаемся дополнительно сжать
    if aggressive:
        combined = "\n\n".join(compressed_parts)
        combined = re.sub(r"\n{3,}", "\n\n", combined)
        combined = re.sub(r"  +", " ", combined)
        compressed_parts = [combined]

    result = "\n\n".join(compressed_parts)
    compressed_tokens = counter(result)

    # Safety: если сжатие не дало выгоды — вернуть original
    if compressed_tokens >= original_tokens * 0.95:
        return text, original_tokens, original_tokens

    return result, original_tokens, compressed_tokens


def compress_tool_output(tool_name: str, result: dict | str | None) -> str:
    """Сжать вывод tool call для injection в context (Headroom-style).

    Для JSON данных: flatten + truncate
    Для текста: loss-tolerance routing
    """
    from app.infrastructure.json_compressor import compress_json

    if not result:
        return ""
    if isinstance(result, str):
        compressed, _, _ = compress_with_routing(result)
        return compressed
    if isinstance(result, dict):
        if result.get("error") and not result.get("data"):
            return f"[{tool_name}]: error: {result['error']}"
        data = result.get("data", result)
        return compress_json(data)
    return str(result)[:500]
