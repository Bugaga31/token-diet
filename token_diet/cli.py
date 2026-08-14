"""token-diet — единый командный интерфейс всего арсенала.

Один инструмент вместо сотни скриптов: память, рынок, мозг, армия,
панель Рика, прокси, установка, доктор и быстрый самотест.

    token-diet                 # обзор арсенала
    token-diet doctor          # проверить, что все модули живы
    token-diet self-test       # быстрый офлайн-тест ядра (без сети)
    token-diet memo ...        # память Obsidian (все подкоманды memory_cli)
    token-diet panel <запрос>  # пульт Рика: что есть на ситуацию
    token-diet scan            # живой сканер рынка (рост + объём + стакан)
    token-diet serve           # прокси-сервер экономии токенов
    token-diet setup           # автоконфигурация под Claude/OpenCode/...
"""

from __future__ import annotations

import argparse
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
            "  token-diet memo remember 'урок: ...'\n"
            "  token-diet panel деньги    # что есть на «деньги»\n"
            "  token-diet scan            # живой сканер рынка\n"
            "  token-diet serve           # прокси экономии токенов\n"
        ),
    )
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="проверка здоровья всех модулей")
    sub.add_parser("self-test", help="быстрый офлайн-тест ядра")
    sub.add_parser("serve", help="прокси-сервер экономии токенов")
    sub.add_parser("setup", help="автоконфигурация под Claude Code/OpenCode/...")

    p_panel = sub.add_parser("panel", help="пульт Рика: модули под задачу")
    p_panel.add_argument("query", nargs="*", default=[], help="что ищем (деньги, память, кризис...)")

    sub.add_parser("scan", help="живой сканер рынка (рост+объём+стакан)")

    p_memo = sub.add_parser("memo", help="память Obsidian (см. token-diet memo -h)")
    p_memo.add_argument("args", nargs=argparse.REMAINDER, help="подкоманды memory_cli")

    args = p.parse_args(argv)

    if args.cmd is None:
        from token_diet.rick_panel import panel_block
        print(panel_block())
        print("\nКоманды: doctor | self-test | serve | setup | panel <что> | scan | memo ...")
        return 0

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
    if args.cmd == "panel":
        return _panel(" ".join(args.query) if args.query else None)
    if args.cmd == "scan":
        return _scan()
    if args.cmd == "memo":
        from token_diet.memory_cli import main as memo_main
        return memo_main(args.args)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
