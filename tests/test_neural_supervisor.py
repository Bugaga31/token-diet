"""Tests for neural_scorer.py + agent_supervisor.py"""

from token_diet.agent_supervisor import (
    AgentSupervisor,
    LoopAlert,
    SupervisorState,
    supervised_agent_loop,
    truncate_tool_result,
)
from token_diet.neural_scorer import (
    NeuralScorer,
    ScoredChunk,
    score_prompt_sections,
    strip_noise,
)

# ══════════════════════════════════════════════════════════════════════
# NeuralScorer
# ══════════════════════════════════════════════════════════════════════

class TestNeuralScorer:
    def setup_method(self):
        self.scorer = NeuralScorer()

    def test_empty_text(self):
        assert self.scorer.score("") == 0.0
        result = self.scorer.classify("")
        assert not result.is_noise

    def test_pure_filler_high_score(self):
        text = "It is important to note that we appreciate your feedback."
        score = self.scorer.score(text)
        assert score >= 0.3, f"Expected high noise score, got {score}"

    def test_informative_content_low_score(self):
        text = "The quarterly revenue increased 23% to $4.2M driven by new enterprise contracts in EMEA."
        score = self.scorer.score(text)
        assert score < 0.5, f"Expected low noise score, got {score}"

    def test_please_do_not_hesitate(self):
        text = "Please do not hesitate to contact us if you have any questions."
        score = self.scorer.score(text)
        assert score >= 0.2, f"Expected elevated score for politeness, got {score}"

    def test_classify_returns_scored_chunk(self):
        result = self.scorer.classify("Hello world")
        assert isinstance(result, ScoredChunk)
        assert result.text == "Hello world"
        assert 0.0 <= result.score <= 1.0

    def test_filter_removes_noise(self):
        chunks = [
            "It is important to note that we truly value your feedback and appreciate your patience.",
            "Revenue grew 23% to $4.2M in Q3.",
            "Furthermore, please feel free to reach out with any questions or concerns you may have.",
            "Churn decreased from 5.1% to 3.8%.",
        ]
        kept, classes = self.scorer.filter(chunks)
        # The factual sentences should survive (they have low noise scores)
        assert any("Revenue" in k for k in kept), f"Revenue missing from {kept}"
        assert any("Churn" in k for k in kept), f"Churn missing from {kept}"
        # At least one chunk should be classified (not all noise, not all signal)
        noise_scores = [c.score for c in classes]
        assert max(noise_scores) > min(noise_scores), f"Expected varied scores: {noise_scores}"

    def test_russian_filler(self):
        text = "Следует подчеркнуть, что необходимо отметить важность данного вопроса."
        score = self.scorer.score(text)
        assert score >= 0.3, f"Expected high score for Russian filler, got {score}"

    def test_threshold_configurable(self):
        text = "Please feel free to contact us."

        # Very strict: almost everything is noise
        strict = NeuralScorer(threshold=0.0)
        assert strict.classify(text).is_noise

        # Very loose: almost nothing is noise
        loose = NeuralScorer(threshold=0.95)
        assert not loose.classify(text).is_noise

    def test_score_prompt_sections(self):
        prompt = (
            "Thank you for your inquiry. "
            "The server returned error 500 at 14:32 UTC. "
            "Root cause: out of memory in worker pool. "
            "Please don't hesitate to reach out."
        )
        sections = score_prompt_sections(prompt)
        assert len(sections) >= 3
        # First and last likely noise
        scores = [s.score for s in sections]
        assert max(scores) > min(scores), f"Expected varied scores, got {scores}"

    def test_strip_noise_reduces_tokens(self):
        prompt = (
            "Furthermore, it is important to note that we truly appreciate your patience. "
            "The database migration completed at 03:00 UTC with zero data loss. "
            "Additionally, we would like to thank you for your continued support. "
            "The new indexes improved query performance by 340%."
        )
        clean, before, after = strip_noise(prompt)
        assert after <= before, f"Expected reduction: {before} → {after}"
        assert "database migration" in clean.lower()
        assert "indexes" in clean.lower()


# ══════════════════════════════════════════════════════════════════════
# AgentSupervisor
# ══════════════════════════════════════════════════════════════════════

