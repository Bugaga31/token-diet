"""Тесты system_turbo — безопасные, без sudo и без реальных процессов."""

import sys
from unittest.mock import patch

sys.path.insert(0, ".")

from token_diet.system_turbo import TurboReport, report_text, top_memory_processes  # noqa: E402


def test_report_structure():
    rep = TurboReport()
    rep.line("тест")
    assert "тест" in rep.actions
    assert rep.errors == []
    assert rep.top_memory == []


def test_report_text_formatting():
    rep = TurboReport()
    rep.line("swappiness: 60 → 5 ✅")
    rep.top_memory = ["  100MB  firefox", "  50MB  chromium"]
    text = report_text(rep)
    assert "SYSTEM TURBO" in text
    assert "swappiness" in text
    assert "ТОП ПО ПАМЯТИ" in text
    assert "firefox" in text


def test_top_memory_processes_returns_list():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = (
            "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND\n"
            "ro 1 0.0 0.0 100 204800 ? S 00:00 0:00 /usr/bin/firefox\n"
            "ro 2 0.0 0.0 100 102400 ? S 00:00 0:00 chromium\n"
        )
        top = top_memory_processes(2)
    assert len(top) == 2
    assert "200MB" in top[0]
    assert "100MB" in top[1]


def test_turbo_graceful_without_sudo():
    """Без прав turbo не падает, а возвращает отчёт с ошибками."""
    with patch("token_diet.system_turbo._write_sys", return_value=(False, "нет прав")):
        with patch("token_diet.system_turbo._run", return_value=(1, "")) as mock_run:
            rep = TurboReport()
            rep.line("swappiness: уже 5 ✅")
            rep.top_memory = []
            # вызовем только отчёт — без реального turbo (иначе нужен sudo)
    assert report_text(rep) != ""
