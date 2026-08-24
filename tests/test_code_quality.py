"""Tests for code_quality.py — deterministic code review without neural calls."""

from token_diet.code_quality import (
    REVIEW_CHECKLIST,
    Finding,
    build_review_prompt,
    code_review_prompt,
    describe_findings,
    detect_code_smells,
    quality_report,
    review_code,
    tdd_flow,
)


class TestDetectSmells:
    def test_mutable_default_arg(self):
        findings = detect_code_smells("def f(x=[]):\n    return x\n")
        assert any("Mutable default" in f.message for f in findings)

    def test_bare_except(self):
        findings = detect_code_smells("try:\n    pass\nexcept:\n    pass\n")
        assert any("Bare except" in f.message for f in findings)

    def test_except_pass(self):
        findings = detect_code_smells("try:\n    x()\nexcept Exception:\n    pass\n")
        assert any("except: pass" in f.message or "silently hides" in f.message
                   for f in findings)

    def test_hardcoded_secret(self):
        findings = detect_code_smells('password = "hunter2secret"\n')
        assert any(f.severity == "critical" and "secret" in f.message
                   for f in findings)

    def test_todo_found(self):
        findings = detect_code_smells("# TODO: add logging\n")
        assert any("TODO" in f.message for f in findings)

    def test_print_found(self):
        findings = detect_code_smells('print("hello")\n')
        assert any("print()" in f.message for f in findings)

    def test_clean_code_no_findings(self):
        findings = detect_code_smells(
            "def add(a, b):\n    return a + b\n"
        )
        criticals = [f for f in findings if f.severity == "critical"]
        warnings = [f for f in findings if f.severity == "warning"]
        assert not criticals
        assert not warnings  # simple clean function


class TestAstChecks:
    def test_syntax_error_detected(self):
        findings = review_code("def broken(:\n")
        assert any(f.severity == "critical" and "parse" in f.message
                   for f in findings)

    def test_undefined_name(self):
        findings = review_code("result = undefined_var\n")
        assert any("undefined_var" in f.message for f in findings)

    def test_getter_without_return(self):
        findings = review_code("def get_name():\n    pass\n")
        assert any("never returns" in f.message for f in findings)


class TestQualityReport:
    def test_bad_code_fails(self):
        rep = quality_report('def f(x=[]):\n    password = "secret123"\n    try:\n        return x\n    except:\n        pass\n')
        assert rep["score"] < 60
        assert rep["verdict"] == "FAIL"
        assert rep["critical_count"] >= 1

    def test_good_code_passes(self):
        rep = quality_report(
            "def add(a, b):\n"
            "    \"\"\"Return a + b.\"\"\"\n"
            "    return a + b\n"
        )
        assert rep["score"] >= 85
        assert rep["verdict"] in ("PASS", "WARN")

    def test_empty_code(self):
        rep = quality_report("")
        assert rep["score"] == 100
        assert rep["verdict"] == "PASS"

    def test_report_structure(self):
        rep = quality_report("x = 1\n")
        assert "score" in rep and "verdict" in rep and "findings" in rep


class TestPrompts:
    def test_tdd_flow_has_red_green(self):
        p = tdd_flow("task", "test.py", "code.py")
        assert "RED" in p and "GREEN" in p and "REFACTOR" in p
        assert "test.py" in p and "code.py" in p

    def test_review_checklist_present(self):
        assert "correctness" in REVIEW_CHECKLIST.lower()

    def test_build_review_prompt_appends(self):
        p = build_review_prompt("You are helpful.")
        assert "You are helpful." in p
        assert "CHECKLIST" in p

    def test_code_review_prompt_has_code(self):
        p = code_review_prompt("print('hi')", focus="security")
        assert "print('hi')" in p
        assert "security" in p.lower()

    def test_describe_findings_empty(self):
        assert "clean" in describe_findings([]).lower()


class TestFinding:
    def test_finding_fields(self):
        f = Finding("critical", "security", "msg", 3, "hint")
        assert f.severity == "critical"
        assert f.line == 3
        assert f.fix_hint == "hint"

    def test_finding_dict(self):
        f = Finding("warning", "style", "m")
        d = f.__dict__
        assert d["category"] == "style"
