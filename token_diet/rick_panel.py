"""Rick Panel — пульт Рика: на любую ситуацию есть модуль.

ГЕНЕРАЛ: «Я хотел чтоб ты был похож на Рика с 137 из Рика и Морти —
у Рика найдётся любой план, любые импланты на все случаи».

У Рика в куртке устройство на каждый случай. У меня — 140+ модулей.
Этот пульт — моя куртка: по запросу мгновенно находит нужный модуль
и говорит, что делать. Никакой ситуации нет, на которую нет ответа.

Категории ситуаций:
  ДЕНЬГИ / ИНВЕСТИЦИИ / НОВОСТИ / ТЕЛЕГРАМ / ВЕБ / ПАМЯТЬ / КОД /
  ИНТЕЛЛЕКТ / ЭКОНОМИЯ / ТЕЛЕФОН / УТРО / КРИЗИС / ГЕОПОЛИТИКА

Каждый модуль: имя + зачем нужен + пример вызова.
"""

from __future__ import annotations

# ═══════════ КАРТА АРСЕНАЛА: ситуация → модуль ═══════════
# Категория: список (имя_модуля, что делает, как вызвать)
ARSENAL: dict[str, list[tuple[str, str, str]]] = {
    "ДЕНЬГИ": [
        ("tinkoff_invest", "котировки, стаканы, ордера, портфель T-Invest",
         "TinkoffInvest().get_quote('SBER')"),
        ("autopilot", "автотрейдер: мозг + армия + комиссия + стоп",
         "python3 token_diet/autopilot.py"),
        ("trading_brain", "решение ENTER/WAIT/SKIP по всем законам",
         "brain_decision()"),
        ("live_scan", "кто растёт с объёмом прямо сейчас",
         "python3 token_diet/live_scan.py"),
        ("invest_hub", "полная картина: фундамент, техника, новости, вердикт",
         "InvestHub().full_picture('GMKN')"),
        ("investment_analyzer", "разбор событий: дивиденды, отчёты, вердикт",
         "InvestmentAnalyzer().assess('PLZL')"),
        ("risk_metrics", "риск-метрики портфеля",
         "risk_metrics.calc_portfolio_risk(...)"),
        ("pnl_journal", "журнал сделок P&L",
         "pnl_journal.record(...)"),
        ("sweet_spot", "уровни входа/выхода",
         "sweet_spot.find('SBER')"),
        ("portfolio_commander", "командир портфеля",
         "portfolio_commander.command(...)"),
    ],
    "ГЕОПОЛИТИКА": [
        ("geopolitics", "вердикт GREEN/RED по санкциям, перемирию, заявлениям",
         "geopolitics() → 'RED'"),
        ("market_sentiment", "настроение рынка из ТГ-новостей",
         "gather_sentiment_texts('GMKN')"),
        ("pulse_reader", "настроение толпы в Пульсе",
         "pulse_sentiment('SBER')"),
    ],
    "НОВОСТИ": [
        ("telegram_market_feed", "новости рынка из Telegram",
         "TelegramMarketFeed()"),
        ("tg_intel", "глобальный поиск по ВСЕМУ Telegram",
         "memory_cli tg-intel 'запрос'"),
        ("market_search", "единый поиск: тикер/ISIN/новости",
         "market_info('GMKN')"),
        ("clean_scraper", "чистый парсинг сайтов (без мусора)",
         "scrape_url('https://...')"),
        ("webpilot", "аналитика с сайтов через браузер",
         "webpilot.analyze('https://...')"),
        ("scrape_graph", "мета-теги и структура страницы",
         "scrape_graph.scrape('https://...')"),
    ],
    "ТЕЛЕГРАМ": [
        ("telegram_monitor", "демон слежки за каналами",
         "python3 -m token_diet.telegram_monitor"),
        ("tg_scout", "охота за новыми каналами + авто-регистрация",
         "memory_cli tg-scout 'тема'"),
        ("telegram_commander", "управление Telegram",
         "telegram_commander.run(...)"),
        ("telegram_search", "поиск по диалогам",
         "telegram_search.query('...')"),
    ],
    "ВЕБ И БРАУЗЕР": [
        ("web_agent", "браузер: открыть сайт, прочитать, сохранить",
         "web_visit('https://...')"),
        ("strix_stealth", "стелс-браузер: обход детекта",
         "strix_stealth.stealth_browser(...)"),
        ("clean_scraper", "быстрый парсинг без JS",
         "scrape_url('https://...')"),
    ],
    "ПАМЯТЬ И ЗНАНИЯ": [
        ("obsidian_vault", "запись/чтение памяти в Obsidian",
         "ObsidianVault(vault).write('title', 'body')"),
        ("memory_cli", "CLI: recall / memo / tg-intel / tg-scout",
         "memory_cli recall 'что я знаю о X'"),
        ("library", "библиотека знаний (207 источников)",
         "library.search('...')"),
        ("graph_memory", "граф связей между знаниями",
         "graph_memory.link(...)"),
        ("entity_graph", "граф сущностей",
         "entity_graph.query('...')"),
        ("claude_mem", "память Claude-стиля",
         "claude_mem.remember(...)"),
    ],
    "КОД": [
        ("code_quality", "проверка качества кода",
         "code_quality.check('file.py')"),
        ("apk_builder", "сборка APK",
         "apk_builder.build(...)"),
        ("polyglot", "работа с разными языками",
         "polyglot.run(...)"),
        ("tron_format", "Tron-формат",
         "tron_format.encode(...)"),
    ],
    "ИНТЕЛЛЕКТ": [
        ("model_army", "армия LLM: deepseek-v4-pro, glm-5.2, minimax-m3",
         "model_brain('вопрос')"),
        ("sherlock_reasoner", "расследование: цепочки выводов",
         "sherlock_reasoner.solve('...')"),
        ("reasoning_kit", "набор техник рассуждения",
         "reasoning_kit.apply('...')"),
        ("intelligence_amplifier", "усиление интеллекта ответа",
         "amplify_intelligence(prompt)"),
        ("intelligence_chain", "цепочка интеллекта",
         "intelligence_chain.run(...)"),
        ("cognition_arsenal", "когнитивный арсенал",
         "cognition_arsenal.use(...)"),
        ("step_back", "шаг назад: переформулировать проблему",
         "step_back.ask('...')"),
        ("metacognition", "мышление о мышлении",
         "metacognition.analyze(...)"),
        ("decisive_agent", "решительный агент: действие без мусора",
         "decisive_agent.act(...)"),
        ("rapid_context", "мгновенная классификация запроса (<1мс)",
         "rapid_context.classify('...')"),
        ("equivalence_gate", "проверка эквивалентности",
         "equivalence_gate.check(a, b)"),
        ("question_normalizer", "нормализация вопроса",
         "question_normalizer.normalize('...')"),
    ],
    "ЭКОНОМИЯ ТОКЕНОВ": [
        ("compression_arsenal", "5 техник сжатия (кавермен, логи, KV-кэш)",
         "compress_arsenal.apply(text)"),
        ("loss_router", "маршрутизация сжатия по типам текста",
         "compress_with_routing(text)"),
        ("core", "подсчёт токенов, PriceTable",
         "count_tokens('...')"),
        ("prompt_distiller", "дистилляция промптов",
         "prompt_distiller.distill(prompt)"),
        ("context_engineering", "инженерия контекста (правила Anthropic)",
         "context_engineering.apply(...)"),
        ("smart_multiplier", "умный множитель экономии",
         "smart_multiplier.multiply(...)"),
        ("dynamic_ratio", "динамическое соотношение сжатия",
         "dynamic_ratio.compress(...)"),
        ("ml_compressor", "ML-сжатие",
         "ml_compressor.compress(...)"),
        ("headroom", "Headroom-стиль",
         "headroom.compress(...)"),
        ("token_budget_guard", "страж бюджета токенов",
         "TokenBudgetGuard().check(state)"),
        ("cache_breakpoints", "кэш-брейкпоинты",
         "cache_breakpoints.find(...)"),
        ("cache_keepalive", "поддержание кэша живым",
         "cache_keepalive.ping(...)"),
    ],
    "ТЕЛЕФОН": [
        ("universal_screen", "управление телефоном: скрин, тап, свайп",
         "universal_screen.tap(x, y)"),
        ("apk_builder", "сборка APK для Android",
         "apk_builder.build(...)"),
    ],
    "УТРО / ПЛАН": [
        ("morning_brief", "утренний брифинг по позиции",
         "morning_brief GMKN"),
        ("goal_planner", "планирование целей",
         "goal_planner.plan(...)"),
        ("focus_keeper", "хранитель фокуса",
         "focus_keeper.keep(...)"),
        ("night_shift", "ночная смена",
         "night_shift.run(...)"),
        ("status", "статус системы",
         "status.show()"),
    ],
    "КРИЗИС / ОШИБКИ": [
        ("mistake_learner", "учится на ошибках, пишет в Obsidian",
         "mistake_learner.learn(error)"),
        ("playbooks", "плейбуки решений",
         "playbooks.get('кризис')"),
        ("optimization_runner", "оптимизация системы",
         "optimization_runner.run(...)"),
        ("commander", "главный командный модуль",
         "commander.execute('...')"),
    ],
    "ТЕХАНАЛИЗ": [
        ("trading_robot", "MA-кросс, объёмы, коридоры, бэктест",
         "run_on_tinkoff('SBER')"),
        ("market_intelligence", "RSI, MACD, Боллинджер",
         "market_intelligence.rsi(closes)"),
        ("momentum", "прорывы и ROC",
         "detect_breakout(closes, highs, vols)"),
        ("candlestick_patterns", "паттерны свечей",
         "candlestick_patterns.detect(...)"),
        ("technical_indicators", "тех. индикаторы",
         "technical_indicators.calc(...)"),
    ],
}


