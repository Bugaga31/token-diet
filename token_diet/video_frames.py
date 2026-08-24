"""Video Frames — просмотр видео: метаданные, главы, ключевые кадры.

Пара к ``youtube_learner`` (тот читает субтитры). Здесь — визуальная часть:

1. ``video_metadata`` — название, длительность, каналы форматов (yt-dlp).
2. ``extract_keyframes`` — равномерные кадры по таймлайну через
   yt-dlp + ffmpeg в локальную папку; их можно отдать в vision-модель
   или посмотреть самому.
3. ``storyboard_block`` — токен-бережный текстовый план «что в кадре»
   по временным меткам (кадры + субтитры рядом).

Требует установленные ``yt-dlp`` и ``ffmpeg``; функции возвращают None/[]
и не падают, если инструменты отсутствуют.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VideoMeta:
    """Метаданные видео."""

    id: str = ""
    title: str = ""
    duration_sec: float = 0.0
    uploader: str = ""
    webpage_url: str = ""
    chapters: list[tuple[float, str]] | None = None   # (start, title)

    def block(self) -> str:
        mins, secs = divmod(int(self.duration_sec), 60)
        lines = [f"[video] {self.title or self.id} ({mins}:{secs:02d}, {self.uploader})"]
        for start, name in (self.chapters or [])[:10]:
            m, s = divmod(int(start), 60)
            lines.append(f"[video]   {m}:{s:02d} — {name}")
        return "\n".join(lines)


def _yt_dlp() -> str | None:
    return shutil.which("yt-dlp")


def video_metadata(url: str) -> VideoMeta | None:
    """Метаданные через yt-dlp (без скачивания видео)."""
    exe = _yt_dlp()
    if not exe:
        return None
    try:
        raw = subprocess.run(  # noqa: S603 - фиксированный исполняемый файл
            [exe, "--dump-single-json", "--no-warnings", "--no-playlist", url],
            capture_output=True, text=True, timeout=90, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return None
    chapters = [
        (float(ch.get("start_time", 0.0)), str(ch.get("title", "")))
        for ch in (d.get("chapters") or [])
    ]
    return VideoMeta(
        id=str(d.get("id", "")),
        title=str(d.get("title", "")),
        duration_sec=float(d.get("duration") or 0),
        uploader=str(d.get("uploader", "")),
        webpage_url=str(d.get("webpage_url") or url),
        chapters=chapters or None,
    )


def extract_keyframes(
    url: str,
    dest_dir: str | Path,
    n_frames: int = 6,
    *,
    quality: int = 2,
    max_height: int = 480,
) -> list[Path]:
    """Скачать видео в низком разрешении и вытащить n ключевых кадров.

    Кадры кладутся в dest_dir как frame_00.png ... Возвращает список путей;
    при отсутствии yt-dlp/ffmpeg — пустой список.
    """
    exe = _yt_dlp()
    if not exe or not shutil.which("ffmpeg") or n_frames <= 0:
        return []
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    video_tmp = dest / "_source.mp4"
    if not video_tmp.exists():
        try:
            subprocess.run(  # noqa: S603
                [exe, "--no-playlist", "--no-warnings",
                 "-f", f"best[height<={max_height}]/best",
                 "-o", str(video_tmp), url],
                capture_output=True, text=True, timeout=300, check=True,
            )
        except (subprocess.SubprocessError, OSError):
            return []
    probe = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", str(video_tmp)],
        capture_output=True, text=True, timeout=30,
    )
    duration = 0.0
    try:
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    if duration <= 0:
        return []

    frames: list[Path] = []
    for i in range(n_frames):
        ts = duration * (i + 0.5) / n_frames
        out = dest / f"frame_{i:02d}.png"
        subprocess.run(  # noqa: S603
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.2f}",
             "-i", str(video_tmp), "-frames:v", "1",
             "-q:v", str(quality), str(out)],
            capture_output=True, text=True, timeout=60,
        )
        if out.exists():
            frames.append(out)
    video_tmp.unlink(missing_ok=True)
    return frames


def storyboard_block(meta: VideoMeta, transcript_excerpt: str = "") -> str:
    """Свести метаданные + кусок транскрипта в компактный блок контекста."""
    lines = [meta.block()]
    if transcript_excerpt:
        lines.append("[video] фрагмент речи:")
        lines.append(transcript_excerpt.strip()[:1200])
    return "\n".join(lines)
