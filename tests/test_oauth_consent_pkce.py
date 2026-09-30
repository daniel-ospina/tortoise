"""Behavioural harness for the MCP consent page's browser auth client (#3496).

The page (`tortoise/oauth.py::consent_page_html`) is a FastAPI-rendered HTML
document whose inline script drives supabase-js. Before this harness, its
hardening was pinned only by static-string assertions on the server-rendered
markup (`tests/test_oauth_mcp.py`), which cannot discriminate the behaviour this
issue changes: the grant type actually used, where the PKCE
`code_verifier` is stored, whether a sign-in refusal reaches the user, and
whether a transient is echoed back to the provider.

This harness renders the page with the PURE `consent_page_html(...)`, extracts
the inline script from the RENDERED HTML, and EXECUTES it under Node `vm`
against the **vendored** `@supabase/supabase-js` bundle with a DOM/fetch/storage
shim. The token POST is captured, the cookie jar is a raw per-assignment write
log, and the results are asserted as observable behaviour.

Toolchain contract: node is REQUIRED. A missing node FAILS by name (never
skips) — reusing `_require_node` from the sibling session-bridge harness (#3786),
because a skipped harness is green, exit 0, zero coverage.

Invariants pinned here:
 1 grant type actually used (`code_challenge_method=s256`) + implicit control
 2 single-origin completion (#1566): the return target the page BUILDS, evaluated
   at the callback origin, plus the exchange POST being `grant_type=pkce` and
   carrying the stored verifier. **Scope note:** this harness shims the DOM, the
   stores and `fetch`, so it exercises the PAGE in isolation — it does NOT observe
   GoTrue's own redirect handling, the terminal `POST /oauth/consent`, or its
   401-refresh leg (those remain pinned by `tests/test_oauth_mcp.py` statically
   and by `test_oauth_token_fault.py`/`test_cursor_mcp_exit_evidence.py`).
 3 key-identity routing on set: the verifier never reaches `document.cookie`,
   asserted against BOTH verifier-shaped and non-verifier keys (a denylist routes
   the former correctly, so only the latter discriminate)
 4 the removal path (invalid stored session, with a verifier seeded so the
   assertion can fail) writes ONLY the session-key expiry
 5 the terminal state is exact, with a clean-load negative control; the message
   is BOUNDED and control-char stripped; and a transient carried in the FRAGMENT
   reaches the same state (with a benign-fragment control)
 6 an unavailable store refuses locally (no navigation, no verifier) — both a
   method-throw and an access-time-throw store
 7 item 6 write-path parity: ≤SIZE_GUARD byte-identical, >SIZE_GUARD stripped AND
   actually written (asserting `strippedWrites == 1`, so removing the strip cannot
   pass by falling through to the refusal), >SIZE_CAP refused AND page-reported
 8 version coupling: the page's CDN specifier EQUALS the version of the vendored
   bundle this harness executes (pure text/path, no node — see
   `test_page_specifier_matches_the_vendored_bundle_version`)
 9 no WebCrypto refuses locally; with the guard removed the bundle downgrades
   to `code_challenge_method=plain` (the paired control)
11 the return target is canonicalised (no transient echoed), with a
   guard-removed control that DOES carry it
12 the aux stores hold no verifier after the removal path
13 the WRITER probes each store with a same-length dummy under a throwaway key
   and selects a store only when the write/read/remove cycle completed AND the
   real value was read back; a store that fails is skipped for USE
   (`test_inv13_...`, A5 writer half). The writer cannot prove the REAL key is
   removable before the credential is written to it, so a store that accepts the
   credential and refuses its removal keeps a copy — the recorded residual R21.

#5734 (the refusal UX on top of #3496 — option C + B, page-scoped):
14 capability is probed at LOAD: an incapable page (no WebCrypto / no
   TextEncoder / no usable aux store / store access throws) disables the provider
   buttons — and only those: the email form the copy names as its alternative
   stays usable — and shows the inline explanation BEFORE the user acts, naming
   the cause AND the remedy, with a capable-load control
15 the load probe is an AFFORDANCE, not the boundary: capability LOST after load
   still refuses at click, writes no verifier, and updates the affordance
16 the post-redirect terminal state names its CAUSE and its REMEDY per cause
   (declined / provider failure / returned-but-unexchanged code / any other
   transient / over-SIZE_CAP write), offers the retry affordance, and states only
   a cause its branch condition establishes — two causes must not share copy
17 every message added here is PAGE-SCOPED (positive: the capability copy names
   this page; negative: no message names the browser or the product), and EVERY
   post-redirect cause is collected — the #4678 lesson, applied to copy rather
   than to a behaviour
18 the retry affordance's handler RE-PROBES capability (lost and restored after
   the attempt), rather than rendering the load-time verdict
19 a refused over-SIZE_CAP write is NOT overwritten by the transitions that can
   erase it — the terminal fallback's generic `?code` branch, and the consent
   flow itself, which otherwise renders its own message about the STALE session,
   and which `showConsentOnce()` therefore refuses to enter at all while a
   refusal is pending (with a refusal pending, this `hideError()` is unreachable
   from the UI, so it is a guard no UI test can lose rather than a live one) —
   driven through the REAL exchange callback (a
   `?code` load against an over-cap token response), with and without a stale
   session
20 the retry the refusal's copy names CLEARS it (a `writeRefused` the handler had
   not cleared would leave the sticky refusal on screen), the retry leaves no
   control behind for a message that does not imply one, and a SECOND refusal
   then does not suppress the next attempt's own message
21 a message that does not imply a retry carries no retry control: the affordance
   is a sibling of #error OUTSIDE both views, so a consent-view message that
   follows one has to drop it explicitly, a validation message from the form does
   too, and entering the consent view after a terminal state drops it — with the
   ownership rule ALSO pinned DIRECTLY on showError/hideError: the `showError`
   arm because no UI scenario can lose it (every UI route to a non-retry message
   drops the control first, via a handler's hideError() or via showSignin()), the
   `hideError` arm as a contract pin it shares with a UI scenario
22 the generic post-redirect terminal branch names its OWN remedy (the one cause
   whose remedy cannot be pinned by borrowing a sibling's phrase), and EVERY
   alias of a shared-cause class selects its own branch rather than falling
   through to it
23 a preview that resolves with no org is REPORTED and leaves Authorize disabled,
   and a caller that lands on the sign-in view with a refusal pending
   (`showExpiredSignin`) neither replaces the refusal nor drops its control

Every field the tests assert on is produced by the page's own code running in the
context, never read back from a shim.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

# #3786: the session-bridge toolchain contract is owned by the sibling harness —
# one guard, not a second variant. It FAILS (never skips) when node is absent.
from tests.test_session_bridge_fragment_retention import _require_node

REPO_ROOT = Path(__file__).resolve().parent.parent
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"
VENDOR_DIR = REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "vendor"

COOKIE_NAME = "sb-tortoise-auth-token"
PAGE_ORIGIN = "https://api.premiselabs.co"
AUTHORIZE_PATH = "/oauth/authorize"

# The inline script is the SECOND `<script>` (the first is the CDN tag, which
# begins `<script src=`); both carry the same nonce. This strict form matches
# only the inline block — a permissive `<script[^>]*nonce="…"[^>]*>` matches the
# CDN tag first with an EMPTY body (verified), which the sentinel assertion
# below would then catch as an extraction failure rather than a vacuous pass.
_INLINE_RE = re.compile(r'<script nonce="([^"]+)">([\s\S]*?)</script>')
_SENTINEL = "PARAMS"


def _vendored_bundle() -> Path:
    """The single vendored supabase-js bundle, derived from the directory so a
    version bump does not silently leave this harness executing a stale copy."""
    files = sorted(VENDOR_DIR.glob("supabase-*.min.js"))
    assert len(files) == 1, (
        f"expected exactly one vendored supabase bundle in {VENDOR_DIR}, found "
        f"{[f.name for f in files]}"
    )
    return files[0]


def _render_page() -> str:
    """Render the consent page with the pure renderer (no app boot).

    Note: the load-time URL is supplied to the DRIVER (`_run(..., search=...)`),
    not here — `consent_page_html` takes no URL. A `search` parameter on this
    renderer would be inert, and a caller reaching for it to build a
    ``?error=…`` case would silently exercise a clean load.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from tortoise.oauth import consent_page_html

    html, _nonce = consent_page_html(
        client_name="test-connector",
        scope="mcp",
        params={
            "client_id": "client-1",
            "redirect_uri": "https://client.example/cb",
            "response_type": "code",
            "state": "st-1",
            "code_challenge": "chal",
            "code_challenge_method": "S256",
            "scope": "mcp",
            "resource": "",
            "client_name": "test-connector",
        },
        supabase_url="https://proj.supabase.co",
        supabase_anon_key="anon-key",
    )
    return html


def _extract_inline(html: str) -> str:
    m = _INLINE_RE.search(html)
    assert m, "could not extract the inline consent script from the rendered page"
    body = m.group(2)
    assert _SENTINEL in body, (
        "the extracted inline script does not contain the PARAMS sentinel — the "
        "extraction matched the wrong block (the CDN tag carries the same nonce)"
    )
    assert "</" not in body, (
        "the extracted inline script contains '</' — `_json_for_script` escapes "
        "'<', so this means the extraction is wrong (or the escape regressed)"
    )
    return body


