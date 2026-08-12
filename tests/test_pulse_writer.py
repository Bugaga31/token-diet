"""Tests for pulse_writer (offline, guardrails tested)."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet import pulse_writer as pw


def test_validate_text():
    assert pw.validate_text("")["ok"] is False
    assert pw.validate_text("   ")["ok"] is False
    assert pw.validate_text("x" * 4000)["ok"] is False
    assert pw.validate_text("смотри https://evil.com")["ok"] is False
    assert pw.validate_text("Норм пост про {$PLZL}")["ok"] is True


def test_validate_text_dedupe():
    r1 = pw.validate_text("пост один")
    assert r1["ok"] is True
    pw._STATE.posted_hashes.add(r1["hash"])
    r2 = pw.validate_text("пост один")
    assert r2["ok"] is False  # дедуп


def test_post_dry_run_no_network():
    r = pw.post_text("Просто текст без кук", dry_run=True)
    assert r["ok"] is True
    assert r["stage"] == "dry_run"
    assert "ничего не отправлено" in r["message"]


def test_post_requires_confirm_and_cookies(monkeypatch, tmp_path):
    # без кук и без dry_run → ошибка auth (не сеть)
    monkeypatch.setattr(
        pw, "load_cookies_from_firefox",
        lambda: (_ for _ in ()).throw(RuntimeError("нет профиля")),
    )
    r = pw.post_text("текст", dry_run=False)
    assert r["ok"] is False
    assert r["stage"] == "auth"

    # с куками, но без confirm → отказ
    monkeypatch.setattr(pw, "_http_post", lambda *a, **k: {"status": "Ok"})
    r = pw.post_text("текст", cookie="a=b", dry_run=False, confirm=False)
    assert r["stage"] == "confirm"
    assert r["ok"] is False


def test_post_real_sends(monkeypatch):
    sent = []
    monkeypatch.setattr(
        pw, "_http_post",
        lambda path, payload, cookie, timeout=15.0: (sent.append((path, payload)) or {"status": "Ok"}),
    )
    r = pw.post_text("{$PLZL} золото растёт", cookie="a=b", dry_run=False, confirm=True)
    assert r["ok"] is True
    assert r["stage"] == "posted"
    assert sent and sent[0][0] == "/post"
    assert "{$PLZL}" in sent[0][1]["text"]


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(pw.time, "time", lambda: 1000.0)
    pw._STATE.window_start = 999.0  # окно ещё не истекло
    pw._STATE.last_action_ts = 0.0  # чистим следы прошлых тестов
    pw._STATE.actions_in_window = 3  # уже лимит
    assert pw._check_rate_limit()["ok"] is False
    pw._STATE.actions_in_window = 1
    assert pw._check_rate_limit()["ok"] is True


def test_load_cookies_file_txt(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(
        "# Netscape HTTP Cookie File\n"
        ".tinkoff.ru\tTRUE\t/\tFALSE\t0\tsid\tabc123\n"
        ".tbank.ru\tTRUE\t/\tTRUE\t0\tauth\ttok456\n"
    )
    c = pw.load_cookies_from_file(str(f))
    assert "sid=abc123" in c and "auth=tok456" in c


def test_load_cookies_file_json(tmp_path):
    f = tmp_path / "cookies.json"
    f.write_text('[{"name": "sid", "value": "xyz"}, {"name": "a", "value": "b"}]')
    c = pw.load_cookies_from_file(str(f))
    assert "sid=xyz" in c and "a=b" in c


def test_load_cookies_firefox(tmp_path):
    db = tmp_path / "cookies.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)"
    )
    con.execute("INSERT INTO moz_cookies VALUES ('.tinkoff.ru','sid','ff1')")
    con.execute("INSERT INTO moz_cookies VALUES ('.google.com','x','y')")
    con.commit()
    con.close()
    c = pw.load_cookies_from_firefox(str(tmp_path))
    assert "sid=ff1" in c
    assert "x=y" not in c  # чужие домены не попадают


def test_like_and_comment_dry_run():
    assert pw.like_post("123", "a=b", dry_run=True)["stage"] == "dry_run"
    assert pw.comment("123", "согласен", "a=b", dry_run=True)["stage"] == "dry_run"
