"""Тесты video_frames: метаданные, сториборд, деградация без yt-dlp."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet import video_frames as vf  # noqa: E402
from token_diet.video_frames import VideoMeta, storyboard_block  # noqa: E402


class TestVideoMeta:
    def test_block_renders_duration_and_chapters(self):
        meta = VideoMeta(
            title="Разбор портфеля", duration_sec=754, uploader="Канал",
            chapters=[(0.0, "Вступление"), (300.0, "Стопы")],
        )
        text = meta.block()
        assert "[video] Разбор портфеля (12:34, Канал)" in text
        assert "0:00 — Вступление" in text
        assert "5:00 — Стопы" in text

    def test_block_without_chapters(self):
        text = VideoMeta(title="x", duration_sec=61).block()
        assert "1:01" in text and len(text.strip().splitlines()) == 1


class TestStoryboard:
    def test_combines_meta_and_transcript(self):
        meta = VideoMeta(title="t", duration_sec=30)
        block = storyboard_block(meta, transcript_excerpt="речь про риск-менеджмент")
        assert "фрагмент речи" in block and "риск-менеджмент" in block

    def test_transcript_truncated(self):
        meta = VideoMeta(title="t", duration_sec=30)
        block = storyboard_block(meta, transcript_excerpt="ж" * 5000)
        assert len(block) < 1400


class TestDegradeGracefully:
    def test_extract_returns_empty_for_bad_url(self, tmp_path, monkeypatch):
        """Без сети yt-dlp упадёт → функция обязана вернуть [] а не исключение."""
        monkeypatch.setattr(vf, "_yt_dlp", lambda: "/usr/bin/yt-dlp")
        assert vf.extract_keyframes("https://invalid.invalid/v", tmp_path) == []

    def test_metadata_none_on_garbage(self):
        assert vf.video_metadata("not a url at all") is None

    def test_no_binary_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vf, "_yt_dlp", lambda: None)
        assert vf.extract_keyframes("https://x", tmp_path) == []


class TestJsonRobustness:
    def test_metadata_parses_chapters(self, monkeypatch):
        payload = json.dumps({
            "id": "dQw4w9WgXcQ", "title": "Demo", "duration": 213.0,
            "uploader": "ch", "webpage_url": "https://youtu.be/dQw4w9WgXcQ",
            "chapters": [
                {"start_time": 0, "title": "Intro"},
                {"start_time": 60.5, "title": "Body"},
            ],
        })
        class FakeRun:
            def __init__(self, out): self._out = out
        def fake_run(*a, **k):
            r = FakeRun(payload + "extra-garbage")   # yt-dlp иногда шумит в stdout
            r.stdout = payload
            r.stderr = ""
            r.returncode = 0
            return r
        monkeypatch.setattr(vf.subprocess, "run", fake_run)
        meta = vf.video_metadata("https://youtu.be/x")
        assert meta is not None and meta.title == "Demo"
        assert meta.chapters == [(0.0, "Intro"), (60.5, "Body")]
