"""Tests for the 4 new lesson-learned modules: rapid_context, decisive_agent, focus_keeper, mistake_learner."""

from token_diet.decisive_agent import (
    DecisiveAgent,
    strip_fluff,
)
from token_diet.focus_keeper import (
    FocusKeeper,
    analyze_focus,
)
from token_diet.mistake_learner import (
    AntiPattern,
    MistakeLearner,
    check_action,
)
from token_diet.rapid_context import (
    classify,
    quick_action,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Rapid Context
# ═══════════════════════════════════════════════════════════════════════════════

class TestRapidContext:
    def test_classify_code(self):
        ctx = classify("напиши функцию фибоначчи на Python")
        assert ctx.intent == "CODE"
        assert ctx.confidence > 0.5
        assert ctx.needs_tools

    def test_classify_question(self):
        ctx = classify("что такое квантовая запутанность?")
        assert ctx.intent in ("QUESTION", "RESEARCH")

    def test_classify_action(self):
        ctx = classify("установи python3-pip")
        assert ctx.intent in ("ACTION", "RESEARCH")

    def test_classify_debug(self):
        ctx = classify("код не работает, ошибка на строке 5")
        assert ctx.intent == "DEBUG"

    def test_classify_investment(self):
        ctx = classify("купить акции сбера или лукойла?")
        assert ctx.intent == "INVESTMENT"

    def test_classify_quick(self):
        ctx = classify("да")
        assert ctx.intent == "QUICK"
        assert ctx.is_trivial

    def test_classify_urgent(self):
        ctx = classify("срочно напиши код!!!")
        assert ctx.urgency >= 0.3  # "срочно" detected, baseline urgency

    def test_classify_empty(self):
        ctx = classify("")
        assert ctx.intent == "CHAT"

    def test_decide_action_code(self):
        plan = quick_action("напиши класс User")
        assert plan["intent"] == "CODE"
        assert plan["action"] in ("write_code", "code")

    def test_rapid_context_confidence(self):
        ctx = classify("создай файл main.py и добавь туда функцию hello world")
        assert ctx.confidence > 0.4
        assert ctx.intent in ("CODE", "ACTION")


# ═══════════════════════════════════════════════════════════════════════════════
# Decisive Agent
# ═══════════════════════════════════════════════════════════════════════════════

class TestDecisiveAgent:
    def test_instant_mode(self):
        agent = DecisiveAgent()
        d = agent.decide("да", intent="QUICK", urgency=0.0, complexity=0.0)
        assert d.mode == "INSTANT"
        assert d.skip_deliberation

    def test_fast_mode(self):
        agent = DecisiveAgent()
        d = agent.decide("напиши функцию", intent="CODE", urgency=0.3, complexity=0.2)
        assert d.mode == "FAST"
        assert d.max_reasoning_tokens <= 100

    def test_deep_mode(self):
        agent = DecisiveAgent()
        d = agent.decide("проанализируй рынок акций", intent="RESEARCH", urgency=0.2, complexity=0.7)
        assert d.mode == "DEEP"

    def test_urgent_overrides(self):
        agent = DecisiveAgent()
        d = agent.decide("сложный анализ", intent="RESEARCH", urgency=0.9, complexity=0.8)
        assert d.mode == "INSTANT"

    def test_strip_deliberation(self):
        agent = DecisiveAgent()
        result = agent.strip_deliberation(
            "Let me think about this. First, I'll analyze the code. The bug is on line 5."
        )
        assert "Let me think" not in result
        assert "bug" in result.lower()

    def test_strip_russian_deliberation(self):
        agent = DecisiveAgent()
        result = agent.strip_deliberation(
            "Хорошо, давайте разберёмся. Проблема в том, что порт занят."
        )
        assert "разберёмся" not in result.lower()
        assert "порт" in result.lower()

    def test_strip_fluff_one_liner(self):
        result = strip_fluff("Let me think... OK here is the answer: 42")
        assert "Let me think" not in result
        assert "42" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Focus Keeper
# ═══════════════════════════════════════════════════════════════════════════════

class TestFocusKeeper:
    def test_set_goal(self):
        keeper = FocusKeeper()
        goal = keeper.set_goal("напиши функцию фибоначчи", intent="CODE")
        assert goal.intent == "CODE"
        assert len(goal.key_terms) > 0

    def test_no_tangent_when_on_track(self):
        keeper = FocusKeeper()
        keeper.set_goal("напиши функцию", intent="CODE")
        # "function" matches CODE intent — should be on track
        alert = keeper.check("creating file main.py with function")
        assert alert is None

    def test_tangent_detected(self):
        keeper = FocusKeeper()
        keeper.set_goal("напиши функцию", intent="CODE")
        alert = keeper.check("debugging adb input text on phone screen")
        assert alert is not None
        assert alert.tangent_type is not None

    def test_tangent_depth_increases(self):
        keeper = FocusKeeper()
        keeper.set_goal("напиши функцию", intent="CODE")
        keeper.check("debugging adb connection")  # tangent 1
        assert keeper.tangent_depth == 1
        keeper.check("trying adb shell input tap")  # tangent 2
        assert keeper.tangent_depth == 2

    def test_hard_reset_at_max_depth(self):
        keeper = FocusKeeper(max_tangent_depth=3)
        keeper.set_goal("напиши функцию", intent="CODE")
        for _ in range(3):
            keeper.check("debugging adb shell input text again")
        assert keeper.should_hard_reset()

    def test_refocus_prompt(self):
        keeper = FocusKeeper()
        keeper.set_goal("напиши функцию", intent="CODE")
        alert = keeper.check("debugging adb connection endlessly")
        prompt = keeper.get_refocus_prompt(alert)
        assert "напиши функцию" in prompt

    def test_analyze_focus(self):
        keeper = FocusKeeper()
        keeper.set_goal("test goal", intent="CODE")
        keeper.check("writing a function")  # function → CODE hint → on track
        keeper.check("creating a class")  # class → CODE hint → on track
        report = analyze_focus(keeper)
        assert report.focus_score >= 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# Mistake Learner
# ═══════════════════════════════════════════════════════════════════════════════

class TestMistakeLearner:
    def test_builtin_patterns_loaded(self):
        learner = MistakeLearner()
        assert len(learner.patterns) >= 8  # built-in patterns

    def test_check_adb_input_text(self):
        alert = check_action("adb shell input text cp file")
        assert alert is not None
        assert "NEVER" in alert.pattern.category or "WARN" in alert.pattern.category or "PREFER" in alert.pattern.category

    def test_check_safe_action(self):
        alert = check_action("write a Python function")
        assert alert is None  # no anti-pattern

    def test_record_failure(self):
        learner = MistakeLearner()
        initial_count = len(learner.patterns)
        learner.record(
            "trying some new bad approach that always fails",
            success=False,
            mistake_description="This approach never works",
        )
        assert len(learner.patterns) >= initial_count

    def test_pattern_matches(self):
        pattern = AntiPattern(
            pattern_id="TEST",
            category="NEVER",
            description="test pattern",
            condition="bad_approach",
            alternative="good_approach",
            severity=0.5,
        )
        assert pattern.matches("using bad_approach to solve problem")
        assert not pattern.matches("using good approach")

    def test_summary(self):
        learner = MistakeLearner()
        summary = learner.summary()
        assert "Mistake Learner" in summary
        assert len(summary) > 20


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: all 4 modules work together
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    def test_full_pipeline(self):
        """Simulate the lesson: user asks something, we classify, decide, check focus, learn."""
        # Step 1: Classify
        ctx = classify("напиши функцию фибоначчи быстро!")
        assert ctx.intent == "CODE"
        assert ctx.urgency > 0.3

        # Step 2: Decide
        agent = DecisiveAgent()
        d = agent.decide("напиши функцию фибоначчи быстро!", intent=ctx.intent, urgency=ctx.urgency, complexity=ctx.complexity)
        assert d.mode in ("INSTANT", "FAST")

        # Step 3: Focus check (on-track)
        keeper = FocusKeeper()
        keeper.set_goal("напиши функцию фибоначчи", intent="CODE")
        alert = keeper.check("writing a fibonacci function in python")
        assert alert is None  # "function" → CODE intent match → on track

        # Step 4: Would detect tangent
        bad_alert = keeper.check("debugging adb input text on phone screen")
        assert bad_alert is not None

        # Step 5: Mistake check
        m_alert = check_action("adb shell input text cp file")
        assert m_alert is not None  # known anti-pattern
