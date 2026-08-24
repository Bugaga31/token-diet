"""Tinkoff Pulse (Пульс) reader — читает соцсеть Т-Инвестиций.

Что это и зачем:
  Пульс — соцсеть Т-Инвестиций, где розница и «крупные» обсуждают бумаги.
  Сигналы оттуда (настроение толпы, упоминания, лайки) часто опережают новости.
  token-diet умеет это читать БЕЗ токена — публичная лента по тикеру
  открыта через веб-шлюз: https://www.tinkoff.ru/api/invest-gw/social/v1/

Безопасность:
  - Никаких токенов не требуется (публичный шлюз).
  - SSL: пробуем проверку сертификата, при DPI-перехвате (самоподписанный
    сертификат VPN) — fallback на не-проверенный SSL, как в tinkoff_invest.
  - Только чтение. Никаких лайков/постов/комментариев от имени пользователя.

Endpoints:
  GET /post/instrument/{TICKER}   — посты по тикеру (публично)
  GET /post/{POST_ID}/comment     — комментарии к посту
  GET /profile/nickname/{NICK}    — профиль по никнейму
  GET /profile/{USER_ID}/post     — посты пользователя
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

SOCIAL_BASE = "https://www.tinkoff.ru/api/invest-gw/social/v1"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
}


@dataclass
class PulsePost:
    """Нормализованный пост Пульса."""

    id: str
    text: str
    nickname: str
    inserted: str
    likes: int = 0
    comments: int = 0
    instruments: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)


def _fetch(url: str, timeout: int = 15) -> dict | None:
    """GET JSON с веб-шлюза Пульса. SSL-fallback как в tinkoff_invest."""
    req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    return json.loads(resp.read().decode("utf-8", errors="replace"))
            raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def parse_post(raw: dict) -> PulsePost | None:
    """Сырой пост шлюза → PulsePost (устойчиво к разным форматам поля text)."""
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    text = raw.get("text")
    if not text:
        content = raw.get("content")
        text = content.get("text") if isinstance(content, dict) else None
    instruments = [
        ins.get("ticker")
        for ins in (raw.get("instruments") or [])
        if isinstance(ins, dict) and ins.get("ticker")
    ]
    return PulsePost(
        id=str(raw["id"]),
        text=(text or "").strip(),
        nickname=raw.get("nickname") or "?",
        inserted=str(raw.get("inserted") or "")[:19].replace("T", " "),
        likes=int(raw.get("likesCount") or 0),
        comments=int(raw.get("commentsCount") or 0),
        instruments=instruments,
        hashtags=[str(h) for h in (raw.get("hashtags") or [])],
    )


def get_ticker_posts(ticker: str, limit: int = 30, cursor: int | None = None) -> list[PulsePost]:
    """Свежие посты по тикеру (например 'PLZL'). cursor=None → самые новые."""
    params = f"limit={int(limit)}&appName=invest&platform=web"
    if cursor:
        params += f"&cursor={int(cursor)}"
    url = f"{SOCIAL_BASE}/post/instrument/{ticker}?{params}"
    data = _fetch(url)
    if not data or data.get("status") != "Ok":
        return []
    payload = data.get("payload") or {}
    return [p for p in (parse_post(i) for i in (payload.get("items") or [])) if p]


def get_post_comments(post_id: str, limit: int = 30) -> list[PulsePost]:
    """Комментарии к посту (нормализованы как посты: текст+ник+лайки)."""
    url = f"{SOCIAL_BASE}/post/{post_id}/comment?limit={int(limit)}&appName=invest&platform=web"
    data = _fetch(url)
    if not data or data.get("status") != "Ok":
        return []
    payload = data.get("payload") or {}
    return [p for p in (parse_post(i) for i in (payload.get("items") or [])) if p]


def get_user_posts(nickname: str, limit: int = 30) -> list[PulsePost]:
    """Посты пользователя по никнейму."""
    url = f"{SOCIAL_BASE}/profile/nickname/{nickname}/post?limit={int(limit)}&appName=invest&platform=web"
    data = _fetch(url)
    if not data or data.get("status") != "Ok":
        return []
    payload = data.get("payload") or {}
    return [p for p in (parse_post(i) for i in (payload.get("items") or [])) if p]


def digest(ticker: str, limit: int = 12) -> str:
    """Компактная выжимка по тикеру для отчётов/ленты (экономия токенов).

    Возвращает готовый текст: самые популярные посты, имена, лайки.
    """
    posts = get_ticker_posts(ticker, limit=limit)
    if not posts:
        return f"[Пульс] по {ticker}: постов нет"
    posts.sort(key=lambda p: p.likes, reverse=True)
    lines = [f"[Пульс] {ticker}: {len(posts)} постов, топ:"]
    for p in posts[:limit]:
        text = p.text.replace("\n", " ")[:140]
        lines.append(f"  [{p.inserted}] @{p.nickname} 👍{p.likes}💬{p.comments}: {text}")
    return "\n".join(lines)


def search_keywords(posts: list[PulsePost], *keywords: str) -> list[PulsePost]:
    """Отфильтровать посты по ключевым словам (регистронезависимо)."""
    keys = [k.lower() for k in keywords]
    return [p for p in posts if any(k in p.text.lower() for k in keys)]


def pulse_sentiment(ticker: str, limit: int = 20) -> dict:
    """Настроение толпы Пульса по тикеру: скоринг постов.

    Переиспользует market_intelligence.score_sentiment (рус+англ словарь).
    Возвращает: signal (bullish/bearish/neutral), score, % быков/медведей,
    топ постов по лайкам и контрарный сигнал, если толпа в панике.
    """
    from .market_intelligence import score_sentiment

    posts = get_ticker_posts(ticker, limit=limit)
    if not posts:
        return {
            "signal": "neutral", "score": 0.0, "sample_size": 0,
            "bullish_pct": 0, "bearish_pct": 0, "posts": [],
        }

    scored = []
    for p in posts:
        s = score_sentiment(p.text)
        scored.append({
            "score": s["score"],
            "text": p.text[:160],
            "nickname": p.nickname,
            "likes": p.likes,
        })

    n = len(scored)
    bullish = sum(1 for s in scored if s["score"] > 0.1)
    bearish = sum(1 for s in scored if s["score"] < -0.1)

    if bullish > bearish and bullish / n >= 0.5:
        signal = "bullish"
    elif bearish > bullish and bearish / n >= 0.5:
        signal = "bearish"
    else:
        signal = "neutral"

    result = {
        "signal": signal,
        "score": round(sum(s["score"] for s in scored) / n, 3),
        "sample_size": n,
        "bullish_pct": round(100 * bullish / n),
        "bearish_pct": round(100 * bearish / n),
        "posts": sorted(scored, key=lambda s: s["likes"], reverse=True)[:3],
    }
    if signal == "bearish" and bearish >= max(3, n // 2):
        result["contrarian"] = (
            "толпа в панике по " + ticker + " — контрарный сценарий: "
            "возможен отскок при развороте золота или пробое уровня"
        )
    return result
