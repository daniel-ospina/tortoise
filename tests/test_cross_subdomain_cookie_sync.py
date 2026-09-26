"""Static sync test: the tortoise cross-subdomain session bridge must stay
byte-compatible with the other adapters that share its cookie contract
(issue #1225).

A session created on tortoise.premiselabs.co is persisted to the parent-domain
cookie `sb-tortoise-auth-token` by `website/assets/supabase-session.js`; the
OAuth consent page (`tortoise/oauth.py`) carries a faithful inline port of the
same adapter, and the retained blog-admin SPA
(`website/apps/blog-admin/src/lib/supabase.ts`) reads/writes the same cookie
name. Any drift between the copies — cookie name, domain, path, SameSite,
Secure, expiry, or the write/remove templates — silently recreates the exact bug
this issue fixes (post-signup redirect lands on the dashboard LOGIN screen).

#4054 deleted the dashboard's SESSION adapter from
`website/apps/dashboard/src/main.jsx` (the BFF owns the session now). main.jsx
KEEPS the host-conditional helpers (`isLocal`/`isPremiselabsHost`/`domainAttr`/
`secureAttr`) because it still writes the NON-SECRET `tt_claim_pending` marker to
the parent domain — those helpers must stay in parity, which is why `DASHBOARD`
remains in the helper/attribute sets as a MARKER writer, not a session adapter.
The session-cookie assertions that named the deleted adapter
(`COOKIE_NAME`/`getItem` escape/`SIZE_GUARD`/`setItem`/`removeItem`) are
retargeted at the surfaces that still carry them.

Pure static text assertions: no browser, no network. Source files, not bundles,
so regex anchoring to declaration patterns is reliable.

#3485 is deliberately NOT pinned by text here. Its read/migrate/store invariants
(the localStorage-only loop, expired-legacy displacement, corrupt-legacy
containment, fragment-strip ordering) are EXECUTED against the real script by
`website/apps/dashboard/src/supabaseSessionBridge.test.js` (behavioural, node:vm).
The fragment/store invariants are additionally executed by
tests/test_session_bridge_fragment_retention.py under a cookie-cap harness. The
dashboard stores no session at all.
#
# #4054 CORRECTION: an earlier revision of this note claimed the behavioural suite
tested the deleted dashboard ADAPTER and was deleted with it. That was FALSE —
the suite never referenced `main.jsx`; its only fixture was the RETAINED shared
bridge (`website/assets/supabase-session.js`), which #4054 keeps and still serves
from premiselabs.co. A retained artifact with no executing test is how it rots,
so the suite is RESTORED (the file was deleted with the dashboard's COPY of the
bridge, not with an artifact that went away).
#
# Nor is the bridge kept because blog-admin loads it: blog-admin ships its OWN
# adapter and does not load or import the shared script. It is kept because
# `tortoise/oauth.py`'s live consent-page client is a faithful inline PORT of its
# adapter (pinned here), the dashboard's live `tt_claim_pending` marker helpers
# mirror its host-conditional helpers (pinned here), and it is the subject of the
# #3503 fragment-retention gate.

⚠️ THIS MODULE PINS A DESIGN THAT IS BEING RETIRED (#3501). The parent-domain
cookie is a REVERSAL, not a fallback: it is JavaScript-readable by construction,
which is the property #3501 exists to remove. This suite stays green for the
pages not yet migrated, and each migration must REMOVE its page from `PAGES`
rather than delete its assertions — that way the remaining legacy surface is
always listed explicitly, and the end state (an empty `PAGES`) doubles as the
"no page is on the legacy bridge" proof. #4054 reached that end state: `PAGES`
is empty and `test_no_page_is_left_on_the_legacy_bridge` inverts the old
per-page wiring assertion into the absence proof. See #3559 for the backlog.
"""

from __future__ import annotations

import re
from pathlib import Path

# #3786: the session-bridge toolchain contract is owned by the sibling harness —
# one guard, not a second variant. It FAILS (never skips) when node is absent.
from tests.test_session_bridge_fragment_retention import _require_node

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED = REPO_ROOT / "website" / "assets" / "supabase-session.js"
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"

# #4054: main.jsx's SESSION adapter is deleted, but the file still writes the
# non-secret `tt_claim_pending` parent-domain marker with the same
# host-conditional helpers (isLocal/isPremiselabsHost/domainAttr/secureAttr). It
# stays in the helper/attribute sets as a MARKER writer, not a session adapter —
# the session-cookie assertions that referenced the deleted adapter (COOKIE_NAME,
# the getItem regex escape, SIZE_GUARD, setItem/removeItem) are retargeted.
DASHBOARD = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"

