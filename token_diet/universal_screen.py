"""Universal Screen — see & control ANY screen (desktop + phone).

Reverse-engineered from the best open-source tools:
- scrcpy (Genymobile): ADB video streaming + input forwarding
- uiautomator2 (Xiaocong): Python-wrapped UIAutomator for Android
- screenpipe (louis030195): OCR pipeline for desktop screens
- MSS (BoboTiG): fastest cross-platform screenshot library
- xdotool: X11 input automation

Architecture (like scrcpy but simpler):
  Desktop: MSS screenshot → PIL/OCR → xdotool input
  Phone:   ADB screenshot → PIL/OCR → adb input + uiautomator
  Both:    unified API, auto-detect platform

Key insight (from scrcpy): don't stream video — capture on demand.
One screenshot = one actionable frame. No H.264 overhead.
"""

from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False


# ═══════════════════════════════════════════════════════════════════════════════
# Platform detection
# ═══════════════════════════════════════════════════════════════════════════════

def _which(cmd: str) -> str | None:
    """Find executable path, like /usr/bin/adb."""
    try:
        result = subprocess.run(
            ["which", cmd], capture_output=True, text=True, timeout=5,
        )
        path = result.stdout.strip()
        return path if path else None
    except Exception:
        return None


def _has_adb_device() -> bool:
    """Check if an Android device is connected via ADB."""
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().split("\n")
        # First line: "List of devices attached"
        # Subsequent lines: "<serial>\t<state>"
        for line in lines[1:]:
            if line.strip() and "\tdevice" in line:
                return True
        return False
    except Exception:
        return False


def detect_platform() -> str:
    """Auto-detect available screen platform.

    Priority: phone (adb) > desktop (X11) > desktop (Wayland) > none.
    """
    if _has_adb_device():
        return "android"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "none"


# ═══════════════════════════════════════════════════════════════════════════════
# Shared types
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class UINode:
    """A UI element found on screen (from accessibility tree or OCR)."""
    text: str = ""
    content_desc: str = ""
    class_name: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    clickable: bool = False
    index: int = 0

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)

    @property
    def label(self) -> str:
        return self.text or self.content_desc or self.class_name


@dataclass
class ScreenFrame:
    """A captured screen frame with metadata."""
    platform: str
    width: int
    height: int
    image_base64: str  # JPEG base64
    image_bytes: bytes  # raw bytes for OCR
    pil_image: Any = None  # PIL Image (if PIL available)
    ui_nodes: list[UINode] = field(default_factory=list)

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.image_bytes)


# ═══════════════════════════════════════════════════════════════════════════════
# Desktop screen (X11)
# ═══════════════════════════════════════════════════════════════════════════════

