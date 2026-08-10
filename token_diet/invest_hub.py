"""InvestHub — единый вход во все инвест-инструменты token-diet.

Один класс, один вызов = полная картина рынка. Никакого импорта пяти
модулей и ручной склейки: всё уже собранно в JSON-совместимые dict'ы.

Объединяет (лениво, с деградацией):
  - TinkoffInvest   — портфель, котировки, свечи, стакан (токен, живой)
  - MoexFeed        — котировки, свечи, дивиденды (БЕСПЛАТНО, без токена)
  - market_intelligence — RSI/MACD/Bollinger/SMA консенсус, сентимент
  - trading_robot   — MA cross, Volume Profile POC, interval, backtest
  - investment_analyzer — ловушки дивидендов, priced-in, комитет
  - telegram_market_feed — сентимент из курируемых каналов
  - date_anchor     — точная дата и торговые дни (модель не угадывает)

Принципы:
  - Всё возвращается как dict — удобно читать, логировать, передавать.
  - Никогда не бросает исключений: каждая секция в своём try/except.
  - Честность: нет данных → пишем «нет данных», а не выдумываем.
  - Без токена Т-Инвестиций хаб работает на MOEX (free fallback).

For the people. For the planet. Honesty is cheaper than regret.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .tinkoff_invest import KNOWN_FIGI, TinkoffInvest, get_token
from .moex_feed import MoexFeed
from .market_intelligence import generate_signal, estimate_price_range, aggregate_sentiment
from .trading_robot import run_strategies, backtest
from .investment_analyzer import InvestmentAnalyzer, CommitteeVote, NewsItem
from .date_anchor import full_date_context


# ─────────────────────────────────────────────────────────────────────────────
# Внутренние хелперы
# ─────────────────────────────────────────────────────────────────────────────

def _safe(fn, default=None):
    """Выполнить fn; при ЛЮБОЙ ошибке вернуть default. Никогда не бросает."""
    try:
        return fn()
    except Exception:
        return default


def _quotation_to_float(q: dict | None) -> float:
    """Tinkoff Quotation {units, nano} → float, с защитой от кривых данных."""
    if not q or not isinstance(q, dict):
        return 0.0
    try:
        return float(q.get("units", 0)) + float(q.get("nano", 0)) / 1e9
    except (TypeError, ValueError):
        return 0.0


def _candle_to_dict(c) -> dict:
    """Любая свеча (TinkoffCandle / MoexCandle) → dict."""
    t = getattr(c, "time", None)
    return {
        "time": t.isoformat() if isinstance(t, datetime) else str(t),
        "open": round(float(getattr(c, "open", 0)), 2),
        "high": round(float(getattr(c, "high", 0)), 2),
        "low": round(float(getattr(c, "low", 0)), 2),
        "close": round(float(getattr(c, "close", 0)), 2),
        "volume": float(getattr(c, "volume", 0)),
    }


# ─────────────────────────────────────────────────────────────────────────────

class InvestHub:
    """Единый хаб всех инвест-инструментов.

    Usage:
        hub = InvestHub()
        pic = hub.full_picture("PLZL")   # ВСЁ по бумаге одним вызовом
        print(InvestHub.render(pic))
        book = hub.orderbook("PLZL")     # стакан со стенами и давлением
        pos  = hub.portfolio()           # портфель с сигналами
    """

    def __init__(
        self,
        token: str | None = None,
        session_path: str = "~/.telegram-mcp/telegram_live.session",
        credentials_path: str | None = None,
        news_enabled: bool = True,
    ):
        self.token = token or get_token()
        self.tinkoff = TinkoffInvest(self.token)
        self.moex = MoexFeed()
        self._news_enabled = news_enabled
        self._feed = None
        self._session_path = session_path
        self._credentials_path = credentials_path

    # ── ленивый Telegram-фид ──────────────────────────────────────────────
    def _get_feed(self):
        if not self._news_enabled:
            return None
        if self._feed is None:
            from .telegram_market_feed import TelegramMarketFeed
            self._feed = TelegramMarketFeed(
                session_path=self._session_path,
                credentials_path=self._credentials_path,
            )
        return self._feed

    # ── статус ────────────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        """Что доступно: токен, источники, фичи."""
        return {
            "tinkoff_token": bool(self.token),
            "sources": {
                "tinkoff": "live" if self.tinkoff.available else "disabled",
                "moex": "free",
                "telegram_news": "enabled" if self._news_enabled else "disabled",
            },
            "known_tickers": len(KNOWN_FIGI),
        }

    # ── котировка (Tinkoff + MOEX) ────────────────────────────────────────
    def quote(self, ticker: str) -> dict[str, Any]:
        """Цена из обоих источников. MOEX работает без токена."""
        out: dict[str, Any] = {"ticker": ticker.upper()}

        # Tinkoff (живая, если есть токен)
        tq = _safe(lambda: self.tinkoff.get_quote(ticker))
        if tq is not None:
            out["tinkoff"] = {
                "price": round(float(getattr(tq, "price", 0)), 2),
                "time": str(getattr(tq, "time", "")),
            }
        else:
            out["tinkoff"] = None

        # MOEX (free, всегда)
        mq = _safe(lambda: self.moex.get_quote(ticker))
        if mq is not None:
            out["moex"] = {
                "price": round(float(getattr(mq, "price", 0)), 2),
                "prev_price": round(float(getattr(mq, "prev_price", 0) or 0), 2),
                "change_pct": getattr(mq, "change_pct", None),
                "currency": getattr(mq, "currency", "RUB"),
            }
        else:
            out["moex"] = None

        # Единая цена: Tinkoff > MOEX
        price = None
        if out["tinkoff"]:
            price = out["tinkoff"]["price"]
        elif out["moex"]:
            price = out["moex"]["price"]
        out["price"] = price
        out["change_pct"] = (out["moex"] or {}).get("change_pct")
        return out

    # ── свечи ─────────────────────────────────────────────────────────────
    def candles(self, ticker: str, days: int = 60, source: str = "auto") -> list[dict]:
        """Свечи как список dict'ов. auto: Tinkoff → MOEX fallback."""
        ticker = ticker.upper()
        if source == "tinkoff" or (source == "auto" and self.tinkoff.available):
            got = _safe(lambda: self.tinkoff.get_candles(ticker, days=days), [])
            if got:
                return [_candle_to_dict(c) for c in got]
        got = _safe(lambda: self.moex.get_candles(ticker, days=days), [])
        return [_candle_to_dict(c) for c in got]

    # ── технический анализ (market_intelligence) ──────────────────────────
    def technical(self, ticker: str, days: int = 90) -> dict[str, Any]:
        """Консенсус индикаторов: RSI/MACD/SMA/Bollinger + диапазон на завтра."""
        candles = self.candles(ticker, days=days)
        if len(candles) < 31:
            return {"ticker": ticker.upper(), "error": f"мало данных ({len(candles)} свечей, нужно 31+)"}

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        vols = [c["volume"] for c in candles]

        sig = _safe(lambda: generate_signal(ticker, closes, highs, lows, vols), None)
        rng = _safe(lambda: estimate_price_range(ticker, closes), None)

        out: dict[str, Any] = {
            "ticker": ticker.upper(),
            "price": closes[-1],
            "last_candle_date": candles[-1]["time"][:10],
        }
        if sig is not None:
            out["verdict"] = sig.final_verdict
            out["confidence"] = round(sig.final_confidence, 3)
            out["technical"] = sig.technical_verdict
            out["sentiment"] = sig.sentiment_signal
            out["signals"] = [
                {"name": s.name, "direction": s.direction,
                 "strength": round(s.strength, 2), "description": s.description}
                for s in sig.signals_detail
            ]
            out["targets"] = {
                k: [round(x, 2) for x in v] for k, v in (sig.price_targets or {}).items()
            }
        if rng is not None and "error" not in rng:
            out["expected_range"] = {
                "low": rng.get("expected_low"),
                "high": rng.get("expected_high"),
                "daily_volatility_pct": rng.get("daily_volatility_pct"),
            }
        return out

    # ── роботы (trading_robot) ────────────────────────────────────────────
    def robot(self, ticker: str, days: int = 90) -> dict[str, Any]:
        """Стратегии роботов: MA cross, Volume Profile POC, interval, backtest."""
        candles = self.candles(ticker, days=days)
        if len(candles) < 21:
            return {"ticker": ticker.upper(), "error": f"мало данных ({len(candles)} свечей)"}

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        vols = [c["volume"] for c in candles]

        strat = _safe(lambda: run_strategies(closes, highs, lows, vols), None)
        bt = _safe(lambda: backtest(closes), None)

        out: dict[str, Any] = {"ticker": ticker.upper()}
        if strat is not None:
            out["strategies"] = strat
        if bt is not None:
            out["backtest"] = {
                "verdict": bt.verdict,
                "total_pnl_pct": bt.total_pnl_pct,
                "win_rate": bt.win_rate,
                "max_drawdown_pct": bt.max_drawdown_pct,
                "trades": len(bt.trades),
            }
        return out

    # ── стакан со стенами и давлением ─────────────────────────────────────
    def orderbook(self, ticker: str, depth: int = 10) -> dict[str, Any]:
        """Стакан (Tinkoff): уровни, стены, давление, интерпретация.

        Возвращает asks/bids как списки {price, quantity, volume_rub},
        суммарные объёмы, давление ask/bid и крупнейшие стены.
        """
        if not self.tinkoff.available:
            return {"ticker": ticker.upper(), "error": "нет токена Т-Инвестиций"}

        data = _safe(lambda: self.tinkoff.get_orderbook(ticker, depth=depth), None)
        if not data:
            return {"ticker": ticker.upper(), "error": "стакан не получен"}

        def _levels(raw: list) -> list[dict]:
            out = []
            for item in raw or []:
                if not isinstance(item, dict):
                    continue
                price = float(item.get("price", 0))
                qty = int(item.get("quantity", 0))
                if price <= 0:
                    continue
                out.append({
                    "price": round(price, 2),
                    "quantity": qty,
                    "volume_rub": round(price * qty, 0),
                })
            return out

        asks = _levels(data.get("asks"))
        bids = _levels(data.get("bids"))

        ask_vol = sum(a["quantity"] for a in asks)
        bid_vol = sum(b["quantity"] for b in bids)
        ask_rub = sum(a["volume_rub"] for a in asks)
        bid_rub = sum(b["volume_rub"] for b in bids)

        out: dict[str, Any] = {
            "ticker": ticker.upper(),
            "asks": asks,
            "bids": bids,
            "ask_volume": ask_vol,
            "bid_volume": bid_vol,
            "ask_rub": round(ask_rub, 0),
            "bid_rub": round(bid_rub, 0),
            "pressure_ask_over_bid": round(ask_vol / max(bid_vol, 1), 2),
        }

        # Крупнейшие стены
        walls = {"ask": None, "bid": None}
        if asks:
            top = max(asks, key=lambda a: a["quantity"])
            walls["ask"] = top
        if bids:
            top = max(bids, key=lambda b: b["quantity"])
            walls["bid"] = top
        out["biggest_wall"] = walls

        # Спред
        if asks and bids:
            out["spread"] = round(asks[0]["price"] - bids[0]["price"], 2)
            out["mid_price"] = round((asks[0]["price"] + bids[0]["price"]) / 2, 2)
        else:
            out["spread"] = None

        # Интерпретация (честная, по цифрам)
        interp = "neutral"
        reasons = []
        if walls["bid"] and walls["ask"]:
            if walls["bid"]["quantity"] > walls["ask"]["quantity"] * 1.5:
                interp = "bullish"
                reasons.append(f"стена покупки {walls['bid']['quantity']} шт больше стены продажи "
                               f"{walls['ask']['quantity']} шт в 1.5+ раза — накопление/поддержка")
            elif walls["ask"]["quantity"] > walls["bid"]["quantity"] * 1.5:
                interp = "bearish"
                reasons.append(f"стена продажи {walls['ask']['quantity']} шт больше стены покупки "
                               f"{walls['bid']['quantity']} шт в 1.5+ раза — давление продавцов")
            else:
                reasons.append("стены сопоставимы — битва уровней, равновесие")
        if out["pressure_ask_over_bid"] is not None:
            if out["pressure_ask_over_bid"] > 1.3:
                reasons.append(f"суммарный объём продаж > покупок ({out['pressure_ask_over_bid']}x)")
            elif out["pressure_ask_over_bid"] < 0.77:
                reasons.append(f"суммарный объём покупок > продаж ({out['pressure_ask_over_bid']}x)")
        out["interpretation"] = interp
        out["reasons"] = reasons
        return out

    # ── портфель с сигналами по каждой позиции ────────────────────────────
    def portfolio(self, days: int = 90) -> dict[str, Any]:
        """Позиции портфеля + по каждой: котировка, технический сигнал, роботы."""
        positions = _safe(lambda: self.tinkoff.get_portfolio(), None)
        if positions is None:
            return {"error": "нет доступа к портфелю (нет токена Т-Инвестиций)"}

        rows = []
        total_profit = 0.0
        for p in positions:
            ticker = getattr(p, "ticker", "UNKNOWN")
            row = {
                "ticker": ticker,
                "quantity": float(getattr(p, "quantity", 0)),
                "avg_price": round(float(getattr(p, "avg_price", 0)), 2),
                "current_price": round(float(getattr(p, "current_price", 0)), 2),
                "profit_pct": getattr(p, "profit_pct", 0.0),
            }
            qty = row["quantity"]
            profit = (row["current_price"] - row["avg_price"]) * qty
            total_profit += profit
            row["profit_rub"] = round(profit, 2)

            if ticker != "UNKNOWN" and not ticker.startswith("uid:"):
                tech = _safe(lambda: self.technical(ticker, days=days), None)
                if tech and "error" not in tech:
                    row["signal"] = {
                        "verdict": tech.get("verdict"),
                        "confidence": tech.get("confidence"),
                        "target_3pct": round(tech["price"] * 1.03, 2),
                        "stop_5pct": round(tech["price"] * 0.95, 2),
                    }
            rows.append(row)

        rows.sort(key=lambda r: r["profit_rub"], reverse=True)
        return {
            "positions": rows,
            "total_profit_rub": round(total_profit, 2),
            "count": len(rows),
        }

    # ── фундаментал: дивиденды ────────────────────────────────────────────
    def fundamentals(self, ticker: str) -> dict[str, Any]:
        """Дивиденды MOEX: история, доходность, ближайшие отсечки."""
        ticker = ticker.upper()
        yield_info = _safe(lambda: self.moex.dividend_yield(ticker), None)
        dividends = _safe(lambda: self.moex.get_dividends(ticker, limit=8), [])
        next_div = _safe(lambda: self.moex.next_dividends([ticker]), [])

        out: dict[str, Any] = {"ticker": ticker}
        if yield_info and "error" not in yield_info:
            out["yield"] = {
                "yield_pct": yield_info.get("yield_pct"),
                "sum_last_12m": yield_info.get("sum_last_12m"),
                "count": yield_info.get("count"),
            }
        else:
            out["yield"] = None
        out["history"] = [
            {"ex_date": str(d.registry_close) if d.registry_close else None,
             "value": round(float(d.value), 2), "currency": d.currency}
            for d in dividends
        ]
        out["next"] = next_div
        return out

    # ── новости и сентимент (Telegram) ────────────────────────────────────
    def news(self, ticker: str, limit: int = 8) -> dict[str, Any]:
        """Свежие сообщения каналов про тикер + агрегированный сентимент.

        Сканирует больше сообщений на канал (limit), чтобы не пропустить
        тикер, которого нет в первых 2-3 постах каждого канала.
        """
        ticker = ticker.upper()
        feed = self._get_feed()
        if feed is None:
            return {"ticker": ticker, "enabled": False}

        raw = _safe(lambda: feed.fetch_latest_sync(limit=limit), [])
        if not raw:
            return {"ticker": ticker, "enabled": True, "messages": [],
                    "error": "Telegram недоступен (нет сессии/телефона/сети)"}

        from .telegram_market_feed import parse_message, compact_digest
        relevant = []
        texts = []
        for m in raw:
            fm = parse_message(m)
            if ticker in fm.tickers:
                relevant.append({
                    "channel": fm.channel,
                    "published": fm.published.isoformat(),
                    "text": " ".join(fm.text.split())[:180],
                })
                texts.append(fm.text)

        out: dict[str, Any] = {
            "ticker": ticker,
            "enabled": True,
            "messages": relevant[:10],
            "total_channels_scanned": len(_safe(lambda: list(feed.channels), [])),
        }
        if texts:
            agg = _safe(lambda: aggregate_sentiment(texts), None)
            if agg:
                out["sentiment"] = agg
        return out

    # ── вердикт (investment_analyzer + новости) ───────────────────────────
    def verdict(
        self,
        ticker: str,
        price_change_pct: float = 0.0,
        votes: list[CommitteeVote] | None = None,
        news_items: list[NewsItem] | None = None,
        fetch_news: bool = True,
    ) -> dict[str, Any]:
        """Честный вердикт: дивидендные ловушки + priced-in + комитет.

        Args:
            news_items: готовые новости (чтобы не тянуть Telegram повторно).
            fetch_news: тянуть ли новости самим (False — используем news_items).
        """
        ticker = ticker.upper()
        if news_items is None and fetch_news:
            feed = self._get_feed()
            if feed is not None:
                raw = _safe(lambda: feed.fetch_latest_sync(limit=5), [])
                if raw:
                    from .telegram_market_feed import feed_to_news_items
                    news_items = _safe(lambda: feed_to_news_items(raw), []) or []

        analyzer = InvestmentAnalyzer(news=news_items or [])
        v = _safe(
            lambda: analyzer.analyze(ticker, price_change_pct=price_change_pct, votes=votes),
            None,
        )
        if v is None:
            return {"ticker": ticker, "error": "вердикт не построен"}

        return {
            "ticker": v.ticker,
            "action": v.action,
            "confidence": round(v.confidence, 3),
            "actionable": v.is_actionable(),
            "traps": list(v.traps),
            "catalysts": list(v.catalysts),
            "risks": list(v.risks),
            "priced_in": {
                "is_priced_in": v.priced_in.is_priced_in if v.priced_in else None,
                "signal": v.priced_in.signal if v.priced_in else None,
                "reason": v.priced_in.reason if v.priced_in else None,
            } if v.priced_in else None,
        }

    # ── ГЛАВНЫЙ МЕТОД: полная картина одним вызовом ───────────────────────
    def full_picture(
        self,
        ticker: str,
        days: int = 90,
        include_news: bool = True,
        include_orderbook: bool = True,
    ) -> dict[str, Any]:
        """ВСЁ по одной бумаге одним вызовом.

        Собирает: дату, котировку, портфельную позицию, технику, роботов,
        фундаментал, новости/сентимент, вердикт, стакан. Каждая секция
        самостоятельна и не роняет остальные.
        """
        ticker = ticker.upper()
        picture: dict[str, Any] = {
            "ticker": ticker,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "date": full_date_context(),
        }

        picture["quote"] = self.quote(ticker)

        # портфельная позиция (если есть)
        positions = _safe(lambda: self.tinkoff.get_portfolio(), None)
        if positions:
            for p in positions:
                if getattr(p, "ticker", "") == ticker:
                    picture["position"] = {
                        "quantity": float(getattr(p, "quantity", 0)),
                        "avg_price": round(float(getattr(p, "avg_price", 0)), 2),
                        "current_price": round(float(getattr(p, "current_price", 0)), 2),
                        "profit_pct": getattr(p, "profit_pct", 0.0),
                    }
                    break

        tech = self.technical(ticker, days=days)
        if "error" not in tech:
            picture["technical"] = tech

        rob = self.robot(ticker, days=days)
        if "error" not in rob:
            picture["robot"] = rob

        fund = self.fundamentals(ticker)
        if fund.get("yield") or fund.get("next"):
            picture["fundamentals"] = fund

        if include_news:
            news = self.news(ticker)
            if news.get("messages"):
                picture["news"] = news

        # Вердикт: если новости уже тянули — передаём их, чтобы не фетчить
        # Telegram второй раз (и не фетчить вовсе при include_news=False).
        news_items: list[NewsItem] | None = None
        fetched_news = picture.get("news")
        if fetched_news and fetched_news.get("messages"):
            from .telegram_market_feed import RawMessage, feed_to_news_items
            raw = [
                RawMessage(channel=m["channel"], text=m["text"],
                           published=datetime.fromisoformat(m["published"]))
                for m in fetched_news["messages"]
            ]
            news_items = _safe(lambda: feed_to_news_items(raw), []) or []

        picture["verdict"] = self.verdict(
            ticker,
            news_items=news_items,
            fetch_news=include_news and news_items is None,
        )

        if include_orderbook:
            book = self.orderbook(ticker)
            if "error" not in book:
                picture["orderbook"] = book

        return picture

    # ── вотчлист ───────────────────────────────────────────────────────────
    def watchlist(self, tickers: list[str], days: int = 90) -> list[dict[str, Any]]:
        """Сравнить несколько бумаг: котировка + технический сигнал + вердикт."""
        rows = []
        for t in tickers:
            tech = self.technical(t, days=days)
            v = self.verdict(t)
            rows.append({
                "ticker": t.upper(),
                "quote": self.quote(t),
                "signal": {
                    "verdict": tech.get("verdict") if "error" not in tech else None,
                    "confidence": tech.get("confidence") if "error" not in tech else None,
                },
                "verdict": {"action": v.get("action"), "confidence": v.get("confidence")},
            })
        return rows

    # ── рендер полной картины (для человека) ──────────────────────────────
    @staticmethod
    def render(pic: dict[str, Any]) -> str:
        """Компактный текстовый рендер full_picture() — token-diet стиль."""
        t = pic.get("ticker", "?")
        lines = [f"=== {t} — полная картина ==="]
        d = pic.get("date", {})
        if d:
            lines.append(f"Дата: {d.get('today_ru')} ({d.get('weekday')}), "
                         f"торг. день: {'да' if d.get('is_trading_day') else 'нет'}, "
                         f"через 3 торг. дня: {d.get('in_3_trading_days')}")

        q = pic.get("quote", {})
        price = q.get("price")
        if price is not None:
            change = q.get("change_pct")
            ch = f" ({change:+.2f}% за день)" if change is not None else ""
            src = "Tinkoff" if q.get("tinkoff") else ("MOEX" if q.get("moex") else "—")
            lines.append(f"Цена: {price:.2f}₽{ch} [источник: {src}]")

        pos = pic.get("position")
        if pos:
            lines.append(f"Позиция: {pos['quantity']:.0f} шт, средняя {pos['avg_price']:.2f}₽, "
                         f"P/L {pos['profit_pct']:+.2f}%")

        tech = pic.get("technical")
        if tech:
            conf = tech.get("confidence")
            conf_str = f" (conf {conf:.0%})" if conf is not None else ""
            lines.append(f"Техника: {tech.get('verdict')}{conf_str}")
            for s in tech.get("signals", []):
                arrow = "🟢" if s["direction"] > 0 else ("🔴" if s["direction"] < 0 else "⚪")
                lines.append(f"  {arrow} {s['name']}: {s['description']}")
            rng = tech.get("expected_range")
            if rng:
                lines.append(f"Ожидаемый диапазон: {rng['low']} — {rng['high']}₽")

        rob = pic.get("robot")
        if rob:
            s = rob.get("strategies", {})
            lean = s.get("combined_lean")
            lines.append(f"Роботы: наклон {lean}")
            if s.get("ma_cross"):
                mc = s["ma_cross"]
                lines.append(f"  MA cross: {mc.get('signal')} — {mc.get('reason')}")
            if s.get("volume_profile"):
                vp = s["volume_profile"]
                lines.append(f"  POC: {vp.get('poc'):.0f}₽ ({vp.get('insight')})")
            if rob.get("backtest"):
                bt = rob["backtest"]
                lines.append(f"  Backtest: {bt.get('verdict')} P/L {bt.get('total_pnl_pct'):+.2f}%, "
                             f"win rate {bt.get('win_rate'):.0%}")

        fund = pic.get("fundamentals")
        if fund:
            y = fund.get("yield")
            if y and y.get("yield_pct") is not None:
                lines.append(f"Див. доходность: {y['yield_pct']:.2f}% (за 12 мес: {y.get('sum_last_12m')}₽)")

        news = pic.get("news")
        if news and news.get("messages"):
            sent = news.get("sentiment")
            if sent:
                lines.append(f"Новости: {len(news['messages'])} сообщений, "
                             f"сентимент {sent.get('signal')} (score {sent.get('score'):+.2f})")
            for m in news["messages"][:3]:
                lines.append(f"  [{m['channel']}] {m['text'][:90]}")

        v = pic.get("verdict", {})
        if v and "error" not in v:
            lines.append(f"Вердикт: {v.get('action').upper()} (уверенность {v.get('confidence'):.0%})")
            for tr in v.get("traps", []):
                lines.append(f"  🚫 {tr}")
            if v.get("priced_in") and v["priced_in"].get("reason"):
                lines.append(f"  ℹ️ {v['priced_in']['reason']}")

        book = pic.get("orderbook")
        if book:
            lines.append(f"Стакан: давление ask/bid {book.get('pressure_ask_over_bid')}x, "
                         f"интерпретация {book.get('interpretation')}")
            w = book.get("biggest_wall") or {}
            if w.get("ask"):
                lines.append(f"  стена продажи: {w['ask']['price']:.2f}₽ × {w['ask']['quantity']} шт")
            if w.get("bid"):
                lines.append(f"  стена покупки: {w['bid']['price']:.2f}₽ × {w['bid']['quantity']} шт")

        return "\n".join(lines)


__all__ = ["InvestHub"]
