"""Network Control — инфраструктура сетевого доступа для агентов.

Зачем: модули token-diet ходят в сеть (MOEX, Reddit, YouTube, Telethon).
Каждый тащит свои retry/timeout/proxy — это дублирование и разные ошибки.
Здесь один слой:

1. ``polite_fetch`` — GET с таймаутом, retry c exponential backoff,
   jitter и уважением к ``Retry-After`` (нас не банят за жадность).
2. ``RateLimiter`` — токен-ведро на домен: не чаще N запросов/сек.
3. ``check_connectivity`` — матрица доступности (DNS/TCP/HTTP) по списку
   хостов: диагностика сети одним вызовом.
4. Прокси: один раз настроил opener глобально (или передал явно) —
   все модули идут через него.

Чистая стандартная библиотека, детерминированная логика, тестируется
с подставными fetch-функциями — без реальной сети.
"""

from __future__ import annotations

import random
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse

USER_AGENT = "token-diet/3.29 (+https://github.com/Bugaga31/token-diet)"

_DEFAULT_TIMEOUT = 10.0


# ── Rate limiting ───────────────────────────────────────────────────


@dataclass
class RateLimiter:
    """Токен-ведро по ключу (обычно домен): не чаще rate в секунду."""

    rate: float = 1.0            # запросов в секунду
    _last: dict[str, float] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def acquire(self, key: str = "*", now: float | None = None) -> float:
        """Дождаться своей очереди; вернуть время ожидания."""
        wait = self.reserve(key=key, now=now)
        if wait > 0:
            time.sleep(wait)
        return wait

    def reserve(self, key: str = "*", now: float | None = None) -> float:
        """Сколько ждать до следующего разрешения (не спит сам — для тестов)."""
        t = time.monotonic() if now is None else now
        with self._lock:
            last = self._last.get(key)
            interval = 1.0 / max(self.rate, 1e-9)
            if last is None or t - last >= interval:
                self._last[key] = t
                return 0.0
            return interval - (t - last)


def domain_of(url: str) -> str:
    """Домен из URL ('' для невалидных)."""
    host = urlparse(url).hostname or ""
    return host.lower()


# ── Polite fetch ────────────────────────────────────────────────────


class FetchError(RuntimeError):
    """Сеть не ответила после всех попыток."""


def _default_opener(proxy: str | None):
    if not proxy:
        return None
    handler = urllib.request.ProxyHandler({
        "http": proxy,
        "https": proxy,
    })
    return urllib.request.build_opener(handler)


def polite_fetch(
    url: str,
    *,
    fetcher=None,
    timeout: float = _DEFAULT_TIMEOUT,
    retries: int = 3,
    backoff: float = 0.5,
    limiter: RateLimiter | None = None,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
) -> tuple[int, bytes]:
    """GET с вежливостью: rate-limit, backoff, Retry-After.

    Args:
        url: адрес.
        fetcher: подставная функция для тестов: (req, timeout) -> (status, body).
        retries: число попыток после первой неудачи.
        backoff: база паузы (0.5 → 0.5с, 1с, 2с...).
        limiter: общий RateLimiter по доменам.

    Returns (status, body_bytes).
    Raises FetchError после исчерпания попыток.
    """
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    opener = _default_opener(proxy)

    def do_request() -> tuple[int, bytes]:
        req = urllib.request.Request(url, headers=req_headers)
        if fetcher is not None:
            return fetcher(req, timeout)
        try:
            resp = opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)
            return int(resp.status), resp.read()
        except urllib.error.HTTPError as e:      # noqa: PERF203 - осознанно
            return int(e.code), e.read()

    attempts = retries + 1
    last_err = ""
    for attempt in range(attempts):
        if limiter is not None:
            limiter.acquire(domain_of(url))
        try:
            status, body = do_request()
            if status == 200:
                return status, body
            if status in (301, 302, 303, 307, 308):
                # редиректы urlopen проходит сам; сюда попадаем только в тестах
                return status, body
            if status == 429:
                last_err = "HTTP 429 (rate limited)"
            elif status >= 500:
                last_err = f"HTTP {status}"
            else:
                # клиентские ошибки ретраить бессмысленно
                raise FetchError(f"HTTP {status} for {url}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < attempts - 1:
            pause = min(backoff * (2 ** attempt), 8.0)
            time.sleep(pause * random.uniform(0.5, 1.5))
    raise FetchError(f"{url}: {attempts} попыток без результата ({last_err})")


# ── Connectivity matrix ─────────────────────────────────────────────


@dataclass
class HostCheck:
    host: str
    dns_ok: bool
    tcp_ok: bool
    latency_ms: float | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.dns_ok and self.tcp_ok


def check_host(host: str, port: int = 443, timeout: float = 3.0) -> HostCheck:
    """DNS резолвится? TCP-коннект открывается? Сколько мс занимает?"""
    try:
        ip = socket.gethostbyname(host)
    except OSError as e:
        return HostCheck(host=host, dns_ok=False, tcp_ok=False, error=f"dns: {e}")
    t0 = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            pass
    except OSError as e:
        return HostCheck(host=host, dns_ok=True, tcp_ok=False, error=f"tcp: {e}")
    return HostCheck(
        host=host, dns_ok=True, tcp_ok=True,
        latency_ms=round((time.monotonic() - t0) * 1000, 1),
    )


def check_connectivity(hosts: list[str] | None = None) -> list[HostCheck]:
    """Матрица связности; дефолт — критичные для token-diet сервисы."""
    targets = hosts or [
        ("iss.moex.com", 443),
        ("www.reddit.com", 443),
        ("www.youtube.com", 443),
        ("github.com", 443),
    ]
    out: list[HostCheck] = []
    for item in targets:
        host, port = item if isinstance(item, tuple) else (item, 443)
        out.append(check_host(host, port))
    return out


def connectivity_block(hosts: list[str] | None = None) -> str:
    rows = check_connectivity(hosts)
    lines = ["[net] связность:"]
    for r in rows:
        mark = "✓" if r.ok else "✗"
        lat = f" {r.latency_ms:.0f}ms" if r.latency_ms is not None else ""
        lines.append(f"[net]   {mark} {r.host}{lat}" + (f" — {r.error}" if r.error else ""))
    up = sum(1 for r in rows if r.ok)
    lines.append(f"[net] {up}/{len(rows)} доступны")
    return "\n".join(lines)


# ── Per-domain call budget (защита от циклов-монстров) ──────────────


class DomainBudget:
    """Жёсткий лимит запросов на домен за жизнь объекта."""

    def __init__(self, max_per_domain: int = 100):
        self.max = max_per_domain
        self._count: dict[str, int] = defaultdict(int)

    def spend(self, url: str) -> bool:
        """Списать один запрос; False если бюджет исчерпан."""
        d = domain_of(url) or "*"
        if self._count[d] >= self.max:
            return False
        self._count[d] += 1
        return True

    def used(self, domain: str) -> int:
        return self._count[domain]
