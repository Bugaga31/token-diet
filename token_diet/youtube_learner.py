"""youtube_learner — read YouTube videos and learn from them.

Extracts the transcript (subtitles) of a YouTube video via the yt-dlp
CLI (more reliable than the Python API against YouTube's frequent
changes) and stores a distilled extract into the Obsidian vault — the
same memory that books and lessons use. A video watched once =
knowledge that any future session (Hermes, Claude, OpenCode, this chat)
can recall.

Why this matters (honest):
  - Models can't watch video, but transcripts carry the knowledge.
  - Storing in the vault keeps knowledge in ONE place, cheap to
    retrieve, and independent of any single conversation's cache.

Dependencies: yt-dlp (system CLI). No other third-party libs.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path


def _clean_vtt(text: str) -> str:
    """Strip VTT timing lines, HTML entities and markup."""
    lines: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # skip cue timestamps like 00:00:01.000 --> 00:00:03.000
        if "-->" in line and re.match(r"^[\d:.]+", line):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s*$", line):
            continue
        if line.startswith("WEBVTT") or line.startswith("Kind:") or \
           line.startswith("Language:"):
            continue
        # strip inline tags <c>...</c>, <00:00:01.000>
        line = re.sub(r"<[^>]+>", "", line)
        line = line.replace("&amp;", "&").replace("&quot;", '"')
        if line.strip():
            lines.append(line.strip())
    text = " ".join(lines)
    return re.sub(r"\s+", " ", text).strip()


def extract_transcript(
    url: str,
    max_chars: int = 60_000,
    prefer_auto: bool = True,
) -> dict:
    """Get the transcript of a YouTube video via the yt-dlp CLI.

    Downloads subtitles (auto first, Russian preferred, fallback any),
    cleans VTT, returns dict with title/channel/duration/transcript.
    Never raises.
    """
    video_id = url
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]{6,})", url)
    if m:
        video_id = m.group(1)

    langs = "ru,ru-orig,en,en-orig" if prefer_auto else "ru,en"

    with tempfile.TemporaryDirectory(prefix="yt_learn_") as tmp:
        out_tmpl = str(Path(tmp) / "sub")

        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-auto-subs",
            "--write-subs",
            "--sub-langs", langs,
            "--sub-format", "vtt",
            "--no-warnings",
            "--quiet",
            "-o", out_tmpl,
            url,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"status": "error", "error": str(e), "video_id": video_id}

        if proc.returncode != 0:
            return {
                "status": "error",
                "error": proc.stderr.strip()[-300:] or "yt-dlp failed",
                "video_id": video_id,
            }

        # pick the best subtitle file: prefer Russian
        files = sorted(Path(tmp).glob("sub.*.vtt"))
        chosen = None
        for f in files:
            if ".ru." in f.name:
                chosen = f
                break
        if chosen is None and files:
            chosen = files[0]
        if chosen is None:
            return {
                "status": "no_subs",
                "video_id": video_id,
                "error": "Субтитры не найдены (видео может не иметь транскрипта)",
            }

        transcript = _clean_vtt(chosen.read_text(encoding="utf-8", errors="ignore"))

        # title/channel/duration via JSON dump
        info: dict = {}
        try:
            cmd_info = ["yt-dlp", "--skip-download", "--no-warnings", "--quiet",
                        "--print", "%(title)s|%(channel)s|%(duration)s", url]
            out = subprocess.run(cmd_info, capture_output=True, text=True,
                                 timeout=60).stdout.strip()
            parts = out.split("|")
            if len(parts) == 3:
                info = {"title": parts[0], "channel": parts[1],
                        "duration": int(float(parts[2] or 0))}
        except Exception:
            pass

        if len(transcript) < 100:
            return {"status": "no_subs", "video_id": video_id,
                    "error": "Субтитры пустые или не найдены"}

        return {
            "status": "ok",
            "video_id": video_id,
            "title": info.get("title", "YouTube видео"),
            "channel": info.get("channel", ""),
            "duration_sec": int(info.get("duration", 0)),
            "url": url,
            "transcript": transcript[:max_chars],
            "transcript_chars": len(transcript),
        }


def study_video(url: str, vault_path: str | Path | None = None) -> str:
    """Read a YouTube video and store the knowledge in the vault."""
    from .obsidian_vault import ObsidianVault

    vault = ObsidianVault(vault_path or "~/token-diet-memory")
    result = extract_transcript(url)
    if result["status"] != "ok":
        return f"⚠️ {result.get('error', 'не удалось получить транскрипт')}"

    mins = result.get("duration_sec", 0) // 60
    title = result.get("title", "YouTube видео")
    transcript = result.get("transcript", "")
    if len(transcript) < 200:
        return f"⚠️ Слишком мало текста в видео «{title}»"

    vault.write(
        f"Видео: {title}",
        f"Источник: {result.get('url')}\n"
        f"Канал: {result.get('channel')} · Длительность: {mins} мин · "
        f"Транскрипт: {len(transcript)} символов\n\n"
        f"--- ТРАНСКРИПТ (первые 4000 симв.) ---\n\n{transcript[:4000]}",
        tags=["video", "youtube", "learning"],
        kind="reference",
    )
    return f"✓ Прочитал видео «{title}» ({mins} мин) → сохранено в память"


__all__ = ["extract_transcript", "study_video"]