# #4054: the auth page (signup.html = `/auth`) moved to the app project. It is
# BFF-migrated for SESSION handling, but it still has its OWN inline
# `tt_claim_pending` marker writer (`setClaimPendingMarker`) — re-implemented
# in-page when the bridge was removed, because the dashboard's claim card still
# reads that marker. It uses INLINE hostname/protocol conditions rather than the
# shared `domainAttr()`/`secureAttr()` helpers, so it cannot join the `surfaces`
# list below (whose loop asserts the helper calls); its contract is pinned by the
# dedicated `test_signup_marker_is_host_conditional` instead.
SIGNUP_PAGE = (
    REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "signup.html"
)

# The deliberately-retained legacy surface (#4054): the blog-admin SPA reads and
# writes the same parent-domain cookie on the `premise-labs` project (its API is
# on `tortoise.*` and `__Host-session` is host-only). It is a DIFFERENT adapter
# design (cookieScope() instead of domainAttr()/secureAttr(); no size guard), so
# it is pinned on the properties it SHARES — the cookie name and the
# parent-domain scope — not helper-by-helper parity. Its own behavioural suite is
# website/apps/blog-admin/src/lib/supabase-auth-storage.test.ts.
BLOG_ADMIN = (
    REPO_ROOT / "website" / "apps" / "blog-admin" / "src" / "lib" / "supabase.ts"
)

# The exact script tag a page uses to load the shared bridge.
BRIDGE_SCRIPT = 'src="/assets/supabase-session.js"'

# #3501/#4054: NO page is on the legacy bridge any more. Each migration removed
# its page from this list rather than deleting the wiring assertions:
# welcome.html left in #3501; signup.html MOVED to the app project and re-plumbed
# to the BFF; signin.html was DELETED (301'd to /auth on every host — see
# `_redirects` and `functions/_middleware.ts`); the dashboard index.html dropped
# its script tag. The empty list is the "no page is on the legacy bridge" proof,
# and `test_no_page_is_left_on_the_legacy_bridge` scans for a reintroduction
# so the emptiness is not vacuous. See #3559 for the backlog.
PAGES: list[Path] = []


