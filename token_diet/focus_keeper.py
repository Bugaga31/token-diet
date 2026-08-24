"""Focus Keeper — детектор ухода в tangent и возврат к задаче.

УРОК: ассистент потратил 20 итераций на копирование файла через adb input text,
вместо того чтобы найти простой способ. Это tangent — отклонение от цели.

Focus Keeper:
1. Запоминает изначальную цель пользователя
2. Проверяет каждый шаг: приближает ли он к цели?
3. Если обнаружен tangent — возвращает фокус
4. Если глубина tangent > 3 шагов — жёсткий возврат

Внедряется как обёртка вокруг агента или как проверка post-hoc.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════════════════
# Tangent detection patterns
# ═══════════════════════════════════════════════════════════════════════════════

# Words that indicate the model is going off-track
_TANGENT_SIGNALS = [
    # Debugging rabbit holes
    re.compile(r"\b(?:debug|investigate|trace|step\s+through|dive\s+into|dig\s+into)\b", re.I),
    # Environment setup tangents
    re.compile(r"\b(?:install|configure|setup|set\s+up|download|compile|build\s+from)\b", re.I),
    # Tool fixation
    re.compile(r"\b(?:adb|termux|uiautomator|screencap|input\s+text|input\s+tap)\b", re.I),
    # Permission/access spirals
    re.compile(r"\b(?:permission|access\s+denied|run-as|chmod|sudo|root|su\b)\b", re.I),
    # Endless testing loops
    re.compile(r"\b(?:let\s+me\s+test|let\s+me\s+try|one\s+more\s+time|try\s+again|attempt\s+#?\d)\b", re.I),
    # Russian tangent signals
    re.compile(r"\b(?:давай\s+попробуем|ещё\s+раз|давай\s+ещё|попытка|проверим|протестируем)\b", re.I),
]


# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Goal:
    """The user's original goal — what we're trying to achieve."""

    text: str  # original user message
    intent: str  # classified intent
    key_terms: list[str] = field(default_factory=list)  # extracted keywords
    goal_hash: str = ""  # fingerprint for comparison

    def __post_init__(self):
        if not self.key_terms:
            self.key_terms = self._extract_terms()
        if not self.goal_hash:
            self.goal_hash = hashlib.md5(
                " ".join(sorted(self.key_terms)).encode()
            ).hexdigest()[:8]

    def _extract_terms(self) -> list[str]:
        """Extract key nouns and verbs from goal."""
        # Extract meaningful words (nouns, verbs, proper names)
        words = re.findall(r'\b[а-яёa-z]{3,}\b', self.text.lower())
        # Filter out stop words
        stop = {
            'the', 'and', 'for', 'with', 'this', 'that', 'what', 'how',
            'when', 'where', 'which', 'who', 'why', 'can', 'you', 'your',
            'и', 'в', 'на', 'с', 'по', 'для', 'что', 'как', 'это', 'так',
            'мне', 'нужно', 'надо', 'есть', 'ещё', 'уже', 'если', 'чтобы',
            'тогда', 'давай', 'сейчас', 'просто', 'очень', 'может', 'будет',
        }
        return [w for w in words if w not in stop][:10]


@dataclass
class TangentAlert:
    """Detected tangent — we're going off-track."""

    tangent_type: str  # DEBUG_SPIRAL | TOOL_FIXATION | PERMISSION_SPIRAL | SLOW_LOOP
    distance_from_goal: float  # 0..1 — how far from the original goal
    steps_deep: int  # how many steps into the tangent
    current_action: str  # what we're doing now (wrong thing)
    goal_reminder: str  # what we SHOULD be doing
    confidence: float  # how sure we are this is a tangent

    @property
    def is_critical(self) -> bool:
        """Tangent is deep enough to require hard reset."""
        return self.steps_deep >= 3 or self.distance_from_goal > 0.8


