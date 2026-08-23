"""Tests for design_tokens — Figma-style token extraction + Tailwind mapping."""
import json

from token_diet.design_tokens import (
    closest_key,
    estimate_design_savings,
    extract_design_tokens,
    map_to_tailwind,
    tailwind_class_for,
)

FIGMA = {
    "document": {
        "name": "LoginScreen",
        "children": [
            {
                "name": "Card",
                "type": "FRAME",
                "layoutMode": "VERTICAL",
                "itemSpacing": 12,
                "paddingLeft": 24,
                "cornerRadius": 8,
                "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}}],
                "children": [
                    {
                        "name": "Title",
                        "type": "TEXT",
                        "style": {"fontFamily": "Inter", "fontSize": 24, "fontWeight": 700},
                        "fills": [{"type": "SOLID", "color": {"r": 0.06, "g": 0.09, "b": 0.16}}],
                    },
                    {
                        "name": "PrimaryButton",
                        "type": "FRAME",
                        "layoutMode": "HORIZONTAL",
                        "itemSpacing": 8,
                        "fills": [{"type": "SOLID", "color": {"r": 0.23, "g": 0.51, "b": 0.96}}],
                    },
                ],
            }
        ],
    }
}


def test_extract_colors():
    t = extract_design_tokens(FIGMA)
    assert len(t.colors) >= 3
    # r=0.06 g=0.09 b=0.16 → #0f1729
    assert t.colors.get("title-color") == "#0f1729"


def test_extract_layout_rules():
    t = extract_design_tokens(FIGMA)
    flex = [r for r in t.layout_rules if "flex-direction: column" in r]
    assert flex, "should extract a vertical layout rule"


def test_spacing_and_radii():
    t = extract_design_tokens(FIGMA)
    assert 12 in t.spacing
    assert 8 in t.radii


def test_tailwind_mapping():
    t = extract_design_tokens(FIGMA)
    mapping = map_to_tailwind(t)
    assert set(mapping.keys()) == set(t.colors.keys())
    assert all(isinstance(v, str) and v for v in mapping.values())
    assert tailwind_class_for("#ffffff") == "white"


def test_closest_key_fuzzy():
    assert closest_key("primary", ["primary-500", "secondary-500"]) == "primary-500"


def test_savings():
    s = estimate_design_savings(FIGMA)
    assert s["token_block_tokens"] < s["raw_tokens"]
    assert s["savings_pct"] > 0


def test_from_json_string():
    t = extract_design_tokens(json.dumps(FIGMA))
    assert len(t.colors) >= 3
