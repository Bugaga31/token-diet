"""Tinkoff Invest API — live prices, candles, portfolio, signals.

Позволяет token-diet получать РЕАЛЬНЫЕ данные с биржи MOEX через API
Т-Инвестиций: текущие цены, исторические свечи, портфель — и гонять их
через market_intelligence для живых сигналов.

Безопасность (жёсткие правила):
- Токен НИКОГДА не хранится в коде
- Загружается из env (TINKOFF_TOKEN) или локального файла ~/.tinkoff/token
- Файл токена НЕ попадает в git (см. .gitignore)
- Никогда не печатает токен в логи

Использует официальный SDK: pip install tinkoff-invest-python
Без SDK модуль возвращает None — не падает.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from tinkoff.invest import Client, CandleInterval
    HAS_TINKOFF = True
except ImportError:
    HAS_TINKOFF = False


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
# Тикеры → FIGI/UID (популярные MOEX инструменты)
# ═══════════════════════════════════════════════════════════════════════════════

# FIGI — можно заменить на UID через client.instruments.find_instrument
KNOWN_FIGI: dict[str, str] = {
    "SBER": "BBG004730N88",   # Сбер
    "GAZP": "BBG004730RP0",   # Газпром
    "PLZL": "BBG004S68B31",   # Полюс
    "LKOH": "BBG004731032",   # Лукойл
    "YDEX": "BBG006L8G4H1",   # Яндекс
    "GMKN": "BBG0047315D6",   # Норникель
    "ROSN": "BBG004731354",   # Роснефть
    "VTBR": "BBG004730ZJ9",   # ВТБ
    "RUAL": "BBG008F2T3T2",   # Русал
    "NVTK": "BBG00475KKY8",   # Новатэк
    "MOEX": "BBG004730JJ5",   # Мосбиржа
    "T": "TCS001650VAL",      # Т-Технологии
    "SBERP": "BBG004731489",  # Сбер преф
    "MGNT": "BBG0047315K7",   # Магнит
    "CHMF": "BBG0047316N6",   # Северсталь
    "MTSS": "BBG0047315W0",   # МТС
    "AFLT": "BBG0047332T5",   # Аэрофлот
    "SNGSP": "BBG00475K0S0",  # Сургутнефтегаз преф
    "TATN": "BBG0047315D8",   # Татнефть
    "AKRN": "BBG00475LJX8",   # Акрон
}


def resolve_figi(ticker: str) -> str | None:
    """Тикер → FIGI из известного списка, либо по API."""
    return KNOWN_FIGI.get(ticker.upper())


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
    """Интеграция с Т-Инвестициями.

    Usage:
        tink = TinkoffInvest()   # токен из env/файла
        quote = tink.get_quote("PLZL")           # цена в реальном времени
        candles = tink.get_candles("PLZL")       # 30 дневных свечей
        signal = tink.get_signal("PLZL")         # сигнал от market_intelligence
        portfolio = tink.get_portfolio()         # позиции
    """

    def __init__(self, token: str | None = None):
        self.token = get_token(token)
        self.available = bool(self.token and HAS_TINKOFF)
        self._client = None

    # ── клиент ────────────────────────────────────────────────────────────
    def _get_client(self) -> Any | None:
        if not self.available:
            return None
        if self._client is None:
            self._client = Client(self.token)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.__exit__(None, None, None)
            except Exception:
                pass
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── поиск инструмента ────────────────────────────────────────────────
    def find_figi(self, ticker: str) -> str | None:
        """Найти FIGI по тикеру: известный список, потом API."""
        figi = KNOWN_FIGI.get(ticker.upper())
        if figi:
            return figi
        client = self._get_client()
        if client is None:
            return None
        try:
            resp = client.instruments.find_instrument(query=ticker)
            for item in resp.instruments:
                if item.ticker.upper() == ticker.upper():
                    return item.figi
        except Exception:
            pass
        return None

    # ── котировка ────────────────────────────────────────────────────────
    def get_quote(self, ticker: str, figi: str | None = None) -> TinkoffQuote | None:
        """Текущая цена в реальном времени."""
        client = self._get_client()
        if client is None:
            return None
        figi = figi or self.find_figi(ticker)
        if not figi:
            return None
        try:
            resp = client.market_data.get_last_prices(figi=[figi])
            for price in resp.last_prices:
                q = price.price
                return TinkoffQuote(
                    ticker=ticker,
                    figi=figi,
                    price=q.units + q.nano / 1e9,
                    time=price.time,
                )
        except Exception:
            return None
        return None

    # ── свечи ────────────────────────────────────────────────────────────
    def get_candles(
        self,
        ticker: str,
        days: int = 30,
        interval: Any = None,
        figi: str | None = None,
    ) -> list[TinkoffCandle]:
        """Исторические свечи (по умолчанию 30 дней, дневной интервал)."""
        client = self._get_client()
        if client is None:
            return []
        figi = figi or self.find_figi(ticker)
        if not figi:
            return []
        interval = interval or CandleInterval.CANDLE_INTERVAL_DAY
        try:
            now = datetime.utcnow()
            resp = client.market_data.get_candles(
                figi=figi,
                from_=now - timedelta(days=days),
                to=now,
                interval=interval,
            )
            candles = []
            for c in resp.candles:
                candles.append(TinkoffCandle(
                    time=c.time,
                    open=c.open.units + c.open.nano / 1e9,
                    high=c.high.units + c.high.nano / 1e9,
                    low=c.low.units + c.low.nano / 1e9,
                    close=c.close.units + c.close.nano / 1e9,
                    volume=float(c.volume),
                ))
            return candles
        except Exception:
            return []

    # ── сигнал ───────────────────────────────────────────────────────────
    def get_signal(
        self,
        ticker: str,
        days: int = 30,
        sentiment_texts: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Полный сигнал от market_intelligence на РЕАЛЬНЫХ свечах.

        Возвращает dict с вердиктом, уверенностью, целями. None если нет API.
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

    # ── портфель ─────────────────────────────────────────────────────────
    def get_portfolio(self) -> list[PortfolioPosition] | None:
        """Текущие позиции портфеля."""
        client = self._get_client()
        if client is None:
            return None
        try:
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
                ticker = figi
                # Ищем тикер по FIGI в обратную сторону
                for t, f in KNOWN_FIGI.items():
                    if f == figi:
                        ticker = t
                        break
                cur = p.current_price.units + p.current_price.nano / 1e9
                avg = (p.average_position_price.units +
                       p.average_position_price.nano / 1e9) if p.average_position_price else 0
                qty = p.quantity.units + p.quantity.nano / 1e9
                profit = ((cur - avg) / avg * 100) if avg else 0.0
                positions.append(PortfolioPosition(
                    ticker=ticker,
                    figi=figi,
                    quantity=qty,
                    current_price=cur,
                    avg_price=avg,
                    profit_pct=round(profit, 2),
                ))
            return positions
        except Exception:
            return None

    # ── сигналы по всему портфелю ───────────────────────────────────────
    def scan_portfolio(self) -> list[dict[str, Any]]:
        """Сигналы по всем бумагам в портфеле."""
        positions = self.get_portfolio() or []
        results = []
        for pos in positions:
            if pos.ticker == figi_unknown(pos.figi):
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


def figi_unknown(figi: str) -> str:
    """Возвращает 'UNKNOWN' если FIGI не в известном списке."""
    for t, f in KNOWN_FIGI.items():
        if f == figi:
            return t
    return "UNKNOWN"


def status() -> dict[str, Any]:
    """Статус интеграции (без токена в выводе)."""
    token = get_token()
    return {
        "sdk_installed": HAS_TINKOFF,
        "token_found": bool(token),
        "token_source": ("env" if os.environ.get(TOKEN_ENV) else
                         ("file" if TOKEN_FILE.exists() else "none")),
        "known_tickers": len(KNOWN_FIGI),
    }
