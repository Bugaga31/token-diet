"""Session Bridge — перенос сессий из Firefox (где генерал залогинен) в headless Chrome.

ПРОБЛЕМА: Gemini/ChatGPT/Claude в браузере требуют логин. Генерал залогинен
в обычном Firefox, а headless Chrome — чистый, без сессии.

РЕШЕНИЕ (16.08.2026, реверс живого Firefox-профиля):
1. Читаем cookies.sqlite работающего Firefox (копия, чтобы не мешать WAL).
2. Конвертируем куки в CDP Network.setCookie.
3. Открываем сайт, подкладываем куки, перезагружаем — сессия подхватывается.

Всё локально, куки не покидают машину. Модуль не хранит значения куки в коде.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

try:
    from .browser_llm import CDPSession, _get_page_ws, start_chrome
except ImportError:  # standalone use
    from browser_llm import CDPSession, _get_page_ws, start_chrome  # type: ignore[no-redef]


# Из каких доменов тащим куки (в порядке важности)
SITE_DOMAINS = {
    "gemini": ("https://gemini.google.com/app",
               [".gemini.google.com", ".google.com", "accounts.google.com",
                ".google.", ".yandex."]),
    "deepseek": ("https://chat.deepseek.com/",
                 [".deepseek.com", "chat.deepseek.com"]),
    "claude": ("https://claude.ai/new",
               [".claude.ai", "claude.ai"]),
    "chatgpt": ("https://chatgpt.com/",
                [".openai.com", "chatgpt.com", "auth.openai.com"]),
}

FIREFOX_PROFILES = [
    Path.home() / ".mozilla/firefox",
]


def find_firefox_cookies_db() -> Path | None:
    """Ищет cookies.sqlite среди Firefox-профилей (самый свежий)."""
    best: Path | None = None
    best_mtime = 0.0
    root = FIREFOX_PROFILES[0]
    if not root.exists():
        return None
    for profile in root.iterdir():
        if not profile.is_dir():
            continue
        db = profile / "cookies.sqlite"
        if db.exists() and db.stat().st_mtime > best_mtime:
            best = db
            best_mtime = db.stat().st_mtime
    return best


def read_cookies(db_path: Path, host_filters: list[str]) -> list[dict]:
    """Читает куки из SQLite-копии. Возвращает список dict для CDP."""
    # копируем, чтобы WAL работающего Firefox не мешал
    tmp = Path(tempfile.mkdtemp(prefix="td-cookies-"))
    shutil.copy2(db_path, tmp / "cookies.sqlite")
    wal = db_path.parent / "cookies.sqlite-wal"
    if wal.exists():
        try:
            shutil.copy2(wal, tmp / "cookies.sqlite-wal")
        except OSError:
            pass

    con = sqlite3.connect(tmp / "cookies.sqlite")
    try:
        con.execute("PRAGMA journal_mode=WAL")
        try:
            con.execute("VACUUM")  # сольёт WAL в основной файл
        except sqlite3.OperationalError:
            pass
    except sqlite3.OperationalError:
        pass

    rows = con.execute(
        "SELECT host, name, value, path, expiry, isSecure, sameSite "
        "FROM moz_cookies"
    ).fetchall()
    con.close()

    out = []
    for host, name, value, path, expiry, is_secure, same_site in rows:
        host_l = host.lstrip(".").lower()
        if not any(f.lstrip(".").lower() in host_l or host_l in f.lstrip(".").lower()
                   for f in host_filters):
            continue
        if value is None:
            value = ""
        # sameSite: 0=None, 1=Lax, 2=Strict
        samesite = {0: "None", 1: "Lax", 2: "Strict"}.get(same_site, "Lax")
        out.append({
            "name": name,
            "value": value,
            "domain": host,
            "path": path or "/",
            "expires": float(expiry) if expiry and expiry > 0 else -1,
            "secure": bool(is_secure),
            "sameSite": samesite,
            "httpOnly": False,
        })
    return out


def inject_cookies(cdp: CDPSession, cookies: list[dict]) -> int:
    """Загружает куки в headless Chrome через Network.setCookie. Возвращает число."""
    ok = 0
    for c in cookies:
        try:
            r = cdp.cmd("Network.setCookie", c)
            if r.get("result", {}).get("success"):
                ok += 1
        except Exception:  # noqa: BLE001
            continue
    return ok


def open_with_session(site: str, question: str, wait: int = 30) -> dict:
    """Открыть нейронку в headless Chrome С сессией из Firefox, задать вопрос.

    site: gemini | deepseek | claude | chatgpt
    """
    site_key = site.lower()
    if site_key not in SITE_DOMAINS:
        return {"status": f"ERR: нет {site!r}. Есть: {', '.join(SITE_DOMAINS)}"}
    url, filters = SITE_DOMAINS[site_key]

    db = find_firefox_cookies_db()
    if not db:
        return {"status": "ERR: cookies.sqlite не найден (Firefox не используется?)"}
    cookies = read_cookies(db, filters)
    if not cookies:
        return {"status": "ERR: куки для этого сайта не найдены — генерал не залогинен?"}

    proc = start_chrome()
    try:
        time.sleep(2)
        cdp = CDPSession(_get_page_ws())
        cdp.cmd("Page.enable")
        cdp.cmd("Runtime.enable")
        cdp.cmd("Network.enable")
        # сначала открываем домен (чтобы установить куки на нужный origin)
        cdp.cmd("Page.navigate", {"url": url})
        time.sleep(6)
        n = inject_cookies(cdp, cookies)
        cdp.cmd("Page.reload", {"ignoreCache": True})
        time.sleep(12)

        # 1. принимаем куки-баннер, если есть
        cdp.eval("""
            const btns = [...document.querySelectorAll('button')];
            const accept = btns.find(b => /принять все|accept all|accepter tout/i.test((b.innerText||'')+(b.getAttribute('aria-label')||'')));
            if (accept) accept.click();
        """)
        time.sleep(1)

        # 2. вводим вопрос (contenteditable у Gemini / textarea у DeepSeek)
        q = json.dumps(question)
        cdp.eval(f"""
            const el = document.querySelector('[contenteditable="true"], textarea, [role="textbox"]');
            if (!el) {{ }}
            else if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {{
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ) || Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                );
                if (setter && setter.set) setter.set.call(el, {q});
                else el.value = {q};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }} else {{
                el.focus();
                el.textContent = {q};
                el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: {q}}}));
            }}
        """)
        time.sleep(1)

        # 3. отправка: Enter (и клик по кнопке отправки как запасной)
        cdp.eval("""
            const el = document.querySelector('[contenteditable="true"], textarea, [role="textbox"]');
            if (el) el.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', code:'Enter', keyCode:13, bubbles:true}}));
            const btns = [...document.querySelectorAll('button')];
            const send = btns.find(b => /отправить|send|запрос|envoyer/i.test((b.getAttribute('aria-label')||b.innerText||'')))
                || document.querySelector('button[type=submit]');
            if (send) send.click();
        """)

        # 4. поллинг ответа: ждём, пока в тексте появится что-то после вопроса
        body = ""
        steps = max(1, wait // 5)
        for _ in range(steps):
            time.sleep(5)
            try:
                body = cdp.eval("document.body.innerText")
            except Exception:  # noqa: BLE001
                continue
            if question[:20] in body and len(body) > len(question) + 80:
                break
        cdp.close()
        return {
            "url": url,
            "question": question,
            "cookies_injected": n,
            "answer": body[:3000],
            "status": "ok",
        }
    except Exception as e:  # noqa: BLE001
        return {"status": f"ERR: {str(e)[:150]}"}
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(prog="session-bridge", description="Нейронка с сессией Firefox")
    p.add_argument("question", help="вопрос")
    p.add_argument("--site", default="gemini", choices=list(SITE_DOMAINS))
    p.add_argument("--wait", type=int, default=30)
    args = p.parse_args()
    r = open_with_session(args.site, args.question, wait=args.wait)
    print(f"[{r['status']}] куки: {r.get('cookies_injected', 0)}")
    print((r.get("answer") or "")[:1500])
