"""schema_compressor — compact database schema for LLM context.

Reverse-engineered from:
- Supabase MCP / postgres MCP: database tools that introspect the
  schema and hand the LLM only the table/column/type skeleton, so the
  model can write correct SQL without a giant dump of the whole DB.

The problem it solves:
    A real DB schema dump (all tables, all columns, indexes, defaults,
    comments) is 5-20k tokens. The model almost always needs only the
    table names + column names + keys. We strip the rest by default
    and keep the shape that makes SQL generation correct.

Pure stdlib. Two inputs supported:
    - dict schema: {"tables": {name: {"columns": [{name, type, nullable,
      default, pk, fk, comment}], "indexes": [...], "rows": int}}}
    - SQL DDL string: CREATE TABLE ... (parsed with a small regex)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# Column line: name + everything up to the trailing comma (or line end).
_DDL_COL_RE = re.compile(
    r"^\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s+(.+?)\s*,?$",
    re.IGNORECASE,
)

# Keywords that end the type and start the constraint tail.
_CONSTRAINT_KW = re.compile(
    r"\b(PRIMARY\s+KEY|NOT\s+NULL|NULL|UNIQUE|REFERENCES|DEFAULT|CHECK|"
    r"CONSTRAINT|COLLATE|GENERATED|COMMENT)\b",
    re.IGNORECASE,
)

_COMMON_TYPES = {
    "integer", "int", "bigint", "smallint", "serial", "bigserial",
    "numeric", "decimal", "real", "double precision", "float",
    "text", "varchar", "char", "uuid", "bool", "boolean", "date",
    "time", "timestamp", "timestamptz", "json", "jsonb", "bytea",
    "interval", "money", "inet", "citext", "enum",
}


@dataclass
class ColumnInfo:
    name: str
    type: str = ""
    nullable: bool = True
    default: str = ""
    pk: bool = False
    fk: str = ""           # "table.column" when foreign key
    comment: str = ""


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    rows: int = 0
    indexes: list[str] = field(default_factory=list)


def parse_ddl(ddl: str) -> dict[str, Any]:
    """Parse a simple CREATE TABLE DDL into the dict schema format.

    Handles one column per line (the common formatting in exports and
    migrations). Ignores constraint lines it can't parse.
    """
    schema: dict[str, Any] = {"tables": {}}
    current: str | None = None

    for raw in ddl.splitlines():
        line = raw.strip().rstrip(",")
        m = re.match(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?`?([\w.]+)`?\s*\(", line, re.IGNORECASE)
        if m:
            current = m.group(1).split(".")[-1]
            schema["tables"][current] = {"columns": [], "indexes": [], "rows": 0}
            continue
        if not current or not line:
            continue
        if line.startswith(")"):
            current = None
            continue
        if re.match(r"(CONSTRAINT|PRIMARY KEY|FOREIGN KEY|UNIQUE|INDEX|KEY)\b", line, re.IGNORECASE):
            # composite/table-level constraints — keep as a compact note
            if "PRIMARY KEY" in line.upper():
                schema["tables"][current]["indexes"].append("pk")
            elif "INDEX" in line.upper() or "KEY" in line.upper():
                schema["tables"][current]["indexes"].append("idx")
            continue

        cm = _DDL_COL_RE.match(line)
        if cm:
            rest = cm.group(2).strip()
            # split type from constraint tail at the first constraint keyword
            m = _CONSTRAINT_KW.search(rest)
            if m and m.start() > 0:
                col_type, tail = rest[:m.start()].strip(), rest[m.start():].upper()
            else:
                col_type, tail = rest, ""
            col: dict[str, Any] = {"name": cm.group(1), "type": col_type.lower()}
            col["nullable"] = "NOT NULL" not in tail
            col["pk"] = "PRIMARY KEY" in tail
            if "REFERENCES" in tail:
                col["fk"] = tail.split("REFERENCES", 1)[1].strip().split()[0]
            schema["tables"][current]["columns"].append(col)
    return schema


# ═══════════════════════════════════════════════════════════════════════════════

def compress_schema(schema: dict | str) -> list[TableInfo]:
    """Normalize either input (dict or DDL string) into TableInfo list."""
    if isinstance(schema, str):
        schema = parse_ddl(schema)
    tables = schema.get("tables", schema if isinstance(schema, dict) else {})

    out: list[TableInfo] = []
    for name, t in tables.items():
        ti = TableInfo(
            name=str(name),
            rows=int(t.get("rows", 0) or 0),
            indexes=list(t.get("indexes", []) or []),
        )
        for c in t.get("columns", []) or []:
            if isinstance(c, str):
                ti.columns.append(ColumnInfo(name=c))
                continue
            ti.columns.append(ColumnInfo(
                name=str(c.get("name", "")),
                type=str(c.get("type", "")).lower(),
                nullable=bool(c.get("nullable", True)),
                default=str(c.get("default", "") or ""),
                pk=bool(c.get("pk", False)),
                fk=str(c.get("fk", "") or ""),
                comment=str(c.get("comment", "") or ""),
            ))
        out.append(ti)
    return out


def render_schema(schema: dict | str, *, types: bool = True,
                  comments: bool = False, rows: bool = True,
                  max_tables: int | None = None) -> str:
    """Compact schema block for an LLM prompt (Supabase-style skeleton).

    Defaults strip indexes/defaults/comments — the noise that costs
    tokens without helping SQL generation.
    """
    tables = compress_schema(schema)
    if max_tables:
        tables = tables[:max_tables]

    lines: list[str] = []
    for t in tables:
        cols = []
        for c in t.columns:
            part = c.name
            if types and c.type:
                part += f":{c.type}"
            if c.pk:
                part = f"🔑{part}"
            if c.fk:
                part += f"→{c.fk}"
            if not c.nullable and c.type not in ("",):
                part += "!"
            if comments and c.comment:
                part += f" // {c.comment[:40]}"
            cols.append(part)
        if not cols:
            cols = ["_"]
        line = f"📋 {t.name}"
        if rows:
            line += f" (~{t.rows:,} rows)" if t.rows else ""
        lines.append(line)
        lines.append("    " + ", ".join(cols))
        if t.indexes and "pk" in t.indexes and not any(c.pk for c in t.columns):
            lines.append("    (composite PK)")
    return "\n".join(lines)


def schema_to_prompt_block(schema: dict | str, max_tokens: int = 900) -> str:
    """[DB schema] block ready to paste into a prompt, token-capped."""
    block = render_schema(schema)
    if not block:
        return ""
    # crude token cap ~4 chars/token
    if len(block) > max_tokens * 4:
        block = block[: max_tokens * 4]
    return "[DB schema]\n" + block + "\n[End schema]"


def estimate_schema_savings(schema: dict | str) -> dict[str, Any]:
    """Raw schema tokens vs compact block tokens."""
    from .core import count_tokens

    raw = schema if isinstance(schema, str) else json.dumps(schema, ensure_ascii=False)
    block = render_schema(schema)
    raw_tokens = count_tokens(raw)
    block_tokens = count_tokens(block)
    saved = raw_tokens - block_tokens
    return {
        "raw_tokens": raw_tokens,
        "compact_tokens": block_tokens,
        "saved_tokens": saved,
        "savings_pct": round(100 * saved / max(1, raw_tokens), 1),
    }


__all__ = [
    "ColumnInfo",
    "TableInfo",
    "compress_schema",
    "estimate_schema_savings",
    "parse_ddl",
    "render_schema",
    "schema_to_prompt_block",
]
