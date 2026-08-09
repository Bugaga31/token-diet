"""Promptology — the SCIENCE of prompt efficiency.

Research-backed techniques (2024-2026):
1. XML tags > Markdown for Claude; Markdown > XML for GPT/Gemini
2. Minimal Role + Rigid Constraints beats verbose personas (40-70% bloat)
3. Positive framing > Negation ("only use real data" vs "don't use mocks")
4. U-shaped attention: static at beginning, query at end
5. 12-block semantic compilation for max IQ/token
6. Cross-model prompt transfer requires adapter layers
7. Token Economics: every extra token degrades reasoning (O(n^2) attention)
8. Plan in English → Output in native language (best for Russian users)

Sources:
- Prompt Engineering Patterns (GitHub, 15K+ stars)
- Anthropic Prompt Engineering Guide
- Liu et al. "Lost in the Middle" (U-shaped attention)
- Zhejiang Univ. "Token Economics for LLM Agents" (2026)
- Yandex Alice PromptHub + prompt1.ru (Russian prompt marketplace)
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
# 2b. POSITIVE REFRAMER — negation → positive framing
# ═══════════════════════════════════════════════════════════════════════════════

# Negative → Positive rewrites (Anthropic research: negation triggers "Pink Elephant")
_NEGATIVE_TO_POSITIVE = [
    (re.compile(r"do\s+not\s+use\s+mock\s+data", re.IGNORECASE), "only use real data"),
    (re.compile(r"don't\s+use\s+mock", re.IGNORECASE), "use real"),
    (re.compile(r"do\s+not\s+invent|don't\s+make\s+up", re.IGNORECASE), "use only verified facts"),
    (re.compile(r"do\s+not\s+hallucinate|don't\s+hallucinate", re.IGNORECASE), "be factually accurate"),
    (re.compile(r"never\s+use\s+(\S+\s+){0,3}mock", re.IGNORECASE), "always use real data"),
    (re.compile(r"do\s+not\s+guess", re.IGNORECASE), "state only what you know"),
    (re.compile(r"don't\s+be\s+lazy", re.IGNORECASE), "be thorough"),
    (re.compile(r"do\s+not\s+skip", re.IGNORECASE), "include everything"),
    (re.compile(r"never\s+say\s+(?:sorry|apologize)", re.IGNORECASE), "be direct and factual"),
    (re.compile(r"don't\s+(?:use|write|add|include)\s+(?:unnecessary|redundant|extra|verbose)", re.IGNORECASE), "be concise"),
]


def reframe_positive(text: str) -> str:
    """Rewrite negative instructions as positive ones.

    Negative constraints force the model to process the forbidden concept
    before suppressing it ("Pink Elephant" problem). Positive framing
    directly steers toward the desired behaviour, improving compliance
    and saving tokens.
    """
    result = text
    for pattern, replacement in _NEGATIVE_TO_POSITIVE:
        result = pattern.sub(replacement, result)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2c. CROSS-MODEL ADAPTER — format for specific model families
# ═══════════════════════════════════════════════════════════════════════════════


def adapt_for_model(prompt: str, model: str = "auto") -> str:
    """Adapt prompt formatting for the target model family.

    Claude: XML tags (<instructions>, <constraints>) — best compliance
    GPT/Gemini: Markdown headers — best performance
    Auto: detects from model name
    """
    model_lower = model.lower()

    # Detect model family
    if model == "auto":
        return prompt  # Keep existing XML format (default)

    if "claude" in model_lower or "anthropic" in model_lower:
        # XML is already optimal for Claude — ensure tags are present
        if "<instructions>" not in prompt and "<constraints>" not in prompt:
            return f"<instructions>{prompt}</instructions>"
        return prompt

    if "gpt" in model_lower or "openai" in model_lower or "gemini" in model_lower:
        # Convert XML to Markdown for GPT/Gemini
        result = prompt
        result = re.sub(r"<instructions>(.*?)</instructions>", r"# Instructions\n\1", result, flags=re.DOTALL)
        result = re.sub(r"<constraints>(.*?)</constraints>", r"# Constraints\n\1", result, flags=re.DOTALL)
        result = re.sub(r"<question>(.*?)</question>", r"**Q:** \1", result, flags=re.DOTALL)
        result = re.sub(r"<instruction>(.*?)</instruction>", r"\1", result, flags=re.DOTALL)
        return result

    return prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 2d. ATTENTION-OPTIMAL ORDERING — U-shaped curve placement
# ═══════════════════════════════════════════════════════════════════════════════


def order_for_attention(
    system: str = "",
    context: str = "",
    examples: str = "",
    query: str = "",
) -> str:
    """Arrange prompt sections for U-shaped attention curve.

    Beginning (best recall): system instructions, constraints, tool defs
    Middle (worst recall): large context, documents
    End (best recall): user query, current task

    This ordering maximizes prompt caching (static at start) and ensures
    the most critical information (instructions + query) gets attention.
    """
    parts = []

    # BEGINNING: static, cacheable, most important
    if system:
        parts.append(system)

    # MIDDLE: context, background
    if context:
        parts.append(context)
    if examples:
        parts.append(examples)

    # END: query — most recent, highest attention
    if query:
        parts.append(query)

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# 2e. PLAN-IN-ENGLISH adapter — for Russian (and other non-English) users
# ═══════════════════════════════════════════════════════════════════════════════

_PLAN_IN_ENGLISH_PREAMBLE = (
    "Think and reason internally in English for maximum logical depth. "
    "Output the final answer in {language}."
)


def adapt_for_language(prompt: str, target_language: str = "Russian") -> str:
    """Add plan-in-English instruction for non-English outputs.

    Research shows LLMs reason best in English (majority of training data).
    For complex tasks with non-English output, instructing the model to
    think in English but output in the target language improves accuracy.

    Only applies when the user prompt appears to be in a non-English language.
    """
    # Detect if prompt is in Russian (Cyrillic characters)
    has_cyrillic = bool(re.search(r"[а-яёА-ЯЁ]", prompt))
    if has_cyrillic:
        return _PLAN_IN_ENGLISH_PREAMBLE.format(language=target_language) + "\n\n" + prompt
    return prompt


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
    model: str = "auto",
    target_language: str = "",
) -> PromptologyResult:
    """Apply full promptology optimization: rewrite + reframe + adapt.

    Args:
        system: system prompt
        user: user message
        examples: few-shot examples
        max_examples: cap on examples after minimization
        counter: token counter (default: count_tokens)
        model: target model ("claude", "gpt", "gemini", or "auto")
        target_language: for plan-in-English adapter ("Russian", etc.)

    Returns the rewritten prompt and token savings.
    """
    from token_diet.core import count_tokens

    ct = counter if counter else count_tokens
    examples = examples or []

    # Phase 1: Rewrite (strip bloat, structure)
    system_rw = rewrite_system_prompt(system)
    user_rw = rewrite_user_prompt(user)

    # Phase 2: Positive reframing
    system_rw = reframe_positive(system_rw)
    user_rw = reframe_positive(user_rw)

    # Phase 3: Language adaptation (plan-in-English for Russian users)
    if target_language:
        user_rw = adapt_for_language(user_rw, target_language)

    # Phase 4: Few-shot minimization
    minimized_examples = minimize_few_shot(examples, max_examples)

    # Phase 5: Cross-model format adaptation
    system_rw = adapt_for_model(system_rw, model)
    user_rw = adapt_for_model(user_rw, model)

    # Phase 6: Attention-optimal ordering
    examples_text = "\n\n".join(
        f"Example:\nInput: {e.get('input','')}\nOutput: {e.get('output','')}"
        for e in minimized_examples
    )
    full_prompt = order_for_attention(
        system=system_rw,
        examples=examples_text if examples_text else "",
        query=user_rw,
    )

    # Count tokens
    before = (
        ct(system) + ct(user) +
        sum(ct(str(e.get("input", "")) + " " + str(e.get("output", ""))) for e in examples)
    )
    after = ct(full_prompt)

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


def compile_prompt(
    system: str = "",
    user: str = "",
    context: str = "",
    examples: list[dict[str, str]] | None = None,
    model: str = "auto",
    target_language: str = "",
) -> str:
    """Full prompt compilation pipeline — everything optimized.

    Applies ALL promptology techniques and returns the final prompt string.
    This is the function you call before sending to the LLM API.

    Usage:
        prompt = compile_prompt(
            system="You are a coding assistant.",
            user="Write a Fibonacci function",
            model="claude",
        )
        response = llm.send(prompt)
    """
    result = optimize_prompt(
        system=system, user=user, examples=examples,
        model=model, target_language=target_language,
    )

    # Use attention-optimal ordering with all components
    examples_text = ""
    if examples:
        minimized = minimize_few_shot(examples)
        examples_text = "\n\n".join(
            f"Example:\nInput: {e.get('input','')}\nOutput: {e.get('output','')}"
            for e in minimized
        )

    return order_for_attention(
        system=result.system_after,
        context=context,
        examples=examples_text if examples_text else "",
        query=result.user_after,
    )



