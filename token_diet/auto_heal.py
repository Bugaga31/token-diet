"""auto_heal — самолечение по следам doctor.

Идея: `token-diet doctor` уже знает что сломано. Зачем человеку читать вывод
и гуглить? Пусть `auto_heal` сам предложит следующий приём через self_belief.persist
и попробует починить.

0 LLM, детерминирован. Использует то, что уже есть: _core_checks + persist.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cli import _core_checks
from .self_belief import persist


@dataclass
class HealPlan:
    broken: list[str]
    fixes: list[str]
    should_retry: bool
    summary: str

    def render(self) -> str:
        if not self.broken:
            return "🟢 Всё здорово — лечить нечего. Арсенал готов."
        lines = [f"🔧 AutoHeal: сломано {len(self.broken)}:"]
        for b in self.broken:
            lines.append(f"  ❌ {b}")
        lines.append("🩹 Следующие приёмы:")
        for f in self.fixes:
            lines.append(f"  → {f}")
        lines.append("💡 Запусти `token-diet doctor` снова после фиксов.")
        return "\n".join(lines)


def heal() -> HealPlan:
    """Проверить doctor и составить план лечения."""
    results = _core_checks()
    broken = [name for name, ok, msg in results if not ok]
    fixes: list[str] = []
    for name, ok, msg in results:
        if not ok:
            plan = persist(msg or name, attempt=1)
            fixes.append(f"{name}: {plan.next_move} (пример: {plan.diagnosis or 'нет деталей'})")
    return HealPlan(
        broken=broken,
        fixes=fixes,
        should_retry=len(broken) > 0,
        summary=f"сломано {len(broken)}/{len(results)}" if broken else "всё ок",
    )


def heal_cli() -> int:
    """CLI entry для `token-diet heal`."""
    plan = heal()
    print(plan.render())
    return 0 if not plan.broken else 1
