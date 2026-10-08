"""Regression gate for #3503 — discharged structurally by #3559.

THE DEFECT (why this file existed)
----------------------------------
``website/assets/supabase-session.js`` parsed the implicit-flow OAuth fragment
(``#access_token=…``) synchronously at load time and called ``storeSession()``,
which correctly returned ``false`` when the parent-domain cookie write did not
take. The caller DISCARDED that return value and then stripped the fragment
unconditionally::

    storeSession(session);
    history.replaceState(null, '', window.location.pathname + window.location.search);

So when the write failed (a cookie over the browser's ~4096-byte limit is
silently dropped — no exception), both copies of the credential vanished: the
cookie absent, the fragment erased. The user was stranded on ``/auth?next=…``
with a clean console.

The second consumer made it worse: supabase-js re-read the same hash and cleared
``window.location.hash`` BEFORE awaiting ``_saveSession()``, destroying the
fragment a second time. The remedy was one fragment consumer per page.

HOW IT IS CLOSED NOW (#3559)
----------------------------
The bridge is **DELETED**, together with the blog-admin adapter that shared its
cookie (#4178). The cookie-jar harness that executed the real script is deleted
WITH its subject — a test cannot execute a file that no longer exists, and the
invariant it proved is now structural: under the BFF the browser holds exactly
one credential (the HttpOnly, host-only ``__Host-session``) and **no browser code
writes a session to a cookie or localStorage at all**. A failed write cannot
destroy a credential the browser never stores.

What remains is the ABSENCE proof (the bridge is gone) plus the
one-fragment-consumer property (the consent page is the sole place that ingests
the OAuth fragment; every other surface builds no client or explicitly opts out).
The static, repo-wide absence check lives in
``tests/test_no_legacy_token_path.py``; this file owns the deletion and the
fragment-consumer map.

KEEPING ``_require_node``
-------------------------
``tests/test_oauth_consent_pkce.py`` imports ``_require_node`` from this module
for its own node harness. It stays here, and MUST keep failing (never skipping)
when node is absent — a skipped harness is a no-op gate.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED = REPO_ROOT / "website" / "assets" / "supabase-session.js"
DASHBOARD = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"
# The BFF-backed client that replaced the deleted blog-admin adapter.
BLOG_BACKEND = (
    REPO_ROOT / "website" / "apps" / "blog-admin" / "src" / "lib" / "backend.ts"
)
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"
SIGNUP_PAGE = (
    REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "signup.html"
)
DASHBOARD_INDEX = REPO_ROOT / "website" / "apps" / "dashboard" / "index.html"


def _require_node() -> None:
    """Fail — never skip — when node is absent.

    A skipped harness is a no-op gate: the session-bridge harnesses are wired
    into the ``onboarding``/``api`` CI surfaces, and a harness must not depend on
    whatever the runner image happens to ship. The contract here is deliberately
    FAIL-ALWAYS (stricter than the CI-only gate ``tests/test_pi_capture_hooks.py``
    uses): it predates #4620 and is unchanged by it, even though that PR now
    provisions Node 22 in the ``python-ci`` ``test`` job they run in. Mirrors
    tests/e2e/auth/bff_test_helpers.py::require_toolchain; set
    SESSION_BRIDGE_ALLOW_NO_TOOLCHAIN=1 only for a deliberate toolchain-free
    subset (an explicit skip, never a green no-op).
    """
    if shutil.which("node"):
        return
    if os.environ.get("SESSION_BRIDGE_ALLOW_NO_TOOLCHAIN") == "1":
        pytest.skip("node absent — SESSION_BRIDGE_ALLOW_NO_TOOLCHAIN=1")
    pytest.fail(
        "missing required toolchain: node — the session-bridge harness "
        "cannot run and MUST NOT silently pass. Install Node (v20+) "
        "or set SESSION_BRIDGE_ALLOW_NO_TOOLCHAIN=1 to opt out explicitly."
    )


def _read(path: Path) -> str:
    assert path.exists(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def test_the_legacy_bridge_is_deleted() -> None:
    """#3559: the subject of this gate is gone, so the gate is its absence.

    The old harness executed ``SHARED`` under ``vm.runInNewContext`` with a
    cookie jar. It is deleted with the file: the #3503 invariant (a failed
    session write must not destroy the only credential copy) is now discharged by
    construction, because no browser surface writes a session at all.
    """
    assert not SHARED.exists(), (
        f"{SHARED} is back — the legacy cross-subdomain bridge. It is the "
        "JS-readable session artifact #3501/#3559 removed; a browser that holds "
        "a session cookie can again destroy its only copy on a failed write "
        "(#3503). Do not restore it, and do not restore this harness unless the "
        "artifact does."
    )
    # Non-vacuity: the parent directory must still exist, so this absence cannot
    # be a deleted tree rather than a deleted file.
    assert SHARED.parent.is_dir(), f"{SHARED.parent} is gone — the check is vacuous"


def test_no_browser_surface_writes_a_session_to_a_js_readable_store() -> None:
    """The #3503 property, structurally: nothing in the browser writes the
    session.

    The bridge wrote a parent-domain session cookie and migrated a localStorage
    copy; the deleted blog-admin adapter wrote the same cookie. Both are gone.
    The BFF-backed replacement holds NO session (``persistSession: false``, no
    storage adapter) and the dashboard builds NO client. A browser that never
    stores a credential cannot lose one to a failed write.
    """
    backend = _read(BLOG_BACKEND)
    assert "persistSession: false" in backend, (
        "the BFF-backed client must hold no session (#3503 is unreachable only "
        "while the browser stores nothing)"
    )
    assert "autoRefreshToken: false" in backend, (
        "the BFF-backed client must not refresh a token it does not hold"
    )
    assert "storage:" not in backend, (
        "the BFF-backed client must not configure a storage adapter — that was "
        "how the legacy cookie was written and re-written"
    )

    dashboard = _read(DASHBOARD)
    assert "createClient(" not in dashboard, (
        "main.jsx must not construct a supabase-js client — it is BFF-migrated "
        "and cannot be a credential writer (#4054)"
    )
    # The dashboard still writes the NON-SECRET claim marker, which is not a
    # credential; that is the only remaining document.cookie write and it is
    # pinned by tests/test_cross_subdomain_cookie_sync.py.
    assert "sb-tortoise-auth-token" not in dashboard


def test_one_fragment_consumer_per_page() -> None:
    """#3503 P1: exactly one surface ingests the OAuth fragment.

    The consent page builds its own inline client with ``detectSessionInUrl:
    true`` and loads no bridge; every other surface builds no client, or
    explicitly opts out (the BFF-backed console). With the bridge deleted there
    is no second consumer to clear the hash before a save.
    """
    oauth = _read(OAUTH)
    assert "detectSessionInUrl: true" in oauth, (
        "the consent page loads NO bridge and builds its own inline client — it "
        "is the sole fragment consumer there and must ingest the hash (#3503 P1)"
    )
    assert '<script src="/assets/supabase-session.js">' not in oauth, (
        "the consent page must not load the deleted shared bridge; its inline "
        "client is the only consumer (#3503 P1)"
    )

    # The dashboard is BFF-migrated: it builds NO supabase-js client and sets NO
    # detectSessionInUrl, so it cannot be a second fragment consumer.
    dashboard = _read(DASHBOARD)
    assert "detectSessionInUrl" not in dashboard, (
        "main.jsx configures supabase-js auth again — it is BFF-migrated and "
        "must not become a second fragment consumer (#3503 P1, #4054)"
    )
    assert "createClient(" not in dashboard, (
        "main.jsx must not construct a supabase-js client (#4054)"
    )

    assert "detectSessionInUrl: false" in _read(BLOG_BACKEND), (
        "the BFF-backed console client never receives the OAuth fragment (the "
        "BFF exchanges the code server-side) — it must stay false so it does not "
        "become a second consumer (#3503 P1)"
    )

    # No served page may load the deleted bridge.
    for page in (SIGNUP_PAGE, DASHBOARD_INDEX):
        assert 'src="/assets/supabase-session.js"' not in _read(page), (
            f"{page.name}: must not load the deleted shared bridge (#3559)"
        )
    # signin.html was the last legacy bridge page; it is deleted in #4054 (all
    # of /signin, /signin/, /signin.html 301 to /auth).
    assert not (REPO_ROOT / "website" / "signin.html").exists(), (
        "the retired signin.html is back — it was the last page on the legacy "
        "cross-subdomain bridge (#4054)"
    )
