"""AuditChain — hash-chain журнал решений (реверс-инжиниринг block/buzz).

Взято из Buzz (Block Inc., Apache 2.0), крейт buzz-audit:
- SHA-256 цепочка: каждая запись хранит hash, покрывающий prev_hash — подделка
  любой записи ломает все последующие хэши;
- канонический JSON (рекурсивная сортировка ключей) — хэш воспроизводим
  на любой машине;
- presence-теги — отличить None от пустой строки (не дают коллизий);
- timestamp на микросекундной точности (урок Buzz: «хэшируй то, что хранишь» —
  иначе verify_chain вечно ломается из-за наносекунд);
- verify_chain() проходит цепочку и ловит любую подделку.

Зачем нам: принцип «без вранья». Каждый прогноз, решение и сделка
записываются в цепочку. Подделать задним числом невозможно — verify()
доказывает целостность. Прогнозы нельзя «поправить» после того как рынок
показал правду.

100% офлайн, только hashlib+json, 0 LLM-вызовов, файл-хранилище JSONL.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

GENESIS_HASH = b"\x00" * 32  # 32 нулевых байта — для первой записи цепочки

# Известные действия. Любая строка допустима, но эти — типовые.
KNOWN_ACTIONS = {
    "prediction_made",    # сделан прогноз (тикер -> цель к дате)
    "decision_made",      # принято решение (купить/продать/держать/ждать)
    "trade_executed",     # сделка исполнена
    "position_opened",    # позиция открыта
    "position_closed",    # позиция закрыта
    "memory_written",     # записано в память
    "config_changed",     # изменена конфигурация
    "goal_set",           # поставлена цель
    "lesson_learned",     # записан урок
    "external_event",     # важное внешнее событие (новость, геополитика)
}


def _now_storage_precision() -> str:
    """Текущее время в RFC3339 UTC, усечённое до микросекунд.

    Урок Buzz: если хэшировать наносекунды, а хранить микросекунды —
    verify_chain() никогда не сойдётся. Поэтому СРАЗУ берём хранimую точность.
    """
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y-%m-%dT%H:%M:%S')}.{now.microsecond:06d}Z"


def canonical_json(value: Any) -> str:
    """JSON с рекурсивно отсортированными ключами — детерминированный хэш.

    Эквивалент BTreeMap-сериализации из buzz-audit: {'z':1,'a':2} и
    {'a':2,'z':1} дают ОДНУ строку.
    """
    if isinstance(value, dict):
        items = sorted(value.items())
        body = ",".join(
            f"{json.dumps(str(k))}:{canonical_json(v)}" for k, v in items
        )
        return "{" + body + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(str(value))


def _presence_tag(value: Optional[str]) -> bytes:
    """1 байт-тег: 1 — значение есть, 0 — None. Не даёт Some('') == None."""
    return b"\x01" if value is not None else b"\x00"


class AuditEntry:
    """Одна запись цепочки. hash покрывает ВСЕ поля кроме самого hash.

    chain_id ведёт хэш (как community_id в buzz) — запись из одной цепочки
    не может быть переиграна в другой.
    """

    __slots__ = ("chain_id", "seq", "hash", "prev_hash", "action", "actor",
                 "object_id", "detail", "created_at")

    def __init__(self, chain_id: str, seq: int, hash_: str, prev_hash: Optional[str],
                 action: str, actor: Optional[str], object_id: Optional[str],
                 detail: dict, created_at: str):
        self.chain_id = chain_id
        self.seq = seq
        self.hash = hash_
        self.prev_hash = prev_hash
        self.action = action
        self.actor = actor
        self.object_id = object_id
        self.detail = detail if detail is not None else {}
        self.created_at = created_at

    def compute_hash(self) -> str:
        """SHA-256 по полям в фиксированном порядке (как buzz-audit)."""
        h = hashlib.sha256()
        h.update(self.chain_id.encode())                 # привязка цепочки — первой
        h.update(self.seq.to_bytes(8, "big"))           # seq, big-endian
        h.update(self.created_at.encode())              # уже storage precision
        h.update(self.action.encode())
        h.update(_presence_tag(self.actor))
        if self.actor is not None:
            h.update(self.actor.encode())
        h.update(_presence_tag(self.object_id))
        if self.object_id is not None:
            h.update(self.object_id.encode())
        h.update(canonical_json(self.detail).encode())
        if self.prev_hash is not None:
            h.update(bytes.fromhex(self.prev_hash))
        else:
            h.update(GENESIS_HASH)
        return h.hexdigest()

    def to_line(self) -> str:
        return json.dumps({
            "chain_id": self.chain_id,
            "seq": self.seq,
            "hash": self.hash,
            "prev_hash": self.prev_hash,
            "action": self.action,
            "actor": self.actor,
            "object_id": self.object_id,
            "detail": self.detail,
            "created_at": self.created_at,
        }, sort_keys=True)

    @classmethod
    def from_line(cls, line: str) -> "AuditEntry":
        d = json.loads(line)
        return cls(
            chain_id=d.get("chain_id", ""),
            seq=int(d["seq"]),
            hash_=d["hash"],
            prev_hash=d.get("prev_hash"),
            action=d["action"],
            actor=d.get("actor"),
            object_id=d.get("object_id"),
            detail=d.get("detail") or {},
            created_at=d["created_at"],
        )

    def summary(self) -> str:
        tail = f" [{self.object_id}]" if self.object_id else ""
        return (f"#{self.seq} {self.action}{tail} @ {self.created_at} "
                f"hash={self.hash[:12]}… prev={self.prev_hash[:12] if self.prev_hash else '—'}…")


def default_chain_path() -> Path:
    """Путь по умолчанию: там же, где память Obsidian (vault)."""
    try:
        from token_diet.memory_cli import vault_path
        return vault_path() / "audit_chain.jsonl"
    except Exception:
        return Path("~/token-diet-memory/audit_chain.jsonl").expanduser()


class AuditChain:
    """Append-only hash-chain журнал на JSONL-файле с потокобезопасностью."""

    def __init__(self, path: Optional[Path] = None, chain_id: Optional[str] = None):
        self.path = Path(path) if path else default_chain_path()
        if chain_id is None:
            # стабильная привязка к месту хранения (как community_id в buzz)
            chain_id = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:16]
        self.chain_id = chain_id
        self._lock = threading.Lock()

    # ── чтение ────────────────────────────────────────────────────────────
    def _load(self) -> list[AuditEntry]:
        if not self.path.exists():
            return []
        entries: list[AuditEntry] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(AuditEntry.from_line(line))
                except (ValueError, json.JSONDecodeError):
                    continue  # битая строка не роняет чтение (но verify её поймает)
        return entries

    def entries(self, limit: Optional[int] = None) -> list[AuditEntry]:
        with self._lock:
            return self._load()[-limit:] if limit else self._load()

    # ── запись ────────────────────────────────────────────────────────────
    def log(self, action: str, detail: Optional[dict] = None,
            actor: Optional[str] = None, object_id: Optional[str] = None) -> AuditEntry:
        """Добавить запись в цепочку. Возвращает созданную запись."""
        with self._lock:
            head = self._load()[-1] if self.path.exists() and self._load() else None
            seq = (head.seq + 1) if head else 1
            entry = AuditEntry(
                chain_id=self.chain_id,
                seq=seq,
                hash_="",
                prev_hash=head.hash if head else None,
                action=action,
                actor=actor,
                object_id=object_id,
                detail=detail or {},
                created_at=_now_storage_precision(),
            )
            entry.hash = entry.compute_hash()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(entry.to_line() + "\n")
            return entry

    # ── удобные обёртки ───────────────────────────────────────────────────
    def predict(self, ticker: str, target: float, by: str,
                note: str = "", actor: str = "assistant") -> AuditEntry:
        """Записать прогноз: «тикер дойдёт до target к by». Не подделать."""
        return self.log(
            action="prediction_made",
            detail={"ticker": ticker.upper(), "target": target,
                    "by": by, "note": note, "asset_class": "stock"},
            actor=actor,
            object_id=f"{ticker.upper()}->{target}",
        )

    def decision(self, what: str, reason: str = "",
                 actor: str = "assistant") -> AuditEntry:
        return self.log(
            action="decision_made",
            detail={"what": what, "reason": reason},
            actor=actor,
        )

    # ── проверка целостности ──────────────────────────────────────────────
    def verify(self) -> tuple[bool, Optional[int]]:
        """Проверить всю цепочку. Возвращает (ok, seq_первой_проблемы).

        Ловит: разрыв prev_hash-связи, подмену любого поля, вставку чужой
        записи, битые строки. Пустая цепочка -> (False, None) как в buzz.
        """
        with self._lock:
            entries = self._load()
            if not entries:
                return False, None
            prev_hash: Optional[str] = None
            for e in entries:
                if e.chain_id != self.chain_id:
                    return False, e.seq  # запись из чужой цепочки (replay)
                if prev_hash is not None and e.prev_hash != prev_hash:
                    return False, e.seq  # разорвана связь
                if e.compute_hash() != e.hash:
                    return False, e.seq  # подделан контент
                prev_hash = e.hash
            return True, None

    def tamper_evidence(self) -> str:
        ok, bad = self.verify()
        n = len(self.entries())
        if not ok:
            return f"⚠ ЦЕПОЧКА ПОВРЕЖДЕНА: первая проблема в записи #{bad} (всего {n})"
        return f"✓ Цепочка цела: {n} записей, хэши сходятся, подделка невозможна"

    def last(self, n: int = 5) -> list[AuditEntry]:
        return self.entries(limit=n)[::-1]
