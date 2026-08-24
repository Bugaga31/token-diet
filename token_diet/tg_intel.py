"""TG Intel — единый разведчик по Telegram.

Совмещает три вещи в одну команду:

  1. Глобальный поиск по ВСЕЙ платформе (SearchGlobalRequest) —
     включая каналы, на которые мы не подписаны и про которые
     даже не знаем.
  2. Поиск по подписанным диалогам (search_telegram).
  3. Дедуп + BM25-ранжирование по релевантности запросу.

И главное: найденное АВТОМАТИЧЕСКИ сохраняется в Обсидиан
(память на будущее) — чтобы следующая сессия уже знала,
что мы видели.

Всё детерминированное (merge/dedup/rank) вынесено в чистую
функцию merge_and_rank — её можно тестировать без сети.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

# ТГ через DPI/прокси шумит «Server closed» — не даём этому мусору
# засорять вывод разведчика.
logging.getLogger("telethon").setLevel(logging.CRITICAL)

from .telegram_search import _bm25_rerank, _dedupe_results, search_telegram


def _run_with_timeout(fn: Callable[[], Any], seconds: float) -> Any:
    """Запустить сетевой вызов с жёстким таймаутом.

    Telegram через DPI/прокси может зависать — не даём команде
    висеть бесконечно: что успело — то и отдаём.

    Важно: поток daemon — он не блокирует выход из процесса,
    если сеть так и не отпустила (в отличие от ThreadPoolExecutor,
    который ждёт завершения потока в with-блоке).
    """
    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["res"] = fn()
        except Exception as exc:  # сеть упала — считаем, что результата нет
            box["err"] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=seconds)
    if t.is_alive():
        return None  # не уложились в таймаут — поток умрёт с процессом
    if "err" in box:
        return None
    return box.get("res")


def _normalize(result: dict, source: str) -> dict:
    """Привести результат любого поиска к единому формату."""
    text = (result.get("text") or "").strip()
    channel = (result.get("channel") or result.get("dialog") or "?").strip()
    return {
        "channel": channel,
        "date": str(result.get("date", "")),
        "text": text[:400],
        "important": bool(result.get("important")),
        "source": source,
    }


def merge_and_rank(
    query: str,
    global_results: list[dict] | None,
    dialog_results: list[dict] | None,
    top_k: int = 12,
) -> list[dict]:
    """Чистая функция: объединить два потока, убрать дубли, отранжировать.

    Без сети. Возвращает список результатов с полями:
    channel / date / text / important / source / relevance.
    """
    merged: list[dict] = [_normalize(r, "global") for r in global_results or []]
    merged += [_normalize(r, "dialogs") for r in dialog_results or []]
    merged = _dedupe_results(merged)
    return _bm25_rerank(query, merged, top_k=top_k)


def _save_to_vault(query: str, results: list[dict]) -> bool:
    """Сохранить топ находок в Обсидиан (память на будущее)."""
    try:
        from .memory_cli import vault_path
        from .obsidian_vault import ObsidianVault

        vault = ObsidianVault(vault_path())
        lines = [f"Запрос: {query}", f"Найдено: {len(results)}", ""]
        for r in results[:15]:
            mark = "🔥" if r.get("important") else "·"
            lines.append(f"### {mark} [{r['channel']}] {r['date']} ({r['source']})")
            lines.append(r["text"])
            lines.append("")
        vault.write(f"TG-разведка: {query}", "\n".join(lines))
        return True
    except Exception:
        return False


def tg_intel(query: str, limit: int = 10, save: bool = True,
              retries: int = 2) -> dict:
    """Одна команда: поиск по всему Telegram + память в Obsidian.

    Глобальный и диалоговый поиски идут ПАРАЛЛЕЛЬНО (а не друг за
    другом — так быстрее в 2 раза на рваной сети), каждый с ретраями:
    сеть через DPI/прокси может молчать первым вызовом и ответить
    вторым. Таймауты щедрые (45с), чтобы медленный, но живой ответ
    не считался провалом.

    Returns {"status", "query", "found", "global_found", "dialogs_found",
             "results", "saved"}.
    Честно возвращает status="error"/"no_session", если сеть или
    сессия недоступны — без фейковых данных.
    """
    from .telegram_monitor import global_search

    def _glob_once() -> list[dict]:
        r = global_search(query, limit=limit)
        if isinstance(r, dict) and r.get("status") == "ok":
            return r.get("results", [])
        return []

    def _dial_once() -> list[dict]:
        r = search_telegram(query, limit_per_dialog=3,
                            max_dialogs=30, save=False)
        if isinstance(r, dict) and r.get("status") == "ok":
            return r.get("results", [])
        return []

    # ретраи: сеть через DPI капризничает, повторный вызов часто проходит
    def _with_retries(fn, seconds: float) -> list[dict]:
        for _attempt in range(retries + 1):
            res = _run_with_timeout(fn, seconds=seconds)
            if res:
                return res
        return []

    # 1+2) Параллельно: глобальный поиск + поиск по диалогам
    import threading

    box: dict[str, list[dict]] = {"glob": [], "dial": []}

    def _worker_glob() -> None:
        box["glob"] = _with_retries(_glob_once, seconds=45)

    def _worker_dial() -> None:
        box["dial"] = _with_retries(_dial_once, seconds=45)

    t1 = threading.Thread(target=_worker_glob, daemon=True)
    t2 = threading.Thread(target=_worker_dial, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=95)
    t2.join(timeout=95)

    glob_results = box["glob"]
    dial_results = box["dial"]

    if not glob_results and not dial_results:
        return {
            "status": "error",
            "error": "поиск не дал результатов (сеть Telegram нестабильна "
                     "или нет живой сессии)",
            "query": query,
        }

    ranked = merge_and_rank(query, glob_results, dial_results, top_k=limit)
    saved = _save_to_vault(query, ranked) if (save and ranked) else False

    return {
        "status": "ok",
        "query": query,
        "found": len(ranked),
        "global_found": len(glob_results),
        "dialogs_found": len(dial_results),
        "results": ranked,
        "saved": saved,
    }


__all__ = ["tg_intel", "merge_and_rank"]
