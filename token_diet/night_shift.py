"""Night Shift — автономный командир, работающий без «меня».

Запускается по расписанию (cron): собирает полную картину портфеля,
рассуждает через DeepSeek с контекстом, и пишет доклад с рекомендацией.

Что делает:
  1. Полный план портфеля (PortfolioCommander) + сторож (свежие цены)
  2. Полная картина по каждой позиции (InvestHub: цена/стакан/новости/техника)
  3. Рассуждение через DeepSeek (ваш ключ, локально) — вывод + рекомендация
  4. Пишет доклад в файл и возвращает путь

Честность:
  - Доклад — это РАССУЖДЕНИЕ, а не гарантия. Никогда не обещает прибыль.
  - Ордера не ставит. Стоп/тейк на бирже уже стоят — смена только наблюдает.
  - Нет данных → пишет «нет данных», не выдумывает.

Usage:
    python3 -m token_diet.night_shift [--report-dir ~/token-diet-reports]
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path

from .date_anchor import full_date_context
from .invest_hub import InvestHub
from .portfolio_commander import PortfolioCommander

REPORT_DIR = Path.home() / "token-diet-reports"
DEEPSEEK_KEY_FILE = Path.home() / ".token-diet" / "deepseek_key"
SECRETS_FILE = Path.home() / ".invest_bot" / "secrets.env"

SYSTEM_PROMPT = """Ты — ночной командир портфеля token-diet. Ты работаешь автономно, без человека рядом.
Ниже — фактический контекст: портфель, план по позициям, свежие цены, стакан, новости, техника.
Твоя задача — ЧЕСТНЫЙ доклад:
1. Что происходит с позициями прямо сейчас (по фактам, не по догадкам).
2. Риски и катализаторы (например, макро-события: CPI, заседания ЦБ, дивидендные отсечки).
3. Рекомендация на завтра: держать / докупить / выйти / переставить стоп. Обоснуй числами.
ПРАВИЛА:
- Никогда не обещай прибыль. Это рассуждение, а не гарантия.
- Не выдумывай цифры — только то, что есть в контексте. Нет данных → так и скажи.
- Стоп-лоссы и тейки на бирже стоят сами; ты не исполняешь сделки.
- Пиши по-русски, кратко и по делу (до 600 слов)."""


def load_deepseek_key() -> str:
    """Ключ DeepSeek: env → ~/.token-diet/deepseek_key → secrets.env."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key and "PASTE" not in key:
        return key
    if DEEPSEEK_KEY_FILE.exists():
        v = DEEPSEEK_KEY_FILE.read_text().strip()
        if v and "PASTE" not in v:
            return v
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v and "PASTE" not in v:
                    return v
    return ""


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def collect_context() -> dict:
    """Собрать полный фактический контекст: портфель + позиции + дата."""
    ctx: dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": full_date_context(),
    }
    pc = PortfolioCommander()
    ctx["portfolio_plan"] = pc.plan()
    ctx["watch"] = pc.watch()

    # полная картина по каждой позиции
    positions = ctx["portfolio_plan"].get("positions", [])
    pictures = []
    for p in positions:
        ticker = p["ticker"]
        hub = InvestHub()
        pic = _safe(lambda t=ticker, h=hub: h.full_picture(
            t, include_news=True, include_orderbook=True), None)
        if pic:
            pictures.append(pic)
    ctx["tickers_picture"] = pictures
    return ctx


def ask_deepseek(key: str, context: dict) -> str:
    """Рассуждение DeepSeek по контексту."""
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(
                context, ensure_ascii=False, default=str)[:12000]},
        ],
        "temperature": 0.4,
        "max_tokens": 1500,
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    return d["choices"][0]["message"]["content"].strip()


