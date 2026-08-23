"""Tests for auto_heal — самолечение."""

from token_diet.auto_heal import heal


def test_heal_all_ok():
    plan = heal()
    # в норме всё ок (doctor 12/12)
    assert isinstance(plan.broken, list)
    assert isinstance(plan.fixes, list)
    assert "AutoHeal" in plan.render() or "здорово" in plan.render()


def test_heal_export_via_init():
    import token_diet
    assert hasattr(token_diet, "heal")
    assert hasattr(token_diet, "HealPlan")
