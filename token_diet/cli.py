"""token-diet — единый командный интерфейс всего арсенала.

Один инструмент вместо сотни скриптов: память, рынок, мозг, армия,
панель Рика, прокси, установка, доктор и быстрый самотест.

    token-diet                 # обзор арсенала
    token-diet doctor          # проверить, что все модули живы
    token-diet self-test       # быстрый офлайн-тест ядра (без сети)
    token-diet diet <текст>    # сжать текст: меньше токенов
    token-diet memo ...        # память Obsidian (все подкоманды memory_cli)
    token-diet recall <запрос> # найти в памяти
    token-diet panel <запрос>  # пульт Рика: что есть на ситуацию
    token-diet scan            # живой сканер рынка (рост + объём + стакан)
    token-diet serve           # прокси-сервер экономии токенов
    token-diet setup           # автоконфигурация под Claude/OpenCode/...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))


# ── doctor: проверка здоровья всех модулей (офлайн, <2с) ─────────────────────

def _core_checks() -> list[tuple[str, bool, str]]:
    """Быстрые офлайн-проверки ядра: импорты + базовая работа."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append((name, True, "ok"))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, str(e)[:80]))

    def _count_tokens() -> None:
        from token_diet.core import count_tokens
        assert count_tokens("привет мир") > 0

    def _compress() -> None:
        from token_diet.loss_router import compress_with_routing
        out = compress_with_routing("You are a helpful assistant. Be polite.")
        assert out and len(out) > 0

    def _arsenal() -> None:
        from token_diet.rick_panel import categories
        assert len(categories()) > 0

    def _memory() -> None:
        from token_diet.memory_cli import vault_path
        assert vault_path()

    def _brain() -> None:
        from token_diet.trading_brain import decide
        assert callable(decide)

    def _scan() -> None:
        from token_diet.live_scan import scan_market
        assert callable(scan_market)

    def _army() -> None:
        from token_diet.model_army import army_verdict
        assert callable(army_verdict)

    def _geo() -> None:
        from token_diet.geopolitics import geo_verdict
        geo_verdict  # импорт и имя существуют

    def _pulse() -> None:
        from token_diet.pulse_reader import pulse_sentiment
        pulse_sentiment  # импорт и имя существуют

    def _tg() -> None:
        from token_diet.telegram_market_feed import TelegramMarketFeed
        TelegramMarketFeed  # импорт и имя существуют

    def _invest() -> None:
        from token_diet.invest_hub import InvestHub
        InvestHub  # импорт и имя существуют

    def _observe() -> None:
        from token_diet.obsidian_vault import ObsidianVault
        ObsidianVault  # импорт и имя существуют

    for name, fn in [
        ("core.count_tokens", _count_tokens),
        ("loss_router.compress", _compress),
        ("rick_panel.arsenal", _arsenal),
        ("memory.vault", _memory),
        ("trading_brain", _brain),
        ("live_scan", _scan),
        ("model_army", _army),
        ("geopolitics", _geo),
        ("pulse_reader", _pulse),
        ("telegram_feed", _tg),
        ("invest_hub", _invest),
        ("obsidian_vault", _observe),
    ]:
        check(name, fn)
    return results


def _doctor() -> int:
    print("🧬 token-diet DOCTOR — проверка здоровья модулей")
    print("-" * 56)
    results = _core_checks()
    broken = 0
    for name, ok, msg in results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name:<24} {'' if ok else msg}")
        broken += 0 if ok else 1
    print("-" * 56)
    if broken:
        print(f"СЛОМАНО: {broken} модулей. Чини: token-diet self-test")
        return 1
    print(f"ВСЕ {len(results)} МОДУЛЕЙ ЖИВЫ. Арсенал готов. 🫡")
    return 0


# ── self-test: быстрый офлайн-тест ядра ───────────────────────────────────────