def _run(scenario: str, *, page: str | None = None, **opts) -> dict:
    _require_node()
    html = page if page is not None else _render_page()
    inline = _extract_inline(html)
    with tempfile.TemporaryDirectory(prefix="3496-harness-") as td:
        tdp = Path(td)
        (tdp / "driver.js").write_text(_DRIVER_JS)
        (tdp / "page.js").write_text(inline)
        payload = json.dumps({"scenario": scenario, **opts})
        proc = subprocess.run(
            ["node", str(tdp / "driver.js"), str(_vendored_bundle()),
             str(tdp / "page.js"), payload],
            capture_output=True, text=True, timeout=120, cwd=str(tdp),
        )
    if proc.returncode != 0:
        raise AssertionError(
            f"harness scenario {scenario!r} failed (exit {proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    m = re.findall(r"^RESULT (\{.*\})$", proc.stdout, re.MULTILINE)
    assert m, f"no RESULT line for {scenario!r}:\n{proc.stdout}\n{proc.stderr}"
    # NOT `splitlines()`: Python splits on U+2028/U+2029, which ARE Unicode line
    # boundaries there but are ordinary characters to JSON and to this regex —
    # and `JSON.stringify` does not escape them, so a value carrying one (any
    # URL-derived text) silently truncated the line and produced a JSON error
    # pointing at the wrong thing.
    return json.loads(m[-1])


# ── invariant 8: version coupling (pure text / path, no node) ───────────────

def test_page_specifier_matches_the_vendored_bundle_version() -> None:
    """The CDN specifier must equal the version of the vendored bundle that this
    harness executes. A vendor-only PR selects `api` (see
    `SOURCE_PATTERNS["api"]` in tools/ci_selection.py) so this runs on the bump,
    and an oauth-only edit selects `api` too — both directions."""
    vendored = _vendored_bundle().name  # supabase-<ver>.min.js
    ver = re.match(r"supabase-(.+)\.min\.js$", vendored).group(1)
    text = OAUTH.read_text()
    assert f"@supabase/supabase-js@{ver}/dist/umd/supabase.min.js" in text, (
        f"the consent page's CDN specifier does not match the vendored bundle "
        f"version {ver!r} ({vendored}) — bump them together"
    )
    assert "@supabase/supabase-js@2/" not in text, (
        "the mutable `@2` specifier is back — pin the exact version"
    )


# ── the behavioural scenarios ───────────────────────────────────────────────

def test_inv1_grant_type_is_pkce_with_an_implicit_negative_control() -> None:
    """Invariant 1: the approve flow's authorize URL carries `code_challenge`
    with `code_challenge_method=s256`. The paired control removes the explicit
    flowType and asserts the challenge DISAPPEARS — so the signal demonstrably
    discriminates rather than being vacuously true."""
    r = _run("grant")
    assert r["method"] == "s256", f"expected s256, got {r}"
    assert r["challenge"], f"no code_challenge in {r}"

    implicit_page = _render_page().replace('flowType: "pkce",', 'flowType: "implicit",')
    ctl = _run("grant", page=implicit_page)
    assert not ctl["challenge"], (
        "the implicit control still produced a code_challenge — the assertion "
        f"does not discriminate the flow type: {ctl}"
    )


def test_inv2_single_origin_completion_and_pkce_exchange() -> None:
    """Invariant 2 (#1566's regression guard): the flow completes on the SAME
    origin it initiated on, and the token exchange is `grant_type=pkce` carrying
    the verifier that was stored.

    Two REAL observations, neither read back from the shim:
      * the return target the page actually BUILDS, evaluated where the callback
        landed (`authorizeReturnTo()`), paired with a control that moves it to a
        foreign host;
      * the token POST captured from the running client.
    The INITIATION half — the `redirect_to` supabase-js puts on the authorize URL
    — is observed by `test_inv11_...`, which parses it off the assign URL. The
    return target here is read from what the page BUILDS, never read back from
    the driver: reading `location.origin` back from the shim asserts the shim
    against itself and stays green even with the page returning
    `https://evil.example`."""
    r = _run("single_origin", search="?code=THECODE&state=st-1",
             seedVerifier="verifier-abc")
    assert r["exchangeGrant"] == "pkce", f"token POST was not grant_type=pkce: {r}"
    assert r["exchangeVerifier"] == "verifier-abc", (
        f"the exchange did not carry the stored verifier: {r}"
    )
    rt = urllib.parse.urlparse(r["returnTarget"])
    assert f"{rt.scheme}://{rt.netloc}" == PAGE_ORIGIN, (
        f"the page builds a return target for another origin: {r['returnTarget']!r}"
    )
    assert rt.path == AUTHORIZE_PATH, r

    evil = _render_page().replace(
        'return window.location.origin + AUTHORIZE_PATH + (u.search || "");',
        'return "https://evil.example" + AUTHORIZE_PATH + (u.search || "");',
    )
    ctl = _run("single_origin", page=evil, search="?code=THECODE&state=st-1",
               seedVerifier="verifier-abc")
    assert urllib.parse.urlparse(ctl["returnTarget"]).netloc == "evil.example", (
        f"the control did not move the return target off-origin, so the assertion "
        f"above cannot fail: {ctl['returnTarget']!r}"
    )


def test_inv3_and_inv12_the_verifier_never_reaches_the_cookie() -> None:
    """Invariants 3 + 12: on initiation the verifier keys land in the aux store
    and NEVER in `document.cookie`; on removal the aux stores are cleared and the
    cookie jar sees only the session-key expiry.

    The two NON-verifier aux shapes are exercised on purpose. A denylist router
    (`key.endsWith("-code-verifier")`) routes verifier-shaped keys CORRECTLY by
    construction, so only a key that does not look like a verifier discriminates
    an allowlist from a denylist — and the cookie log is read by key IDENTITY,
    not by a name substring (a substring filter answered `[]` with the denylist
    installed, so it could not fail)."""
    r = _run("routing")
    assert r["auxVerifierKeys"], f"no verifier key in sessionStorage: {r}"
    assert r["cookieVerifierKeys"] == [], (
        f"a verifier key reached document.cookie: {r['cookieVerifierKeys']}"
    )
    assert len(r["nonVerifierAuxKeys"]) == 2, (
        f"the non-verifier aux keys never reached a store, so the next assertion "
        f"cannot fail: {r}"
    )
    assert r["cookieAuxWrites"] == [], (
        f"an aux key produced a cookie write: {r['cookieAuxWrites']}"
    )


def test_inv4_removal_path_writes_only_the_session_key() -> None:
    """Invariant 4: an invalid stored session drives `_removeSession`, whose
    ONLY cookie assignment must be the session-key expiry. The raw per-assignment
    log is load-bearing — the final-state jar is identical whether or not an aux
    key also reached the cookie.

    A verifier IS seeded: without one the aux stores start empty, so
    `auxVerifierKeys == []` held whether or not the removal path cleared them
    (verified by mutation: a `removeAux` that skips `sessionStorage` stayed green
    with no seed and reddens with one)."""
    r = _run("removal", seedSession='{"access_token":"a"}',
             seedVerifier="verifier-abc")
    assert r["cookieWriteNames"] == [COOKIE_NAME], (
        f"expected exactly one cookie assignment (the session key), got {r}"
    )
    assert r["navs"] == [], f"the removal path navigated: {r['navs']}"
    assert r["auxVerifierKeys"] == [], (
        f"an aux-store verifier survived the removal path: {r['auxVerifierKeys']}"
    )

    # The SECOND store too: with sessionStorage unwilling to take a write, the
    # verifier lives in localStorage and the removal path must clear it THERE.
    # Without this half only the first store is ever cleared, so a `removeAux`
    # that skipped `localStorage` would leave the verifier behind and this test
    # would stay green. (The library's getItemAsync JSON-parses what it reads,
    # so the seed uses the shape its own writer produces.)
    second = _run("removal", seedSession='{"access_token":"a"}',
                  seedVerifier="verifier-abc", sessionMode="throw-method",
                  seedVerifierStore="local")
    assert second["auxVerifierKeys"] == [], (
        f"a localStorage verifier survived the removal path: {second['auxVerifierKeys']}"
    )


def test_inv5_terminal_state_exact_with_a_clean_load_control() -> None:
    """Invariant 5: `?error=access_denied` ends on a VISIBLE sign-in view with the
    error; a CLEAN load with no session shows the sign-in view with NO error (the
    negative control that catches a spurious-error regression).

    Three further halves, each with its own control:
      * the message is BOUNDED and control-char stripped (`boundedText`). The
        description is attacker-controlled, so removing the bound must redden
        this test rather than pass silently.
      * a transient carried in the FRAGMENT reaches the same terminal state.
        supabase-js folds the hash into the params it parses and the page keeps
        the library as the fragment consumer, so a hash-carried refusal the page
        cannot see is exactly the dead end this state exists to remove.
      * a benign fragment is NOT read as a transient and is left alone."""
    bad = _run("load", search="?error=access_denied&error_description=boom&state=st-1")
    assert bad["viewSignin"] == "block", bad
    assert bad["errorVisible"] is True, bad
    assert "boom" in bad["errorText"], bad
    assert bad["replaceStates"], "the transient URL was not sanitised"
    assert all("error_description" not in u for u in bad["replaceStates"]), (
        f"the transient survived in the sanitised URL: {bad['replaceStates']}"
    )

    # The controls are placed at the FRONT, inside the 299-char window: at the
    # tail the bound alone would remove them, and a test that cannot tell the
    # strip from the bound passes with the strip deleted (verified — the
    # tail-placement form survived that mutation).
    long_msg = _run(
        "load",
        search=("?error=access_denied&error_description="
                + "\x07\x0b\u2028" + "X" * 10000 + "\u2029\x1f"),
    )
    assert long_msg["errorVisible"] is True, long_msg
    assert len(long_msg["errorText"]) <= 300, (
        f"boundedText did not bound the description: {len(long_msg['errorText'])} chars"
    )
    assert long_msg["controlCharsInError"] == 0, (
        f"boundedText did not strip control characters: {long_msg['errorText']!r}"
    )

    frag = _run("load", hash="#error=access_denied&error_description=hashboom")
    assert frag["viewSignin"] == "block", frag
    assert frag["errorVisible"] is True, (
        f"a hash-carried refusal produced no message — the dead end is back: {frag}"
    )
    assert "hashboom" in frag["errorText"], frag
    assert frag["replaceStates"], "the fragment transient was not sanitised"
    assert all("hashboom" not in u for u in frag["replaceStates"]), (
        f"the transient survived in the sanitised URL: {frag['replaceStates']}"
    )

    # …and the same fragment carrying an ESCAPED value. The param-list test used to
    # be byte-identity after re-serialisation, which `%20` fails (URLSearchParams
    # re-serialises a space as `+`), so the branch was skipped and the transient
    # stayed on the URL — for exactly the provider `error_description` values that
    # contain spaces.
    frag_esc = _run("load", hash="#error=access_denied&error_description=boom%20boom")
    assert frag_esc["viewSignin"] == "block", frag_esc
    assert frag_esc["replaceStates"], "the escaped fragment transient was not sanitised"
    assert all("error_description" not in u for u in frag_esc["replaceStates"]), (
        f"an escaped transient survived in the sanitised URL: {frag_esc['replaceStates']}"
    )

    # The stricter param-list test must not now MANGLE a fragment it used to leave
    # alone: `#/route/x?a=1` and `#settings?tab=x&foo=bar` both contain `=`, so a
    # bare "has an `=`" test would re-serialise them to `%2Froute%2Fx%3Fa=1`.
    # `#view:detail=1` covers the `:` arm of the param-name shape test, which
    # neither of the other two exercises.
    for router_frag in ("#/route/x?a=1", "#settings?tab=x&foo=bar", "#view:detail=1"):
        routed = _run("load", search="?error=access_denied&error_description=boom",
                      hash=router_frag)
        assert routed["errorVisible"] is True, routed
        assert routed["replaceStates"], (
            f"the load was not sanitised, so the fragment pin is vacuous: {routed}"
        )
        # Compare the FRAGMENT itself, not a whole-URL substring: the mangling this
        # guards against re-serialises `#settings?tab=x&foo=bar` to
        # `settings%3Ftab=x&foo=bar`, which contains no `%2F` at all. `partition`
        # rather than `split(...)[1]`, so a fragment-less URL (which the page does
        # emit once a param-list fragment is fully stripped) fails the assertion
        # cleanly instead of raising IndexError.
        assert all(u.partition("#")[2] == router_frag[1:] for u in routed["replaceStates"]), (
            f"a router-shaped fragment was re-encoded: {routed['replaceStates']}"
        )

    # A reachable, non-provider cause of the SAME terminal state: the provider
    # returns `?code=`, and the code exchange inside createClient() is rejected.
    # This is the case the state exists to catch — it carries no
    # `error_description`, so the generic message is the entire report.
    failed = _run("load", search="?code=THECODE&state=st-1",
                  seedVerifier="verifier-abc", exchangeFails=True)
    assert failed["exchangeCount"] == 1, (
        f"the code exchange was never attempted (no stored verifier makes the "
        f"library treat the callback as non-PKCE), so the assertions below say "
        f"nothing about a REJECTED exchange: {failed}"
    )
    assert failed["viewSignin"] == "block", failed
    assert failed["errorVisible"] is True, (
        f"a rejected code exchange produced no message: {failed}"
    )
    assert failed["errorText"].strip(), failed

    clean = _run("load", search="")
    assert clean["viewSignin"] == "block", clean
    assert clean["errorVisible"] is False, (
        f"a clean load showed an error: {clean['errorText']!r}"
    )

    # A benign first load carrying a GENERIC param name must not be read as a
    # completed provider round trip: `type`/`flow_id` stay in the strip list
    # (stripping is cosmetic) but are not provider-owned markers, so treating them
    # as evidence told a first-time visitor who had done nothing that their
    # sign-in had failed.
    for generic in ("?client_id=x&type=mcp", "?client_id=x&flow_id=abc"):
        benign_first = _run("load", search=generic)
        assert benign_first["errorVisible"] is False, (
            f"a benign load with {generic} was reported as a failed sign-in: "
            f"{benign_first['errorText']!r}"
        )

    benign = _run("load", hash="#section-2")
    assert benign["errorVisible"] is False, (
        f"a benign fragment was read as a transient: {benign['errorText']!r}"
    )
    # Deliberately NO `replaceStates == []` assertion here: `sanitiseUrl()` runs
    # only on the transient-present/no-session branch, so a benign load reports
    # no replaceState whatever the fragment logic does — the assertion could not
    # fail. The `mixed` case below DOES run it and is the real discriminator.

    # Mixed case: a transient in the QUERY and a NON-param fragment. The
    # fragment is not a param list, so stripping must leave it byte-identical
    # (`#section-2` re-serialises to `section-2=`, which is not the same URL
    # fragment). Without this control the query-only and fragment-only cases
    # both miss the fragment branch mangling it to `#section-2=`.
    mixed = _run("load", search="?error=access_denied&error_description=boom",
                 hash="#section-2")
    assert mixed["errorVisible"] is True, mixed
    assert all("section-2=" not in u for u in mixed["replaceStates"]), (
        f"the fragment branch mangled a non-param fragment: {mixed['replaceStates']}"
    )
    assert any(u.endswith("#section-2") for u in mixed["replaceStates"]), (
        f"the benign fragment was not preserved: {mixed['replaceStates']}"
    )


def test_inv6_unavailable_store_refuses_locally() -> None:
    """Invariant 6: with both aux stores unavailable — by method throw AND by
    access-time throw — a provider click must NOT navigate and must report the
    refusal; no verifier may be written anywhere.

    The `throw-remove` mode pins the refusal: with the SESSION store accepting writes
    but refusing removal, the guard refuses rather than continuing to a localStorage
    that could take the verifier. Note the guard is a fail-closed approximation, not
    the writer's own test (it probes a fixed 160-byte payload where the writer probes
    the real value's length), so it can refuse a configuration the writer would have
    completed; that over-refusal is recorded with the refusal UX work (#5734). What
    this mode pins is that the guard refuses AT ALL in that shape, and that no
    verifier reaches any store."""
    for mode in ({"sessionMode": "throw-method", "localMode": "throw-method"},
                 {"accessThrow": True},
                 # session accepts writes but cannot remove; local is fully OK.
                 {"sessionMode": "throw-remove"}):
        r = _run("click", search="", **mode)
        assert r["navs"] == [], f"navigated despite an unusable store: {r} ({mode})"
        assert r["errorVisible"] is True, f"no refusal reported: {r} ({mode})"
        assert r["errorText"].strip(), (
            f"the refusal was visible but carried no message: {r} ({mode})"
        )
        assert r["cookieVerifierKeys"] == [], r
        assert r["auxVerifierKeys"] == [], r


def _assert_session_store_attempted(r: dict) -> None:
    """The A5 writer must have TRIED the failing store. Without this every
    `sessionVerifierKeys == []` assertion in inv13 is satisfied by the store never
    being in the chain at all: dropping `sessionStorage` from `auxStores()` leaves
    all of them green while `writeAux`'s per-store proof never runs."""
    assert any(a.startswith("session:setItem:__tt_wprobe-") for a in r["attempts"]), (
        f"the session store was never attempted by the A5 writer, so asserting it "
        f"holds no verifier is vacuous: {r['attempts']}"
    )


def test_inv13_the_write_lands_only_after_a_probe_cycle() -> None:
    """Invariant 13 (A5, the WRITER half): the pre-flight probe writes its OWN key
    with a 160-byte payload, so it cannot prove that the store which will RECEIVE
    the real verifier can also remove it. The writer therefore probes per store on
    every write — a same-length DUMMY under a throwaway key, written, read back,
    removed and read back null — and writes the real value into the store only
    once that cycle completed, then reads it back. A store that fails any step is
    skipped for USE.

    What the writer cannot do is prove the REAL key is removable before the
    credential is written to it: the write has to happen first for removal to be
    observable. A store that accepts the credential and then refuses to remove it
    therefore keeps a copy no path can clean — the recorded residual R21, reachable
    only by a store that discriminates by key, which is not a conforming browser
    store (a re-probe on the real key DOES detect such a store, but only after the
    credential has been written to it, so it cannot un-write it — it copies the
    residue into the next store, leaving two copies where the throwaway-key probe
    leaves one).

    This pins the divergence the pre-flight guard cannot see: a first store
    whose accepted-size band sits BETWEEN the probe (rejected) and the real value
    (accepted) and which refuses removal passed the guard on the second store,
    so only the writer could keep the credential out of the first. Without the
    writer's proof the outcome is
    `sessionVerifierKeys == [sb-tortoise-auth-token-code-verifier]` with
    `localVerifierKeys == []` — a credential in an un-cleanable store.

    The probe's own payload is asserted too, over the RAW store entries: a store
    that accepts the write but silently ignores removal RETAINS its probe entry,
    so a probe payload of `v` would leave the credential under a random
    `__tt_wprobe-*` key that nothing can clean — and the verifier-shaped filters
    used above cannot see a probe key at all."""
    r = _run("click", search="", sessionMode="throw-remove", sessionQuota=130)
    _assert_session_store_attempted(r)
    assert r["navs"], f"the flow did not proceed although localStorage is usable: {r}"
    assert r["errorVisible"] is False, r
    assert r["cookieVerifierKeys"] == [], r
    assert r["sessionVerifierKeys"] == [], (
        f"a verifier was written to a store that refuses removal: {r}"
    )
    assert r["localVerifierKeys"], (
        f"the verifier reached no store at all — the write was refused, not relocated: {r}"
    )

    # Control: when NO store can be cleaned, the guard must still refuse locally
    # (this is inv 6's contract, repeated here so the writer's relocation cannot be
    # mistaken for permission to write anywhere).
    both = _run("click", search="", sessionMode="throw-remove",
                localMode="throw-remove", sessionQuota=130)
    assert both["navs"] == [], f"navigated although no store can be cleaned: {both}"
    assert both["auxVerifierKeys"] == [], both

    # A store that accepts the write and SILENTLY IGNORES removal (no throw) is the
    # third shape the writer must reject, and it is only reachable in combination
    # with an item-size cap: with no cap the GUARD refuses the store first (its
    # 160-byte probe fits, its read-back-null fires), so the writer's own
    # read-back-null line is never exercised. Measured: with that one line deleted,
    # `sessionVerifierKeys` goes from [] to the full verifier set and
    # `localVerifierKeys` from populated to empty — the credential orphaned in the
    # store that cannot remove it.
    silent = _run("click", search="", sessionMode="silent-remove", sessionQuota=130)
    _assert_session_store_attempted(silent)
    assert silent["navs"], f"the flow did not proceed although localStorage is usable: {silent}"
    assert silent["errorVisible"] is False, silent
    assert silent["sessionVerifierKeys"] == [], (
        f"a verifier was written to a store that silently ignores removal: {silent}"
    )
    assert silent["localVerifierKeys"], (
        f"the verifier reached no store at all — the write was refused, not relocated: {silent}"
    )
    # The A5 probe must never carry the CREDENTIAL. Asserted over the raw entries,
    # because the verifier-shaped filters above do not see a `__tt_wprobe-*` key.
    verifier_values = {
        e["value"] for e in silent["auxRawEntries"] if e["key"].endswith("-code-verifier")
    }
    assert verifier_values, (
        f"no stored verifier value found, so the probe-leak assertion is vacuous: {silent}"
    )
    leaked = [
        e for e in silent["auxRawEntries"]
        if not e["key"].endswith("-code-verifier") and e["value"] in verifier_values
    ]
    assert not leaked, (
        f"the A5 probe retained the credential under a non-credential key: {leaked}"
    )

    # A store that ACCEPTS the probe but stores a DIFFERENT value for the real key
    # fails at the real-key read-back — AFTER the credential was written to it.
    # That is the only shape reaching `writeAux`'s catch with the real key
    # populated, so it is what pins the catch's `removeItem(key)`: without that
    # line the store keeps a copy while the writer relocates to the next store.
    norm = _run("click", search="", sessionMode="normalise")
    _assert_session_store_attempted(norm)
    assert norm["navs"], (
        f"the flow did not proceed although localStorage is usable: {norm}"
    )
    assert norm["sessionVerifierKeys"] == [], (
        f"the store that failed its own read-back retained the credential: "
        f"{norm['sessionVerifierKeys']}"
    )
    assert norm["localVerifierKeys"], (
        f"the verifier reached no store at all — the write was refused, not relocated: {norm}"
    )

    # …and a silent-remove store is not a way in when no store can be cleaned: the
    # guard's read-back-null refuses it too (fail closed).
    both_silent = _run("click", search="", sessionMode="silent-remove",
                       localMode="silent-remove", sessionQuota=130)
    assert both_silent["navs"] == [], (
        f"navigated although no store can be cleaned: {both_silent}"
    )
    assert both_silent["auxVerifierKeys"] == [], both_silent


def _cookie_value(header: str) -> str:
    """Decode the VALUE out of one raw `document.cookie` assignment.

    `cookieWrites[].header` is the full assignment string
    (`name=<encoded-value>; Path=/; ...`), so the value is the first
    `;`-segment after the first `=`. `unquote`, NOT `unquote_plus`: the page's
    `encodeURIComponent` renders a space as `%20` and never as `+`.
    """
    _, _, value = header.split(";", 1)[0].partition("=")
    return urllib.parse.unquote(value)


def test_inv7_item6_write_path_parity() -> None:
    """Invariant 7: ≤SIZE_GUARD is written byte-identically; over SIZE_GUARD is
    stripped — provider tokens dropped AND the non-essential-claim narrowing
    (`user.identities` / `user_metadata`) applied; over SIZE_CAP is refused (no
    write) AND reported on the page."""
    r = _run("item6")
    assert r["smallWritten"] is True, r
    assert r["strippedWrites"] == 1, (
        "the oversized write must emit exactly ONE assignment: none means the "
        "token assertion below is vacuous, and a second (under any name) would "
        f"persist un-narrowed data this test reads past; got "
        f"{r['strippedWrites']}: {r['strippedHeaders']}"
    )
    assert r["strippedHasToken"] is False, (
        f"the size guard did not strip provider tokens: {r}"
    )

    # Non-vacuity for the `if (obj.user)` narrowing (#3496 item 6): the payload
    # above carries a `user` object, so a dropped or misspelled narrowing branch
    # is OBSERVABLE in the assertions below. WITHOUT a `user` object the
    # provider-token deletes alone bring the value under SIZE_GUARD (#1225), so
    # the item6 fixture needs one for the narrowing to do any work — the driver
    # scenario states the same beside the fixture. (With this fixture's large
    # `user`, the token deletes alone leave the value ABOVE SIZE_CAP, so the
    # narrowing — not the token strip — is what makes this write land at all.)
    sessions = []
    for header in r["strippedHeaders"]:
        value = _cookie_value(header)
        if not value:
            continue
        try:
            sessions.append(json.loads(value))
        except ValueError:
            continue
    assert sessions, f"no parseable session reached the cookie jar: {r}"

    # The written session is characterised COMPLETELY — every key AND every value
    # — rather than by enumerating the bad things we happened to think of. A
    # negative check ("nothing un-narrowed survives anywhere") is a partial
    # denylist: it goes false the moment a re-attach lands in a slot it does not
    # name (`obj.user.id = md.<bloat>`) or a SECOND cookie carries the bloat past
    # the one entry this reads. A complete positive assertion is defeated only by
    # a mutation that writes a DIFFERENT artifact — which is what it exists to
    # catch.
    #
    # `strippedWrites == 1` above plus the non-empty `sessions` here is what makes
    # this the ONLY write to characterise.
    sess = sessions[0]
    assert sess == {
        "access_token": "a" * 100,
        "refresh_token": "r" * 50,
        "expires_at": 9,
        "user": {
            "id": "u-1",
            "user_metadata": {
                "display_name": "Ada Lovelace",
                "avatar_url": "https://a.example/a.png",
                "full_name": "Ada Lovelace",
                "name": "ada",
            },
        },
    }, f"the written session is not the fully-narrowed one: {sess}"

    assert r["overCapWrote"] is False, f"an over-cap write reached the cookie: {r}"
    assert r["overCapReported"] is True, f"the over-cap refusal was not reported: {r}"
    assert r["overCapText"].strip(), (
        f"the over-cap refusal was visible but carried no message: {r}"
    )


def test_inv9_weak_challenge_refused_with_a_guard_removed_control() -> None:
    """Invariant 9: without `crypto.subtle` the page refuses to initiate. The
    paired control removes the guard and asserts the bundle really does downgrade
    the challenge to `plain` — so the guard is doing work, not merely present.

    The no-subtle mode must keep `getRandomValues` (a real non-secure-context
    browser has it); with `crypto = {}` the bundle throws BEFORE the downgrade,
    which would make the control pass for the wrong reason."""
    r = _run("click", search="", noSubtle=True)
    assert r["navs"] == [], f"initiated without WebCrypto: {r}"
    assert r["errorVisible"] is True, r
    assert r["errorText"].strip(), (
        f"the refusal was visible but carried no message: {r}"
    )

    page = _render_page().replace("const incap = pkceIncapable();", "const incap = null;")
    ctl = _run("grant", page=page, noSubtle=True)
    assert ctl["method"] == "plain", (
        f"the guard-removed control did not downgrade to plain: {ctl}"
    )


def test_inv11_return_target_is_canonicalised_with_a_control() -> None:
    """Invariant 11: the `redirect_to` the page hands to the provider is rebuilt
    from a SANITISED query — no `code`/`error`/`error_description`/`sb_flow_id`
    is echoed. The nested `redirect_to` is parsed explicitly: the assign URL's own
    origin is the Supabase host and carries the transients percent-encoded
    INSIDE `redirect_to`, so asserting on the assign URL directly is both a false
    red and a permanent false green.

    The control restores the raw-search form and asserts the transient IS echoed,
    so the assertion discriminates."""
    hostile = ("?client_id=x&redirect_uri=https%3A%2F%2Fclient.example%2Fcb"
               "&response_type=code&code_challenge=chal&code_challenge_method=S256"
               "&scope=mcp&state=st-1&code=STALE&error=access_denied"
               "&error_description=leak&sb_flow_id=flow123")
    r = _run("return_target", search=hostile)
    assert r["innerOrigin"] == PAGE_ORIGIN, r
    assert r["innerPath"] == AUTHORIZE_PATH, r
    assert r["innerTransients"] == [], (
        f"a transient was echoed into the return target: {r['innerTransients']}"
    )
    # The OTHER half of the contract: the params the flow NEEDS must SURVIVE the
    # strip. The server re-renders PARAMS from this callback URL, so silently
    # dropping `state` or `code_challenge` here breaks /oauth/consent and the MCP
    # handshake while every "the transient was removed" assertion above stays
    # green — an over-strip is a permanent false green without this.
    for required in ("client_id", "redirect_uri", "response_type",
                     "code_challenge", "code_challenge_method", "state", "scope"):
        assert required in r["innerParams"], (
            f"required authorize param {required!r} was stripped from the return "
            f"target: {r['innerParams']}"
        )

    guard_off = _render_page().replace(
        "options: { redirectTo: authorizeReturnTo() },",
        "options: { redirectTo: window.location.origin + AUTHORIZE_PATH + window.location.search },",
    )
    ctl = _run("return_target", page=guard_off, search=hostile)
    assert "code" in ctl["innerTransients"], (
        f"the control did not echo the transient — the assertion is vacuous: {ctl}"
    )


# ── #5734: sign-in refusal UX — option C (detect at load) + B (per-cause) ───

# Any message on THIS surface that attributes a limitation to the browser (or to
# the product as a whole) is the #4678 absolute: the auth code and docs have
# repeatedly asserted browser-level session material that a live legacy path
# falsifies. The copy must name this PAGE as the subject instead.
#
# The arms are the observed CLASSES of that framing, not just the word "browser".
# The private/incognito arm matches an ATTRIBUTION — the browsing mode as the
# SUBJECT with the limiting verb adjacent — and NOT a bare mention, because the
# remedy this issue prescribes for the no-store cause is literally "leave private
# browsing": a bare-phrase arm would flag the prescribed copy itself. The
# control set below keeps each arm honest (an arm that cannot match guards
# nothing, so a typo in the second alternative would pass a single control).
_GLOBAL_CLAIM_RE = re.compile(
    r"\bbrowsers?\b"
    r"|\b(?:private|incognito)(?:\s+(?:browsing|mode|window|tab)){1,2}\s+"
    r"(?:blocks?|blocked|is\s+blocking|prevents?|prevented|is\s+preventing|"
    r"disallows?|forbids?|stops?|breaks?|is\s+unusable|does\s?n[o']t\s+allow)\b"
    r"|\bfor (?:all|every) users\b"
    r"|\bTortoise (?:does not|doesn't|won't|cannot|can't) support\b",
    re.IGNORECASE,
)

# One known global claim PER ARM, plus one per extra FORM the second arm accepts
# (an interposed mode word, a present-continuous verb). An arm or a form the
# control set does not exercise is unguarded — a single control proves only that
# the pattern can match at all.
_GLOBAL_CLAIM_CONTROLS = (
    "This browser cannot complete a secure sign-in here.",                    # arm 1
    "Private browsing prevents this page from storing the sign-in.",          # arm 2
    "Private browsing is blocking the sign-in on this page.",                 # arm 2, continuous
    "Incognito mode is blocking site storage for this page.",                 # arm 2, mode word
    "Private browsing mode prevents this page from storing the sign-in.",     # arm 2, both
    "Sign-in is unavailable for all users here.",                             # arm 3
    "Tortoise does not support this configuration.",                          # arm 4
)


def test_5734_capability_is_probed_at_load_and_disables_the_providers() -> None:
    """Option C: at page LOAD an incapable page disables the provider buttons and
    shows the explanation inline, so the user never takes the dead-end action.

    Three incapacity shapes: no WebCrypto, both stores refusing by method throw,
    and both store objects throwing on ACCESS — plus TextEncoder absent, the
    probe's OTHER conjunct (`pkceIncapable()` returns `no-webcrypto` for either
    missing conjunct, so a copy that names only WebCrypto asserts a cause its own
    probe contradicts). The paired control at the end (a capable load) requires
    the buttons USABLE and the notice EMPTY — without it, "the notice is present"
    could pass with the notice hard-wired visible.

    The CAUSE and the REMEDY are asserted separately per case, so neither half of
    option B can be emptied while the other keeps the test green."""
    for mode, cause, remedy in (
        ({"noSubtle": True},
         "secure-context crypto", "Open this page over HTTPS"),
        ({"noTextEncoder": True},
         "text encoding", "Open this page over HTTPS"),
        ({"sessionMode": "throw-method", "localMode": "throw-method"},
         "usable site storage", "Allow site storage for this page"),
        ({"accessThrow": True},
         "usable site storage", "Allow site storage for this page"),
    ):
        r = _run("load", search="", **mode)
        assert r["githubDisabled"] is True, f"GitHub was clickable at load: {r} ({mode})"
        assert r["googleDisabled"] is True, f"Google was clickable at load: {r} ({mode})"
        assert r["capabilityVisible"] == "block", f"no inline explanation: {r} ({mode})"
        assert "this page" in r["capabilityText"].lower(), r
        assert cause in r["capabilityText"], (
            f"the copy did not name the cause the probe established: {r} ({mode})"
        )
        assert remedy in r["capabilityText"], (
            f"the copy named no remedy for its cause: {r} ({mode})"
        )
        # The copy's remedy is an ALTERNATIVE ("...or sign in with email and
        # password below"), so the alternative has to survive the state that
        # promises it: disabling the email button too would leave the user with a
        # remedy the page itself has closed.
        assert r["emailDisabled"] is False, (
            f"the incapable state disabled the alternative its own copy names: {r} ({mode})"
        )
        assert "email and password" in r["capabilityText"], (
            f"the copy named no alternative the page keeps usable: {r} ({mode})"
        )

    ok = _run("load", search="")
    assert ok["githubDisabled"] is False, f"a capable load disabled a provider: {ok}"
    assert ok["googleDisabled"] is False, ok
    assert ok["emailDisabled"] is False, ok
    assert ok["capabilityVisible"] == "none", ok
    assert ok["capabilityText"] == "", ok


def test_5734_capability_lost_after_load_still_refuses_at_click() -> None:
    """Option C's boundary: the load probe is an AFFORDANCE, never the security
    boundary. Capability is lost between load and click — WebCrypto removed, and
    the stores replaced by refusing ones — and the click must STILL refuse, with
    no verifier written anywhere.

    The `loadGithubDisabled is False` control is load-bearing, but not for the
    reason a `disabled` attribute might suggest: the harness invokes the button's
    REAL bound handler, and a programmatic call runs a handler even when the
    button is disabled. What the control proves is that the load probe left the
    button ENABLED, so the refusal below came from the click-path re-check rather
    than from a button the load probe had already disabled. `githubDisabled is
    True` after the call then shows the affordance followed reality, rather than
    leaving a live-looking button.

    The expected notice is pinned PER CAUSE, so the re-check cannot render a cause
    it did not observe (a hard-coded `renderCapability("no-store")`)."""
    for lose, provider, cause, remedy in (("subtle", "github", "secure-context crypto",
                                         "Open this page over HTTPS"),
                                        ("store", "github", "usable site storage",
                                         "Allow site storage for this page"),
                                        # The OTHER provider button carries its own
                                        # `onclick` binding, so driving only GitHub
                                        # would let a dead Google button ship.
                                        ("subtle", "google", "secure-context crypto",
                                         "Open this page over HTTPS")):
        r = _run("click_after_load", search="", lose=lose, provider=provider)
        assert r["provider"] == provider, r
        assert r["loadProviderDisabled"] is False, (
            f"the load probe had already disabled the {provider} button, so this "
            f"scenario does not exercise the click-time re-check: {r} ({lose})"
        )
        assert r["loadEmailDisabled"] is False, (
            f"the load probe disabled the email form, which the copy names as the "
            f"remedy for exactly this state: {r} ({lose})"
        )
        assert r["navs"] == [], f"navigated although capability was lost: {r} ({lose})"
        assert r["errorVisible"] is True, f"no refusal reported: {r} ({lose})"
        assert r["clickedProviderLabel"] in r["errorText"], (
            f"the {provider} button's handler asked for a different provider: "
            f"{r['errorText']!r} names no {r['clickedProviderLabel']!r}"
        )
        assert r["providerDisabled"] is True, (
            f"{provider}'s affordance did not follow the click-time re-check (a dead "
            f"onclick binding looks exactly like this): {r} ({lose})"
        )
        assert r["emailDisabled"] is False, (
            f"the click-time re-check disabled the alternative the refusal's own "
            f"copy names: {r} ({lose})"
        )
        assert cause in r["capabilityText"], (
            f"the click-time re-check rendered the wrong cause: {r} ({lose})"
        )
        assert remedy in r["capabilityText"], (
            f"the click-time re-check named no remedy: {r} ({lose})"
        )
        assert r["retrySigninVisible"] == "block", (
            f"a failed attempt offered no retry affordance: {r} ({lose})"
        )
        # EFFECTIVE visibility, not just the button's own inline style. The
        # affordance is a sibling of #error, OUTSIDE both views (asserted
        # structurally below), so a `block` style is genuinely visible; the view
        # assertion additionally pins that the page stayed on the sign-in state.
        assert r["viewSignin"] == "block", (
            f"the refusal state did not show the sign-in view: {r} ({lose})"
        )
        assert r["viewConsent"] == "none", (
            f"the consent view was entered for a refused/unusable attempt: {r} ({lose})"
        )
        assert r["auxVerifierKeys"] == [], r
        assert r["cookieVerifierKeys"] == [], r


def test_5734_post_redirect_copy_names_its_cause_and_remedy() -> None:
    """Option B: every post-redirect terminal state names its CAUSE and its
    REMEDY, and offers the retry affordance.

    Two different causes must render DIFFERENT copy — that is what discriminates
    per-cause messages from the single generic line this issue removes — and each
    must carry its OWN remedy, asserted by phrase rather than by the shared word
    "Retry" (a shared-token assertion left an emptied remedy green). The
    bounded-description contract (#3496 inv 5) is preserved: the provider's
    description is appended inside the remaining budget, which that test's ≤300
    assertion still pins (and which this test checks does not swallow the fixed
    copy)."""
    declined = _run("load", search="?error=access_denied&state=st-1", awaitTerminal=True)
    assert declined["errorVisible"] is True, declined
    assert "declined" in declined["errorText"], declined
    assert "pick an account and approve" in declined["errorText"], declined
    assert declined["retrySigninVisible"] == "block", declined

    temporary = _run("load", search="?error=server_error", awaitTerminal=True)
    assert temporary["errorVisible"] is True, temporary
    assert "temporary failure" in temporary["errorText"], temporary
    assert "Retry in a moment" in temporary["errorText"], temporary
    assert temporary["retrySigninVisible"] == "block", temporary
    assert temporary["errorText"] != declined["errorText"], (
        "two different causes rendered the same copy — the generic line the "
        f"issue removes: {temporary['errorText']!r}"
    )

    # Every ALIAS of a class is pinned per alias, not just the class's
    # representative: each variant's own condition has to select its branch. If it
    # does not, the copy falls through to the generic branch and states a cause
    # that branch's own condition cannot establish — the defect this cheap loop
    # closes.
    unavailable = _run("load", search="?error=temporarily_unavailable", awaitTerminal=True)
    assert "temporary failure" in unavailable["errorText"], unavailable
    assert "Retry in a moment" in unavailable["errorText"], unavailable
    for reauth in ("consent_required", "interaction_required",
                   "account_selection_required"):
        r = _run("load", search=f"?error={reauth}", awaitTerminal=True)
        assert "provider's sign-in" in r["errorText"], r
        assert "finish the provider's sign-in" in r["errorText"], r

    oauth_err = _run("load", search="?error=login_required", awaitTerminal=True)
    assert "provider's sign-in" in oauth_err["errorText"], oauth_err
    assert "finish the provider's sign-in" in oauth_err["errorText"], oauth_err
    assert oauth_err["errorText"] not in (declined["errorText"], temporary["errorText"]), (
        oauth_err
    )

    # A code that came back but was never exchanged carries NO provider `error`,
    # so it takes the last branch — the dead end the terminal state exists to
    # remove, and the one a `error`-only discriminator would miss.
    unexchanged = _run("load", search="?code=STALE&state=st-1",
                       seedVerifier="verifier-abc", exchangeFails=True,
                       awaitTerminal=True)
    assert unexchanged["errorVisible"] is True, (
        f"a returned-but-unexchanged code produced no explanation: {unexchanged}"
    )
    assert "could not finish" in unexchanged["errorText"], unexchanged
    assert "allow storage for it and reload" in unexchanged["errorText"], unexchanged
    assert unexchanged["retrySigninVisible"] == "block", unexchanged
    assert unexchanged["errorText"] != declined["errorText"], unexchanged

    # A transient that is NOT a code (`?sb_flow_id=` — a real GoTrue PKCE param)
    # reaches the same terminal state, but no code was ever received, so the copy
    # must not claim one was exchanged: the stated cause has to match what the
    # branch condition actually establishes.
    other = _run("load", search="?sb_flow_id=flow123&state=st-1", awaitTerminal=True)
    assert other["errorVisible"] is True, other
    assert "no session was established" in other["errorText"], other
    # The claim that must NOT appear is a POSITIVE one — that a code came back /
    # was exchanged. Denylisting the NEGATED phrase ("not exchanged") would be
    # vacuous: it appears nowhere in the page, so rewriting this branch to
    # ``(the provider code was exchanged, but no session was established)`` left
    # it green. Assert the branch does not mention a code at all — it received
    # none.
    assert "code" not in other["errorText"].lower(), other
    assert "code" in unexchanged["errorText"].lower(), unexchanged
    assert "start again from your MCP client" in other["errorText"], other
    assert other["errorText"] != unexchanged["errorText"], other

    # The GENERIC branch — a provider error the page has no specific copy for —
    # names its own remedy. It is the one terminal cause whose remedy cannot be
    # pinned by borrowing a sibling's phrase, and an emptied remedy still leaves
    # `cause + " "` non-empty, so without this assertion the branch would carry no
    # remedy and every test would stay green.
    generic = _run("load", search="?error=weird_provider_code", awaitTerminal=True)
    assert "start again from your MCP client" in generic["errorText"], (
        f"the generic terminal branch named no remedy: {generic['errorText']!r}"
    )
    assert generic["retrySigninVisible"] == "block", generic
    assert generic["errorText"] not in (declined["errorText"], temporary["errorText"],
                                       oauth_err["errorText"], unexchanged["errorText"],
                                       other["errorText"]), generic

    # The third post-redirect terminal state — the refused over-SIZE_CAP write —
    # is reported by the write path (inv 7 pins that it is refused AND reported),
    # so its copy is read from there.
    over = _run("item6")
    assert over["overCapReported"] is True, over
    assert "this page" in over["overCapText"].lower(), over
    assert "fewer linked providers" in over["overCapText"], (
        f"the over-cap refusal is pinned only by the shared word 'Retry': "
        f"{over['overCapText']!r}"
    )
    assert over["retrySigninVisible"] == "block", (
        f"the over-cap refusal named a retry but offered no affordance: {over}"
    )
    assert over["viewSignin"] == "block", over

    # The affordance must be effectually visible wherever it is shown, which a
    # `display:block` style alone does not establish when the button sits inside a
    # hidden view. That is a STRUCTURAL contract, so it is asserted on the
    # rendered page: `#error` follows both `#view-*` blocks, so a button after it
    # is outside them.
    html = _render_page()
    assert html.index('id="btn-retry-signin"') > html.index('id="error"'), (
        "the retry affordance is back inside a view — inside #view-signin it is "
        "hidden whenever the consent view is shown, so the refusal's 'Retry' would "
        "name a control the user cannot see"
    )


def test_5734_consent_messages_stay_page_scoped() -> None:
    """The #4678 lesson, pinned on the messages this issue adds: every one is
    about THIS PAGE. A message that attributes a capability limit to the browser
    (or to the product as a whole) is a global claim, and this surface's auth
    history is a list of those being falsified by a live legacy path.

    The assertion is positive (each capability message names this page) AND
    negative (NO collected message names the browser / the product), so neither
    half can be satisfied by saying nothing — an empty message fails the positive
    half, and the fixed copy cannot pass the negative half by accident.

    EVERY terminal cause is collected, not one: with only the declined branch
    collected, a browser-global claim in any other branch's copy goes unseen. One
    sample cannot pin a per-cause surface, and the browser-global claim is exactly
    what a per-cause rewrite would reintroduce."""
    # The capability copy, per path and per cause. The click path renders the SAME
    # notice as the load path (`renderCapability` refreshes the affordance on the
    # way out); the per-cause correctness of each path is pinned in
    # test_5734_capability_lost_after_load_still_refuses_at_click.
    capability_copy = []
    for mode in ({"noSubtle": True},
                 {"sessionMode": "throw-method", "localMode": "throw-method"}):
        load = _run("load", search="", **mode)
        capability_copy.append(load["capabilityText"])
        click = _run("click", search="", **mode)
        assert click["errorVisible"] is True, click
        capability_copy.append(click["errorText"])
        capability_copy.append(click["capabilityText"])

    # EVERY post-redirect cause, so no branch can carry a browser-level claim
    # unobserved.
    terminal_causes = {
        "declined": ("?error=access_denied&error_description=boom", {}),
        "temporary": ("?error=server_error", {}),
        "temporarily_unavailable": ("?error=temporarily_unavailable", {}),
        "reauth": ("?error=login_required", {}),
        "consent_required": ("?error=consent_required", {}),
        "interaction_required": ("?error=interaction_required", {}),
        "account_selection": ("?error=account_selection_required", {}),
        "provider_refused": ("?error=weird_code", {}),
        "code_not_exchanged": ("?code=STALE&state=st-1",
                               {"seedVerifier": "verifier-abc", "exchangeFails": True}),
        "other_transient": ("?sb_flow_id=flow123&state=st-1", {}),
    }
    terminal_copy = {}
    for name, (search, opts) in terminal_causes.items():
        r = _run("load", search=search, awaitTerminal=True, **opts)
        assert r["errorVisible"] is True, f"{name} rendered no terminal state: {r}"
        terminal_copy[name] = r["errorText"]
    # …and the bounded provider description still reaches the user through the
    # append (the ≤300 bound itself is pinned by inv 5).
    assert "boom" in terminal_copy["declined"], (
        "the bounded provider description must still reach the user: "
        + terminal_copy["declined"]
    )

    over = _run("item6")

    for msg in capability_copy:
        assert "this page" in msg.lower(), f"capability copy is not page-scoped: {msg!r}"
    assert "this page" in over["overCapText"].lower(), over["overCapText"]
    for msg in capability_copy + list(terminal_copy.values()) + [over["overCapText"]]:
        assert msg, "an empty message was asserted — this test would be vacuous"
        assert not _GLOBAL_CLAIM_RE.search(msg), f"global claim in message: {msg!r}"

    # Each arm must be able to match, or that arm guards nothing — a typo in the
    # second or third alternative passes a single match-anything control.
    for claim in _GLOBAL_CLAIM_CONTROLS:
        assert _GLOBAL_CLAIM_RE.search(claim), (
            "_GLOBAL_CLAIM_RE does not match a known global claim, so the "
            f"negative assertion above is vacuous for it: {claim!r}"
        )
    # …and the PER-CAUSE contract is pinned for the capability causes too: a
    # single shared string (or a fallthrough to the `|| CAPABILITY["no-store"]`
    # default) must not pass.
    assert capability_copy[0] != capability_copy[3], (
        "both incapacity causes rendered the same copy, so per-cause copy is not "
        f"pinned: {capability_copy[0]!r}"
    )


def test_5734_a_refused_write_is_not_overwritten_by_the_terminal_state() -> None:
    """The refused over-SIZE_CAP write is one of the terminal states this issue
    gives per-cause copy, and its message must SURVIVE the two transitions that
    can erase it. The #3496 scoping doc (Step 7) records exactly this as #5734's
    work, not #3496's: "that its message SURVIVES the consent view (a stale
    session currently lets `showConsentOnce()` `hideError()` it)".

    Driven through the REAL exchange callback — a `?code=` load against a token
    endpoint whose session exceeds the cookie cap — not by calling
    `storage.setItem` directly as the `item6` scenario does. That is what makes
    the erasers reachable: the library believes a refused write persisted, so it
    re-reads the cookie in `onAuthStateChange` and lands on the terminal fallback,
    whose generic `?code` branch would otherwise replace the accurate copy with a
    cause it cannot establish. The `staleSession` variant is the second eraser: a
    session already in the cookie makes the consent view render, and its
    `hideError()` would clear the refusal.

    Without `writeRefused` the first variant reads the terminal fallback's own
    copy — "This page could not finish the sign-in it had started (a code came
    back, but no session was established on this page)." — instead of the
    refused-write copy, and the second (a STALE session in the cookie) enters the
    consent view for that older session and reports its own state instead.

    The stale variant asserts the consent view is NOT entered: a refusal means the
    user's sign-in did not complete, so presenting consent for a superseded
    session is the wrong state — and every message the consent flow can render is
    an eraser. This is the IN-FLIGHT case (the flow is already awaiting
    `getSession()` when the refusal lands).

    Two further checks:

    * an `viaEmail` variant reaches the SAME refusal through the email/password
      form. There is no `?code` transient there, so the terminal fallback never
      runs and what follows the refusal is the flow's NO-SESSION branch — the
      path on which the refusal's copy would name a retry with no control, because
      `showSignin()` drops the affordance and only re-asserts it while a refusal is
      pending.
    * the precedence rule itself is pinned DIRECTLY on the page's own
      `showError`/`hideError`/`cookieStorage.setItem`, because once the consent
      view is not entered, NO UI path reaches `hideError` with a refusal pending —
      a guard no UI test can lose is a guard no test pins."""
    stale_session = ('{"access_token":"stale","refresh_token":"r",'
                     '"expires_at":9999999999,"expires_in":3600,'
                     '"token_type":"bearer","user":{"id":"u-old"}}')

    fresh = _run("overcap_exchange", search="?code=OK&state=st-1",
                 seedVerifier="verifier-abc", exchangeOversized=True)
    assert fresh["exchangeCount"] == 1, (
        f"the harness did not drive a real exchange: {fresh}"
    )
    assert fresh["oversizedLanded"] is False, (
        f"the refused session reached the cookie jar anyway: {fresh}"
    )
    assert fresh["errorVisible"] is True, f"the refused write was not reported: {fresh}"
    assert "cookie limit" in fresh["errorText"], (
        f"the accurate refused-write copy did not survive the terminal state: "
        f"{fresh['errorText']!r}"
    )
    assert "could not finish" not in fresh["errorText"], (
        f"the terminal fallback overwrote the refusal with a cause it cannot "
        f"establish: {fresh['errorText']!r}"
    )
    assert fresh["pageReplaced"] is True, (
        f"the terminal branch never completed, so its eraser was not exercised: {fresh}"
    )
    assert fresh["viewSignin"] == "block", fresh
    assert fresh["viewConsent"] == "none", fresh
    assert fresh["retrySigninVisible"] == "block", fresh

    stale = _run("overcap_exchange", search="?code=OK&state=st-1",
                 seedVerifier="verifier-abc", exchangeOversized=True,
                 seedSession=stale_session)
    assert stale["exchangeCount"] == 1, stale
    assert stale["errorVisible"] is True, (
        f"the stale session erased the refused-write report: {stale}"
    )
    assert "cookie limit" in stale["errorText"], stale["errorText"]
    assert "could not finish" not in stale["errorText"], stale["errorText"]
    assert "session expired" not in stale["errorText"], stale["errorText"]
    assert "usable org" not in stale["errorText"], stale["errorText"]
    assert stale["viewConsent"] == "none", (
        f"the consent view was entered for a session the user did not just sign "
        f"in as, which is where the refusal gets replaced: {stale}"
    )
    assert stale["viewSignin"] == "block", stale
    assert stale["retrySigninVisible"] == "block", stale

    # The SAME refusal through the email/password form: no `?code` transient, so
    # the terminal fallback never runs, and the flow's no-session branch is what
    # follows. The refusal's copy says "Retry", so the affordance must survive it.
    by_email = _run("overcap_exchange", search="", exchangeOversized=True,
                    viaEmail=True)
    assert by_email["exchangeCount"] >= 1, (
        f"the email path did not reach the token endpoint: {by_email}"
    )
    assert by_email["errorVisible"] is True, by_email
    assert "cookie limit" in by_email["errorText"], by_email["errorText"]
    assert by_email["viewSignin"] == "block", by_email
    assert by_email["retrySigninVisible"] == "block", (
        f"the refused write told the user to retry but the email path's "
        f"showSignin() hid the affordance: {by_email}"
    )

    # The email form CLEARS the pending refusal before it attempts anything, so
    # the attempt's own failure can render. Without that clear the sticky guard
    # suppresses it and the user reads the stale refusal instead of why the
    # attempt they just made failed.
    bad_email = _run("overcap_exchange", search="", exchangeOversized=True,
                     viaEmail=True, thenBadEmail=True)
    assert "cookie limit" in bad_email["errorText"], bad_email
    assert bad_email["afterBadEmailErrorText"] != bad_email["errorText"], (
        f"the email form's own failure was suppressed by the stale refusal: "
        f"{bad_email['afterBadEmailErrorText']!r}"
    )
    assert "cookie limit" not in bad_email["afterBadEmailErrorText"], bad_email

    # The precedence rule, pinned DIRECTLY on the page's own helpers: with a
    # refusal pending neither a hide nor a later error may touch it, and a write
    # that LANDS clears the state so the page can move on.
    assert fresh["afterHideRefusalVisible"] is True, (
        f"hideError() cleared a pending refusal: {fresh}"
    )
    assert fresh["afterShowErrorText"] == fresh["errorText"], (
        f"a later showError() replaced a pending refusal: {fresh}"
    )
    assert fresh["afterLandedWriteErrorVisible"] is False, (
        f"a write that landed left the refusal state stuck (the page could not "
        f"move on): {fresh}"
    )
    assert fresh["showErrorDroppedAffordance"] == "none", (
        f"showError() left the retry control behind for a message that implies no "
        f"retry: {fresh}"
    )
    assert fresh["hideErrorDroppedAffordance"] == "none", (
        f"hideError() left the retry control behind: {fresh}"
    )
    assert fresh["flowGuardShared"] is True, (
        f"a concurrent runConsentFlow() caller resolved before the flow it did "
        f"not start has finished: {fresh}"
    )
    assert fresh["expiredKeptRefusal"] == "THE REFUSAL", (
        f"a message from a caller that lands on the sign-in view with a refusal "
        f"pending replaced the refusal: {fresh['expiredKeptRefusal']!r}"
    )
    assert fresh["expiredRetryVisible"] == "block", (
        f"that caller dropped the refusal's retry control: {fresh}"
    )
    assert fresh["expiredViewSignin"] == "block", fresh

def test_5734_the_retry_affordance_reprobes_capability() -> None:
    """The retry handler re-probes capability (`renderCapability(pkceIncapable())`),
    because it may have been lost OR restored since the attempt.

    `lose="subtle"` discriminates: a handler that renders a hard-coded capable
    state instead of the probe's verdict leaves the buttons enabled and reddens
    this. `lose="restore"` is the mirror — the page loaded
    incapable and WebCrypto comes back — proving the probe is live rather than
    the load-time verdict."""
    lost = _run("retry_signin", search="?error=access_denied", lose="subtle")
    assert lost["beforeGithubDisabled"] is False, lost
    assert lost["beforeCapabilityVisible"] == "none", lost
    assert lost["beforeRetrySigninVisible"] == "block", (
        f"the terminal state did not offer the retry affordance at all: {lost}"
    )
    assert lost["githubDisabled"] is True, lost
    assert lost["capabilityVisible"] == "block", lost
    assert "this page" in lost["capabilityText"].lower(), lost
    assert lost["errorVisible"] is False, lost
    assert lost["retrySigninVisible"] == "none", lost

    restored = _run("retry_signin", search="", noSubtle=True, lose="restore")
    assert restored["beforeGithubDisabled"] is True, restored
    assert restored["beforeCapabilityVisible"] == "block", restored
    assert restored["githubDisabled"] is False, restored
    assert restored["capabilityVisible"] == "none", restored
    assert restored["capabilityText"] == "", restored


def test_5734_the_retry_the_refusal_names_clears_it() -> None:
    """The refusal's copy says "Retry", so the affordance it renders has to USHER
    THE USER ON. The handler's `writeRefused = false` is what lets its
    `hideError()` act: with the sticky guard still set, the refusal stays on
    screen and the user is stuck on the dead end the copy describes. Asserted as a
    DELTA (the scenario proves the refusal was up first), so the check cannot pass
    vacuously.

    The second half pins the other end of the same rule: a pending refusal must
    not suppress the NEXT attempt's own message. A second refusal is raised
    through the page's own write path, capability is lost under it, and the
    provider click's re-check must be able to render its own cause and copy — a
    `writeRefused` that click had not cleared would leave the stale cookie-limit
    text on screen and no capability notice at all.
    """
    r = _run("overcap_exchange", search="?code=OK&state=st-1",
             seedVerifier="verifier-abc", exchangeOversized=True, thenRetry=True)
    assert r["errorVisible"] is True and "cookie limit" in r["errorText"], r
    assert r["retryClearedError"] is True, (
        f"the retry the refusal named left the refusal on screen, so the user "
        f"cannot move on: {r}"
    )
    assert r["retryAffordance"] == "none", (
        f"the retry left its own control behind with no message implying one: {r}"
    )
    assert r["secondRefusalRaised"] is True, (
        f"the second refusal was never raised, so the click below proves nothing: {r}"
    )
    assert "secure-context crypto" in r["afterClickCapabilityText"], (
        f"the click-time re-check rendered no cause after a pending refusal: {r}"
    )
    assert "note above the buttons" in r["afterClickErrorText"], (
        f"the click-time refusal was suppressed by a still-pending refusal: {r}"
    )
    assert "cookie limit" not in r["afterClickErrorText"], r
    assert r["afterClickRetry"] == "block", r
    assert r["afterClickNavs"] == [], (
        f"the click proceeded although capability was lost: {r}"
    )


def test_5734_a_message_that_does_not_imply_a_retry_carries_no_control() -> None:
    """The retry affordance is a sibling of `#error`, OUTSIDE both views, so once
    a message has shown it, only a message can end its warrant. A transition into
    a state whose message does not imply a retry has to drop it: in the consent
    view a stray control sits under the approve/deny decision, and its handler
    runs `showSignin()` — silently bouncing the user out of that decision and
    abandoning it."""
    r = _run("email_after_terminal", search="?error=access_denied&state=st-1")
    assert r["beforeRetrySigninVisible"] == "block", (
        f"the terminal state never showed the affordance, so this scenario "
        f"exercises nothing: {r}"
    )
    assert r["viewConsent"] == "block", (
        f"the scenario never reached the consent view, whose message is the one "
        f"that has to drop the affordance: {r}"
    )
    assert r["viewSignin"] == "none", r
    assert r["retrySigninVisible"] == "none", (
        f"a control whose message no longer implies a retry is still visible in "
        f"the consent view (its handler would abandon the decision): {r}"
    )


def test_5734_a_validation_message_does_not_leave_the_retry_control() -> None:
    """The email form's own VALIDATION message (empty password) is not a refusal,
    so the control the previous refusal rendered must not survive it — the user is
    on the sign-in view being told to fill the form in, with a stale "Try again"
    underneath. The same eraser class as the consent-view case, at the second site
    a transition renders a message with no retry behind it."""
    r = _run("email_after_terminal", search="?error=access_denied&state=st-1",
             blankPassword=True)
    assert r["beforeRetrySigninVisible"] == "block", (
        f"the terminal state never showed the affordance, so this scenario "
        f"exercises nothing: {r}"
    )
    assert r["errorText"] == "Enter email and password.", r
    assert r["retrySigninVisible"] == "none", (
        f"the form's validation message left the previous refusal's control on "
        f"screen: {r}"
    )


def test_5734_entering_the_consent_view_drops_the_terminal_retry_control() -> None:
    """The consent view's ENTRY drops the retry control a terminal state showed.
    The control is a sibling of `#error` and outside both `#view-*` blocks, so the
    view switch does not contain it: on this path (no form involved, and a preview
    that RESOLVES, so it renders no message of its own) `showConsentOnce()`'s own
    `hideError()` is the only transition that can drop it.

    The `beforeRetrySigninVisible` control is load-bearing, as is the consent-view
    assertion: a page that never showed the control, or that never reached the
    view, would otherwise pass without exercising the entry."""
    r = _run("consent_after_terminal", search="?error=access_denied&state=st-1",
             previewOk=True)
    assert r["beforeRetrySigninVisible"] == "block", (
        f"the terminal state never showed the control, so this scenario exercises "
        f"nothing: {r}"
    )
    assert r["viewConsent"] == "block", (
        f"the scenario never entered the consent view, so it does not exercise the "
        f"entry that drops the control: {r}"
    )
    assert r["viewSignin"] == "none", r
    assert r["authorizeEnabled"] is True, (
        f"the consent view did not complete its own render: {r}"
    )
    assert r["retrySigninVisible"] == "none", (
        f"the control whose message the consent view replaced is still visible "
        f"under the approve/deny decision (its handler leaves that decision): {r}"
    )


def test_5734_the_consent_view_reports_a_preview_with_no_org() -> None:
    """A preview that resolves with neither `memberships` nor `org_id` must REPORT
    and disable Authorize. Without it the consent view renders with the button
    permanently disabled and no message — a dead end of exactly the class this
    issue removes, and the message is one of the erasers the refusal must survive.

    The default harness preview response is that shape, so this shares the
    `consent_after_terminal` route with no `previewOk`."""
    r = _run("consent_after_terminal", search="?error=access_denied&state=st-1")
    assert r["beforeRetrySigninVisible"] == "block", r
    assert r["viewConsent"] == "block", r
    assert r["authorizeEnabled"] is False, (
        f"Authorize was left enabled with no org resolved: {r}"
    )
    assert r["errorVisible"] is True, (
        f"the no-org state was not reported at all: {r}"
    )
    assert r["errorText"] == "No usable org for this account.", (
        f"the no-org state was not named: {r['errorText']!r}"
    )
    assert r["retrySigninVisible"] == "none", (
        f"a control with no message implying a retry is visible under the "
        f"approve/deny decision: {r}"
    )


# ── the Node driver ─────────────────────────────────────────────────────────

_DRIVER_JS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const nodeCrypto = require('crypto');

const bundlePath = process.argv[2];
const pagePath = process.argv[3];
const opts = JSON.parse(process.argv[4] || '{}');

const bundle = fs.readFileSync(bundlePath, 'utf8');
const page = fs.readFileSync(pagePath, 'utf8');

const COOKIE_NAME = opts.cookieName || 'sb-tortoise-auth-token';

function mkEl(id) {
  const set = new Set();
  return {
    id: id,
    style: {},
    textContent: '',
    value: '',
    disabled: false,
    firstChild: null,
    children: [],
    classList: {
      add: function (c) { set.add(c); },
      remove: function (c) { set.delete(c); },
      contains: function (c) { return set.has(c); },
      toggle: function (c) { if (set.has(c)) { set.delete(c); } else { set.add(c); } },
    },
    appendChild: function (n) { this.children.push(n); this.firstChild = this.children[0]; },
    removeChild: function (n) {
      this.children = this.children.filter(function (x) { return x !== n; });
      this.firstChild = this.children[0] || null;
    },
    setAttribute: function () {}, removeAttribute: function () {},
    addEventListener: function () {}, focus: function () {}, blur: function () {},
    getAttribute: function () { return null; },
  };
}

// Per-store attempt log. A store that is never in the chain leaves an assertion
// like `sessionVerifierKeys == []` green for the wrong reason — the store was not
// CLEARED, it was never TRIED — so the tests assert the store was attempted.
// Recorded BEFORE the method body runs, so a throwing method still counts.
const storeAttempts = [];

function makeStore(mode, quota, label) {
  const map = new Map();
  function rec(op, k) { storeAttempts.push(label + ':' + op + ':' + String(k)); }
  return {
    _map: map,
    get length() { return map.size; },
    key: function (i) { const k = Array.from(map.keys())[i]; return k === undefined ? null : k; },
    getItem: function (k) {
      rec('getItem', k);
      if (mode === 'throw-method') throw new Error('storage disabled');
      return map.has(String(k)) ? map.get(String(k)) : null;
    },
    setItem: function (k, v) {
      rec('setItem', k);
      if (mode === 'throw-method') throw new Error('storage disabled');
      v = String(v);
      // 'normalise': accepts every write but stores a DIFFERENT string for any
      // value that is not the A5 probe payload (`x…x`). The probe therefore
      // passes and the store is SELECTED; the failure appears only at the
      // real-key read-back — the one shape that reaches writeAux's catch with
      // the credential already written to this store.
      if (mode === 'normalise' && !/^x*$/.test(v)) v = v.toUpperCase();
      const cap = quota || opts.storeQuota;
      if (cap && v.length > cap) {
        const e = new Error('QuotaExceededError'); e.name = 'QuotaExceededError'; throw e;
      }
      map.set(String(k), v);
    },
    removeItem: function (k) {
      rec('removeItem', k);
      if (mode === 'throw-method' || mode === 'throw-remove') throw new Error('storage disabled');
      if (mode === 'silent-remove') return;   // accepted, but nothing happens
      map.delete(String(k));
    },
    clear: function () { map.clear(); },
  };
}

const cookieWrites = [];
const cookies = new Map();
const navs = [];
const replaceStates = [];
const warns = [];
const fetchCalls = [];
const els = {};

const location = {
  hostname: opts.hostname || 'api.premiselabs.co',
  pathname: '/oauth/authorize',
  protocol: 'https:',
  search: opts.search || '',
  hash: opts.hash || '',
};
location.origin = 'https://' + location.hostname;
Object.defineProperty(location, 'href', {
  get: function () { return location.origin + location.pathname + location.search + location.hash; },
  set: function (u) { navs.push(String(u)); },
  configurable: true,
});
location.assign = function (u) { navs.push(String(u)); };
location.replace = function (u) { navs.push(String(u)); };
location.reload = function () {};

const documentObj = {};
Object.defineProperty(documentObj, 'cookie', {
  get: function () {
    return Array.from(cookies.entries()).map(function (e) { return e[0] + '=' + e[1]; }).join('; ');
  },
  set: function (text) {
    text = String(text);
    const semi = text.indexOf(';');
    const first = semi < 0 ? text : text.slice(0, semi);
    const eq = first.indexOf('=');
    const name = (eq < 0 ? first : first.slice(0, eq)).trim();
    const value = eq < 0 ? '' : first.slice(eq + 1);
    const attrs = semi < 0 ? '' : text.slice(semi);
    const removed = /Max-Age=0/i.test(attrs) || value === '';
    if (removed) { cookies.delete(name); } else { cookies.set(name, value); }
    cookieWrites.push({ header: text, name: name, removed: removed, dropped: text.length > 4096 });
  },
  configurable: true,
});
documentObj.getElementById = function (id) { if (!els[id]) { els[id] = mkEl(id); } return els[id]; };
documentObj.createElement = function (tag) { return mkEl('created-' + tag); };
documentObj.querySelector = function () { return null; };
documentObj.querySelectorAll = function () { return []; };
documentObj.addEventListener = function () {};
documentObj.readyState = 'complete';
documentObj.head = mkEl('head');
documentObj.body = mkEl('body');

// Seed state BEFORE the page script runs (the library reads the cookie in
// createClient()'s _initialize).
if (opts.seedSession) { cookies.set(COOKIE_NAME, opts.seedSession); }
const sessionStorage = makeStore(opts.sessionMode || 'ok', opts.sessionQuota, 'session');
const localStorage = makeStore(opts.localMode || 'ok', opts.localQuota, 'local');
if (opts.seedVerifier) {
  const target = (opts.seedVerifierStore === 'local') ? localStorage : sessionStorage;
  target.setItem(COOKIE_NAME + '-code-verifier', JSON.stringify(opts.seedVerifier));
}

function jsonResponse(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200, headers: { 'Content-Type': 'application/json' },
  });
}

