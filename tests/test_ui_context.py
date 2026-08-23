"""Tests for ui_context — Playwright/Strix-style a11y extraction."""
import pytest

from token_diet.ui_context import (
    extract_interactive_elements,
    estimate_ui_savings,
    is_bot_like_ua,
    render_accessibility_tree,
    render_ui_context,
    stealth_fingerprint,
)

HTML = """
<html><head><title>Test</title></head><body>
<nav><a href="/home">Home</a></nav>
<main>
  <h1>Login</h1>
  <form>
    <label>Email <input type="email" name="email" placeholder="you@x.io"></label>
    <input type="password" name="pass">
    <button type="submit">Sign in</button>
    <button aria-label="Close dialog">×</button>
  </form>
</main>
<footer><a href="/about">About us</a></footer>
</body></html>
"""


def test_extract_finds_interactive_elements():
    els = extract_interactive_elements(HTML)
    tags = {e.tag for e in els}
    assert "a" in tags and "button" in tags and "input" in tags
    assert len(els) >= 5


def test_input_type_and_name():
    els = extract_interactive_elements(HTML)
    email = next(e for e in els if e.name == "email")
    assert email.input_type == "email"
    assert "you@x.io" in email.placeholder


def test_aria_label_used():
    els = extract_interactive_elements(HTML)
    close = next(e for e in els if e.tag == "button" and e.aria_label)
    assert close.label == "Close dialog"


def test_render_ui_context_compact():
    block = render_ui_context(HTML)
    assert block.startswith("[UI context")
    assert "Sign in" in block
    assert "Email" in block or "email" in block


def test_render_tree():
    tree = render_accessibility_tree(HTML)
    assert "Login" in tree


def test_estimate_savings_honest():
    s = estimate_ui_savings(HTML)
    assert s["map_tokens"] < s["html_tokens"]
    assert s["saved_vs_screenshot"] > 0


def test_stealth_fingerprint():
    fp = stealth_fingerprint("mobile")
    assert fp["is_mobile"] is True
    assert "Mobile" in fp["user_agent"]
    fp2 = stealth_fingerprint("desktop")
    assert fp2["viewport"]["width"] > 1000


def test_bot_detection():
    assert is_bot_like_ua("Mozilla/5.0 (compatible; Googlebot/2.1)")
    assert not is_bot_like_ua("Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0.0.0")


def test_empty_html():
    assert extract_interactive_elements("") == []
    assert render_ui_context("") == "[No interactive elements found in HTML]"