def _self_test() -> int:
    """Быстрый офлайн-прогон: экономия токенов, память, качество."""
    print("⚡ token-diet SELF-TEST (офлайн, без сети)")
    print("-" * 56)
    fails = 0

    def t(name: str, fn) -> None:
        nonlocal fails
        try:
            fn()
            print(f"  ✅ {name}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  ❌ {name}: {str(e)[:90]}")

    def _savings() -> None:
        from token_diet.core import count_tokens
        from token_diet.loss_router import compress_with_routing
        text = ("You are a helpful assistant. Please be polite and courteous "
                "at all times. Always greet the user warmly.")
        before = count_tokens(text)
        out, _, after = compress_with_routing(text)
        assert isinstance(out, str) and len(out) > 0
        assert after <= before, f"сжатие не сжало: {before} → {after}"

    def _memory_roundtrip() -> None:
        import tempfile
        from pathlib import Path
        from token_diet.obsidian_vault import ObsidianVault
        with tempfile.TemporaryDirectory() as d:
            v = ObsidianVault(Path(d))
            v.write("Тест", "запись для проверки")
            hits = v.search("проверки")
            assert len(hits) >= 1, "память не нашла запись"

    def _reasoning() -> None:
        from token_diet.reasoning_kit import plan_steps
        steps = plan_steps("сравни два варианта и выбери лучший")
        assert isinstance(steps, list) and len(steps) >= 1

    def _tokens_saved() -> None:
        from token_diet.green_calculator import GreenCalculator
        g = GreenCalculator()
        m = g.measure(tokens_saved=10_000)
        assert m.tokens_saved == 10_000

    def _arsenal_count() -> None:
        from token_diet.rick_panel import categories
        assert len(categories()) >= 10

    for name, fn in [
        ("сжатие реально экономит токены", _savings),
        ("память Obsidian: запись+поиск", _memory_roundtrip),
        ("reasoning_kit: план рассуждений", _reasoning),
        ("green: расчёт экономии", _tokens_saved),
        ("арсенал: 10+ категорий", _arsenal_count),
    ]:
        t(name, fn)

    print("-" * 56)
    if fails:
        print(f"ПРОВАЛЕНО: {fails}. Ошибки выше.")
        return 1
    print("ВСЕ ПРОВЕРКИ ПРОШЛИ. Ядро здорово. 🫡")
    return 0


# ── panel: пульт Рика ─────────────────────────────────────────────────────────

def _panel(query: str | None) -> int:
    from token_diet.rick_panel import panel_block
    print(panel_block(query))
    return 0


# ── scan: живой сканер рынка ─────────────────────────────────────────────────

def _scan() -> int:
    from token_diet.live_scan import scan_market
    try:
        from token_diet.geopolitics import market_context_block
        print(market_context_block())
    except Exception:  # noqa: BLE001
        pass
    print()
    print(f"{'ТИКЕР':<7}{'ЦЕНА':>9}{'ДЕНЬ%':>8}{'ОБЪЁМ':>8}{'УХАЯ':>7}{'СКОР':>6}  СИГНАЛЫ")
    print("-" * 78)
    found = 0
    for r in scan_market():
        found += 1
        lean = f"{r.orderbook_lean:+.2f}" if r.orderbook_lean is not None else "  -"
        print(f"{r.ticker:<7}{r.price:>9.1f}{r.change_pct:>+7.2f}%{r.volume_ratio:>7.1f}x"
              f"{r.day_position:>6.0%}{r.score:>6.1f}  {', '.join(r.signals)}")
    if not found:
        print("  (пусто — никто не растёт с объёмом прямо сейчас)")
    return 0


# ── snapshot: мгновенный снимок рынка ────────────────────────────────────────

