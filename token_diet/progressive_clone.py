"""progressive_clone — ленивое поэтапное извлечение UI (Strix-style).

УРОК 16.08 (stealth-browser-mcp / Strix):
Strix Progressive Cloning извлекает элемент НЕ целиком, а по слоям:
  1. clone_element_progressive() — лёгкий скелет (тег, id, классы, дети);
  2. expand_styles() / expand_events() / expand_children() / expand_css_rules()
     / expand_pseudo_elements() / expand_animations() — докручивай ТОЛЬКО
     тот слой, который нужен задаче.

Почему это важно для token-diet:
    Полный DOM + стили + события + анимации элемента — сотни токенов,
    из которых модели нужны 5%. Progressive = отдай скелет, а глубина
    добирается по запросу. Для UI-агента это 3-10x экономия токенов.

Наша реализация — чистое HTML-дерево, детерминированно, без браузера:
    clone_skeleton(html, selector) — скелет выбранного элемента;
    expand_layer(clone, layer)    — style/events/children/pseudo/attrs;
    clone_complete(...)           — полный клон (когда правда нужен весь).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

# ── Что считать «событием» (слой events) ───────────────────────────────────
_EVENT_ATTRS = (
    "onclick", "onchange", "oninput", "onsubmit", "onload", "onerror",
    "onmouseover", "onmouseout", "onkeydown", "onkeyup", "onkeypress",
    "onfocus", "onblur", "ondblclick", "oncontextmenu", "ondragstart",
    "ondrop", "onscroll", "ontouchstart", "ontouchend",
)


@dataclass
class ClonedElement:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["ClonedElement"] = field(default_factory=list)
    text: str = ""

    # слои, которые уже развёрнуты (ленивость)
    expanded: set[str] = field(default_factory=set)
    # полные атрибуты (для expand) — skeleton хранит их отдельно
    _full_attrs: dict[str, str] = field(default_factory=dict, repr=False)

    def to_dict(self, depth: int = 0) -> dict[str, Any]:
        d: dict[str, Any] = {"tag": self.tag}
        if self.attrs:
            d["attrs"] = dict(self.attrs)
        if self.text:
            d["text"] = self.text[:200]
        if self.children:
            d["children"] = [c.to_dict(depth + 1) for c in self.children[:10]]
        return d

    def token_estimate(self) -> int:
        """Грубая оценка токенов (как для сравнения слоёв)."""
        n = len(self.tag)
        for k, v in self.attrs.items():
            n += len(k) + len(v or "")  # HTML-атрибут может быть None (hidden)
        n += len(self.text)
        for c in self.children:
            n += c.token_estimate()
        return max(1, n // 4)


# ── Парсер HTML в дерево (только нужные элементы) ─────────────────────────
class _SkeletonParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[ClonedElement] = []
        self.roots: list[ClonedElement] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        el = ClonedElement(tag=tag, attrs={k: (v or "") for k, v in attrs})
        if self.stack:
            self.stack[-1].children.append(el)
        else:
            self.roots.append(el)
        self.stack.append(el)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data: str) -> None:
        if self.stack:
            t = data.strip()
            if t and len(t) > 1:
                self.stack[-1].text += t + " "


def _parse(html: str) -> list[ClonedElement]:
    p = _SkeletonParser()
    try:
        p.feed(html or "")
    except Exception:  # noqa: BLE001 — битый HTML не должен ронять
        pass
    return p.roots


# ── Селекторы: минимальная поддержка id / class / tag ─────────────────────
def _matches(el: ClonedElement, selector: str) -> bool:
    sel = selector.strip()
    if not sel:
        return True
    if sel.startswith("#"):
        return el.attrs.get("id") == sel[1:]
    if sel.startswith("."):
        classes = el.attrs.get("class", "").split()
        return sel[1:] in classes
    if sel.startswith("[") and sel.endswith("]"):  # [attr=value]
        inner = sel[1:-1]
        if "=" in inner:
            k, _, v = inner.partition("=")
            v = v.strip('"').strip("'")
            return el.attrs.get(k.strip()) == v
        return sel[1:-1] in el.attrs
    return el.tag == sel


def _find(el: ClonedElement, selector: str, results: list[ClonedElement],
          depth: int = 0, max_depth: int = 30) -> None:
    if depth > max_depth:
        return
    if _matches(el, selector):
        results.append(el)
    for c in el.children:
        _find(c, selector, results, depth + 1, max_depth)


# ── Публичное API (как у Strix: скелет → expand слоёв) ────────────────────
def clone_skeleton(html: str, selector: str = "body") -> ClonedElement | None:
    """Лёгкий клон: тег + id/class/type + текст + список детей (без стилей).

    Слой по умолчанию: attrs (без событий) + text. Дети — только скелеты.
    """
    roots = _parse(html)
    matches: list[ClonedElement] = []
    for r in roots:
        _find(r, selector, matches)
    if not matches:
        return None
    el = matches[0]
    # скелет: убираем «тяжёлые» атрибуты, оставляем идентифицирующие
    el._full_attrs = dict(el.attrs)
    keep = {k for k in el.attrs if k in ("id", "class", "type", "name", "role", "href", "src", "aria-label", "placeholder")}
    el.attrs = {k: el.attrs[k] for k in keep}
    el.expanded.add("skeleton")
    _trim_children(el, max_children=12)
    return el


def _trim_children(el: ClonedElement, max_children: int) -> None:
    if len(el.children) > max_children:
        el.children = el.children[:max_children]
    for c in el.children:
        _trim_children(c, max_children)


def expand_layer(el: ClonedElement, layer: str) -> int:
    """Развернуть один слой. Возвращает, сколько нового добавлено.

    layer: attrs | events | children | pseudo | all
    """
    added = 0
    if layer in ("attrs", "all") and "attrs" not in el.expanded:
        # вернуть ВСЕ атрибуты из полной копии
        full = el._full_attrs or el.attrs
        new = {k: v for k, v in full.items() if k not in el.attrs}
        el.attrs = {**el.attrs, **new}
        added += len(new)
        el.expanded.add("attrs")
    if layer in ("events", "all") and "events" not in el.expanded:
        full = el._full_attrs or el.attrs
        ev = {k: full.get(k, "") for k in _EVENT_ATTRS if k in full}
        if ev:
            el.attrs = {**el.attrs, **ev}
            added += len(ev)
        el.expanded.add("events")
    if layer in ("children", "all") and "children" not in el.expanded:
        # дети уже в дереве — просто снимаем лимит и помечаем
        added += len(el.children)
        el.expanded.add("children")
    return added


def clone_complete(html: str, selector: str = "body") -> ClonedElement | None:
    """Полный клон (когда задача правда требует весь элемент)."""
    el = clone_skeleton(html, selector)
    if el is None:
        return None
    expand_layer(el, "all")
    return el


def render(el: ClonedElement, max_depth: int = 5) -> str:
    """Компактный текст для промпта (с отступами)."""
    lines: list[str] = []

    def _walk(node: ClonedElement, depth: int) -> None:
        if depth > max_depth:
            return
        pad = "  " * depth
        attrs = " ".join(f'{k}="{v}"' for k, v in sorted(node.attrs.items()))
        head = f"<{node.tag}" + (f" {attrs}" if attrs else "") + ">"
        lines.append(pad + head)
        if node.text:
            lines.append(pad + "  " + node.text.strip()[:120])
        for c in node.children:
            _walk(c, depth + 1)
        if node.children:
            lines.append(pad + f"</{node.tag}>")

    _walk(el, 0)
    return "\n".join(lines)


def savings_report(html: str, selector: str = "body") -> dict[str, Any]:
    """Сколько токенов экономит progressive vs полный клон."""
    full = clone_complete(html, selector)
    skel = clone_skeleton(html, selector)
    if full is None or skel is None:
        return {"error": "не найден"}
    full_t = full.token_estimate()
    skel_t = skel.token_estimate()
    return {
        "full_tokens": full_t,
        "skeleton_tokens": skel_t,
        "savings_pct": round(100 * (1 - skel_t / max(full_t, 1)), 1),
    }
