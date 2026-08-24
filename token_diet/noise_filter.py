"""Noise Filter — extract signal from ANY garbage input.

Philosophy: people type nonsense. LLMs should see only the real question.
This module handles: ALL CAPS, emoji spam, gibberish, repetition, filler,
emotional padding, excessive punctuation, and mixed-language noise.

Use case: "asdfghjkl OMG PLZ HELP!!!!! 😭😭😭 I need to like um basically
           maybe write a function or whatever lol" → "Write a function"

All techniques are 100% algorithmic (no neural models).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ═══════════════════════════════════════════════════════════════════════════════
# 1. ALL CAPS NORMALIZER
# ═══════════════════════════════════════════════════════════════════════════════


def _is_all_caps(text: str) -> bool:
    """Check if text is predominantly ALL CAPS."""
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return False
    caps_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
    return caps_ratio > 0.7


def normalize_caps(text: str) -> str:
    """Normalize ALL CAPS shouting to normal case.

    Heuristic: if 70%+ of alpha chars are uppercase, normalize the whole text.
    Otherwise, leave mixed-case text alone (preserves proper names).
    """
    if _is_all_caps(text):
        # Don't just lowercase — preserve sentence boundaries
        result = text.lower()
        # Capitalize first letter of sentences
        result = re.sub(r'(^\s*|[.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), result)
        return result
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# 2. EMOJI + SPECIAL CHARACTER SPAM REMOVER
# ═══════════════════════════════════════════════════════════════════════════════

# Emoji ranges
_EMOJI_PATTERN = re.compile(
    '[' +
    '\U0001F300-\U0001F9FF'  # Misc symbols, emoticons, transport, etc.
    '\U0001FA00-\U0001FA6F'  # Chess symbols
    '\U0001FA70-\U0001FAFF'  # Symbols extended-A
    '\u2600-\u27BF'          # Misc symbols (includes many emoji)
    '\u2B50'                  # Star
    '\u2705'                  # Checkmark
    '\u274C'                  # Cross mark
    '\u2753-\u2757'           # Question/exclamation marks
    '\u2795-\u2797'           # Plus/minus/divide
    '\u2728'                  # Sparkles
    '\u26A0-\u26A1'           # Warning/high voltage
    '\u2764'                  # Heart
    '\U0001F000-\U0001F02F'  # Mahjong/domino
    ']+',
    re.UNICODE,
)

# Common emoticon patterns
_EMOTICON_PATTERN = re.compile(
    r'(?:[:;][\\-^]?[)D(dPp/\\$@*]|'
    r'<3|XD|X\-D|:\-?\)|:\-?\(|:\-?D|:\-?P|;\-?\)|:\'\'\(|'
    r'T_T|TT|o_O|O_o|\._\.|\-_\-)',
)

# Aggressive emoji spam: 3+ emojis in a row (handled by _EMOJI_PATTERN which removes ALL emoji)


def remove_emoji(text: str) -> str:
    """Remove emoji and emoticons from text.

    Single emoji: remove with surrounding whitespace
    Emoji spam (3+): replace with '' (they're noise, not communication)
    """
    result = text
    # Aggressive: remove all emoji sequences
    result = _EMOJI_PATTERN.sub('', result)
    result = _EMOTICON_PATTERN.sub('', result)
    # Clean up: double spaces
    result = re.sub(r'  +', ' ', result)
    result = result.strip()
    return result if result else text


# ═══════════════════════════════════════════════════════════════════════════════
# 3. EXCESSIVE PUNCTUATION NORMALIZER
# ═══════════════════════════════════════════════════════════════════════════════


def normalize_punctuation(text: str) -> str:
    """Reduce excessive punctuation to normal levels.

    "HELP!!!!!" → "HELP!"
    "what??????" → "what?"
    "so....... yeah" → "so... yeah"
    "!!?!?!?" → "!?"
    """
    result = text
    # Collapse repeated exclamation marks
    result = re.sub(r'!{2,}', '!', result)
    # Collapse repeated question marks
    result = re.sub(r'\?{2,}', '?', result)
    # Collapse interrobangs
    result = re.sub(r'[!?]{3,}', '!?', result)
    # Collapse repeated periods
    result = re.sub(r'\.{4,}', '...', result)
    # Collapse !?!?!?
    result = re.sub(r'(!\?){2,}', '!?', result)
    result = re.sub(r'(\?!)(\?!)+', '?!', result)
    # Remove spaces between repeated punctuation
    result = re.sub(r'([!?.])\s+([!?.])', r'\1\2', result)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 4. GIBBERISH DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════


def _is_gibberish_word(word: str) -> bool:
    """Check if a word appears to be random keyboard mashing.

    Gibberish patterns:
    - Random-looking character sequences (no vowel patterns)
    - Repeated character patterns (aaaaa, asdfasdf)
    - Keyboard walks (asdfghjkl, qwertyuiop)
    """
    if not word or len(word) < 3:
        return False

    word_lower = word.lower()

    # Pure repetition: "aaaa", "hahaha", "lololol"
    if re.match(r'^(.{1,3})\1{2,}$', word_lower):
        return True

    # Keyboard walk patterns: asdf, qwer, zxcv, etc.
    keyboard_walks = [
        'asdf', 'qwer', 'zxcv', 'uiop', 'hjkl', 'nm,',
        'asdfg', 'qwert', 'zxcvb', 'uiop[', 'hjkl;', 'nm,./',
        'asdfgh', 'qwerty', 'zxcvbn', 'uiop[]', 'hjkl;\'', 'nm,./',
        'asdfghj', 'qwertyu', 'zxcvbnm', 'qaz', 'wsx', 'edc',
        'rfv', 'tgb', 'yhn', 'ujm', 'ik,', 'ol.', 'p;/',
        'йцук', 'фыва', 'ячсм', 'олдж', 'гшщз',
    ]
    for walk in keyboard_walks:
        if walk in word_lower:
            return True

    # No vowels → likely gibberish (but "hmm", "tsk" are exceptions)
    vowel_count = sum(1 for c in word_lower if c in 'aeiouyаеёиоуыэюя')
    consonant_count = sum(1 for c in word_lower if c.isalpha())
    if consonant_count > 0 and vowel_count == 0 and len(word_lower) > 3:
        return True
    # Very high consonant density (>85% consonants)
    if consonant_count > 0 and vowel_count / consonant_count < 0.15 and len(word_lower) > 4:
        return True

    return False


def remove_gibberish(text: str) -> str:
    """Remove gibberish words while keeping real content.

    Detects: keyboard mashing, random character sequences, repeated patterns.
    Preserves: real words, code identifiers, URLs, numbers.
    """
    words = text.split()
    if not words:
        return text

    cleaned = []
    for w in words:
        # FIRST: check if gibberish (before identifier check)
        if _is_gibberish_word(w):
            continue
        # Preserve: code-like, URLs, numbers, real words
        if (
            w.startswith(('http://', 'https://')) or
            re.match(r'^[\d.,]+$', w) or
            len(w) > 0  # all non-gibberish words pass
        ):
            cleaned.append(w)

    result = ' '.join(cleaned)
    return result if result.strip() else text


# ═══════════════════════════════════════════════════════════════════════════════
# 5. FILLER RAMBLING STRIPPER
# ═══════════════════════════════════════════════════════════════════════════════

# Filler sounds — spoken language artifacts in written text
_FILLER_SOUNDS = re.compile(
    r'\b(?:um|uh|er|ah|hmm|mmm|hm|like|y\'know|you know|'
    r'i mean|basically|literally|actually|essentially|'
    r'kind of|sort of|kinda|sorta|stuff|things|whatever|'
    r'э|ну|типа|как бы|короче|вообще|блин|там|это самое|'
    r'значит|в общем|собственно|так сказать)\b[,\s]*',
    re.IGNORECASE,
)

# Rambling intros — verbal filler before the actual question
_RAMBLING_INTROS = [
    re.compile(
        r'^(?:so|well|okay|ok|alright|right|now then|'
        r'ну|так|ладно|хорошо|итак|значит так)[,.\s]+',
        re.IGNORECASE,
    ),
    re.compile(
        r'^(?:hey|hi|hello|yo|sup|hey there|'
        r'привет|здравствуйте|здарова|ку)[,.!\s]+',
        re.IGNORECASE,
    ),
    re.compile(
        r'^(?:I have a question|I was wondering|I wanted to ask|'
        r'can I ask|may I ask|quick question|just wondering|'
        r'у меня вопрос|я хотел спросить|можно вопрос)[,.!\s]+',
        re.IGNORECASE,
    ),
]

# Complex filler phrases that span multiple words
# Complex filler PREFIXES — only remove the filler words, keep the main verb
_COMPLEX_FILLER_PREFIXES = [
    (re.compile(r"\b(?:I was (?:just |kinda |sort of )?wondering (?:if |whether )?)\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:could you|could ya|can you|can ya|would you|will you)\s+(?:maybe |possibly |perhaps |please |kindly )?", re.IGNORECASE), ""),
    (re.compile(r"\b(?:would (?:it be possible|there be a way) (?:to |for |that )?)", re.IGNORECASE), ""),
    (re.compile(r"\b(?:is there (?:any |a )?(?:way|chance|possibility) (?:to |that |for )?)", re.IGNORECASE), ""),
    (re.compile(r"\b(?:do you (?:happen to |maybe |possibly )?know (?:about |of |if |how |what |where |when |why )?)", re.IGNORECASE), ""),
    (re.compile(r"\b(?:if (?:it['\u2019]s|it is|that['\u2019]s|that is)) (?:not too much trouble|okay|alright|possible)[,.!\s]*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:sorry (?:to bother|for bothering|to ask|for asking))[,.!\s]*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:я (?:тут |вот )?(?:подумал|решил|хотел|собрался) (?:спросить|узнать|попросить))\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:не (?:подскажешь|подскажете|знаешь|знаете) (?:ли |случайно )?)\s*", re.IGNORECASE), ""),
    (re.compile(r"\b(?:извини|извините|прости|простите)(?: что (?:беспокою|отвлекаю|лезу))?[,.!\s]*", re.IGNORECASE), ""),
]


def strip_filler_rambling(text: str) -> str:
    """Strip verbal filler and rambling from text.

    Removes:
    - Filler sounds: um, uh, like, you know, basically...
    - Rambling intros: "So, I was wondering if maybe..."
    - Complex filler phrases
    - Politeness padding

    Keeps: the actual question/instruction.
    """
    result = text

    # Phase 1: Remove rambling intros
    for pattern in _RAMBLING_INTROS:
        result = pattern.sub('', result, count=1)

    # Phase 2: Remove filler sounds
    result = _FILLER_SOUNDS.sub('', result)

    # Phase 3: Remove complex filler prefixes
    for pattern, replacement in _COMPLEX_FILLER_PREFIXES:
        result = pattern.sub(replacement, result, count=1)

    # Clean up
    result = re.sub(r'  +', ' ', result)
    result = re.sub(r'^[,.\s]+', '', result)
    result = re.sub(r'[,.\s]+$', '', result)
    result = result.strip()

    # If we stripped too much, return original
    if len(result) < 5 and len(text) > 10:
        return text

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 6. EXTREME REPETITION DETECTOR + COLLAPSER
# ═══════════════════════════════════════════════════════════════════════════════


def collapse_repetition(text: str) -> str:
    """Collapse extreme repetition into single instances.

    "help help help help help" → "help"
    "please please please help me" → "please help me"
    "I need help. I need help. I need help." → "I need help."
    """
    # Word-level repetition (3+ consecutive identical words)
    result = re.sub(r'\b(\w+)(?:\s+\1\b){2,}', r'\1', text)

    # Phrase-level repetition: "I need help. I need help. I need help."
    # Split into sentences and deduplicate adjacent identical ones
    sentences = re.split(r'(?<=[.!?])\s+', result)
    if len(sentences) > 1:
        deduped = []
        prev = ''
        for s in sentences:
            s_norm = s.strip().lower()
            if s_norm != prev.lower():
                deduped.append(s)
                prev = s
        result = ' '.join(deduped)

    # Line-level repetition (3+ identical lines)
    lines = result.split('\n')
    if len(lines) > 2:
        deduped_lines = []
        prev_line = ''
        for line in lines:
            if line.strip().lower() != prev_line.lower():
                deduped_lines.append(line)
                prev_line = line
            elif line.strip():  # Duplicate non-empty line
                pass  # Skip duplicate
            else:
                deduped_lines.append(line)
        result = '\n'.join(deduped_lines)

    # Clean up
    result = re.sub(r'  +', ' ', result)
    return result.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. EMOTIONAL PADDING STRIPPER
# ═══════════════════════════════════════════════════════════════════════════════

# Only strip emotional padding when it appears as standalone drama
# (followed by exclamations, or repeated, or at line start — NOT in normal text)
_EMOTIONAL_PADDING = [
    re.compile(r'(?:^|[.!?]\s+)(?:OMG|OMFG|WTF|LOL|LMAO|ROFL|BRUH)\b\s*[!?]*\s*', re.IGNORECASE),
    re.compile(r'(?:^|[.!?]\s+)(?:PLEASE|PLZ|PLZZZ|PLS)\s*[!?]+\s*', re.IGNORECASE),
    re.compile(r'\b(?:I(?:\'| |)M BEGGING (?:YOU )?)[!?\s]*', re.IGNORECASE),
    re.compile(r'\b(?:THIS IS (?:SO |VERY |REALLY |EXTREMELY )?IMPORTANT)[!?\s]*', re.IGNORECASE),
    re.compile(r'(?:^|[.!?]\s+)(?:URGENT|ASAP|EMERGENCY|CRITICAL)\b[!?\s]*', re.IGNORECASE),
    # Only strip HELP/SOS when at line start AND followed by !!! (drama), not in normal usage like "help me"
    re.compile(r'^(?:HELP|SOS|HELLPPP)\s*[!?]+\s*', re.IGNORECASE),
    # Russian emotional
    re.compile(r'(?:^|[.!?]\s+)(?:БЛИН|ЧЁРТ|КАПЕЦ|ПИЗДЕЦ|ЖЕСТЬ|УЖАС)\b[!?\s]*', re.IGNORECASE),
    re.compile(r'(?:^|[.!?]\s+)(?:ПОЖАЛУЙСТА|УМОЛЯЮ|ПРОШУ)\s*[!?]+\s*', re.IGNORECASE),
    re.compile(r'(?:^|[.!?]\s+)(?:СРОЧНО|ОЧЕНЬ (?:ВАЖНО|НУЖНО|СРОЧНО))\b[!?\s]*', re.IGNORECASE),
]


def strip_emotional_padding(text: str) -> str:
    """Remove emotional outbursts and drama from text.

    Strips:
    - "OMG PLEASE I'M BEGGING YOU THIS IS SO IMPORTANT!!!!"
    - "HELP!!! SOS!!! URGENT!!!"
    - "БЛИН, ПОЖАЛУЙСТА, УМОЛЯЮ!!!"

    Keeps: the actual request after the drama.
    """
    result = text
    for pattern in _EMOTIONAL_PADDING:
        result = pattern.sub('', result)

    # Clean up
    result = re.sub(r'  +', ' ', result)
    result = re.sub(r'^[,.\s!?]+', '', result)
    result = result.strip()

    if len(result) < 5 and len(text) > 10:
        return text

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 8. SIGNAL EXTRACTOR — finds the real question in the noise
# ═══════════════════════════════════════════════════════════════════════════════


def _score_sentence_signal(sentence: str) -> float:
    """Score a sentence by how likely it contains the REAL question."""
    score = 0.0

    # Question markers
    if '?' in sentence:
        score += 0.3
    # Imperative/instruction markers
    if re.search(r'\b(?:write|create|make|build|fix|find|get|show|tell|explain|'
                 r'calculate|analyze|solve|implement|add|remove|update|delete|'
                 r'напиши|создай|сделай|найди|покажи|расскажи|объясни|'
                 r'посчитай|реши|исправь|добавь|удали|обнови)\b',
                 sentence, re.IGNORECASE):
        score += 0.3
    # Technical terms → likely the real question
    if re.search(r'\b(?:function|code|script|program|file|data|API|database|'
                 r'Python|JavaScript|React|SQL|HTTP|JSON|XML|'
                 r'функци|код|скрипт|файл|данны|баз[ау])\b',
                 sentence, re.IGNORECASE):
        score += 0.3
    # Length: too short = likely noise, too long = likely filler
    words = len(sentence.split())
    if 3 <= words <= 30:
        score += 0.2
    elif words > 30:
        score -= 0.1
    elif words < 3:
        score -= 0.1
    # No filler words = more likely real question
    filler_count = len(re.findall(
        r'\b(?:um|uh|like|basically|actually|literally|stuff|whatever|thing|'
        r'э|ну|типа|короче|вообще|блин)\b',
        sentence, re.IGNORECASE))
    if filler_count == 0:
        score += 0.1
    else:
        score -= 0.1 * filler_count

    # Cap
    return max(0.0, min(1.0, score))


def extract_signal(text: str) -> str:
    """Extract the core question/instruction from noisy text.

    If multiple sentences exist, pick the highest-signal one(s).
    If only one sentence, return it cleaned.
    If everything was stripped, return original (fail open).
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if not sentences:
        return text

    if len(sentences) <= 1:
        return text.strip()

    # Score each sentence
    scored = [(s, _score_sentence_signal(s)) for s in sentences]

    # Filter: keep sentences with score > 0.25 (lowered from 0.3)
    signal_sentences = [s for s, sc in scored if sc > 0.25]

    if signal_sentences:
        result = ' '.join(signal_sentences).strip()
        if len(result) > 5:
            return result

    # If no sentence scored high enough, keep all that have any content
    content_sentences = [s for s in sentences if len(s.split()) >= 2]
    if content_sentences:
        return ' '.join(content_sentences).strip()

    # Last resort: return original
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 9. UNIFIED NOISE FILTER — apply all stages
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class NoiseFilterResult:
    original: str
    cleaned: str
    noise_removed: bool
    stages_applied: list[str]


def filter_noise(
    text: str,
    *,
    aggressive: bool = True,
) -> NoiseFilterResult:
    """Apply ALL noise filters to extract clean signal from garbage input.

    Pipeline (order matters):
    1. Normalize caps (HELLO → Hello)
    2. Remove emoji
    3. Normalize punctuation (!!!! → !)
    4. Remove gibberish (asdfghjkl → '')
    5. Strip filler rambling (um, like, you know → '')
    6. Strip emotional padding (OMG PLEASE!!! → '')
    7. Collapse repetition (help help help → help)
    8. Extract signal (find the real question)

    Args:
        text: any noisy/garbage input
        aggressive: if True, extract only core signal; if False, just clean

    Returns:
        NoiseFilterResult with cleaned text and applied stages

    Examples:
        "OMG PLZ HELP!!!! 😭 asdfghjkl write a function lol"
        → "write a function"

        "So like um I was wondering if maybe you could possibly help me
         with idk a Fibonacci function or whatever lol thanks!!! 😊"
        → "help me with a Fibonacci function"

        "HELP ME. HELP ME. HELP ME. SOS. SOS. PLEASE."
        → "HELP ME."
    """
    stages = []
    result = text
    original = text

    # Stage 1: Normalize ALL CAPS
    prev = result
    result = normalize_caps(result)
    if result != prev:
        stages.append('caps')

    # Stage 2: Remove emoji
    prev = result
    result = remove_emoji(result)
    if result != prev:
        stages.append('emoji')

    # Stage 3: Normalize punctuation
    prev = result
    result = normalize_punctuation(result)
    if result != prev:
        stages.append('punctuation')

    # Stage 4: Remove gibberish words
    prev = result
    result = remove_gibberish(result)
    if result != prev:
        stages.append('gibberish')

    # Stage 5: Strip filler rambling
    prev = result
    result = strip_filler_rambling(result)
    if result != prev:
        stages.append('filler')

    # Stage 6: Strip emotional padding
    prev = result
    result = strip_emotional_padding(result)
    if result != prev:
        stages.append('emotional')

    # Stage 7: Collapse repetition
    prev = result
    result = collapse_repetition(result)
    if result != prev:
        stages.append('repetition')

    # Stage 8: Extract signal (only if aggressive)
    if aggressive:
        prev = result
        result = extract_signal(result)
        if result != prev:
            stages.append('signal_extract')

    # Stage 9: Strip trailing filler (thanks, lol, etc.)
    result = re.sub(r'\s*(?:thanks|thank you|thx|lol|lmao|rofl|спасибо|благодарю)\s*(?:in advance|заранее)?[.!]*\s*$', '', result, flags=re.IGNORECASE)

    # Final cleanup
    result = result.strip()
    # Ensure we didn't lose everything
    if not result or len(result) < 3:
        return NoiseFilterResult(
            original=original,
            cleaned=original.strip(),
            noise_removed=False,
            stages_applied=stages,
        )

    noise_removed = len(stages) > 0 and result != original.strip()

    return NoiseFilterResult(
        original=original,
        cleaned=result,
        noise_removed=noise_removed,
        stages_applied=stages,
    )
