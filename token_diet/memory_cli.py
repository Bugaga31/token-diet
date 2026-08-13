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

    # 1) ПОЛНЫЙ текст → библиотека (поиск по главам, RAG-подход)
    from .library import Library

    lib = Library()
    meta = lib.add(title, text, source=str(Path(path).name), kind="book")

    # 2) Выжимка + указатель на полный текст → память (для быстрого recall)
    vault.write(
        f"Книга: {title}",
        f"Источник: {Path(path).name}\n"
        f"Полный текст: {meta['chars']} символов · {meta['chunks']} глав\n"
        f"Файл: {meta['raw_file']}\n\n"
        f"--- ВЫЖИМКА (первые 4000 симв.) ---\n\n{text[:4000]}",
        tags=["book", "learning"],
        kind="reference",
    )
    return (f"✓ Прочитал книгу «{title}» — полный текст "
            f"({meta['chars']} симв., {meta['chunks']} глав) в библиотеке, "
            f"выжимка в памяти")


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
    p_lib = sub.add_parser("library-search", help="search full texts in the library")
    p_lib.add_argument("query")
    sub.add_parser("library-stats", help="library statistics")
    p_tg = sub.add_parser("tg-search", help="search Telegram channels/groups")
    p_tg.add_argument("query", help="search query")
    p_tg.add_argument("--dialogs", default="",
                      help="comma-separated dialog filter (optional)")
    p_tg.add_argument("--limit", type=int, default=5,
                      help="max messages per dialog (default 5)")
    p_tg.add_argument("--dialogs-max", type=int, default=50,
                      help="max dialogs to scan (default 50)")
    p_tg.add_argument("--save", action="store_true",
                      help="save findings to the library")
    p_fresh = sub.add_parser("tg-fresh",
                             help="latest posts from top channels NOW")
    p_fresh.add_argument("--per-channel", type=int, default=3,
                         help="posts per channel (default 3)")
    p_fresh.add_argument("--no-ai", action="store_true",
                         help="skip AI channels")
    p_watch = sub.add_parser("tg-watch",
                             help="live daemon: catch new posts instantly")
    p_watch.add_argument("--no-save", action="store_true",
                         help="do not save to library")
    p_glob = sub.add_parser("tg-global",
                            help="search ALL public Telegram (even unsubscribed)")
    p_glob.add_argument("query", help="search query")
    p_glob.add_argument("--limit", type=int, default=10,
                        help="max results (default 10)")
    p_intel = sub.add_parser("tg-intel",
                             help="unified TG intel: global + dialogs + memory")
    p_intel.add_argument("query", help="search query")
    p_intel.add_argument("--limit", type=int, default=10,
                         help="max results (default 10)")
    p_intel.add_argument("--no-save", action="store_true",
                         help="do not save findings to Obsidian")
    p_un = sub.add_parser("tg-usernames",
                          help="latest posts from username-channels (even unsubscribed)")
    p_un.add_argument("--limit", type=int, default=3,
                      help="posts per channel (default 3)")
    p_docs = sub.add_parser("docs", help="live package docs (Context7-style)")
    p_docs.add_argument("package", help="package name (requests, fastapi, react...)")
    p_docs.add_argument("--tokens", type=int, default=500,
                        help="max tokens for the context block")
    p_docs.add_argument("--fresh", action="store_true",
                        help="ignore cache, re-fetch")
    p_docs.add_argument("--flush", action="store_true",
                        help="clear docs cache for this package")
    p_scrape = sub.add_parser("scrape", help="clean readable text from a URL (Firecrawl-lite)")
    p_scrape.add_argument("url", help="http(s) URL")
    p_scrape.add_argument("--save", action="store_true",
                          help="save cleaned text to the library")
    p_scrape.add_argument("--chars", type=int, default=12000,
                          help="max characters to keep (default 12000)")
    p_bm25 = sub.add_parser("bm25", help="smart library search (Okapi BM25 rerank)")
    p_bm25.add_argument("query", help="search query")
    p_bm25.add_argument("--top", type=int, default=3,
                        help="top results (default 3)")
    p_sum = sub.add_parser("summarize", help="map-reduce summary of a long text/file")
    p_sum.add_argument("path", help="path to a .txt/.md/.py file (or URL)")
    p_sum.add_argument("--query", default="",
                       help="focus the summary on this question")
    p_sum.add_argument("--max-chars", type=int, default=12000,
                       help="chunk budget (default 12000)")
    p_lang = sub.add_parser("lang", help="detect language of a code file")
    p_lang.add_argument("path", help="path to a code file")
    p_lang.add_argument("--prompt", action="store_true",
                        help="show the idiomatic-code system prompt")
    p_lang.add_argument("--budget", action="store_true",
                        help="show what can be safely compressed")
    p_tool = sub.add_parser("toolchain", help="detect compilers/interpreters on this machine")
    p_apk = sub.add_parser("apk", help="Android APK build & inspect")
    apk_sub = p_apk.add_subparsers(dest="apk_cmd")
    p_apk_check = apk_sub.add_parser("check", help="check APK toolchain")
    p_apk_new = apk_sub.add_parser("new", help="generate a minimal Android project")
    p_apk_new.add_argument("dir", help="output directory")
    p_apk_new.add_argument("--package", default="com.example.hello",
                           help="java package (default com.example.hello)")
    p_apk_new.add_argument("--name", default="HelloApp", help="app name")
    p_apk_build = apk_sub.add_parser("build", help="build an APK from a project")
    p_apk_build.add_argument("dir", help="project directory")
    p_apk_build.add_argument("--out", default="", help="output apk path")
    p_apk_inspect = apk_sub.add_parser("inspect", help="inspect an existing APK")
    p_apk_inspect.add_argument("apk", help="path to .apk file")
    p_fix = sub.add_parser("fix-prompt",
                           help="right-size a system prompt (Anthropic context engineering)")
    p_fix.add_argument("file", help="path to a prompt text file")
    p_fix.add_argument("--analyze", action="store_true",
                       help="only report what's bloating it")
    p_think = sub.add_parser("think", help="optimize reasoning for a question")
    p_think.add_argument("question", help="the user question")
    p_think.add_argument("--mode", choices=["auto", "draft", "standard", "deep"],
                         default="auto", help="reasoning mode")
    p_ka = sub.add_parser("keepalive", help="estimate prompt-cache keepalive savings")
    p_ka.add_argument("--calls", type=int, default=50, help="calls per session")
    p_ka.add_argument("--tokens", type=int, default=5000,
                      help="input tokens per call")
    p_tron = sub.add_parser("tron", help="compact tool schemas (TRON)")
    p_tron.add_argument("file", help="path to JSON file with tool schemas")
    p_sk = sub.add_parser("skills", help="lazy-load skills (Pi-style): inject only what's needed")
    p_sk.add_argument("query", help="the user request to match against skills")
    p_sk.add_argument("--budget", type=int, default=0,
                      help="max instruction tokens to load (0 = no limit)")
    p_sk.add_argument("--base", default="You are a capable assistant.",
                      help="base system prompt text")
    p_memo = sub.add_parser(
        "memo", help="typed memory graph (Memora-style): link/boost/dupes/merge/digest")
    memo_sub = p_memo.add_subparsers(dest="memo_cmd")
    p_m_link = memo_sub.add_parser("link", help="connect two notes with a typed edge")
    p_m_link.add_argument("source", help="source note title")
    p_m_link.add_argument("target", help="target note title")
    p_m_link.add_argument("--type", default="related_to",
                          choices=["references", "implements", "supersedes",
                                   "extends", "contradicts", "related_to"],
                          help="edge type (default related_to)")
    p_m_boost = memo_sub.add_parser("boost", help="raise a note's importance")
    p_m_boost.add_argument("title", help="note title")
    p_m_boost.add_argument("--amount", type=float, default=0.2, help="importance boost")
    p_m_dupes = memo_sub.add_parser("dupes", help="find duplicate notes")
    p_m_dupes.add_argument("--threshold", type=float, default=0.85,
                           help="overlap threshold (default 0.85)")
    p_m_merge = memo_sub.add_parser("merge", help="merge two duplicate notes")
    p_m_merge.add_argument("keep", help="note to keep")
    p_m_merge.add_argument("drop", help="note to drop")
    p_m_merge.add_argument("--strategy", choices=["append", "prepend", "replace"],
                           default="append", help="merge strategy (default append)")
    p_m_digest = memo_sub.add_parser("digest", help="compressed knowledge digest about a topic")
    p_m_digest.add_argument("topic", help="topic to summarize from memory")

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

    if args.cmd == "library-search":
        from .library import Library
        lib = Library()
        block = lib.context_for_prompt(args.query, max_chars=2000)
        print(block if block else "(в библиотеке ничего не найдено)")
        return 0

    if args.cmd == "library-stats":
        from .library import Library
        print(Library().stats())
        return 0

    if args.cmd == "tg-search":
        from .telegram_search import search_telegram
        filt = [d.strip() for d in args.dialogs.split(",") if d.strip()]
        res = search_telegram(
            args.query,
            limit_per_dialog=args.limit,
            max_dialogs=args.dialogs_max,
            dialog_filter=filt or None,
            save=args.save,
        )
        if res["status"] == "error":
            print(f"⚠️ {res.get('error')}")
            return 1
        if res["status"] == "no_session":
            print(f"⚠️ {res.get('error')}")
            return 1
        print(f"✓ Найдено: {res['found']} сообщений (сессия: {res['session']})")
        for r in res["results"][:20]:
            print(f"\n📌 [{r['dialog']}] {r['date']}")
            print(f"   {r['text'][:220]}")
        if res.get("saved"):
            print("\n✓ Сохранено в библиотеку")
        return 0

    if args.cmd == "tg-fresh":
        from .telegram_monitor import collect_fresh
        res = collect_fresh(per_channel=args.per_channel, include_ai=not args.no_ai)
        if res["status"] != "ok":
            print(f"⚠️ {res.get('error')}")
            return 1
        print(f"✓ Свежак: {res['found']} постов из топ-каналов\n")
        for r in res["results"][:25]:
            mark = "🔥" if r["important"] else "·"
            print(f"{mark} [{r['channel']}] {r['date']}")
            print(f"   {r['text'][:200]}\n")
        return 0

    if args.cmd == "tg-watch":
        from .telegram_monitor import watch
        watch(save_to_lib=not args.no_save)
        return 0

    if args.cmd == "tg-intel":
        from .tg_intel import tg_intel
        res = tg_intel(args.query, limit=args.limit, save=not args.no_save)
        if res["status"] != "ok":
            print(f"⚠️ {res.get('error', 'не удалось')}")
            return 1
        print(f"✓ Разведка «{res['query']}»: {res['found']} результатов "
              f"(глобально {res['global_found']}, подписки {res['dialogs_found']})")
        if res.get("saved"):
            print("  🧠 сохранено в Obsidian")
        print()
        for r in res["results"][:15]:
            mark = "🔥" if r.get("important") else "·"
            print(f"{mark} [{r['channel']}] {r['date']} ({r['source']})")
            print(f"   {r['text'][:200]}\n")
        return 0

    if args.cmd == "tg-global":
        from .telegram_monitor import global_search
        res = global_search(args.query, limit=args.limit)
        if res["status"] != "ok":
            print(f"⚠️ {res.get('error')}")
            return 1
        print(f"✓ Глобальный поиск «{args.query}»: {res['found']} результатов\n")
        for r in res["results"][:15]:
            mark = "🔥" if r.get("important") else "·"
            print(f"{mark} [{r['channel']}] {r['date']}")
            print(f"   {r['text'][:200]}\n")
        return 0

    if args.cmd == "tg-usernames":
        from .telegram_monitor import by_username
        res = by_username(limit=args.limit)
        if res["status"] != "ok":
            print(f"⚠️ {res.get('error')}")
            return 1
        print(f"✓ По username-каналам: {res['found']} постов\n")
        for r in res["results"][:25]:
            mark = "🔥" if r.get("important") else "·"
            print(f"{mark} [{r['channel']}] {r['date']}")
            print(f"   {r['text'][:180]}\n")
        return 0

    if args.cmd == "docs":
        from .live_docs import flush_cache, get_live_docs
        if args.flush:
            print(f"✓ Очищено кэшей: {flush_cache(args.package)}")
            return 0
        res = get_live_docs(args.package, max_tokens=args.tokens,
                            use_cache=not args.fresh)
        if res.error:
            print(f"⚠️ {args.package}: {res.error}")
            return 1
        print(f"✓ {res.package} {res.version} — {res.source_url}")
        print(f"  Сыро: {res.raw_tokens} ток. → Сжато: {res.compressed_tokens} ток."
              f" (экономия {res.savings_pct}%)")
        print()
        print(res.context_block)
        return 0

    if args.cmd == "scrape":
        from .clean_scraper import scrape_and_save
        from .library import Library
        lib = Library() if args.save else None
        res = scrape_and_save(args.url, max_chars=args.chars, library=lib)
        if res.status == "error":
            print(f"⚠️ {res.error}")
            return 1
        print(f"✓ {res.title}")
        print(f"  HTML: {res.raw_chars} симв. → Чистый текст: {res.clean_chars} симв."
              f" (убранo {res.compression_pct}% мусора)")
        if res.status == "saved":
            print("  ✓ Сохранено в библиотеку")
        print()
        print(res.text[:args.chars])
        return 0

    if args.cmd == "bm25":
        from .bm25_reranker import rerank_library
        from .library import Library
        hits = rerank_library(args.query, Library(), candidates=8, top_k=args.top)
        if not hits:
            print("(в библиотеке ничего не найдено)")
            return 1
        for h in hits:
            print(f"\n### {h['title']} (глава {h['chunk'] + 1}) — score {h['score']}")
            print(h["text"][:400])
        return 0

    if args.cmd == "summarize":
        from .map_reduce import map_reduce
        text = ""
        if args.path.startswith(("http://", "https://")):
            from .clean_scraper import scrape_url
            res = scrape_url(args.path, max_chars=200_000)
            if res.status != "ok":
                print(f"⚠️ {res.error}")
                return 1
            text = res.text
        else:
            from pathlib import Path
            p = Path(args.path)
            if not p.exists():
                print(f"⚠️ файл не найден: {p}")
                return 1
            text = p.read_text(encoding="utf-8", errors="ignore")
        res = map_reduce(text, query=args.query, max_chunk_chars=args.max_chars)
        print(f"✓ Карта-редукция: {res.n_chunks} чанков · вх. {res.input_tokens} ток."
              f" → вых. {res.total_tokens} ток. (экономия {res.savings_pct}%)")
        print(f"  Детерминированный режим (без API): {'да' if not res.used_llm else 'нет'}")
        print()
        print(res.answer[:3000])
        return 0

    if args.cmd == "lang":
        from pathlib import Path
        from .polyglot import code_budget, detect_language, system_prompt_for
        p = Path(args.path)
        if not p.exists():
            print(f"⚠️ файл не найден: {p}")
            return 1
        code = p.read_text(encoding="utf-8", errors="ignore")
        lang = detect_language(code, str(p))
        print(f"✓ Язык: {lang}  (файл {p.name})")
        if args.budget:
            b = code_budget(code, lang)
            print(f"  Всего: {b.total_chars} симв. · тело: {b.body_chars} · "
                  f"импорты: {b.import_chars} · комментарии: {b.comment_chars}")
            print(f"  Можно безопасно сжать: {b.droppable_pct}% (импорты+комменты+пустые)")
        if args.prompt:
            sp = system_prompt_for(lang)
            print(f"\n  Системный промпт для {lang}:")
            print(f"  {sp}")
        return 0

    if args.cmd == "toolchain":
        from .polyglot import build_toolchain
        tc = build_toolchain()
        print(f"✓ Найдено {len(tc.available)} инструментов:")
        for tool, path in sorted(tc.available.items()):
            print(f"  {tool}: {path}")
        return 0

    if args.cmd == "apk":
        from .apk_builder import (
            build_apk, check_toolchain, create_android_project,
            inspect_apk, install_instructions,
        )
        if not args.apk_cmd:
            print(check_toolchain().render())
            print()
            print(install_instructions())
            return 0
        if args.apk_cmd == "check":
            print(check_toolchain().render())
            print()
            if not check_toolchain().ready:
                print(install_instructions())
            return 0
        if args.apk_cmd == "new":
            proj = create_android_project(args.dir, package=args.package,
                                          app_name=args.name)
            print(f"✓ Проект создан: {proj.root}")
            print(f"  Манифест: {proj.manifest_path}")
            print(f"  Активность: {proj.main_activity}")
            print(f"  Дальше: memory_cli apk build {proj.root}")
            return 0
        if args.apk_cmd == "build":
            res = build_apk(args.dir, out_apk=args.out or None)
            print(res.render())
            return 0 if res.ok else 1
        if args.apk_cmd == "inspect":
            import json
            print(json.dumps(inspect_apk(args.apk), ensure_ascii=False,
                             indent=1))
            return 0
        return 1

    if args.cmd == "fix-prompt":
        from pathlib import Path
        from .context_engineering import analyze_prompt, minimize_system_prompt
        p = Path(args.file)
        if not p.exists():
            print(f"⚠️ файл не найден: {p}")
            return 1
        prompt = p.read_text(encoding="utf-8", errors="ignore")
        if args.analyze:
            print(analyze_prompt(prompt).render())
            return 0
        res = minimize_system_prompt(prompt)
        print(f"✓ {res.tokens_before} → {res.tokens_after} токенов "
              f"(экономия {res.savings_pct}%)")
        print(f"  убрано: {res.removed_duplicate_lines} дубликатов, "
              f"{res.removed_filler_lines} общих фраз, "
              f"{res.removed_ban_lines} запретов")
        print()
        print(res.minimized)
        return 0

    if args.cmd == "think":
        from .efficient_thinking import optimize_thinking
        r = optimize_thinking(args.question, mode=args.mode)
        print(f"✓ Режим: {r['mode']} · оценка экономии рассуждений: "
              f"~{r['estimated_reasoning_savings_pct']}%")
        print(f"  {r['note']}")
        print()
        print(r["prompt"])
        return 0

    if args.cmd == "keepalive":
        from .cache_keepalive import estimate_keepalive_savings
        r = estimate_keepalive_savings(args.calls, args.tokens)
        print(f"✓ Keepalive: {r['n_calls']} вызовов × {r['input_tokens_per_call']} ток.")
        print(f"  Без пинга:   ${r['no_keepalive_usd']}")
        print(f"  С пингом:    ${r['with_keepalive_usd']}")
        print(f"  Экономия:    ${r['savings_usd']} ({r['savings_pct']}%)")
        print(f"  Оптимальный пинг: каждые {r['optimal_ping_seconds']}с")
        print(f"  Потолок выгоды:   {r['break_even_minutes']} мин простоя")
        return 0

    if args.cmd == "skills":
        from .lazy_skills import default_registry
        reg = default_registry()
        st = reg.stats()
        skills = reg.select(args.query, budget_tokens=args.budget)
        prompt = reg.build_prompt(args.base, args.query,
                                  budget_tokens=args.budget)
        print(f"✓ Скиллов в реестре: {st['skills']} · всего инструкций: "
              f"{st['instruction_tokens_total']} ток.")
        print(f"  Запрос: «{args.query}» → загружено {len(skills)} скиллов:"
              f" {', '.join(s.name for s in skills) or '—'}")
        print(f"  Экономия: {reg.savings_pct(args.query)}% инструкций НЕ грузились")
        print()
        print(prompt)
        return 0

    if args.cmd == "tron":
        import json as _json
        from pathlib import Path
        from .tron_format import savings as tron_savings
        p = Path(args.file)
        if not p.exists():
            print(f"⚠️ файл не найден: {p}")
            return 1
        try:
            tools = _json.loads(p.read_text(encoding="utf-8"))
        except _json.JSONDecodeError as e:
            print(f"⚠️ не JSON: {e}")
            return 1
        r = tron_savings(tools)
        print(f"✓ JSON: {r['json_tokens']} ток. → TRON: {r['tron_tokens']} ток."
              f" (экономия {r['savings_pct']}%)")
        print()
        print(r["tron"])
        return 0

    if args.cmd == "memo":
        from .obsidian_memory import ObsidianMemoryStore
        store = ObsidianMemoryStore(path=vault_path() / "graph.json")

        # Sync: load every note from the file vault so memo can link/boost
        # notes created with `remember` (one memory, two views).
        # The real title lives in frontmatter (`title: ...`); filenames are
        # slugified, so we must read the frontmatter to register notes under
        # their display names.
        _synced = 0
        for f in vault.notes():
            raw = f.read_text(encoding="utf-8", errors="ignore")
            fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
            title = f.stem
            if fm:
                m = re.search(r"^title:\s*(.+)$", fm.group(1), re.MULTILINE)
                if m:
                    title = m.group(1).strip()
            if store.get(title) is None:
                store.remember(title, raw[:2000])
                _synced += 1
        if _synced:
            store._save()

        if not args.memo_cmd:
            print(store.stats())
            return 0

        if args.memo_cmd == "link":
            if not store.get(args.source) or not store.get(args.target):
                print(f"⚠️ Обе заметки должны существовать. Создайте их через `remember`.")
                return 1
            ok = store.link(args.source, args.target, edge_type=args.type)
            print(f"✓ {args.source} —[{args.type}]→ {args.target}" if ok
                  else "⚠️ не удалось связать")
            return 0 if ok else 1

        if args.memo_cmd == "boost":
            if not store.boost(args.title, amount=args.amount):
                print(f"⚠️ заметка не найдена: {args.title}")
                return 1
            print(f"✓ Важность «{args.title}» поднята (+{args.amount}) → "
                  f"{store.get(args.title).importance:.2f}")
            return 0

        if args.memo_cmd == "dupes":
            dupes = store.find_duplicates(threshold=args.threshold)
            if not dupes:
                print("✓ Дубликатов не найдено")
                return 0
            print(f"⚠️ Найдено {len(dupes)} пар дубликатов:")
            for a, b in dupes:
                print(f"  • «{a.title}» ⇄ «{b.title}»")
                print(f"    → memory_cli memo merge \"{a.title}\" \"{b.title}\"")
            return 0

        if args.memo_cmd == "merge":
            if not store.merge(args.keep, args.drop, strategy=args.strategy):
                print("⚠️ не удалось слить (проверьте названия)")
                return 1
            print(f"✓ Слито: «{args.drop}» → «{args.keep}» (стратегия {args.strategy})")
            print(f"  Осталось заметок: {len(store.notes)}")
            return 0

        if args.memo_cmd == "digest":
            print(store.digest(args.topic))
            return 0

        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
