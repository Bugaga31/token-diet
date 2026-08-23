"""Тесты browser_llm — фикс профиля (16.08: USB-диск медленный → ~/.cache)."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import token_diet.browser_llm as bl


class TestBrowserLLM:
    def test_start_chrome_profile_in_cache(self, monkeypatch):
        """УРОК 16.08: профиль НЕ на внешнем диске — Chrome не успевает подняться."""
        captured = {}

        class FakeProc:
            def kill(self):
                pass

        def fake_popen(args, **kw):
            captured["args"] = list(args)
            return FakeProc()

        monkeypatch.setattr(bl.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(bl, "find_chrome", lambda: "/fake/chrome")

        # прерываем ожидание CDP: urlopen бросает, цикл ждёт → но мы делаем
        # лимит попыток 1 через monkeypatch
        def fake_urlopen(*a, **kw):
            raise OSError("cdp wait")

        monkeypatch.setattr(bl.urllib.request, "urlopen", fake_urlopen)
        # сократим цикл ожидания до 1 попытки
        import builtins
        monkeypatch.setattr(builtins, "range", lambda n: range(1))
        try:
            bl.start_chrome()
        except RuntimeError:
            pass  # ожидаемо — CDP не поднялся, но аргументы уже перехвачены

        args = captured.get("args", [])
        joined = " ".join(args)
        # профиль в ~/.cache, а не в state/ (USB)
        assert "--user-data-dir" in joined
        assert ".cache" in joined
        assert "KINGSTON" not in joined

    def test_find_chrome_returns_path_or_empty(self):
        # не падает и возвращает строку
        assert isinstance(bl.find_chrome(), str)

    def test_cdp_session_ws_url_helper(self):
        # _get_page_ws должен уметь отдавать пустую строку при отсутствии CDP
        assert hasattr(bl, "_get_page_ws")
