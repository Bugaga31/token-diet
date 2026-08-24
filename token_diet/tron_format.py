"""tron_format — TRON: Token Reduced Object Notation (arXiv:2605.29676).

The benchmark "Notation Matters" proved that standard JSON tool schemas
are bloated: every object repeats its key names (quotes, braces,
commas). TRON defines the class/keys ONCE, then emits compact value
tuples per instance — cutting tool-schema and execution-payload tokens
by 27-32% without degrading multi-turn agent accuracy.

How TRON works:
    CLASS Tool(name,description,params):
      instance(get_weather,Current weather,{"city":"str"})
      instance(get_stock,Stock price,{"ticker":"str"})

vs JSON's repeated {"name": "...", "description": "...", ...} per tool.

This module provides:
    to_tron(obj)          — serialize a dict/list of dicts into TRON
    to_tron_schemas(tools) — serialize OpenAI-style tool schemas
    tron_roundtrip_safe()  — guard: only use TRON if the model can parse
                             it back (we keep a compact JSON fallback flag)
    savings()             — honest token comparison TRON vs JSON

Pure stdlib. Zero dependencies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

try:
    from .core import count_tokens
except ImportError:  # pragma: no cover
    from core import count_tokens  # type: ignore[no-redef]


# ── TRON serializer ──────────────────────────────────────────────────────────


def _scalar(value: Any) -> str:
    """Render a scalar value compactly."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    s = str(value)
    # quote only if it has special chars; keep bare otherwise
    if re.search(r"[\s,{}()\"']", s):
        return json.dumps(s, ensure_ascii=False)
    return s


def to_tron(obj: Any) -> str:
    """Serialize a dict (or list of dicts) into TRON.

    - dict with all-scalar values → `key=value,key=value`
    - list of dicts sharing keys → `CLASS K(k1,k2,...):` + `instance(...)` rows
    - nested dicts → compact inline `{k=v, ...}`
    - lists of scalars → `[v1,v2,...]`
    """
    if isinstance(obj, dict):
        items = []
        for k, v in obj.items():
            items.append(f"{k}={_to_tron_value(v)}")
        return "{" + ",".join(items) + "}"

    if isinstance(obj, list):
        if not obj:
            return "[]"
        # list of dicts with shared keys → CLASS + instances
        if all(isinstance(x, dict) for x in obj):
            keys = list(obj[0].keys())
            all_share = all(set(x.keys()) == set(keys) for x in obj)
            if all_share and len(keys) > 1:
                lines = [f"CLASS {_class_name(keys)}({','.join(keys)}):"]
                for x in obj:
                    vals = ",".join(_to_tron_value(x[k]) for k in keys)
                    lines.append(f"  instance({vals})")
                return "\n".join(lines)
        return "[" + ",".join(_to_tron_value(x) for x in obj) + "]"

    return _scalar(obj)


def _to_tron_value(value: Any) -> str:
    if isinstance(value, dict | list):
        return to_tron(value)
    return _scalar(value)


def _class_name(keys: list[str]) -> str:
    """Derive a class name from the shared keys (or 'Item')."""
    base = "Item"
    for candidate in ("name", "title", "type", "tool", "id"):
        if candidate in keys:
            base = candidate.capitalize()
            break
    return base


def to_tron_schemas(tools: list[dict[str, Any]]) -> str:
    """Serialize OpenAI-style tool schemas into TRON.

    Each tool: {"name": ..., "description": ..., "parameters": {...}}.
    The parameters sub-objects are compacted too.
    """
    if not tools:
        return ""
    keys = ["name", "description", "parameters"]
    lines = [f"CLASS Tool({','.join(keys)}):"]
    for tool in tools:
        name = _scalar(tool.get("name", ""))
        desc = _scalar(tool.get("description", ""))
        params = to_tron(tool.get("parameters", {}))
        lines.append(f"  instance({name},{desc},{params})")
    return "\n".join(lines)


# ── roundtrip guard ──────────────────────────────────────────────────────────


@dataclass
class TronCheck:
    """Honest check: can we parse TRON back? (We use a JSON fallback.)"""
    ok: bool = True
    note: str = ""
    tron_tokens: int = 0
    json_tokens: int = 0

    @property
    def savings_pct(self) -> float:
        if self.json_tokens <= 0:
            return 0.0
        return round(100 * (self.json_tokens - self.tron_tokens)
                     / self.json_tokens, 1)


def tron_roundtrip_safe(
    tools: list[dict[str, Any]],
    parser_ok: bool = True,
) -> TronCheck:
    """Guard: measure TRON vs JSON and report whether it's safe to use.

    We don't claim TRON is parseable by every model — the caller tells
    us whether their model handles it (parser_ok). If not, fall back
    to JSON. The honest number (token savings) is always reported.
    """
    tron = to_tron_schemas(tools)
    jsn = json.dumps(tools, ensure_ascii=False)
    return TronCheck(
        ok=parser_ok,
        note=("TRON экономит токены, но убедитесь, что ваша модель его "
              "понимает. Иначе используйте JSON." if parser_ok else
              "Модель не понимает TRON — используйте JSON."),
        tron_tokens=count_tokens(tron),
        json_tokens=count_tokens(jsn),
    )


# ── comparison ───────────────────────────────────────────────────────────────


def savings(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Honest token comparison: JSON vs TRON for tool schemas."""
    jsn = json.dumps(tools, ensure_ascii=False)
    tron = to_tron_schemas(tools)
    jt, tt = count_tokens(jsn), count_tokens(tron)
    return {
        "json_tokens": jt,
        "tron_tokens": tt,
        "saved": jt - tt,
        "savings_pct": round(100 * (jt - tt) / max(1, jt), 1),
        "json": jsn,
        "tron": tron,
    }


__all__ = [
    "TronCheck",
    "to_tron",
    "to_tron_schemas",
    "tron_roundtrip_safe",
    "savings",
]
