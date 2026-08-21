"""market_guard — сторожевой пост по позиции: стакан + золото + новости.

Что делает (всё детерминированно, без нейронок):
  1. snapshot()  — полный снимок: котировка PLZL, стакан (bid/ask, стены,
                   баланс лотов), золото (XAU/USD), P&L по позиции,
                   свежие новости из ленты по драйверам.
  2. stress_test() — стресс-сценарии: что будет с позицией при разных ценах
                   и при движении золота. Показывает P&L в рублях/процентах.
  3. monitor_loop() — фоновый дежурный: каждые N минут снимает снапшот,
                   пишет в Obsidian-память, при пробое критических уровней
                   пишет ALERT в alert-файл (его читаем в любом разговоре)
                   и делает принудительный досбор новостей.
  4. news_sweep() — собрать свежее из Telegram: by_username + global search
                   по драйверам (Полюс, золото, ЦБ, санкции, рубль).

Уровни по Полюсу (настраиваются):
  STOP     — жёсткий стоп (выход без торга)
  FLOOR    — дно покупательской лестницы (если лестницу съели — тезис сломан)
  RED_LINE — красная черта (согласована с генералом)
  BREAKEVEN— цена входа (возврат вложенного)
  TARGET   — цель генерала
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    from .tinkoff_invest import TinkoffInvest
    from . import telegram_monitor as tgm
except Exception:  # pragma: no cover — сырые импорты для прямого запуска
    from tinkoff_invest import TinkoffInvest
    import telegram_monitor as tgm

try:
    from .memory_cli import vault_path
    from .obsidian_vault import ObsidianVault
except Exception:  # pragma: no cover
    from memory_cli import vault_path
    from obsidian_vault import ObsidianVault

GOLD_API = "https://api.gold-api.com/price/XAU"

try:
    from .config import log_path
except Exception:  # pragma: no cover
    from config import log_path

ALERT_FILE = log_path("market_guard_alert.txt")
LOG_FILE = log_path("market_guard.log")

# ── Уровни позиции (можно менять) ─────────────────────────────────────────
@dataclass
class GuardConfig:
    entry: float = 41.50           # цена входа SNGSP
    stop: float = 39.5             # жёсткий стоп (ВЫСТАВЛЕН 16.08)
    floor: float = 40.0            # дно лестницы покупок
    red_line: float = 40.5         # красная черта
    target: float = 43.5           # цель (тейк)
    gold_floor: float = 0.0        # золото не драйвер Сургута — отключено
    figi: str = "BBG004S681M2"
    ticker: str = "SNGSP"
    portfolio_size: float = 18090.0  # для оценочного P&L в рублях


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fetch_gold() -> float | None:
    """Текущая цена золота XAU/USD."""
    import urllib.request

    try:
        req = urllib.request.Request(GOLD_API, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=12).read().decode())
        return float(d.get("price", 0.0)) or None
    except Exception:
        return None


def _read_fresh_news(drivers: list[str] | None = None) -> list[str]:
    """Вытащить из FRESH_NEWS.md посты по драйверам позиции."""
    drivers = drivers or ["полюс", "plzl", "золот", "gold", "цб", "ставк",
                          "санкци", "рубль", "инфляци", "дкп"]
    path = Path.home() / "FRESH_NEWS.md"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    hits: list[str] = []
    cur: list[str] = []
    for line in lines:
        if line.startswith("## ["):
            if cur:
                block = " ".join(cur)
                if any(d in block.lower() for d in drivers):
                    hits.append(block[:500])
                cur = []
        cur.append(line.strip())
    if cur:
        block = " ".join(cur)
        if any(d in block.lower() for d in drivers):
            hits.append(block[:500])
    return hits[-12:]


def get_orderbook(inv: TinkoffInvest, cfg: GuardConfig) -> dict:
    """Стакан с агрегатами: баланс лотов, стены, крупные заявки."""
    ob = inv.get_orderbook(cfg.ticker, depth=20) or {}
    bids, asks = ob.get("bids", []), ob.get("asks", [])
    bq = sum(b.get("quantity", 0) for b in bids)
    aq = sum(a.get("quantity", 0) for a in asks)
    ratio = (bq / aq) if aq else None
    walls = sorted(
        [b for b in bids if b.get("quantity", 0) >= 800],
        key=lambda x: -x.get("quantity", 0),
    )[:4]
    awalls = sorted(
        [a for a in asks if a.get("quantity", 0) >= 800],
        key=lambda x: -x.get("quantity", 0),
    )[:3]
    return {
        "bid_total": bq, "ask_total": aq, "ratio": ratio,
        "best_bid": bids[0] if bids else None,
        "best_ask": asks[0] if asks else None,
        "bid_walls": walls, "ask_walls": awalls,
    }


def snapshot(cfg: GuardConfig | None = None) -> dict:
    """Полный снимок позиции: цена, стакан, золото, P&L, новости."""
    cfg = cfg or GuardConfig()
    inv = TinkoffInvest()
    out: dict = {"time": _now(), "ticker": cfg.ticker}

    # Котировка
    try:
        q = inv.get_quote(cfg.ticker, figi=cfg.figi)
        price = float(q.price) if q else None
        out["price"] = price
    except Exception as e:
        out["quote_error"] = str(e)[:120]
        price = None

    # P&L по позиции
    if price:
        pnl_pct = (price / cfg.entry - 1) * 100
        pnl_rub = (price - cfg.entry) / cfg.entry * cfg.portfolio_size
        out["pnl_pct"] = round(pnl_pct, 2)
        out["pnl_rub"] = round(pnl_rub)
        # расстояние до уровней
        out["to_stop"] = round(price - cfg.stop, 1)
        out["to_breakeven"] = round((cfg.entry / price - 1) * 100, 2)
        out["to_target"] = round((cfg.target / price - 1) * 100, 2)

    # Стакан
    try:
        out["orderbook"] = get_orderbook(inv, cfg)
    except Exception as e:
        out["orderbook_error"] = str(e)[:120]

    # Золото
    gold = _fetch_gold()
    out["gold"] = gold
    if gold is not None:
        out["gold_ok"] = gold >= cfg.gold_floor

    # Новости по драйверам
    out["news"] = _read_fresh_news()

    # Статус по правилам
    out["status"] = _evaluate_status(out, cfg)
    return out


def _evaluate_status(snap: dict, cfg: GuardConfig) -> str:
    price = snap.get("price")
    if price is None:
        return "NO_DATA"
    gold = snap.get("gold")
    gold_bad = gold is not None and gold < cfg.gold_floor
    if price <= cfg.stop:
        return "STOP_HIT"
    if gold_bad:
        return "GOLD_BREACH"
    if price < cfg.red_line:
        return "BELOW_RED"
    if price >= cfg.target:
        return "TARGET_HIT"
    if price >= cfg.breakeven if hasattr(cfg, "breakeven") else price >= cfg.entry:
        return "GREEN"
    return "HOLD"


# ── Стресс-тесты ──────────────────────────────────────────────────────────
def stress_test(cfg: GuardConfig | None = None) -> list[dict]:
    """Сценарии: P&L позиции при разных ценах и движении золота."""
    cfg = cfg or GuardConfig()
    inv = TinkoffInvest()
    try:
        q = inv.get_quote(cfg.ticker, figi=cfg.figi)
        cur = float(q.price) if q else cfg.entry
    except Exception:
        cur = cfg.entry
    gold = _fetch_gold()

    price_scenarios = [
        ("Стоп 1290 пробит", cfg.stop - 10),
        ("Стоп 1290", cfg.stop),
        ("Дно лестницы 1292", cfg.floor),
        ("Сейчас", cur),
        ("Красная черта 1305", cfg.red_line),
        ("Вчерашнее закрытие ~1318", 1318.0),
        ("Безубыток", cfg.entry),
        ("Цель 1400", cfg.target),
        ("Локальный хай 1450", 1450.0),
    ]
    rows = []
    for name, p in price_scenarios:
        rows.append({
            "scenario": name,
            "price": p,
            "pnl_pct": round((p / cfg.entry - 1) * 100, 2),
            "pnl_rub": round((p - cfg.entry) / cfg.entry * cfg.portfolio_size),
        })

    # Золото-сценарии: как Полюс реагирует на ±% золота (бета ~1.2 к золоту)
    if gold:
        for shift, label in [(-3, "золото -3%"), (-1, "золото -1%"),
                             (+1, "золото +1%"), (+3, "золото +3%")]:
            g = gold * (1 + shift / 100)
            est = cur * (1 + shift * 1.2 / 100)  # бета к золоту
            rows.append({
                "scenario": f"{label} (XAU→${g:.0f})",
                "price": round(est, 1),
                "pnl_pct": round((est / cfg.entry - 1) * 100, 2),
                "pnl_rub": round((est - cfg.entry) / cfg.entry * cfg.portfolio_size),
            })
    return rows


def stress_block(cfg: GuardConfig | None = None) -> str:
    """Компактный блок стресс-теста для промпта/отчёта."""
    rows = stress_test(cfg)
    lines = ["[Stress — PLZL]"]
    for r in rows:
        sign = "+" if r["pnl_rub"] >= 0 else ""
        lines.append(f"  {r['scenario']:<26} {r['price']:>7.1f}  "
                     f"{sign}{r['pnl_pct']:.1f}%  {sign}{r['pnl_rub']} ₽")
    return "\n".join(lines)


# ── Новости по драйверам ──────────────────────────────────────────────────
def news_sweep(queries: list[str] | None = None) -> dict:
    """Принудительный досбор свежего по драйверам (даже вне демона)."""
    queries = queries or ["Полюс PLZL", "золото рекорд", "золото цена",
                          "ставка ЦБ", "санкции Россия", "курс рубля"]
    found: list[dict] = []
    errors: list[str] = []
    try:
        r = tgm.by_username(limit=2)
        if r.get("status") == "ok":
            for item in r.get("results", [])[:10]:
                low = item["text"].lower()
                if any(k in low for k in
                       ["полюс", "plzl", "золот", "gold", "цб", "ставк"]):
                    found.append(item)
    except Exception as e:
        errors.append(f"by_username: {str(e)[:100]}")
    for q in queries[:4]:
        try:
            r = tgm.global_search(q, limit=4)
            if r.get("status") == "ok":
                found += r.get("results", [])
        except Exception as e:
            errors.append(f"global({q[:12]}): {str(e)[:80]}")
    # дедуп + сортировка по свежести
    seen: set[str] = set()
    uniq = []
    for item in found:
        key = f"{item.get('channel')}|{item.get('text','')[:60]}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    uniq.sort(key=lambda x: x.get("date", ""), reverse=True)
    return {"status": "ok", "found": len(uniq),
            "results": uniq[:15], "errors": errors}


def news_block(queries: list[str] | None = None, max_items: int = 8) -> str:
    """Компактный блок новостей по драйверам."""
    r = news_sweep(queries)
    lines = [f"[News — drivers] ({r['found']} найдено)"]
    for item in r["results"][:max_items]:
        text = (item.get("text") or "").replace("\n", " ")[:220]
        lines.append(f"  • {item.get('channel','?')}: {text}")
    if r.get("errors"):
        lines.append(f"  ⚠️ {r['errors'][0]}")
    return "\n".join(lines)


# ── Фоновый дежурный ──────────────────────────────────────────────────────
def _append_log(line: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _write_alert(title: str, body: str) -> None:
    try:
        ALERT_FILE.write_text(
            f"# ⚠️ {title}\n\n{body}\n\n— market_guard {_now()}\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def monitor_loop(interval_min: int = 10, cfg: GuardConfig | None = None,
                 max_loops: int = 0) -> None:
    """Дежурный цикл: снапшот → Obsidian + alert-файл при пробоях.

    max_loops=0 — бесконечно (для демона в tmux/nohup).
    """
    cfg = cfg or GuardConfig()
    _append_log(f"[market_guard] запущен, интервал {interval_min} мин, "
                f"стоп {cfg.stop}, красная {cfg.red_line}")
    loop = 0
    while True:
        loop += 1
        snap = snapshot(cfg)
        status = snap.get("status", "NO_DATA")
        price = snap.get("price")
        _append_log(f"[{snap['time']}] #{loop} {cfg.ticker}={price} "
                    f"status={status} gold={snap.get('gold')}")

        # Пробой уровней → ТРЕВОГА
        if status == "STOP_HIT":
            _write_alert(
                f"СТОП ПРОБИТ — {cfg.ticker} выход по правилам",
                f"{cfg.ticker} {price} ≤ стоп {cfg.stop}\n"
                f"P&L: {snap.get('pnl_pct')}% ({snap.get('pnl_rub')} ₽)\n"
                f"Стакан: {snap.get('orderbook', {}).get('ratio')}:1\n"
                f"Золото: {snap.get('gold')}",
            )
        elif status == "GOLD_BREACH":
            _write_alert(
                "ЗОЛОТО ПРОБИЛО ПОЛ — тезис шатается",
                f"XAU {snap.get('gold')} < {cfg.gold_floor}\n"
                f"{cfg.ticker} {price} (P&L {snap.get('pnl_pct')}%)",
            )
        elif status == "TARGET_HIT":
            _write_alert(
                f"ЦЕЛЬ ДОСТИГНУТА — {cfg.ticker}",
                f"{cfg.ticker} {price} ≥ {cfg.target}\n"
                f"P&L: {snap.get('pnl_pct')}% ({snap.get('pnl_rub')} ₽)",
            )
        elif price is not None and status == "BELOW_RED" and loop % 6 == 0:
            _append_log(f"  ниже красной {cfg.red_line}: {price}")

        # Авто-досбор ленты: если FRESH_NEWS старее 30 минут — дособрать
        # по драйверам и дописать в файл (демон может лежать)
        if loop % 2 == 1:
            try:
                fresh = Path.home() / "FRESH_NEWS.md"
                stale = (not fresh.exists()
                         or time.time() - fresh.stat().st_mtime > 1800)
                if stale:
                    sw = news_sweep()
                    for item in sw.get("results", [])[:8]:
                        text = item.get("text") or ""
                        if not text:
                            continue
                        tgm._append_fresh(text.splitlines()[0][:80], text,
                                          item.get("channel", "?"))
                    _append_log(f"  лента устарела — дособрал "
                                f"{len(sw.get('results', [])[:8])} постов")
            except Exception as e:
                _append_log(f"  досбор ленты: {str(e)[:100]}")

        # Запись в Obsidian-память с ДЕДУПОМ: одна заметка на день/тикер
        # (УРОК 16.08: раньше плодилось по файлу каждый цикл — 82 шт STOP_HIT)
        if loop % 2 == 1:
            try:
                vault = ObsidianVault(vault_path())
                ob = snap.get("orderbook", {})
                ratio = ob.get("ratio")
                body = (f"ДЕЖУРНЫЙ СНАПШОТ #{loop} {snap['time']}:\n"
                        f"{cfg.ticker} {price} (P&L {snap.get('pnl_pct')}%, "
                        f"{snap.get('pnl_rub')} ₽) status={status}\n"
                        f"Стакан: bid {ob.get('bid_total')} / "
                        f"ask {ob.get('ask_total')} = {ratio}:1, "
                        f"стена бид {ob.get('bid_walls', [])[:1]}, "
                        f"стена аск {ob.get('ask_walls', [])[:1]}\n"
                        f"Золото: {snap.get('gold')} "
                        f"({'(ниже пола!)' if snap.get('gold_ok') is False else ''})\n"
                        f"Новости по драйверам: {len(snap.get('news', []))} в ленте")
                # дедуп: vault.write перезаписывает файл с тем же именем,
                # так что за день будет ОДНА заметка на тикер (не 72 файла)
                day_file = f"market_guard_{cfg.ticker}_{_now()[:10]}.md"
                vault.write(day_file, body)
            except Exception as e:
                _append_log(f"  obsidian: {str(e)[:100]}")

        if max_loops and loop >= max_loops:
            break
        time.sleep(interval_min * 60)


def pulse_block(ticker: str = "PLZL", max_items: int = 4) -> str:
    """Свежее настроение Пульса по тикеру (топ по лайкам)."""
    from .pulse_reader import digest

    try:
        return digest(ticker, limit=max_items)
    except Exception as e:  # сеть упала — не роняем сторож
        return f"[Пульс] {ticker}: ошибка ({str(e)[:60]})"


def snapshot_block(cfg: GuardConfig | None = None) -> str:
    """Компактный блок снимка для промпта."""
    snap = snapshot(cfg)
    ob = snap.get("orderbook", {})
    ticker = (cfg.ticker if cfg else "PLZL").upper()
    lines = [
        f"[Guard — {ticker}] {snap['time']}  status={snap.get('status')}",
        f"  Цена: {snap.get('price')} | P&L {snap.get('pnl_pct')}% "
        f"({snap.get('pnl_rub')} ₽)",
        f"  Стакан: bid {ob.get('bid_total')} / ask {ob.get('ask_total')} "
        f"= {ob.get('ratio')}:1",
    ]
    if ob.get("best_bid"):
        lines.append(f"  Лучший бид {ob['best_bid']['price']} "
                     f"x{ob['best_bid']['quantity']} | лучший аск "
                     f"{ob['best_ask']['price']} x{ob['best_ask']['quantity']}")
    for label, walls in [("Стены покупки", ob.get("bid_walls", [])),
                         ("Стены продажи", ob.get("ask_walls", []))]:
        if walls:
            w = ", ".join(f"{x['price']}(x{x['quantity']})" for x in walls[:3])
            lines.append(f"  {label}: {w}")
    lines.append(f"  Золото: {snap.get('gold')}")
    if snap.get("news"):
        lines.append(f"  Новости в ленте: {len(snap.get('news'))} "
                     f"({ticker.lower()})")
    lines.append(pulse_block(ticker, max_items=3))
    try:
        from .pulse_reader import pulse_sentiment

        s = pulse_sentiment(ticker, limit=20)
        if s.get("sample_size"):
            lines.append(
                f"  Пульс-настроение: {s['signal']} "
                f"(быки {s['bullish_pct']}% / медведи {s['bearish_pct']}%, "
                f"n={s['sample_size']})"
            )
            if s.get("contrarian"):
                lines.append(f"  ⚡ {s['contrarian']}")
    except Exception:
        pass  # сентимент не критичен — сторож живёт и без него
    return "\n".join(lines)


__all__ = ["GuardConfig", "snapshot", "snapshot_block", "stress_test",
           "stress_block", "news_sweep", "news_block", "pulse_block",
           "monitor_loop", "ALERT_FILE", "LOG_FILE"]


if __name__ == "__main__":  # python3 -m token_diet.market_guard
    import argparse

    p = argparse.ArgumentParser(description="Сторожевой пост по позиции")
    p.add_argument("--once", action="store_true",
                   help="один снапшот + стресс-тест и выход")
    p.add_argument("--interval", type=int, default=10,
                   help="интервал дежурного цикла, мин (по умолчанию 10)")
    p.add_argument("--news", action="store_true",
                   help="только досбор новостей по драйверам")
    p.add_argument("--loops", type=int, default=0,
                   help="сколько циклов отработать (0 = бесконечно)")
    args = p.parse_args()

    if args.news:
        print(news_block(max_items=12))
    elif args.once:
        print(snapshot_block())
        print()
        print(stress_block())
    else:
        print(f"🛡️ market_guard: дежурный цикл каждые {args.interval} мин. "
              f"Лог: {LOG_FILE}, тревоги: {ALERT_FILE}", flush=True)
        monitor_loop(interval_min=args.interval, max_loops=args.loops)
