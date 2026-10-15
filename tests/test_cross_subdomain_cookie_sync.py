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
    copy (out of scope for #3559/#4178 — its owner is #3496);
  - the completeness of the key-identity ROUTER REGISTRY (`_ROUTER_CASES`):
    every product source carrying the contract must be registered there, so a
    FOURTH copy cannot land unguarded (#5735's residual half — see
    `test_every_storage_adapter_copy_is_registered`).

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
#
# This table IS the contract's declared home (#5735): a copy of the adapter that
# is not listed here is a copy no gate can see, so
# `test_every_storage_adapter_copy_is_registered` below enumerates the copies and
# fails on any that is missing.
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


def test_dashboard_marker_writers_are_host_conditional() -> None:
    """#1857: main.jsx's `tt_claim_pending` marker writers must stay
    host-conditional.

    The dashboard's SESSION adapter is deleted (#4054), but these two writers
    survive because the marker is a NON-SECRET claim-intent signal the welcome
    page and the signin/signup routing read. They must build the cookie through
    the shared `domainAttr()`/`secureAttr()` helpers — a revert to a hardcoded
    `; Domain=.premiselabs.co; Secure` would drop the marker on
    localhost/preview origins (the #1857 bug class) and break cross-origin claim
    routing. This is the last subject of that invariant.
    """
    text = _read(DASHBOARD)
    for name in ("setClaimPendingMarker", "clearClaimPendingMarker"):
        body = _extract_fn_body(text, name)
        assert "domainAttr()" in body, (
            f"{name} no longer builds its Domain attribute through domainAttr() "
            "— a hardcoded Domain is rejected on localhost/preview origins"
        )
        assert "secureAttr()" in body, (
            f"{name} no longer builds its Secure attribute through secureAttr() "
            "— a hardcoded Secure is rejected over http (localhost/previews)"
        )
        assert "'; Domain=" not in body, (
            f"{name} hardcodes a Domain attribute (#1857): the host-conditional "
            "helper is what keeps the marker working off premiselabs.co"
        )
        assert "'; Secure" not in body, (
            f"{name} hardcodes a Secure attribute (#1857): the https-conditional "
            "helper is what keeps the marker working over http"
        )

    # Non-vacuity: the helpers the bodies delegate to must still be the
    # host-conditional definitions — asserting only on the bodies would stay
    # green if a helper itself were changed to a hardcoded attribute.
    norm = _strip_js_comments(text)
    assert (
        "const domainAttr = () => (isPremiselabsHost() && !isLocal() ? '; Domain=' + COOKIE_DOMAIN : '')"
        in norm
    ), "domainAttr must stay host-conditional (#1857)"
    assert "const secureAttr = () => (isLocal() ? '' : '; Secure')" in norm, (
        "secureAttr must stay https-conditional (#1857)"
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


# ── #5735: the registry is the contract's DECLARED HOME, and it must be complete ──
#
# #5735 counted three copies of this contract and only one CI-gated. Two of the
# three were then DELETED by the session-model lane (#3559 the bridge, #4178 the
# console adapter), so `oauth.py` is the last copy and the duplication itself is
# gone. What #5735 left un-done is its SECOND half: "the contract has a declared
# home that a new copy must be added to". `_ROUTER_CASES` above is that home —
# but a table only declares the copies someone remembered to list, and the two
# absence gates above are written by PATH (they can only forbid a copy that was
# deleted once). A FOURTH copy, in a new file, landed green. This scan is that
# gate.
#
# TWO detectors, because the contract has two failure shapes:
#
#   * ROUTER — a `setItem`/`removeItem` that routes on `key`: the shape
#     `_ROUTER_CASES` pins (exactly one key comparison per method, allowlisting
#     the session key).
#   * COOKIE-WRITER — a file that names the session cookie AND assigns
#     `document.cookie`: the shape #5735 calls "no routing — writes any key"
#     (the deleted bridge's own shape). A writer with NO router is caught too,
#     which is the point — such a copy has no legal registration, so it must be
#     key-routed by identity (then registered) or deleted, which is exactly the
#     decision #5735's task 4 asked the session-model lane to make.
#
# Scope: the PRODUCT source roots below. `tests/` is deliberately out of scope —
# a harness embeds the adapter on purpose (this file pins bodies by text, and
# tests/test_oauth_consent_pkce.py runs a JS `cookieStorage` over a mocked jar)
# and can never reach a browser. The platform-wide absence of the JS-readable
# artifact itself is owned by tests/test_no_legacy_token_path.py.

_PRODUCT_ROOTS = (
    "apps",
    "client",
    "integrations",
    "menu-bar",
    "services",
    "tortoise",
    "website",
)
_SCAN_SKIP_DIRS = {
    "node_modules",
    "dist",
    ".git",
    "archive",
    ".worktrees",
    "vendor",
    ".wrangler",
    "build",
}
_SCAN_SUFFIXES = {".py", ".html", ".js", ".ts", ".tsx", ".jsx", ".mjs"}

# The session cookie's name as a bare literal. The registry carries it as a
# SYMBOL (`COOKIE_NAME`), so a copy has to name the cookie itself to be seen by
# the writer detector — as the deleted bridge and console adapter did.
_SESSION_COOKIE_LITERAL = "sb-tortoise-auth-token"

# A storage-adapter METHOD DEFINITION that takes a `key`, in any of the styles
# this repo uses (`setItem(key, value) {`, `setItem: (key: string) => {`,
# `setItem: function (key, value) {`, `setItem = function (key, value) {`).
#
# This matches the DEFINITION, not a use of it: a CALL SITE ends at `)`, which
# is followed by `;`, `.` or end-of-statement, so requiring the parameter list
# to CLOSE and then be followed by a body opener (`{` or `=>`) excludes it.
# #8701 review (P2, cycle 1): the prior `[:(=]` character class contained `(`,
# so a call like `s.setItem(key, v);` matched the `(` arm and was reported as
# an adapter copy — a false positive whose only legal resolution was to rename
# the argument (the registration pin `_extract_fn_body` still demands a real
# definition).
# #8701 review (P2, cycle 2): `[^)]*` still SPANNED a nested delimiter in the
# parameter text, so `store.setItem(key, () => { … })` reached the inner `)`
# and then the body opener and matched too — the same false positive via an
# ordinary JS call with a callback/object argument. The parameter text must
# not span `(`/`{`, so both delimiters are in the negated class as well.
# Pinned by test_router_detector_matches_definitions_not_call_sites.
_ROUTER_METHOD = re.compile(
    r"\b(?:setItem|removeItem)\s*(?::|=)?\s*(?:function\s*)?"
    r"\([^(){}]*\bkey\b[^(){}]*\)\s*(?:\{|=>)"
)
_COOKIE_ASSIGNMENT = re.compile(r"document\s*\.\s*cookie\s*=[^=]")


def _scan_code(path: Path) -> str:
    """File text with comments removed, so each detector matches CODE and not
    the prose that documents the removal — every deleted path here carries a
    comment naming it, and this suite's own header names the cookie.

    A `.py` file hosts the consent page's JS inside a string literal, so it gets
    only its FULL-LINE `#` comments removed: stripping JS comments from Python
    would eat a floor-division tail and could blank an inline script that starts
    later on that same line.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".py":
        return re.sub(r"(?m)^\s*#.*$", "", text)
    return _strip_js_comments(text)


def _scannable_sources() -> list[Path]:
    """Every product source file a browser- or page-facing adapter could live in.

    A root absent from this checkout is skipped (the app trees have moved
    between root and `apps/` before); `inspected` in the test below is what keeps
    that from silently shrinking the scan.
    """
    sources: list[Path] = []
    for root in _PRODUCT_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _SCAN_SUFFIXES:
                continue
            if any(part in _SCAN_SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
                continue
            sources.append(path)
    return sorted(sources)


def _adapter_copies() -> tuple[dict[Path, set[str]], int]:
    """`({copy: {detector, ...}}, files_inspected)` over the product sources."""
    copies: dict[Path, set[str]] = {}
    sources = _scannable_sources()
    for path in sources:
        code = _scan_code(path)
        hits: set[str] = set()
        if _ROUTER_METHOD.search(code) and _ROUTER_KEY_CMP.search(code):
            hits.add("router")
        if _SESSION_COOKIE_LITERAL in code and _COOKIE_ASSIGNMENT.search(code):
            hits.add("cookie-writer")
        if hits:
            copies[path] = hits
    return copies, len(sources)


def test_router_detector_matches_definitions_not_call_sites() -> None:
    """#8701 review (P2): `_ROUTER_METHOD` must match the adapter DEFINITION,
    not a call site.

    The old pattern's `[:(=]` character class contained `(`, so a call like
    `s.setItem(key, v);` matched and the file was reported as an unregistered
    copy. That is a false positive with no legal resolution: registering it
    cannot succeed because `test_key_identity_router_allows_only_the_session_key`
    resolves the entry through `_extract_fn_body`, which asserts a real
    `setItem`/`removeItem` definition. This test FAILS on the pre-fix regex
    (`old = re.compile(r"\\b(?:setItem|removeItem)\\s*[:(=]\\s*(?:function\\s*)?\\(?\\s*key\\b")`),
    which matches every call below; the fixed pattern requires the parameter
    list to close and be followed by a body opener.

    #8701 review (P2, cycle 2): a `[^)]*` parameter text still SPANNED a nested
    delimiter, so an ordinary `setItem(key, cb)` / `setItem(key, { … })` call
    whose argument list carries its own parens matched at the first inner `)`
    and was reported as a copy. The parameter text now forbids `(`/`{` as well
    (`[^(){}]*`), and the nested-paren call sites below pin it.
    """
    # Call sites: a use of the adapter, not the adapter itself.
    call_sites = (
        "s.setItem(key, v);",
        "s.removeItem(key);",
        "store.setItem(key, value);",
        "this.removeItem(key);",
        "s.setItem(\n  key,\n  v\n);",
        # #8701 review (P2, cycle 2): argument lists with NESTED parens/braces —
        # ordinary JS that matched the pre-cycle-2 `[^)]*` pattern.
        "store.setItem(key, () => { doThing(); });",
        "store.setItem(key, { onError: (e) => { log(e); } });",
        "wrap.removeItem(key, function (err) { report(err); });",
    )
    for code in call_sites:
        assert not _ROUTER_METHOD.search(code), (
            f"the detector matched a CALL SITE ({code!r}) — a file that merely "
            "uses a storage adapter would be reported as an unregistered copy, "
            "and registering it cannot succeed (#8701 review P2)"
        )

    # Definitions, in every style this repo uses — these MUST match.
    definitions = (
        "setItem(key, value) {",
        "removeItem(key) {",
        "setItem: function (key, value) {",
        "setItem: (key: string) => {",
        "setItem = function (key, value) {",
    )
    for code in definitions:
        assert _ROUTER_METHOD.search(code), (
            f"the detector missed a real adapter DEFINITION ({code!r}) — a new "
            "copy in that style would land unguarded (#5735)"
        )

    # The end-to-end ROUTER branch: a file that only CALLS an adapter and
    # contains an unrelated `key === <id>` comparison must not be flagged. That
    # combination is exactly the reviewer's false-positive vector (a call site
    # + a property access such as `t.key === key`) — with the regex fixed, the
    # method half no longer fires, so the branch stays silent.
    false_positive = "\n".join(
        [
            "widget.setItem(key, payload);",
            "if (t.key === key) { render(); }",
        ]
    )
    assert not (_ROUTER_METHOD.search(false_positive) and _ROUTER_KEY_CMP.search(false_positive)), (
        "a file that only USES an adapter (call site + a `key ===` comparison) "
        "was reported as a router copy — a false positive whose only legal fix "
        "is renaming the caller's argument (#8701 review P2)"
    )

    # #8701 review (P2, cycle 2): the same false positive via a callback/object
    # argument whose list holds its own parens — the vector the `[^)]*` pattern
    # missed. The method half must stay silent so the branch does not fire.
    nested_false_positive = "\n".join(
        [
            "widget.setItem(key, () => { render(); });",
            "if (t.key === key) { render(); }",
        ]
    )
    assert not (
        _ROUTER_METHOD.search(nested_false_positive)
        and _ROUTER_KEY_CMP.search(nested_false_positive)
    ), (
        "a file that only USES an adapter with a callback argument was reported "
        "as a router copy — no legal registration exists (#8701 review P2, "
        "cycle 2)"
    )


def test_every_storage_adapter_copy_is_registered() -> None:
    """#5735: `_ROUTER_CASES` is the contract's declared home — a copy of the
    adapter that is not registered there is not gated, and this fails until it
    is.

    Registration is not paperwork: the loop in
    `test_key_identity_router_allows_only_the_session_key` pins the registered
    copy's per-method polarity (exactly one `key !== <that file's session key>`
    comparison, and no string-shape predicate on `key`), so adding a copy here
    is what makes the contract actually bind it.
    """
    copies, inspected = _adapter_copies()
    registered = {path for path, _, _ in _ROUTER_CASES}

    # NON-VACUITY. A broken root list, a missing suffix (the platform's scripts
    # are .jsx/.ts/.html as well as .py) or a detector that no longer recognises
    # the one live copy would each report "no unregistered copies" while seeing
    # nothing — a dead gate reads exactly like a clean one.
    assert inspected > 100, (
        f"only {inspected} product sources scanned under {list(_PRODUCT_ROOTS)} — "
        "the root list or the glob is broken, so every absence proved below would "
        "pass vacuously"
    )
    assert OAUTH in copies and "router" in copies[OAUTH], (
        f"the one live adapter copy ({OAUTH.relative_to(REPO_ROOT)}) was NOT "
        "detected as a router — the detector is dead, so its silence proves nothing"
    )
    # #8701 review (P2): pin the WRITER detector too. `oauth.py` is the only
    # product source that hits it, so if `_SESSION_COOKIE_LITERAL` /
    # `_COOKIE_ASSIGNMENT` regress, `unregistered`, `stale` and `decorative`
    # all stay empty and the test above reads as clean while the whole
    # `cookie-writer` failure shape (#5735's "no routing — writes any key") goes
    # unwatched. This assertion is what makes a dead writer detector fail.
    assert OAUTH in copies and "cookie-writer" in copies[OAUTH], (
        f"the one live adapter copy ({OAUTH.relative_to(REPO_ROOT)}) was NOT "
        "detected as a cookie-writer — that detector is dead, so its silence "
        "proves nothing and a `document.cookie` writer that names the session "
        "cookie would land unregistered (#8701 review P2)"
    )

    unregistered = sorted(copies.keys() - registered)
    assert not unregistered, (
        "these product sources carry the key-routing storage-adapter contract but "
        "are NOT in `_ROUTER_CASES`, so nothing gates them (#5735):\n"
        + "\n".join(
            f"  {p.relative_to(REPO_ROOT)}  [{', '.join(sorted(copies[p]))}]" for p in unregistered
        )
        + "\n\nRoute the copy by key IDENTITY — exactly one `key !== <that file's "
        "session-key constant>` in each of setItem/removeItem, and no "
        "endsWith/startsWith/slice/charAt/includes/regex test on `key` — then add "
        "it to `_ROUTER_CASES` as (path, SESSION_KEY_SYMBOL, {'setItem': op, "
        "'removeItem': op}). A `cookie-writer` hit with no router has no legal "
        "registration: key-route it, or delete it. Do not rely on this list "
        "staying short — a copy of this contract that is not registered is a copy "
        "no gate can see (the #3496 duplication finding, #5735's residual half)."
    )

    stale = sorted(registered - copies.keys())
    assert not stale, (
        f"`_ROUTER_CASES` registers {[p.name for p in stale]} but no adapter was "
        "detected there — a stale entry is a registry that no longer describes the "
        "contract it declares. Drop the entry, or restore the detector if the copy "
        "is real and it was missed"
    )
    # (stale is empty by here, so every registered path IS a key of `copies`.)
    decorative = sorted(p for p in registered if "router" not in copies[p])
    assert not decorative, (
        f"`_ROUTER_CASES` registers {[p.name for p in decorative]} but that file "
        "carries no key-routing setItem/removeItem body, so the polarity "
        "assertions in test_key_identity_router_allows_only_the_session_key cannot "
        "bind it — the entry is decorative"
    )
