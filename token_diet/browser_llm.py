"""BrowserLLM — общение с нейронками через браузер (CDP), без API-ключей.

Идея генерала: «просто в браузере открывать и общаться с нейронками».
Зачем: многие модели (Gemini, DeepSeek, Claude) имеют веб-чаты, которые
дают бесплатные ответы без API-ключей. API может быть недоступен (DPI),
а веб-интерфейс — доступен.

Как работает:
1. Запускаем Chrome for Testing (headless) с remote-debugging-port.
2. Открываем вкладку с нужной нейронкой.
3. Пишем вопрос в поле ввода, жмём Enter.
4. Ждём и читаем ответ.
5. (опционально) сохраняем в Obsidian.

Всё через Chrome DevTools Protocol (CDP) — чистый Python, 0 зависимостей
кроме websocket-client (есть).

ВАЖНО: Chrome умирает вместе с командой-родителем, поэтому каждый вызов
должен запускать браузер, делать работу и завершаться в ОДНОМ процессе.
Метод run_once() именно так и работает.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request

CHROME_CANDIDATES = [
    os.path.expanduser("~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"),
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]
CDP_PORT = 9222
CDP_HOST = "127.0.0.1"


def find_chrome() -> str:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return shutil.which("chromium") or shutil.which("google-chrome") or ""


class CDPSession:
    """Тонкий клиент Chrome DevTools Protocol."""

    def __init__(self, ws_url: str):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self._id = 0

    def cmd(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        while True:
            resp = json.loads(self.ws.recv())
            if resp.get("id") == self._id:
                return resp

    def eval(self, expression: str) -> str:
        r = self.cmd("Runtime.evaluate",
                     {"expression": expression, "returnByValue": True, "awaitPromise": True})
        res = r.get("result", {}).get("result", {})
        if res.get("type") == "undefined":
            return ""
        return str(res.get("value", ""))

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def _get_page_ws() -> str:
    with urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5) as r:
        tabs = json.loads(r.read())
    for t in tabs:
        if t.get("type") == "page" and not t.get("url", "").startswith("chrome"):
            return t["webSocketDebuggerUrl"]
    return tabs[0]["webSocketDebuggerUrl"]


def start_chrome(profile_dir: str = "/tmp/cdp-profile") -> subprocess.Popen:
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Chrome не найден")
    proc = subprocess.Popen(
        [chrome, "--headless", "--no-sandbox", "--disable-gpu",
         "--disable-dev-shm-usage", f"--remote-debugging-port={CDP_PORT}",
         "--remote-allow-origins=*", f"--user-data-dir={profile_dir}",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # ждём CDP
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json/version", timeout=2) as r:
                return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("Chrome не поднял CDP")


def open_and_ask(url: str, question: str, wait_seconds: int = 20,
                 input_selector: str | None = None,
                 submit_selector: str | None = None,
                 answer_selector: str | None = None) -> dict:
    """Открыть нейронку, задать вопрос, прочитать ответ.

    selectors можно не давать — попытаемся угадать (textarea/input + Enter).
    Возвращает {url, question, answer, status}.
    """
    proc = start_chrome()
    try:
        time.sleep(2)
        cdp = CDPSession(_get_page_ws())
        cdp.cmd("Page.enable")
        cdp.cmd("Runtime.enable")
        cdp.cmd("Page.navigate", {"url": url})
        time.sleep(wait_seconds)

        # пробуем найти поле ввода
        if input_selector:
            cdp.eval(
                f'document.querySelector({json.dumps(input_selector)}).focus()')
        else:
            # угадываем: textarea или contenteditable
            cdp.eval("""
                const el = document.querySelector('textarea, [contenteditable="true"], input[type="text"]');
                if (el) el.focus();
            """)
        # вводим вопрос
        q = json.dumps(question)
        cdp.eval(f"""
            const el = document.querySelector('textarea, [contenteditable="true"], input[type="text"]');
            if (!el) {{ }} else if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {{
                el.value = {q};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
            }} else {{
                el.textContent = {q};
                el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: {q}}}));
            }}
        """)
        # жмём Enter
        cdp.eval("""
            const el = document.querySelector('textarea, [contenteditable="true"], input[type="text"]');
            if (el) {
                el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
            }
        """)
        # пробуем кликнуть кнопку отправки (по тексту или типу)
        cdp.eval("""
            const btns = [...document.querySelectorAll('button, [role=button]')];
            const send = btns.find(b => /запрос|send|ask|submit|отправить/i.test(b.innerText || '') && (b.innerText||'').length < 25)
                || btns.find(b => b.getAttribute('aria-label') && /send|ask|submit/i.test(b.getAttribute('aria-label')))
                || document.querySelector('button[type=submit]');
            if (send) send.click();
        """)
        # ждём ответ
        time.sleep(wait_seconds)
        if answer_selector:
            ans = cdp.eval(
                f"document.querySelector({json.dumps(answer_selector)})?.innerText || ''")
        else:
            ans = cdp.eval("document.body.innerText")
        cdp.close()
        return {"url": url, "question": question, "answer": ans[:4000],
                "status": "ok"}
    except Exception as e:  # noqa: BLE001
        return {"url": url, "question": question, "answer": "",
                "status": f"ERR: {str(e)[:120]}"}
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


NEURAL_SITES = {
    "gemini": "https://gemini.google.com/app",
    "deepseek": "https://chat.deepseek.com/",
    "claude": "https://claude.ai/new",
    "chatgpt": "https://chatgpt.com/",
    "grok": "https://grok.com/",
}


def ask_neural(name: str, question: str, wait: int = 25) -> dict:
    """Спросить нейронку по имени (gemini/deepseek/claude/chatgpt/grok)."""
    url = NEURAL_SITES.get(name.lower())
    if not url:
        return {"status": f"ERR: нет такой нейронки {name!r}. Есть: {', '.join(NEURAL_SITES)}"}
    return open_and_ask(url, question, wait_seconds=wait)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(prog="browser-llm", description="Спросить нейронку в браузере")
    p.add_argument("question", help="вопрос")
    p.add_argument("--site", default="deepseek", help="gemini/deepseek/claude/chatgpt/grok")
    p.add_argument("--wait", type=int, default=25)
    args = p.parse_args()
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    r = ask_neural(args.site, args.question, wait=args.wait)
    print(f"[{r['status']}] {r.get('answer', '')[:1500]}")
