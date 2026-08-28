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

from __future__ import annotations  # noqa: I001

import re
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED = REPO_ROOT / "website" / "assets" / "supabase-session.js"
DASHBOARD = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"

PAGES = [
    REPO_ROOT / "website" / "signup.html",
    REPO_ROOT / "website" / "signin.html",
    REPO_ROOT / "website" / "welcome.html",
    # #1511: the dashboard loads the shared script + gate helpers (its gate
    # never calls createTortoiseSupabaseClient — the client is built in
    # main.jsx — so the wiring assertion below relaxes the factory check for
    # this entry).
    REPO_ROOT / "website" / "apps" / "dashboard" / "index.html",
]


def _read(path: Path) -> str:
    assert path.exists(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def _extract_helper(text: str, name: str) -> str:
    """Extract the full declaration source of a named helper from either
    adapter style: ES5 `var f = function () { ... };` (shared bridge), ES6
    `const f = () => { ... }` / `const f = () => (expr)` (dashboard main.jsx),
    or the oauth.py inline copy. Returns from the declaration keyword through
    the matching close brace/paren (plus a trailing `;` if present)."""
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
    shared-bridge form (`var f = function () { return X; };`), the ES6
    dashboard form (`const f = () => X` / block body), and the oauth.py inline
    copy compare EQUAL on the logic they encode, ignoring formatting/quote/
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
    identical across all THREE adapter copies (shared bridge, dashboard
    main.jsx, oauth.py inline). A mere presence check ("domainAttr" in text)
    catches a MISSING helper but not DRIFT in one adapter's logic — the exact
    bug class #1857 is about (dashboard hardcoded Domain+Secure while the
    shared bridge was conditional, and the string-presence test didn't catch
    it). Normalize each helper declaration across styles and assert equality;
    then assert the conditional helpers are actually WIRED into the write/
    remove templates, not just declared."""
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


def test_shared_adapter_declares_dashboard_cookie_identity() -> None:
    """Both adapters must declare the same cookie name + domain (anchored to
    const declarations so comment text can't false-match)."""
    for path, other in ((SHARED, DASHBOARD), (DASHBOARD, SHARED), (OAUTH, SHARED)):
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
    # #1704: the OAuth consent page embeds a third copy of the adapter — it
    # must declare the full SupportedStorage interface + the size guard (a
    # missing setItem/COOKIE_DOMAIN breaks prod cookie writes at runtime).
    oauth_text = _read(OAUTH)
    assert "setItem(key, value) {" in oauth_text
    assert "removeItem(key) {" in oauth_text
    assert "SIZE_GUARD" in oauth_text and "provider_token" in oauth_text


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
        (DASHBOARD, [
            ("setItem", "set"),
            ("removeItem", "remove"),
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


def test_signup_marker_is_host_conditional() -> None:
    """#1857 (code-review P2, cycle 3): signup.html has its OWN inline
    tt_claim_pending marker writer (setClaimPendingMarker) that shares the
    bug class — a revert to hardcoded `; Domain=.premiselabs.co; Secure` there
    would drop the marker on localhost/previews and break cross-origin claim
    routing. It uses inline conditions (hostname + protocol), not the shared
    domainAttr()/secureAttr() helpers, so it needs its own contract: no
    unconditional Domain/Secure, host-conditional Domain, https-conditional
    Secure, SameSite=Lax, Expires=."""
    text = _read(REPO_ROOT / "website" / "signup.html")
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


def test_adapters_share_size_guard_and_localhost_handling() -> None:
    """Size-guard parity: BOTH adapters must strip provider tokens when the
    cookie would exceed the 4096-byte cap. #1835: the dashboard adapter was
    wrongly assumed to never need the guard — a Google OAuth session
    (provider_token ~1200 chars + full identity, ~5012 encoded bytes) exceeds
    the cap and is silently rejected, so the dashboard must strip provider
    tokens exactly like the shared factory. #1857: the dashboard adapter ALSO
    now degrades off-premiselabs (host-conditional Domain/Secure), so
    localhost handling is no longer shared-file-only."""
    shared = _read(SHARED)
    dash = _read(DASHBOARD)
    assert "localhost" in shared and "127.0.0.1" in shared
    assert "localhost" in dash and "127.0.0.1" in dash  # #1857
    assert "premiselabs.co" in shared
    # size guard strips provider tokens when the cookie would exceed the cap
    # — required in BOTH adapters (#1835 parity)
    for path, text in ((SHARED, shared), (DASHBOARD, dash)):
        assert "SIZE_GUARD" in text, f"{path}: missing SIZE_GUARD"
        assert "provider_token" in text, f"{path}: size guard must strip provider_token"
        assert "provider_refresh_token" in text, (
            f"{path}: size guard must strip provider_refresh_token too"
        )
        assert "SIZE_GUARD + 100" in text, (
            f"{path}: must warn only when still over SIZE_GUARD + 100"
        )


def test_all_tortoise_pages_wire_the_shared_bridge() -> None:
    """A dropped script tag or an unwired page passes the constant checks but
    silently returns the login-wall bug — assert the wiring directly."""
    for page in PAGES:
        text = _read(page)
        assert 'src="/assets/supabase-session.js"' in text, (
            f"{page.name}: missing shared bridge script tag"
        )
        if page.name == "index.html":
            # #1511 dashboard variant: its gate uses the shared helpers, not
            # the factory (the client is built in main.jsx).
            assert "readValidSession(" in text,                 f"{page.name}: dashboard gate must use readValidSession"
        else:
            assert "createTortoiseSupabaseClient(" in text, (
                f"{page.name}: createClient not routed through the shared factory"
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


def test_dashboard_public_copy_is_byte_identical() -> None:
    """The dashboard loads the shared script from its own public/ copy (the
    dashboard is a separate Pages project — dist/ only deploys). It must stay
    byte-identical to the shared file (Task 5 asserts the built dist copy).
    The built dist/ copy is also git-tracked and is what the deployed
    dashboard actually serves — assert it too so a forgotten rebuild can't
    ship a stale bridge (code-review P3, cycle 3)."""
    public_copy = REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "assets" / "supabase-session.js"
    dist_copy = REPO_ROOT / "website" / "apps" / "dashboard" / "dist" / "assets" / "supabase-session.js"
    shared = _read(SHARED)
    assert public_copy.exists(), "missing dashboard public/ copy"
    assert public_copy.read_text(encoding="utf-8") == shared, \
        "dashboard public/ copy drifted from the shared file"
    assert dist_copy.exists(), "missing dashboard dist/ copy"
    assert dist_copy.read_text(encoding="utf-8") == shared, \
        "dashboard dist/ copy drifted from the shared file (rebuild dashboard)"
