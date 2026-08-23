"""ui_context — see a web page the way the model needs to, for the least tokens.

Reverse-engineered from:
- Playwright MCP: instead of screenshots (expensive pixels), it dumps the
  DOM accessibility tree + interactable elements so the LLM can target
  exact elements ("click element #42") without a single image token.
- Strix (stealth browser automation): realistic browser fingerprints
  (User-Agent, viewport, headers) so a driven browser is not detected.

Why this matters for token-diet:
    A screenshot of a page costs hundreds of image tokens. An
    accessibility map — buttons, links, inputs, roles, text — costs
    tens of TEXT tokens and is *more* reliable for acting on the page.
    For UI agents this is a 10-100x context reduction.

Pure stdlib (html.parser). No browser, no OCR, no network.
Feed it the HTML you already have; get the compact map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

# Tags that are "interactive" — the LLM can act on them.
_INTERACTIVE_TAGS = {"a", "button", "input", "select", "textarea", "option", "summary", "label", "datalist"}
# Input types that produce a value (text-entry or choice), vs pure buttons.
_VALUE_INPUTS = {"text", "search", "email", "tel", "url", "password", "number",
                 "date", "time", "range", "color", "file"}
_BUTTON_INPUTS = {"submit", "button", "reset", "image"}
# Roles that matter even without an obvious tag.
_ROLE_TAGS = {"nav", "header", "footer", "main", "aside", "dialog", "form", "menu"}

# Strix-style realistic fingerprint defaults (safe, non-identifying).
DEFAULT_VIEWPORTS = ((1920, 1080), (1536, 864), (1440, 900), (1366, 768), (390, 844))
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class InteractiveElement:
    """One actionable element on the page."""
    index: int
    tag: str
    role: str = ""                 # computed accessibility role
    text: str = ""                 # visible label
    href: str = ""                 # links only
    name: str = ""                 # input name attribute
    input_type: str = ""           # input type attribute
    placeholder: str = ""
    aria_label: str = ""
    value: str = ""
    disabled: bool = False
    depth: int = 0

    @property
    def label(self) -> str:
        """Best human/LLM label: aria-label > text > placeholder > name."""
        return (self.aria_label or self.text or self.placeholder or self.name).strip()

    def to_line(self) -> str:
        """One compact line for the LLM context block."""
        bits = [f"[{self.index}]"]
        bits.append(f"<{self.tag}>")
        if self.role and self.role != self.tag:
            bits.append(f"role={self.role}")
        if self.label:
            bits.append(f'"{self.label[:60]}"')
        if self.href:
            bits.append(f"→{self.href[:60]}")
        if self.name and self.name != self.label:
            bits.append(f"name={self.name[:30]}")
        if self.input_type:
            bits.append(f"type={self.input_type}")
        if self.placeholder and self.placeholder != self.label:
            bits.append(f"ph={self.placeholder[:30]}")
        if self.value and len(self.value) < 25:
            bits.append(f"val={self.value}")
        if self.disabled:
            bits.append("disabled")
        return " ".join(bits)


@dataclass
class AccessibilityNode:
    """A node in the compact accessibility tree (semantic structure)."""
    tag: str
    role: str
    text: str = ""
    children: list["AccessibilityNode"] = field(default_factory=list)
    depth: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# HTML → interactive element map (Playwright-style, pure stdlib)
# ═══════════════════════════════════════════════════════════════════════════════

def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_tags(text or "")).strip()


class _UiParser(HTMLParser):
    """Collects interactive elements + builds a semantic skeleton tree."""

    _VOID = {"input", "img", "br", "hr", "meta", "link", "area", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[InteractiveElement] = []
        self.skeleton: list[list] = []  # [tag, text, attrs]
        self._text_buf: list[str] = []
        self._stack: list[str] = []
        self._struct_stack: list[int] = []  # skeleton indices of open structural tags
        self._current_attrs: dict = {}
        self._seen: set[tuple[str, str]] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        self._stack.append(tag)
        self._text_buf = []
        d = dict(attrs)
        self._current_attrs = d

        if tag in _INTERACTIVE_TAGS:
            self._register(tag, d, depth=len(self._stack))
        elif tag in _ROLE_TAGS or (len(tag) == 2 and tag[0] == "h" and tag[1].isdigit()):
            self.skeleton.append([tag, "", {}])
            self._struct_stack.append(len(self.skeleton) - 1)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        # self-closing interactive (e.g. <input />)
        if tag in _INTERACTIVE_TAGS:
            self._register(tag, dict(attrs), depth=len(self._stack) + 1)

    def handle_endtag(self, tag: str) -> None:
        if self._stack:
            self._stack.pop()
        # attach collected text to the nearest open structural tag (e.g. h1)
        if self._struct_stack and self._text_buf:
            idx = self._struct_stack[-1]
            if not self.skeleton[idx][1]:
                self.skeleton[idx][1] = _clean_text(" ".join(self._text_buf))
            self._struct_stack.pop()
        elif self._struct_stack and tag in _ROLE_TAGS:
            # landmark closed without text — keep it but drop empty children noise
            if not self.skeleton[self._struct_stack[-1]][1]:
                pass
            self._struct_stack.pop()

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self._text_buf.append(data.strip())
        # Attach accumulated text to the most recent interactive element
        if self.elements:
            last = self.elements[-1]
            if last.text == "":
                last.text = _clean_text(" ".join(self._text_buf))

    def _register(self, tag: str, attrs: dict, depth: int) -> None:
        text = _clean_text(attrs.get("aria-label", "")) or _clean_text(attrs.get("title", ""))
        role = attrs.get("role", "")
        if not role:
            role = {"a": "link", "button": "button", "input": "textbox",
                    "select": "combobox", "textarea": "textbox", "option": "option",
                    "summary": "button", "label": "label"}.get(tag, "")

        input_type = attrs.get("type", "")
        if tag == "input" and input_type in _BUTTON_INPUTS:
            role = "button"

        el = InteractiveElement(
            index=len(self.elements),
            tag=tag,
            role=role,
            text=text,
            href=attrs.get("href", ""),
            name=attrs.get("name", ""),
            input_type=input_type,
            placeholder=attrs.get("placeholder", ""),
            aria_label=attrs.get("aria-label", ""),
            value=attrs.get("value", ""),
            disabled=attrs.get("disabled") is not None or attrs.get("aria-disabled") == "true",
            depth=depth,
        )

        key = (tag, el.label, el.href)
        if key in self._seen:
            return
        self._seen.add(key)
        self.elements.append(el)
        self.skeleton.append([tag, el.label, {}])


def extract_interactive_elements(html: str) -> list[InteractiveElement]:
    """Parse HTML → list of actionable elements (Playwright a11y style).

    Deduplicates nested/overlapping elements so the map stays compact.
    """
    parser = _UiParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return []
    return parser.elements


def render_ui_context(html: str, max_elements: int = 40) -> str:
    """Compact LLM context block: page structure + actionable elements.

    Designed to replace a screenshot for acting on a page.
    """
    elements = extract_interactive_elements(html)
    if not elements:
        return "[No interactive elements found in HTML]"

    lines = ["[UI context — interactive elements]"]
    shown = 0
    for el in elements:
        if shown >= max_elements:
            lines.append(f"...and {len(elements) - shown} more")
            break
        lines.append("  " + el.to_line())
        shown += 1
    lines.append("[End UI context]")
    return "\n".join(lines)


def render_accessibility_tree(html: str, max_depth: int = 6) -> str:
    """Compact semantic tree of the page (headings/landmarks/labels).

    Cheaper than the full DOM, richer than the flat element list.
    """
    parser = _UiParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return ""

    # Rebuild a tree from the skeleton stack (simple indentation by depth)
    lines: list[str] = []
    depth = 0
    for tag, text, _attrs in parser.skeleton:
        if tag in {"html", "head", "body"}:
            continue
        if tag in _INTERACTIVE_TAGS and not text:
            continue
        indent = "  " * min(depth, max_depth)
        line = f"{indent}<{tag}>"
        if text:
            line += f" {text[:70]}"
        lines.append(line)
        depth = min(depth + 1, max_depth)
    if not lines:
        return ""
    return "[Page structure]\n" + "\n".join(lines) + "\n[End structure]"


def estimate_ui_savings(html: str) -> dict[str, Any]:
    """Honest token math: raw HTML vs compact a11y context vs screenshot."""
    from .core import count_tokens

    elements = extract_interactive_elements(html)
    context = render_ui_context(html)
    raw_tokens = count_tokens(html)
    map_tokens = count_tokens(context) if context else 0

    # Screenshot comparison (approx: one 1280x720 screenshot ≈ 800-1500 img tokens)
    shot_tokens = 1100
    saved_vs_html = raw_tokens - map_tokens
    saved_vs_shot = shot_tokens - map_tokens

    return {
        "html_tokens": raw_tokens,
        "map_tokens": map_tokens,
        "elements": len(elements),
        "saved_vs_html": saved_vs_html,
        "saved_vs_html_pct": round(100 * saved_vs_html / max(1, raw_tokens), 1),
        "saved_vs_screenshot": saved_vs_shot,
        "saved_vs_screenshot_pct": round(100 * saved_vs_shot / shot_tokens, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Stealth fingerprint (Strix-style, but local and optional)
# ═══════════════════════════════════════════════════════════════════════════════

def stealth_fingerprint(viewpoint: str = "desktop") -> dict[str, Any]:
    """Realistic browser fingerprint to blend in (Strix insight).

    Returns a dict you can apply to any headless browser:
      - consistent UA/headers
      - non-headless-looking viewport
      - Accept-Language matching the locale
    Local and optional: no network calls, no detection itself.
    """
    if viewpoint == "mobile":
        viewport = DEFAULT_VIEWPORTS[-1]
        ua = (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
        )
    else:
        viewport = DEFAULT_VIEWPORTS[0]
        ua = DEFAULT_UA

    return {
        "user_agent": ua,
        "viewport": {"width": viewport[0], "height": viewport[1]},
        "headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        },
        "is_mobile": viewpoint == "mobile",
    }


def is_bot_like_ua(user_agent: str) -> bool:
    """Cheap heuristic: does this UA look like an automated client?

    Strix exists because headless browsers are fingerprintable. This is
    the defensive mirror: flag obvious bot markers so *we* can notice
    when a target might be serving us a bot-blocking page.
    """
    ua = (user_agent or "").lower()
    markers = ("headless", "phantomjs", "python-requests", "python-urllib",
               "curl/", "wget/", "bot", "crawler", "scrapy", "selenium")
    return any(m in ua for m in markers)


__all__ = [
    "AccessibilityNode",
    "DEFAULT_UA",
    "InteractiveElement",
    "extract_interactive_elements",
    "estimate_ui_savings",
    "is_bot_like_ua",
    "render_accessibility_tree",
    "render_ui_context",
    "stealth_fingerprint",
]
