"""scrapling_diet — техники Scrapling, реверс-инжиниринг в чистый Python.

Что взято у Scrapling (https://github.com/d4vinci/Scrapling) и переписано
под наши нужды без их зависимостей (только lxml + requests + websocket):

  1. network_capture()      — «capture_xhr»: перехват XHR/fetch ответов
     через CDP. Берём JSON-API любого сайта, не реверся запросы.
  2. adaptive_select()      — «adaptive=True»: селектор не нашёл элементов?
     Ищем похожие (token-overlap по классам) — переживаем редизайны.
  3. find_by_text()         — поиск элементов по текстовому содержимому.
  4. AutoThrottle           — детекция блокировок (403/429/Cloudflare/
     Turnstile) и адаптивные паузы между запросами.
  5. DiskResponseCache      — dev-режим: кеш ответов на диск, повторные
     заходы не бьют по сети и не жгут токены.
  6. ProxyRotator           — циклическая ротация прокси со скипом битых.
  7. LinkExtractor          — извлечение ссылок с allow/deny фильтрами.

Всё — без нейронок и без API-ключей. Работает локально, <1ms на парсинг.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from lxml import etree, html

try:
    from .core import count_tokens  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # type: ignore[no-redef]

CACHE_DIR = os.path.expanduser("~/.cache/token-diet/response-cache")

# ── 1. NETWORK CAPTURE (XHR/fetch через CDP) ────────────────────────────────


@dataclass
class CapturedResponse:
    """Один перехваченный XHR/fetch ответ страницы."""

    url: str
    status: int
    body: str
    content_type: str = ""

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except json.JSONDecodeError:
            return None

    @property
    def is_json(self) -> bool:
        return self.body.lstrip().startswith(("{", "["))


def network_capture(ws_url: str, url_pattern: str = "",
                    timeout: float = 15.0, body_limit: int = 2_000_000,
                    stop_on_event: str = "") -> list[CapturedResponse]:
    """Перехват XHR/fetch ответов страницы через CDP (как capture_xhr у Scrapling).

    Открываем отдельный WS-канал, включаем Network, слушаем ответы,
    достаём тела через Network.getResponseBody. Совпадение по url_pattern
    (подстрока или regex). Возвращает список тел — JSON-API сайта
    достаётся без реверс-инжиниринга запросов.
    """
    import websocket

    captured: list[CapturedResponse] = []
    try:
        ws = websocket.create_connection(ws_url, timeout=60)
    except Exception:  # noqa: BLE001 — нет браузера/порта
        return captured
    try:
        _send(ws, "Network.enable", {})
        _send(ws, "Runtime.enable", {})
        _capture_loop(ws, url_pattern, timeout, captured, body_limit,
                      stop_on_event=stop_on_event)
        return captured
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def _send(ws: Any, method: str, params: dict | None = None) -> dict:
    """Отправить CDP-команду и дождаться ответа с её id."""

    ws.send(json.dumps({"id": 0, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 0:
            return msg
        # события пропускаем — они обрабатываются в основном цикле


def browser_api_grab(url: str, url_pattern: str = "", wait_seconds: int = 12,
                     timeout: float = 15.0) -> list[CapturedResponse]:
    """Удобная обёртка: поднять headless-браузер → открыть URL →
    перехватить все XHR/fetch, матчащие pattern.

    Навигация и перехват идут по ОДНОМУ WS-соединению — без гонок.
    Пример: открываем страницу котировок и забираем JSON-API биржи,
    не разбираясь, какие запросы она шлёт.
    """
    import websocket

    from .browser_llm import CDP_HOST, CDP_PORT, _get_page_ws, start_chrome

    proc = None
    try:
        proc = start_chrome()
        import urllib.request

        for _ in range(40):
            try:
                with urllib.request.urlopen(
                    f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=2
                ) as r:
                    json.loads(r.read())
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        ws_url = _get_page_ws()
        captured: list[CapturedResponse] = []
        try:
            ws = websocket.create_connection(ws_url, timeout=60)
        except Exception:  # noqa: BLE001
            return captured
        try:
            _send(ws, "Network.enable", {})
            _send(ws, "Page.enable", {})
            ws.send(json.dumps({
                "id": 1, "method": "Page.navigate", "params": {"url": url},
            }))
            _capture_loop(ws, url_pattern, timeout, captured)
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        return captured
    finally:
        if proc:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


def _capture_loop(ws: Any, url_pattern: str, timeout: float,
                  captured: list[CapturedResponse],
                  body_limit: int = 2_000_000,
                  stop_on_event: str = "") -> None:
    """Читать CDP-события Network и собирать тела (общий цикл).

    ВАЖНО: всё в одном recv-цикле. Нельзя ждать ответ команды внутри
    обработки события — события, пришедшие в это время, теряются.
    Тела запрашиваем fire-and-forget и сопоставляем по id ответа.
    """
    pending: dict[str, tuple[str, int, str]] = {}   # requestId → слот
    body_reqs: dict[int, tuple[str, int, str]] = {}  # id команды → слот
    pattern = re.compile(url_pattern) if url_pattern else None
    cmd_id = 100
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ws.settimeout(max(0.1, deadline - time.monotonic()))
            msg = json.loads(ws.recv())
        except Exception:  # noqa: BLE001 — таймаут/обрыв
            break
        mid = msg.get("id")
        if mid is not None:
            slot = body_reqs.pop(mid, None)
            if slot:
                body = ((msg.get("result") or {}).get("body") or "")[:body_limit]
                captured.append(CapturedResponse(slot[0], slot[1], body, slot[2]))
            continue  # ответ на нашу команду
        method = msg.get("method", "")
        params = msg.get("params", {}) or {}
        if method == "Network.responseReceived":
            resp = params.get("response", {})
            purl = resp.get("url", "")
            if pattern and not pattern.search(purl):
                continue
            if not purl.startswith(("http://", "https://")):
                continue
            pending[params.get("requestId", "")] = (
                purl, resp.get("status", 0),
                (resp.get("mimeType") or "").split(";")[0],
            )
        elif method == "Network.loadingFinished":
            rid = params.get("requestId", "")
            if rid not in pending:
                continue
            slot = pending.pop(rid)
            cmd_id += 1
            body_reqs[cmd_id] = slot
            try:
                ws.send(json.dumps({
                    "id": cmd_id, "method": "Network.getResponseBody",
                    "params": {"requestId": rid},
                }))
            except Exception:  # noqa: BLE001 — сокет упал
                body_reqs.pop(cmd_id, None)
        if stop_on_event and method == stop_on_event:
            break


# ── 2. ADAPTIVE SELECT ──────────────────────────────────────────────────────


def _tokenize(s: str) -> set[str]:
    return {w for w in re.split(r"[^a-zа-я0-9_]+", s.lower()) if len(w) > 1}


def _simple_css_select(root: Any, selector: str) -> list[Any]:
    """Мини-CSS: .class, #id, tag, tag.class, tag#id, запятые — без cssselect.

    lxml.cssselect требует внешний пакет cssselect — у нас его нет,
    поэтому свой матчер на XPath (покрывает 99% реальных селекторов).
    """
    out: list[Any] = []
    for part in selector.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([a-zA-Z][\w-]*)?([.#][\w-]+)$", part)
        if not m:
            continue
        tag, ident = m.group(1), m.group(2)
        tag_x = tag or "*"
        if ident.startswith("."):
            cls = ident[1:]
            xpath = (f"//{tag_x}[contains(concat(' ', normalize-space(@class), ' '), "
                     f"' {cls} ')]")
        else:
            xpath = f"//{tag_x}[@id='{ident[1:]}']"
        try:
            out.extend(root.xpath(xpath))
        except etree.XPathError:  # pragma: no cover
            continue
    return out


def adaptive_select(html_text: str, selector: str,
                    context: str = "") -> list[dict[str, str]]:
    """«adaptive=True»: селектор не сработал на новом дизайне? Ищем похожие.

    Сначала пробуем точный CSS-селектор (свой матчер + lxml.cssselect,
    если пакет стоит). Если пусто — ищем узлы, чьи классы пересекаются
    с классом из селектора (token overlap), и берём их текст.
    Возвращает список {"text": ..., "tag": ..., "class": ...}.

    Пример: страница сменила .product на .product-card — мы всё равно
    находим карточки.
    """
    try:
        root = html.fromstring(html_text)
    except etree.ParserError:
        return []
    found: list[dict[str, str]] = []

    matched: list[Any] = []
    try:
        matched = _simple_css_select(root, selector)
    except Exception:  # noqa: BLE001
        pass
    if not matched:
        try:
            matched = root.cssselect(selector)  # если cssselect всё же стоит
        except Exception:  # noqa: BLE001 — кривой селектор / нет пакета
            matched = []
    for el in matched:
        found.append(_el_summary(el))
    if found:
        return found

    # ── фолбэк: similarity relocation ──
    # достаём «целевые» классы из селектора: .foo.bar → {foo, bar},
    # .product-item → {product, item} (дефисы разбиваем — как в _tokenize)
    target_classes: set[str] = set()
    for m in re.findall(r"\.([a-zA-Z_][\w-]*)", selector):
        target_classes.update(t for t in m.split("-") if t)
    if not target_classes:
        return []

    def walk(node: Any, depth: int = 0) -> Iterator[Any]:
        if depth > 40:
            return
        for child in node.iterchildren():
            yield child
            yield from walk(child, depth + 1)

    best: list[tuple[float, Any]] = []
    for el in walk(root):
        if el.tag in ("script", "style", "comment") or not isinstance(el.tag, str):
            continue
        cls = " ".join(el.get("class", "").split())
        overlap = len(_tokenize(cls) & target_classes)
        if overlap >= max(1, len(target_classes) // 2):
            best.append((overlap / max(1, len(target_classes)), el))

    best.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    for _, el in best[:8]:
        key = etree.tostring(el)[:400]
        if key in seen:
            continue
        seen.add(key)
        found.append(_el_summary(el))
    return found


def _el_summary(el: Any) -> dict[str, str]:
    text = " ".join((el.text_content() or "").split())
    if len(text) > 2000:
        text = text[:2000] + "…"
    return {
        "text": text,
        "tag": el.tag if isinstance(el.tag, str) else "",
        "class": " ".join(el.get("class", "").split()),
        "id": el.get("id", ""),
    }


# ── 3. FIND BY TEXT ─────────────────────────────────────────────────────────


def find_by_text(html_text: str, text: str, tag: str = "*",
                 fuzzy: bool = True) -> list[dict[str, str]]:
    """Найти элементы по текстовому содержимому (как find_by_text у Scrapling).

    fuzzy=True — допускает частичное совпадение (регистр, обрезка).
    """
    try:
        root = html.fromstring(html_text)
    except etree.ParserError:
        return []
    needle = text.lower().strip()
    out: list[dict[str, str]] = []
    for el in root.iter(tag):
        if el.tag in ("script", "style", "comment"):
            continue
        own = " ".join((el.text_content() or "").split())
        if not own:
            continue
        if fuzzy:
            if needle in own.lower():
                out.append(_el_summary(el))
        elif own.lower() == needle:
            out.append(_el_summary(el))
        if len(out) >= 20:
            break
    return out


# ── 4. AUTO THROTTLE ────────────────────────────────────────────────────────

_BLOCK_MARKERS = (
    "cloudflare", "turnstile", "captcha", "attention required",
    "access denied", "just a moment", "challenge", "rate limit",
    "too many requests", "cf-chl", "ddos-guard", "blocked",
)


@dataclass
class AutoThrottle:
    """Детекция блокировок + адаптивные паузы (как AutoThrottle у Scrapling).

    Отвечает быстро — пауза растёт. Заблокировали — пауза удваивается
    (или ждём Retry-After). Работает и как sleep-планировщик.
    """

    base_delay: float = 0.5
    max_delay: float = 30.0
    backoff: float = 2.0
    _current: float = field(default=0.0, init=False)
    _hits: int = field(default=0, init=False)
    _blocks: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._current = self.base_delay

    @staticmethod
    def detect_blocked(status: int, body: str = "") -> bool:
        """403/429/503 или страница-вызов (Cloudflare/Turnstile/rate limit).

        Cloudflare challenge часто отдаёт HTTP 200 + страницу «Just a moment» —
        поэтому маркеры проверяем при любом статусе (если есть тело).
        """
        if status in (403, 429, 503):
            return True
        if body:
            low = body[:4000].lower()
            return any(m in low for m in _BLOCK_MARKERS)
        return False

    def report(self, status: int, body: str = "", retry_after: int = 0) -> None:
        if self.detect_blocked(status, body):
            self._blocks += 1
            self._current = min(self._current * self.backoff, self.max_delay)
            if retry_after:
                self._current = max(self._current, float(retry_after))
        else:
            self._hits += 1
            # сайт отвечает — плавно разгоняемся обратно
            self._current = max(self.base_delay, self._current / self.backoff)

    def wait(self) -> None:
        if self._current > 0:
            time.sleep(self._current)

    @property
    def stats(self) -> dict[str, Any]:
        return {"hits": self._hits, "blocks": self._blocks,
                "current_delay": round(self._current, 2)}


# ── 5. DISK RESPONSE CACHE ──────────────────────────────────────────────────


class DiskResponseCache:
    """Dev-режим Scrapling: кеш ответов на диск, повторные заходы —
    без сети. Экономит и токены (не парсим одно и то же), и трафик.

    Ключ = sha1(method + url + body_hash). Срок жизни — опционально.
    """

    def __init__(self, cache_dir: str = CACHE_DIR, ttl_seconds: int = 3600):
        self.dir = Path(cache_dir)
        self.ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def _key(self, url: str, method: str = "GET", payload: str = "") -> str:
        raw = f"{method}|{url}|{payload}".encode()
        return hashlib.sha1(raw).hexdigest()[:32]

    def get(self, url: str, method: str = "GET", payload: str = "") -> str | None:
        p = self.dir / self._key(url, method, payload)
        if not p.exists():
            self._misses += 1
            return None
        age = time.time() - p.stat().st_mtime
        if self.ttl > 0 and age > self.ttl:
            self._misses += 1
            try:
                p.unlink()
            except OSError:
                pass
            return None
        self._hits += 1
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            self._misses += 1
            return None

    def set(self, url: str, body: str, method: str = "GET", payload: str = "") -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / self._key(url, method, payload)).write_text(
                body, encoding="utf-8")
        except OSError:  # pragma: no cover
            pass

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses}

    def size_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.dir.glob("*")
                   if f.is_file()) if self.dir.exists() else 0


def cached_fetch(url: str, cache: DiskResponseCache | None = None,
                 headers: dict | None = None, timeout: float = 20.0,
                 proxy: str = "") -> tuple[str, int, bool]:
    """GET с кешем: вернул кеш → (тело, 0, True); иначе запрос + запись."""
    cache = cache or DiskResponseCache()
    hit = cache.get(url)
    if hit is not None:
        return hit, 0, True
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = requests.get(url, headers=headers or {}, timeout=timeout, proxies=proxies)
    if r.status_code == 200:
        cache.set(url, r.text)
    return r.text, r.status_code, False


# ── 6. PROXY ROTATOR ────────────────────────────────────────────────────────


class ProxyRotator:
    """Циклическая ротация прокси (как ProxyRotator у Scrapling).

    Битые помечаются и временно пропускаются. Потокобезопасно.
    """

    def __init__(self, proxies: Iterable[str], skip_seconds: int = 60):
        self._proxies: list[str] = list(proxies)
        self._idx = 0
        self._skip_until: dict[str, float] = {}
        self._skip_seconds = skip_seconds
        self._lock = threading.Lock()

    def next(self) -> str:
        with self._lock:
            for _ in range(len(self._proxies)):
                proxy = self._proxies[self._idx % len(self._proxies)]
                self._idx += 1
                if time.time() >= self._skip_until.get(proxy, 0):
                    return proxy
            # все в отлёте — берём первый
            return self._proxies[self._idx % len(self._proxies)]

    def mark_bad(self, proxy: str) -> None:
        with self._lock:
            self._skip_until[proxy] = time.time() + self._skip_seconds

    def __len__(self) -> int:
        return len(self._proxies)


# ── 7. LINK EXTRACTOR ───────────────────────────────────────────────────────


@dataclass
class LinkExtractor:
    """Ссылки со страницы с фильтрами (как LinkExtractor у Scrapling)."""

    base_url: str = ""
    allow: tuple[str, ...] = ()          # regex — обязательное совпадение
    deny: tuple[str, ...] = ()           # regex — запрет
    allowed_domains: tuple[str, ...] = ()  # только эти домены
    allowed_extensions: tuple[str, ...] = (".html", ".htm", "/")  # пустое = все
    deny_extensions: tuple[str, ...] = (".pdf", ".zip", ".jpg", ".jpeg",
                                        ".png", ".gif", ".mp4", ".mp3")

    def extract(self, html_text: str) -> list[str]:
        try:
            root = html.fromstring(html_text)
        except etree.ParserError:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for a in root.iter("a"):
            href = a.get("href")
            if not href:
                continue
            href = href.strip()
            if href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
                continue
            url = urllib.parse.urljoin(self.base_url, href)
            url = urllib.parse.urldefrag(url)[0]
            if url in seen:
                continue
            if not self._allowed(url):
                continue
            seen.add(url)
            out.append(url)
        return out

    def _allowed(self, url: str) -> bool:
        if self.deny_extensions:
            path = urllib.parse.urlparse(url).path.lower()
            if any(path.endswith(e) for e in self.deny_extensions):
                return False
        if self.allowed_extensions and "/" not in self.allowed_extensions:
            path = urllib.parse.urlparse(url).path.lower()
            if not any(path.endswith(e) for e in self.allowed_extensions):
                return False
        if self.allowed_domains:
            host = urllib.parse.urlparse(url).netloc.lower()
            if not any(host == d or host.endswith("." + d)
                       for d in self.allowed_domains):
                return False
        if self.allow and not any(re.search(p, url) for p in self.allow):
            return False
        if self.deny and any(re.search(p, url) for p in self.deny):
            return False
        return True


# ── CLI-помощник ────────────────────────────────────────────────────────────


def scrape_cli(url: str, selector: str = "", text: str = "",
               use_cache: bool = True, proxy: str = "") -> str:
    """`scrape URL [--selector .foo] [--text 'искомый текст']` — быстрый
    парсинг без браузера: кеш → fetch → adaptive_select / find_by_text."""
    cache = DiskResponseCache() if use_cache else None
    body, status, from_cache = cached_fetch(url, cache=cache, proxy=proxy)
    if status and status != 200:
        return (f"HTTP {status} (detect_blocked="
                f"{AutoThrottle.detect_blocked(status, body)})")
    lines = [f"# {url}  [{'CACHE' if from_cache else 'LIVE'}]  "
             f"{len(body)} bytes"]
    if selector:
        for el in adaptive_select(body, selector):
            lines.append(f"\n<{el['tag']} class=\"{el['class']}\">\n{el['text']}")
        if len(lines) == 1:
            lines.append("\n(точный селектор не сработал — adaptive не нашёл "
                         "похожих)")
    elif text:
        for el in find_by_text(body, text):
            lines.append(f"\n<{el['tag']} class=\"{el['class']}\">\n{el['text']}")
    else:
        from .clean_scraper import html_to_text

        lines.append("\n" + html_to_text(body))
    return "\n".join(lines)


__all__ = [
    "CapturedResponse", "network_capture", "browser_api_grab",
    "adaptive_select", "find_by_text", "AutoThrottle",
    "DiskResponseCache", "cached_fetch", "ProxyRotator",
    "LinkExtractor", "scrape_cli",
]