def _read(path: Path) -> str:
    assert path.exists(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    """Blank out `//` and `/* */` comments so a text assertion below matches
    CODE, not prose. A comment quoting the pre-#3930 destination must neither
    red this test nor satisfy its positive pin (#3930 review).

    A PARTIAL port of src/testSupport.js::stripComments, not a mirror: it keeps
    that function's `:`-prefixed-`//` guard (so `https://` inside a string
    survives) but has no quote/template awareness — a `//` inside a `'`/`"`/` `
    literal IS treated as a comment here, where the JS original preserves it.
    Verified byte-identical on today's main.jsx; do not reuse this for a file
    where `//` appears inside a string.
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


def _extract_helper(text: str, name: str) -> str:
    """Extract the full declaration source of a named helper from either
    adapter style: ES5 `var f = function () { ... };` (shared bridge) or ES6
    `const f = () => { ... }` / `const f = () => (expr)` (oauth.py inline copy).
    Returns from the declaration keyword through the matching close brace/paren
    (plus a trailing `;` if present)."""
    # f-string braces vs regex char class — build the opener pattern without
    # interpolation so `([\({])` stays literal
    m = re.search(
        rf"(?:const|var)\s+{re.escape(name)}\s*=\s*(?:function\s*)?\(\s*\)\s*(?:=>\s*)?"
        + r"([\({])",
        text,
    )
    assert m, f"missing helper declaration: {name}"
    start = m.start()
    opener = m.group(1)
    close = {"(": ")", "{": "}"}[opener]
    depth = 0
    i = m.end() - 1
    while i < len(text):
        c = text[i]
        if c == opener:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        raise AssertionError(f"unbalanced helper declaration: {name}")
    end = i + 1
    if end < len(text) and text[end] == ";":
        end += 1
    return text[start:end]


def _extract_fn_body(text: str, name: str) -> str:
    """Extract a named function/method body (any style: `name(key, value) {`,
    `name: function (key, value) {`, `function name() {`, or
    `var name = function (...) {`) up to the matching close brace, including
    the signature line. Does NOT match call sites (`name(...);` — no `{`)."""
    m = re.search(
        rf"(?:var\s+|function\s+)?{re.escape(name)}\s*"
        rf"(?:(?:\:\s*function\s*|\=)\s*(?:function\s*)?)?\([^)]*\)\s*(?:=>\s*)?{{",
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


def _normalize_helper(src: str) -> str:
    """Collapse a helper declaration to a comparable token stream so the ES5
    shared-bridge form (`var f = function () { return X; };`), the ES6 oauth.py
    form (`const f = () => X` / block body), and the dashboard main.jsx marker
    helpers compare EQUAL on the logic they encode, ignoring formatting/quote/
    semicolon drift. Any semantic drift (changed hostname list, dropped isLocal
    check, flipped operator) changes the normalized form."""
    s = re.sub(r"\bvar\b", "const", src)
    # strip // and /* */ comments (comments differ between the copies and are
    # not part of the encoded logic)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"//[^\n]*", " ", s)
    # normalize the signature: ES5 `function ()` == ES6 `() =>`
    s = s.replace("function ()", "() =>").replace("function()", "() =>")
    # drop semicolons BEFORE unwrapping so oauth's trailing `;` can't defeat
    # the `$` anchor on the expression-body regex
    s = s.replace(";", "")
    # unwrap expression bodies: `() => (X)` → `() => X` (dashboard/oauth style)
    s = re.sub(r"\(\s*\)\s*=>\s*\((.*?)\)$", r"() => \1", s, flags=re.DOTALL)
    # unwrap single-return block bodies: `() => { return X; }` → `() => X`
    # (shared-bridge style; multi-statement block bodies stay as-is)
    s = re.sub(r"\(\s*\)\s*=>\s*\{\s*return\s+(.*?)\s*\}", r"() => \1", s, flags=re.DOTALL)
    s = s.replace('"', "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def test_adapters_share_host_conditional_attribute_logic() -> None:
    """#1857: the host-conditional Domain/Secure logic must be SEMANTICALLY
    identical across every copy (shared bridge, dashboard main.jsx marker
    writer, oauth.py inline). A mere presence check ("domainAttr" in text)
    catches a MISSING helper but not DRIFT in one copy's logic — the exact bug
    class #1857 is about (a copy hardcoded Domain+Secure while the shared bridge
    was conditional, and the string-presence test didn't catch it). Normalize
    each helper declaration across styles and assert equality; then assert the
    conditional helpers are actually WIRED into the write/remove templates, not
    just declared.

    #4054: main.jsx is no longer a SESSION adapter but still writes the
    non-secret claim marker with these helpers, so the parity must hold there
    too. blog-admin's adapter uses its own cookieScope() and is deliberately
    outside this helper-parity set (pinned by its own test below).
    """
    helpers = ("isLocal", "isPremiselabsHost", "domainAttr", "secureAttr")
    copies = [("shared", _read(SHARED)), ("dash", _read(DASHBOARD)), ("oauth", _read(OAUTH))]
    for name in helpers:
        normalized = [
            (label, _normalize_helper(_extract_helper(text, name)))
            for label, text in copies
        ]
        first = normalized[0][1]
        for label, n in normalized[1:]:
            assert n == first, (
                f"{name} conditional logic drifted across adapters:\n"
                f"  shared: {normalized[0][1]}\n"
                f"  {label}:  {n}\n"
                "KEEP website/assets/supabase-session.js, "
                "website/apps/dashboard/src/main.jsx and tortoise/oauth.py in sync."
            )
    # the conditionals must be wired into the actual write/remove templates
    # (declared-but-unused helpers pass the parity check but do nothing)
    for label, text in ((SHARED, _read(SHARED)), (DASHBOARD, _read(DASHBOARD)), (OAUTH, _read(OAUTH))):
        assert "domainAttr()" in text, f"{label}: domainAttr() not wired into templates"
        assert "secureAttr()" in text, f"{label}: secureAttr() not wired into templates"


def test_adapters_declare_the_same_session_cookie_identity() -> None:
    """Every adapter that touches the SESSION cookie must declare the same
    cookie name + parent domain (anchored to declarations so comment text
    cannot false-match).

    #4054: the dashboard's session adapter is deleted, so main.jsx no longer
    declares COOKIE_NAME; it is asserted only on the COOKIE_DOMAIN it still uses
    for the non-secret claim marker. Session-cookie identity is now shared by
    the shared bridge, the oauth.py inline port, and the retained blog-admin
    adapter (whose constant is STORAGE_KEY).
    """
    shared = _read(SHARED)
    cookie_pat = re.compile(r"(?:const|var)\s+COOKIE_NAME\s*=\s*['\"]([^'\"]+)['\"]")
    domain_pat = re.compile(r"(?:const|var)\s+COOKIE_DOMAIN\s*=\s*['\"]([^'\"]+)['\"]")

    sm = cookie_pat.search(shared)
    assert sm, f"{SHARED}: missing COOKIE_NAME"
    om = cookie_pat.search(_read(OAUTH))
    assert om, f"{OAUTH}: missing COOKIE_NAME"
    assert sm.group(1) == om.group(1), (
        f"COOKIE_NAME drift: {SHARED}={sm.group(1)!r} vs {OAUTH}={om.group(1)!r}"
    )
    sdm = domain_pat.search(shared)
    odm = domain_pat.search(_read(OAUTH))
    assert sdm, f"{SHARED}: missing COOKIE_DOMAIN"
    assert odm, f"{OAUTH}: missing COOKIE_DOMAIN"
    assert sdm.group(1) == odm.group(1), (
        f"COOKIE_DOMAIN drift: {SHARED}={sdm.group(1)!r} vs {OAUTH}={odm.group(1)!r}"
    )
    assert "sb-tortoise-auth-token" in shared
    assert ".premiselabs.co" in shared

    # The retained blog-admin adapter declares the SAME cookie name under its own
    # constant (STORAGE_KEY) and the same parent domain — a drift here means the
    # console stops seeing the session the rest of the site writes.
    blog = _read(BLOG_ADMIN)
    bm = re.search(r"export\s+const\s+STORAGE_KEY\s*=\s*['\"]([^'\"]+)['\"]", blog)
    assert bm, f"{BLOG_ADMIN}: missing STORAGE_KEY"
    assert bm.group(1) == sm.group(1), (
        f"blog-admin must persist the SAME cookie name: STORAGE_KEY={bm.group(1)!r} "
        f"vs COOKIE_NAME={sm.group(1)!r}"
    )
    assert ".premiselabs.co" in blog, "blog-admin must scope the cookie to the parent domain"

    # main.jsx still writes the non-secret claim marker to the parent domain; it
    # must keep the same COOKIE_DOMAIN (it no longer declares COOKIE_NAME).
    dash = _read(DASHBOARD)
    ddm = domain_pat.search(dash)
    assert ddm, f"{DASHBOARD}: missing COOKIE_DOMAIN"
    assert ddm.group(1) == sdm.group(1), (
        f"COOKIE_DOMAIN drift: {SHARED}={sdm.group(1)!r} vs {DASHBOARD}={ddm.group(1)!r}"
    )

    # #1704: the OAuth consent page embeds a third copy of the adapter — it
    # must declare the full SupportedStorage interface + the size guard (a
    # missing setItem/COOKIE_DOMAIN breaks prod cookie writes at runtime).
    oauth_text = _read(OAUTH)
    assert "setItem(key, value) {" in oauth_text
    assert "removeItem(key) {" in oauth_text
    assert "SIZE_GUARD" in oauth_text and "provider_token" in oauth_text


def test_only_the_shared_regex_reader_needs_a_key_escape() -> None:
    """#1860 (P3-3): a cookie-read built from a RegExp must escape the key, or
    regex metacharacters ([.*+?^${}()|\\]) in a key become pattern — benign for
    today's keys, latent drift.

    #4054 retarget: this used to assert PARITY between the shared bridge's
    readCookie and the dashboard's inline getItem. The dashboard session adapter
    is deleted, so there is no second REGEX reader left to compare against; the
    invariant is now split into (a) the one remaining regex reader escapes, and
    (b) the remaining non-shared readers match the key by exact equality, so
    they cannot be metacharacter-affected at all.
    """
    shared = _read(SHARED)
    escape = "key.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')"
    # normalize quotes (a cosmetic double-quote refactor is NOT drift)
    assert escape in shared.replace('"', "'"), "shared readCookie must escape the key"

    # (b) oauth.py and blog-admin read the cookie by an exact key comparison
    # (`=== key` / `=== STORAGE_KEY`) instead of building a pattern.
    oauth = _read(OAUTH).replace('"', "'")
    assert "p.slice(0, eq) === key" in oauth, (
        "oauth.py's cookie reader must match the key by exact equality"
    )
    assert "name === STORAGE_KEY" in _read(BLOG_ADMIN), (
        "blog-admin's cookie reader must match the key by exact equality"
    )


def test_auth_bounce_preserves_search_params() -> None:
    """#1860 (P3-5): the auth bounce must preserve the search params — /auth's
    OAuth-error banner reads ?error=... — plus the #1909 error fragment.

    #4054 retarget: the bridge's global `window.bounceToAuth` is gone; main.jsx
    now owns a local same-origin bounce, and installs no cross-origin fallback.
    The param-preservation intent is unchanged, so it is asserted on the two call
    sites that carry it; the old degraded `https://tortoise.premiselabs.co/auth`
    fallback assertion is REMOVED because that path no longer exists.

    #3930 retarget: the destination literal moved into the pure `authBounceTarget`
    module (which also carries the requested PATHNAME as `/auth`'s `next`). The
    target is still a same-origin `/auth` navigation; the assertion follows the
    shape so a revert of either half reds this test. `authBounce.test.js` and
    `test_admin_return_to.py` own the behaviour.
    """
    dash = _read(DASHBOARD)
    # Both bounce sites (the 401-provision path and the mount gate) pass the
    # search string and the #1909 error fragment through the local helper.
    assert dash.count("bounceToAuth(window.location.search, oauthErrorHash())") >= 2, (
        "the auth bounce must preserve search params + the #1909 error fragment "
        "(both the 401-provision and the mount-gate bounce)"
    )
    # The bounce target is same-origin now — the cross-origin bridge hop (and its
    # separate fallback) is gone. It is built by the pure module from THIS
    # document's pathname, so the destination can never name another origin.
    # Whitespace-tolerant: a prettier re-wrap of the call must not red this.
    # Comments are stripped first: `_read` returns raw source, so an assertion
    # on it can be satisfied (or reddened) by PROSE. Ported from
    # src/testSupport.js::stripComments, same `:`-prefixed-`//` guard for URLs.
    norm = _strip_js_comments(dash).replace('"', "'")
    assert re.search(
        r"authBounceTarget\(\{\s*pathname:\s*window\.location\.pathname,\s*search,\s*errorHash:\s*hash\s*\}\)",
        norm,
    ), (
        "the bounce must be the same-origin /auth navigation that consumes the "
        "preserved search/hash (and, since #3930, the pathname)"
    )
    # The pre-#3930 destination, matched as CODE — see _strip_js_comments.
    assert not re.search(r"location\.replace\(\s*['\"]/auth['\"]\s*\+", norm), (
        "the pathname-dropping destination is back (#3930)"
    )


def test_cookie_write_templates_wire_conditionals_in_every_adapter() -> None:
    """#1857 (code-review P2-1/2/3): the host-conditional helpers must be wired
    into EVERY cookie-writing template in EVERY adapter — not merely present in
    the file. A partial revert of ONE template (e.g. setItem back to a hardcoded
    `Domain=${COOKIE_DOMAIN}; Secure`) while another template keeps the
    conditionals passes the helper-parity + file-wide presence checks but
    silently recreates the exact bug this issue fixes (cookie dropped on
    localhost/previews). Assert per-function: both helpers called, Path= +
    SameSite=Lax present, no hardcoded `Domain=` / `; Secure` literal in the
    template body (the only legal Domain/Secure come via domainAttr()/
    secureAttr()), and the correct expiry token per kind (set→Expires=,
    remove→Max-Age=0)."""
    surfaces = [
        # (adapter text, [(fn, kind)]) — kind 'set' requires Expires=,
        # 'remove' requires Max-Age=0. clearStoredSession and
        # setLastAuthMethod are the shared bridge's OTHER parent-domain
        # cookie writes — a revert there recreates the #1857 class
        # (stale session survives logout / last-auth cookie dropped).
        (SHARED, [
            ("setItem", "set"),
            ("removeItem", "remove"),
            ("clearStoredSession", "remove"),
            ("setLastAuthMethod", "set"),
        ]),
        # #4054: main.jsx's SESSION adapter (setItem/removeItem) is deleted. Its
        # only remaining document.cookie writes are the non-secret
        # tt_claim_pending marker pair, which must stay host-conditional (#1857)
        # — and the completeness scan below still covers BOTH writes, so a new
        # unguarded cookie write in main.jsx fails here.
        (DASHBOARD, [
            ("setClaimPendingMarker", "set"),
            ("clearClaimPendingMarker", "remove"),
        ]),
        (OAUTH, [("setItem", "set"), ("removeItem", "remove")]),
    ]
    for path, fns in surfaces:
        text = _read(path)
        for fn, kind in fns:
            body = _extract_fn_body(text, fn)
            assert "domainAttr()" in body, f"{path.name}:{fn}: missing domainAttr() call"
            assert "secureAttr()" in body, f"{path.name}:{fn}: missing secureAttr() call"
            assert "Path=" in body, f"{path.name}:{fn}: missing Path="
            assert "SameSite=Lax" in body, f"{path.name}:{fn}: missing SameSite=Lax"
            assert "Domain=" not in body, (
                f"{path.name}:{fn}: hardcoded Domain= in template — use domainAttr()"
            )
            assert "; Secure" not in body, (
                f"{path.name}:{fn}: hardcoded Secure in template — use secureAttr()"
            )
            if kind == "set":
                assert "Expires=" in body, f"{path.name}:{fn}: missing Expires="
            else:
                assert "Max-Age=0" in body, f"{path.name}:{fn}: missing Max-Age=0"
    # Completeness: EVERY document.cookie write in the three adapter files must
    # fall inside one of the watched bodies above. Auto-catches a future
    # unwatched write (the cycle-1/2/3 finding class) without hand-maintaining
    # the surface list.
    for path, fns in surfaces:
        text = _read(path)
        spans = []
        for fn, _kind in fns:
            body = _extract_fn_body(text, fn)
            start = text.index(body)
            spans.append((start, start + len(body)))
        spans.sort()
        for wm in re.finditer(r"document\.cookie\s*=", text):
            wpos = wm.start()
            assert any(a <= wpos < b for a, b in spans), (
                f"{path.name}: document.cookie write at offset {wpos} is NOT inside a "
                "watched function body — add it to the surfaces list"
            )


# #4054: `test_signup_marker_is_host_conditional` was RETARGETED, not removed.
# signup.html moved to the app project, and it still writes the `tt_claim_pending`
# marker — so the #1857 invariant (host-conditional Domain, https-conditional
# Secure, exactly one of each) still has a live subject. An earlier revision of
# this migration deleted the test on the false premise that the writer no longer
# existed; a VGATE reviewer caught that the writer is still called from the
# ANON_TEAM_NO_OWNER sign-in funnel. The test now points at the moved file.
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
    # host-conditional domain: the .premiselabs.co value must be gated on a
    # hostname check (an unconditional `; Domain=.premiselabs.co` write would
    # drop the marker on localhost/previews — the #1857 bug class)
    # normalize to single quotes so either JS quote style matches
    norm = body.replace('"', "'")
    assert "endsWith('.premiselabs.co')" in norm
    assert "? '; Domain=.premiselabs.co'" in norm
    # exactly ONE occurrence each — a hardcode that leaves dead conditional
    # code in place would otherwise keep the ternary literals present
    assert norm.count("; Domain=.premiselabs.co") == 1
    # https-conditional Secure: gated on protocol, not unconditional
    assert "protocol === 'https:'" in norm
    assert "? '; Secure'" in norm
    assert norm.count("; Secure") == 1
    assert "SameSite=Lax" in body
    assert "Expires=" in body

# The writer is also REACHED — a correct-but-uncalled function would satisfy the
# assertions above while silently dropping cross-origin claim routing.
def test_the_signup_marker_writer_is_actually_called() -> None:
    """#4054: pin the CALL SITE, not just the body shape.

    The ANON_TEAM_NO_OWNER sign-in funnel sets the marker so the dashboard's
    claim card can pick the visitor up. Deleting the call (or the marker name)
    would leave the body assertions green and the routing broken — the exact
    failure mode that deleting this test's sibling invited.

    NOT a bare `"setClaimPendingMarker(" in text` check: that string also appears
    in the FUNCTION DECLARATION, so it stayed true after the call site was
    deleted. A first draft of this test made exactly that mistake and was caught
    by its own mutation control (removing the call left it green). The match must
    therefore exclude a preceding `function` keyword.
    """
    text = _read(SIGNUP_PAGE)
    calls = [
        m
        for m in re.finditer(r"(?<![\w.])setClaimPendingMarker\s*\(", text)
        # `function setClaimPendingMarker(` is the DECLARATION, not a call.
        if not re.search(r"function\s+$", text[max(0, m.start() - 40) : m.start()])
    ]
    assert calls, (
        "setClaimPendingMarker is DECLARED but never CALLED — the marker would "
        "never be set, so cross-origin claim routing silently stops working"
    )
    # Scope the name check to the WRITER BODY: `tt_claim_pending` also appears in
    # the page's READ sites, so a file-wide check stayed green even after the
    # marker the writer emits was renamed (caught by this test's own mutation
    # control). What matters is the name the WRITER writes.
    body = _extract_fn_body(text, "setClaimPendingMarker")
    assert "tt_claim_pending" in body, (
        "the writer no longer emits the tt_claim_pending marker the dashboard reads"
    )


def test_blog_admin_adapter_keeps_host_conditional_cookie_scope() -> None:
    """#4054: blog-admin is the one deliberately-retained legacy surface. Its
    adapter is an independent port (cookieScope() rather than
    domainAttr()/secureAttr()), so it is not in the helper-parity set — but it
    writes the SAME parent-domain cookie, so the #1857 property still applies:
    the Domain attribute is gated on a premiselabs host and Secure on https, and
    a non-matching domain is never written (which would silently drop the cookie
    and break the console).

    The adapter's own behavioural suite
    (website/apps/blog-admin/src/lib/supabase-auth-storage.test.ts) covers the
    read/write/clear semantics; this pins the host-conditional SHAPE statically,
    so a hardcoded `domain=.premiselabs.co` cannot slip in unnoticed.
    """
    blog = _read(BLOG_ADMIN)
    assert "function cookieScope()" in blog, "blog-admin must build the scope conditionally"
    # The leading-dot boundary: `host.endsWith('premiselabs.co')` also matched
    # evilpremiselabs.co, where the browser REJECTS the Domain attribute.
    assert "host === 'premiselabs.co' || host.endsWith('.premiselabs.co')" in blog, (
        "blog-admin's parent-domain check must keep the leading-dot boundary"
    )
    assert "parts.push('domain=.premiselabs.co')" in blog, (
        "the Domain attribute must be pushed only inside the premiselabs-host branch"
    )
    assert "parts.push('secure')" in blog, "Secure must be pushed only over https"
    assert "window.location.protocol === 'https:'" in blog, (
        "Secure must be gated on the protocol"
    )


def test_adapters_write_and_remove_cookie_with_same_attributes() -> None:
    """Attribute-sequence drift (Path/SameSite/Secure/Max-Age/expiry) breaks a
    sibling reader even when the constants match.

    #4054 retarget: parity is between the two copies that still implement the
    SESSION adapter — the shared bridge and the oauth.py inline port. The
    dashboard's copy is deleted (main.jsx keeps only the claim marker, pinned
    above), and blog-admin's is an independent design (see its own test).
    """
    shared = _read(SHARED)
    oauth = _read(OAUTH)
    # write template: key=encoded value + domain + path + SameSite=Lax + Secure + Expires
    assert "SameSite=Lax" in shared and "SameSite=Lax" in oauth
    # Path VALUE must be '/' in both — a Path drift silently breaks the bridge
    path_pat = re.compile(r"(?:const|var)\s+COOKIE_PATH\s*=\s*['\"]([^'\"]+)['\"]")
    sm = path_pat.search(shared)
    om = path_pat.search(oauth)
    assert sm and sm.group(1) == "/", f"shared COOKIE_PATH must be '/': {sm.group(1) if sm else None}"
    assert om and om.group(1) == "/", f"oauth COOKIE_PATH must be '/': {om.group(1) if om else None}"
    # Secure + Max-Age=0 removal (both always emit these; localhost omission is
    # a separate branch inside the shared file, not the template)
    assert "Secure" in shared and "Secure" in oauth
    assert "Max-Age=0" in shared and "Max-Age=0" in oauth
    # encode/decode round-trip
    assert "encodeURIComponent" in shared and "encodeURIComponent" in oauth
    assert "decodeURIComponent" in shared and "decodeURIComponent" in oauth
    # 7-day expiry parity
    assert "7 * 24 * 3600 * 1000" in shared and "7 * 24 * 3600 * 1000" in oauth
    # storageKey parity — the cookie name written/read must be the same on both sides
    assert "storageKey" in shared and "storageKey" in oauth


def test_shared_script_syntax() -> None:
    """The wiring tests are string-presence based — a parse-error'd shared
    script would pass them. `node --check` is the only thing that parses it.

    #3786: a missing `node` used to SKIP here — green, exit 0, zero coverage,
    which is indistinguishable from passing. It now FAILS by name unless the
    explicit SESSION_BRIDGE_ALLOW_NO_TOOLCHAIN=1 opt-out is set, reusing the
    session-bridge toolchain contract from the sibling harness."""
    import subprocess

    _require_node()
    subprocess.run(["node", "--check", str(SHARED)], check=True)


def test_adapters_share_size_guard_and_localhost_handling() -> None:
    """Size-guard parity: BOTH session adapters must strip provider tokens when
    the cookie would exceed the 4096-byte cap. #1835: a Google OAuth session
    (provider_token ~1200 chars + full identity, ~5012 encoded bytes) exceeds
    the cap and is silently rejected, so every session adapter must strip
    provider tokens exactly like the shared factory. #1857: the adapters ALSO
    degrade off-premiselabs (host-conditional Domain/Secure), so localhost
    handling is not shared-file-only.

    #4054 retarget: the dashboard's session adapter is deleted, so size-guard
    parity is between the shared bridge and the oauth.py port. main.jsx keeps
    only its localhost-aware helpers for the claim marker (asserted here); the
    retained blog-admin adapter is an independent design with no size guard, so
    it is pinned on its cookie scope, not on this contract.
    """
    shared = _read(SHARED)
    oauth = _read(OAUTH)
    assert "localhost" in shared and "127.0.0.1" in shared
    assert "localhost" in oauth and "127.0.0.1" in oauth  # #1857
    assert "premiselabs.co" in shared
    # size guard strips provider tokens when the cookie would exceed the cap
    # — required in BOTH session adapters (#1835 parity)
    for path, text in ((SHARED, shared), (OAUTH, oauth)):
        assert "SIZE_GUARD" in text, f"{path}: missing SIZE_GUARD"
        assert "provider_token" in text, f"{path}: size guard must strip provider_token"
        assert "provider_refresh_token" in text, (
            f"{path}: size guard must strip provider_refresh_token too"
        )
        assert "SIZE_GUARD + 100" in text, (
            f"{path}: must warn only when still over SIZE_GUARD + 100"
        )
    # main.jsx keeps the host-conditional helpers for the marker, so it must
    # still recognise the local origins.
    dash = _read(DASHBOARD)
    assert "localhost" in dash and "127.0.0.1" in dash


def test_no_page_is_left_on_the_legacy_bridge() -> None:
    """#3501/#4054: NO served HTML page may load the cross-subdomain bridge.

    This REPLACES the per-page wiring assertion for `PAGES`. A dropped script
    tag or an unwired page used to pass the constant checks AND return the
    login-wall bug, so the wiring was asserted directly; the retirement protocol
    then emptied `PAGES` page by page (signup.html moved to the app project;
    signin.html deleted; welcome.html left in #3501; the dashboard index.html
    dropped its script tag). With the list empty the old loop would pass
    vacuously, so the assertion is INVERTED into the proof the module docstring
    names: scan every HTML page under `website/` (build output excluded) for the
    bridge script tag and fail on any hit. A page that later re-arms the bridge
    is caught here, not silently ignored.
    """
    assert PAGES == [], (
        "PAGES is non-empty — a page is back on the legacy bridge; restore the "
        "per-page wiring assertions rather than listing it without them"
    )
    skip = {"node_modules", "dist", "vendor", ".wrangler", ".git"}
    website = REPO_ROOT / "website"
    pages = [
        p
        for p in website.rglob("*.html")
        if not any(part in skip for part in p.relative_to(website).parts)
    ]
    # Non-vacuity: a broken glob must not read as "no page on the bridge".
    assert len(pages) > 10, (
        f"only {len(pages)} HTML pages scanned under {website} — the glob is "
        "broken, so this absence proof would pass vacuously"
    )
    offenders = [
        p.relative_to(REPO_ROOT) for p in pages if BRIDGE_SCRIPT in _read(p)
    ]
    assert not offenders, (
        "these pages still load the legacy cross-subdomain bridge:\n"
        + "\n".join(f"  {o}" for o in offenders)
        + "\n\nThe BFF owns the session now; a page that loads the shared bridge "
        "re-arms the JS-readable cross-subdomain cookie #3501 exists to remove."
    )


# ── #1511 shared gate helpers ────────────────────────────────────────────────


def test_shared_helpers_present() -> None:
    """The #1511 auth-gate helpers (readValidSession/clearStoredSession/
    getLastAuthMethod/setLastAuthMethod/bounceToAuth) exist on the shared
    bridge + window, with the strict validity predicate."""
    text = _read(SHARED)
    for fn in ("readValidSession", "clearStoredSession",
               "getLastAuthMethod", "setLastAuthMethod", "bounceToAuth",
               "storeSession"):
        assert f"var {fn} = function" in text, f"missing helper {fn}"
        assert f"window.{fn} = {fn}" in text, f"missing window export {fn}"
    # Strict validity: missing OR past expires_at = INVALID (presence ≠ auth).
    assert "!s.expires_at || s.expires_at * 1000 <= Date.now()" in text


def test_dashboard_does_not_ship_the_shared_bridge_copy() -> None:
    """#4054: the dashboard is BFF-migrated, so it must NOT ship a copy of the
    shared bridge — re-adding `public/assets/supabase-session.js` would re-arm
    the JS-readable cross-subdomain session the migration removed (vite copies
    public/ verbatim into the deployed dist/).

    REPLACES the byte-identity assertion for that copy: with the file deleted the
    invariant is now its ABSENCE, not its content, so the check is inverted
    rather than dropped. The shared file itself is the retained bridge and is
    read by every test above, so a deletion there still fails loudly.
    """
    public_copy = (
        REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "assets" / "supabase-session.js"
    )
    assert not public_copy.exists(), (
        "the dashboard public/ copy of the shared bridge is back — the dashboard "
        "is BFF-migrated and must not ship a JS-readable session bridge (#4054)"
    )
    assert SHARED.exists(), f"missing retained shared bridge: {SHARED}"
