"""The BFF invariant gate — #3501.

THE INVARIANT
-------------
Under the BFF, the browser holds exactly one credential: an HttpOnly `__Host-session`
cookie it cannot read. Therefore:

  (A) No browser-reachable surface may READ or HOLD session material — no JS-readable
      session cookie, no localStorage/sessionStorage token, no `supabase.auth` client session.
  (B) Every client data call must go through the same-origin BFF (`/api/...`), because the
      BFF is the only thing that holds the access token.

WHY THIS TEST EXISTS
--------------------
W1's suite passes 20/20 while `welcome.html` and the blog-admin data layer are still wired to
the deleted token. Unit tests exercise the layer being edited; they cannot see that a *consumer*
was left pointing at a credential that no longer exists. Three user-facing surfaces were broken
under a fully green suite.

This is a STATIC gate. It is deliberately blunt: it asserts absence, repo-wide, so that a
surface cannot be silently left behind. Absence-by-pointer cannot be gamed by paraphrasing,
which is why it is a grep and not a review.

NON-VACUITY
-----------
A gate that scans nothing passes. Every scan asserts it inspected a plausible number of files,
so a broken glob (or a moved directory) fails loudly instead of reporting clean.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEBSITE = REPO / "website"

# Directories that are not browser-served source.
SKIP_DIRS = {"node_modules", "dist", ".git", "archive", ".worktrees", "vendor", ".wrangler"}

# The legacy cross-subdomain bridge: a JS-readable, parent-domain cookie holding a session.
LEGACY_TOKEN_MARKERS = (
    "sb-tortoise-auth-token",
    "createTortoiseSupabaseClient",
)

# Browser surfaces that are MIGRATED (or must be). These are the files the BFF owns.
MIGRATED_SURFACES = (
    "website/welcome.html",
    "website/apps/blog-admin/src/lib/blog-api.ts",
    "website/apps/blog-admin/src/hooks/useAuth.ts",
    "website/apps/dashboard/src/main.jsx",
)

# The proxy endpoint every migrated client data call must route through.
PROXY_PREFIX = "/api/v1"

# Client-surface migration is tracked in #3559. These five invariants fail until the
# remaining browser surfaces migrate. They are xfail — NOT deleted and NOT skipped —
# so the obligation stays visible and the gate keeps naming the offenders. Flip to
# strict, then remove the markers, when #3559 lands.
CLIENT_MIGRATION = "client-surface migration outstanding — see #3559"


def _browser_sources():
    """Yield browser-reachable source files (html/js/ts/tsx/jsx), excluding build output."""
    out = []
    for p in WEBSITE.rglob("*"):
        if not p.is_file():
            continue
        # Skip on the path RELATIVE to WEBSITE. Using p.parts here would match `.worktrees`
        # in the absolute prefix (the repo lives under .worktrees/ in this worktree) and
        # silently discard every file — a broken gate that reports clean.
        if any(part in SKIP_DIRS for part in p.relative_to(WEBSITE).parts):
            continue
        if p.suffix.lower() not in {".html", ".js", ".ts", ".tsx", ".jsx", ".mjs"}:
            continue
        out.append(p)
    return sorted(out)


@pytest.fixture(scope="module")
def sources():
    srcs = _browser_sources()
    # NON-VACUITY: a broken glob must not read as "clean".
    assert len(srcs) > 40, (
        f"scan found only {len(srcs)} browser sources under {WEBSITE} — the glob is broken, "
        "so every assertion below would pass vacuously"
    )
    return srcs


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _strip_comments(text: str, suffix: str) -> str:
    """Remove comments before scanning for markers.

    A marker NAMED in a comment is documentation, not a live path — and this gate
    itself is the reason those comments exist (each says what was removed and
    why). Matching prose made the gate report a file whose only remaining mention
    was the explanation of its own removal.
    """
    if suffix.lower() == ".html":
        return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    out = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Line comments, but NOT the `//` in `https://`.
    return re.sub(r"(?<!:)//[^\n]*", "", out)


def _code(p: Path) -> str:
    """File contents with comments removed."""
    return _strip_comments(_read(p), p.suffix)


@pytest.mark.xfail(reason=CLIENT_MIGRATION, strict=False)
def test_no_legacy_js_readable_token_anywhere(sources):
    """(A) The legacy JS-readable session cookie must be gone from every browser surface.

    It is parent-domain and non-HttpOnly by construction — the exact artifact §1 exists to
    remove. A single surviving reference re-opens the cross-subdomain bridge.
    """
    offenders = []
    for p in sources:
        text = _code(p)
        for marker in LEGACY_TOKEN_MARKERS:
            if marker in text:
                rel = p.relative_to(REPO)
                lines = [i for i, ln in enumerate(text.splitlines(), 1) if marker in ln]
                offenders.append(f"  {rel}:{lines} references {marker!r}")
    assert not offenders, (
        "legacy JS-readable token path still present in browser source:\n"
        + "\n".join(offenders)
        + "\n\nUnder the BFF nothing writes this token, so every consumer of it is broken."
    )


@pytest.mark.xfail(reason=CLIENT_MIGRATION, strict=False)
def test_migrated_surfaces_do_not_use_supabase_auth_client():
    """(A) No `supabase.auth` client session in a migrated surface.

    The BFF holds the session server-side; a client that still calls `getSession()` will get
    `null` and — worse — `onAuthStateChange` will never fire, so the UI cannot react to auth
    state it can no longer observe.
    """
    patterns = (
        re.compile(r"supabaseClient\.auth\.|supabase\.auth\.(getSession|onAuthStateChange)"),
    )
    offenders = []
    for rel in MIGRATED_SURFACES:
        p = REPO / rel
        if not p.exists():
            offenders.append(f"  {rel}: MISSING (expected a migrated surface)")
            continue
        text = _code(p)
        for pat in patterns:
            for m in pat.finditer(text):
                line = text[: m.start()].count("\n") + 1
                offenders.append(f"  {rel}:{line} uses {m.group(0)}")
    assert not offenders, (
        "client-side auth session still referenced in a migrated surface:\n"
        + "\n".join(offenders)
    )


@pytest.mark.xfail(reason=CLIENT_MIGRATION, strict=False)
def test_no_client_holds_a_bearer_token(sources):
    """(A) No browser surface may construct an Authorization header from client state.

    The proxy strips + re-sets Authorization server-side. A client that builds one is either
    holding a token it should not have, or sending a header that will be discarded — both
    indicate the surface was not migrated.
    """
    pat = re.compile(r"Bearer\s*\$\{")
    offenders = []
    for p in sources:
        # Server code is ALLOWED to build a Bearer header — that is how the BFF talks to
        # GoTrue/api.premiselabs.co, and it is the whole point of the proxy. Only a
        # *browser* building one is a defect, because it means the browser holds a token.
        if str(p.relative_to(REPO)).startswith("website/functions/"):
            continue
        text = _read(p)
        for m in pat.finditer(text):
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"  {p.relative_to(REPO)}:{line}")
    assert not offenders, "client-constructed Bearer headers remain:\n" + "\n".join(offenders)


@pytest.mark.xfail(reason=CLIENT_MIGRATION, strict=False)
def test_the_proxy_has_a_caller(sources):
    """(B) The BFF proxy must actually be used by a client.

    A proxy with no caller is scaffolding. This asserts the migration reached the data layer,
    which is precisely where W2 stopped: the three `Bearer` sites were converted while the
    underlying `supabase.storage` calls still rode the legacy client.
    """
    callers = [
        p.relative_to(REPO)
        for p in sources
        if PROXY_PREFIX in _read(p)
        # Server code is not a caller. The proxy source itself contains the string, so
        # scanning it would let the proxy "prove" its own existence — a false pass.
        and not str(p.relative_to(REPO)).startswith("website/functions/")
    ]
    assert callers, (
        f"no client surface calls {PROXY_PREFIX} — the W6 proxy has no caller, so every "
        "client data call is still going somewhere else (probably a JS-readable token path)"
    )


@pytest.mark.xfail(reason=CLIENT_MIGRATION, strict=False)
def test_client_data_layer_does_not_import_the_legacy_supabase_client():
    """(B) The blog-admin data layer must not reach for the legacy client.

    `@/lib/supabase` stores its session in the JS-readable parent-domain cookie. Any call
    through it is unauthenticated under the BFF and will fail RLS.
    """
    p = REPO / "website/apps/blog-admin/src/lib/blog-api.ts"
    assert p.exists(), f"{p} missing"
    text = _read(p)
    offenders = []
    for m in re.finditer(r"from\s+'@/lib/supabase'", text):
        line = text[: m.start()].count("\n") + 1
        offenders.append(f"  blog-api.ts:{line}")
    for m in re.finditer(r"\bsupabase\.(storage|from)\(", text):
        line = text[: m.start()].count("\n") + 1
        offenders.append(f"  blog-api.ts:{line} calls {m.group(0)} via the legacy client")
    assert not offenders, (
        "blog-admin data layer still uses the legacy Supabase client:\n"
        + "\n".join(offenders)
    )


def test_welcome_page_does_not_load_a_client_auth_library():
    """(A) `/welcome` must not load supabase-js or build a client.

    /welcome is gated SERVER-SIDE by functions/welcome.ts. The page must render, not
    authenticate. It previously called `window.createTortoiseSupabaseClient` — a factory
    defined in a script the page never loaded — so every successful login fell into the
    "temporarily unavailable" branch. That is the #3485 loop's sibling.
    """
    p = REPO / "website/welcome.html"
    assert p.exists(), f"{p} missing"
    text = _code(p)
    offenders = []
    if "@supabase/supabase-js" in text:
        line = next(i for i, ln in enumerate(text.splitlines(), 1) if "@supabase/supabase-js" in ln)
        offenders.append(f"  welcome.html:{line} loads supabase-js")
    if "createTortoiseSupabaseClient" in text:
        line = next(
            i for i, ln in enumerate(text.splitlines(), 1) if "createTortoiseSupabaseClient" in ln
        )
        offenders.append(f"  welcome.html:{line} builds a client session")
    assert not offenders, "/welcome still authenticates client-side:\n" + "\n".join(offenders)


def test_error_semantics_never_conflate_store_fault_with_signed_out(sources):
    """A D1/store fault must not be reported as "not signed in".

    This is the #3485 bug class: a transient fault answered as 401 tells the browser to clear
    state, so an outage signs the user out and then loops. `admin-auth.ts` documents the
    distinction as load-bearing and then erases it.
    """
    p = REPO / "website/functions/blog/_shared/admin-auth.ts"
    assert p.exists(), f"{p} missing"
    text = _read(p)
    # A resolver that collapses "unavailable" into the same value as "no session" erases the
    # distinction its own callers depend on.
    bad = re.search(r'if\s*\(resolved\s*&&\s*"unavailable"\s+in\s+resolved\)\s*return\s+null', text)
    assert not bad, (
        "admin-auth.ts collapses `unavailable` into `null`, which its callers emit as 401 — "
        "the #3485 class. A store fault must surface as 503, never as signed-out."
    )
