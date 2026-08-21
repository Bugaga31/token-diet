"""Tests for strix_stealth — fingerprint coherence and anti-detection."""
from token_diet.strix_stealth import (
    audit_fingerprint,
    build_profile,
    is_coherent,
    rotate_profiles,
)


def test_build_desktop_profile():
    p = build_profile("desktop", seed=1)
    assert p.viewport[0] > 1000
    assert "X11" in p.user_agent
    assert p.platform == "linux"
    assert p.hardware["cores"] >= 4


def test_build_mobile_profile():
    p = build_profile("mobile", seed=2)
    assert p.viewport[0] < 500
    assert "Mobile" in p.user_agent
    assert p.platform == "android"
    assert p.hardware["dpr"] == 2.0


def test_profiles_are_coherent():
    for kind in ("desktop", "mobile", "mac"):
        p = build_profile(kind, seed=7)
        assert is_coherent(p), f"{kind}: {audit_fingerprint(p)}"


def test_rotate_profiles_distinct():
    profs = rotate_profiles("desktop", count=3, seed=5)
    names = {p.name for p in profs}
    assert len(names) >= 2  # distinct profiles


def test_audit_catches_inconsistency():
    p = build_profile("desktop", seed=3).to_dict()
    p["user_agent"] = "Mozilla/5.0 (Linux; Android 13; Pixel 7) ... Mobile Safari/537.36"
    p["platform"] = "linux"
    issues = audit_fingerprint(p)
    assert issues  # UA says Mobile but platform is linux


def test_audit_catches_tz_mismatch():
    p = build_profile("desktop", seed=4).to_dict()
    p["locale"] = "en-US"
    p["timezone"] = "Europe/Moscow"
    issues = audit_fingerprint(p)
    assert any("Moscow" in i or "timezone" in i for i in issues)
