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

⚠️ THIS MODULE PINS A DESIGN THAT IS BEING RETIRED (#3501). The parent-domain
cookie is a REVERSAL, not a fallback: it is JavaScript-readable by construction,
which is the property #3501 exists to remove. This suite stays green for the
pages not yet migrated, and each migration must REMOVE its page from `PAGES`
rather than delete its assertions — that way the remaining legacy surface is
always listed explicitly, and the end state (an empty `PAGES`) doubles as the
"no page is on the legacy bridge" proof. See #3559 for the migration backlog.
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
    # welcome.html is NOT here: #3501 migrated it off the cross-subdomain
    # bridge entirely. It no longer loads supabase-session.js, no longer
    # reads a JavaScript-visible session, and delegates the auth decision to
    # `functions/welcome.ts`. Its EXCLUSION from this adapter-compatibility
    # set is the point — asserting the old wiring on it would pin the very
    # design #3501 removed.
    #
    # signup.html / signin.html / the dashboard are still on the legacy bridge
    # because they are still being migrated: see #3559. When each lands, it
    # moves out of this list and into tests/test_no_legacy_token_path.py, which
    # asserts absence for the migrated surfaces.
    #
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


def test_adapters_escape_cookie_key_in_read_regex() -> None:
    """#1860 (P3-3): the dashboard getItem cookie-read regex must escape the
    key exactly like the shared bridge's readCookie. An unescaped key treats
    regex metacharacters ([.*+?^${}()|\\]) as pattern — benign for today's
    keys, latent drift. Both read paths must carry the same escape replace()
    (mirrors readCookie's, KEEP IN SYNC)."""
    shared = _read(SHARED)
    dash = _read(DASHBOARD)
    escape = "key.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')"
    # normalize quotes (a cosmetic double-quote refactor is NOT drift)
    shared_norm = shared.replace('"', "'")
    dash_norm = dash.replace('"', "'")
    assert escape in shared_norm, "shared readCookie must escape the key"
    assert escape in dash_norm, "dashboard getItem must escape the key (drift from readCookie)"


def test_dashboard_preserves_search_params_on_auth_bounce() -> None:
    """#1860 (P3-5): the provisionInApp 401 bounce must preserve the search
    params — /auth's OAuth-error banner reads ?error=... — in BOTH the
    bounceToAuth call and the degraded window.location.replace fallback
    (review P2-1): the bare fallback would drop the banner's cause exactly
    when the shared bridge is blocked/unavailable. Mirrors the mount gate
    (main.jsx ~1629) and the signup-form precedent."""
    dash = _read(DASHBOARD)
    # the primary path passes the params through bounceToAuth (#1909 adds the
    # oauthErrorHash second arg — assert the leading params-preserving call
    # shape, not the closed literal, so the intent survives arg evolution)
    assert "window.bounceToAuth(window.location.search," in dash, (
        "provisionInApp 401 bounce must preserve search params"
    )
    # the degraded fallback appends them too (mount-gate parity)
    assert "'https://tortoise.premiselabs.co/auth' + window.location.search" in dash, (
        "degraded fallback must append window.location.search (mount-gate parity)"
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
    silently returns the login-wall bug — assert the wiring directly.

    Scope is the set of pages STILL on the cross-subdomain bridge (see the
    PAGES comment). welcome.html left this set in #3501; its absence from the
    bridge is pinned by tests/test_no_legacy_token_path.py instead.
    """
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
    byte-identical to the shared file.

    #3775: dist/ is no longer git-tracked — it is a build artifact. A dist/
    copy used to be asserted here too, so a forgotten rebuild could not ship a
    stale bridge; that failure class is gone. Both deploy paths (deploy.sh +
    deploy-pages.yml) and the dashboard-js-tests job build dist/ from public/
    with vite immediately before it is served, and vite copies public/
    verbatim (verified byte-for-byte in #3775), so public/ is the contract to
    pin. The built bundle is what `distBundle.test.js` scans."""
    public_copy = REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "assets" / "supabase-session.js"
    shared = _read(SHARED)
    assert public_copy.exists(), "missing dashboard public/ copy"
    assert public_copy.read_text(encoding="utf-8") == shared, \
        "dashboard public/ copy drifted from the shared file"


# ── #3485: readValidSession trusts ONLY the cookie ──────────────────────────


def _strip_comments(text: str) -> str:
    """Remove JS comments before any brace counting.

    A brace-counting extractor that counts RAW text is defeated by a brace inside
    a comment — e.g. `// legacy fallback (}}}` — which truncates the extracted
    body and hides everything below it from every assertion in this file. That is
    how the loop could be reintroduced with the suite still green (#3485 review,
    cycle 3). String literals are deliberately left intact: several pins below
    match code that contains them.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def _function_body(name: str) -> str:
    """Extract `var <name> = function (...) { ... }` by brace counting over
    comment-stripped text."""
    clean = _strip_comments(_read(SHARED))
    start = clean.index(f"var {name} = function")
    i = clean.index("{", start)
    depth, j = 0, i
    while j < len(clean):
        if clean[j] == "{":
            depth += 1
        elif clean[j] == "}":
            depth -= 1
            if depth == 0:
                return clean[i : j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces in {name}")


def _read_valid_session_body() -> str:
    body = _function_body("readValidSession")
    # Sanity: a truncated extraction would silently weaken every assertion below.
    assert "return null" in body and len(body) > 200, (
        "readValidSession body extraction is not plausible — a truncated body "
        "would make every assertion in this file vacuous (#3485 review, cycle 3)"
    )
    return body


def _assert_each_branch_confirms_before_clearing(body: str, *, ret: bool) -> None:
    """Every cookie write must be CONFIRMED before the legacy copy is dropped.

    Four bypasses are closed here (#3485 review, cycles 2-3). A TOTAL count of 2
    is satisfied by duplicating one branch's confirm and deleting the other's. A
    PER-BRANCH count is satisfied by wrapping one confirm in `if (false) { ... }`.
    A count of any kind is satisfied by the confirm running BEFORE the write
    rather than after it, or by the removal running before the confirm.
    """
    statement = "return;" if ret else "continue;"
    confirm = f"if (readCookie(COOKIE_NAME) !== legacy) {statement}"
    write = "supabaseStorage.setItem(COOKIE_NAME, legacy);"
    assert body.count(write) == 2, "both branches must write the cookie"
    adjacent = re.findall(
        r"supabaseStorage\.setItem\(COOKIE_NAME, legacy\);\s*\n\s*" + re.escape(confirm),
        body,
    )
    assert len(adjacent) == 2, (
        "each write must be IMMEDIATELY followed by its own confirm — a guard "
        "wrapped around the confirm (`if (false) { ... }`), a confirm that runs "
        "before the write, or a confirm present in only one branch would otherwise "
        "pass (#3485 review)"
    )
    assert body.rindex("removeItem(") > body.rindex(confirm), (
        "the legacy key must be removed only AFTER the write is confirmed — "
        "removing it first destroys the only surviving copy when the size guard "
        "stripped the value (#3485 review, successor cycle)"
    )


def test_read_valid_session_never_returns_a_localstorage_only_session() -> None:
    """#3485: the auth loop was `readValidSession()` reporting a session that
    lived ONLY in origin-scoped localStorage — invisible to app.premiselabs.co
    and the server gate, which bounced straight back to /auth, forever (393
    document loads / 8s reproduced with Playwright against production).

    A localStorage fallback here IS the loop, so the read must be cookie-only:
    when the cookie is absent, migrate the hardcoded LEGACY_KEYS synchronously
    into it, then return null if it still is not there (the visitor signs in
    again rather than being told they are signed in somewhere they are not).
    """
    body = _read_valid_session_body()
    assert "migrateLegacyKeysToCookie()" in body, (
        "readValidSession must migrate legacy keys synchronously before deciding"
    )
    # Absence of the INVARIANT, not of a spelling (#3485 review, successor
    # cycle): checking for `window.localStorage.getItem` exactly let the same
    # defect back in as `localStorage.getItem(LEGACY_KEYS[0])`.
    assert "localStorage" not in body, (
        "readValidSession must never read a session straight out of localStorage "
        "— that session is invisible to the other subdomain and the server gate (#3485)"
    )


def test_sync_legacy_migration_is_present_and_confirm_before_clear() -> None:
    """The gate-time migration must exist and never destroy the only copy, and a
    legacy value that is not a session must never be written to the shared cookie
    (nor may a present-but-unusable cookie outrank a valid legacy session)."""
    text = _read(SHARED)
    assert "var migrateLegacyKeysToCookie = function" in text, (
        "missing the #3485 synchronous legacy→cookie migration"
    )
    body = _function_body("migrateLegacyKeysToCookie")
    _assert_each_branch_confirms_before_clearing(body, ret=False)

    # The derivation is pinned, not just the guard that consumes it: appending
    # `|| true` to any clause makes legacyOk unconditionally true while every
    # named guard string stays intact (#3485 review, cycle 3).
    deriv = re.search(r"legacyOk = (!![^;]*);", body)
    assert deriv, "migrateLegacyKeysToCookie must derive legacyOk from the parsed value"
    assert "typeof lo.access_token === 'string'" in deriv.group(1), (
        "legacyOk must require a real access_token (#3485 review P3)"
    )
    assert "||" not in deriv.group(1), (
        "legacyOk's derivation must be a conjunction — a disjunct (e.g. `|| true`) "
        "defeats the both-present guard without changing its text (#3485 review P2)"
    )
    assert "if (!legacyOk) {" in body, (
        "the cookie-absent branch must refuse to share a non-session with the parent "
        "domain — poison there would outrank the second legacy key (#3485 review P3)"
    )
    # Both-present guard: requires a real session AND that it is still usable.
    # An expired legacy session (or one with no expires_at) must never displace a
    # valid parent-domain cookie — that cookie is what the server gate reads, so
    # swapping it for a dead one signs the visitor out of both subdomains.
    guard = " ".join(body.split())
    assert (
        "if (legacyOk && legacyExp * 1000 > Date.now() && "
        "(!cookieOk || legacyExp > cookieExp)) {" in guard
    ), (
        "the both-present branch must require a real AND UNEXPIRED legacy session "
        "before overwriting the cookie (#3485 review P1/P2)"
    )
    assert "typeof co.access_token === 'string'" in body, (
        "a present-but-unusable cookie must not outrank a valid legacy session (#3485 review P3)"
    )
    assert "LEGACY_KEYS" in body, "migration must iterate the hardcoded LEGACY_KEYS"

    # The sibling writer for the SAME keys (the createTortoiseSupabaseClient path,
    # reached when the head gate is skipped) must not be a second, unguarded way
    # to write a non-session into the cookie — nor to drop the only copy.
    mig = _function_body("migrateLegacySession")
    _assert_each_branch_confirms_before_clearing(mig, ret=True)
    assert "typeof lo.access_token === 'string'" in mig and "!legacyOk" in mig, (
        "migrateLegacySession must refuse to share a non-session (#3485 review)"
    )
    # The SIBLING writer must be pinned to the same standard as the gate-time one.
    # Mutation proved the gap: `|| true` appended to the sibling's derivation, and
    # `legacyOk = true;` appended after it, both left every assertion above green
    # (#3485 review, cycle 3).
    for name, fn in (("migrateLegacyKeysToCookie", body), ("migrateLegacySession", mig)):
        assert fn.count("legacyOk =") == 2, (
            f"{name} may assign legacyOk only in its declaration and its parse "
            "derivation — a third assignment (e.g. `legacyOk = true;`) makes the "
            "guard's operand unconditionally true while its text stays intact"
        )
        fn_deriv = re.search(r"legacyOk = (!![^;]*);", fn)
        assert fn_deriv, f"{name} must derive legacyOk from the parsed value"
        assert "typeof lo.access_token === 'string'" in fn_deriv.group(1), (
            f"{name}: legacyOk must require a real access_token"
        )
        assert "||" not in fn_deriv.group(1), (
            f"{name}: legacyOk's derivation must be a conjunction — a disjunct "
            "(e.g. `|| true`) defeats the guard without changing its text"
        )
    mig_guard = " ".join(mig.split())
    assert (
        "if (legacyExp * 1000 > Date.now() && (!cookieOk || legacyExp > cookieExp)) { "
        in mig_guard + " "
    ), (
        "migrateLegacySession must also require an UNEXPIRED legacy session before "
        "overwriting the cookie (#3485 review P1/P2)"
    )

    # storeSession verifies the cookie it just wrote — re-entering the now
    # migrating readValidSession could let a legacy session win.
    store = _function_body("storeSession")
    assert "return readValidSession()" not in store, (
        "storeSession must not re-enter the migrating accessor to verify its write (#3485 review)"
    )
    # The predicate must GOVERN THE RETURN, as a conjunction: substring checks
    # pass with the comparisons kept as no-op statements and `return true;` at the
    # end, and they also pass when a `&&` is flipped to `||`, which reports success
    # for a rotated or expired session the destination gate will reject.
    verdict = re.search(r"return\s+!!\([^;]*\);", store)
    assert verdict, (
        "storeSession must RETURN its verified verdict — a computed-but-discarded "
        "check reports success unconditionally (#3485 review P1)"
    )
    v = " ".join(verdict.group(0).split())
    assert v.count("&&") == 4 and "||" not in v, (
        "the verdict must be a conjunction of four checks — an operator change or a "
        "dropped clause keeps every substring while defeating the guard "
        "(#3485 review P1)"
    )
    assert "stored.access_token === session.access_token" in v, (
        "the verdict must confirm the cookie now carries THE session just written — "
        "reading back a stale pre-existing value reports success for a write the "
        "browser actually refused (#3485 review P1)"
    )
    assert "stored.refresh_token === session.refresh_token" in v, (
        "the verdict must compare the token PAIR — a cookie sharing only the "
        "access_token (a rotated pair) is not this write (#3485 review P2)"
    )
    assert "stored.expires_at && stored.expires_at * 1000 > Date.now()" in v, (
        "the verdict must apply the same strict validity predicate as "
        "readValidSession, or it reports success for a session the destination gate "
        "will reject and bounce back to /auth (#3485 review P1)"
    )

    # The migration must run BEFORE the first cookie read, and as a BARE
    # statement: `if (false) migrateLegacyKeysToCookie();` keeps the text in
    # place while the call never runs, and the same trick on the PREVIOUS line
    # defeats a same-line-only check.
    read_body = _read_valid_session_body()
    call = re.search(r"^(\s*)migrateLegacyKeysToCookie\(\);\s*$", read_body, re.MULTILINE)
    assert call, (
        "the migration must be CALLED as a bare statement, not wrapped in a guard "
        "that can be disabled while keeping the text (#3485 review P2)"
    )
    # Nothing CONDITIONAL may precede the call: `if (false) { … }` (or a ternary)
    # keeps the text in place while the call never runs. A bare `try {` wrapper is
    # fine — the call still executes — so this pins unconditionality rather than
    # the shape of the enclosing block (#3485 review, cycle 3).
    prefix = read_body[: call.start()]
    assert not re.search(r"\bif\b|\?", prefix), (
        "nothing conditional may precede the migration — a disabled call would "
        "otherwise satisfy every text check (#3485 review, cycle 3)"
    )
    first_read = read_body.index("readCookie(COOKIE_NAME)")
    assert call.start() < first_read, (
        "the migration must run BEFORE the first cookie read, not inside the "
        "cookie-absent branch (#3485 review P1)"
    )


def test_clear_stored_session_also_clears_the_spa_localstorage_key() -> None:
    """#3485 review (successor cycle): the blog-admin SPA persists the session in
    localStorage under the SAME name and refreshes tokens from there
    (website/apps/blog-admin/src/lib/supabase.ts), so clearing only the cookie
    lets the console re-write the cookie from an origin-scoped copy the server
    gate never saw — resurrecting the session just signed out of. Clearing the
    cookie alone is not a sign-out."""
    body = _function_body("clearStoredSession")
    assert "removeItem(COOKIE_NAME)" in body, (
        "clearStoredSession must also clear the localStorage copy the blog-admin "
        "SPA writes under the same name — otherwise sign-out does not stick (#3485)"
    )


def test_oauth_fragment_is_only_stripped_once_the_write_landed() -> None:
    """#3485 review P1 (cycle 2): the implicit-flow fragment carries the ONLY copy
    of the NEW credential, so erasing it when storeSession() returned false
    strands the visitor on /auth (or leaves the previous account's session in
    place) with nothing stored. The strip must be conditional on the write."""
    text = _read(SHARED)
    assert "if (storeSession(session)) {" in text, (
        "the fragment must only be stripped when the session actually landed in the "
        "cookie (#3485 review P1)"
    )
    assert not re.search(r"^\s*storeSession\(session\);\s*$", text, re.MULTILINE), (
        "a bare `storeSession(session);` discards the verdict — the fragment is then "
        "erased unconditionally (#3485 review P1)"
    )
