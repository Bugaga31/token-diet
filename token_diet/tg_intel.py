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
from typing import Any, Callable

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


def tg_intel(query: str, limit: int = 10, save: bool = True) -> dict:
    """Одна команда: поиск по всему Telegram + память в Obsidian.

    Returns {"status", "query", "found", "global_found", "dialogs_found",
             "results", "saved"}.
    Честно возвращает status="error"/"no_session", если сеть или
    сессия недоступны — без фейковых данных.
    """
    from .telegram_monitor import global_search

    # 1) Глобально — по ВСЕЙ платформе (неподписанные каналы в том числе),
    #    но с таймаутом: сеть через DPI может молчать, не вешаемся на ней
    glob_results: list[dict] = []
    glob_result = _run_with_timeout(
        lambda: global_search(query, limit=limit), seconds=30)
    if isinstance(glob_result, dict) and glob_result.get("status") == "ok":
        glob_results = glob_result.get("results", [])

    # 2) По подписанным диалогам — тоже с таймаутом
    dial_results: list[dict] = []
    dial = _run_with_timeout(
        lambda: search_telegram(query, limit_per_dialog=3,
                                max_dialogs=30, save=False),
        seconds=30)
    if isinstance(dial, dict) and dial.get("status") == "ok":
        dial_results = dial.get("results", [])

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