class DesktopScreen:
    """Control desktop screen via X11/xdotool.

    Reverse-engineered from:
    - screen_server.py (our own MCP server)
    - screenpipe (OCR pipeline)
    - xdotool (X11 automation standard)
    """

    def __init__(self, display: str | None = None):
        self.display = display or os.environ.get("DISPLAY", ":0")
        self._has_mss = HAS_MSS
        self._has_pil = HAS_PIL

    def capture(self, quality: int = 40, scale: float = 0.66) -> ScreenFrame:
        """Capture desktop screenshot.

        Args:
            quality: JPEG quality (20-80). Lower = smaller = fewer tokens.
            scale: downsample factor. 0.66 = 66% of original size.
        """
        if self._has_mss:
            return self._capture_mss(quality, scale)
        return self._capture_import(quality, scale)

    def _capture_mss(self, quality: int, scale: float) -> ScreenFrame:
        """Fast capture via MSS (C library)."""
        with mss.MSS(display=self.display) as sct:
            monitor = sct.monitors[0]
            img = sct.grab(monitor)
            pil = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

        w, h = pil.size
        if scale != 1.0:
            pil = pil.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
            w, h = pil.size

        if pil.mode != "RGB":
            pil = pil.convert("RGB")
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=True)
        img_bytes = buf.getvalue()
        b64 = base64.b64encode(img_bytes).decode()

        return ScreenFrame(
            platform="x11",
            width=w, height=h,
            image_base64=b64,
            image_bytes=img_bytes,
            pil_image=pil,
        )

    def _capture_import(self, quality: int, scale: float) -> ScreenFrame:
        """Fallback: ImageMagick import."""
        from .config import log_path
        path = str(log_path("desktop_screen.png"))
        env = {**os.environ, "DISPLAY": self.display}
        subprocess.run(
            ["import", "-window", "root", path],
            env=env, timeout=10, capture_output=True,
        )
        pil = Image.open(path)
        w, h = pil.size
        if scale != 1.0:
            pil = pil.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
            w, h = pil.size
        if pil.mode != "RGB":
            pil = pil.convert("RGB")
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=True)
        img_bytes = buf.getvalue()
        b64 = base64.b64encode(img_bytes).decode()
        return ScreenFrame(
            platform="x11",
            width=w, height=h,
            image_base64=b64,
            image_bytes=img_bytes,
            pil_image=pil,
        )

    def click(self, x: int, y: int, button: str = "left") -> None:
        """Click at pixel coordinates."""
        btn_map = {"left": "1", "middle": "2", "right": "3"}
        self._xdo("mousemove", str(x), str(y))
        self._xdo("click", btn_map.get(button, "1"))

    def type_text(self, text: str) -> None:
        """Type text via xdotool."""
        self._xdo("type", "--clearmodifiers", text)

    def key_press(self, keys: str) -> None:
        """Press key combination like 'ctrl+c', 'alt+Tab'."""
        for key in keys.split("+"):
            self._xdo("key", key.strip())

    def move(self, x: int, y: int) -> None:
        """Move mouse without clicking."""
        self._xdo("mousemove", str(x), str(y))

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Drag from (x1,y1) to (x2,y2)."""
        self._xdo("mousemove", str(x1), str(y1))
        self._xdo("mousedown", "1")
        self._xdo("mousemove", str(x2), str(y2))
        self._xdo("mouseup", "1")

    def click_text(self, text: str) -> bool:
        """Find text on screen via OCR and click it. Returns True if found."""
        frame = self.capture()
        positions = self.ocr_find(frame, text)
        if positions:
            x, y, _ = positions[0]
            self.click(x, y)
            return True
        return False

    def ocr_find(
        self, frame: ScreenFrame, needle: str,
    ) -> list[tuple[int, int, str]]:
        """Find text on screen via OCR. Returns list of (x, y, matched_text).

        Uses tesseract if available, falls back to PIL-only (no OCR).
        """
        results = []
        tesseract = _which("tesseract")
        if not tesseract:
            return results

        # Save to temp file for tesseract
        tmp_in = "/tmp/ocr_input.png"
        tmp_out = "/tmp/ocr_output"
        frame.save(tmp_in)

        try:
            subprocess.run(
                [tesseract, tmp_in, tmp_out, "--psm", "6"],
                timeout=15, capture_output=True,
            )

            # tesseract with --psm 6 gives word positions via tsv output
            # Try tsv for precise positions
            subprocess.run(
                [tesseract, tmp_in, tmp_out, "--psm", "6", "tsv"],
                timeout=15, capture_output=True,
            )
            if os.path.exists(tmp_out + ".tsv"):
                with open(tmp_out + ".tsv") as f:
                    for line in f:
                        if needle.lower() in line.lower():
                            parts = line.strip().split("\t")
                            if len(parts) >= 12:
                                try:
                                    x = int(parts[6])  # left
                                    y = int(parts[7])  # top
                                    w = int(parts[8])
                                    h = int(parts[9])
                                    txt = parts[11]
                                    results.append((x + w // 2, y + h // 2, txt))
                                except (ValueError, IndexError):
                                    pass
        except Exception:
            pass

        return results

    def _xdo(self, *args: str) -> None:
        env = {**os.environ, "DISPLAY": self.display}
        subprocess.run(["xdotool", *args], env=env, timeout=5, capture_output=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Phone screen (Android via ADB)
# ═══════════════════════════════════════════════════════════════════════════════

class PhoneScreen:
    """Control Android phone screen via ADB.

    Reverse-engineered from:
    - scrcpy (Genymobile): ADB-based screen streaming
    - uiautomator2 (Xiaocong): Python UIAutomator wrapper
    - Our tor_rotator_android.py: real-world ADB usage patterns

    Key insight: scrcpy works by:
    1. `adb push` a tiny Java server to /data/local/tmp/
    2. Server captures screen via MediaProjection API
    3. Server streams H.264 via socket
    4. Client decodes and displays

    Our approach (simpler, on-demand):
    1. `adb exec-out screencap -p` → PNG screenshot
    2. `adb shell uiautomator dump` → UI element tree
    3. `adb shell input tap/swipe/text` → control
    """

    def __init__(self, serial: str | None = None):
        self.serial = serial
        self._adb_prefix = ["adb"]
        if serial:
            self._adb_prefix += ["-s", serial]

    def _adb(self, *args: str, timeout: int = 15) -> str:
        """Run ADB command, return stdout."""
        cmd = self._adb_prefix + list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0 and result.stderr:
                return f"ERROR: {result.stderr.strip()}"
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "ERROR: timeout"
        except Exception as e:
            return f"ERROR: {e}"

    def capture(self, quality: int = 50, scale: float = 0.5) -> ScreenFrame:
        """Capture phone screenshot via ADB.

        Uses `adb exec-out screencap -p` which outputs PNG to stdout.
        Then converts to JPEG for token efficiency.
        """
        # Get raw PNG from phone
        try:
            result = subprocess.run(
                self._adb_prefix + ["exec-out", "screencap", "-p"],
                capture_output=True, timeout=10,
            )
            png_bytes = result.stdout
        except Exception:
            # Fallback: save to sdcard then pull
            self._adb("shell", "screencap", "-p", "/sdcard/screen.png")
            try:
                result = subprocess.run(
                    self._adb_prefix + ["exec-out", "cat", "/sdcard/screen.png"],
                    capture_output=True, timeout=10,
                )
                png_bytes = result.stdout
            except Exception:
                return ScreenFrame(
                    platform="android",
                    width=0, height=0,
                    image_base64="", image_bytes=b"",
                )

        if not png_bytes:
            return ScreenFrame(
                platform="android", width=0, height=0,
                image_base64="", image_bytes=b"",
            )

        # Convert PNG → JPEG (much smaller for AI consumption)
        if HAS_PIL:
            pil = Image.open(io.BytesIO(png_bytes))
            w, h = pil.size
            if scale != 1.0:
                pil = pil.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
                w, h = pil.size
            if pil.mode != "RGB":
                pil = pil.convert("RGB")
            buf = io.BytesIO()
            pil.save(buf, format="JPEG", quality=quality, optimize=True)
            img_bytes = buf.getvalue()
            b64 = base64.b64encode(img_bytes).decode()
            return ScreenFrame(
                platform="android",
                width=w, height=h,
                image_base64=b64,
                image_bytes=img_bytes,
                pil_image=pil,
            )
        else:
            # No PIL: return raw PNG base64 (larger but works)
            b64 = base64.b64encode(png_bytes).decode()
            return ScreenFrame(
                platform="android",
                width=0, height=0,
                image_base64=b64,
                image_bytes=png_bytes,
            )

    def get_ui_tree(self) -> list[UINode]:
        """Get UI element tree via uiautomator.

        Returns list of UINode objects parsed from XML dump.
        """
        nodes = []
        try:
            # Dump UI hierarchy to /sdcard
            self._adb("shell", "uiautomator", "dump", "/sdcard/ui_tree.xml")

            # Pull the XML
            result = subprocess.run(
                self._adb_prefix + ["shell", "cat", "/sdcard/ui_tree.xml"],
                capture_output=True, text=True, timeout=10,
            )
            xml_text = result.stdout

            if not xml_text or "ERROR" in xml_text:
                return nodes

            # Parse XML
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)

            for i, node in enumerate(root.iter()):
                bounds_str = node.get("bounds", "")
                bounds = self._parse_bounds(bounds_str)
                nodes.append(UINode(
                    text=node.get("text", "") or "",
                    content_desc=node.get("content-desc", "") or "",
                    class_name=node.get("class", "") or "",
                    bounds=bounds,
                    clickable=node.get("clickable", "") == "true",
                    index=i,
                ))
        except Exception:
            pass

        return nodes

    def find_element(
        self, text: str = "", content_desc: str = "", class_name: str = "",
    ) -> list[UINode]:
        """Find UI elements matching criteria."""
        all_nodes = self.get_ui_tree()
        results = []
        for node in all_nodes:
            if text and text.lower() not in node.text.lower():
                continue
            if content_desc and content_desc.lower() not in node.content_desc.lower():
                continue
            if class_name and class_name.lower() not in node.class_name.lower():
                continue
            results.append(node)
        return results

    def click_element(self, node: UINode) -> bool:
        """Click a UI element by its bounds."""
        x, y = node.center
        if x > 0 and y > 0:
            self.tap(x, y)
            return True
        return False

    def click_text(self, text: str) -> bool:
        """Find text on screen and click it. Returns True if clicked."""
        elements = self.find_element(text=text)
        if not elements:
            elements = self.find_element(content_desc=text)
        if elements:
            return self.click_element(elements[0])
        return False

    def tap(self, x: int, y: int) -> None:
        """Tap at coordinates."""
        self._adb("shell", "input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """Swipe from (x1,y1) to (x2,y2)."""
        self._adb("shell", "input", "swipe",
                   str(x1), str(y1), str(x2), str(y2), str(duration_ms))

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        """Long press at coordinates."""
        self._adb("shell", "input", "swipe",
                   str(x), str(y), str(x), str(y), str(duration_ms))

    def type_text(self, text: str) -> None:
        """Type text. Handles spaces and special characters."""
        # ADB input text doesn't handle spaces well on all devices
        # Strategy: replace spaces with %s (which adb converts to space)
        text_escaped = text.replace(" ", "%s")
        self._adb("shell", "input", "text", text_escaped)

    def key_press(self, keycode: str) -> None:
        """Press a key. Use Android keycodes: KEYCODE_HOME, KEYCODE_BACK, etc."""
        self._adb("shell", "input", "keyevent", keycode)

    def press_back(self) -> None:
        self.key_press("KEYCODE_BACK")

    def press_home(self) -> None:
        self.key_press("KEYCODE_HOME")

    def press_enter(self) -> None:
        self.key_press("KEYCODE_ENTER")

    def ocr_find(
        self, frame: ScreenFrame, needle: str,
    ) -> list[tuple[int, int, str]]:
        """OCR on phone screenshot. Uses tesseract locally on the PNG."""
        results = []
        tesseract = _which("tesseract")
        if not tesseract or not frame.image_bytes:
            return results

        tmp_in = "/tmp/phone_ocr.png"
        tmp_out = "/tmp/phone_ocr_out"
        with open(tmp_in, "wb") as f:
            f.write(frame.image_bytes)

        try:
            subprocess.run(
                [tesseract, tmp_in, tmp_out, "--psm", "6", "tsv"],
                timeout=15, capture_output=True,
            )
            tsv_path = tmp_out + ".tsv"
            if os.path.exists(tsv_path):
                with open(tsv_path) as f:
                    for line in f:
                        if needle.lower() in line.lower():
                            parts = line.strip().split("\t")
                            if len(parts) >= 12:
                                try:
                                    x = int(parts[6])
                                    y = int(parts[7])
                                    w = int(parts[8])
                                    h = int(parts[9])
                                    txt = parts[11]
                                    # Scale coordinates back up if we scaled the image
                                    results.append((x + w // 2, y + h // 2, txt))
                                except (ValueError, IndexError):
                                    pass
        except Exception:
            pass

        return results

    def _parse_bounds(self, bounds_str: str) -> tuple[int, int, int, int]:
        """Parse '[left,top][right,bottom]' → (left, top, right, bottom)."""
        try:
            parts = re.findall(r'\d+', bounds_str)
            if len(parts) == 4:
                return tuple(int(p) for p in parts)
        except Exception:
            pass
        return (0, 0, 0, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Unified API — auto-detects platform
# ═══════════════════════════════════════════════════════════════════════════════

class UniversalScreen:
    """See & control any screen — auto-detects desktop vs phone.

    Usage:
        screen = UniversalScreen()
        frame = screen.capture()             # auto-detect, grab screenshot
        screen.click_text("Settings")        # find and click
        screen.click(500, 300)               # click at coordinates
        screen.type_text("hello")            # type text
        elements = screen.find("Submit")     # find UI elements

    Reverse-engineered from: scrcpy + uiautomator2 + screenpipe + screen_server.py
    """

    def __init__(self, platform: str = "auto"):
        self.platform = platform if platform != "auto" else detect_platform()
        self._desktop: DesktopScreen | None = None
        self._phone: PhoneScreen | None = None

        if self.platform in ("x11", "wayland"):
            self._desktop = DesktopScreen()
        elif self.platform == "android":
            self._phone = PhoneScreen()

    @property
    def is_available(self) -> bool:
        return self._desktop is not None or self._phone is not None

    def capture(self, quality: int = 40, scale: float = 0.66) -> ScreenFrame:
        """Capture screen. Auto-detects platform."""
        if self._phone:
            return self._phone.capture(quality=quality, scale=scale)
        if self._desktop:
            return self._desktop.capture(quality=quality, scale=scale)
        return ScreenFrame(
            platform="none", width=0, height=0,
            image_base64="", image_bytes=b"",
        )

    def click(self, x: int, y: int, button: str = "left") -> None:
        """Click at coordinates."""
        if self._phone:
            self._phone.tap(x, y)
        elif self._desktop:
            self._desktop.click(x, y, button)

    def click_text(self, text: str) -> bool:
        """Find text on screen and click it."""
        if self._phone:
            return self._phone.click_text(text)
        if self._desktop:
            return self._desktop.click_text(text)
        return False

    def type_text(self, text: str) -> None:
        """Type text."""
        if self._phone:
            self._phone.type_text(text)
        elif self._desktop:
            self._desktop.type_text(text)

    def key_press(self, keys: str) -> None:
        """Press key combination."""
        if self._phone:
            self._phone.key_press(keys)
        elif self._desktop:
            self._desktop.key_press(keys)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """Swipe (phone only)."""
        if self._phone:
            self._phone.swipe(x1, y1, x2, y2, duration_ms)

    def find(
        self, text: str = "", content_desc: str = "", class_name: str = "",
    ) -> list[UINode]:
        """Find UI elements (phone only, uses accessibility tree)."""
        if self._phone:
            return self._phone.find_element(
                text=text, content_desc=content_desc, class_name=class_name,
            )
        # Desktop: try OCR-based search
        if self._desktop and text:
            frame = self._desktop.capture()
            positions = self._desktop.ocr_find(frame, text)
            return [
                UINode(text=matched, bounds=(x-10, y-10, x+10, y+10))
                for x, y, matched in positions
            ]
        return []

    def ocr_text(self, quality: int = 50) -> str:
        """Extract ALL text from screen via OCR."""
        frame = self.capture(quality=quality)
        if not frame.image_bytes:
            return ""

        tesseract = _which("tesseract")
        if not tesseract:
            return ""

        tmp_in = "/tmp/ocr_full.png"
        tmp_out = "/tmp/ocr_full_out"
        with open(tmp_in, "wb") as f:
            f.write(frame.image_bytes)

        try:
            subprocess.run(
                [tesseract, tmp_in, tmp_out, "--psm", "3"],
                timeout=20, capture_output=True,
            )
            out_path = tmp_out + ".txt"
            if os.path.exists(out_path):
                with open(out_path) as f:
                    return f.read().strip()
        except Exception:
            pass

        return ""

    def grid_click(self, col: str, row: int, cols: int = 16, rows: int = 9) -> None:
        """Click a grid cell like spreadsheet (A1, B3, etc.).
        
        col: 'A'-'P', row: 1-9. Screen divided into cols×rows grid.
        """
        # Get screen size
        frame = self.capture()
        w, h = frame.width, frame.height
        if w == 0 or h == 0:
            return

        cw = w // cols
        ch = h // rows
        col_idx = ord(col.upper()) - 65
        row_idx = row - 1
        x = col_idx * cw + cw // 2
        y = row_idx * ch + ch // 2
        self.click(x, y)

    def click_and_see(self, x: int, y: int) -> ScreenFrame:
        """Click and return fresh screenshot in one call."""
        self.click(x, y)
        time.sleep(0.3)
        return self.capture()


# ═══════════════════════════════════════════════════════════════════════════════
# One-liners
# ═══════════════════════════════════════════════════════════════════════════════

def screenshot(quality: int = 40) -> ScreenFrame:
    """One-liner: capture screen (auto-detect platform)."""
    return UniversalScreen().capture(quality=quality)


def click_text(text: str) -> bool:
    """One-liner: find text on screen and click it."""
    return UniversalScreen().click_text(text)


def see_and_click(text: str) -> ScreenFrame | None:
    """Find text, click it, return new screenshot."""
    screen = UniversalScreen()
    if screen.click_text(text):
        time.sleep(0.3)
        return screen.capture()
    return None
