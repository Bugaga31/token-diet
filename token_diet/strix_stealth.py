"""strix_stealth — anti-detection browser fingerprint profiles (Strix-style).

Reverse-engineered from Strix (stealth browser automation MCP):
    Real browsers are identified by far more than the User-Agent.
    Strix makes a driven browser look real by keeping a CONSISTENT
    fingerprint: UA + viewport + headers + hardware hints + locale +
    timezone all agree with each other. Inconsistency is what bots
    get caught on (e.g. Chrome UA but no WebGL, US locale but RU
    timezone).

Our take — deterministic, local, no browser needed:
    1. StrixProfile — a coherent, self-consistent fingerprint:
       UA, viewport, headers, hardware hints, locale, timezone.
    2. rotate_profiles() — pick a fresh coherent profile per session.
    3. audit_fingerprint() — the DEFENSIVE mirror: check a candidate
       fingerprint for the inconsistencies bots are caught on.

Pure stdlib.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

_DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)
_MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_VIEWPORTS = {
    "desktop": ((1920, 1080), (1536, 864), (1440, 900), (1366, 768)),
    "mobile": ((390, 844), (393, 852), (412, 915), (360, 800)),
}

_LOCALES = ("ru-RU", "en-US", "ru-RU", "en-GB")
_TIMEZONES = ("Europe/Moscow", "Europe/Moscow", "UTC", "Europe/Berlin")


@dataclass
class StrixProfile:
    """A coherent, self-consistent browser fingerprint."""
    name: str
    user_agent: str
    viewport: tuple[int, int]
    platform: str
    locale: str
    timezone: str
    hardware: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "user_agent": self.user_agent,
            "viewport": {"width": self.viewport[0], "height": self.viewport[1]},
            "platform": self.platform,
            "locale": self.locale,
            "timezone": self.timezone,
            "hardware": self.hardware,
            "headers": self.headers,
        }

    def render(self) -> str:
        """Compact fingerprint block (for config or docs)."""
        lines = [f"[Fingerprint: {self.name}]"]
        lines.append(f"  UA:       {self.user_agent}")
        lines.append(f"  viewport: {self.viewport[0]}x{self.viewport[1]} ({self.platform})")
        lines.append(f"  locale:   {self.locale} | tz: {self.timezone}")
        lines.append(f"  cores:    {self.hardware.get('cores', '?')} | "
                     f"ram: {self.hardware.get('ram_gb', '?')} GB | "
                     f"gpu: {self.hardware.get('gpu', '?')}")
        return "\n".join(lines)


def build_profile(kind: str = "desktop", seed: int | None = None) -> StrixProfile:
    """Build one coherent fingerprint (deterministic with a seed)."""
    rng = random.Random(seed)
    if kind == "mobile":
        ua, platform = _MOBILE_UA, "android"
        viewports, cores_range, ram_range = _VIEWPORTS["mobile"], (6, 8), (4, 8)
    elif kind == "mac":
        ua, platform = _MAC_UA, "macos"
        viewports, cores_range, ram_range = _VIEWPORTS["desktop"], (8, 12), (8, 16)
    else:
        ua, platform = _DESKTOP_UA, "linux"
        viewports, cores_range, ram_range = _VIEWPORTS["desktop"], (4, 16), (8, 32)

    vp = rng.choice(viewports)
    cores = rng.choice(list(range(cores_range[0], cores_range[1] + 1, 2)))
    ram = rng.choice(list(range(ram_range[0], ram_range[1] + 1, 4)))
    locale = rng.choice(_LOCALES)
    # coherence: RU locale ⇒ Moscow timezone, EN ⇒ non-Moscow (audit-proof)
    tz = "Europe/Moscow" if locale.startswith("ru") else rng.choice(("UTC", "Europe/Berlin"))

    gpu_pool = {
        "linux": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11)",
        "macos": "Apple M1",
        "android": "Adreno (TM) 740",
    }
    return StrixProfile(
        name=f"{kind}-{cores}c{ram}g",
        user_agent=ua,
        viewport=vp,
        platform=platform,
        locale=locale,
        timezone=tz,
        hardware={
            "cores": cores,
            "ram_gb": ram,
            "gpu": gpu_pool.get(platform, "SwiftShader"),
            "dpr": 2.0 if kind == "mobile" else 1.0,
        },
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": f"{locale},{locale[:2]};q=0.9,en;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        },
    )


def rotate_profiles(kind: str = "desktop", count: int = 3,
                    seed: int | None = None) -> list[StrixProfile]:
    """A rotation pool of coherent profiles (Strix per-session identity)."""
    rng = random.Random(seed)
    return [build_profile(kind, seed=(seed or 0) + i * 7 + rng.randint(0, 9999))
            for i in range(count)]


# ═══════════════════════════════════════════════════════════════════════════════
# Defensive audit — the inconsistencies bots get caught on
# ═══════════════════════════════════════════════════════════════════════════════

def audit_fingerprint(fp: dict[str, Any] | StrixProfile) -> list[str]:
    """Check a fingerprint for tell-tale inconsistencies. Returns issues.

    Empty list = coherent. Each string names one leak a site's
    fingerprinting JS could use to flag the session as automated.
    """
    issues: list[str] = []
    if isinstance(fp, StrixProfile):
        fp = fp.to_dict()

    ua = (fp.get("user_agent") or "").lower()
    platform = (fp.get("platform") or "").lower()
    viewport = fp.get("viewport") or {}
    w = viewport.get("width", 0) if isinstance(viewport, dict) else 0

    if "mobile" in ua and platform != "android" and w < 500:
        issues.append("UA says Mobile but platform is not android")
    if "mobile" in ua and w > 1200:
        issues.append("mobile UA but desktop-width viewport")
    if "mac os x" in ua and platform != "macos":
        issues.append("UA says macOS but platform is not macos")
    if "x11" in ua and platform not in ("linux",):
        issues.append("UA says X11/Linux but platform is not linux")
    if w > 1200 and platform == "android":
        issues.append("mobile platform but desktop-width viewport")

    locale = fp.get("locale", "")
    tz = fp.get("timezone", "")
    if locale.startswith("ru") and "moscow" not in tz.lower() and tz not in ("", None):
        issues.append(f"RU locale {locale} but timezone {tz}")
    if locale.startswith("en") and "moscow" in tz.lower():
        issues.append(f"EN locale {locale} but Moscow timezone")

    hw = fp.get("hardware") or {}
    cores = hw.get("cores")
    if isinstance(cores, int) and cores > 8 and platform == "android":
        issues.append("android with 10+ cores is suspicious")
    return issues


def is_coherent(fp: dict[str, Any] | StrixProfile) -> bool:
    return not audit_fingerprint(fp)


__all__ = [
    "StrixProfile",
    "audit_fingerprint",
    "build_profile",
    "is_coherent",
    "rotate_profiles",
]