def _portfolio_block() -> str:
    """Блок портфеля из T-Invest API (пустая строка, если недоступен)."""
    try:
        # Живой портфель из API (Д10: раньше snapshot его не показывал)
        from token_diet.tinkoff_invest import TinkoffInvest
        inv = TinkoffInvest()
        pf = inv.get_portfolio()
        if pf:
            rows, total = [], 0.0
            for p in pf:
                t = getattr(p, "ticker", None)
                if not t or t.startswith("uid:"):
                    # Д11: рублёвый кэш рисуется как uid — показываем как КЭШ
                    total += getattr(p, "quantity", 0.0) * getattr(p, "avg_price", 1.0)
                    rows.append(f"  💵 КЭШ: {getattr(p, 'quantity', 0.0):,.0f} ₽")
                    continue
                q = getattr(p, "quantity", 0)
                ap = getattr(p, "avg_price", 0.0)
                pnl = getattr(p, "profit_pct", None)
                val = q * ap
                total += val
                pnl_s = f" ({pnl:+.2f}%)" if pnl is not None else ""
                rows.append(f"  {t}: {q:g} шт @ {ap:.2f} = {val:,.0f} ₽{pnl_s}")
            return "\n📊 ПОРТФЕЛЬ:\n" + "\n".join(rows) + f"\n  ИТОГО: {total:,.0f} ₽"
        return "\n📊 ПОРТФЕЛЬ: пусто"
    except Exception:  # noqa: BLE001
        return ""


def _snapshot(brief: bool) -> int:
    """Быстрый снимок: геополитика + портфель + лучший кандидат. Без демонов."""
    lines: list[str] = []
    try:
        from token_diet.geopolitics import market_context_block
        lines.append(market_context_block())
    except Exception:  # noqa: BLE001
        pass
    pf_block = _portfolio_block()
    if pf_block:
        lines.append(pf_block)
    try:
        from token_diet.live_scan import scan_market
        best = None
        for r in scan_market():
            if best is None or r.score > best.score:
                best = r
        if best:
            lines.append(f"\n🏆 Лучший кандидат: {best.ticker} {best.price:.1f} "
                         f"({best.change_pct:+.2f}%) скор {best.score:.1f} "
                         f"— {' '.join(best.signals)}")
        else:
            lines.append("\n🏆 Кандидатов нет — никто не растёт с объёмом.")
    except Exception:  # noqa: BLE001
        lines.append("\n⚠️ сканер недоступен")
    text = "\n".join(lines)
    if brief:
        # одна строка: фон + кандидат
        brief_line = " ".join(l for l in lines if "🏆" in l) or "кандидатов нет"
        print(brief_line)
        return 0
    print(text)
    return 0


