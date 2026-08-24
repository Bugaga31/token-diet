"""Tests for universal_screen.py — cross-platform screen control."""

import os

# These tests work without actual screen hardware by testing the API surface
from token_diet.universal_screen import (
    DesktopScreen,
    PhoneScreen,
    ScreenFrame,
    UINode,
    UniversalScreen,
    _which,
    detect_platform,
)


class TestPlatformDetection:
    def test_detect_platform_returns_string(self):
        p = detect_platform()
        assert p in ("x11", "wayland", "android", "none")

    def test_which_finds_adb(self):
        adb_path = _which("adb")
        assert adb_path is not None
        assert "adb" in adb_path

    def test_which_missing(self):
        assert _which("nonexistent_binary_xyz") is None


class TestUINode:
    def test_center(self):
        node = UINode(bounds=(0, 0, 100, 200))
        assert node.center == (50, 100)

    def test_label_fallback(self):
        node = UINode(text="", content_desc="Submit button", class_name="Button")
        assert node.label == "Submit button"

    def test_label_text_first(self):
        node = UINode(text="OK", content_desc="Submit", class_name="Btn")
        assert node.label == "OK"


class TestScreenFrame:
    def test_create_frame(self):
        frame = ScreenFrame(
            platform="test",
            width=100, height=200,
            image_base64="base64data",
            image_bytes=b"raw_bytes",
        )
        assert frame.platform == "test"
        assert frame.width == 100
        assert frame.height == 200

    def test_save(self, tmp_path):
        frame = ScreenFrame(
            platform="test", width=10, height=10,
            image_base64="", image_bytes=b"\xff\xd8\xff\x00",
        )
        path = str(tmp_path / "test.jpg")
        frame.save(path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0


class TestDesktopScreen:
    def test_init(self):
        screen = DesktopScreen()
        assert screen.display is not None

    def test_init_custom_display(self):
        screen = DesktopScreen(display=":99")
        assert screen.display == ":99"


class TestPhoneScreen:
    def test_init(self):
        screen = PhoneScreen()
        assert screen.serial is None

    def test_init_with_serial(self):
        screen = PhoneScreen(serial="abc123")
        assert screen.serial == "abc123"

    def test_parse_bounds(self):
        screen = PhoneScreen()
        bounds = screen._parse_bounds("[0,100][200,300]")
        assert bounds == (0, 100, 200, 300)

    def test_parse_bounds_empty(self):
        assert PhoneScreen()._parse_bounds("") == (0, 0, 0, 0)


class TestUniversalScreen:
    def test_init_auto(self):
        screen = UniversalScreen()
        assert screen.platform in ("x11", "wayland", "android", "none")

    def test_init_explicit(self):
        screen = UniversalScreen(platform="x11")
        assert screen.platform == "x11"
        assert screen._desktop is not None

    def test_init_none(self):
        screen = UniversalScreen(platform="none")
        assert not screen.is_available

    def test_capture_no_display(self):
        """Capture should return empty frame when no display."""
        # In CI there's often no real display
        screen = UniversalScreen(platform="none")
        frame = screen.capture()
        assert frame.platform == "none"
        assert frame.width == 0

    def test_is_available_desktop(self):
        screen = UniversalScreen(platform="x11")
        # X11 availability depends on DISPLAY env
        # At minimum, the object should be properly initialized
        assert screen._desktop is not None


class TestOneLiners:
    def test_screenshot(self):
        from token_diet.universal_screen import screenshot
        # Should not crash even without real screen
        frame = screenshot(quality=20)
        assert isinstance(frame, ScreenFrame)

    def test_click_text_no_screen(self):
        from token_diet.universal_screen import click_text
        # Should return False when no screen available
        result = click_text("nonexistent")
        assert isinstance(result, bool)
