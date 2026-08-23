"""Rapid Context Classifier — мгновенное понимание намерения без нейронок.

УРОК: медленный ассистент = бесполезный ассистент.
Решение: за 1 проход по тексту определяем тип задачи, сложность,
и какой модуль token-diet применить. Никаких API-вызовов. <1ms.

Классифицирует в 8 категорий за один регекс-проход:
- CODE: написать/исправить код
- QUESTION: вопрос на знание
- ACTION: сделать что-то (файл, команду, запустить)
- RESEARCH: исследовать/проанализировать/найти
- INVESTMENT: про инвестиции/рынок/акции
- DEBUG: ошибка/не работает/почини
- QUICK: односложный запрос (да/нет/ок)
- CHAT: просто разговор
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# Signal words for each intent type
# ═══════════════════════════════════════════════════════════════════════════════

_CODE_SIGNALS = re.compile(
    r'\b(?:write|create|implement|code|function|class|script|program|'
    r'build|develop|refactor|rewrite|fix\s+(?:the\s+)?code|patch|'
    r'debug|add\s+(?:a\s+)?(?:function|method|class|endpoint|route|api)|'
    r'напиши|создай|функци[юя]|код|скрипт|программ|исправь\s+код|'
    r'добавь\s+(?:функци|метод|класс)|почини|запрограммируй|'
    r'реализуй|разработай)\b',
    re.IGNORECASE,
)

_QUESTION_SIGNALS = re.compile(
    r'\b(?:what|who|where|when|why|how|explain|describe|tell\s+me|'
    r'что|кто|где|когда|почему|зачем|как|объясни|расскажи|опиши|'
    r'что такое|какой|какая|какие|сколько)\b',
    re.IGNORECASE,
)

_ACTION_SIGNALS = re.compile(
    r'\b(?:do|run|execute|start|stop|install|deploy|push|commit|'
    r'configure|setup|set\s+up|download|upload|copy|move|delete|remove|'
    r'create\s+(?:a\s+)?(?:file|dir|folder|repo)|'
    r'сделай|запусти|установи|настрой|скачай|загрузи|скопируй|'
    r'перемести|удали|выполни|открой|закрой|перезапусти|'
    r'добавь|убери|поменяй|измени)\b',
    re.IGNORECASE,
)

_RESEARCH_SIGNALS = re.compile(
    r'\b(?:research|analyze|investigate|study|review|audit|compare|'
    r'evaluate|assess|explore|scan|search|find|look\s+(?:for|up|into)|'
    r'read|check|verify|validate|examine|'
    r'исследуй|проанализируй|изучи|проверь|сравни|оцени|найди|'
    r'поищи|посмотри|почитай|разберись|выясни|узнай|проверь)\b',
    re.IGNORECASE,
)

_INVESTMENT_SIGNALS = re.compile(
    r'\b(?:stock|bond|etf|fund|market|trading|invest|portfolio|'
    r'dividend|yield|coupon|share|ticker|exchange|MOEX|NASDAQ|NYSE|'
    r'bull|bear|long|short|position|option|futures|'
    r'акци[ия]|облигаци[ия]|рынок|биржа|инвестици|дивиденд|'
    r'портфел|тикер|котировк|доходност|купон|брокер|'
    r'сбер|газпром|лукойл|яндекс|полюс|русал|норникель|'
    r'рост|падение|график|свеч[аи]|тренд)\b',
    re.IGNORECASE,
)

_DEBUG_SIGNALS = re.compile(
    r'\b(?:error|bug|fail|crash|broken|doesn\'t\s+work|not\s+working|'
    r'issue|problem|wrong|incorrect|unexpected|exception|traceback|'
    r'ошибка|не\s+работает|сломалось|глюк|баг|не\s+получается|'
    r'не\s+выходит|не\s+запускается|вылетает|падает|краш)\b',
    re.IGNORECASE,
)

# Urgency signals — user wants result NOW
_URGENCY_SIGNALS = re.compile(
    r'\b(?:quick|fast|ASAP|urgent|immediately|now|right\s+now|'
    r'hurry|emergency|critical|'
    r'быстро|срочно|немедленно|прямо\s+сейчас|сию\s+минуту|'
    r'не\s+медли|давай|скорее|поторопись|живо)\b',
    re.IGNORECASE,
)

# Triviality signals — don't overthink this
_TRIVIAL_SIGNALS = re.compile(
    r'^(?:yes|no|ok|k|okay|yep|nope|sure|thanks|thx|спасибо|'
    r'да|нет|ок|ага|угу|ладно|хорошо|понял|принял|отбой)$',
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Intent types
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ContextClass:
    """What the user actually wants RIGHT NOW."""

    intent: str  # CODE | QUESTION | ACTION | RESEARCH | INVESTMENT | DEBUG | QUICK | CHAT
    urgency: float  # 0..1 — how fast they want result
    complexity: float  # 0..1 — estimated task difficulty
    needs_reasoning: bool  # needs chain-of-thought?
    needs_tools: bool  # needs external tools (files, shell, browser)?
    should_compress: bool  # should we run token-diet compression?
    confidence: float  # how sure are we about this classification?
    matching_signals: list[str] = field(default_factory=list)

    @property
    def is_trivial(self) -> bool:
        """Skip all processing — just answer."""
        return self.intent == "QUICK" or self.complexity < 0.05

    @property
    def is_action(self) -> bool:
        """User wants us to DO something."""
        return self.intent in ("CODE", "ACTION", "DEBUG")

    @property
    def is_think(self) -> bool:
        """User wants us to THINK about something."""
        return self.intent in ("QUESTION", "RESEARCH", "INVESTMENT")


# ═══════════════════════════════════════════════════════════════════════════════
# Rapid Classifier — one pass, no neural models
# ═══════════════════════════════════════════════════════════════════════════════

class RapidContext:
    """Instant intent classifier — the FAST path.

    Usage:
        ctx = RapidContext()
        classification = ctx.classify("напиши функцию фибоначчи")
        # → ContextClass(intent="CODE", urgency=0.3, complexity=0.2, ...)
    """

    def __init__(self):
        # Intent detectors ordered by specificity (most specific first)
        self.detectors: list[tuple[str, re.Pattern, float]] = [
            ("INVESTMENT", _INVESTMENT_SIGNALS, 0.85),
            ("DEBUG", _DEBUG_SIGNALS, 0.80),
            ("CODE", _CODE_SIGNALS, 0.75),
            ("ACTION", _ACTION_SIGNALS, 0.70),
            ("RESEARCH", _RESEARCH_SIGNALS, 0.65),
            ("QUESTION", _QUESTION_SIGNALS, 0.60),
        ]

    def classify(self, text: str, *, context_hints: dict[str, Any] | None = None) -> ContextClass:
        """Classify user intent in one pass.

        Args:
            text: user's message
            context_hints: optional hints from surrounding context
                (e.g., {"has_code_blocks": True, "prev_intent": "CODE"})

        Returns:
            ContextClass with intent, urgency, complexity, etc.
        """
        if not text or not text.strip():
            return ContextClass(
                intent="CHAT", urgency=0.0, complexity=0.0,
                needs_reasoning=False, needs_tools=False,
                should_compress=False, confidence=1.0,
            )

        text = text.strip()
        text_lower = text.lower()

        # ── Step 0: Trivial? ──
        if _TRIVIAL_SIGNALS.match(text):
            return ContextClass(
                intent="QUICK", urgency=0.0, complexity=0.0,
                needs_reasoning=False, needs_tools=False,
                should_compress=False, confidence=0.95,
                matching_signals=["trivial_pattern"],
            )

        # ── Step 1: Count signals for each intent ──
        scores: dict[str, float] = {}
        all_signals: dict[str, list[str]] = {}

        for intent, pattern, base_weight in self.detectors:
            matches = pattern.findall(text)
            if matches:
                # Score = base_weight + 0.05 per extra match (cap at 0.95)
                score = min(0.95, base_weight + 0.05 * (len(matches) - 1))
                scores[intent] = score
                all_signals[intent] = [m if isinstance(m, str) else str(m) for m in matches[:5]]

        # ── Step 2: Boost from context hints ──
        if context_hints:
            prev = context_hints.get("prev_intent", "")
            if prev in scores:
                scores[prev] = min(0.95, scores[prev] + 0.1)  # continuity bonus
            if context_hints.get("has_code_blocks"):
                scores["CODE"] = scores.get("CODE", 0.0) + 0.15
            if context_hints.get("has_errors"):
                scores["DEBUG"] = scores.get("DEBUG", 0.0) + 0.15

        # ── Step 3: Pick winner ──
        if not scores:
            # Has question mark → QUESTION, otherwise CHAT
            if "?" in text:
                return ContextClass(
                    intent="QUESTION", urgency=0.2, complexity=0.15,
                    needs_reasoning=False, needs_tools=False,
                    should_compress=False, confidence=0.5,
                    matching_signals=["has_question_mark"],
                )
            return ContextClass(
                intent="CHAT", urgency=0.1, complexity=0.05,
                needs_reasoning=False, needs_tools=False,
                should_compress=False, confidence=0.4,
                matching_signals=["fallback"],
            )

        best_intent = max(scores, key=lambda k: scores[k])
        confidence = scores[best_intent]

        # ── Step 4: Urgency ──
        urgency_matches = _URGENCY_SIGNALS.findall(text)
        urgency = min(1.0, 0.3 + 0.2 * len(urgency_matches))

        # ── Step 5: Complexity ──
        complexity = self._estimate_complexity(text, best_intent)

        # ── Step 6: Needs reasoning? ──
        needs_reasoning = (
            best_intent in ("QUESTION", "RESEARCH", "INVESTMENT", "DEBUG") and
            complexity > 0.2
        )

        # ── Step 7: Needs tools? ──
        needs_tools = best_intent in ("CODE", "ACTION", "DEBUG")

        # ── Step 8: Should we compress? ──
        # Compress if: long text (>200 chars) AND not trivial AND not urgent
        should_compress = (
            len(text) > 200 and
            complexity > 0.1 and
            urgency < 0.7
        )

        return ContextClass(
            intent=best_intent,
            urgency=urgency,
            complexity=complexity,
            needs_reasoning=needs_reasoning,
            needs_tools=needs_tools,
            should_compress=should_compress,
            confidence=confidence,
            matching_signals=all_signals.get(best_intent, []),
        )

    def _estimate_complexity(self, text: str, intent: str) -> float:
        """Fast complexity estimate based on text features."""
        score = 0.0

        # Length
        words = len(text.split())
        if words <= 3:
            score += 0.0
        elif words <= 10:
            score += 0.1
        elif words <= 30:
            score += 0.2
        elif words <= 100:
            score += 0.35
        else:
            score += 0.5

        # Multiple sentences → more complex
        sentences = len(re.findall(r'[.!?]+', text))
        if sentences >= 3:
            score += 0.15
        elif sentences >= 2:
            score += 0.05

        # Code indicators
        if re.search(r'[{}();]|\bdef\b|\bclass\b|\bimport\b|\bfunction\b', text):
            score += 0.15

        # Multiple topics
        if re.search(r'\band\b.*\band\b|\bтакже\b.*\bи\b', text, re.IGNORECASE):
            score += 0.1

        # Numbers → data analysis
        if len(re.findall(r'\d+', text)) >= 3:
            score += 0.1

        # Intent-specific adjustments
        if intent == "CODE":
            score += 0.1  # code is inherently medium-complexity
        elif intent == "QUICK":
            score = 0.0  # trivial
        elif intent == "INVESTMENT":
            score += 0.1  # investment needs data analysis

        return min(1.0, score)


# ═══════════════════════════════════════════════════════════════════════════════
# Quick decision helper — "what should I do RIGHT NOW?"
# ═══════════════════════════════════════════════════════════════════════════════

def decide_action(ctx: ContextClass) -> dict[str, Any]:
    """Given context class, return the optimal action plan.

    This is the "stop overthinking and DO" function.
    """
    plan = {
        "intent": ctx.intent,
        "skip_reasoning": not ctx.needs_reasoning,
        "skip_compression": not ctx.should_compress,
        "direct_action": ctx.is_action,
        "mode": "fast" if ctx.urgency > 0.5 else "normal",
    }

    # What to do based on intent
    if ctx.intent == "QUICK":
        plan["action"] = "answer_directly"
        plan["max_tokens"] = 50
    elif ctx.intent == "CODE":
        plan["action"] = "write_code"
        plan["use_tools"] = ["write_file", "str_replace"]
    elif ctx.intent == "ACTION":
        plan["action"] = "execute"
        plan["use_tools"] = ["basher", "write_file"]
    elif ctx.intent == "DEBUG":
        plan["action"] = "diagnose"
        plan["use_tools"] = ["basher", "read_files"]
    elif ctx.intent == "QUESTION":
        plan["action"] = "answer"
        plan["max_tokens"] = 300
    elif ctx.intent == "RESEARCH":
        plan["action"] = "research"
        plan["use_tools"] = ["researcher_web", "read_files"]
    elif ctx.intent == "INVESTMENT":
        plan["action"] = "analyze_market"
        plan["use_tools"] = ["researcher_web", "read_files"]
    else:
        plan["action"] = "chat"

    # If urgent: skip everything, direct answer
    if ctx.urgency > 0.7:
        plan["mode"] = "urgent"
        plan["skip_reasoning"] = True
        plan["skip_compression"] = True

    return plan


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: measure how long classification takes
# ═══════════════════════════════════════════════════════════════════════════════

_global_classifier: RapidContext | None = None


def classify(text: str, **kwargs: Any) -> ContextClass:
    """One-liner: classify user intent."""
    global _global_classifier
    if _global_classifier is None:
        _global_classifier = RapidContext()
    return _global_classifier.classify(text, **kwargs)


def quick_action(text: str) -> dict[str, Any]:
    """One-liner: classify AND decide action."""
    ctx = classify(text)
    return decide_action(ctx)
