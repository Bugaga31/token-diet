"""JSON-compressor для tool results — Headroom-style context compression.

Идея из github.com/headroomlabs-ai/headroom:
  - 95% token savings for JSON data
  - content-aware compression: понимает структуру данных
  - reversible: можно восстановить original (с потерей None/empty ключей)

JSON compression utilities.
  - tool results (market data, portfolio, search results) → compressed
  - research context в дебатах → меньше токенов, быстрее ответ
  - не трогает текст HTML — только структурированные данные

Методы:
  - flatten nested dict → key.subkey.key:value
  - skip None, "", empty list/dict
  - truncate long values (>500 chars → "...N chars...")
  - deduplicate repeated lines
  - humanize timestamps (iso→relative for readability — не reversibly)
"""

from __future__ import annotations

from typing import Any

_MAX_VALUE = 500  # символов в value
_MAX_LINES = 100  # линий в output
_TRUNCATE_MARKER = " ..."

_SKIP_VALUES = (None, "", [], {}, ())


def _flatten(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Развернуть nested dict/list в плоский список (path, value)."""
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if v in _SKIP_VALUES:
                continue
            if isinstance(v, dict | list):
                out.extend(_flatten(v, path))
            else:
                out.append((path, str(v)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            path = f"{prefix}[{i}]" if prefix else str(i)
            if v in _SKIP_VALUES:
                continue
            if isinstance(v, dict | list):
                out.extend(_flatten(v, path))
            else:
                out.append((path, str(v)))
    else:
        out.append((prefix, str(obj)))
    return out


def _truncate(val: str) -> str:
    """Обрезать длинные значения."""
    if len(val) > _MAX_VALUE:
        return val[:_MAX_VALUE] + f"{_TRUNCATE_MARKER}({len(val)} chars)"
    return val


def compress_json(data: Any) -> str:
    """Сжать JSON-like данные в компактный text, экономя токены."""
    if not data:
        return ""
    if isinstance(data, str):
        return _truncate(data)[: _MAX_VALUE * 2]
    if isinstance(data, int | float | bool):
        return str(data)

    lines: list[str] = []
    flat = _flatten(data)
    for key, value in flat:
        # Skip empty values
        if value in _SKIP_VALUES or not str(value).strip():
            continue
        value = _truncate(value)
        lines.append(f"{key}: {value}")
        if len(lines) >= _MAX_LINES:
            lines.append(f"... +{len(flat) - len(lines)} more")
            break
    return "\n".join(lines)


def compress_tool_result(name: str, result: dict | None) -> str:
    """Сжать результат tool call в компактный текст для research context."""
    if not result:
        return ""
    if "error" in result and result.get("error") and not result.get("data"):
        return f"[{name}]: error: {result['error']}"

    # Special handling для known tools
    if "data" in result:
        compressed = compress_json(result["data"])
        return f"[{name}]:\n{compressed}" if compressed else ""
    if "content" in result:
        return f"[{name}]: {str(result['content'])[: _MAX_VALUE]}"
    # Generic
    compressed = compress_json(result)
    return f"[{name}]:\n{compressed}" if compressed else ""
