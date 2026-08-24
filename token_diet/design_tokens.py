"""design_tokens — turn a design file into compact, reusable style tokens.

Reverse-engineered from:
- Figma MCP: walks the design node tree, extracts layout (layoutMode,
  padding, itemSpacing), fills, typography — and serializes ONLY what
  the model needs, dropping render metadata that wastes tokens.
- Figma Console / Tailwind MCP: maps design tokens onto Tailwind
  utility classes using string-distance, no embeddings, no LLM calls.
- "Skill UI"-style frontend skills: a design system expressed as a
  small token vocabulary beats dumping raw CSS/JSON into the prompt.

Why this matters for token-diet:
    A Figma JSON export for one screen can be 50k+ tokens. The
    meaningful design tokens (colors, spacing, radius, type scale) are
    usually a few hundred tokens. Same design decisions, 50-100x fewer
    tokens — and the model actually *applies* the tokens instead of
    drowning in metadata.

Pure stdlib. Input: a JSON tree shaped like Figma's `document` (dict).
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any

# A compact built-in Tailwind v3 palette (subset) for hex→class mapping.
# Color distance is Euclidean RGB — good enough to pick the nearest class.
_TAILWIND_COLORS: dict[str, str] = {
    # name -> hex
    "slate-500": "#64748b", "slate-700": "#334155", "slate-900": "#0f172a",
    "gray-500": "#6b7280", "gray-700": "#374151", "gray-900": "#111827",
    "zinc-500": "#71717a", "zinc-800": "#27272a", "zinc-900": "#18181b",
    "neutral-500": "#737373", "neutral-800": "#262626", "neutral-900": "#171717",
    "red-500": "#ef4444", "red-600": "#dc2626", "red-700": "#b91c1c",
    "orange-500": "#f97316", "amber-500": "#f59e0b", "yellow-500": "#eab308",
    "lime-500": "#84cc16", "green-500": "#22c55e", "green-600": "#16a34a",
    "emerald-500": "#10b981", "teal-500": "#14b8a6", "cyan-500": "#06b6d4",
    "sky-500": "#0ea5e9", "blue-500": "#3b82f6", "blue-600": "#2563eb",
    "indigo-500": "#6366f1", "violet-500": "#8b5cf6", "purple-500": "#a855f7",
    "fuchsia-500": "#d946ef", "pink-500": "#ec4899", "rose-500": "#f43f5e",
    "white": "#ffffff", "black": "#000000",
}

_LAYOUT_KEYS = ("layoutMode", "paddingLeft", "paddingRight", "paddingTop",
                "paddingBottom", "itemSpacing", "primaryAxisAlignItems",
                "counterAxisAlignItems", "gap", "cornerRadius", "opacity")


@dataclass
class DesignTokens:
    """Extracted, deduplicated design tokens."""
    colors: dict[str, str] = field(default_factory=dict)      # name -> hex
    typography: dict[str, str] = field(default_factory=dict)  # name -> style summary
    spacing: set[int] = field(default_factory=set)            # px values
    radii: set[int] = field(default_factory=set)              # px values
    layout_rules: list[str] = field(default_factory=list)     # flex/grid rules
    node_count: int = 0

    def render(self, fmt: str = "css") -> str:
        """Render tokens as a compact CSS or Tailwind config block."""
        lines: list[str] = []
        if self.colors:
            if fmt == "tailwind":
                lines.append("// colors")
                for name, hexv in sorted(self.colors.items()):
                    lines.append(f'  "{name}": "{hexv}",')
            else:
                lines.append(":root {")
                for name, hexv in sorted(self.colors.items()):
                    lines.append(f"  --{name}: {hexv};")
                lines.append("}")
        if self.typography:
            lines.append("/* type */")
            for name, summary in sorted(self.typography.items()):
                lines.append(f"  {name}: {summary};")
        if self.spacing:
            vals = ", ".join(str(s) for s in sorted(self.spacing))
            lines.append(f"/* spacing: {vals} px */")
        if self.radii:
            vals = ", ".join(str(r) for r in sorted(self.radii))
            lines.append(f"/* radii: {vals} px */")
        for rule in self.layout_rules[:20]:
            lines.append(rule)
        if not lines:
            return ""
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "colors": self.colors,
            "typography": self.typography,
            "spacing": sorted(self.spacing),
            "radii": sorted(self.radii),
            "layout_rules": self.layout_rules,
            "node_count": self.node_count,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Extraction (Figma-style tree walk)
# ═══════════════════════════════════════════════════════════════════════════════

def _hex(color: dict) -> str:
    r = int(round(color.get("r", 0) * 255))
    g = int(round(color.get("g", 0) * 255))
    b = int(round(color.get("b", 0) * 255))
    return f"#{r:02x}{g:02x}{b:02x}"


def _walk(node: dict, tokens: DesignTokens, name_prefix: str = "") -> None:
    tokens.node_count += 1
    name = str(node.get("name", "")).strip() or name_prefix

    # Colors from fills
    for fill in node.get("fills") or []:
        if fill.get("type") == "SOLID" and fill.get("visible", True):
            color = fill.get("color")
            if color:
                hexv = _hex(color)
                key = f"{_slug(name)}-color" if name else hexv.lstrip("#")
                tokens.colors.setdefault(_slug(key) or hexv.lstrip("#"), hexv)

    # Typography from text styles
    style = node.get("style") or {}
    if style.get("fontSize") or style.get("fontWeight"):
        font = style.get("fontFamily", "sans")
        size = style.get("fontSize", "?")
        weight = style.get("fontWeight", "400")
        tokens.typography[f"{_slug(name) or 'text'}"] = (
            f"{font} {weight}, {size}px")

    # Spacing / radius
    for k, v in node.items():
        if k in ("paddingLeft", "paddingRight", "paddingTop", "paddingBottom",
                 "itemSpacing", "gap", "cornerRadius"):
            if isinstance(v, int | float) and v > 0:
                if "Radius" in k or k == "cornerRadius":
                    tokens.radii.add(int(round(v)))
                else:
                    tokens.spacing.add(int(round(v)))

    # Layout rule (auto-layout → flex)
    lm = node.get("layoutMode")
    if lm in ("HORIZONTAL", "VERTICAL"):
        direction = "row" if lm == "HORIZONTAL" else "column"
        gap = node.get("itemSpacing") or node.get("gap") or 0
        rule = f".{_slug(name) or 'layout'} {{ display: flex; flex-direction: {direction}; gap: {gap}px; }}"
        if rule not in tokens.layout_rules:
            tokens.layout_rules.append(rule)

    for child in node.get("children") or []:
        _walk(child, tokens, name_prefix=name)


def _slug(text: str) -> str:
    import re
    s = re.sub(r"[^0-9A-Za-zА-Яа-яЁё _-]+", "", text or "").strip().lower()
    return re.sub(r"\s+", "-", s).replace("_", "-")[:40]


def extract_design_tokens(figma_json: dict | str) -> DesignTokens:
    """Walk a Figma-style document tree and extract compact tokens."""
    tokens = DesignTokens()
    if isinstance(figma_json, str):
        try:
            figma_json = json.loads(figma_json)
        except json.JSONDecodeError:
            return tokens
    if isinstance(figma_json, dict):
        # Accept {document: ...} or the document directly
        root = figma_json.get("document", figma_json)
        _walk(root, tokens)
    return tokens


# ═══════════════════════════════════════════════════════════════════════════════
# Tailwind mapping (Figma Console style, no vectors)
# ═══════════════════════════════════════════════════════════════════════════════

def _hex_rgb(hexv: str) -> tuple[int, int, int]:
    h = hexv.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (0, 0, 0)


def tailwind_class_for(hexv: str) -> str:
    """Nearest Tailwind color class for a hex value (Euclidean RGB)."""
    target = _hex_rgb(hexv)
    best, best_dist = "gray-500", 1e9
    for name, candidate in _TAILWIND_COLORS.items():
        c = _hex_rgb(candidate)
        dist = sum((a - b) ** 2 for a, b in zip(target, c, strict=False))
        if dist < best_dist:
            best, best_dist = name, dist
    return best


def map_to_tailwind(tokens: DesignTokens) -> dict[str, str]:
    """Map every extracted color hex to its nearest Tailwind class."""
    return {name: tailwind_class_for(hexv) for name, hexv in tokens.colors.items()}


def closest_key(query: str, candidates: list[str]) -> str | None:
    """String-distance match (difflib) — Figma Console's token lookup.

    Lets "color/primary/500" or "primary" find "primary-500" without
    exact-name agreement between design and code.
    """
    if not candidates:
        return None
    match = difflib.get_close_matches(query.lower(), [c.lower() for c in candidates], n=1, cutoff=0.4)
    if not match:
        return None
    return candidates[[c.lower() for c in candidates].index(match[0])]


# ═══════════════════════════════════════════════════════════════════════════════
# Token math
# ═══════════════════════════════════════════════════════════════════════════════

def estimate_design_savings(figma_json: dict | str) -> dict[str, Any]:
    """Honest savings: raw design JSON tokens vs compact token block."""
    from .core import count_tokens

    raw = figma_json if isinstance(figma_json, str) else json.dumps(figma_json, ensure_ascii=False)
    tokens = extract_design_tokens(figma_json)
    block = tokens.render()
    raw_tokens = count_tokens(raw)
    block_tokens = count_tokens(block)
    saved = raw_tokens - block_tokens
    return {
        "raw_tokens": raw_tokens,
        "token_block_tokens": block_tokens,
        "saved_tokens": saved,
        "savings_pct": round(100 * saved / max(1, raw_tokens), 1),
        "colors": len(tokens.colors),
        "typography": len(tokens.typography),
        "layout_rules": len(tokens.layout_rules),
    }


__all__ = [
    "DesignTokens",
    "closest_key",
    "estimate_design_savings",
    "extract_design_tokens",
    "map_to_tailwind",
    "tailwind_class_for",
]
