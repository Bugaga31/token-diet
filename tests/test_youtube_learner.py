"""Tests for youtube_learner (transcript extraction, mocked subprocess).

The module calls the yt-dlp CLI via subprocess; tests mock it so we
never hit the network. Verifies: VTT cleaning, fallback file choice
(prefer Russian), no-subtitles handling, and study_video -> vault.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.youtube_learner import _clean_vtt, extract_transcript, study_video

_LONG_LINE = (
    "This is a long lesson line about learning systems, memory storage, "
    "and how models can read books and videos to become smarter over time. "
    "The transcript needs to be long enough to pass the minimum length check "
    "so the knowledge actually gets stored in the vault for future sessions. "
    "Repeating this content makes the test sample realistically long.")

VTT_SAMPLE = """WEBVTT

00:00:01.000 --> 00:00:03.000
<v Speaker>Hello world and welcome to this lesson about learning systems</v>

00:00:03.500 --> 00:00:06.000
This is a <00:00:04.000>test</c> line with more words to pass the minimum length

00:00:06.500 --> 00:00:09.000
&nbsp;Second &amp; line and here is additional content making it long enough

""" + _LONG_LINE + "\n\n" + _LONG_LINE + "\n\n" + _LONG_LINE + "\n"


def test_clean_vtt_strips_timestamps_and_tags():
    out = _clean_vtt(VTT_SAMPLE)
    assert "WEBVTT" not in out
    assert "-->" not in out
    assert "<v" not in out
    assert "Hello world" in out
    assert "&amp;" not in out  # entity decoded


def test_extract_transcript_ok(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, capture_output=True, text=True, timeout=120):
        calls.append(cmd)
        if "--print" in cmd:
            class R:
                returncode = 0
                stdout = "Test Video|Some Channel|60"
                stderr = ""
            return R()
        # subtitle download command: write a fake ru.vtt
        out = tmp_path / "sub.ru.vtt"
        out.write_text(VTT_SAMPLE, encoding="utf-8")
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("token_diet.youtube_learner.subprocess.run", fake_run)
    # point tempdir inside tmp_path for the glob to find the file
    monkeypatch.setattr("token_diet.youtube_learner.tempfile",
                        type("T", (), {"TemporaryDirectory": lambda prefix="": _FakeTmp(tmp_path)}))

    r = extract_transcript("https://youtu.be/abc123", max_chars=2000)
    assert r["status"] == "ok"
    assert r["title"] == "Test Video"
    assert r["channel"] == "Some Channel"
    assert r["duration_sec"] == 60
    assert "Hello world" in r["transcript"]


class _FakeTmp:
    def __init__(self, path):
        self._p = path

    def __enter__(self):
        return str(self._p)

    def __exit__(self, *a):
        return False


def test_extract_transcript_no_subs(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output=True, text=True, timeout=120):
        if "--print" in cmd:
            class R:
                returncode = 0
                stdout = "No Subs Video||10"
                stderr = ""
            return R()
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("token_diet.youtube_learner.subprocess.run", fake_run)
    monkeypatch.setattr("token_diet.youtube_learner.tempfile",
                        type("T", (), {"TemporaryDirectory": lambda prefix="": _FakeTmp(tmp_path)}))
    r = extract_transcript("https://youtu.be/xyz789")
    assert r["status"] == "no_subs"


def test_study_video_saves_to_vault(monkeypatch, tmp_path):
    vault_dir = tmp_path / "vault"

    def fake_run(cmd, capture_output=True, text=True, timeout=120):
        if "--print" in cmd:
            class R:
                returncode = 0
                stdout = "Learning Video|Channel X|300"
                stderr = ""
            return R()
        out = tmp_path / "sub.ru.vtt"
        out.write_text(VTT_SAMPLE, encoding="utf-8")
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("token_diet.youtube_learner.subprocess.run", fake_run)
    monkeypatch.setattr("token_diet.youtube_learner.tempfile",
                        type("T", (), {"TemporaryDirectory": lambda prefix="": _FakeTmp(tmp_path)}))
    msg = study_video("https://youtu.be/learn123", vault_path=vault_dir)
    assert "✓" in msg
    notes = list(vault_dir.glob("*.md"))
    assert notes, "заметка не создана"
    assert "Learning Video" in notes[0].read_text(encoding="utf-8")