function fetchImpl(input, init) {
  const url = typeof input === 'string' ? input : (input && input.url ? input.url : String(input));
  const body = init && init.body ? String(init.body) : '';
  fetchCalls.push({ url: url, method: (init && init.method) || 'GET', body: body });
  if (url.indexOf('/auth/v1/token') >= 0) {
    if (opts.exchangeFails) { return jsonResponse({ error: 'invalid_grant' }, 400); }
    if (opts.exchangeOversized) {
      // #5734: a session that survives the write path's narrowing and still
      // exceeds SIZE_CAP, so the REAL exchange reached through `?code=` lands on
      // the refuse-and-report branch. `access_token` is kept by the narrowing, so
      // this is the field that has to carry the size.
      return jsonResponse({
        access_token: 'z'.repeat(6000), token_type: 'bearer', expires_in: 3600,
        refresh_token: 'rt-1', user: { id: 'u1' },
      }, 200);
    }
    return jsonResponse({
      access_token: 'at-' + 'x'.repeat(4), token_type: 'bearer', expires_in: 3600,
      refresh_token: 'rt-1', user: { id: 'u1' },
    }, 200);
  }
  if (url.indexOf('/oauth/consent/preview') >= 0 && opts.previewOk) {
    // A single-org resolve, so the consent view completes WITHOUT rendering a
    // message of its own — the only transition left that can touch the retry
    // affordance is the consent view's own entry (see `consent_after_terminal`).
    return jsonResponse({ org_id: 'org-1', org_name: 'Org One' }, 200);
  }
  return jsonResponse({}, 200);
}

