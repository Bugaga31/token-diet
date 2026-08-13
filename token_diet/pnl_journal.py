"""P&L Journal — честный учёт портфеля во времени.

Цель: не гадать «сколько мы вернули», а видеть реальную динамику.
Каждый вызов record() дописывает снапшот (кэш + позиции + итог) в JSONL-файл,
а history() считает: старт, текущее, P&L, сколько % возврата к цели.

Всё детерминированно, файл-хранилище, 0 LLM-вызовов.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def snapshot(inv) -> dict[str, Any]:
    """Снять состояние портфеля: кэш + позиции + итоговая стоимость.

    inv — объект с методом get_portfolio(), возвращающим позиции с полями
    ticker/quantity/current_price (как TinkoffInvest).
    """
    cash = 0.0
    positions: dict[str, dict[str, float]] = {}
    total = 0.0
    try:
        for p in inv.get_portfolio() or []:
            t = getattr(p, "ticker", "") or ""
            qty = float(getattr(p, "quantity", 0) or 0)
            price = float(getattr(p, "current_price", 0) or 0)
            value = qty * price
            if t.startswith("uid:") and "RUB" in getattr(p, "figi", ""):
                cash = value  # рублёвая позиция = свободные деньги
                continue
            positions[t] = {"shares": qty, "price": price, "value": round(value, 2)}
            total += value
    except Exception as e:
        return {"error": str(e)}

    total = round(total + cash, 2)
    return {
        "ts": _now(),
        "cash": round(cash, 2),
        "positions": positions,
        "total": total,
    }


def record(inv, path: str | Path | None = None) -> dict[str, Any]:
    """Дописать снапшот в журнал (JSONL). Возвращает записанный снапшот."""
    path = Path(path) if path else Path.home() / ".token-diet" / "pnl_journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    snap = snapshot(inv)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return snap


def history(path: str | Path | None = None, target: float | None = None) -> dict[str, Any]:
    """Прочитать журнал и посчитать динамику: старт, текущее, P&L, возврат к цели.

    target — целевая сумма (напр. стартовый капитал), чтобы считать % возврата.
    """
    path = Path(path) if path else Path.home() / ".token-diet" / "pnl_journal.jsonl"
    rows: list[dict] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass
    if not rows:
        return {"records": 0, "error": "журнал пуст"}

    first, last = rows[0], rows[-1]
    total = float(last.get("total", 0))
    start = float(first.get("total", 0))
    pnl = round(total - start, 2)
    result: dict[str, Any] = {
        "records": len(rows),
        "start": start,
        "current": total,
        "pnl": pnl,
        "pnl_pct": round(100 * pnl / start, 2) if start else 0.0,
        "first_ts": first.get("ts"),
        "last_ts": last.get("ts"),
    }
    if target and target > 0:
        result["target"] = target
        result["to_target"] = round(target - total, 2)
        result["recovery_pct"] = round(100 * (total - start) / (target - start), 2) if target != start else 0.0
    return result


def journal_block(inv, path: str | Path | None = None, target: float | None = None) -> str:
    """Текстовый отчёт: текущий снапшот + динамика для Obsidian/лога."""
    snap = snapshot(inv)
    hist = history(path, target)
    lines = [
        f"[P&L] итог портфеля: {snap.get('total')} ₽ (кэш {snap.get('cash')} ₽)",
    ]
    if "records" in hist and hist["records"]:
        lines.append(
            f"  динамика: {hist['start']} → {hist['current']} ₽ "
            f"({hist['pnl']:+} ₽, {hist['pnl_pct']:+}%)"
        )
        if "recovery_pct" in hist:
            lines.append(f"  возврат к цели: {hist['recovery_pct']}%")
    return "\n".join(lines)
