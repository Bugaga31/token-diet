"""Tests for schema_compressor — Supabase-style compact DB schema."""
from token_diet.schema_compressor import (
    compress_schema,
    estimate_schema_savings,
    parse_ddl,
    render_schema,
    schema_to_prompt_block,
)

SCHEMA = {
    "tables": {
        "users": {
            "columns": [
                {"name": "id", "type": "uuid", "pk": True, "nullable": False},
                {"name": "email", "type": "varchar", "nullable": False},
                {"name": "created_at", "type": "timestamptz", "default": "now()"},
                {"name": "plan", "type": "text", "comment": "free|pro|enterprise"},
            ],
            "indexes": ["idx_users_email"],
            "rows": 125000,
        },
        "orders": {
            "columns": [
                {"name": "id", "type": "bigint", "pk": True, "nullable": False},
                {"name": "user_id", "type": "uuid", "fk": "users.id", "nullable": False},
                {"name": "total", "type": "numeric"},
            ],
            "rows": 4200000,
        },
    }
}

DDL = """
CREATE TABLE users (
  id uuid PRIMARY KEY NOT NULL,
  email varchar NOT NULL,
  created_at timestamptz DEFAULT now(),
  plan text
);
CREATE TABLE orders (
  id bigint PRIMARY KEY NOT NULL,
  user_id uuid NOT NULL REFERENCES users(id),
  total numeric
);
"""


def test_parse_ddl():
    schema = parse_ddl(DDL)
    assert set(schema["tables"]) == {"users", "orders"}
    users = schema["tables"]["users"]
    assert len(users["columns"]) == 4
    assert users["columns"][0]["pk"] is True


def test_render_compact():
    block = render_schema(SCHEMA)
    assert "users" in block
    assert "🔑id:uuid" in block
    assert "→users.id" in block  # FK visible
    assert "idx_users_email" not in block  # noise stripped by default


def test_render_with_rows():
    block = render_schema(SCHEMA)
    assert "4,200,000" in block or "4200000" in block


def test_prompt_block_wrapped():
    block = schema_to_prompt_block(SCHEMA)
    assert block.startswith("[DB schema]")
    assert block.endswith("[End schema]")


def test_compress_accepts_both_inputs():
    assert len(compress_schema(SCHEMA)) == 2
    assert len(compress_schema(DDL)) == 2


def test_savings():
    s = estimate_schema_savings(SCHEMA)
    assert s["compact_tokens"] < s["raw_tokens"]
    assert s["savings_pct"] > 30