const sandbox = {
  console: {
    log: function () {}, info: function () {}, debug: function () {},
    warn: function () { warns.push(Array.prototype.map.call(arguments, String).join(' ')); },
    error: function () { warns.push(Array.prototype.map.call(arguments, String).join(' ')); },
  },
  document: documentObj,
  location: location,
  history: {
    replaceState: function (s, t, url) { replaceStates.push(String(url)); },
    pushState: function () {}, go: function () {}, back: function () {},
  },
  sessionStorage: sessionStorage,
  localStorage: localStorage,
  navigator: { userAgent: 'node-harness', language: 'en', locks: undefined },
  TextEncoder: opts.noTextEncoder ? undefined : TextEncoder,
  TextDecoder: TextDecoder,
  URL: URL,
  URLSearchParams: URLSearchParams,
  Headers: Headers,
  Request: Request,
  Response: Response,
  FormData: FormData,
  Blob: Blob,
  AbortController: AbortController,
  // supabase-js's runtime detection refuses to initialise without a WebSocket
  // constructor in the realm ("Unknown JavaScript runtime without WebSocket
  // support"). Node 22 provides one; it is never dialled by this harness.
  WebSocket: WebSocket,
  fetch: fetchImpl,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: setInterval,
  clearInterval: clearInterval,
  queueMicrotask: queueMicrotask,
  btoa: function (s) { return Buffer.from(String(s), 'binary').toString('base64'); },
  atob: function (s) { return Buffer.from(String(s), 'base64').toString('binary'); },
  crypto: opts.noSubtle
    ? { getRandomValues: function (a) { return nodeCrypto.randomFillSync(a); } }
    : { getRandomValues: function (a) { return nodeCrypto.randomFillSync(a); },
        subtle: nodeCrypto.webcrypto.subtle },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;
sandbox.top = sandbox;
// Harness-only: the WebCrypto the page loaded with, so a scenario can RESTORE it
// (opts.lose='restore'). Never read by the page.
sandbox.__harnessSubtle__ = nodeCrypto.webcrypto.subtle;
if (opts.accessThrow) {
  ['sessionStorage', 'localStorage'].forEach(function (nm) {
    Object.defineProperty(sandbox, nm, {
      get: function () { throw new Error('access to ' + nm + ' is denied'); },
      configurable: true,
    });
  });
}

const ctx = vm.createContext(sandbox);
vm.runInContext(bundle, ctx, { filename: 'supabase.min.js' });
if (!sandbox.supabase || typeof sandbox.supabase.createClient !== 'function') {
  throw new Error('the UMD bundle did not expose sandbox.supabase.createClient');
}
vm.runInContext(page, ctx, { filename: 'consent-inline.js' });

const q = function (expr) { return vm.runInContext(expr, ctx); };
const sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
// Poll instead of sleeping a fixed window: under machine load a fixed settle
// made the click scenarios flaky (1 failure in 49 runs of inv 11 was observed
// at load ~12). The condition, not the clock, decides when the run is ready.
const waitFor = async function (fn, ms) {
  const t0 = Date.now();
  while (!fn() && Date.now() - t0 < ms) { await sleep(10); }
  return fn();
};

function innerTarget(assignUrl) {
  // The assign URL is Supabase's authorize endpoint; the page's own return
  // target is percent-encoded INSIDE its `redirect_to` param.
  const u = new URL(assignUrl);
  const rt = u.searchParams.get('redirect_to');
  if (!rt) { return null; }
  return new URL(rt);
}

(async function main() {
  const scenario = opts.scenario;
  const out = { scenario: scenario };

  if (scenario === 'grant' || scenario === 'click' || scenario === 'return_target') {
    await sleep(60);   // let createClient()'s _initialize settle
    await q('signInWithProvider("github")');
    await waitFor(function () { return navs.length > 0 || (els['error'] &&
      els['error'].classList.contains('visible')); }, 3000);
    await sleep(20);
    out.navs = navs.slice();
    out.errorVisible = els['error'] ? els['error'].classList.contains('visible') : false;
    out.errorText = els['error'] ? String(els['error'].textContent) : '';
    out.cookieVerifierKeys = cookieWrites
      .filter(function (w) { return w.header.indexOf('code-verifier') >= 0; })
      .map(function (w) { return w.name; });
    out.auxVerifierKeys = Array.from(sessionStorage._map.keys())
      .concat(Array.from(localStorage._map.keys()))
      .filter(function (k) { return k.indexOf('code-verifier') >= 0; });
    // Per-store, so a credential can be located in the store that will KEEP it:
    // "is there no verifier" is not the same claim as "is the credential in a
    // store that can remove it".
    out.sessionVerifierKeys = Array.from(sessionStorage._map.keys())
      .filter(function (k) { return k.indexOf('code-verifier') >= 0; });
    out.localVerifierKeys = Array.from(localStorage._map.keys())
      .filter(function (k) { return k.indexOf('code-verifier') >= 0; });
    // #5734: the CLICK path renders the same capability notice as the load path
    // (the click-time re-check calls `renderCapability` before refusing), so its
    // cause is readable here and pinnable per cause.
    out.capabilityText = els['signin-capability'] ? String(els['signin-capability'].textContent) : '';
    out.capabilityVisible = els['signin-capability']
      ? String(els['signin-capability'].style.display) : null;
    out.attempts = storeAttempts.slice();
    // RAW entries, unfiltered by key shape. The A5 probe writes a throwaway
    // `__tt_wprobe-*` key, so a verifier-shaped filter cannot see whether the
    // probe's PAYLOAD was the credential. Pinned by test_inv13.
    const rawEntries = function (store, label) {
      return Array.from(store._map.entries()).map(function (e) {
        return { store: label, key: e[0], value: String(e[1]) };
      });
    };
    out.auxRawEntries = rawEntries(sessionStorage, 'session')
      .concat(rawEntries(localStorage, 'local'));
    if (navs.length) {
      const inner = innerTarget(navs[navs.length - 1]);
      out.assign = navs[navs.length - 1];
      if (inner) {
        out.innerOrigin = inner.origin;
        out.innerPath = inner.pathname;
        // supabase-js puts the challenge on the AUTHORIZE url itself, as a
        // sibling of `redirect_to` (which carries only the page's return target).
        const assignParams = new URL(navs[navs.length - 1]).searchParams;
        out.challenge = assignParams.get('code_challenge');
        out.method = assignParams.get('code_challenge_method');
        const transients = ['code', 'error', 'error_code', 'error_description',
                            'error_uri', 'sb_flow_id', 'flow_id', 'type'];
        out.innerTransients = transients.filter(function (k) { return inner.searchParams.has(k); });
        out.innerParams = Array.from(inner.searchParams.keys());
      }
    }
  } else if (scenario === 'load') {
    // One of the two terminal states must have been rendered; wait for the
    // page to have touched its own view rather than assuming a duration.
    await waitFor(function () { return els['view-signin'] || els['view-consent']; }, 3000);
    // #5734: when the caller is going to assert on the TERMINAL state, wait for
    // the terminal OBSERVABLE rather than a fixed settle. The transient's
    // `replaceState` and the page's own error rendering are separate steps, and
    // under load a fixed 40 ms could read `errorVisible`/`errorText` before the
    // error existed. Opt-in: the pre-existing inv-5 calls keep their fixed
    // settle, unchanged.
    if (opts.awaitTerminal) {
      await waitFor(function () {
        return !!(els['error'] && els['error'].classList.contains('visible'));
      }, 3000);
    }
    await sleep(40);   // let the transient's replaceState land
    out.viewSignin = els['view-signin'] ? String(els['view-signin'].style.display) : null;
    out.viewConsent = els['view-consent'] ? String(els['view-consent'].style.display) : null;
    out.errorVisible = els['error'] ? els['error'].classList.contains('visible') : false;
    out.errorText = els['error'] ? String(els['error'].textContent) : '';
    out.controlCharsInError = Array.from(String(out.errorText)).filter(function (c) {
      const n = c.codePointAt(0);
      return n < 32 || (n >= 127 && n <= 159) || n === 0x2028 || n === 0x2029;
    }).length;
    // Did this load actually ATTEMPT a code exchange? Without it, a terminal
    // state on a `?code=` load cannot be told from a load that never called the
    // token endpoint at all (with no stored verifier the library treats the
    // callback as non-PKCE and skips the exchange entirely).
    out.exchangeCount = fetchCalls.filter(function (c) {
      return c.url.indexOf('/auth/v1/token') >= 0;
    }).length;
    out.replaceStates = replaceStates.slice();
    out.navs = navs.slice();
    // #5734: the LOAD-time capability affordance. `capabilityVisible` is the
    // notice element's inline display, so it is 'none' only because the page set
    // it — the shim never assigns it.
    out.capabilityText = els['signin-capability'] ? String(els['signin-capability'].textContent) : '';
    out.capabilityVisible = els['signin-capability'] ? String(els['signin-capability'].style.display) : null;
    out.githubDisabled = els['btn-github'] ? els['btn-github'].disabled : null;
    out.googleDisabled = els['btn-google'] ? els['btn-google'].disabled : null;
    out.emailDisabled = els['btn-email'] ? els['btn-email'].disabled : null;
    out.retrySigninVisible = els['btn-retry-signin']
      ? String(els['btn-retry-signin'].style.display) : null;
  } else if (scenario === 'click_after_load') {
    // #5734 (C): the load-time probe is an AFFORDANCE. Capability is then LOST
    // in the realm the page reads at click time, and the click must still refuse.
    await sleep(60);   // let the load-time probe and createClient() settle
    const provider = opts.provider || 'github';
    out.provider = String(provider);
    out.loadProviderDisabled = els['btn-' + provider] ? els['btn-' + provider].disabled : null;
    out.loadGithubDisabled = els['btn-github'] ? els['btn-github'].disabled : null;
    out.loadEmailDisabled = els['btn-email'] ? els['btn-email'].disabled : null;
    out.loadCapabilityVisible = els['signin-capability']
      ? String(els['signin-capability'].style.display) : null;
    const stores0 = [sessionStorage, localStorage];
    if (opts.lose === 'subtle' || opts.lose === 'both') {
      vm.runInContext('window.crypto.subtle = undefined;', ctx);
    }
    if (opts.lose === 'store' || opts.lose === 'both') {
      sandbox.sessionStorage = makeStore('throw-method');
      sandbox.localStorage = makeStore('throw-method');
    }
    // #5734: invoke the CHOSEN button's REAL bound handler, not
    // `signInWithProvider` directly. A programmatic call runs a handler even when
    // the button is disabled, so this is what makes a broken or absent `onclick`
    // binding on a security-boundary button visible; calling the function directly
    // would exercise the re-check while leaving the user's actual entry point dead.
    // Parameterised over the provider because each button carries its OWN binding:
    // a dead `onclick` on either one ships undetected if only one is driven.
    await q('document.getElementById("btn-' + provider + '").onclick()');
    await waitFor(function () {
      return navs.length > 0 || (els['error'] && els['error'].classList.contains('visible'));
    }, 3000);
    await sleep(20);
    out.navs = navs.slice();
    out.errorVisible = els['error'] ? els['error'].classList.contains('visible') : false;
    out.errorText = els['error'] ? String(els['error'].textContent) : '';
    out.capabilityText = els['signin-capability'] ? String(els['signin-capability'].textContent) : '';
    out.providerDisabled = els['btn-' + provider] ? els['btn-' + provider].disabled : null;
    // The clicked BUTTON's own provider, read from the page's own table: a binding
    // that names the wrong provider (a swapped binding, which is strictly worse
    // than a dead one — it launches the wrong sign-in) is only visible by comparing
    // this against the refusal the click produced.
    out.clickedProviderLabel = vm.runInContext('PROVIDER_LABEL["' + provider + '"]', ctx);
    out.githubDisabled = els['btn-github'] ? els['btn-github'].disabled : null;
    out.emailDisabled = els['btn-email'] ? els['btn-email'].disabled : null;
    out.viewSignin = els['view-signin'] ? String(els['view-signin'].style.display) : null;
    out.viewConsent = els['view-consent'] ? String(els['view-consent'].style.display) : null;
    out.retrySigninVisible = els['btn-retry-signin']
      ? String(els['btn-retry-signin'].style.display) : null;
    // BOTH generations of store: the originals (in case anything was written
    // before the swap) and the replacements.
    out.auxVerifierKeys = stores0.concat([sandbox.sessionStorage, sandbox.localStorage])
      .reduce(function (acc, s) { return acc.concat(Array.from(s._map.keys())); }, [])
      .filter(function (k) { return k.indexOf('code-verifier') >= 0; });
    out.cookieVerifierKeys = cookieWrites
      .filter(function (w) { return w.header.indexOf('code-verifier') >= 0; })
      .map(function (w) { return w.name; });
  } else if (scenario === 'retry_signin') {
    // #5734: the retry affordance's handler must RE-PROBE capability — it may
    // have been lost (lose='subtle') or restored (lose='restore') since the
    // attempt. The handler is invoked directly: in the restore case no terminal
    // state has shown the button, so a real click is not available.
    await waitFor(function () {
      if (opts.lose === 'restore') {
        return !!(els['signin-capability'] && els['signin-capability'].style.display === 'block');
      }
      return !!(els['btn-retry-signin'] && els['btn-retry-signin'].style.display === 'block');
    }, 3000);
    await sleep(20);
    out.beforeGithubDisabled = els['btn-github'] ? els['btn-github'].disabled : null;
    out.beforeCapabilityVisible = els['signin-capability']
      ? String(els['signin-capability'].style.display) : null;
    out.beforeRetrySigninVisible = els['btn-retry-signin']
      ? String(els['btn-retry-signin'].style.display) : null;
    if (opts.lose === 'subtle') {
      vm.runInContext('window.crypto.subtle = undefined;', ctx);
    } else if (opts.lose === 'restore') {
      vm.runInContext('window.crypto.subtle = __harnessSubtle__;', ctx);
    }
    vm.runInContext('document.getElementById("btn-retry-signin").onclick();', ctx);
    await sleep(20);
    out.githubDisabled = els['btn-github'] ? els['btn-github'].disabled : null;
    out.capabilityVisible = els['signin-capability']
      ? String(els['signin-capability'].style.display) : null;
    out.capabilityText = els['signin-capability'] ? String(els['signin-capability'].textContent) : '';
    out.errorVisible = els['error'] ? els['error'].classList.contains('visible') : false;
    out.retrySigninVisible = els['btn-retry-signin']
      ? String(els['btn-retry-signin'].style.display) : null;
  } else if (scenario === 'email_after_terminal') {
    // #5734: the affordance is a sibling of #error, OUTSIDE both views, so a
    // message that does not imply a retry has to DROP it — otherwise it stays on
    // screen in the consent view, where its handler silently returns the user to
    // the sign-in view and abandons the approve/deny decision.
    await waitFor(function () {
      return !!(els['btn-retry-signin'] && els['btn-retry-signin'].style.display === 'block');
    }, 3000);
    out.beforeRetrySigninVisible = String(els['btn-retry-signin'].style.display);
    await q('(function () {' +
      'document.getElementById("email").value = "a@b.example";' +
      'document.getElementById("password").value = ' +
      (opts.blankPassword ? '""' : '"pw"') + ';' +
      'return document.getElementById("btn-email").onclick(); })()');
    if (opts.blankPassword) {
      // The form's own VALIDATION branch, which renders a message implying no
      // retry — so the refusal's control must not survive it.
      out.errorText = els['error'] ? String(els['error'].textContent) : '';
      out.retrySigninVisible = String(els['btn-retry-signin'].style.display);
    } else {
      await waitFor(function () {
        return !!(els['view-consent'] && els['view-consent'].style.display === 'block');
      }, 3000);
      await sleep(20);
      out.viewConsent = els['view-consent'] ? String(els['view-consent'].style.display) : null;
      out.viewSignin = els['view-signin'] ? String(els['view-signin'].style.display) : null;
      out.retrySigninVisible = String(els['btn-retry-signin'].style.display);
      out.errorText = els['error'] ? String(els['error'].textContent) : '';
    }
  } else if (scenario === 'consent_after_terminal') {
    // #5734: the CONSENT VIEW'S ENTRY must drop the control a terminal state showed.
    // The `view-consent`/`view-signin` switch does not contain the button — it is a
    // sibling of #error — and no form is involved on this path, and the preview
    // RESOLVES (so it renders no message of its own), so `showConsentOnce()`'s own
    // `hideError()` is the only transition that can drop it here.
    await waitFor(function () {
      return !!(els['btn-retry-signin'] && els['btn-retry-signin'].style.display === 'block');
    }, 3000);
    out.beforeRetrySigninVisible = String(els['btn-retry-signin'].style.display);
    // A session through the page's OWN storage adapter, so no page handler runs
    // and nothing but the consent flow itself touches the affordance.
    vm.runInContext('cookieStorage.setItem(COOKIE_NAME, JSON.stringify(' +
      '{access_token:"a",refresh_token:"r",expires_at:9999999999,' +
      'token_type:"bearer",user:{id:"u1"}}));', ctx);
    await q('runConsentFlow()');
    await sleep(20);
    out.viewConsent = els['view-consent'] ? String(els['view-consent'].style.display) : null;
    out.viewSignin = els['view-signin'] ? String(els['view-signin'].style.display) : null;
    out.retrySigninVisible = String(els['btn-retry-signin'].style.display);
    out.authorizeEnabled = els['btn-auth'] ? !els['btn-auth'].disabled : null;
    out.errorText = els['error'] ? String(els['error'].textContent) : '';
    out.errorVisible = els['error'] ? els['error'].classList.contains('visible') : false;
  } else if (scenario === 'overcap_exchange') {
    // #5734: the REFUSED over-SIZE_CAP write, driven through the REAL exchange
    // callback rather than by calling `storage.setItem` directly (the `item6`
    // scenario). The library believes the refused write persisted, so it re-reads
    // the cookie after the exchange and lands on the terminal fallback — or, with
    // a stale session present, on the consent flow.
    //
    // WAIT ON THE OBSERVABLES, not on a duration. `replaceStates` alone is NOT a
    // terminal observable: the vendored library also calls
    // `window.history.replaceState` (with an ABSOLUTE url), so a non-empty list
    // can be observed before the page has rendered anything. The page's own push
    // comes from `sanitiseUrl()` and is PATH-RELATIVE, and it happens immediately
    // after `showTerminalFallback()`. The refusal itself is reported
    // synchronously inside the exchange, so requiring it first removes the
    // remaining race.
    const pageReplaced = function () {
      return replaceStates.some(function (u) { return u.charAt(0) === '/'; });
    };
    if (opts.viaEmail) {
      // #5734: the SAME refusal, reached through the email/password form. There is
      // no `?code` transient, so the terminal fallback never runs; what follows the
      // refusal is the email handler's `runConsentFlow()` → `showConsentOnce()`'s
      // NO-SESSION branch → `showSignin()`. That is the path on which the
      // affordance the refusal's copy promises would go missing again —
      // `showSignin()` hides it, so it has to re-assert it while a refusal is
      // pending.
      await q('(function () {' +
        'document.getElementById("email").value = "a@b.example";' +
        'document.getElementById("password").value = "pw";' +
        'return document.getElementById("btn-email").onclick(); })()');
    }
    await waitFor(function () {
      return !!(els['error'] && els['error'].classList.contains('visible'));
    }, 3000);
    if (opts.viaEmail) {
      // Not a duration window: the guard hands back the RUNNING flow, so awaiting
      // it settles on the page's own continuation. A stability window could read
      // the pre-bug state if the continuation stalled past it.
      await q('runConsentFlow()');
      await sleep(20);
    } else {
      await waitFor(function () {
        // Either the terminal branch completed, or the consent view was entered —
        // and the second is the bug this pins, so give it its full window rather
        // than reading after a fixed settle.
        return pageReplaced() ||
               !!(els['view-consent'] && els['view-consent'].style.display === 'block');
      }, 3000);
    }
    out.exchangeCount = fetchCalls.filter(function (c) {
      return c.url.indexOf('/auth/v1/token') >= 0;
    }).length;
    out.pageReplaced = pageReplaced();
    out.errorVisible = els['error'] ? els['error'].classList.contains('visible') : false;
    out.errorText = els['error'] ? String(els['error'].textContent) : '';
    out.viewConsent = els['view-consent'] ? String(els['view-consent'].style.display) : null;
    out.viewSignin = els['view-signin'] ? String(els['view-signin'].style.display) : null;
    out.retrySigninVisible = els['btn-retry-signin']
      ? String(els['btn-retry-signin'].style.display) : null;
    // The refused write must not have reached the cookie jar.
    out.cookieWrites = cookieWrites.length;
    out.oversizedLanded = Array.from(cookies.values()).some(function (v) {
      return v.indexOf('z'.repeat(200)) >= 0;
    });
    if (opts.thenRetry) {
      // #5734: the retry the refusal's copy names must CLEAR the refusal. The
      // handler's `writeRefused = false` is what lets its `hideError()` act;
      // without it the sticky guard leaves the refusal on screen, so the user
      // cannot move on.
      //
      // EVERY step below is a synchronous handler (each is sync up to its first
      // `await`, and the refusal paths never await at all), and each state is read
      // immediately after the call that changed it — so this block cannot read a
      // state a stale continuation later overwrites, and no settle window is
      // trusted. The `hadRefusal` control makes the delta non-vacuous.
      const refuses = function () {
        return warns.filter(function (w) { return w.indexOf('refusing') >= 0; }).length;
      };
      const hadRefusal = els['error'].classList.contains('visible') &&
        String(els['error'].textContent).indexOf('cookie limit') >= 0;
      vm.runInContext('document.getElementById("btn-retry-signin").onclick();', ctx);
      out.retryClearedError = hadRefusal &&
        !els['error'].classList.contains('visible');
      out.retryAffordance = String(els['btn-retry-signin'].style.display);
      // A SECOND refusal, raised through the page's own write path, and then
      // capability LOST under it. The provider click's re-check must be able to
      // render ITS OWN cause and copy — a `writeRefused` that click handler had
      // not cleared would leave the stale cookie-limit text on screen and no
      // capability notice at all.
      const before = refuses();
      vm.runInContext('cookieStorage.setItem(COOKIE_NAME, JSON.stringify(' +
        '{access_token:"' + 'z'.repeat(6000) + '",refresh_token:"rt",' +
        'expires_at:9999999999}));', ctx);
      out.secondRefusalRaised = refuses() > before;
      vm.runInContext('window.crypto.subtle = undefined;', ctx);
      vm.runInContext('document.getElementById("btn-github").onclick();', ctx);
      out.afterClickErrorText = els['error'] ? String(els['error'].textContent) : '';
      out.afterClickCapabilityText = els['signin-capability']
        ? String(els['signin-capability'].textContent) : '';
      out.afterClickRetry = String(els['btn-retry-signin'].style.display);
      out.afterClickNavs = navs.slice();
    }
    if (opts.viaEmail && opts.thenBadEmail) {
      // #5734: the email form clears `writeRefused` at the START of the attempt,
      // so the attempt's OWN failure can render. Without that clear the sticky
      // guard suppresses it and the user keeps reading the stale refusal instead
      // of why the attempt they just made failed.
      opts.exchangeFails = true;   // the next password grant is rejected
      await q('(function () {' +
        'document.getElementById("password").value = "wrong";' +
        'return document.getElementById("btn-email").onclick(); })()');
      await sleep(40);
      out.afterBadEmailErrorText = els['error'] ? String(els['error'].textContent) : '';
      out.afterBadEmailErrorVisible = els['error'].classList.contains('visible');
    }
    // #5734: the precedence rule is pinned DIRECTLY. Once `showConsentOnce`
    // refuses to enter the consent view, no UI path reaches `hideError` with a
    // refusal pending, so its guard is unobservable through the UI and would
    // otherwise be a line no test can lose. A contract on the page's OWN helpers
    // is the honest way to pin it.
    vm.runInContext('hideError();', ctx);
    out.afterHideRefusalVisible = els['error']
      ? els['error'].classList.contains('visible') : false;
    vm.runInContext('showError("SHOULD NOT REPLACE THE REFUSAL");', ctx);
    out.afterShowErrorText = els['error'] ? String(els['error'].textContent) : '';
    // …and a write that LANDS clears the state the refusal describes, so the page
    // can move on (the four user-facing continuations clear it themselves; this
    // is the semantic clear). Everything BELOW runs with no refusal pending —
    // which is what the ownership pins need.
    vm.runInContext('cookieStorage.setItem(COOKIE_NAME, JSON.stringify(' +
      '{access_token:"a",refresh_token:"r",expires_at:9}));', ctx);
    vm.runInContext('hideError();', ctx);
    out.afterLandedWriteErrorVisible = els['error']
      ? els['error'].classList.contains('visible') : false;
    // The retry affordance's OWNERSHIP rule is pinned the same DIRECT way, with an
    // asymmetry: `showError`'s drop is pinned here because no UI scenario can lose
    // it (on every route to a non-retry message the control is dropped first, by a
    // handler's `hideError()` or by `showSignin()`), while `hideError`'s drop is a
    // contract pin here AND witnessed by
    // test_5734_entering_the_consent_view_drops_the_terminal_retry_control. With no
    // refusal pending, a message that does not imply a retry must drop it —
    // through `showError` (the message) and through `hideError` (the transition
    // that says nothing).
    vm.runInContext('showRetrySignin();', ctx);
    vm.runInContext('showError("no retry is implied here");', ctx);
    out.showErrorDroppedAffordance = String(els['btn-retry-signin'].style.display);
    vm.runInContext('showRetrySignin();', ctx);
    vm.runInContext('hideError();', ctx);
    out.hideErrorDroppedAffordance = String(els['btn-retry-signin'].style.display);
    // …and the concurrent guard is IDEMPOTENT AND AWAITABLE: two calls in the
    // same turn must hand back the SAME running flow. An early `return` resolves
    // the second caller before the flow it did not start has finished — the
    // window a caller (or the harness) would otherwise have to guess at.
    out.flowGuardShared = vm.runInContext(
      '(function () { var a = runConsentFlow(); var b = runConsentFlow(); ' +
      'return a === b && !!a && typeof a.then === "function"; })()', ctx);
    vm.runInContext('hideError();', ctx);
    // #5734: `showExpiredSignin` is a caller that lands on the sign-in view with a
    // refusal pending (a refused refresh write). Its message must not replace the
    // refusal and the retry control must stay — a caller that cleared the flag
    // first would swap the refusal for "Your session expired — sign in again." and
    // drop the control. Driven on the page's own helpers, since the refusal-pending
    // double-stale path is not reachable through any of the scenarios above.
    vm.runInContext('showError("THE REFUSAL");', ctx);
    vm.runInContext('showRetrySignin();', ctx);
    vm.runInContext('writeRefused = true;', ctx);
    vm.runInContext('showExpiredSignin();', ctx);
    out.expiredKeptRefusal = els['error'] ? String(els['error'].textContent) : '';
    out.expiredRetryVisible = String(els['btn-retry-signin'].style.display);
    out.expiredViewSignin = els['view-signin'] ? String(els['view-signin'].style.display) : null;
    vm.runInContext('writeRefused = false; hideError();', ctx);
  } else if (scenario === 'routing') {
    await sleep(60);
    await q('signInWithProvider("google")');
    await waitFor(function () { return navs.length > 0; }, 3000);
    await sleep(20);
    // The two NON-verifier aux shapes a suffix denylist cannot see. A denylist
    // routes verifier-SHAPED keys correctly by construction, so only keys that
    // do not look like verifiers can tell an allowlist from a denylist. Written
    // through the adapter — the real entry point — not straight to a store.
    const storage = q('cookieStorage');
    storage.setItem(COOKIE_NAME + '-user', 'u1');
    storage.setItem(COOKIE_NAME + '-unknown-aux', 'x1');
    const auxKeys = Array.from(sessionStorage._map.keys())
      .concat(Array.from(localStorage._map.keys()));
    out.auxVerifierKeys = auxKeys.filter(function (k) { return k.indexOf('code-verifier') >= 0; });
    out.nonVerifierAuxKeys = [COOKIE_NAME + '-user', COOKIE_NAME + '-unknown-aux']
      .filter(function (k) { return auxKeys.indexOf(k) >= 0; });
    out.cookieVerifierKeys = cookieWrites
      .filter(function (w) { return w.header.indexOf('code-verifier') >= 0; })
      .map(function (w) { return w.name; });
    // EVERY cookie assignment whose name is not the session key. Keyed on
    // identity, not on a name substring: a substring filter answered [] even
    // when the router was replaced by a denylist, which made the assertion
    // incapable of failing.
    out.cookieAuxWrites = cookieWrites
      .filter(function (w) { return w.name !== COOKIE_NAME; })
      .map(function (w) { return w.name; });
  } else if (scenario === 'removal') {
    await waitFor(function () { return cookieWrites.length > 0; }, 3000);
    await sleep(40);
    out.cookieWriteNames = cookieWrites.map(function (w) { return w.name; });
    out.cookieWrites = cookieWrites.slice();
    out.navs = navs.slice();
    out.auxVerifierKeys = Array.from(sessionStorage._map.keys())
      .concat(Array.from(localStorage._map.keys()))
      .filter(function (k) { return k.indexOf('code-verifier') >= 0; });
  } else if (scenario === 'single_origin') {
    await waitFor(function () {
      return fetchCalls.some(function (c) { return c.url.indexOf('/auth/v1/token') >= 0; });
    }, 3000);
    await sleep(40);
    out.fetchCalls = fetchCalls.slice();
    const exch = fetchCalls.filter(function (c) { return c.url.indexOf('/auth/v1/token') >= 0; });
    out.exchangeCount = exch.length;
    out.exchangeGrant = exch.length ? (new URL(exch[0].url)).searchParams.get('grant_type') : null;
    const body = exch.length ? exch[0].body : '';
    // supabase-js posts the exchange as a JSON body (verified against the
    // vendored bundle), not form-encoded — accept both so a transport change
    // surfaces as a wrong-value failure rather than a confusing null.
    let params = new URLSearchParams();
    try { const j = JSON.parse(body); params = new URLSearchParams(j); }
    catch (e) { params = new URLSearchParams(body); }
    out.exchangeVerifier = params.get('code_verifier');
    out.exchangeCode = params.get('auth_code') || params.get('code');
    out.navs = navs.slice();
    out.outerOrigin = fetchCalls.length ? new URL(fetchCalls[0].url).origin : null;
    // The return target the page BUILDS, evaluated where the callback actually
    // landed. Reporting `location.origin` back instead would assert the shim
    // against itself and stay green even if the page returned a foreign host.
    out.returnTarget = q('authorizeReturnTo()');
  } else if (scenario === 'item6') {
    await sleep(60);
    const storage = q('cookieStorage');
    const small = JSON.stringify({ access_token: 'a', refresh_token: 'r', expires_at: 9 });
    cookieWrites.length = 0;
    storage.setItem(COOKIE_NAME, small);
    const smallWrite = cookieWrites[0];
    out.smallWritten = !!smallWrite && smallWrite.name === COOKIE_NAME &&
      smallWrite.header.indexOf('=' + encodeURIComponent(small)) >= 0;

    cookieWrites.length = 0;
    const big = JSON.stringify({
      access_token: 'a'.repeat(100), refresh_token: 'r'.repeat(50), expires_at: 9,
      provider_token: 'p'.repeat(2000), provider_refresh_token: 'q'.repeat(2000),
      // #3496 item 6: a REAL GitHub session carries a `user`, and the provider
      // tokens alone already clear SIZE_GUARD — so without this object the
      // `if (obj.user)` narrowing below is DEAD in every run, and deleting the
      // whole identities/user_metadata block left this test green.
      user: {
        id: 'u-1',
        identities: Array.from({ length: 40 }, function (_, i) {
          return { provider: 'github', identity_id: 'id-' + i, id: 'x'.repeat(40) };
        }),
        user_metadata: {
          display_name: 'Ada Lovelace', avatar_url: 'https://a.example/a.png',
          full_name: 'Ada Lovelace', name: 'ada',
          // THREE non-allowlisted keys, so a deny-list must name ALL THREE to
          // produce the same session; one that enumerates fewer keeps the
          // third. (A deny-list naming all three writes a byte-identical
          // session — equivalent to the allowlist, and therefore not something
          // this test claims to distinguish.)
          gigantic: 'y'.repeat(1500),             // must NOT survive the narrowing
          legacy_blob: 'z'.repeat(400),           // must NOT survive the narrowing
          provider_claims_blob: 'w'.repeat(120),  // ditto — deliberately not an
                                                  // obvious deny-list candidate
        },
      },
    });
    storage.setItem(COOKIE_NAME, big);
    out.strippedWrites = cookieWrites.length;
    // The raw per-assignment write log IS the artifact — assert on it rather
    // than re-deriving the page's stripping decision in the test.
    out.strippedHeaders = cookieWrites.map(function (w) { return w.header; });
    out.strippedHasToken = cookieWrites.some(function (w) {
      // BOTH: `provider_refresh_token` does not contain `provider_token`, so a
      // single substring probe left the sibling `delete obj.provider_refresh_token`
      // unguarded — deleting just that line kept this assertion green.
      return w.header.indexOf('provider_token') >= 0 ||
             w.header.indexOf('provider_refresh_token') >= 0;
    });

    cookieWrites.length = 0;
    if (els['error']) { els['error'].classList.remove('visible'); }
    const over = JSON.stringify({ access_token: 'z'.repeat(6000), refresh_token: 'r', expires_at: 9 });
    storage.setItem(COOKIE_NAME, over);
    out.overCapWrote = cookieWrites.length > 0;
    out.overCapReported = els['error'] ? els['error'].classList.contains('visible') : false;
    out.overCapText = els['error'] ? String(els['error'].textContent) : '';
    out.retrySigninVisible = els['btn-retry-signin']
      ? String(els['btn-retry-signin'].style.display) : null;
    // #5734: the retry affordance is a SIBLING of #error, OUTSIDE both
    // #view-* divs, so its own inline style IS effective visibility. (This is
    // the property the structural assertion in
    // test_5734_post_redirect_copy_names_its_cause_and_remedy pins; inside
    // #view-signin it would be hidden whenever the consent view is shown.)
    // `viewSignin` below is therefore a STATE assertion, not a visibility one.
    out.viewSignin = els['view-signin'] ? String(els['view-signin'].style.display) : null;
  } else {
    throw new Error('unknown scenario: ' + scenario);
  }

  out.warns = warns.slice(0, 20);
  process.stdout.write('RESULT ' + JSON.stringify(out) + '\n');
})().catch(function (e) {
  process.stderr.write('DRIVER ERROR: ' + (e && e.stack ? e.stack : e) + '\n');
  process.exit(1);
});
"""
