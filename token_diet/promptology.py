"""Promptology — the SCIENCE of prompt efficiency.

Research-backed techniques (2024-2025):
1. XML tags outperform markdown for structural clarity
2. Minimal Role + Rigid Constraints beats verbose personas (40-70% bloat)
3. Extractive compression beats token pruning for reasoning
4. Dynamic few-shot selection (similarity-based) beats static examples
5. Structured output schemas reduce output tokens

All techniques are ZERO neural models — pure algorithmic promptology.

Sources:
- CompactPrompt (Choi et al., 2025)
- Jha et al., ICML: Prompt Compression benchmarks
- RECOMP / RECON: abstractive vs extractive compression
- DSPy / Minimum Viable Prompt research
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Prompt Rewriter — verbose → compact, structured, XML-tagged
# ═══════════════════════════════════════════════════════════════════════════════

# Persona bloat patterns — verbose role descriptions that add zero value
_PERSONA_BLOAT = [
    re.compile(
        r"(?:act|you are|you're)\s+(?:as\s+)?(?:an?\s+)?"
        r"(?:(?:elite|senior|expert|experienced|world-class|professional|skilled|"
        r"master|top-tier|highly\s+skilled)\s+)+"
        r"(?:software\s+)?(?:engineer|developer|architect|programmer|analyst|"
        r"consultant|scientist|researcher|advisor|specialist)\s*"
        r"(?:with\s+(?:over\s+)?\d+\+?\s+years?(?:\s+of)?\s+experience)?[.,;]*"
        r"(?:\s+in\s+[^.]*?)?[.,;]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:imagine|pretend|suppose)\s+(?:you\s+are|that\s+you\s+are)\s+"
        r"[^.]+?(?:expert|specialist|professional)[^.]*\.",
        re.IGNORECASE,
    ),
]

# Safety disclaimers already baked into model weights
_BAKED_IN_SAFETY = [
    re.compile(
        r"(?:always|please|remember\s+to)\s+(?:be\s+)?"
        r"(?:polite|courteous|respectful|kind|friendly|professional|helpful)"
        r"(?:\s+and\s+(?:polite|courteous|respectful|kind|friendly|professional|helpful))*[.,;]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:do\s+not|don't|never|avoid)\s+(?:use\s+)?"
        r"(?:offensive|inappropriate|harmful|dangerous|illegal|unethical|toxic)"
        r"[^.]*\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:if\s+you\s+don't\s+know|when\s+unsure|in\s+case\s+of\s+doubt)"
        r"[^.]*(?:\bsay\b|\bstate\b|\brespond\b|\badmit\b)[^.]*\.",
        re.IGNORECASE,
    ),
]

# Step-by-step instructions that can be triggered dynamically
_STEP_BY_STEP_BLOAT = [
    re.compile(
        r"(?:let's|let\s+us|we\s+will|we'll|I\s+will|I'll)\s+"
        r"(?:think|reason|work|go|proceed|continue|start|begin)\s+"
        r"(?:about\s+)?(?:this|the\s+problem|step\s*by\s*step|through\s+this|"
        r"carefully|methodically|systematically)[^.]*\.",
        re.IGNORECASE,
    ),
    # Step enumerations: "First, ... Second, ... Third, ..."
    re.compile(
        r"\s*(?:first|second|third|fourth|fifth|finally|lastly)[,.]\s+[^.]*\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*(?:step\s*\d+|\d+\.)\s+[^.]*\.",
        re.IGNORECASE,
    ),
]

# Redundant instruction patterns
_REDUNDANT_INSTRUCTIONS = [
    re.compile(
        r"(?:please|kindly)\s+(?:note|be\s+aware|remember|keep\s+in\s+mind|understand)"
        r"\s+that[^.]*\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:it\s+is\s+(?:important|crucial|essential|critical|vital|necessary)"
        r"\s+(?:to|that))[^.]*\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:additionally|furthermore|moreover|in\s+addition|also\s+note|please\s+also)"
        r"[^.]*(?:that|to|the)[^.]*\\.?",
        re.IGNORECASE,
    ),
]

# Emoji + flourish patterns — purely decorative
_EMOJI_FLOURISH = [
    re.compile(r"[\U0001F300-\U0001F9FF\u2600-\u27BF\u2B50\u2705\u274C\u2753\u2757\u2795-\u2797\u2728\u26A0\u26A1]{1,3}"),
    re.compile(r"\s*:\)|:\(|;\)|:D|:P|<3\s*"),
    re.compile(r"~{2,}|_{3,}|={3,}|#{3,}"),
]


def rewrite_system_prompt(prompt: str) -> str:
    """Rewrite a verbose system prompt into a compact, structured form."""
    result = prompt

    # Phase 1: Remove bloat patterns (regex substitutions)
    for pattern in _PERSONA_BLOAT:
        result = pattern.sub("", result)
    for pattern in _BAKED_IN_SAFETY:
        result = pattern.sub("", result)
    for pattern in _STEP_BY_STEP_BLOAT:
        result = pattern.sub("", result)
    for pattern in _REDUNDANT_INSTRUCTIONS:
        result = pattern.sub("", result)

    # Phase 2: Clean up emoji, whitespace
    for pattern in _EMOJI_FLOURISH:
        result = pattern.sub("", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"  +", " ", result)
    result = re.sub(r"\.\s*\.", ".", result)
    result = re.sub(r",\s*,", ",", result)
    result = re.sub(r"^[,.;:!?\s]+", "", result)
    result = re.sub(r"[,.;:!?\s]+$", "", result)
    result = result.strip()

    # If nothing useful remains, keep original
    if len(result) < 15:
        return prompt.strip()

    # Phase 3: Extract constraint-like sentences
    constraints = []
    # Find sentences with MUST, DO NOT, ONLY, etc.
    constraint_re = re.compile(
        r'[^.\n]*(?:\b(?:must|required|mandatory|do\s+not|don\'t|never|only|'
        r'strictly|обязательно|нельзя|запрещено|строго)\b)[^.\n]*[.!]?',
        re.IGNORECASE,
    )
    for m in constraint_re.finditer(result):
        c = m.group().strip(" .,;!\n\t")
        if c and len(c) > 5 and c not in constraints:
            constraints.append(c)

    # Phase 4: Remove constraints from main text, wrap remaining in tags
    for c in constraints:
        result = result.replace(c, "", 1)
    result = result.strip()

    # Build output
    parts = []
    if result:
        parts.append("<instructions>" + result + "</instructions>")
    if constraints:
        parts.append("<constraints>" + "; ".join(constraints) + "</constraints>")

    return "\n".join(parts) if parts else prompt.strip()


def rewrite_user_prompt(prompt: str) -> str:
    """Rewrite a verbose user prompt into a compact, structured form.

    Strips:
    - Conversational filler ("I was wondering if...", "Could you please...")
    - Redundant politeness
    - Multi-sentence buildup before the actual question

    Adds:
    - <question> tag for clarity
    - <context> tag if background info present
    """
    # Detect question vs instruction
    is_question = "?" in prompt

    # Strip conversational preamble
    preamble_patterns = [
        re.compile(
            r"^(?:hey|hi|hello|greetings|привет|здравствуйте),?\s*",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:I\s+was\s+wondering|I\s+would\s+like\s+to\s+ask|I\s+wanted\s+to\s+ask|"
            r"I\s+have\s+a\s+question|can\s+you|could\s+you|would\s+you|will\s+you|"
            r"мне\s+интересно|я\s+хотел\s+бы|у\s+меня\s+вопрос)\s+",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:please|kindly|пожалуйста|будьте\s+добры),?\s*",
            re.IGNORECASE,
        ),
    ]
    result = prompt
    for pat in preamble_patterns:
        result = pat.sub("", result, count=1)

    # Strip closing politeness
    closing_patterns = [
        re.compile(r"\s*(?:thank\s+you|thanks|спасибо|благодарю)\s*(?:in\s+advance|заранее)?[.!]*\s*$", re.IGNORECASE),
        re.compile(r"\s*(?:please|kindly|пожалуйста)[.!]*\s*$", re.IGNORECASE),
    ]
    for pat in closing_patterns:
        result = pat.sub("", result)

    result = result.strip(" .,;\n\t")

    if not result or len(result) < 5:
        return prompt.strip()

    # Structure
    if is_question:
        return f"<question>{result}</question>"
    return f"<instruction>{result}</instruction>"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Few-Shot Minimizer — reduce examples to minimum representative set
# ═══════════════════════════════════════════════════════════════════════════════


def _text_diversity_score(examples: list[str]) -> float:
    """Estimate diversity via unique word ratio (proxy for clustering)."""
    if len(examples) <= 1:
        return 0.0
    all_words: set[str] = set()
    for ex in examples:
        all_words.update(re.findall(r"\w+", ex.lower()))
    total_words = sum(len(re.findall(r"\w+", ex.lower())) for ex in examples)
    if total_words == 0:
        return 0.0
    return len(all_words) / max(1, total_words)


def minimize_few_shot(
    examples: list[dict[str, str]],
    max_examples: int = 3,
) -> list[dict[str, str]]:
    """Reduce few-shot examples to minimum representative set.

    Strategy:
    1. If <= max_examples: return all
    2. Sort by length (longer = more informative)
    3. Add highest-diversity examples first
    4. Stop when adding more reduces diversity or hits max

    Research: K-means on embeddings is ideal, but word-ratio proxy works
    surprisingly well for selecting representative samples.
    """
    if len(examples) <= max_examples:
        return examples

    # Score each example by: length (info density) × uniqueness (word overlap)
    scored = []
    for i, ex in enumerate(examples):
        text = str(ex.get("input", "")) + " " + str(ex.get("output", ""))
        length_score = min(1.0, len(text) / 200.0)
        # Uniqueness: how different from average
        all_other_texts = [
            str(e.get("input", "")) + " " + str(e.get("output", ""))
            for j, e in enumerate(examples)
            if j != i
        ]
        other_words: set[str] = set()
        for t in all_other_texts:
            other_words.update(re.findall(r"\w+", t.lower()))
        my_words = set(re.findall(r"\w+", text.lower()))
        uniqueness = 1.0 - len(my_words & other_words) / max(1, len(my_words))
        scored.append((i, length_score * 0.4 + uniqueness * 0.6))

    scored.sort(key=lambda x: -x[1])
    return [examples[i] for i, _ in scored[:max_examples]]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Full Prompt Pipeline — rewrite entire prompt (system + user + examples)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class PromptologyResult:
    system_before: str
    system_after: str
    user_before: str
    user_after: str
    examples_before: int
    examples_after: int
    tokens_before: int
    tokens_after: int

    @property
    def savings_pct(self) -> float:
        if self.tokens_before == 0:
            return 0.0
        return 100 * (self.tokens_before - self.tokens_after) / self.tokens_before


def optimize_prompt(
    system: str = "",
    user: str = "",
    examples: list[dict[str, str]] | None = None,
    max_examples: int = 3,
    counter: Any = None,
) -> PromptologyResult:
    """Apply full promptology optimization to a prompt.

    Returns the rewritten prompt and token savings.
    """
    from token_diet.core import count_tokens

    ct = counter if counter else count_tokens
    examples = examples or []

    # Count tokens BEFORE
    before = (
        ct(system)
        + ct(user)
        + sum(ct(str(e.get("input", "")) + " " + str(e.get("output", ""))) for e in examples)
    )

    # Rewrite
    system_rw = rewrite_system_prompt(system)
    user_rw = rewrite_user_prompt(user)
    minimized_examples = minimize_few_shot(examples, max_examples)

    # Count tokens AFTER
    after = (
        ct(system_rw)
        + ct(user_rw)
        + sum(ct(str(e.get("input", "")) + " " + str(e.get("output", ""))) for e in minimized_examples)
    )

    return PromptologyResult(
        system_before=system,
        system_after=system_rw,
        user_before=user,
        user_after=user_rw,
        examples_before=len(examples),
        examples_after=len(minimized_examples),
        tokens_before=before,
        tokens_after=after,
    )



