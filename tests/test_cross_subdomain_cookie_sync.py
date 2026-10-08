"""Retirement gate for the cross-subdomain session bridge (#3501 / #3559).

WHAT THIS FILE USED TO PIN
--------------------------
A session created on tortoise.premiselabs.co was persisted to the parent-domain
cookie `sb-tortoise-auth-token` by `website/assets/supabase-session.js`; the
OAuth consent page (`tortoise/oauth.py`) carried a faithful inline port of the
same adapter, and the blog-admin SPA (`website/apps/blog-admin/src/lib/
supabase.ts`) read/wrote the same cookie under `STORAGE_KEY`. This file asserted
those copies stayed byte-compatible on the cookie name, domain, path, SameSite,
Secure, expiry, size guard and the write/remove templates.

WHY THE SYNC ASSERTIONS ARE GONE (#3559 / #4178)
------------------------------------------------
The copies are DELETED, so there is nothing left to keep in sync:

  - `website/assets/supabase-session.js` — the cross-subdomain bridge itself,
    the exact JS-readable artifact #3501 exists to remove. DELETED (#3559).
    It was already unloaded by every page (`PAGES` emptied in #4054); it lived
    on only because `oauth.py` ported it and the #3503 fragment harness executed
    it. `test_the_shared_bridge_is_deleted` below is its absence proof.
  - `website/apps/blog-admin/src/lib/supabase.ts` — the console's legacy
    storage adapter. DELETED (#4178); the console's data layer now rides the
    same-origin `/api/sb/*` Token Handler (`src/lib/backend.ts`).
  - The dashboard's session adapter was deleted in #4054; `main.jsx` keeps the
    host-conditional helpers ONLY for the non-secret `tt_claim_pending` marker.

What survives here, and is still gated:

  - the ABSENCE of the bridge file and of the dashboard's `public/` copy;
  - the absence of the bridge `<script>` tag on every served HTML page;
  - the `tt_claim_pending` marker writers (`signup.html`, `main.jsx`) — the one
    remaining host-conditional cookie write on the site;
  - the same-origin auth bounce's search-param preservation (`main.jsx`);
  - the key-identity router in `oauth.py`, the last remaining session-adapter
    copy (out of scope for #3559/#4178 — its owner is #3496).

The #3503 fragment-retention harness that executed the bridge lives in
`tests/test_session_bridge_fragment_retention.py`; it is now the same deletion
gate, because with the browser holding no session there is no failed write to
retain a fragment against.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The deleted bridge — asserted ABSENT, not read.
SHARED = REPO_ROOT / "website" / "assets" / "supabase-session.js"
# The deleted blog-admin adapter — asserted ABSENT, not read.
BLOG_ADMIN = (
    REPO_ROOT / "website" / "apps" / "blog-admin" / "src" / "lib" / "supabase.ts"
)
# The BFF-backed client that replaced the blog-admin adapter.
BLOG_BACKEND = (
    REPO_ROOT / "website" / "apps" / "blog-admin" / "src" / "lib" / "backend.ts"
)
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"

# #4054: main.jsx's SESSION adapter is deleted, but the file still writes the
# non-secret `tt_claim_pending` parent-domain marker with the same
# host-conditional helpers (isLocal/isPremiselabsHost/domainAttr/secureAttr). It
# stays in the helper/attribute sets as a MARKER writer, not a session adapter.
DASHBOARD = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"

# #4054: the auth page (signup.html = `/auth`) moved to the app project. It has
# its OWN inline `tt_claim_pending` marker writer (`setClaimPendingMarker`) using
# INLINE hostname/protocol conditions rather than the shared helpers.
SIGNUP_PAGE = (
    REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "signup.html"
)

# The exact script tag a page would use to load the shared bridge.
BRIDGE_SCRIPT = 'src="/assets/supabase-session.js"'

# ── #3496: key-identity routing (allowlist of ONE key) ────────────────────
# The router lives in the write/remove paths of the session adapter. With the
# bridge and the blog-admin adapter deleted, `oauth.py` is the LAST copy; its
# write/remove paths route a non-session key to the aux stores and never to the
# cookie (`!==` on both methods).
_ROUTER_KEY_CMP = re.compile(r"\bkey\s*(===|!==)\s*([A-Za-z_$][\w$]*)")
_ROUTER_SHAPE_PREDICATE = re.compile(
    r"\.endsWith\(|\.startsWith\(|\.slice\(|\.charAt\(|\.includes\("
    r"|RegExp|\.test\(\s*key\b|\.match\(|typeof\s+key\b|\bkey\s*\["
)
_ROUTER_CASES = [
    (OAUTH, "COOKIE_NAME", {"setItem": "!==", "removeItem": "!=="}),
]


def _read(path: Path) -> str:
    assert path.exists(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    """Blank out `//` and `/* */` comments so a text assertion below matches
    CODE, not prose. Ported from src/testSupport.js::stripComments with its
    `:`-prefixed-`//` guard (so `https://` inside a string survives); no
    quote/template awareness, so a `//` inside a string literal IS treated as a
    comment here.
    """
    out = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/" and not (out and out[-1] == ":"):
            while i < n and src[i] != "\n":
                i += 1
        elif c == "/" and nxt == "*":
            i += 2
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                i += 1
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _extract_fn_body(text: str, name: str) -> str:
    r"""Extract a named function/method body (any style: `name(key, value) {`,
    `name: function (key, value) {`, `function name() {`,
    `var name = function (...) {`, or the TS object-property form
    `name: (key: string) => {`) up to the matching close brace, including the
    signature line. Does NOT match call sites (`name(...);` — no `{`)."""
    m = re.search(
        rf"(?:var\s+|function\s+)?{re.escape(name)}\s*"
        rf"(?:(?::|=)\s*(?:function\s*)?)?\([^)]*\)\s*(?:=>\s*)?{{",
        text,
    )
    assert m, f"missing function: {name}"
    start = m.start()
    depth = 0
    i = m.end() - 1  # the '{'
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced function body: {name}")


# ── The retirement proofs ──────────────────────────────────────────────────


def test_the_shared_bridge_is_deleted() -> None:
    """#3559: the cross-subdomain bridge file must be GONE.

    It is the JS-readable, parent-domain session artifact #3501 exists to
    remove. Every assertion that used to read it is deleted with it; this is the
    absence proof that replaces them.
    """
    assert not SHARED.exists(), (
        f"{SHARED} is back — the JS-readable cross-subdomain session bridge is "
        "the artifact #3501/#3559 removed. Do not restore it: the BFF owns the "
        "session now (`__Host-session`, HttpOnly and host-only)."
    )
    # Non-vacuity: the directory itself must still exist, or this "absence" could
    # be a moved/deleted tree rather than a deleted artifact.
    assert SHARED.parent.is_dir(), f"{SHARED.parent} is gone — the check is vacuous"


def test_the_blog_admin_legacy_adapter_is_deleted() -> None:
    """#4178: the console's legacy storage adapter must be GONE, and the
    BFF-backed client that replaced it must exist."""
    assert not BLOG_ADMIN.exists(), (
        f"{BLOG_ADMIN} is back — it stored the console's Supabase session in the "
        "JS-readable parent-domain cookie (#4178). The console's data layer now "
        "rides the same-origin /api/sb/* Token Handler."
    )
    assert BLOG_BACKEND.exists(), (
        f"{BLOG_BACKEND} is missing — the console's data layer has no BFF-backed "
        "client, so either it regressed to the legacy adapter or the migration "
        "was reverted."
    )
    backend = _read(BLOG_BACKEND)
    assert "persistSession: false" in backend, (
        "the BFF-backed client must hold no session (persistSession: false)"
    )
    assert "autoRefreshToken: false" in backend, (
        "the BFF-backed client must not refresh a token it does not hold"
    )
    assert "detectSessionInUrl: false" in backend, (
        "the console never receives the OAuth fragment (the BFF exchanges the "
        "code server-side) — it must not become a second fragment consumer"
    )
    assert "/api/sb" in backend, (
        "the client's rest/storage requests must be rewritten to the same-origin "
        "Token Handler"
    )


def test_dashboard_does_not_ship_the_shared_bridge_copy() -> None:
    """#4054/#3559: the dashboard must NOT ship a copy of the bridge — re-adding
    `public/assets/supabase-session.js` would re-arm the JS-readable
    cross-subdomain session (vite copies public/ verbatim into the dist)."""
    public_copy = (
        REPO_ROOT
        / "website"
        / "apps"
        / "dashboard"
        / "public"
        / "assets"
        / "supabase-session.js"
    )
    assert not public_copy.exists(), (
        "the dashboard public/ copy of the shared bridge is back — the dashboard "
        "is BFF-migrated and must not ship a JS-readable session bridge (#4054)"
    )


def test_no_page_is_left_on_the_legacy_bridge() -> None:
    """#3501/#4054/#3559: NO served HTML page may load the cross-subdomain bridge.

    Scans every HTML page under `website/` (build output excluded) for the bridge
    script tag and fails on any hit, including a page that would 404 on the now
    deleted file. Non-vacuity: a broken glob must not read as "clean".
    """
    skip = {"node_modules", "dist", "vendor", ".wrangler", ".git"}
    website = REPO_ROOT / "website"
    pages = [
        p
        for p in website.rglob("*.html")
        if not any(part in skip for part in p.relative_to(website).parts)
    ]
    assert len(pages) > 10, (
        f"only {len(pages)} HTML pages scanned under {website} — the glob is "
        "broken, so this absence proof would pass vacuously"
    )
    offenders = [p.relative_to(REPO_ROOT) for p in pages if BRIDGE_SCRIPT in _read(p)]
    assert not offenders, (
        "these pages still load the legacy cross-subdomain bridge:\n"
        + "\n".join(f"  {o}" for o in offenders)
        + "\n\nThe BFF owns the session now; a page that loads the shared bridge "
        "re-arms the JS-readable cross-subdomain cookie #3501 exists to remove."
    )


# ── The surviving marker writers (#1857) ───────────────────────────────────
#
# #4054 retargeted these, not removed: signup.html and main.jsx still write the
# `tt_claim_pending` marker — the last host-conditional cookie writes on the
# site — so the #1857 invariant (host-conditional Domain, https-conditional
# Secure) still has a live subject.


def test_signup_marker_is_host_conditional() -> None:
    """#1857 (code-review P2, cycle 3): the auth page has its OWN inline
    tt_claim_pending marker writer (setClaimPendingMarker) that shares the
    bug class — a revert to hardcoded `; Domain=.premiselabs.co; Secure` there
    would drop the marker on localhost/previews and break cross-origin claim
    routing. It uses inline conditions (hostname + protocol), not the shared
    domainAttr()/secureAttr() helpers, so it needs its own contract: no
    unconditional Domain/Secure, host-conditional Domain, https-conditional
    Secure, SameSite=Lax, Expires=."""
    text = _read(SIGNUP_PAGE)
    body = _extract_fn_body(text, "setClaimPendingMarker")
    norm = body.replace('"', "'")
    assert "endsWith('.premiselabs.co')" in norm
    assert "? '; Domain=.premiselabs.co'" in norm
    assert norm.count("; Domain=.premiselabs.co") == 1
    assert "protocol === 'https:'" in norm
    assert "? '; Secure'" in norm
    assert norm.count("; Secure") == 1
    assert "SameSite=Lax" in body
    assert "Expires=" in body


def test_the_signup_marker_writer_is_actually_called() -> None:
    """#4054: pin the CALL SITE, not just the body shape.

    The ANON_TEAM_NO_OWNER sign-in funnel sets the marker so the dashboard's
    claim card can pick the visitor up. Deleting the call (or the marker name)
    would leave the body assertions green and the routing broken. The match
    excludes a preceding `function` keyword so the DECLARATION does not satisfy
    it."""
    text = _read(SIGNUP_PAGE)
    calls = [
        m
        for m in re.finditer(r"(?<![\w.])setClaimPendingMarker\s*\(", text)
        if not re.search(r"function\s+$", text[max(0, m.start() - 40) : m.start()])
    ]
    assert calls, (
        "setClaimPendingMarker is DECLARED but never CALLED — the marker would "
        "never be set, so cross-origin claim routing silently stops working"
    )
    body = _extract_fn_body(text, "setClaimPendingMarker")
    assert "tt_claim_pending" in body, (
        "the writer no longer emits the tt_claim_pending marker the dashboard reads"
    )


def test_auth_bounce_preserves_search_params() -> None:
    """#1860 (P3-5): the auth bounce must preserve the search params — /auth's
    OAuth-error banner reads ?error=... — plus the #1909 error fragment.

    #4054 retarget: the bridge's global `window.bounceToAuth` is gone; main.jsx
    now owns a local same-origin bounce. #3930 moved the destination literal into
    the pure `authBounceTarget` module (which also carries the requested PATHNAME
    as `/auth`'s `next`). `authBounce.test.js` and `test_admin_return_to.py` own
    the behaviour."""
    dash = _read(DASHBOARD)
    norm = _strip_js_comments(dash)
    assert norm.count("bounceToAuth(window.location.search, oauthErrorHash())") >= 2, (
        "the auth bounce must preserve search params + the #1909 error fragment "
        "(both the 401-provision and the mount-gate bounce)"
    )
    norm = _strip_js_comments(dash).replace('"', "'")
    assert re.search(
        r"authBounceTarget\(\{\s*pathname:\s*window\.location\.pathname,\s*search,\s*errorHash:\s*hash\s*\}\)",
        norm,
    ), (
        "the bounce must be the same-origin /auth navigation that consumes the "
        "preserved search/hash (and, since #3930, the pathname)"
    )
    assert not re.search(r"location\.replace\(\s*['\"]/auth['\"]\s*\+", norm), (
        "the pathname-dropping destination is back (#3930)"
    )


def test_key_identity_router_allows_only_the_session_key() -> None:
    """#3496: the write/remove paths route by key IDENTITY, allowlisting ONE
    key (the session key). The PKCE code_verifier must never reach the
    JS-readable parent-domain jar, and the rule that keeps it out must not be a
    denylist of verifier-shaped names.

    Only `oauth.py` remains in `_ROUTER_CASES` — the bridge and the blog-admin
    adapter that carried the other copies are deleted (#3559/#4178). The
    contract is unchanged: EXACTLY ONE key-left comparison per method naming
    that file's session-key constant, and NO string-shape predicate on `key`.
    The behavioural counterpart is tests/test_oauth_consent_pkce.py."""
    for path, session_key, methods in _ROUTER_CASES:
        text = _read(path)
        for name, operator in methods.items():
            body = _extract_fn_body(text, name)
            found = _ROUTER_KEY_CMP.findall(body)
            assert found == [(operator, session_key)], (
                f"{path.name}:{name}: expected exactly one key comparison "
                f"`key {operator} {session_key}`, found {found} — the router is "
                "an allowlist of ONE; a second comparison, a different "
                "identifier, or a different polarity is a second routing rule "
                "(#3496)"
            )
            shape = _ROUTER_SHAPE_PREDICATE.findall(body)
            assert shape == [], (
                f"{path.name}:{name}: string-shape predicate(s) on `key`: "
                f"{shape} — a denylist of verifier-shaped names defaults INTO "
                "the credential jar for every name it has not heard of; route by "
                "key identity instead (#3496)"
            )
