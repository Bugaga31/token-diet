"""Тесты progressive_clone — ленивое поэтапное извлечение (Strix-style, 16.08)."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from token_diet.progressive_clone import (
    clone_complete, clone_skeleton, expand_layer, render, savings_report,
)

HTML = """
<html><body>
  <div id="card" class="product" data-id="42"
       style="color:red;background:#fff"
       onclick="buy(42)" onchange="track()">
    <h1 class="title">Сургут-п</h1>
    <p>Цена 41.50, стоп 39.5</p>
    <button id="buy-btn" type="button">Купить</button>
    <span hidden>скрытый текст</span>
  </div>
  <div id="other">Другое</div>
</body></html>
"""


class TestSkeleton:
    def test_find_by_id(self):
        el = clone_skeleton(HTML, "#card")
        assert el is not None
        assert el.tag == "div"
        assert el.attrs.get("id") == "card"

    def test_find_by_class(self):
        el = clone_skeleton(HTML, ".title")
        assert el is not None
        assert el.tag == "h1"

    def test_find_by_tag(self):
        el = clone_skeleton(HTML, "button")
        assert el is not None
        assert el.attrs.get("id") == "buy-btn"

    def test_not_found(self):
        assert clone_skeleton(HTML, "#nope") is None

    def test_skeleton_keeps_only_id_attrs(self):
        el = clone_skeleton(HTML, "#card")
        # style/onclick/onchange не входят в скелет
        assert "style" not in el.attrs
        assert "onclick" not in el.attrs
        assert el.attrs.get("id") == "card"
        assert el.attrs.get("class") == "product"

    def test_skeleton_has_text(self):
        el = clone_skeleton(HTML, "#card")
        assert "Сургут-п" in el.text or any("Сургут-п" in c.text for c in el.children)


class TestExpand:
    def test_expand_events_adds_handlers(self):
        el = clone_skeleton(HTML, "#card")
        before = len(el.attrs)
        added = expand_layer(el, "events")
        assert added >= 1
        assert "onclick" in el.attrs
        assert len(el.attrs) > before

    def test_expand_attrs_restores_all(self):
        el = clone_skeleton(HTML, "#card")
        expand_layer(el, "attrs")
        assert "style" in el.attrs
        assert "data-id" in el.attrs

    def test_expand_twice_no_duplicate(self):
        el = clone_skeleton(HTML, "#card")
        a1 = expand_layer(el, "events")
        a2 = expand_layer(el, "events")
        assert a2 == 0  # уже развёрнут

    def test_expand_all(self):
        el = clone_skeleton(HTML, "#card")
        added = expand_layer(el, "all")
        assert added > 0
        assert "events" in el.expanded


class TestRender:
    def test_render_outputs_tags(self):
        el = clone_skeleton(HTML, "#card")
        out = render(el)
        assert "<div" in out
        assert "card" in out

    def test_render_compact(self):
        el = clone_skeleton(HTML, "#card")
        out = render(el)
        assert len(out) < 800  # скелет компактный


class TestSavings:
    def test_savings_report(self):
        r = savings_report(HTML, "#card")
        assert "savings_pct" in r
        assert r["full_tokens"] >= r["skeleton_tokens"]

    def test_complete_clone_has_style(self):
        el = clone_complete(HTML, "#card")
        assert el is not None
        assert "style" in el.attrs
