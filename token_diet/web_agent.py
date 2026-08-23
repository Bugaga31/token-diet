"""Web Agent — настоящий браузер для захода на сайты и чтения информации.

Использует playwright + chromium (скачан вручную, запускается через
executable_path — в обход registry-проверки, которая ломалась из-за
рваной сети и DPI).

Возможности:
  1. visit(url) — открыть страницу, дождаться JS, вытащить текст
  2. extract_links(url) — собрать ссылки со страницы
  3. save=True — сохранить находку в Obsidian (память на будущее)

Честное поведение: если браузер недоступен — возвращает
status="no_browser" с понятной причиной, а не фейковые данные.
"""

from __future__ import annotations

import logging
from typing import Any

# Тихий запуск: не спамим в консоль предупреждениями драйвера
logging.getLogger("playwright").setLevel(logging.CRITICAL)

_CHROME_PATHS = [
    # наш вручную скачанный Chrome for Testing (рабочий вариант)
    "/home/ro/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
    # системные fallback
    "/opt/google/chrome/chrome",
    "/usr/lib/chromium/chromium",
]


def _find_chrome() -> str | None:
    import os

    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _extract_text(page: Any) -> str:
    """Вытащить читаемый текст страницы (без скриптов и стилей)."""
    try:
        return page.inner_text("body") or ""
    except Exception:
        try:
            return page.evaluate("document.body ? document.body.innerText : ''")
        except Exception:
            return ""


def visit(url: str, timeout_ms: int = 30000, wait_sec: float = 2.0) -> dict:
    """Открыть сайт в настоящем браузере и вернуть содержимое.

    Returns {"status", "title", "text", "url", "final_url", "chrome"}.
    status: "ok" | "no_browser" | "error"
    """
    chrome = _find_chrome()
    if chrome is None:
        return {"status": "no_browser", "error": "chromium не найден"}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # playwright живёт в .venv проекта — подгружаем его оттуда,
        # чтобы браузер работал из любого интерпретатора
        import os
        import sys

        for cand in (
            "/tmp/token-diet-clone/.venv/lib/python3.12/site-packages",
            os.path.expanduser("~/.venv/lib/python3.12/site-packages"),
        ):
            if os.path.isdir(cand) and cand not in sys.path:
                sys.path.insert(0, cand)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {"status": "no_browser", "error": "playwright не установлен"}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=chrome,
                args=["--no-sandbox", "--disable-gpu"],
            )
            try:
                page = browser.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                if wait_sec:
                    page.wait_for_timeout(int(wait_sec * 1000))
                text = _extract_text(page)[:20000]
                return {
                    "status": "ok",
                    "title": page.title(),
                    "text": text,
                    "url": url,
                    "final_url": page.url,
                    "chrome": chrome,
                }
            finally:
                browser.close()
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200], "url": url}


def extract_links(url: str, timeout_ms: int = 30000) -> dict:
    """Собрать ссылки со страницы (href + текст якоря)."""
    chrome = _find_chrome()
    if chrome is None:
        return {"status": "no_browser", "error": "chromium не найден"}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "no_browser", "error": "playwright не установлен"}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, executable_path=chrome,
                args=["--no-sandbox", "--disable-gpu"])
            try:
                page = browser.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                links = page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => ({href: e.href, text: (e.innerText||'').trim().slice(0,120)}))")
                return {"status": "ok", "links": links[:200], "count": len(links)}
            finally:
                browser.close()
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200]}


def visit_and_save(url: str, tag: str = "", **kw) -> dict:
    """Открыть сайт, сохранить содержимое в Obsidian (память)."""
    res = visit(url, **kw)
    if res.get("status") != "ok":
        return res
    try:
        from .memory_cli import vault_path
        from .obsidian_vault import ObsidianVault

        vault = ObsidianVault(vault_path())
        title = res.get("title") or url
        body = f"URL: {url}\nИтоговый: {res.get('final_url')}\n\n{res.get('text', '')}"
        vault.write(f"Web: {tag or title}", body)
        res["saved"] = True
    except Exception:
        res["saved"] = False
    return res


__all__ = ["visit", "extract_links", "visit_and_save"]
