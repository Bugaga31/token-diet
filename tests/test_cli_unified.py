"""Tests for the unified CLI (token_diet/cli.py) — один инструмент на всё."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.cli import _core_checks, _self_test, main  # noqa: E402


def test_no_args_returns_zero_and_shows_arsenal(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Команды" in out


def test_doctor_all_green():
    results = _core_checks()
    broken = [name for name, ok, _ in results if not ok]
    assert not broken, f"сломано: {broken}"


def test_self_test_all_pass():
    assert _self_test() == 0


def test_panel_finds_money():
    assert main(["panel", "деньги"]) == 0


def test_panel_without_query():
    assert main(["panel"]) == 0


def test_unknown_command_exits_2():
    import pytest
    with pytest.raises(SystemExit) as exc:
        main(["nope-command"])
    assert exc.value.code == 2


def test_memo_passthrough_path():
    """memo path должен вернуть путь к хранилищу (память жива)."""
    assert main(["memo", "path"]) == 0


def test_core_checks_have_expected_modules():
    names = {n for n, _, _ in _core_checks()}
    for expected in ("core.count_tokens", "loss_router.compress",
                     "trading_brain", "model_army", "live_scan"):
        assert expected in names
