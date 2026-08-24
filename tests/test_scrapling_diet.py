"""Тесты scrapling_diet — реверс-инжиниринг Scrapling (без их зависимостей)."""



from token_diet.scrapling_diet import (
    AutoThrottle,
    DiskResponseCache,
    LinkExtractor,
    ProxyRotator,
    adaptive_select,
    find_by_text,
    network_capture,
)

PAGE = """
<html><body>
  <div class="product"><h2>Товар А</h2><span class="price">100</span></div>
  <div class="product"><h2>Товар Б</h2><span class="price">200</span></div>
  <div class="product-card"><h2>Товар В</h2><span class="price">300</span></div>
  <div class="news"><p>Срочно: рынок вырос</p></div>
  <nav><a href="/catalog">Каталог</a></nav>
  <a href="https://example.com/page1.html">1</a>
  <a href="https://example.com/page2.html">2</a>
  <a href="https://example.com/report.pdf">pdf</a>
  <a href="javascript:void(0)">js</a>
  <a href="https://other.com/x.html">other</a>
</body></html>
"""


def test_adaptive_select_exact():
    found = adaptive_select(PAGE, ".product")
    assert len(found) >= 2
    assert any("Товар А" in el["text"] for el in found)


def test_adaptive_select_relocation():
    """Селектор .product не находит .product-card — adaptive должен найти."""
    found = adaptive_select(PAGE, ".product-card")
    assert found, "точный селектор обязан сработать"
    # а вот релокация: ищем .item (не существует) → похожие классы product
    relocated = adaptive_select(PAGE, ".product-item")
    assert relocated, "adaptive relocation не нашёл похожие элементы"


def test_find_by_text():
    found = find_by_text(PAGE, "рынок вырос")
    assert found and "Срочно" in found[0]["text"]


def test_auto_throttle():
    at = AutoThrottle(base_delay=0.1)
    assert not at.detect_blocked(200, "ok")
    assert at.detect_blocked(403, "")
    assert at.detect_blocked(200, "Just a moment... Cloudflare")
    at.report(200, "ok")
    assert at._current < 0.2
    at.report(429, "rate limit", retry_after=5)
    assert at._current >= 5
    assert at.stats["blocks"] == 1


def test_disk_cache(tmp_path):
    dc = DiskResponseCache(str(tmp_path), ttl_seconds=3600)
    assert dc.get("https://x.test/a") is None
    dc.set("https://x.test/a", "<html>hello</html>")
    assert dc.get("https://x.test/a") == "<html>hello</html>"
    assert dc.stats["hits"] == 1
    assert dc.size_bytes() > 0


def test_proxy_rotator():
    pr = ProxyRotator(["p1", "p2", "p3"], skip_seconds=60)
    got = {pr.next() for _ in range(6)}
    assert got == {"p1", "p2", "p3"}
    pr.mark_bad("p1")
    assert pr.next() != "p1"


def test_link_extractor():
    le = LinkExtractor(
        base_url="https://example.com/",
        allowed_domains=("example.com",),
        deny=("secret",),
    )
    links = le.extract(PAGE)
    assert "https://example.com/page1.html" in links
    assert "https://example.com/page2.html" in links
    assert "https://other.com/x.html" not in links
    assert not any("report.pdf" in ln for ln in links)
    assert not any("javascript:" in ln for ln in links)


def test_network_capture_bad_ws():
    """Невалидный WS — не падаем, а возвращаем пусто."""
    caps = network_capture("ws://127.0.0.1:1/devtools/page/0", timeout=1.0)
    assert caps == []