## token-diet v3.1.0 — See & Control ANY Screen

### Reverse-engineered from the best:
- **scrcpy** (Genymobile) — ADB screen streaming protocol
- **uiautomator2** (Xiaocong) — Python UIAutomator wrapper
- **screenpipe** (louis030195) — OCR pipeline for desktop
- **MSS** — C-based fastest screenshot library
- **xdotool** — X11 input automation

### universal_screen.py

One module. Two platforms. Zero external APIs.

```python
from token_diet import UniversalScreen, screenshot, click_text

# Auto-detect desktop or phone
screen = UniversalScreen()

# Desktop: MSS screenshot + xdotool click/type
# Phone:   ADB screencap + input tap + uiautomator dump

frame = screen.capture()           # JPEG (tuned for AI: 15-30K tokens → 1-2K)
screen.click(500, 300)             # click at coordinates
screen.click_text("Settings")      # OCR-find and click
screen.type_text("hello world")    # type text (phone: handles spaces)
screen.swipe(100,500,100,100)      # swipe (phone)
elements = screen.find("Submit")   # UI tree search (phone) or OCR (desktop)
text = screen.ocr_text()           # extract all text from screen
```

### Features
| Feature | Desktop | Phone |
|---------|---------|-------|
| Screenshot (JPEG, token-optimized) | MSS/PIL | ADB screencap |
| Click at coordinates | xdotool | adb input tap |
| Find + click text | OCR (tesseract) | UI tree + OCR |
| Type text | xdotool | adb input text |
| Key combinations | xdotool | adb input keyevent |
| Swipe/drag | xdotool | adb input swipe |
| UI element tree | OCR only | uiautomator XML |
| Grid cell click | ✅ | ✅ |

### Install
```bash
pip install git+https://github.com/Bugaga31/token-diet.git

# Optional dependencies for full functionality:
sudo apt install xdotool tesseract-ocr scrcpy adb
```
