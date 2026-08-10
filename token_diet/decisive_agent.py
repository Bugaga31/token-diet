"""Decisive Agent — Action-first pipeline: stop thinking, start doing.

УРОК: ассистент, который говорит «Let me think about this...» вместо того
чтобы ДЕЛАТЬ — бесполезен. Этот модуль устраняет deliberation и переходит
к действию немедленно.

Три режима:
1. INSTANT: запрос очевиден → ответ/действие сразу (0 токенов на размышления)
2. FAST: нужен 1 шаг reasoning → короткий план + действие
3. DEEP: сложная задача → минимальное reasoning + действие

Ключевое правило: reasoning токены НЕ должны превышать action токены.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


# ═══════════════════════════════════════════════════════════════════════════════
# Anti-deliberation patterns — strip these from output
# ═══════════════════════════════════════════════════════════════════════════════

_DELIBERATION_PATTERNS = [
    # "Let me think..." — just don't
    re.compile(r"^(?:Let me|I need to|I should|I will|I'm going to)\s+(?:think|consider|analyze|process|reason|evaluate|assess)[^.!?]*[.!?]\s*", re.I | re.M),
    # "First, I'll..." — skip to action
    re.compile(r"^(?:First|Initially|To start|To begin|As a first step),?\s+(?:I'?ll|I will|let me|I need to)[^.!?]*[.!?]\s*", re.I | re.M),
    # "Let me break this down..." — just break it down silently
    re.compile(r"^(?:Let me|I'll|I will)\s+(?:break|split|divide|decompose|unpack)[^.!?]*[.!?]\s*", re.I | re.M),
    # "I understand that..." — unnecessary acknowledgment
    re.compile(r"^(?:I understand|I see|I get it|Got it|Understood|Alright|OK|Okay)[,.!]?\s*(?:that|what)?[^.!?]*[.!?]\s*", re.I | re.M),
    # "Based on my analysis..." — just give the result
    re.compile(r"^(?:Based on|According to|After|Following|Given)\s+(?:my|the|our|this)\s+(?:analysis|assessment|evaluation|review|examination)[^.!?]*[.!?]\s*", re.I | re.M),
    # Russian deliberation
    re.compile(r"^(?:Давайте?|Я|Нужно|Надо|Стоит|Сначала)\s+(?:подума[юе]|разбер[уё]|проанализиру|рассмотр|оцен[юи]|выясн[юи])[^.!?]*[.!?]\s*", re.I | re.M),
    # "Хорошо, давайте..." — filler
    re.compile(r"^(?:Хорошо|Ладно|Окей|Понял|Принял|Так)[,.!]?\s*(?:давай|давайте|сейчас|тогда)[^.!?]*[.!?]\s*", re.I | re.M),
]

# Replacements that save tokens AND increase decisiveness
_ACTION_PREFIXES: dict[str, str] = {
    "CODE": "",  # No prefix needed, just output code
    "ACTION": "",  # Just do it
    "QUESTION": "",  # Direct answer
    "DEBUG": "Diagnosis: ",  # One-word prefix → then facts
}


# ═══════════════════════════════════════════════════════════════════════════════
# Decision modes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Decision:
    """What to do, right now."""

    mode: str  # INSTANT | FAST | DEEP
    intent: str
    action: str  # what to do
    max_reasoning_tokens: int  # cap on thinking budget
    max_output_tokens: int  # cap on total output
    skip_deliberation: bool  # strip "Let me think..." from output?
    direct_answer: str | None = None  # if INSTANT and answerable without LLM

    @property
    def reasoning_budget_pct(self) -> float:
        """What % of output can be reasoning?"""
        if self.max_output_tokens == 0:
            return 0.0
        return self.max_reasoning_tokens / self.max_output_tokens


class DecisiveAgent:
    """Stop deliberating. Start doing.

    Usage:
        agent = DecisiveAgent()
        decision = agent.decide("напиши функцию фибоначчи")
        # → Decision(mode="INSTANT", action="write_code", max_reasoning_tokens=0, ...)
    """

    def __init__(self, max_total_tokens: int = 4000):
        self.max_total_tokens = max_total_tokens

    def decide(
        self,
        text: str,
        *,
        intent: str = "CHAT",
        urgency: float = 0.0,
        complexity: float = 0.0,
    ) -> Decision:
        """Make an instant decision on what to do.

        Args:
            text: user's message
            intent: pre-classified intent (from RapidContext)
            urgency: 0..1 how fast user wants result
            complexity: 0..1 estimated difficulty

        Returns:
            Decision with action plan
        """
        # ── INSTANT mode: trivial, urgent, or obvious ──
        if (
            intent == "QUICK" or
            urgency > 0.7 or
            (complexity < 0.1 and len(text.split()) <= 5)
        ):
            return Decision(
                mode="INSTANT",
                intent=intent,
                action=self._action_for_intent(intent),
                max_reasoning_tokens=0,
                max_output_tokens=min(150, self.max_total_tokens),
                skip_deliberation=True,
            )

        # ── FAST mode: moderate complexity, known intent ──
        if complexity < 0.4 and intent in ("CODE", "ACTION", "QUESTION", "DEBUG"):
            return Decision(
                mode="FAST",
                intent=intent,
                action=self._action_for_intent(intent),
                max_reasoning_tokens=min(80, self.max_total_tokens // 10),
                max_output_tokens=min(500, self.max_total_tokens),
                skip_deliberation=True,
            )

        # ── DEEP mode: complex, research, investment ──
        return Decision(
            mode="DEEP",
            intent=intent,
            action=self._action_for_intent(intent),
            max_reasoning_tokens=min(300, self.max_total_tokens // 5),
            max_output_tokens=self.max_total_tokens,
            skip_deliberation=True,  # Even deep: strip filler, keep substance
        )

    def _action_for_intent(self, intent: str) -> str:
        return {
            "CODE": "write_code",
            "ACTION": "execute",
            "QUESTION": "answer",
            "RESEARCH": "research",
            "INVESTMENT": "analyze_market",
            "DEBUG": "diagnose",
            "QUICK": "answer_briefly",
            "CHAT": "respond",
        }.get(intent, "respond")

    def strip_deliberation(self, text: str) -> str:
        """Remove deliberation filler from output.

        Transforms:
            "Let me think about this. First, I'll analyze the code.
             The bug is on line 5. Let me fix that."
        Into:
            "The bug is on line 5. Fix applied."
        """
        result = text
        for pattern in _DELIBERATION_PATTERNS:
            result = pattern.sub("", result)

        # Clean up
        result = re.sub(r"\n{3,}", "\n\n", result)
        result = re.sub(r"  +", " ", result)
        result = result.strip()

        # If we stripped everything, keep original
        if not result or len(result) < 10:
            return text.strip()

        return result

    def inject_action_bias(self, system_prompt: str) -> str:
        """Add action bias to system prompt — model will default to DOING.

        Adds concise instruction at the END (U-shaped attention → highest impact).
        """
        bias = (
            "\n\nPRIORITY: Execute immediately. "
            "Skip introductions and explanations. "
            "Default to action. "
            "Output the result directly."
        )
        if "Execute immediately" not in system_prompt:
            return system_prompt + bias
        return system_prompt

    def optimize_output(
        self, text: str, decision: Decision
    ) -> tuple[str, int, int]:
        """Apply decision to output: strip deliberation, trim to budget.

        Returns (optimized_text, tokens_before, tokens_after).
        """
        try:
            from .core import count_tokens
        except ImportError:
            from core import count_tokens  # type: ignore

        before = count_tokens(text)

        result = text
        if decision.skip_deliberation:
            result = self.strip_deliberation(result)

        # Trim to budget if needed
        if count_tokens(result) > decision.max_output_tokens:
            # Keep first N tokens worth of text
            words = result.split()
            # Approximate: 1 token ≈ 0.75 words (English)
            max_words = int(decision.max_output_tokens * 0.75)
            result = " ".join(words[:max_words])

        after = count_tokens(result)
        return result, before, after


# ═══════════════════════════════════════════════════════════════════════════════
# One-liners
# ═══════════════════════════════════════════════════════════════════════════════

_global_agent: DecisiveAgent | None = None


def decide(text: str, **kwargs: Any) -> Decision:
    """One-liner: decide what to do."""
    global _global_agent
    if _global_agent is None:
        _global_agent = DecisiveAgent()
    return _global_agent.decide(text, **kwargs)


def strip_fluff(text: str) -> str:
    """One-liner: remove deliberation filler from any text."""
    global _global_agent
    if _global_agent is None:
        _global_agent = DecisiveAgent()
    return _global_agent.strip_deliberation(text)