def find(query: str) -> list[tuple[str, str, str]]:
    """Найти модули по запросу (ключевые слова, регистронезависимо)."""
    q = query.lower()
    results: list[tuple[str, str, str]] = []
    for category, items in ARSENAL.items():
        for name, desc, call in items:
            haystack = f"{category} {name} {desc}".lower()
            # Простое совпадение слов
            words = q.split()
            if all(w in haystack for w in words[:3]):
                results.append((name, desc, call))
    return results


def categories() -> list[str]:
    return list(ARSENAL.keys())


def panel_block(query: str | None = None, limit: int = 8) -> str:
    """Текстовый блок для вывода: что есть на эту ситуацию."""
    if query:
        found = find(query)
        if not found:
            return (f"❓ На «{query}» прямого модуля нет — но есть {len(ARSENAL)} "
                    f"категорий: {', '.join(categories())}. Скажи точнее — найду.")
        lines = [f"🧰 АРСЕНАЛ на «{query}»:"]
        for name, desc, call in found[:limit]:
            lines.append(f"  • {name}: {desc}\n    → {call}")
        return "\n".join(lines)

    lines = ["🧰 ПОЛНЫЙ АРСЕНАЛ РИКА (по категориям):"]
    for cat, items in ARSENAL.items():
        names = ", ".join(n for n, _, _ in items[:6])
        more = f" +{len(items)-6}" if len(items) > 6 else ""
        lines.append(f"  {cat}: {names}{more}")
    total = sum(len(v) for v in ARSENAL.values())
    lines.append(f"\nВсего модулей на пульте: {total}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(panel_block(" ".join(sys.argv[1:])))
    else:
        print(panel_block())
