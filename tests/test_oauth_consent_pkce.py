"""Behavioural harness for the MCP consent page's browser auth client (#3496).

The page (`tortoise/oauth.py::consent_page_html`) is a FastAPI-rendered HTML
document whose inline script drives supabase-js. Historically its hardening was
pinned only by static-string assertions ("the page JS has no jsdom harness in
this repo" — see `tests/test_oauth_mcp.py`), which cannot discriminate the
behaviour this issue changes: the grant type actually used, where the PKCE
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
   actually written (asserting `strippedWrites >= 1`, so removing the strip cannot
   pass by falling through to the refusal), >SIZE_CAP refused AND page-reported
 8 version coupling: the page's CDN specifier EQUALS the version of the vendored
   bundle this harness executes (pure text/path, no node — see
   `test_page_specifier_matches_the_vendored_bundle_version`)
 9 no WebCrypto refuses locally; with the guard removed the bundle downgrades
   to `code_challenge_method=plain` (the paired control)
11 the return target is canonicalised (no transient echoed), with a
   guard-removed control that DOES carry it
12 the aux stores hold no verifier after the removal path
13 the WRITER proves, for the real key and the real value, that the store which
   receives the verifier can also remove it — a store that fails that proof is
   skipped, never written to (`test_inv13_...`, A5 writer half)

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
    # Without this half, "removed from both aux stores" was only ever exercised
    # on the first store. (The library's getItemAsync JSON-parses what it reads,
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

    The `throw-remove` mode pins the A5 contract directly: a store that accepts
    a WRITE but refuses REMOVAL is the store `writeAux` would pick, and
    `removeAux` would then orphan the verifier there. The guard must therefore
    refuse rather than fall through to a store the verifier will never reach —
    falling through would proceed with the verifier written to the un-cleanable
    store."""
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


def test_inv13_the_write_is_proven_cleanable_where_it_lands() -> None:
    """Invariant 13 (A5, the WRITER half): the pre-flight probe writes its OWN key
    with a 160-byte payload, so it cannot prove that the store which will RECEIVE
    the real verifier can also remove it. The writer therefore proves the invariant
    per store, for the real key and the real value — write, read back, remove, read
    back null, then write again — and skips a store that fails.

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


def test_inv7_item6_write_path_parity() -> None:
    """Invariant 7: ≤SIZE_GUARD is written byte-identically; over SIZE_GUARD is
    stripped; over SIZE_CAP is refused (no write) AND reported on the page."""
    r = _run("item6")
    assert r["smallWritten"] is True, r
    assert r["strippedWrites"] >= 1, (
        f"no stripped write landed, so the token assertion below is vacuous: {r}"
    )
    assert r["strippedHasToken"] is False, (
        f"the size guard did not strip provider tokens: {r}"
    )
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

function makeStore(mode, quota) {
  const map = new Map();
  return {
    _map: map,
    get length() { return map.size; },
    key: function (i) { const k = Array.from(map.keys())[i]; return k === undefined ? null : k; },
    getItem: function (k) {
      if (mode === 'throw-method') throw new Error('storage disabled');
      return map.has(String(k)) ? map.get(String(k)) : null;
    },
    setItem: function (k, v) {
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
const sessionStorage = makeStore(opts.sessionMode || 'ok', opts.sessionQuota);
const localStorage = makeStore(opts.localMode || 'ok', opts.localQuota);
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
    return jsonResponse({
      access_token: 'at-' + 'x'.repeat(4), token_type: 'bearer', expires_in: 3600,
      refresh_token: 'rt-1', user: { id: 'u1' },
    }, 200);
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
  TextEncoder: TextEncoder,
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
    });
    storage.setItem(COOKIE_NAME, big);
    out.strippedWrites = cookieWrites.length;
    out.strippedHasToken = cookieWrites.some(function (w) {
      return w.header.indexOf('provider_token') >= 0;
    });

    cookieWrites.length = 0;
    if (els['error']) { els['error'].classList.remove('visible'); }
    const over = JSON.stringify({ access_token: 'z'.repeat(6000), refresh_token: 'r', expires_at: 9 });
    storage.setItem(COOKIE_NAME, over);
    out.overCapWrote = cookieWrites.length > 0;
    out.overCapReported = els['error'] ? els['error'].classList.contains('visible') : false;
    out.overCapText = els['error'] ? String(els['error'].textContent) : '';
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
