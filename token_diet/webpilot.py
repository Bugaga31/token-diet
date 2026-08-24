"""WebPilot — целевой веб-агент для исследований (0 LLM-вызовов).

Воспроизводит технику WebPilot (arXiv:2408.15978) на stdlib:

1. Поиск      — s.jina.ai (без ключа) с fallback на DuckDuckGo HTML
2. Чтение     — r.jina.ai/{url} (чистый markdown) с fallback на
                 clean_scraper.scrape_url
3. Селекция   — оставляем ТОЛЬКО предложения, релевантные вопросу
                 (техника Exa Highlights — экономия токенов: не тащим
                 всю страницу, а только «хиты»)
4. Навигация  — переходы по ссылкам, чей якорный текст похож на вопрос,
                 с жёстким лимитом шагов (WebPilot step budget)
5. Сборка     — компактный evidence-пакет: вопрос + источники + хиты,
                 готовый скормить любой LLM без раздувания контекста

Безопасность: только GET, только http/https, лимиты шагов и размера,
SSL-fallback как в остальных модулях (VPN/DPI-перехват).
"""

from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

JINA_READ = "https://r.jina.ai/{url}"
JINA_SEARCH = "https://s.jina.ai/?q={q}"
DDG_SEARCH = "https://html.duckduckgo.com/html/?q={q}"

_STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have how in is it its of on or
    that the this to was were what when where which who will with you your
    и в во на не он она оно они я ты мы вы это то как так но а или если
    что чтобы при от до по за из для с со к у о об же бы ли их его её
    всех всё весь который которая которые было будет есть ещё уже нет да
    очень просто можно нужно надо
    """.split()
)

_JS_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)
_HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://[^\s\"'<>()]+")
_UDDG_RE = re.compile(r"[?&]uddg=([^&]+)")


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http_get(url: str, timeout: float = 12.0) -> str | None:
    """GET с браузерным UA. SSL-fallback. Возвращает текст или None."""
    # не-ASCII (кириллица в URL) кодируем, служебные символы не трогаем
    url = urllib.parse.quote(url, safe="%/:=&?~#+!$,;'@()*[]")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[а-яёa-z0-9-]+", text.lower()) if w not in _STOPWORDS and len(w) > 1]


def search(query: str, limit: int = 5, timeout: float = 12.0) -> list[str]:
    """Найти URL по запросу. Jina → DuckDuckGo fallback."""
    q = urllib.parse.quote(query)
    # 1) Jina Search
    body = _http_get(JINA_SEARCH.format(q=q), timeout=timeout)
    urls: list[str] = []
    if body:
        for m in _URL_RE.findall(body):
            u = m.rstrip(".,);]")
            if u not in urls and "jina.ai" not in u:
                urls.append(u)
            if len(urls) >= limit:
                break
    if len(urls) >= limit:
        return urls[:limit]
    # 2) DuckDuckGo fallback
    body = _http_get(DDG_SEARCH.format(q=q), timeout=timeout)
    if body:
        for m in _UDDG_RE.findall(body):
            u = urllib.parse.unquote(m)
            if u.startswith("http") and u not in urls:
                urls.append(u)
            if len(urls) >= limit:
                break
    return urls[:limit]


def read_page(url: str, timeout: float = 12.0) -> str:
    """Чистый текст страницы: Jina Reader → clean_scraper fallback."""
    clean = _http_get(JINA_READ.format(url=urllib.parse.quote(url, safe="")), timeout=timeout)
    if clean and len(clean.strip()) > 200:
        return clean.strip()
    from .clean_scraper import scrape_url

    res = scrape_url(url, max_chars=50000, timeout=timeout)
    if res.status == "ok":
        return (res.text or "").strip()
    return ""


def _stem(w: str, n: int = 4) -> str:
    """Лёгкий стемминг для русского/английского: общий префикс.

    «золото»/«золота»/«золотом» → «золо» — морфология не мешает скорингу.
    """
    return w[:n] if len(w) > n else w


def _score_sentence(sentence: str, qwords: set[str]) -> int:
    sw = {_stem(w) for w in _words(sentence)}
    return sum(1 for w in qwords if _stem(w) in sw)


def _looks_like_text(s: str) -> bool:
    """Отсечь JS/JSON-мусор: строки с фигурными скобками или без букв."""
    if "{" in s or "}" in s or "function(" in s:
        return False
    letters = sum(1 for ch in s if ch.isalpha())
    return len(s) >= 12 and letters / max(len(s), 1) >= 0.55


def extract_relevant(text: str, question: str, max_chars: int = 4000) -> str:
    """Оставить только предложения, релевантные вопросу (Exa Highlights).

    Экономит токены: вместо всей страницы — только смысловые «хиты».
    """
    if not text or not question:
        return text[:max_chars].strip()
    qwords = set(_words(question))
    if not qwords:
        return text[:max_chars].strip()

    sentences = re.split(r"(?<=[.!?…])\s+|\n+", text)
    scored = [(s.strip(), _score_sentence(s, qwords)) for s in sentences if _looks_like_text(s)]
    hits = [s for s, sc in scored if sc > 0]
    if not hits:  # ничего не совпало — берём самые длинные значимые
        hits = sorted((s for s, sc in scored if len(s) > 25), key=len, reverse=True)[:3]
    hits.sort(key=lambda s: _score_sentence(s, qwords), reverse=True)

    out, used = [], 0
    for h in hits:
        h = " ".join(h.split())
        if used + len(h) + 1 > max_chars:
            h = h[: max_chars - used]
        if h:
            out.append(h)
            used += len(h) + 1
        if used >= max_chars:
            break
    return "\n".join(out).strip()


def _hrefs_with_score(html_text: str, question: str, limit: int = 8) -> list[str]:
    """Ссылки с якорем, похожим на вопрос (сортировка по релевантности)."""
    qwords = set(_words(question))
    cands: list[tuple[int, str]] = []
    for m in _HREF_RE.findall(html_text):
        href, anchor = m
        anchor_text = _TAG_RE.sub("", anchor)
        if not href.startswith("http"):
            continue
        score = _score_sentence(anchor_text, qwords)
        if score > 0:
            cands.append((score, href))
    cands.sort(key=lambda x: x[0], reverse=True)
    seen: list[str] = []
    for _, href in cands:
        if href not in seen:
            seen.append(href)
        if len(seen) >= limit:
            break
    return seen


@dataclass
class WebPilotResult:
    question: str
    sources: list[str] = field(default_factory=list)
    evidence: str = ""
    steps: int = 0
    pages_read: int = 0

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "sources": self.sources,
            "evidence": self.evidence,
            "steps": self.steps,
            "pages_read": self.pages_read,
            "evidence_chars": len(self.evidence),
        }

    def prompt_block(self, max_chars: int = 6000) -> str:
        """Готовый компактный блок для любой LLM."""
        ev = self.evidence[:max_chars]
        src = "\n".join(f"- {s}" for s in self.sources[:10])
        return (
            f"[WebPilot] Вопрос: {self.question}\n"
            f"Прочитано страниц: {self.pages_read}\n\n"
            f"Свидетельства:\n{ev}\n\n"
            f"Источники:\n{src}"
        )


def ask_web(
    question: str,
    start_urls: list[str] | None = None,
    max_steps: int = 4,
    max_chars: int = 4000,
    timeout: float = 12.0,
) -> WebPilotResult:
    """Целевое исследование: поиск → чтение → селекция → переходы.

    max_steps — лимит переходов по ссылкам (WebPilot step budget),
    защищает от бесконечного блуждания и раздувания токенов.
    """
    result = WebPilotResult(question=question)
    queue: list[str] = list(start_urls or [])
    if not queue:
        queue = search(question, limit=3, timeout=timeout)
    visited: set[str] = set()
    evidence_parts: list[str] = []
    used = 0
    steps = 0

    while queue and steps < max_steps and used < max_chars:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        raw = _http_get(url, timeout=timeout)
        if not raw:
            continue
        steps += 1
        result.sources.append(url)

        page_text = extract_relevant(_TAG_RE.sub(" ", raw[:120000]), question, max_chars=max_chars - used)
        if len(page_text) > 40:
            result.pages_read += 1
            snippet = page_text[: max_chars - used]
            if snippet:
                evidence_parts.append(f"## {url}\n{snippet}")
                used += len(snippet) + 4
        # следующий слой ссылок — только релевантные вопросу
        if steps < max_steps:
            for next_url in _hrefs_with_score(raw, question, limit=3):
                if next_url not in visited and next_url not in queue:
                    queue.append(next_url)

    result.evidence = "\n".join(evidence_parts)
    return result