# ── главный парсер ────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="token-diet",
        description="Единый мозг: память + рынок + интеллект + экономия токенов.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  token-diet doctor          # здоровье всех модулей\n"
            "  token-diet self-test       # быстрый офлайн-тест\n"
            "  token-diet diet <текст>    # сжать текст — экономия токенов\n"
            "  token-diet memo remember 'урок: ...'\n"
            "  token-diet recall 'полюс'  # найти в памяти\n"
            "  token-diet portfolio       # живой портфель T-Invest\n"
            "  token-diet panel деньги    # что есть на «деньги»\n"
            "  token-diet scan            # живой сканер рынка\n"
            "  token-diet eyes 'что на экране?'  # зрение через OmniRoute\n"
            "  token-diet serve           # прокси экономии токенов\n"
        ),
    )
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="проверка здоровья всех модулей")
    sub.add_parser("self-test", help="быстрый офлайн-тест ядра")
    sub.add_parser("serve", help="прокси-сервер экономии токенов")
    sub.add_parser("setup", help="автоконфигурация под Claude Code/OpenCode/...")
    p_boot = sub.add_parser("bootstrap", help="самонастройка любой ИИ по одной ссылке")
    p_boot.add_argument("--link", action="store_true",
                        help="показать готовую инструкцию-ссылку")

    p_snap = sub.add_parser("snapshot", help="мгновенный снимок рынка и портфеля")
    p_snap.add_argument("--brief", action="store_true",
                        help="только одна строка: фон + лучший кандидат")

    p_panel = sub.add_parser("panel", help="пульт Рика: модули под задачу")
    p_panel.add_argument("query", nargs="*", default=[], help="что ищем (деньги, память, кризис...)")

    sub.add_parser("scan", help="живой сканер рынка (рост+объём+стакан)")

    p_memo = sub.add_parser("memo", help="память Obsidian (см. token-diet memo -h)")
    p_memo.add_argument("args", nargs=argparse.REMAINDER, help="подкоманды memory_cli")

    p_audit = sub.add_parser("audit", help="hash-chain журнал: прогнозы и решения без подделки (из Buzz)")
    audit_sub = p_audit.add_subparsers(dest="audit_cmd")
    p_aud_log = audit_sub.add_parser("log", help="записать действие в цепочку")
    p_aud_log.add_argument("action", help="действие (prediction_made, decision_made, ...)")
    p_aud_log.add_argument("--detail", default="{}", help="JSON-детали")
    p_aud_log.add_argument("--actor", default="assistant", help="кто сделал")
    p_aud_log.add_argument("--object", default=None, help="объект действия")
    p_aud_pred = audit_sub.add_parser("predict", help="записать прогноз (не подделать!)")
    p_aud_pred.add_argument("ticker", help="тикер (PLZL)")
    p_aud_pred.add_argument("target", type=float, help="целевая цена")
    p_aud_pred.add_argument("--by", default="", help="к какой дате")
    p_aud_pred.add_argument("--note", default="", help="комментарий")
    audit_sub.add_parser("verify", help="проверить целостность цепочки")
    p_aud_last = audit_sub.add_parser("last", help="последние записи")
    p_aud_last.add_argument("n", nargs="?", type=int, default=5, help="сколько (по умолчанию 5)")

    p_army = sub.add_parser("army", help="армия LLM-моделей (AnyModel): спросить всех и собрать голоса")
    p_army.add_argument("question", nargs="?", default="", help="вопрос армии")
    p_army.add_argument("--role", default="brain",
                        help="brain/analyst/fast/generator (по умолчанию brain)")
    p_army.add_argument("--all", action="store_true", help="спросить ВСЮ армию (вердикт)")
    p_army.add_argument("--system", default=None, help="системный промпт")
    p_army.add_argument("--omni", action="store_true",
                        help="спросить локальный роутер OmniRoute (607 моделей, 1M контекст)")
    p_army.add_argument("--omni-role", default="brain",
                        help="омни-роль: brain/coding/fast/claude/gemini")

    p_rules = sub.add_parser("rules", help="YAML-правила автоматизации (из Buzz buzz-workflow)")
    p_rules.add_argument("--rules", default=None, help="путь к файлу правил (по умолчанию ~/token-diet-memory/rules.yaml)")
    rules_sub = p_rules.add_subparsers(dest="rules_cmd")
    rules_sub.add_parser("list", help="список загруженных правил")
    p_rules_ex = rules_sub.add_parser("example", help="сгенерировать пример правил")
    p_rules_ex.add_argument("--path", default=None, help="куда сохранить")
    p_rules_run = rules_sub.add_parser("run", help="подать событие в движок")
    p_rules_run.add_argument("on", help="тип события: price_event / news_event / webhook / message_posted")
    p_rules_run.add_argument("--rules", default=None, help="путь к файлу правил")
    p_rules_run.add_argument("--event", default="{}", help="JSON события (ticker, price, change_pct...)")
    p_rules_run.add_argument("--dry-run", action="store_true", help="без побочных эффектов")

    p_handoff = sub.add_parser("handoff", help="слепок себя: сжать сессию и продолжить с того же места (из Buzz buzz-agent)")
    p_handoff.add_argument("text", nargs="?", default="", help="текст истории сессии для сжатия")
    p_handoff.add_argument("--history", default=None, help="путь к файлу истории (если не текст)")
    p_handoff.add_argument("--max-chars", type=int, default=6000, help="лимит сжатой истории")
    p_handoff.add_argument("--save", default=None, help="куда сохранить handoff (по умолчанию печать)")

    sub.add_parser("turbo", help="выжать комп до максимума (RAM/кеш/ZRAM/лишние браузеры)")
    p_scrape = sub.add_parser("scrape", help="быстрый парсинг страницы (Scrapling-lite: кеш, adaptive select, find_by_text)")
    p_scrape.add_argument("url", help="URL страницы")
    p_scrape.add_argument("--selector", default="", help="CSS-селектор (adaptive: найдёт похожие, если дизайн сменился)")
    p_scrape.add_argument("--text", default="", help="найти элемент по тексту")
    p_scrape.add_argument("--no-cache", action="store_true", help="не использовать дисковый кеш")
    p_scrape.add_argument("--proxy", default="", help="прокси http://host:port")
    p_scrape.add_argument("--capture", default="", help="перехватить XHR/fetch по regex (CDP-браузер)")

    p_watch = sub.add_parser("watch", help="сторож рынка: цена -> правила докупки/продажи (см. -h)")
    p_watch.add_argument("ticker", help="тикер (SNGSP)")
    p_watch.add_argument("--rules", default=None, help="файл правил YAML")
    p_watch.add_argument("--init", action="store_true", help="сгенерировать правила докупки и выйти")
    p_watch.add_argument("--interval", type=int, default=300, help="сек между опросами")
    p_watch.add_argument("--dry-run", action="store_true", help="не исполнять заявки")
    p_watch.add_argument("--once", action="store_true", help="один опрос и выход")

    # ── алиасы из инструкций: diet / recall / portfolio / status / eyes / server ──
    p_diet = sub.add_parser("diet", help="сжать текст: меньше токенов, тот же смысл")
    p_diet.add_argument("text", nargs="+", help="текст для сжатия")
    p_diet.add_argument("--aggressive", action="store_true", help="режим сильнее (допустимы потери)")

    p_recall = sub.add_parser("recall", help="найти в памяти Obsidian")
    p_recall.add_argument("query", nargs="+", help="поисковый запрос")

    sub.add_parser("portfolio", help="портфель из T-Invest API (живые позиции)")

    sub.add_parser("status", help="статус всего: фон + портфель + лучший кандидат (alias snapshot)")

    p_eyes = sub.add_parser("eyes", help="глаза: вопрос vision-модели про экран или картинку")
    p_eyes.add_argument("question", nargs="?", default="Опиши кратко, что сейчас на экране.",
                        help="что спросить (по умолчанию — описать экран)")
    p_eyes.add_argument("--image", default=None, help="путь к изображению (по умолчанию скриншот экрана)")
    p_eyes.add_argument("--model", default=None, help="модель роутера (например agentrouter/claude-opus-5)")

    sub.add_parser("server", help="alias для serve: прокси-сервер экономии токенов")

    p_batch = sub.add_parser("batch", help="батчинг событий: меньше промптов — меньше токенов")
    p_batch.add_argument("--demo", action="store_true", help="наглядный демо-прогон")

    args = p.parse_args(argv)

    if args.cmd is None:
        from token_diet.rick_panel import panel_block
        print(panel_block())
        print("\nКоманды: doctor | self-test | diet <текст> | serve | portfolio | scan | memo ... | eyes <вопрос>")
        return 0

    if args.cmd == "diet":
        from token_diet.loss_router import compress_with_routing
        text = " ".join(args.text)
        out, before, after = compress_with_routing(text, aggressive=args.aggressive)
        saved = max(0, before - after)
        pct = (saved / before * 100) if before else 0.0
        print(out)
        print(f"\n[диета] {before} → {after} токенов (−{pct:.1f}%)"
              + ("  [агрессивно]" if args.aggressive else ""))
        return 0
    if args.cmd == "recall":
        from token_diet.memory_cli import main as memo_main
        return memo_main(["recall", " ".join(args.query)])
    if args.cmd == "portfolio":
        block = _portfolio_block()
        print(block or "📊 Портфель недоступен: нет TINKOFF_TOKEN или сети (см. .env)")
        return 0 if block else 1
    if args.cmd == "status":
        return _snapshot(False)
    if args.cmd == "eyes":
        if args.image:
            from token_diet.omni_eyes import see_image
            print(see_image(args.image, question=args.question, model=args.model))
        else:
            from token_diet.omni_eyes import see
            print(see(args.question, model=args.model))
        return 0
    if args.cmd == "server":
        from token_diet.server import main as serve_main
        return serve_main()

    if args.cmd == "doctor":
        return _doctor()
    if args.cmd == "self-test":
        return _self_test()
    if args.cmd == "serve":
        from token_diet.server import main as serve_main
        return serve_main()
    if args.cmd == "setup":
        from token_diet.auto_setup import main as setup_main
        return setup_main()
    if args.cmd == "bootstrap":
        from token_diet.bootstrap import generate, show_link
        if args.link:
            print(show_link())
        else:
            path = generate()
            print(f"✓ BOOTSTRAP.md сгенерирован: {path}")
            print("  Кинув этот файл (или его текст) любой ИИ, она сама настроится:")
            print("  установит инструмент, загрузит память, узнает правила и команды.")
            print("  token-diet bootstrap --link  # показать инструкцию")
        return 0
    if args.cmd == "snapshot":
        return _snapshot(args.brief)
    if args.cmd == "panel":
        return _panel(" ".join(args.query) if args.query else None)
    if args.cmd == "scan":
        return _scan()
    if args.cmd == "memo":
        from token_diet.memory_cli import main as memo_main
        return memo_main(args.args)
    if args.cmd == "audit":
        return _audit(args)
    if args.cmd == "army":
        from token_diet.model_army import ask, ask_gemini, ask_omni, army_verdict
        if getattr(args, "omni", False):
            print(f"🛰️  OMNIROUTE ({args.omni_role}):")
            print(ask_omni(args.question, role=args.omni_role, system=args.system))
            return 0
        if args.all:
            print("🤖 АРМИЯ ГОЛОСУЕТ")
            for role, vote in army_verdict(args.question, system=args.system).items():
                print(f"\n🎯 {role}: {vote[:300]}")
        else:
            print(ask(args.question, role=args.role, system=args.system))
        return 0
    if args.cmd == "batch":
        from token_diet.event_batcher import EventBatcher
        if args.demo:
            print(EventBatcher().demo())
        else:
            for overhead in (200, 400, 800):
                est = EventBatcher.estimate_savings(10, overhead)
                print(f"10 событий, оверхед {overhead}т/промпт: "
                      f"экономия {est['saved_tokens']}т ({est['saved_percent']}%)")
        return 0
    if args.cmd == "rules":
        return _rules(args)
    if args.cmd == "handoff":
        from token_diet.session_handoff import build_handoff, render_handoff
        if args.history:
            args.text = Path(args.history).read_text(encoding="utf-8")
        h = build_handoff(args.text, max_history_chars=args.max_chars)
        rendered = h["handoff_text"]
        st = h["stats"]
        if args.save:
            Path(args.save).write_text(rendered, encoding="utf-8")
            print(f"Handoff сохранён: {args.save}")
        print(rendered)
        print(f"\n[handoff] {st['source_chars']} симв. → {st['handoff_chars']} симв."
              f" (экономия {st['saved_pct']}%)")
        return 0
    if args.cmd == "turbo":
        from token_diet.system_turbo import report_text, turbo
        print(report_text(turbo(sudo=True)))
        return 0
    if args.cmd == "scrape":
        if args.capture:
            from token_diet.scrapling_diet import browser_api_grab
            caps = browser_api_grab(args.url, args.capture)
            jsons = [c for c in caps if c.is_json]
            print(f"# {args.url} — перехвачено {len(caps)} ответов, "
                  f"из них JSON: {len(jsons)}")
            for c in jsons[:5]:
                print(f"\n## {c.url} (HTTP {c.status})")
                print(json.dumps(c.json(), ensure_ascii=False,
                                 indent=1)[:4000])
            return 0
        from token_diet.scrapling_diet import scrape_cli
        print(scrape_cli(args.url, selector=args.selector, text=args.text,
                         use_cache=not args.no_cache, proxy=args.proxy))
        return 0
    if args.cmd == "watch":
        from token_diet.market_watcher import main as watch_main
        return watch_main([args.ticker] + (["--init"] if args.init else [])
                          + (["--rules", args.rules] if args.rules else [])
                          + (["--dry-run"] if args.dry_run else [])
                          + (["--once"] if args.once else [])
                          + ["--interval", str(args.interval)])
    p.print_help()
    return 2


