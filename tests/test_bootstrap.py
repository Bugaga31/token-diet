"""Тесты самонастройки: bootstrap генерирует BOOTSTRAP.md без секретов,
а CLI регистрирует команды snapshot и bootstrap."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from token_diet import bootstrap


@pytest.fixture()
def fake_vault(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Подменяем путь к хранилищу на временную папку."""
    tmp = Path(tempfile.mkdtemp(prefix="td-bootstrap-"))
    (tmp / "CONTEXT_ALL.md").write_text("# контекст\n", encoding="utf-8")
    (tmp / "урок-рынок.md").write_text("урок: не лови нож\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "VAULT_DIR", tmp)
    monkeypatch.setattr(bootstrap, "BOOTSTRAP", tmp / "BOOTSTRAP.md")
    monkeypatch.setattr(bootstrap, "_git_remote", lambda: "https://github.com/example/token-diet")
    return tmp


def test_generate_creates_file(fake_vault: Path) -> None:
    """generate() создаёт BOOTSTRAP.md на диске."""
    path = bootstrap.generate()
    assert Path(path).exists()
    text = Path(path).read_text(encoding="utf-8")
    assert "САМОНАСТРОЙКА" in text


def test_generate_has_steps(fake_vault: Path) -> None:
    """В BOOTSTRAP.md есть все шаги самонастройки."""
    text = Path(bootstrap.generate()).read_text(encoding="utf-8")
    for step in ("Шаг 1", "Шаг 2", "Шаг 3", "Шаг 4", "Шаг 5", "Шаг 6", "Шаг 7"):
        assert step in text, f"нет шага {step}"


def test_generate_no_secrets(fake_vault: Path) -> None:
    """Никаких секретов в BOOTSTRAP.md: ни токенов, ни паролей, ни телефона."""
    text = Path(bootstrap.generate()).read_text(encoding="utf-8")
    patterns = [
        r"t\.[A-Za-z0-9_-]{20,}",       # токены T-Invest/Telegram
        r"sk-[A-Za-z0-9]{20,}",         # ключи API
        r"\+7\d{10}",                    # телефоны
        r"api[_ -]?hash",                # хеши
        r"password\s*[:=]\s*\S+",        # пароли
        r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b",  # бот-токены
    ]
    for pat in patterns:
        assert not re.search(pat, text, re.IGNORECASE), f"секрет по паттерну {pat}"


def test_generate_uses_remote(fake_vault: Path) -> None:
    """Ссылка на установку берётся из git remote (без хардкода вранья)."""
    text = Path(bootstrap.generate()).read_text(encoding="utf-8")
    assert "pip install git+https://github.com/example/token-diet" in text


def test_show_link_points_to_memory(fake_vault: Path) -> None:
    """show_link() упоминает CONTEXT_ALL.md — полный контекст памяти."""
    link = bootstrap.show_link()
    assert "CONTEXT_ALL.md" in link
    assert "САМОНАСТРОЙКА" in link


def test_cli_has_snapshot_command(fake_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI принимает snapshot: офлайн-снимок не падает даже без сети."""
    import sys
    from token_diet import cli

    # Мокаем тяжёлые сетевые вызовы — без них snapshot виснет >10с офлайн
    monkeypatch.setattr("token_diet.cli._portfolio_block", lambda: "📊 ПОРТФЕЛЬ: (мок)")
    # live_scan модуль в sys.modules, хотя token_diet.live_scan перетёрт функцией в __init__
    monkeypatch.setattr(sys.modules["token_diet.live_scan"], "scan_market", lambda: [])
    monkeypatch.setattr(sys.modules["token_diet.geopolitics"], "market_context_block", lambda: "гео: ок")

    # snapshot ловит все исключения внутри — должен вернуть 0 без сети
    rc = cli.main(["snapshot"])
    assert rc == 0


def test_cli_has_bootstrap_command(fake_vault: Path, capsys: pytest.CaptureFixture) -> None:
    """CLI принимает bootstrap --link и печатает путь к самонастройке."""
    from token_diet import cli

    rc = cli.main(["bootstrap", "--link"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "САМОНАСТРОЙКА" in out
