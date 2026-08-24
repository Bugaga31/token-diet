"""Tests for scrape_graph — ScrapeGraphAI-style schema extraction."""

from token_diet.scrape_graph import (
    ScrapeGraph,
    estimate_scrape_savings,
    extract_across_chunks,
    extract_json_ld,
    extract_meta_tags,
    extract_schema_fields,
    ld_flatten,
)

HTML = """
<html><head>
<title>iPhone 15 Pro — 89 990 ₽</title>
<meta property="og:title" content="iPhone 15 Pro">
<meta name="description" content="Флагманский смартфон Apple">
<script type="application/ld+json">
{"@type": "Product", "name": "iPhone 15 Pro", "price": "89990", "rating": {"ratingValue": "4.8", "bestRating": "5"}}
</script>
</head><body>
<h1>iPhone 15 Pro</h1>
<p>Цена: 89 990 ₽</p>
<p>Рейтинг: 4.8 из 5</p>
<p>Автор обзора: Иван Петров</p>
<p>Email: support@example.com</p>
</body></html>
"""

SCHEMA = {
    "price": "цена товара",
    "rating": "рейтинг",
    "author": "автор",
    "email": "email для связи",
    "date": "дата публикации",
}


def test_extract_json_ld():
    blocks = extract_json_ld(HTML)
    assert len(blocks) == 1
    assert blocks[0]["name"] == "iPhone 15 Pro"
    flat = ld_flatten(blocks[0])
    assert flat.get("price") == "89990"


def test_extract_meta_tags():
    meta = extract_meta_tags(HTML)
    assert meta.get("og:title") == "iPhone 15 Pro"
    assert meta.get("description") == "Флагманский смартфон Apple"


def test_schema_price_rating_email():
    from token_diet.clean_scraper import html_to_text

    text = html_to_text(HTML)
    facts = extract_schema_fields(text, SCHEMA)
    assert "89 990" in facts["price"]["value"]
    assert facts["price"]["found"] is True
    assert "4.8" in facts["rating"]["value"]
    assert "support@example.com" in facts["email"]["value"]


def test_author_label_value():
    text = "Автор обзора: Иван Петров\nEmail: support@example.com"
    facts = extract_schema_fields(text, {"author": "кто написал"})
    assert "Иван Петров" in facts["author"]["value"]


def test_chunked_merge_fills_fields():
    text = ("Цена: 500 ₽\n" * 5) + ("\n" * 50) + "Автор: Мария Иванова"
    facts = extract_across_chunks(text, {"price": "", "author": ""}, chunk_size=200)
    assert facts["price"]["found"] is True
    assert "Мария" in facts["author"]["value"]


def test_scrape_graph_run_on_text():
    g = ScrapeGraph()
    res = g.run(HTML, {"price": "цена"}, is_html=True)
    assert res.status == "ok"
    assert res.title == "iPhone 15 Pro — 89 990 ₽"
    assert res.json_ld
    assert res.facts["price"]["found"] is True


def test_scrape_graph_prompt_block():
    g = ScrapeGraph()
    res = g.run(HTML, {"price": "цена", "rating": "рейтинг"}, is_html=True)
    block = res.to_prompt_block()
    assert block.startswith("[Extracted data]")
    assert "price" in block


def test_estimate_savings():
    s = estimate_scrape_savings(40000, 400)
    assert s["savings_pct"] == 99.0


def test_json_ld_malformed_ignored():
    bad = '<script type="application/ld+json">{not json</script>'
    assert extract_json_ld(bad) == []