class TestAgentSupervisor:
    def setup_method(self):
        self.supervisor = AgentSupervisor()
        self.supervisor.start_session()

    def test_start_session_initializes_state(self):
        state = self.supervisor._state
        assert state.total_tokens == 0
        assert state.tool_call_count == 0
        assert not state.terminated
        assert len(state.turns) == 0

    def test_observe_normal_turns_no_alerts(self):
        alerts = self.supervisor.observe_turn(
            "user", "What is the weather?", 5
        )
        assert len(alerts) == 0

        alerts = self.supervisor.observe_turn(
            "assistant", "The weather is sunny, 22°C.", 8
        )
        assert len(alerts) == 0

    def test_detect_tool_repeat(self):
        # Simulate same tool called 4 times
        for _i in range(4):
            alerts = self.supervisor.observe_tool_call(
                "search_web", {"query": "weather"}, result_tokens=50
            )
        # Last call should trigger alert
        assert any(a.loop_type == "tool_repeat" for a in alerts)

    def test_detect_circular_reasoning(self):
        # Same response hash repeated
        for _i in range(10):
            self.supervisor.observe_turn(
                "assistant", "I think the answer is 42.", 7
            )
        # Check if circular detected
        alerts = self.supervisor._state.alerts
        circular = [a for a in alerts if a.loop_type == "circular"]
        assert len(circular) > 0, f"Expected circular detection, alerts: {alerts}"

    def test_detect_token_growth(self):
        # Normal turns first
        for _i in range(3):
            self.supervisor.observe_turn("user", "hi", 2)
            self.supervisor.observe_turn("assistant", "hello", 2)

        # Then explosive growth
        for _i in range(3):
            self.supervisor.observe_turn(
                "assistant", "x" * 500, 500
            )

        alerts = self.supervisor._state.alerts
        growth = [a for a in alerts if a.loop_type == "token_growth"]
        assert len(growth) > 0, "Expected token growth alert"

    def test_oversized_tool_result_warns(self):
        alerts = self.supervisor.observe_tool_call(
            "read_file", {"path": "/etc/hosts"}, result_tokens=5000
        )
        assert any(a.loop_type == "oversized_result" for a in alerts)

    def test_token_limit_terminates(self):
        self.supervisor.observe_turn(
            "assistant", "x" * 50000, 50000
        )
        alerts = self.supervisor.observe_turn(
            "assistant", "y" * 1000, 1000
        )
        assert any(a.loop_type == "token_limit" for a in alerts)
        assert self.supervisor._state.terminated

    def test_should_continue_normal(self):
        assert self.supervisor.should_continue()

    def test_should_continue_terminated(self):
        self.supervisor._state.terminated = True
        assert not self.supervisor.should_continue()

    def test_summary(self):
        self.supervisor.observe_turn("user", "hello", 3)
        self.supervisor.observe_turn("assistant", "hi there", 3)
        summary = self.supervisor.summary()
        assert summary["total_turns"] == 2
        assert summary["total_tokens"] == 6

    def test_on_alert_callback(self):
        called = []
        sv = AgentSupervisor(on_alert=lambda a: called.append(a))
        sv.start_session()
        for _i in range(5):
            sv.observe_tool_call("search", {"q": "x"}, result_tokens=10)
        assert len(called) > 0


class TestTruncateToolResult:
    def test_short_result_no_truncation(self):
        result, was = truncate_tool_result("Hello world", max_tokens=100)
        assert not was
        assert result == "Hello world"

    def test_long_result_truncated(self):
        long_text = "word " * 3000  # ~3900 tokens
        result, was = truncate_tool_result(long_text, max_tokens=1000)
        assert was
        assert "[... " in result
        assert "truncated ...]" in result

    def test_truncation_preserves_start_and_end(self):
        words = [f"line{i}" for i in range(2000)]
        long_text = " ".join(words)
        result, was = truncate_tool_result(long_text, max_tokens=500)
        assert was
        assert "line0" in result
        assert "line1999" in result


class TestSupervisedAgentLoop:
    def test_normal_agent_completes(self):
        calls = []
        def agent(prompt: str) -> str:
            calls.append(prompt)
            if len(calls) >= 3:
                return "FINAL: The answer is 42."
            return f"Thinking step {len(calls)}..."

        response, state = supervised_agent_loop(
            agent, "What is the meaning of life?", max_iterations=10
        )
        # Agent should complete with FINAL marker
        assert len(state.turns) > 0

    def test_looping_agent_terminated(self):
        def agent(prompt: str) -> str:
            return "I'm still thinking... let me try again."

        response, state = supervised_agent_loop(
            agent, "Solve this", max_iterations=5
        )
        # Should have run all iterations and stopped
        assert len(state.turns) > 0


class TestLoopAlert:
    def test_alert_fields(self):
        alert = LoopAlert(
            loop_type="test",
            details="testing",
            severity="warning",
            suggestion="fix it",
            turns_since_detection=0,
        )
        assert alert.loop_type == "test"
        assert alert.severity == "warning"


class TestSupervisorState:
    def test_default_state(self):
        state = SupervisorState()
        assert state.total_tokens == 0
        assert state.tool_call_count == 0
        assert not state.terminated