def render_report(context: dict, reasoning: str) -> str:
    """Собрать читаемый доклад: факты + рассуждение."""
    lines = ["═══ НОЧНОЙ ДОКЛАД КОМАНДИРА ═══",
             f"Сформирован: {context['generated_at']}",
             f"Дата: {context['date'].get('today_ru')} "
             f"({context['date'].get('weekday')})"]

    plan = context.get("portfolio_plan", {})
    if "error" in plan:
        lines.append(f"\n❌ Портфель: {plan['error']}")
    else:
        lines.append("\n--- ПОРТФЕЛЬ ---")
        for p in plan.get("positions", []):
            lines.append(
                f"{p['ticker']}: {p['quantity']:.0f} шт, средняя {p['avg_price']:.2f}₽, "
                f"сейчас {p['current_price']:.2f}₽ ({p['profit_pct']:+.2f}%)")
            lines.append(f"  🛑 стоп {p['stop_loss']}₽  ✅ тейк {p['take_profit']}₽  "
                         f"действие: {p['action']}")
            lines.append(f"  {p['reason']}")

    w = context.get("watch", {})
    if w.get("alerts"):
        lines.append("\n--- СТОРОЖ ---")
        for a in w["alerts"]:
            lines.append(f"  {a['alert']:12s} {a['ticker']}: {a['message']}")

    for pic in context.get("tickers_picture", []):
        lines.append(f"\n--- {pic.get('ticker')} — ПОЛНАЯ КАРТИНА ---")
        q = pic.get("quote", {})
        if q.get("price") is not None:
            ch = q.get("change_pct")
            lines.append(f"Цена: {q['price']:.2f}₽"
                         + (f" ({ch:+.2f}%)" if ch is not None else ""))
        tech = pic.get("technical")
        if tech and "error" not in tech:
            lines.append(f"Техника: {tech.get('verdict')} "
                         f"(conf {tech.get('confidence')})")
            rng = tech.get("expected_range")
            if rng:
                lines.append(f"Диапазон: {rng['low']} — {rng['high']}₽")
        book = pic.get("orderbook")
        if book and "error" not in book:
            lines.append(f"Стакан: {book.get('interpretation')} "
                         f"(давление {book.get('pressure_ask_over_bid')}x)")
        news = pic.get("news")
        if news and news.get("messages"):
            sent = news.get("sentiment")
            if sent:
                lines.append(f"Новости: {len(news['messages'])} сообщений, "
                             f"сентимент {sent.get('signal')}")
            for m in news["messages"][:3]:
                lines.append(f"  [{m['channel']}] {m['text'][:90]}")
        v = pic.get("verdict", {})
        if v and "error" not in v:
            lines.append(f"Вердикт: {v.get('action').upper()} "
                         f"(conf {v.get('confidence')})")

    lines.append("\n--- РАССУЖДЕНИЕ КОМАНДИРА ---")
    lines.append(reasoning)
    lines.append("\n⚠️ Это рассуждение, а не гарантия прибыли. "
                 "Сделки исполняет владелец.")
    return "\n".join(lines)


def run(report_dir: str | None = None) -> Path:
    """Полный прогон ночной смены. Возвращает путь к докладу."""
    directory = Path(report_dir or REPORT_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    key = load_deepseek_key()
    context = collect_context()

    if key:
        reasoning = _safe(lambda: ask_deepseek(key, context),
                          "❌ DeepSeek не ответил (сеть/ключ). "
                          "Факты выше собраны, рассуждение пропущено.")
    else:
        reasoning = "❌ Нет DEEPSEEK_API_KEY — рассуждение пропущено. " \
                    "Положи ключ в ~/.token-diet/deepseek_key"

    report = render_report(context, reasoning)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = directory / f"night_shift_{stamp}.md"
    path.write_text(report, encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", default=None)
    args = ap.parse_args()
    path = run(args.report_dir)
    print(f"✅ Ночной доклад сохранён: {path}")


if __name__ == "__main__":
    main()

__all__ = ["run", "collect_context", "render_report", "load_deepseek_key",
           "REPORT_DIR", "SYSTEM_PROMPT"]
