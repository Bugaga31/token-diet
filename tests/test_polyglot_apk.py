"""Tests for polyglot (language detection, code protection) and apk_builder."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from token_diet.apk_builder import (
    create_android_project,
    inspect_apk,
)
from token_diet.polyglot import (
    SYSTEM_PROMPTS,
    code_budget,
    detect_language,
    protect_code,
    system_prompt_for,
)


class TestDetectLanguage(unittest.TestCase):
    def test_by_extension(self):
        self.assertEqual(detect_language("x = 1", "foo.py"), "python")
        self.assertEqual(detect_language("var x = 1", "app.js"), "javascript")
        self.assertEqual(detect_language("fn main() {}", "main.rs"), "rust")
        self.assertEqual(detect_language("func main() {}", "main.go"), "go")
        self.assertEqual(detect_language("", "Main.java"), "java")
        self.assertEqual(detect_language("", "App.kt"), "kotlin")

    def test_by_fingerprint(self):
        py = "import os\ndef main():\n    pass\n"
        self.assertEqual(detect_language(py), "python")

        js = "const x = require('os');\n"
        self.assertEqual(detect_language(js), "javascript")

        sql = "SELECT name FROM users WHERE id = 1;"
        self.assertEqual(detect_language(sql), "sql")

    def test_by_shebang(self):
        self.assertEqual(detect_language("#!/usr/bin/env python3\nprint(1)"), "python")
        self.assertEqual(detect_language("#!/bin/bash\necho hi"), "bash")

    def test_unknown(self):
        self.assertEqual(detect_language("just some random prose text here"), "text")


class TestProtectCode(unittest.TestCase):
    def test_python_strings_and_comments(self):
        code = '# comment line\nx = "important string value"\ny = 1\n'
        spans = protect_code(code, "python")
        self.assertTrue(spans)  # found at least comment or string
        kinds = {k for _, _, k in spans}
        self.assertIn("comment", kinds)
        self.assertIn("string", kinds)
        # the string content must be inside a protected span
        str_span = next((s for s in spans if s[2] == "string"), None)
        self.assertIsNotNone(str_span)
        self.assertIn("important string value", code[str_span[0]:str_span[1]])

    def test_javascript_template_literal(self):
        code = "const s = `template ${x} literal`;\n"
        spans = protect_code(code, "javascript")
        str_span = next((s for s in spans if s[2] == "string"), None)
        self.assertIsNotNone(str_span)
        self.assertIn("template", code[str_span[0]:str_span[1]])

    def test_cpp_multiline_comment(self):
        code = "/* multi\nline\ncomment */ int x = 1;"
        spans = protect_code(code, "cpp")
        comments = [s for s in spans if s[2] == "comment"]
        self.assertTrue(comments)
        self.assertIn("multi", code[comments[0][0]:comments[0][1]])


class TestCodeBudget(unittest.TestCase):
    def test_droppable_pct(self):
        code = "# comment line\n# another comment\nimport os\nimport sys\n\nx = 1\n"
        b = code_budget(code, "python")
        self.assertEqual(b.language, "python")
        self.assertGreater(b.import_chars, 0)
        self.assertGreater(b.comment_chars, 0)
        self.assertGreater(b.droppable_pct, 10)

    def test_language_detected_automatically(self):
        b = code_budget("import os\ndef f():\n    pass\n")
        self.assertEqual(b.language, "python")


class TestSystemPrompts(unittest.TestCase):
    def test_has_prompts(self):
        for lang in ("python", "rust", "go", "java", "kotlin", "sql", "bash"):
            self.assertIn(lang, SYSTEM_PROMPTS)
            self.assertTrue(system_prompt_for(lang))

    def test_unknown_returns_empty(self):
        self.assertEqual(system_prompt_for("brainfuck"), "")


class TestApkBuilder(unittest.TestCase):
    def test_create_project(self):
        with tempfile.TemporaryDirectory() as td:
            proj = create_android_project(td, package="com.example.test",
                                          app_name="Test")
            self.assertTrue(proj.manifest_path.exists())
            self.assertTrue((proj.root / "res" / "layout" /
                             "activity_main.xml").exists())
            java_files = list((proj.root / "src").rglob("*.java"))
            self.assertEqual(len(java_files), 1)
            self.assertIn("com.example.test", java_files[0].read_text())

    def test_inspect_apk(self):
        # build a tiny fake apk (zip with manifest + dex) and inspect it
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "fake.apk"
            with zipfile.ZipFile(fake, "w") as z:
                z.writestr("AndroidManifest.xml", "<?xml?><manifest/>")
                z.writestr("classes.dex", b"dex\n035\x00")
            info = inspect_apk(fake)
            self.assertEqual(info["dex_files"], ["classes.dex"])
            self.assertTrue(info["has_manifest"])
            self.assertIn("size_mb", info)


if __name__ == "__main__":
    unittest.main()
