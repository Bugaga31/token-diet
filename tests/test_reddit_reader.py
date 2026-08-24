"""Тесты reddit_reader: парсинг JSON, картинки, комментарии, дайджест.

Сеть не трогаем: polite_fetch подменяется через параметр fetcher.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.reddit_reader import (  # noqa: E402
    RedditPost,
    collect_images,
    digest,
    fetch_comments,
    fetch_subreddit,
)


def _listing(children):
    # Reddit отдаёт [пост-листинг, коммент-листинг]
    return json.dumps([
        {"data": {"children": children}}, {"data": {"children": []}},
    ]).encode()


def _post_child(**over):
    d = {
        "id": "abc1", "subreddit": "investing", "title": "Тестовый пост",
        "author": "alice", "score": 42, "num_comments": 3,
        "created_utc": 1756000000.0, "permalink": "/r/investing/abc1/",
        "selftext": "текст поста",
        "url": "https://i.redd.it/pic.jpg",
    }
    d.update(over)
    return {"kind": "t3", "data": d}


class _FakeReddit:
    """Фейковый транспорт: URL → тело ответа."""

    def __init__(self, routes: dict[str, bytes]):
        self.routes = routes
        self.requested: list[str] = []

    def __call__(self, req, timeout):
        url = req.full_url.split("?")[0]
        self.requested.append(req.full_url)
        for route, body in self.routes.items():
            if url.startswith(route):
                return 200, body
        return 404, b"not found"


class TestFetchSubreddit:
    def test_parses_posts_and_images(self):
        fake = _FakeReddit({
            "https://www.reddit.com/r/investing/new.json":
                _listing([_post_child(), _post_child(id="zz9", score=0)]),
        })
        posts = fetch_subreddit("investing", limit=5, fetcher=fake)
        assert len(posts) == 2
        p = posts[0]
        assert p.title == "Тестовый пост"
        assert p.score == 42 and p.author == "alice"
        assert any("i.redd.it/pic.jpg" in u for u in p.image_urls)

    def test_preview_images_collected(self):
        child = _post_child(url="https://example.com/article", **{
            "preview": {"images": [{"source": {"url": "https://preview.redd.it/x.jpg&amp;a=1"}}]},
        })
        fake = _FakeReddit({
            "https://www.reddit.com/r/i/hot.json": _listing([child]),
        })
        posts = fetch_subreddit("i", sort="hot", fetcher=fake)
        assert posts[0].image_urls == ["https://preview.redd.it/x.jpg&a=1"]

    def test_limit_passed_to_url(self):
        fake = _FakeReddit({"https://www.reddit.com/r/w/new.json": _listing([])})
        fetch_subreddit("w", limit=7, fetcher=fake)
        assert "limit=7" in fake.requested[0]


class TestComments:
    def _comments_payload(self):
        return json.dumps([
            {},
            {"data": {"children": [
                {"kind": "t1", "data": {"id": "c1", "author": "bob",
                                        "body": "первый", "score": 5}},
                {"kind": "t1", "data": {
                    "id": "c2", "author": "eve", "body": "второй", "score": 1,
                    "replies": {"data": {"children": [
                        {"kind": "t1", "data": {"id": "c3", "author": "tom",
                                                "body": "ответ", "score": 0}},
                    ]}}},
                },
            ]}},
        ]).encode()

    def test_root_and_nested_comments(self):
        fake = _FakeReddit({
            "https://www.reddit.com/comments/abc1.json": self._comments_payload(),
        })
        comments = fetch_comments("abc1", limit=10, fetcher=fake)
        assert [c.id for c in comments] == ["c1", "c2", "c3"]
        assert comments[0].body == "первый"

    def test_permalink_path_used(self):
        fake = _FakeReddit({
            "https://www.reddit.com/r/i/comments/abc1/title.json":
                self._comments_payload(),
        })
        comments = fetch_comments("https://www.reddit.com/r/i/comments/abc1/title",
                                  fetcher=fake)
        assert len(comments) == 3


class TestImages:
    def test_download_to_dir(self, tmp_path):
        payload = {"/img": b"\x89PNG-fake-bytes"}
        calls = []

        def img_fetcher(req, timeout):
            calls.append(req.full_url)
            for route, body in payload.items():
                if route in req.full_url:
                    return 200, body
            return 404, b""

        posts = [RedditPost(id="p1", subreddit="r", title="t",
                            image_urls=["https://i.redd.it/img/a.png"])]
        saved = collect_images(posts, tmp_path, fetcher=img_fetcher)
        assert len(saved) == 1
        assert saved[0].read_bytes() == b"\x89PNG-fake-bytes"

    def test_max_images_cap(self, tmp_path):
        def many(req, timeout):
            return 200, b"x"

        posts = [RedditPost(id="p1", subreddit="r", title="t",
                            image_urls=[f"https://i.redd.it/{i}.png" for i in range(20)])]
        saved = collect_images(posts, tmp_path, fetcher=many, max_images=4)
        assert len(saved) == 4


class TestDigest:
    def test_compact_render(self):
        text = digest([
            RedditPost("a", "wsb", "Гигантский ддос", score=100, num_comments=50),
            RedditPost("b", "stocks", "Тихий день"),
        ])
        assert "[reddit]" in text and "+100" in text and "50 комм." in text