def _rules(args) -> int:
    """YAML-движок правил: list / example / run."""
    from pathlib import Path
    from token_diet.workflow_engine import WorkflowEngine
    # --rules работает и до подкоманды, и на самой подкоманде (общий namespace)
    eng = WorkflowEngine(Path(args.rules) if args.rules else None)
    n = eng.load_rules()
    if args.rules_cmd == "list" or args.rules_cmd is None:
        if n == 0:
            print("Правил не найдено. Сгенерируй пример: token-diet rules example")
            print(f"  файл: {eng.rules_path}")
        else:
            print(f"Загружено правил: {n} (из {eng.rules_path})")
            for wf in eng.workflows:
                trig = (wf.get("trigger") or {}).get("on")
                print(f"  • {wf.get('name')}  [{trig}]")
        return 0
    if args.rules_cmd == "example":
        path = eng.save_example(Path(args.path) if args.path else None)
        print(f"✓ Пример правил: {path}")
        print("  Отредактируй под себя и запусти: token-diet rules run price_event")
        return 0
    if args.rules_cmd == "run":
        import json as _json
        try:
            event = _json.loads(args.event)
        except _json.JSONDecodeError:
            print(f"bad --event JSON: {args.event}")
            return 2
        if n == 0:
            print(f"Нет загруженных правил ({eng.rules_path}). Сначала: token-diet rules example")
            return 1
        results = eng.trigger(args.on, event, dry_run=args.dry_run)
        if not results:
            print(f"Событие '{args.on}' не сработало ни по одному правилу")
            return 0
        for r in results:
            print(f"⚡ {r['workflow']} → {r['status']}")
            for s in r.get("steps", []):
                tag = s.get("status")
                extra = s.get("output", {})
                print(f"    [{tag}] {s['id']}")
                if extra and tag not in ("ok", "dry_run"):
                    print(f"        {extra}")
        return 0
    return 2


def _audit(args) -> int:
    """hash-chain журнал: log / predict / verify / last."""
    from token_diet.audit_chain import AuditChain
    chain = AuditChain()
    if args.audit_cmd in (None, "verify"):
        print(chain.tamper_evidence())
        if args.audit_cmd is None:
            return 0
        return 0
    if args.audit_cmd == "last":
        for e in chain.last(args.n):
            print(e.summary())
        return 0
    if args.audit_cmd == "log":
        import json as _json
        try:
            detail = _json.loads(args.detail)
        except _json.JSONDecodeError:
            print(f"bad --detail JSON: {args.detail}")
            return 2
        e = chain.log(args.action, detail=detail, actor=args.actor,
                      object_id=args.object)
        print(f"✓ записано: {e.summary()}")
        return 0
    if args.audit_cmd == "predict":
        e = chain.predict(args.ticker, args.target, by=args.by, note=args.note)
        print(f"✓ прогноз записан в цепочку: {args.ticker.upper()} -> {args.target} "
              f"к {args.by or '?'} {('(' + args.note + ')') if args.note else ''}")
        print(f"  {e.summary()}")
        print("  Подделать задним числом невозможно — verify() докажет.")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
