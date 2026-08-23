"""Tests for CLI alias commands: diet / recall / portfolio / status / eyes / server / batch."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.cli import main  # noqa: E402


# ── diet: сжатие текста ───────────────────────────────────────────────────────


def test_diet_reports_savings(capsys):
    text = "You are a helpful assistant. Please be polite and courteous at all times."
    assert main(["diet", text]) == 0
    out = capsys.readouterr().out
    assert "[диета]" in out
    assert "токенов" in out


def test_diet_joins_multiword_args(capsys):
    assert main(["diet", "hello", "world"]) == 0
    assert "[диета]" in capsys.readouterr().out


def test_diet_aggressive_flag(capsys):
    assert main(["diet", "You are a helpful assistant.", "--aggressive"]) == 0
    assert "агрессивно" in capsys.readouterr().out


# ── recall: поиск по памяти ───────────────────────────────────────────────────


def test_recall_returns_zero(capsys):
    assert main(["recall", "несуществующий-запрос-xyz"]) == 0
    out = capsys.readouterr().out
    assert out.strip()  # что-то напечатано (результаты или «ничего»)


# ── portfolio: живой портфель ─────────────────────────────────────────────────


def test_portfolio_reports_when_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("token_diet.cli._portfolio_block", lambda: "")
    assert main(["portfolio"]) == 1
    assert "недоступен" in capsys.readouterr().out


def test_portfolio_prints_block(monkeypatch, capsys):
    monkeypatch.setattr("token_diet.cli._portfolio_block",
                        lambda: "\n📊 ПОРТФЕЛЬ:\n  TEST: 1 шт @ 1.00 = 1 ₽")
    assert main(["portfolio"]) == 0
    assert "TEST" in capsys.readouterr().out


# ── status: алиас snapshot ────────────────────────────────────────────────────


def test_status_calls_snapshot_full(monkeypatch):
    called = {}

    def fake_snapshot(brief):
        called["brief"] = brief
        return 0

    monkeypatch.setattr("token_diet.cli._snapshot", fake_snapshot)
    assert main(["status"]) == 0
    assert called["brief"] is False


# ── eyes: зрение через OmniRoute ──────────────────────────────────────────────


def test_eyes_screen_question(monkeypatch, capsys):
    monkeypatch.setattr("token_diet.omni_eyes.see",
                        lambda question, model=None: f"видел: {question}")
    assert main(["eyes", "что на экране?"]) == 0
    assert "видел: что на экране?" in capsys.readouterr().out


def test_eyes_image_uses_see_image(monkeypatch, capsys):
    monkeypatch.setattr("token_diet.omni_eyes.see_image",
                        lambda path, question="?", model=None: f"img:{path}")
    assert main(["eyes", "опиши", "--image", "photo.png"]) == 0
    assert "img:photo.png" in capsys.readouterr().out


def test_eyes_passes_model(monkeypatch, capsys):
    seen = {}

    def fake_see(question, model=None):
        seen["model"] = model
        return "ок"

    monkeypatch.setattr("token_diet.omni_eyes.see", fake_see)
    assert main(["eyes", "--model", "agentrouter/claude-opus-5"]) == 0
    assert seen["model"] == "agentrouter/claude-opus-5"


# ── server / batch ────────────────────────────────────────────────────────────


def test_server_alias_registered(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["server", "--help"])
    assert exc.value.code == 0
    assert "serve" in capsys.readouterr().out


def test_batch_estimates_savings(capsys):
    assert main(["batch"]) == 0
    assert "экономия" in capsys.readouterr().out


def test_batch_demo(capsys):
    assert main(["batch", "--demo"]) == 0
    assert capsys.readouterr().out.strip()