class FocusKeeper:
    """Keep the agent focused on the user's actual goal.

    Usage:
        keeper = FocusKeeper()
        keeper.set_goal("напиши функцию фибоначчи")

        # After each action:
        alert = keeper.check("debugging adb input text on phone...")
        if alert:
            print(f"TANGENT: {alert.tangent_type} — returning to: {alert.goal_reminder}")
    """

    def __init__(self, max_tangent_depth: int = 3):
        self.goal: Goal | None = None
        self.max_tangent_depth = max_tangent_depth
        self.action_log: list[str] = []  # history of actions taken
        self.tangent_depth: int = 0  # current tangent depth
        self.alert_count: int = 0  # total alerts raised

    def set_goal(self, text: str, intent: str = "CHAT") -> Goal:
        """Set the current goal. Call this when user sends a new message."""
        self.goal = Goal(text=text, intent=intent)
        self.action_log = []
        self.tangent_depth = 0
        return self.goal

    def record_action(self, action: str) -> None:
        """Record an action taken by the agent."""
        self.action_log.append(action)
        # Keep only last 10 actions
        if len(self.action_log) > 10:
            self.action_log = self.action_log[-10:]

    def check(self, current_action: str) -> TangentAlert | None:
        """Check if current_action is a tangent from the goal.

        Returns TangentAlert if off-track, None if on-track.
        """
        if self.goal is None:
            return None

        self.record_action(current_action)

        # ── Check 1: Does this action match goal intent? ──
        goal_words = set(self.goal.key_terms)
        action_words = set(re.findall(r'\b[а-яёa-z]{3,}\b', current_action.lower()))

        # Also extract English tech terms from action (code, file, function, etc.)
        # These are language-agnostic intent signals
        tech_signals = {
            'code': 'CODE', 'file': 'ACTION', 'function': 'CODE',
            'class': 'CODE', 'script': 'CODE', 'program': 'CODE',
            'write': 'CODE', 'create': 'ACTION', 'run': 'ACTION',
            'debug': 'DEBUG', 'fix': 'DEBUG', 'error': 'DEBUG',
        }
        action_intent_hints = set()
        for word, intent in tech_signals.items():
            if word in action_words:
                action_intent_hints.add(intent)

        # If action intent hints match goal intent → on track
        if self.goal.intent in action_intent_hints:
            self.tangent_depth = max(0, self.tangent_depth - 1)
            return None

        # Overlap between goal and action
        overlap = goal_words & action_words
        relevance = len(overlap) / max(1, len(goal_words))
        distance = 1.0 - relevance

        # ── Check 2: Tangent signals? ──
        tangent_type = None
        for i, pattern in enumerate(_TANGENT_SIGNALS):
            if pattern.search(current_action):
                tangent_types = [
                    "DEBUG_SPIRAL", "ENV_SETUP_TANGENT", "TOOL_FIXATION",
                    "PERMISSION_SPIRAL", "ENDLESS_TESTING",
                ]
                tangent_type = tangent_types[min(i, len(tangent_types) - 1)]
                break

        # ── Check 3: Repeated actions (loop detection) ──
        is_loop = False
        if len(self.action_log) >= 3:
            last_3 = self.action_log[-3:]
            # Check if last 3 actions are similar
            if len(set(last_3)) <= 2:  # 2 or fewer unique actions in 3 steps
                is_loop = True

        # ── Decision ──
        is_tangent = (
            (tangent_type is not None and distance > 0.5) or
            (distance > 0.7) or
            is_loop
        )

        if is_tangent:
            self.tangent_depth += 1
            self.alert_count += 1

            return TangentAlert(
                tangent_type=tangent_type or ("LOOP" if is_loop else "OFF_TOPIC"),
                distance_from_goal=distance,
                steps_deep=self.tangent_depth,
                current_action=current_action[:100],
                goal_reminder=f"Goal: {self.goal.text[:100]}",
                confidence=min(0.95, 0.5 + 0.15 * self.tangent_depth),
            )

        # On-track → reset tangent depth
        self.tangent_depth = max(0, self.tangent_depth - 1)
        return None

    def get_refocus_prompt(self, alert: TangentAlert) -> str:
        """Generate a prompt to refocus the agent on the goal."""
        if alert.is_critical:
            return (
                f"STOP. You are {alert.steps_deep} steps into a tangent "
                f"({alert.tangent_type}). "
                f"Return IMMEDIATELY to the original task: "
                f"{self.goal.text if self.goal else 'the user request'}. "
                f"Do NOT continue the current approach. Find a SIMPLER way."
            )
        return (
            f"Note: you may be overcomplicating this ({alert.tangent_type}). "
            f"Simplify. "
            f"Goal: {self.goal.text if self.goal else 'user request'}"
        )

    def should_hard_reset(self) -> bool:
        """Should we force-reset the entire approach?"""
        return self.tangent_depth >= self.max_tangent_depth


# ═══════════════════════════════════════════════════════════════════════════════
# Tangent history — for post-mortem analysis
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FocusReport:
    """Summary of focus performance."""

    goal: str
    total_actions: int
    tangent_count: int
    max_tangent_depth: int
    focus_score: float  # 0..1 — higher = better focus
    alerts: list[TangentAlert] = field(default_factory=list)

    @property
    def was_focused(self) -> bool:
        return self.focus_score > 0.7


def analyze_focus(keeper: FocusKeeper) -> FocusReport:
    """Generate focus report from keeper state."""
    if keeper.goal is None:
        return FocusReport(
            goal="(none)", total_actions=0, tangent_count=0,
            max_tangent_depth=0, focus_score=1.0,
        )

    total = len(keeper.action_log)
    tangents = keeper.alert_count
    focus_score = 1.0 - min(1.0, tangents / max(1, total)) if total > 0 else 1.0

    return FocusReport(
        goal=keeper.goal.text,
        total_actions=total,
        tangent_count=tangents,
        max_tangent_depth=keeper.tangent_depth,
        focus_score=focus_score,
    )
