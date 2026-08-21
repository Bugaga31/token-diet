"""Тесты hash-chain журнала (из Buzz, buzz-audit).

Проверяем главное: подделку поймать невозможно, цепочка не ломается
на пустых/None полях, хэш детерминирован.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.audit_chain import (  # noqa: E402
    AuditChain,
    AuditEntry,
    canonical_json,
    _now_storage_precision,
    GENESIS_HASH,
)


def make_chain(tmp_path: Path) -> AuditChain:
    return AuditChain(tmp_path / "audit_chain.jsonl")


# ── canonical_json ────────────────────────────────────────────────────────
def test_canonical_json_deterministic():
    a = {"z": 1, "a": 2, "m": 3}
    b = {"a": 2, "m": 3, "z": 1}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_recursive_sorted():
    a = {"b": {"y": 1, "x": [3, {"q": 1, "p": 2}]}, "a": 1}
    b = {"a": 1, "b": {"x": [3, {"p": 2, "q": 1}], "y": 1}}
    assert canonical_json(a) == canonical_json(b)


# ── базовая механика ─────────────────────────────────────────────────────
def test_first_entry_is_genesis(tmp_path):
    c = make_chain(tmp_path)
    e = c.log("decision_made", detail={"what": "test"}, actor="buffy")
    assert e.seq == 1
    assert e.prev_hash is None
    assert e.hash == e.compute_hash()
    assert len(bytes.fromhex(e.hash)) == 32


def test_chain_links(tmp_path):
    c = make_chain(tmp_path)
    e1 = c.log("decision_made", actor="buffy")
    e2 = c.log("prediction_made", actor="buffy")
    e3 = c.log("trade_executed", actor="buffy")
    assert e2.prev_hash == e1.hash
    assert e3.prev_hash == e2.hash
    ok, bad = c.verify()
    assert ok and bad is None


def test_verify_empty_chain_false(tmp_path):
    c = make_chain(tmp_path)
    ok, bad = c.verify()
    assert ok is False and bad is None  # как buzz: пустая цепочка -> False


# ── защита от подделки ───────────────────────────────────────────────────
def test_tamper_detected(tmp_path):
    c = make_chain(tmp_path)
    e1 = c.log("decision_made", detail={"what": "a"}, actor="buffy")
    e2 = c.log("decision_made", detail={"what": "b"}, actor="buffy")
    c.log("trade_executed", detail={"ticker": "PLZL"}, actor="buffy")

    # подделываем detail у e2 в файле
    path = c.path
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    forged = []
    for line in lines:
        d = json.loads(line)
        if d["seq"] == e2.seq:
            d["detail"]["what"] = "ИЗМЕНЕНО ЗАДНИМ ЧИСЛОМ"
            forged.append(json.dumps(d, sort_keys=True))
        else:
            forged.append(line)
    path.write_text("\n".join(forged) + "\n", encoding="utf-8")

    ok, bad = c.verify()
    assert ok is False
    assert bad == e2.seq


def test_chain_break_detected(tmp_path):
    """Разрыв связи (prev_hash не совпадает) тоже ловится."""
    c = make_chain(tmp_path)
    e1 = c.log("decision_made", actor="buffy")
    c.log("decision_made", actor="buffy")

    path = c.path
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    forged = []
    for line in lines:
        d = json.loads(line)
        if d["seq"] == 2:
            d["prev_hash"] = "ff" * 32  # чужая ссылка
            forged.append(json.dumps(d, sort_keys=True))
        else:
            forged.append(line)
    path.write_text("\n".join(forged) + "\n", encoding="utf-8")

    ok, bad = c.verify()
    assert ok is False
    assert bad == 2


def test_cross_chain_row_does_not_verify(tmp_path):
    """Чужая запись (хэш посчитан для другой цепочки) не проходит."""
    c1 = make_chain(tmp_path / "a")
    c2 = make_chain(tmp_path / "b")
    e1 = c1.log("prediction_made", detail={"ticker": "PLZL"}, actor="buffy")

    # вставляем запись c1 в цепочку c2
    entry = AuditEntry(chain_id=c1.chain_id, seq=1, hash_=e1.hash, prev_hash=None,
                       action="prediction_made", actor="buffy",
                       object_id=None, detail={"ticker": "PLZL"},
                       created_at=e1.created_at)
    c2.path.parent.mkdir(parents=True, exist_ok=True)
    c2.path.write_text(entry.to_line() + "\n", encoding="utf-8")
    ok, bad = c2.verify()
    assert ok is False
    assert bad == 1  # пересчитанный хэш не совпадает с сохранённым


# ── presence-теги и чувствительность ─────────────────────────────────────
def test_none_vs_empty_actor(tmp_path):
    c1 = make_chain(tmp_path / "none")
    c2 = make_chain(tmp_path / "empty")
    a = c1.log("decision_made", actor=None)
    b = c2.log("decision_made", actor="")
    assert a.hash != b.hash  # Some('') != None — тег присутствия


def test_sensitive_to_each_field(tmp_path):
    c = make_chain(tmp_path)
    base = c.log("decision_made", detail={"x": 1}, actor="buffy")
    assert base.compute_hash() != AuditEntry(
        chain_id=base.chain_id, seq=base.seq, hash_="", prev_hash=base.prev_hash,
        action="prediction_made", actor="buffy", object_id=None,
        detail={"x": 1}, created_at=base.created_at).compute_hash()
    assert base.compute_hash() != AuditEntry(
        chain_id=base.chain_id, seq=base.seq, hash_="", prev_hash=base.prev_hash,
        action="decision_made", actor="buffy", object_id=None,
        detail={"x": 2}, created_at=base.created_at).compute_hash()


# ── удобства ─────────────────────────────────────────────────────────────
def test_predict_logs_prediction(tmp_path):
    c = make_chain(tmp_path)
    e = c.predict("PLZL", 1400.0, by="19.08.2026", note="рост после отчёта")
    assert e.action == "prediction_made"
    assert e.detail["ticker"] == "PLZL"
    assert e.detail["target"] == 1400.0
    ok, _ = c.verify()
    assert ok


def test_storage_precision_timestamp():
    """Урок Buzz: хэшируем то, что храним — никаких наносекунд."""
    ts = _now_storage_precision()
    assert len(ts.split(".")[1].split("Z")[0]) == 6  # ровно микросекунды


def test_entries_and_last(tmp_path):
    c = make_chain(tmp_path)
    for i in range(3):
        c.log("decision_made", detail={"i": i}, actor="buffy")
    assert len(c.entries()) == 3
    assert [e.seq for e in c.last(2)] == [3, 2]  # свежие первыми
