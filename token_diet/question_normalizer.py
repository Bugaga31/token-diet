"""Question normalizer — strip politeness filler from user questions.

Every "Can you please tell me..." and "I was wondering if you could..."
costs tokens without adding information the model needs. This normalizer
strips common English and Russian politeness/filler prefixes from
questions, keeping the core intent intact.

The normalized question is always verified to be different from the
original; if no filler is detected, the original is returned unchanged.
"""

from __future__ import annotations

import re

_POLITENESS_PATTERNS = [
    # English
    (re.compile(r"^(?:can|will|would|could)\s+you\s+(?:please\s+)?(?:help\s+(?:me\s+)?)?", re.IGNORECASE), ""),
    (re.compile(r"^(?:i\s+(?:was\s+)?wonder(?:ing)?\s+if\s+you\s+(?:could\s+)?(?:please\s+)?)", re.IGNORECASE), ""),
    (re.compile(r"^(?:please\s+)(?:can\s+you\s+)?", re.IGNORECASE), ""),
    (re.compile(r"^(?:can\s+you\s+)?(?:help\s+(?:me\s+(?:to\s+)?)?)?", re.IGNORECASE), ""),
    (re.compile(r"^(?:i\s+(?:would|'d)\s+like\s+(?:you\s+)?to\s+)", re.IGNORECASE), ""),
    (re.compile(r"^(?:i\s+(?:would|'d)\s+like\s+to\s+know\s+)", re.IGNORECASE), ""),
    (re.compile(r"^(?:do\s+you\s+(?:happen\s+to\s+)?know\s+)", re.IGNORECASE), ""),
    # Russian
    (re.compile(r"^(?:можешь|можете)\s+(?:ли\s+)?(?:ты\s+)?(?:вы\s+)?(?:пожалуйста,?\s+)?(?:помочь\s+(?:мне\s+)?)?", re.IGNORECASE), ""),
    (re.compile(r"^(?:скажи(?:те)?,?\s+(?:пожалуйста,?\s+)?)", re.IGNORECASE), ""),
    (re.compile(r"^(?:я\s+(?:бы\s+)?хотел(?:а)?\s+(?:бы\s+)?(?:узнать|спросить),\s+)", re.IGNORECASE), ""),
    (re.compile(r"^(?:подскажи(?:те)?,?\s+(?:пожалуйста,?\s+)?)", re.IGNORECASE), ""),
]

# Capped: only strip if the question is long enough to have substance left.
_MIN_QUESTION_CHARS = 20


def normalize_question(question: str) -> str:
    """Strip politeness/filler prefixes from a user question.

    Returns the original if no filler was detected or the result would be
    too short to be useful.
    """
    original = question.strip()
    if len(original) < _MIN_QUESTION_CHARS:
        return question

    result = original
    changed = False
    for pattern, replacement in _POLITENESS_PATTERNS:
        new = pattern.sub(replacement, result).strip()
        if new != result:
            result = new
            changed = True

    if not changed or len(result) < 10:
        return question

    # Capitalise the result for readability
    if result and result[0].islower():
        result = result[0].upper() + result[1:]
    return result
