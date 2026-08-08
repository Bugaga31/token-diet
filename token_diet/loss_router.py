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

try:
    from .core import count_tokens
except ImportError:  # standalone use (tests, scripts with token-diet-lib on path)
    from core import count_tokens  # type: ignore[no-redef]

# ═══ Классификатор сегментов ═══

_ZERO_TOLERANCE_PATTERNS = [
    re.compile(r"```[\s\S]*?```"),  # code blocks
    re.compile(r"\{[^}]{10,}\}"),  # JSON-like
    re.compile(r"\b\d{4,}\b"),  # long numbers (IDs, timestamps)
    re.compile(r"https?://\S+"),  # URLs
    re.compile(r"\b[A-Z]{2,5}\b"),  # tickers
    re.compile(r"traceback|stack\s*trace|Error:|Exception:", re.IGNORECASE),
    re.compile(r"tool_use_id|tool_call|function_call", re.IGNORECASE),
    # Code signatures even without fences: def/class/import lines. Without
    # these, _compress_prose would collapse indentation inside code.
    re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+\w+\s*\(", re.MULTILINE),
    re.compile(r"^\s*(?:import|from)\s+\S+", re.MULTILINE),
]

_HIGH_TOLERANCE_MARKERS = [
    re.compile(r"^\s*(?:#|//|/\*|\*)", re.MULTILINE),  # comments
    re.compile(r"^\s*(?:описание|comment|note|remark|внимание|note:)", re.IGNORECASE),
]


def _starts_with_filler(text: str) -> bool:
    """True if a segment leads with a removable filler phrase.

    Short segments are normally verbatim (they are likely numbers, tickers,
    names). But a short sentence that merely opens with "Moreover," or
    "In conclusion," carries no information in that prefix.
    """
    cleaned = text.lstrip()
    return any(pattern.match(cleaned) for pattern, _replacement in _FILLER_WORD_PATTERNS)


def _classify_segment(text: str) -> str:
    """Классифицировать сегмент текста по устойчивости к сжатию."""
    if not text or not text.strip():
        return "skip"

    # Code blocks, JSON, stack traces → verbatim
    for pat in _ZERO_TOLERANCE_PATTERNS:
        if pat.search(text):
            return "zero"

    # Short filler segments ("Moreover, ...") — compress normally.
    if len(text) < 60:
        return "high" if _starts_with_filler(text) else "zero"

    # Mid-length: verbatim (numbers, names, URLs without code/stack)
    if len(text) < 100:
        return "zero"

    # Prose: длинный текст без кода → можно сжимать
    return "high"


# ═══ Compressor для high-tolerance сегментов ═══

_FILLER_WORD_PATTERNS = [
    (re.compile(r"\b(?:Furthermore|Moreover|Additionally|In\s+addition|Однако|Кроме\s+того|Более\s+того|Тем\s+не\s+менее),?\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:It\s+is\s+important\s+to\s+note|It\s+should\s+be\s+noted|Следует\s+отметить|Важно\s+отметить)\s+that\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:As\s+mentioned\s+earlier|As\s+stated\s+before|Как\s+упоминалось\s+ранее|Как\s+было\s+сказано)\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:Please\s+note|Please\s+be\s+aware|Обратите\s+внимание)\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:In\s+conclusion|To\s+summarize|In\s+summary|В\s+заключение|Подводя\s+итог)\s*", re.IGNORECASE), ""),
]

_WHITESPACE_PATTERNS = [
    (re.compile(r"\s{2,}"), " "),  # multiple spaces
    (re.compile(r"\n{3,}"), "\n\n"),  # multiple newlines
]

_FILLER_PATTERNS = _FILLER_WORD_PATTERNS + _WHITESPACE_PATTERNS

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
    # "Here is your code:" / "Here is your code." / "Вот ваш файл:" — cut ONLY
    # when the line ends right after the intro (code/table follows on the NEXT
    # line). Inline substance ("Here's the result: 42.") is left intact:
    # [^:\n]{0,80} cannot cross the first colon, so the match fails when any
    # content follows it on the same line.
    re.compile(
        r"^(?:here\s+is|here's|here\s+are|here\s+you\s+go|below\s+is|please\s+find|"
        r"вот\s+ваш(?:а|е)?|ниже\s+привед(?:ён|ен)(?:а|о|ы)?)"
        r"\s+[^:\n]{0,80}[:.]\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
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

    # Разбиваем на сегменты (по параграфам / блокам)
    segments = re.split(r"(\n{2,}|```[\s\S]*?```)", text)
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


# ═══ Aggressive sentence-level prose compressor ──────────────────────────

_IMPORTANCE_SCORE_PATTERNS = [
    (re.compile(r"\b\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?\b"), 8),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), 10),
    (re.compile(r"\b[A-ZА-ЯЁ][a-zа-яё]{2,}(?:\s+[A-ZА-ЯЁ][a-zа-яё]{2,}){0,2}\b"), 6),
    (re.compile(r"\b(?:no|not|never|don't|doesn't|не|никогда|нельзя|must|обязательно|required)\b", re.IGNORECASE), 5),
    (re.compile(r"https?://\S+"), 10),
    (re.compile(r"\b(?:анализ|analysis|report|отчёт|total|итого|summary|compliance|audit)\b", re.IGNORECASE), 4),
]


def _sentence_importance(sentence: str) -> float:
    score = 1.0
    for pattern, weight in _IMPORTANCE_SCORE_PATTERNS:
        if pattern.search(sentence):
            score += weight
    if _starts_with_filler(sentence):
        score -= 5
    if len(sentence) < 25:
        score -= 2
    return max(0.0, score)


def compress_prose_aggressive(
    text: str,
    keep_ratio: float = 0.55,
    counter: Any = count_tokens,
) -> str:
    """Aggressive sentence-level filtering.

    Drops low-importance sentences (filler, repetition) while always
    keeping sentences with numbers, dates, entity names, negations and
    compliance terms. First and last sentences are always preserved for
    context framing. The gate verifies no fact was lost.

    Returns original text when there aren't enough sentences to filter.
    """
    if not text or len(text) < 100:
        return text

    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) < 4:
        return text

    originals = list(sentences)
    scores = [_sentence_importance(s) for s in originals]

    keep_first = 0
    keep_last = len(sentences) - 1

    middle = [(i, scores[i]) for i in range(1, len(sentences) - 1)]
    middle.sort(key=lambda item: -item[1])
    keep_n = max(2, int(len(sentences) * keep_ratio))
    keep_indices = {keep_first, keep_last}
    for idx, _score in middle[:keep_n - 2]:
        keep_indices.add(idx)

    result_sentences = [originals[i] for i in sorted(keep_indices)]
    result = " ".join(result_sentences)

    # Safety: if we barely saved anything, don't compress
    if counter(result) >= counter(text) * 0.90:
        return text
    return result


def compress_tool_output(tool_name: str, result: dict | str | None) -> str:
    """Сжать вывод tool call для injection в context (Headroom-style).

    Для JSON данных: flatten + truncate
    Для текста: loss-tolerance routing
    """
    try:
        from .json_compressor import compress_json
    except ImportError:
        from json_compressor import compress_json  # type: ignore[no-redef]

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
