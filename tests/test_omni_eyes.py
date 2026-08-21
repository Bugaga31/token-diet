"""Тесты «глаз» (omni_eyes) — vision через OmniRoute."""

import base64
import io
import os
import tempfile

import pytest

from token_diet.omni_eyes import (
    VISION_MODELS,
    _cached_image_base64,
    _file_hash,
    _image_to_base64,
    _maybe_shrink,
    ask_vision,
    see_image,
    take_screenshot,
)


def _make_png(path: str, size: int = 1000) -> str:
    """Создать минимальный PNG-файл для тестов."""
    try:
        from PIL import Image
        img = Image.new("RGB", (64, 64), color=(73, 109, 137))
        img.save(path, "PNG")
        return path
    except ImportError:
        # fallback: 1x1 PNG вручную
        data = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        with open(path, "wb") as f:
            f.write(data)
        return path


def test_vision_models_configured():
    assert len(VISION_MODELS) >= 3
    assert VISION_MODELS[0][0] == "agentrouter/claude-opus-5"


def test_image_to_base64():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        _make_png(path)
        b64 = _image_to_base64(path)
        assert len(b64) > 20
        # декодируется обратно в PNG-сигнатуру
        raw = base64.b64decode(b64)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        os.remove(path)


def test_ask_vision_bad_image():
    """Без файла/скриншота — аккуратная ошибка, не исключение."""
    res = ask_vision("что на экране?", image_path="/nonexistent/x.png")
    assert res["ok"] is False
    assert res["error"]


def test_ask_vision_with_image_no_network():
    """Если роутер недоступен — аккуратная ошибка."""
    import token_diet.omni_eyes as oe
    old = oe.OMNI_BASE
    oe.OMNI_BASE = "http://localhost:1/v1"  # гарантированно мёртвый порт
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            _make_png(path)
            res = ask_vision("тест", image_path=path, timeout=3)
            assert res["ok"] is False
            assert res["error"]
        finally:
            os.remove(path)
    finally:
        oe.OMNI_BASE = old


def test_maybe_shrink_small_file_untouched():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        _make_png(path)
        result = _maybe_shrink(path)
        assert result == path  # маленький файл не трогаем
    finally:
        os.remove(path)


def test_see_image_missing_file():
    res = see_image("/nonexistent.png", "опиши")
    assert res.startswith("[глаза не видят") or "нет" in res.lower()


def test_file_hash_stable():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        _make_png(path)
        h1 = _file_hash(path)
        h2 = _file_hash(path)
        assert h1 and h1 == h2
        # хеш большого файла тоже работает (head+tail)
        with open(path, "ab") as f:
            f.write(b"x" * 300_000)
        assert _file_hash(path)
    finally:
        os.remove(path)


def test_cached_image_base64_mime_detection():
    """JPEG-файл → mime image/jpeg (ревью модели: экономия через JPEG)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        path = f.name
    try:
        with open(path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"0" * 100)
        b64, mime = _cached_image_base64(path)
        assert b64 and mime == "image/jpeg"
    finally:
        os.remove(path)


def test_cached_image_base64_cache_hit():
    """Тот же файл дважды — кэш отдаёт без повторного чтения (быстрее)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        _make_png(path)
        b1, m1 = _cached_image_base64(path)
        b2, m2 = _cached_image_base64(path)
        assert b1 == b2
        assert m1 == m2 == "image/png"
    finally:
        os.remove(path)


def test_cached_image_base64_missing():
    assert _cached_image_base64("/nonexistent.png") is None
