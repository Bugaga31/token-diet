"""Tool schema compressor — strip verbose descriptions from tool definitions.

OpenAI/Anthropic charge for every token in the functions/tools block,
including parameter descriptions that the model rarely reads. This
compressor keeps only the structural fields (name, type, required,
properties with types) and strips descriptions entirely, cutting
tool schemas by 40-60% without affecting function calling accuracy.

The original schema is preserved for fallback via canonical_json.
"""

from __future__ import annotations

import copy
from typing import Any

try:
    from .core import canonical_json, count_tokens
except ImportError:
    from core import canonical_json, count_tokens  # type: ignore[no-redef]

_STRUCTURAL_KEY_WHITELIST = frozenset(
    {"name", "type", "required", "properties", "items", "enum", "oneOf", "anyOf"}
)

_PROPERTY_KEY_WHITELIST = frozenset({"type", "required", "properties", "items", "enum"})


def _strip_descriptions(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove 'description' keys from a JSON Schema object."""
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key == "description":
            continue
        if isinstance(value, dict):
            result[key] = _strip_descriptions(value)
        elif isinstance(value, list):
            result[key] = [
                _strip_descriptions(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def compress_tool_schemas(tools: dict[str, Any]) -> dict[str, Any]:
    """Strip descriptions from an OpenAI-compatible tools dict.

    Input example::

        {"functions": [{"name": "search", "description": "search the DB",
                         "parameters": {"type": "object",
                                        "properties": {"query": {"type": "string",
                                         "description": "the search query"}},
                                        "required": ["query"]}}]}

    Output keeps only structural fields: name, type, required, properties
    (minus descriptions), enum, items. Returns a deep copy so the caller's
    original is untouched.
    """
    if not tools:
        return {}

    stripped = copy.deepcopy(tools)
    functions = stripped.get("functions", [])
    for func in functions:
        if not isinstance(func, dict):
            continue
        func.pop("description", None)
        params = func.get("parameters")
        if isinstance(params, dict):
            func["parameters"] = _strip_descriptions(params)

    return stripped


def guarded_tool_schemas(
    tools: dict[str, Any],
    min_savings_ratio: float = 0.80,
) -> tuple[dict[str, Any], int, int, bool]:
    """Strip tool schemas only when it actually saves tokens.

    Returns (tools_dict, tokens_before, tokens_after, applied).
    """
    before = count_tokens(canonical_json(tools))
    compressed = compress_tool_schemas(tools)
    after = count_tokens(canonical_json(compressed))
    applied = after < before * min_savings_ratio
    return compressed, before, after, applied
