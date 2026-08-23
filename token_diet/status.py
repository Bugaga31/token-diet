"""Status — единая картина: портфель + P&L + сигналы + решение + защита.

Раньше всё было разбросано: портфель отдельно, журнал P&L отдельно,
сигналы отдельно, вотчдог отдельно. Этот модуль сводит ВСЁ в один отчёт —
чтобы управлять «всем, что дано» одной командой, а не десятком вызовов.

Чистая сборка из существующих кусков, 0 LLM-вызовов.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .commander import Decision, decide
from .pnl_journal import history, snapshot


def full_status(
    inv,
    tickers: list[str],
    target: float,
    risk_pct: float = 2.0,
    *,
    alert_path: str | Path = "/tmp/gmkn_alert.txt",
    journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Собрать всё в один dict: портфель + P&L + топ-сигналы + решение + тревога."""
    snap = snapshot(inv)
    hist = history(journal_path, target)
    signals: list[dict[str, Any]] = []
    for t in tickers:
        try:
            s = inv.full_signal(t)
            if s:
                s["lot_size"] = inv.lot_size(t)
                signals.append(s)
        except Exception:
            continue
    decision = decide(signals, float(snap.get("cash", 0)), risk_pct)
    alert = ""
    ap = Path(alert_path)
    if ap.exists():
        try:
            alert = ap.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return {
        "portfolio": snap,
        "pnl": hist,
        "top_signals": decision.ranked[:3],
        "decision": decision,
        "alert": alert,
    }


def render_status(st: dict[str, Any]) -> str:
    """Текстовый отчёт по результату full_status."""
    snap = st.get("portfolio", {})
    hist = st.get("pnl", {})
    decision: Decision = st.get("decision")  # type: ignore[assignment]
    alert = st.get("alert", "")

    lines = ["═══ СТАТУС ═══"]
    lines.append(f"Портфель: {snap.get('total')} ₽ (кэш {snap.get('cash')} ₽)")
    for t, p in snap.get("positions", {}).items():
        lines.append(f"  {t}: {p['shares']} акций @ {p['price']} = {p['value']} ₽")

    if hist.get("records"):
        lines.append(f"P&L: {hist['start']} → {hist['current']} ₽ "
                     f"({hist['pnl']:+} ₽, {hist['pnl_pct']:+}%)")
        if "recovery_pct" in hist:
            lines.append(f"Возврат к цели {hist.get('target')} ₽: {hist['recovery_pct']}%")

    if decision is not None:
        lines.append(f"Решение: {decision.action.upper()}")
        body = decision.render()
        lines.extend(f"  {x}" for x in body.splitlines()[:4])

    if alert:
        lines.append(f"Тревога: {alert.splitlines()[0]}")
    else:
        lines.append("Тревога: нет")

    return "\n".join(lines)
