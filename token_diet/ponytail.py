"""ponytail — browser tab/session management for agents (Ponytail-style).

Reverse-engineered from Ponytail / browser-control MCP servers:
    A local MCP server + browser extension exposes tabs and sessions
    to the agent as tools: list tabs, close, group, dedupe, search
    history. The agent sees a COMPACT tab list instead of screenshots.

Our take — deterministic, pure stdlib:
    1. TabManager — registry of tabs (url, title, active, group,
       pinned), with dedupe (same URL), grouping and session
       save/load to JSON.
    2. compact_tabs() — a tiny "[Browser tabs]" block for the LLM:
       one line per tab, which is enough to choose where to act
       (10x fewer tokens than screenshots).

Why this matters for token-diet:
    An agent that "sees" 5 open tabs as a compact list spends ~80
    tokens instead of ~5500 tokens on 5 screenshots — and can still
    decide exactly which tab to switch to.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MAX_TITLE = 60


@dataclass
class Tab:
    id: int
    url: str
    title: str = ""
    active: bool = False
    pinned: bool = False
    group: str = "default"
    created_at: float = 0.0

    def domain(self) -> str:
        m = re.match(r"https?://([^/]+)", self.url or "")
        return m.group(1) if m else (self.url or "?")

    def to_line(self) -> str:
        marker = "*" if self.active else " "
        title = (self.title or self.domain())[:_MAX_TITLE]
        return f"[{self.id}] {marker}{title} ({self.domain()})"


class TabManager:
    """Registry of tabs with dedupe, grouping and session persistence."""

    def __init__(self, session_path: str | Path | None = None):
        self.tabs: list[Tab] = []
        self._next_id = 1
        self.session_path = Path(session_path) if session_path else None
        if self.session_path and self.session_path.exists():
            self._load()

    def open(self, url: str, title: str = "", active: bool = False,
             pinned: bool = False, group: str = "default") -> Tab:
        """Open a tab. Dedupes: same URL returns the existing tab."""
        for t in self.tabs:
            if t.url == url:
                t.active = active
                return t
        tab = Tab(id=self._next_id, url=url, title=title, active=active,
                  pinned=pinned, group=group, created_at=time.time())
        self._next_id += 1
        self.tabs.append(tab)
        if self.session_path:
            self._save()
        return tab

    def close(self, tab_id: int) -> bool:
        before = len(self.tabs)
        self.tabs = [t for t in self.tabs if t.id != tab_id]
        changed = len(self.tabs) != before
        if changed and self.session_path:
            self._save()
        return changed

    def activate(self, tab_id: int) -> bool:
        for t in self.tabs:
            t.active = (t.id == tab_id)
        if self.session_path:
            self._save()
        return True

    def dedupe(self) -> int:
        """Close duplicate URLs, keeping the first of each. Returns count."""
        seen: set[str] = set()
        kept: list[Tab] = []
        removed = 0
        for t in self.tabs:
            if t.url in seen:
                removed += 1
                continue
            seen.add(t.url)
            kept.append(t)
        self.tabs = kept
        if removed and self.session_path:
            self._save()
        return removed

    def group(self, tab_ids: list[int], group_name: str) -> int:
        n = 0
        ids = set(tab_ids)
        for t in self.tabs:
            if t.id in ids:
                t.group = group_name
                n += 1
        if n and self.session_path:
            self._save()
        return n

    def tabs_by_domain(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self.tabs:
            counts[t.domain()] = counts.get(t.domain(), 0) + 1
        return counts

    def compact_tabs(self, max_tabs: int = 15) -> str:
        """Compact '[Browser tabs]' context block for an LLM prompt."""
        if not self.tabs:
            return "[Browser tabs: none open]"
        lines = ["[Browser tabs]"]
        shown = 0
        for t in self.tabs:
            if shown >= max_tabs:
                lines.append(f"  ...and {len(self.tabs) - shown} more")
                break
            lines.append("  " + t.to_line())
            shown += 1
        lines.append("[/Tabs]")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        return {
            "tabs": len(self.tabs),
            "domains": len(self.tabs_by_domain()),
            "active": sum(1 for t in self.tabs if t.active),
            "pinned": sum(1 for t in self.tabs if t.pinned),
            "top_domains": sorted(self.tabs_by_domain().items(),
                                  key=lambda x: -x[1])[:5],
        }

    # ── persistence ──────────────────────────────────────────────────────

    def _save(self) -> None:
        if not self.session_path:
            return
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "next_id": self._next_id,
            "tabs": [
                {"id": t.id, "url": t.url, "title": t.title, "active": t.active,
                 "pinned": t.pinned, "group": t.group, "created_at": t.created_at}
                for t in self.tabs
            ],
        }
        self.session_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _load(self) -> None:
        try:
            data = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self._next_id = int(data.get("next_id", 1))
        self.tabs = [
            Tab(id=t["id"], url=t["url"], title=t.get("title", ""),
                active=t.get("active", False), pinned=t.get("pinned", False),
                group=t.get("group", "default"),
                created_at=t.get("created_at", 0.0))
            for t in data.get("tabs", [])
        ]


def estimate_tabs_savings(n_tabs: int, screenshot_tokens: int = 1100) -> dict[str, Any]:
    """Compact tab list vs screenshots, quantified."""
    list_tokens = 20 + n_tabs * 14   # rough: ~14 tokens per tab line
    shot_tokens = n_tabs * screenshot_tokens
    saved = shot_tokens - list_tokens
    return {
        "n_tabs": n_tabs,
        "compact_list_tokens": list_tokens,
        "screenshots_tokens": shot_tokens,
        "saved_tokens": saved,
        "savings_pct": round(100 * saved / max(1, shot_tokens), 1),
    }


__all__ = [
    "Tab",
    "TabManager",
    "estimate_tabs_savings",
]
