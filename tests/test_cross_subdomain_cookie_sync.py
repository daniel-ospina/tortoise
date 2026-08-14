"""Static sync test: the tortoise cross-subdomain session bridge must stay
byte-compatible with the dashboard's #572 storage adapter (issue #1225).

A session created on tortoise.premiselabs.co is persisted to the parent-domain
cookie `sb-tortoise-auth-token` by `website/assets/supabase-session.js`, and the
dashboard (app.premiselabs.co) reads that same cookie with its own inline
adapter (`website/apps/dashboard/src/main.jsx`). Any drift between the two —
cookie name, domain, path, SameSite, Secure, expiry, or the write/remove
templates — silently recreates the exact bug this issue fixes (post-signup
redirect lands on the dashboard LOGIN screen).

Pure static text assertions: no browser, no network. Source files, not bundles,
so regex anchoring to declaration patterns is reliable.
"""

from __future__ import annotations

import re
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED = REPO_ROOT / "website" / "assets" / "supabase-session.js"
DASHBOARD = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"

PAGES = [
    REPO_ROOT / "website" / "signup.html",
    REPO_ROOT / "website" / "signin.html",
    REPO_ROOT / "website" / "welcome.html",
]


def _read(path: Path) -> str:
    assert path.exists(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def test_shared_adapter_declares_dashboard_cookie_identity() -> None:
    """Both adapters must declare the same cookie name + domain (anchored to
    const declarations so comment text can't false-match)."""
    for path, other in ((SHARED, DASHBOARD), (DASHBOARD, SHARED)):
        text = _read(path)
        for const in ("COOKIE_NAME", "COOKIE_DOMAIN"):
            # shared file uses var (ES5-safe in the CDN-loaded classic script);
            # dashboard main.jsx uses const — accept both declaration forms
            pat = re.compile(rf"(?:const|var)\s+{const}\s*=\s*['\"]([^'\"]+)['\"]")
            m = pat.search(text)
            assert m, f"{path}: missing const {const}"
            m2 = pat.search(_read(other))
            assert m2, f"{other}: missing const {const}"
            assert m.group(1) == m2.group(1), (
                f"{const} drift: {path}={m.group(1)!r} vs {other}={m2.group(1)!r}"
            )
    assert "sb-tortoise-auth-token" in _read(SHARED)
    assert ".premiselabs.co" in _read(SHARED)


def test_both_adapters_write_and_remove_cookie_with_same_attributes() -> None:
    """Attribute-sequence drift (Path/SameSite/Secure/Max-Age/expiry) breaks the
    dashboard read even when the constants match."""
    shared = _read(SHARED)
    dash = _read(DASHBOARD)
    # write template: key=encoded value + domain + path + SameSite=Lax + Secure + Expires
    assert "SameSite=Lax" in shared and "SameSite=Lax" in dash
    # Path VALUE must be '/' in both — a Path drift silently breaks the bridge
    # (shared file builds 'Path=' + COOKIE_PATH; dashboard inlines Path=/)
    path_pat = re.compile(r"(?:const|var)\s+COOKIE_PATH\s*=\s*['\"]([^'\"]+)['\"]")
    pm = path_pat.search(shared)
    assert pm and pm.group(1) == "/", f"shared COOKIE_PATH must be '/': {pm.group(1) if pm else None}"
    assert "Path=/" in dash
    # Secure + Max-Age=0 removal (both always emit these; localhost omission is
    # a separate branch inside the shared file, not the template)
    assert "Secure" in shared and "Secure" in dash
    assert "Max-Age=0" in shared and "Max-Age=0" in dash
    # encode/decode round-trip
    assert "encodeURIComponent" in shared and "encodeURIComponent" in dash
    assert "decodeURIComponent" in shared and "decodeURIComponent" in dash
    # 7-day expiry parity
    assert "7 * 24 * 3600 * 1000" in shared and "7 * 24 * 3600 * 1000" in dash
    # storageKey parity — the cookie name written/read must be the same on both sides
    assert "storageKey" in shared and "storageKey" in dash


def test_shared_script_syntax() -> None:
    """The wiring tests are string-presence based — a parse-error'd shared
    script would pass them. Best-effort node --check (skips when node absent)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    subprocess.run([node, "--check", str(SHARED)], check=True)


def test_shared_adapter_handles_localhost_and_size_guard() -> None:
    """The shared file must degrade safely off-premiselabs hosts and cap cookie
    size (the two things the dashboard adapter never needed)."""
    text = _read(SHARED)
    assert "localhost" in text and "127.0.0.1" in text
    assert "premiselabs.co" in text
    # size guard strips provider tokens when the cookie would exceed the cap
    assert "provider_token" in text and "SIZE_GUARD" in text


def test_all_tortoise_pages_wire_the_shared_bridge() -> None:
    """A dropped script tag or an unwired page passes the constant checks but
    silently returns the login-wall bug — assert the wiring directly."""
    for page in PAGES:
        text = _read(page)
        assert 'src="/assets/supabase-session.js"' in text, (
            f"{page.name}: missing shared bridge script tag"
        )
        assert "createTortoiseSupabaseClient(" in text, (
            f"{page.name}: createClient not routed through the shared factory"
        )
