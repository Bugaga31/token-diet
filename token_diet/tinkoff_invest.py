"""Tinkoff Invest API — live prices, candles, portfolio, signals.

Подключает token-diet к бирже MOEX через Т-Инвестиции.
Работает через ОТКРЫТЫЙ REST API — не требует официального SDK:
только HTTP-запросы + токен. SDK используется если установлен.

Безопасность (жёсткие правила):
- Токен НИКОГДА не хранится в коде
- Загружается из env (TINKOFF_TOKEN) или локального файла ~/.tinkoff/token
- Файл токена НЕ попадает в git (см. .gitignore)
- Никогда не печатает токен в логи

REST endpoints (Tinkoff public API):
  POST https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.contract.v1.MarketDataService/GetLastPrices
  POST .../GetCandles
  POST .../InstrumentsService/FindInstrument
  POST .../UsersService/GetAccounts
  POST .../OperationsService/GetPortfolio
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# REST API endpoints
# ═══════════════════════════════════════════════════════════════════════════════

API_BASE = "https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.contract.v1"

ENDPOINTS = {
    "last_prices": f"{API_BASE}.MarketDataService/GetLastPrices",
    "candles": f"{API_BASE}.MarketDataService/GetCandles",
    "orderbook": f"{API_BASE}.MarketDataService/GetOrderBook",
    "accounts": f"{API_BASE}.UsersService/GetAccounts",
    "portfolio": f"{API_BASE}.OperationsService/GetPortfolio",
    "find_instrument": f"{API_BASE}.InstrumentsService/FindInstrument",
    "get_instrument": f"{API_BASE}.InstrumentsService/GetInstrumentBy",
    "bond_by": f"{API_BASE}.InstrumentsService/GetBondBy",
    "post_order": f"{API_BASE}.OrdersService/PostOrder",
    "get_orders": f"{API_BASE}.OrdersService/GetOrders",
    "post_stop_order": f"{API_BASE}.StopOrdersService/PostStopOrder",
    "get_stop_orders": f"{API_BASE}.StopOrdersService/GetStopOrders",
    "cancel_stop_order": f"{API_BASE}.StopOrdersService/CancelStopOrder",
}


def _rpc(name: str, body: dict, token: str, timeout: int = 15) -> dict | None:
    """Вызвать REST endpoint (JSON-RPC стиль). Возвращает dict или None."""
    url = ENDPOINTS.get(name)
    if not url:
        return None
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    # SSL: на некоторых машинах (DPI-обход, корпоративные прокси) стоит
    # перехват с самоподписанными сертификатами. Сначала пробуем проверку,
    # потом fallback на не-проверенный SSL (только для чтения котировок).
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            # SSL error → retry without verification
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    return json.loads(resp.read().decode())
            raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


def _quotation_to_float(q: dict | None) -> float:
    """Quotation {units, nano} → float."""
    if not q:
        return 0.0
    units = int(q.get("units", 0))
    nano = int(q.get("nano", 0))
    return units + nano / 1e9


# ═══════════════════════════════════════════════════════════════════════════════
# Токен: env или локальный файл (НЕ в git)
# ═══════════════════════════════════════════════════════════════════════════════

TOKEN_ENV = "TINKOFF_TOKEN"
TOKEN_FILE = Path("~/.tinkoff/token").expanduser()


def get_token(token: str | None = None) -> str | None:
    """Получить токен: параметр > env > локальный файл.

    Никогда не печатает токен. Возвращает None, если ничего нет.
    """
    if token and token.strip():
        return token.strip()
    env = os.environ.get(TOKEN_ENV)
    if env and env.strip():
        return env.strip()
    try:
        if TOKEN_FILE.exists():
            content = TOKEN_FILE.read_text().strip()
            if content:
                return content
    except OSError:
        pass
    return None


def save_token(token: str, path: str | None = None) -> Path:
    """Сохранить токен в локальный файл с правами 600 (только владелец).

    Файл лежит вне git-репозитория (~/.tinkoff/token) — в релизы не попадёт.
    """
    target = Path(path) if path else TOKEN_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(token.strip())
    os.chmod(target, 0o600)  # rw-------
    return target


# ═══════════════════════════════════════════════════════════════════════════════
# Тикеры → FIGI (Tinkoff использует свои TCS-коды, НЕ Bloomberg!)
# Большинство решается через API find_instrument; ключевые кэшированы.
# ═══════════════════════════════════════════════════════════════════════════════

# Кэш ПОДТВЕРЖДЁННЫХ через API FIGI (проверено: свечи возвращаются)
# ВАЖНО: для свечей Tinkoff надёжно отдаёт Bloomberg FIGI (BBG...),
# но у части инструментов рабочими оказываются TCS-коды (YDEX, RUAL...).
# Каждая запись ниже реально проверена через GetCandles 10.08.2026.
KNOWN_FIGI: dict[str, str] = {
    "SBER": "BBG004730N88",    # Сбер (29 свечей ✓)
    "GAZP": "BBG004730RP0",    # Газпром (29 ✓)
    "LKOH": "BBG004731032",    # Лукойл (29 ✓)
    "PLZL": "BBG000R607Y3",    # Полюс (29 ✓)
    "ALRS": "BBG004S68B31",    # Алроса (29 ✓)
    "MGNT": "BBG004RVFCY3",    # Магнит (29 ✓)
    "YDEX": "TCS00A107T19",    # Яндекс (29 ✓ — только TCS!)
    "RUAL": "TCSM739025V3",    # Русал (5 ✓)
    "NVTK": "TCS50A0DKVS5",    # Новатэк (15 ✓)
    "MTSS": "TCSM42375219",    # МТС (5 ✓)
    "TATN": "TCSM41233591",    # Татнефть (12 ✓)
    # Не проверены на свечах — решаются через API при первом запросе:
    "GMKN": None,  # Норникель
    "ROSN": None,  # Роснефть
    "VTBR": None,  # ВТБ
    "MOEX": None,  # Мосбиржа
    "SNGS": None,  # Сургутнефтегаз
    "TCSG": None,  # Т-Банк
}
KNOWN_FIGI = {k: v for k, v in KNOWN_FIGI.items() if v}

# Кэш тикер → uid (из find_instrument, быстрее для last_prices)
KNOWN_UID: dict[str, str] = {}


def resolve_figi(ticker: str) -> str | None:
    """Тикер → FIGI из кэша (или None — тогда через API)."""
    return KNOWN_FIGI.get(ticker.upper())


def figi_unknown(figi: str) -> str:
    """FIGI → тикер, или 'UNKNOWN'."""
    for t, f in KNOWN_FIGI.items():
        if f == figi:
            return t
    return "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TinkoffQuote:
    """Реальная котировка с биржи."""
    ticker: str
    figi: str
    price: float
    time: datetime


@dataclass
class TinkoffCandle:
    """Одна свеча."""
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class PortfolioPosition:
    """Позиция в портфеле."""
    ticker: str
    figi: str
    quantity: float
    current_price: float
    avg_price: float
    profit_pct: float


class TinkoffInvest:
    """Интеграция с Т-Инвестициями через REST API (без SDK).

    Usage:
        tink = TinkoffInvest()   # токен из env/файла
        quote = tink.get_quote("PLZL")           # цена в реальном времени
        candles = tink.get_candles("PLZL")       # 30 дневных свечей
        signal = tink.get_signal("PLZL")         # сигнал от market_intelligence
        portfolio = tink.get_portfolio()         # позиции
    """

    def __init__(self, token: str | None = None):
        self.token = get_token(token)
        self.available = bool(self.token)
        self._sdk = False
        # Пробуем SDK, если установлен
        try:
            from tinkoff.invest import Client, CandleInterval  # type: ignore
            self._sdk = True
            self._Client = Client
            self._CandleInterval = CandleInterval
        except ImportError:
            self._sdk = False

    # ── REST helpers ──────────────────────────────────────────────────────
    def _rpc(self, name: str, body: dict) -> dict | None:
        if not self.available:
            return None
        return _rpc(name, body, self.token)

    # ── поиск инструмента ────────────────────────────────────────────────
    def find_figi(self, ticker: str) -> str | None:
        """Найти FIGI по тикеру: кэш, потом API.

        Приоритет: BBG-коды (надёжны для свечей), иначе первый подходящий.
        Кэширует результат, чтобы не дёргать API каждый раз.
        """
        ticker = ticker.upper()
        cached = KNOWN_FIGI.get(ticker)
        if cached:
            return cached
        resp = self._rpc("find_instrument", {
            "query": ticker, "instrumentKind": "INSTRUMENT_TYPE_SHARE",
        })
        if resp and "instruments" in resp:
            matches = [i for i in resp["instruments"]
                       if i.get("ticker", "").upper() == ticker]
            if not matches:
                # некоторые инструменты (Т-Банк) ищутся по имени
                matches = [i for i in resp["instruments"]
                           if i.get("name", "").upper() == ticker]
            # 1) BBG-код, 2) любой
            figi = None
            for item in matches:
                f = item.get("figi")
                if f and f.startswith("BBG"):
                    figi = f
                    break
            if figi is None and matches:
                figi = matches[0].get("figi")
            if figi:
                KNOWN_FIGI[ticker] = figi
                for item in matches:
                    if item.get("uid"):
                        KNOWN_UID[ticker] = item["uid"]
                        break
                return figi
        return None

    # ── лот и безопасные ордера (защита от ошибки «лот ≠ акция») ─────────
    _lot_cache: dict[str, int] = {}

    def lot_size(self, ticker: str, figi: str | None = None) -> int:
        """Размер лота инструмента (сколько акций в 1 лоте).

        УРОК (13.08): лот ≠ акция. Норникель торгуется лотами по 10 акций —
        quantity=15 в ордере = 150 акций, а не 15. Этот метод + buy_shares/
        sell_shares исключают такую ошибку.
        """
        ticker = ticker.upper()
        cached = self._lot_cache.get(ticker)
        if cached:
            return cached
        figi = figi or self.find_figi(ticker)
        if not figi:
            return 1
        resp = self._rpc("get_instrument", {
            "idType": "INSTRUMENT_ID_TYPE_FIGI", "id": figi,
        })
        lot = (resp or {}).get("instrument", {}).get("lot")
        lot = int(lot) if lot else 1
        self._lot_cache[ticker] = lot
        return lot

    def buy_shares(self, ticker: str, shares: int) -> dict | None:
        """Купить КОНКРЕТНОЕ число АКЦИЙ (сам переведёт в лоты).

        Безопасный аналог post_order: нельзя перепутать акции и лоты.
        Округляет вниз до целого лота и проверяет, что лотов > 0.
        """
        lot = self.lot_size(ticker)
        lots = shares // lot
        if lots <= 0:
            return {"error": f"{shares} акций < 1 лота (лот = {lot})"}
        return self.post_order(ticker, quantity=lots, direction="buy")

    def sell_shares(self, ticker: str, shares: int) -> dict | None:
        """Продать КОНКРЕТНОЕ число АКЦИЙ (сам переведёт в лоты)."""
        lot = self.lot_size(ticker)
        lots = shares // lot
        if lots <= 0:
            return {"error": f"{shares} акций < 1 лота (лот = {lot})"}
        return self.post_order(ticker, quantity=lots, direction="sell")

    # ── котировка ────────────────────────────────────────────────────────
    def get_quote(self, ticker: str, figi: str | None = None) -> TinkoffQuote | None:
        """Текущая цена в реальном времени."""
        if not self.available:
            return None
        figi = figi or self.find_figi(ticker)
        if not figi:
            return None
        try:
            # Если SDK есть — используем его
            if self._sdk:
                from tinkoff.invest import Client as C
                with C(self.token) as client:
                    resp = client.market_data.get_last_prices(figi=[figi])
                    for price in resp.last_prices:
                        q = price.price
                        return TinkoffQuote(
                            ticker=ticker, figi=figi,
                            price=q.units + q.nano / 1e9,
                            time=price.time,
                        )
        except Exception:
            pass
        # REST fallback
        resp = self._rpc("last_prices", {"figi": [figi]})
        if resp and "lastPrices" in resp:
            for p in resp["lastPrices"]:
                return TinkoffQuote(
                    ticker=ticker, figi=figi,
                    price=_quotation_to_float(p.get("price")),
                    time=datetime.now(),
                )
        return None

    # ── свечи ────────────────────────────────────────────────────────────
    def get_candles(
        self,
        ticker: str,
        days: int = 30,
        interval: Any = None,
        figi: str | None = None,
        skip_weekends: bool = True,
    ) -> list[TinkoffCandle]:
        """Исторические свечи (по умолчанию 30 дней, дневной интервал).

        Args:
            skip_weekends: фильтровать внебиржевые свечи выходных дней
                (OsEngine-style: OTC-свечи искажают расчёт индикаторов)
        """
        if not self.available:
            return []
        figi = figi or self.find_figi(ticker)
        if not figi:
            return []
        candles: list[TinkoffCandle] = []
        now = datetime.utcnow()

        # SDK path
        if self._sdk:
            try:
                from tinkoff.invest import Client as C
                from tinkoff.invest import CandleInterval as CI
                interval = interval or CI.CANDLE_INTERVAL_DAY
                with C(self.token) as client:
                    resp = client.market_data.get_candles(
                        figi=figi,
                        from_=now - timedelta(days=days),
                        to=now,
                        interval=interval,
                    )
                    for c in resp.candles:
                        candles.append(TinkoffCandle(
                            time=c.time,
                            open=c.open.units + c.open.nano / 1e9,
                            high=c.high.units + c.high.nano / 1e9,
                            low=c.low.units + c.low.nano / 1e9,
                            close=c.close.units + c.close.nano / 1e9,
                            volume=float(c.volume),
                        ))
                    if skip_weekends:
                        from .date_anchor import filter_trading_days
                        candles = filter_trading_days(candles)
                    return candles
            except Exception:
                pass

        # REST fallback: interval mapping
        interval_map = {
            "day": "CANDLE_INTERVAL_DAY", "1min": "CANDLE_INTERVAL_1_MIN",
            "hour": "CANDLE_INTERVAL_HOUR", "week": "CANDLE_INTERVAL_WEEK",
            "month": "CANDLE_INTERVAL_MONTH",
        }
        interval_name = "CANDLE_INTERVAL_DAY"
        if isinstance(interval, str):
            interval_name = interval_map.get(interval.lower(), interval_name)
        # Tinkoff требует ISO8601 с суффиксом 'Z' (UTC)
        def _iso_z(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        resp = self._rpc("candles", {
            "figi": figi,
            "from": _iso_z(now - timedelta(days=days)),
            "to": _iso_z(now),
            "interval": interval_name,
        })
        if resp and "candles" in resp:
            for c in resp["candles"]:
                try:
                    t = datetime.fromisoformat(c["time"].replace("Z", "+00:00"))
                except (ValueError, KeyError):
                    t = now
                candles.append(TinkoffCandle(
                    time=t,
                    open=_quotation_to_float(c.get("open")),
                    high=_quotation_to_float(c.get("high")),
                    low=_quotation_to_float(c.get("low")),
                    close=_quotation_to_float(c.get("close")),
                    volume=float(c.get("volume", 0)),
                ))
        if skip_weekends:
            from .date_anchor import filter_trading_days
            candles = filter_trading_days(candles)
        return candles
    def get_signal(
        self,
        ticker: str,
        days: int = 90,
        sentiment_texts: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Полный сигнал от market_intelligence на РЕАЛЬНЫХ свечах.

        Возвращает dict с вердиктом, уверенностью, целями, lean. None если нет API.

        ВАЖНО: days < 45 даёт <31 торговой свечи — движку не хватает данных
        (MACD slow=26 + буфер), и он молча возвращал HOLD 0.5. Дефолт = 90 дней.
        """
        candles = self.get_candles(ticker, days=days)
        if len(candles) < 20:
            return None

        from .market_intelligence import generate_signal, estimate_price_range

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        signal = generate_signal(
            ticker, closes, highs, lows, volumes, sentiment_texts,
        )
        price_range = estimate_price_range(ticker, closes)

        return {
            "ticker": ticker,
            "price": closes[-1],
            "verdict": signal.final_verdict,
            "confidence": round(signal.final_confidence, 3),
            "lean": signal.lean,
            "lean_direction": signal.lean_direction,
            "technical": signal.technical_verdict,
            "sentiment": signal.sentiment_signal,
            "signals": [{"name": s.name, "direction": s.direction,
                         "description": s.description}
                        for s in signal.signals_detail],
            "expected_range": {
                "low": price_range.get("expected_low"),
                "high": price_range.get("expected_high"),
            },
            "target_3pct": round(closes[-1] * 1.03, 2),
            "stop_5pct": round(closes[-1] * 0.95, 2),
        }

    def screen(self, tickers: list[str], days: int = 90) -> list[dict]:
        """Ранжировать рынок по lean: кто ближе к BUY, кто к SELL.

        В отличие от get_signal (одна бумага), screen прогоняет все тикеры
        и сортирует по непрерывному наклону. Даже если всё HOLD — видно,
        куда смотреть в первую очередь.
        """
        from .market_intelligence import generate_signal
        from .momentum import momentum_lean, rate_of_change

        rows: list[dict] = []
        for t in tickers:
            candles = self.get_candles(t, days=days)
            if len(candles) < 20:
                continue
            closes = [c.close for c in candles]
            highs = [c.high for c in candles]
            lows = [c.low for c in candles]
            volumes = [c.volume for c in candles]
            s = generate_signal(t, closes, highs, lows, volumes)
            roc = rate_of_change(closes, 10)
            ml = momentum_lean(closes, highs, volumes)
            rows.append({
                "ticker": t,
                "price": round(closes[-1], 2),
                "verdict": s.final_verdict,
                "confidence": round(s.final_confidence, 3),
                "lean": s.lean,
                "lean_direction": s.lean_direction,
                "momentum_lean": ml,
                "roc_10d": roc,
            })
        rows.sort(key=lambda r: -r["momentum_lean"])
        return rows

    def full_signal(self, ticker: str, days: int = 90) -> dict | None:
        """Полный сигнал: техника + momentum + сантимент (лента + Пульс).

        В отличие от get_signal (только техника), здесь сантимент толпы
        замешивается в общий вердикт (generate_signal: 70% техника + 30%
        сантимент). Возвращает dict с итоговым lean, verdict, confidence.
        """
        from .market_intelligence import generate_signal
        from .market_sentiment import gather_sentiment_texts

        candles = self.get_candles(ticker, days=days)
        if len(candles) < 31:
            return None
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        texts = gather_sentiment_texts(ticker)
        s = generate_signal(ticker, closes, highs, lows, volumes, texts)
        return {
            "ticker": ticker,
            "price": round(closes[-1], 2),
            "verdict": s.final_verdict,
            "confidence": round(s.final_confidence, 3),
            "lean": s.lean,
            "lean_direction": s.lean_direction,
            "technical": s.technical_verdict,
            "sentiment": s.sentiment_signal,
            "sentiment_score": round(s.sentiment_score, 3),
            "sentiment_texts": len(texts),
        }

    # ── стакан ───────────────────────────────────────────────────────────
    def get_orderbook(self, ticker: str, depth: int = 10) -> dict | None:
        """Стакан (order book) по тикеру: asks/bids списками dict'ов.

        Каждый уровень: {"price": float, "quantity": int}. Returns None
        если токена нет или FIGI/данные не получены. Никогда не бросает.
        """
        if not self.available:
            return None
        figi = self.find_figi(ticker)
        if not figi:
            return None
        resp = self._rpc("orderbook", {"figi": figi, "depth": depth})
        if not resp:
            return None
        result: dict = {
            "ticker": ticker.upper(),
            "figi": figi,
            "asks": [],
            "bids": [],
        }
        for side in ("asks", "bids"):
            for item in resp.get(side, []) or []:
                if not isinstance(item, dict):
                    continue
                price = _quotation_to_float(item.get("price"))
                if price <= 0:
                    continue
                try:
                    qty = int(float(item.get("quantity", 0)))
                except (TypeError, ValueError):
                    qty = 0
                result[side].append({"price": price, "quantity": qty})
        return result

    # ── ордера ───────────────────────────────────────────────────────────
    def post_order(
        self,
        ticker: str,
        quantity: int = 1,
        direction: str = "buy",
        order_type: str = "market",
        price: float | None = None,
        figi: str | None = None,
    ) -> dict | None:
        """Выставить ордер (market или limit) через OrdersService/PostOrder.

        Args:
            ticker: тикер (PLZL, SBER...)
            quantity: кол-во лотов
            direction: 'buy' или 'sell'
            order_type: 'market' (исполнится по рынку) или 'limit' (нужен price)
            price: цена для limit-ордера (для market — игнорируется)

        Returns:
            dict {order_id, status, executed_price, lots} или None при ошибке.
            НИКОГДА не бросает исключение.
        """
        if not self.available:
            return None
        figi = figi or self.find_figi(ticker)
        if not figi:
            return None
        accounts = self._rpc("accounts", {})
        if not accounts or not accounts.get("accounts"):
            return None
        account_id = accounts["accounts"][0]["id"]

        direction_enum = (
            "ORDER_DIRECTION_BUY" if direction.lower() in ("buy", "b")
            else "ORDER_DIRECTION_SELL"
        )
        if order_type.lower() in ("market", "m"):
            order_type_enum = "ORDER_TYPE_MARKET"
            price_q: dict = {"units": 0, "nano": 0}
        else:
            order_type_enum = "ORDER_TYPE_LIMIT"
            if not price or price <= 0:
                return None
            units = int(price)
            nano = int(round((price - units) * 1e9))
            price_q = {"units": units, "nano": nano}

        resp = self._rpc("post_order", {
            "figi": figi,
            "quantity": int(quantity),
            "price": price_q,
            "direction": direction_enum,
            "accountId": account_id,
            "orderType": order_type_enum,
            "instrumentId": figi,
        })
        if not resp:
            return None
        return {
            "order_id": resp.get("orderId"),
            "status": resp.get("executionReportStatus"),
            "executed_price": _quotation_to_float(resp.get("executedOrderPrice")),
            "lots": resp.get("lotsExecuted"),
        }

    def get_orders(self) -> list[dict] | None:
        """Активные заявки (неисполненные) по первому счёту."""
        if not self.available:
            return None
        accounts = self._rpc("accounts", {})
        if not accounts or not accounts.get("accounts"):
            return None
        account_id = accounts["accounts"][0]["id"]
        resp = self._rpc("get_orders", {"accountId": account_id})
        if not resp or "orders" not in resp:
            return []
        return resp["orders"]

    # ── стоп-заявки (защита капитала на бирже) ──────────────────────────
    def _account_id(self) -> str | None:
        """Первый счёт аккаунта, или None."""
        accounts = self._rpc("accounts", {})
        if not accounts or not accounts.get("accounts"):
            return None
        return accounts["accounts"][0]["id"]

    def post_stop_order(
        self,
        ticker: str,
        quantity: int = 1,
        stop_price: float | None = None,
        direction: str = "sell",
        limit_price: float | None = None,
        expire_days: int = 14,
        figi: str | None = None,
    ) -> dict | None:
        """Выставить СТОП-заявку на бирже (защита капитала).

        По умолчанию — рыночный стоп-лосс (STOP_ORDER_TYPE_STOP_LOSS):
        при достижении stop_price биржа исполнит по рынку. Если передан
        limit_price — стоп-лимит (STOP_ORDER_TYPE_STOP_LIMIT).

        Returns:
            dict {stop_order_id, status} или None при ошибке.
        """
        if not self.available:
            return None
        figi = figi or self.find_figi(ticker)
        if not figi or not stop_price or stop_price <= 0:
            return None
        account_id = self._account_id()
        if not account_id:
            return None

        def _q(p: float) -> dict:
            units = int(p)
            nano = int(round((p - units) * 1e9))
            return {"units": units, "nano": nano}

        from datetime import timedelta
        expire = (datetime.utcnow() + timedelta(days=expire_days))
        expire_iso = expire.strftime("%Y-%m-%dT%H:%M:%SZ")

        # ВАЖНО: стоп-заявки используют СВОЙ enum направления (StopOrderDirection),
        # а не OrderDirection. Баг: раньше слали ORDER_DIRECTION_* → API отвечал
        # «Missing parameter: direction» / code 30019.
        direction_enum = (
            "STOP_ORDER_DIRECTION_BUY" if direction.lower() in ("buy", "b")
            else "STOP_ORDER_DIRECTION_SELL"
        )
        stop_type = (
            "STOP_ORDER_TYPE_STOP_LIMIT" if limit_price and limit_price > 0
            else "STOP_ORDER_TYPE_STOP_LOSS"
        )
        body = {
            "figi": figi,
            "quantity": int(quantity),
            "price": _q(limit_price or 0.0),
            "stop_price": _q(stop_price),
            "direction": direction_enum,
            "accountId": account_id,
            "expirationType": "STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL",
            "stopOrderType": stop_type,
            "expireDate": expire_iso,
        }
        resp = self._rpc("post_stop_order", body)
        if not resp:
            return None
        return {"stop_order_id": resp.get("stopOrderId"),
                "status": resp.get("status")}

    def get_stop_orders(self) -> list[dict] | None:
        """Активные стоп-заявки по первому счёту."""
        if not self.available:
            return None
        account_id = self._account_id()
        if not account_id:
            return None
        resp = self._rpc("get_stop_orders", {"accountId": account_id})
        if not resp or "stopOrders" not in resp:
            return []
        return resp["stopOrders"]

    def cancel_stop_order(self, stop_order_id: str) -> dict | None:
        """Отменить стоп-заявку."""
        if not self.available:
            return None
        account_id = self._account_id()
        if not account_id:
            return None
        resp = self._rpc("cancel_stop_order", {
            "accountId": account_id, "stopOrderId": stop_order_id,
        })
        return resp or None

    # ── портфель ─────────────────────────────────────────────────────────
    def get_portfolio(self) -> list[PortfolioPosition] | None:
        """Текущие позиции портфеля."""
        if not self.available:
            return None
        # SDK path
        if self._sdk:
            try:
                from tinkoff.invest import Client as C
                with C(self.token) as client:
                    accounts = client.users.get_accounts()
                    if not accounts.accounts:
                        return []
                    account_id = accounts.accounts[0].id
                    portfolio = client.operations.get_portfolio(account_id=account_id)
                    positions = []
                    for p in portfolio.positions:
                        if not p.lots:
                            continue
                        figi = p.figi
                        ticker = figi_unknown(figi)
                        cur = p.current_price.units + p.current_price.nano / 1e9
                        avg = (p.average_position_price.units +
                               p.average_position_price.nano / 1e9) if p.average_position_price else 0
                        qty = p.quantity.units + p.quantity.nano / 1e9
                        profit = ((cur - avg) / avg * 100) if avg else 0.0
                        positions.append(PortfolioPosition(
                            ticker=ticker, figi=figi, quantity=qty,
                            current_price=cur, avg_price=avg,
                            profit_pct=round(profit, 2),
                        ))
                    return positions
            except Exception:
                pass

        # REST path
        accounts = self._rpc("accounts", {})
        if not accounts or not accounts.get("accounts"):
            return []
        account_id = accounts["accounts"][0]["id"]
        resp = self._rpc("portfolio", {"accountId": account_id})
        if not resp or "positions" not in resp:
            return None
        positions = []
        for p in resp["positions"]:
            figi = p.get("figi", "")
            ticker = figi_unknown(figi)
            if ticker == "UNKNOWN":
                # Пробуем найти тикер по uid через API
                uid = p.get("instrumentUid", "")
                ticker = f"uid:{uid[:8]}" if uid else "UNKNOWN"
            cur = _quotation_to_float(p.get("currentPrice"))
            avg = _quotation_to_float(p.get("averagePositionPrice"))
            qty = _quotation_to_float(p.get("quantity"))
            profit = ((cur - avg) / avg * 100) if avg else 0.0
            positions.append(PortfolioPosition(
                ticker=ticker, figi=figi, quantity=qty,
                current_price=cur, avg_price=avg,
                profit_pct=round(profit, 2),
            ))
        return positions

    # ── сигналы по всему портфелю ───────────────────────────────────────
    def scan_portfolio(self) -> list[dict[str, Any]]:
        """Сигналы по всем бумагам в портфеле."""
        positions = self.get_portfolio() or []
        results = []
        for pos in positions:
            if pos.ticker == "UNKNOWN" or pos.ticker.startswith("uid:"):
                continue
            try:
                sig = self.get_signal(pos.ticker)
                if sig:
                    sig["quantity"] = pos.quantity
                    sig["avg_price"] = pos.avg_price
                    sig["profit_pct"] = pos.profit_pct
                    results.append(sig)
            except Exception:
                continue
        return results


def status() -> dict[str, Any]:
    """Статус интеграции (без токена в выводе)."""
    token = get_token()
    return {
        "api_available": bool(token),
        "token_found": bool(token),
        "token_source": ("env" if os.environ.get(TOKEN_ENV) else
                         ("file" if TOKEN_FILE.exists() else "none")),
        "sdk_installed": _sdk_installed(),
        "known_tickers": len(KNOWN_FIGI),
    }


def _sdk_installed() -> bool:
    try:
        import tinkoff.invest  # noqa: F401
        return True
    except ImportError:
        return False
