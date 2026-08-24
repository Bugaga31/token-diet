"""Reddit Reader — чтение постов, комментариев и картинок без API-ключей.

Reddit отдаёт публичные данные как JSON: ``reddit.com/r/<sub>/new.json``.
Модуль даёт агенту глаза-и-уши на Reddit:

1. ``fetch_subreddit`` — свежие посты (title/selftext/score/created).
2. ``fetch_comments`` — ветка комментариев к посту.
3. ``collect_images`` — прямые ссылки на изображения из постов
   (i.redd.it, i.imgur.com, превью) и их скачивание в локальную папку.

Вежливость: общий RateLimiter из network_control, честный User-Agent,
лимит запросов на домен. Только чтение — ничего не постим и не голосуем.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .network_control import FetchError, polite_fetch

_JSON_SUFFIX = ".json?limit={limit}"


@dataclass
class RedditPost:
    """Пост в удобном виде."""

    id: str
    subreddit: str
    title: str
    selftext: str = ""
    author: str = ""
    score: int = 0
    num_comments: int = 0
    created_utc: float = 0.0
    url: str = ""                  # ссылка поста
    image_urls: list[str] = field(default_factory=list)

    @property
    def age_hours(self) -> float:
        return max(0.0, (time.time() - self.created_utc) / 3600) if self.created_utc else 0.0


@dataclass
class RedditComment:
    """Комментарий (плоский список корней + ответов)."""

    id: str
    author: str
    body: str
    score: int = 0


_IMAGE_HOSTS = ("i.redd.it", "i.imgur.com", "preview.redd.it")


def _parse_post(child: dict) -> RedditPost:
    d = child.get("data", {})
    images: list[str] = []
    if any(h in (d.get("url") or "") for h in _IMAGE_HOSTS):
        images.append(d["url"])
    preview = (d.get("preview") or {}).get("images") or []
    for img in preview:
        src = ((img.get("source") or {}).get("url")) or ""
        if src and src not in images:
            images.append(src.replace("&amp;", "&"))
    gallery = (d.get("media_metadata") or {})
    for item in gallery.values():
        if item.get("status") == "valid":
            src = ((item.get("p") or [{}])[-1]).get("u", "")
            if src and src not in images:
                images.append(src.replace("&amp;", "&"))
    return RedditPost(
        id=d.get("id", ""),
        subreddit=d.get("subreddit", ""),
        title=d.get("title", ""),
        selftext=d.get("selftext", "") or "",
        author=str(d.get("author", "")),
        score=int(d.get("score", 0)),
        num_comments=int(d.get("num_comments", 0)),
        created_utc=float(d.get("created_utc", 0.0)),
        url="https://www.reddit.com" + (d.get("permalink") or ""),
        image_urls=images,
    )


def fetch_subreddit(
    subreddit: str,
    limit: int = 10,
    sort: str = "new",
    *,
    fetcher=None,
    limiter=None,
) -> list[RedditPost]:
    """Свежие посты сабреддита. sort: new | hot | top | rising."""
    url = f"https://www.reddit.com/r/{subreddit}/{sort}{_JSON_SUFFIX.format(limit=min(limit, 100))}"
    _, body = polite_fetch(url, fetcher=fetcher, limiter=limiter)
    data = json.loads(body.decode("utf-8", errors="replace"))
    children = ((data[0] or {}).get("data") or {}).get("children") or []
    return [_parse_post(c) for c in children]


def fetch_comments(
    permalink_or_id: str,
    limit: int = 20,
    *,
    fetcher=None,
    limiter=None,
) -> list[RedditComment]:
    """Корневые комментарии поста (по permalink или id)."""
    if permalink_or_id.startswith("http"):
        path = urlparse_path(permalink_or_id)
    else:
        path = f"/comments/{permalink_or_id}/"
    url = f"https://www.reddit.com{path.rstrip('/')}.json?limit={min(limit, 100)}"
    _, body = polite_fetch(url, fetcher=fetcher, limiter=limiter)
    data = json.loads(body.decode("utf-8", errors="replace"))
    out: list[RedditComment] = []

    def walk(node: dict, depth: int = 0) -> None:
        kind = node.get("kind")
        d = node.get("data") or {}
        if kind == "t1" and depth < 2:      # корневые + один уровень ответов
            out.append(RedditComment(
                id=d.get("id", ""), author=str(d.get("author", "")),
                body=(d.get("body") or "").strip(), score=int(d.get("score", 0)),
            ))
            depth += 1
        replies = d.get("replies")
        if isinstance(replies, dict):
            for child in (replies.get("data") or {}).get("children") or []:
                walk(child, depth)

    listing = data[1] if len(data) > 1 else {}
    for child in (listing.get("data") or {}).get("children") or []:
        walk(child)
    return out[:limit]


def urlparse_path(url: str) -> str:
    """Path части URL ('/r/sub/comments/...')."""
    from urllib.parse import urlparse as _up

    return _up(url).path.rstrip("/") + "/"


def collect_images(
    posts: list[RedditPost],
    dest_dir: str | Path,
    *,
    fetcher=None,
    limiter=None,
    max_images: int = 10,
) -> list[Path]:
    """Скачать картинки постов в dest_dir; вернуть пути сохранённых файлов."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    seen: set[str] = set()
    for post in posts:
        for img in post.image_urls:
            if len(saved) >= max_images or img in seen:
                continue
            seen.add(img)
            name = f"{post.id}_{len(saved)}_{img.rsplit('/', 1)[-1].split('?')[0]}"[:120]
            try:
                _, body = polite_fetch(img, fetcher=fetcher, limiter=limiter)
            except FetchError:
                continue
            path = dest / name
            path.write_bytes(body)
            saved.append(path)
    return saved


def digest(posts: list[RedditPost], width: int = 90) -> str:
    """Текстовая сводка постов для контекста агента (токен-бережно)."""
    lines = [f"[reddit] {len(posts)} постов:"]
    for p in posts:
        head = p.title[:width]
        extra = f" · {p.num_comments} комм." if p.num_comments else ""
        lines.append(f"[reddit] • {head} (+{p.score}{extra}) r/{p.subreddit}")
    return "\n".join(lines)
