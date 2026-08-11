"""memory_cli — self-learning CLI: save lessons, recall memories.

Usage:
    python -m token_diet.memory_cli remember "Урок" "текст" --tags a,b --kind lesson
    python -m token_diet.memory_cli recall "полюс продавать"
    python -m token_diet.memory_cli path

The vault lives on the external Kingston disk (/media/ro/KINGSTON1/token-diet-memory)
so long-term memory does NOT occupy conversation cache. Falls back to
~/token-diet-memory if the disk is not mounted.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .obsidian_vault import ObsidianVault

# Внешний диск, если смонтирован; иначе локальный fallback
VAULT_CANDIDATES = [
    Path("/media/ro/KINGSTON1/token-diet-memory"),
    Path("/mnt/flash1tb/token-diet-memory"),
    Path("~/token-diet-memory").expanduser(),
]


def vault_path() -> Path:
    for p in VAULT_CANDIDATES:
        try:
            p.mkdir(parents=True, exist_ok=True)
            test = p / ".w"
            test.write_text("")
            test.unlink()
            return p
        except OSError:
            continue
    return VAULT_CANDIDATES[-1]


def extract_book_text(path: str | Path, max_chars: int = 200_000) -> tuple[str, str]:
    """Extract plain text from epub / fb2 / txt (stdlib only).

    Returns (title, text). Zero dependencies — no third-party ebook libs.
    epub = zip with xhtml; fb2 = xml with <section>/<p>; txt = raw.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    title = p.stem

    try:
        if suffix == ".epub":
            text_parts: list[str] = []
            with zipfile.ZipFile(p) as z:
                names = [n for n in z.namelist()
                         if n.lower().endswith((".xhtml", ".html", ".htm"))]
                names.sort()
                for n in names:
                    raw = z.read(n).decode("utf-8", errors="ignore")
                    # strip tags, keep text
                    raw = re.sub(r"<script.*?</script>", " ", raw, flags=re.DOTALL | re.I)
                    raw = re.sub(r"<style.*?</style>", " ", raw, flags=re.DOTALL | re.I)
                    raw = re.sub(r"<[^>]+>", " ", raw)
                    raw = re.sub(r"\s+", " ", raw)
                    text_parts.append(raw)
            return title, "\n\n".join(text_parts)[:max_chars]

        if suffix == ".fb2":
            root = ET.parse(p).getroot()
            # strip namespaces
            for el in root.iter():
                el.tag = el.tag.split("}")[-1]
            title_el = root.find(".//book-title")
            if title_el is not None and title_el.text:
                title = title_el.text.strip()
            parts = [el.text.strip() for el in root.iter("p") if el.text and el.text.strip()]
            return title, "\n\n".join(parts)[:max_chars]

        # txt / everything else
        return title, p.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception as e:
        return title, f"[ошибка чтения: {e}]"


def digest_book(vault: ObsidianVault, path: str | Path) -> str:
    """Read a book and store an extract in memory (kind='reference', tag='book').

    This is the 'self-learning' path: knowledge from books goes into the
    external vault, not into conversation cache.
    """
    title, text = extract_book_text(path)
    if text.startswith("[ошибка"):
        return f"⚠️ Не удалось прочитать: {text}"
    if len(text) < 200:
        return f"⚠️ Слишком мало текста в {title}"

    # Сохраняем выжимку с оглавлением-метаданными, чтобы не забивать память
    vault.write(
        f"Книга: {title}",
        f"Источник: {Path(path).name}\n"
        f"Объём текста: {len(text)} символов\n\n"
        f"--- ВЫЖИМКА (первые 4000 симв.) ---\n\n{text[:4000]}",
        tags=["book", "learning"],
        kind="reference",
    )
    return f"✓ Прочитал книгу «{title}» → сохранено в память"


def export_all(vault: ObsidianVault) -> str:
    """Dump the whole vault into one markdown file.

    Purpose: any AI (Hermes, Claude Code, OpenCode, this chat) can load
    the file and 'remember everything' — goals, rules, portfolio, books,
    videos, lessons — without carrying a private conversation cache.
    """
    out = Path("/media/ro/KINGSTON1/token-diet-memory/CONTEXT_ALL.md")
    parts = [
        "# token-diet MEMORY — полный контекст",
        "# Загрузи этот файл в любую ИИ-сессию, чтобы она помнила всё.",
        f"# Экспортировано: {Path('/tmp/token-diet-clone').exists() and 'token-diet' or ''}",
        "",
    ]
    for f in vault.notes():
        text = f.read_text(encoding="utf-8")
        parts.append(f"\n\n---\n\n{text}")
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"✓ Экспорт: {out} ({sum(len(p) for p in parts)} символов)")
    return str(out)


def main() -> int:
    parser = argparse.ArgumentParser(prog="token-diet-memory")
    sub = parser.add_subparsers(dest="cmd")

    p_rem = sub.add_parser("remember", help="save a lesson/fact to memory")
    p_rem.add_argument("title")
    p_rem.add_argument("text")
    p_rem.add_argument("--tags", default="", help="comma-separated tags")
    p_rem.add_argument("--kind", default="lesson",
                       choices=["fact", "decision", "constraint",
                                "preference", "reference", "lesson"])

    sub.add_parser("recall", help="search memory") \
       .add_argument("query")
    sub.add_parser("path", help="print vault path")
    sub.add_parser("stats", help="memory stats")
    p_study = sub.add_parser("study", help="read a book (epub/fb2/txt) into memory")
    p_study.add_argument("path", help="path to the book file")
    p_video = sub.add_parser("study-video", help="read a YouTube video into memory")
    p_video.add_argument("url", help="YouTube URL")
    sub.add_parser("export", help="dump ALL memory to one file (for Hermes/any AI)")

    args = parser.parse_args()
    vault = ObsidianVault(vault_path())

    if args.cmd == "remember":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        f = vault.write(args.title, args.text, tags=tags, kind=args.kind)
        print(f"✓ Запомнил → {f}")
        return 0

    if args.cmd == "recall":
        block = vault.context_for_prompt(args.query, max_chars=800)
        print(block if block else "(ничего не найдено)")
        return 0

    if args.cmd == "path":
        print(vault.path)
        return 0

    if args.cmd == "stats":
        print(vault.stats())
        return 0

    if args.cmd == "study":
        print(digest_book(vault, args.path))
        return 0

    if args.cmd == "study-video":
        try:
            from .youtube_learner import study_video
            print(study_video(args.url, vault_path()))
        except ImportError as e:
            print(f"⚠️ yt-dlp не установлен: {e}")
        return 0

    if args.cmd == "export":
        export_all(vault)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
