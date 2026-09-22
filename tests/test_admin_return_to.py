"""#3080 — the /admin return-to guard.

The blog admin gate (`website/apps/dashboard/functions/admin/[[path]].ts`)
bounces an unauthenticated request to `/auth?next=<path>&stale=1` so the post-login
redirect comes back to the console. Before #3080 the bounce was a bare `/auth`,
so every login landed on the app root and `/admin` was unreachable by
navigation. #4171 moved the gate to the app origin (same-origin with the
`__Host-session` cookie); the return-to contract is unchanged.

`website/signup.html` turns that `next` into the post-login destination, which
is an open-redirect sink unless the value is constrained, so the allowlist is
tested BEHAVIOURALLY by executing the real shipped blocks under node. A static
regex test would miss the three defects the review cycles actually caught:

1. `claimRedirectTarget()` returned a bare path as GoTrue's `redirect_to`.
   GoTrue validates by host + scheme and supabase-js passes the value through
   verbatim, so a relative value is silently discarded and the return-to is lost
   on the primary Google/GitHub sign-in path.
2. Sending the provider back to `/admin` loses the session: a `#access_token`
   fragment is never sent to a server function, so the gate bounces (inheriting
   the fragment) to `/auth?...&stale=1`, and the stale branch then deletes the
   token that had just arrived — every sign-in would revoke itself.
3. `stale=1` on that bounce is why (2) is fatal, so the OAuth `redirect_to` must
   be `/auth` and must NOT carry `stale`.

Node is optional (the repo's convention — see test_cross_subdomain_cookie_sync):
the test skips cleanly when node is unavailable.

#3952 extends the subject from reachability to RENDERABILITY: the URL the gate
returns you to must be able to load its own bundle. The console SPA is built
with an absolute `base: '/admin/'`; a relative base resolved against the
document URL, so the extensionless `/admin` (the form the gate emits in
`next=`) requested `/assets/index-*.js` and rendered blank. The three #3952 tests
at the end of this file pin the build base, the committed snapshot's asset
resolution, and that the referenced files actually exist.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# #4054: the /auth page moved to the APP Pages project with the rest of the BFF.
SIGNUP = REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "signup.html"
# #4171: the gate moved to the app origin with the console itself.
GATE = REPO_ROOT / "website" / "apps" / "dashboard" / "functions" / "admin" / "[[path]].ts"

ORIGIN = "https://tortoise.premiselabs.co"
APP_ORIGIN = "https://app.premiselabs.co"

_EARLY = "#3080: admin return-to."
# #3501: the synchronous `readValidSession` hard gate became an async
# `/api/session` probe (the BFF cookie is HttpOnly, so the browser cannot read
# it). The marker in the page was renamed with it.
_HEAD_GATE = "#1494/#3501: session probe"


def _script_after(html: str, marker: str) -> str:
    i = html.find(marker)
    assert i != -1, f"block gone from signup.html: {marker!r}"
    start = html.find("<script>", i)
    assert start != -1, f"no <script> after marker {marker!r}"
    end = html.find("</script>", start)
    assert end != -1, f"unterminated <script> after marker {marker!r}"
    return html[start + len("<script>") : end]


def _function(html: str, name: str) -> str:
    """Extract a top-level `function <name>() { ... }` by brace counting."""
    start = html.find(f"function {name}(")
    assert start != -1, f"function {name} not found in signup.html"
    i = html.find("{", start)
    assert i != -1, f"no body for {name}"
    depth, j = 0, i
    while j < len(html):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[start : j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces in {name}")


def _brace_block(html: str, marker: str) -> str:
    """Extract an `if (...) { ... }` block starting at `marker` by brace counting."""
    i = html.find(marker)
    assert i != -1, f"block gone from signup.html: {marker!r}"
    # Start at the statement, not the comment: `marker` may name a prose comment.
    i = html.find("if (", i)
    assert i != -1, f"no `if (` after marker {marker!r}"
    j = html.find("{", i)
    assert j != -1, f"no body after marker {marker!r}"
    depth, k = 0, j
    while k < len(html):
        if html[k] == "{":
            depth += 1
        elif html[k] == "}":
            depth -= 1
            if depth == 0:
                return html[i : k + 1]
        k += 1
    raise AssertionError(f"unbalanced braces after {marker!r}")


def _blocks() -> dict[str, str]:
    html = SIGNUP.read_text(encoding="utf-8")
    gate_src = GATE.read_text(encoding="utf-8")
    # The server's returnToPath carries TS annotations; strip only its signature
    # so the harness can execute it. A renamed/retyped signature makes the
    # replace a no-op and node fails loudly, prompting an update here.
    server = (
        _function(gate_src, "returnToPath")
        .replace("function returnToPath(request: Request): string {", "function returnToPath(request) {")
        .replace("let path: string;", "let path;")
    )
    assert "function returnToPath(request) {" in server, "returnToPath signature changed — update this harness"
    assert ": Request" not in server and ": string" not in server, (
        "TS annotations remain in the extracted returnToPath — update this harness"
    )
    return {
        "early": _script_after(html, _EARLY),
        "headGate": _script_after(html, _HEAD_GATE),
        # #3501: `gotrueRedirectTarget` (a GoTrue `redirect_to`) is retired — the
        # BFF `/auth/start` owns the redirect. `oauthNextPath` is the same-origin
        # PATH handed to it as `next`.
        "claim": _function(html, "claimRedirectTarget") + "\n" + _function(html, "oauthNextPath"),
        "consumer": _brace_block(html, "Session probe consumer"),
        "gate": _function(gate_src, "gateDecision")
        + "\n"
        + _function(gate_src, "tokenReasonKind")
        + "\n"
        + _function(gate_src, "adminKindForResponse"),
        "server": server,
    }


_DRIVER = """
const fs = require('fs');
const path = require('path');
const dir = process.argv[2];
const blocks = [];
for (const n of ['early.js', 'headgate.js', 'claim.js', 'consumer.js', 'gate.js', 'server.js']) {
  blocks.push(fs.readFileSync(path.join(dir, n), 'utf8'));
}
const early = blocks[0], headGate = blocks[1], claimSrc = blocks[2], consumerSrc = blocks[3], gateSrc = blocks[4], serverSrc = blocks[5];
const cases = JSON.parse(fs.readFileSync(path.join(dir, 'cases.json'), 'utf8'));
const ORIGIN = process.argv[3];
const APP = process.argv[4];

function mkEnv(search, cookie, session) {
  // Model a real cookie jar: `Max-Age=0` DELETES the cookie. A naive
  // append-only mock would keep the deleted value and produce false failures.
  const jar = {};
  (cookie || '').split('; ').filter(Boolean).forEach(function (p) {
    const i = p.indexOf('=');
    if (i > 0) jar[p.slice(0, i)] = p.slice(i + 1);
  });
  const cleared = [];
  const navigations = [];
  const doc = {
    get cookie() {
      return Object.keys(jar).map(function (k) { return k + '=' + jar[k]; }).join('; ');
    },
    set cookie(v) {
      const parts = String(v).split(';').map(function (s) { return s.trim(); });
      const i = parts[0].indexOf('=');
      if (i < 0) return;
      const name = parts[0].slice(0, i);
      const val = parts[0].slice(i + 1);
      const dead = parts.slice(1).some(function (a) { return /^max-age=0$/i.test(a); });
      if (dead) { delete jar[name]; } else { jar[name] = val; }
    },
  };
  const win = {
    location: {
      search: search, hash: '', hostname: 'tortoise.premiselabs.co',
      origin: ORIGIN, protocol: 'https:', href: '',
      replace: function (u) { navigations.push(u); },
    },
  };
  win.readValidSession = function () { return session || null; };
  win.clearStoredSession = function () { cleared.push(1); delete jar['sb-tortoise-auth-token']; };
  return { win: win, doc: doc, cleared: cleared, navigations: navigations, cookie: function () { return doc.cookie; } };
}

function runEarly(search, cookie) {
  const e = mkEnv(search, cookie);
  new Function('window', 'document', 'URLSearchParams', early)(e.win, e.doc, URLSearchParams);
  return { base: e.win.__DASHBOARD_BASE_URL || null, ret: e.win.__ADMIN_RETURN_TO || null, cookie: e.cookie() };
}

// Real page order: the early block runs, then the probe STARTER. The starter
// only kicks off `GET /api/session`; the DECISION is the consumer's (below).
// `fetch` is stubbed so the driver never touches the network.
function runHeadGate(search, cookie, session) {
  const e = mkEnv(search, cookie, session);
  new Function('window', 'document', 'URLSearchParams', early)(e.win, e.doc, URLSearchParams);
  const fetched = [];
  const never = { then: function () { return never; }, catch: function () { return never; } };
  const fetchStub = function (url) { fetched.push(url); return never; };
  new Function('window', 'document', 'URLSearchParams', 'fetch', headGate)(e.win, e.doc, URLSearchParams, fetchStub);
  return { ret: e.win.__ADMIN_RETURN_TO || null, cleared: e.cleared.length, nav: e.navigations,
           stale: e.win.__ADMIN_STALE || false, probe: !!e.win.__SESSION_PROBE, fetched: fetched };
}

// Mimic the async post-session bounce: whatever claimRedirectTarget() returns is
// assigned to window.location.href, so it must be a NAVIGATION target.
function runTargets(ret, cookie) {
  const e = mkEnv('', cookie);
  e.win.__ADMIN_RETURN_TO = ret || null;
  const DASHBOARD_URL = ret ? ORIGIN + ret : APP;
  const WELCOME_URL = DASHBOARD_URL;
  const make = new Function(
    'window', 'document', 'URLSearchParams', 'DASHBOARD_URL', 'WELCOME_URL',
    claimSrc + '\\nreturn { claim: claimRedirectTarget, oauth: oauthNextPath };',
  );
  const fns = make(e.win, e.doc, URLSearchParams, DASHBOARD_URL, WELCOME_URL);
  return { nav: fns.claim(), oauth: fns.oauth() };
}

// #3501: the decision moved off the synchronous gate into the probe consumer.
// A SYNCHRONOUS thenable stands in for the fetch promise so the callback's
// effect is observable without an event loop.
function runConsumer(status, ret, cookie, opts) {
  opts = opts || {};
  const e = mkEnv('', cookie);
  e.win.__ADMIN_RETURN_TO = ret || null;
  if (opts.adminStale) e.win.__ADMIN_STALE = true;
  if (opts.oauthError) e.win.__OAUTH_ERROR = true;
  const DASHBOARD_URL = ret ? ORIGIN + ret : APP;
  const WELCOME_URL = DASHBOARD_URL;
  const make = new Function(
    'window', 'document', 'URLSearchParams', 'DASHBOARD_URL', 'WELCOME_URL',
    claimSrc + '\\nreturn { claim: claimRedirectTarget };',
  );
  const fns = make(e.win, e.doc, URLSearchParams, DASHBOARD_URL, WELCOME_URL);
  const errors = [];
  e.win.__SESSION_PROBE = { then: function (cb) { cb(status); } };
  new Function('window', 'claimRedirectTarget', 'showError', consumerSrc)(
    e.win, fns.claim, function (m) { errors.push(m); },
  );
  return { nav: e.navigations, errors: errors };
}

const out = { early: [], headGate: [], claim: [], consumer: [], gate: [] };
for (const c of cases.early) out.early.push(runEarly(c[0], c[1]));
for (const c of cases.headGate) out.headGate.push(runHeadGate(c[0], c[1], c[2]));
for (const c of cases.claim) out.claim.push(runTargets(c[0], c[1]));
for (const c of cases.consumer) out.consumer.push(runConsumer(c[0], c[1], c[2], c[3]));

// #3080: execute the gate's real decision table (not a substring check).
const decide = new Function(gateSrc + '\\nreturn { gateDecision: gateDecision, tokenReasonKind: tokenReasonKind, adminKindForResponse: adminKindForResponse };')();
for (const c of cases.gate) {
  out.gate.push(decide.gateDecision({ configured: c[0], token: c[1], session: c[2], admin: c[3] }));
}
out.tokenReason = cases.tokenReason.map(function (c) { return decide.tokenReasonKind(c[0]); });
out.adminKind = cases.adminResponse.map(function (c) { return decide.adminKindForResponse(c[0], c[1]); });

// #3080: cross-allowlist agreement. Whatever the SERVER gate emits for a path
// must be accepted by the CLIENT allowlist — drift silently loses the return-to.
// Each entry is [path, expected server output] so the server side is pinned to
// FIDELITY, not just agreement: agreement alone is satisfied by a server that
// degrades every deep path to the /admin fallback while the client accepts it.
const serverDecide = new Function(serverSrc + '\\nreturn returnToPath;')();
out.corpus = cases.corpus.map(function (e) {
  const p = e[0];
  const s = serverDecide({ url: ORIGIN + p });
  const c = runEarly('?next=' + encodeURIComponent(s), '');
  return { path: p, expected: e[1], server: s, clientRet: c.ret };
});
console.log(JSON.stringify(out));
"""


def _run(cases: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    for key in ("early", "headGate", "claim", "consumer", "gate"):
        cases.setdefault(key, [])
    cases.setdefault("tokenReason", [])
    cases.setdefault("adminResponse", [])
    cases.setdefault("corpus", [])
    blocks = _blocks()
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for key, name in (("early", "early.js"), ("headGate", "headgate.js"), ("claim", "claim.js"), ("consumer", "consumer.js"), ("gate", "gate.js"), ("server", "server.js")):
            (Path(td) / name).write_text(blocks[key], encoding="utf-8")
        (Path(td) / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        driver = Path(td) / "driver.js"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [node, str(driver), td, ORIGIN, APP_ORIGIN],
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


# ── the allowlist (open-redirect sink) ──────────────────────────────────────

def test_next_is_honoured_for_admin_paths() -> None:
    """A well-formed /admin return-to becomes the post-login destination."""
    cases = {"early": [[p, ""] for p in ("?next=/admin", "?next=/admin/blog", "?next=%2Fadmin%2Fblog", "?next=/admin/")], "headGate": [], "claim": []}
    expected = ["/admin", "/admin/blog", "/admin/blog", "/admin/"]
    for result, want in zip(_run(cases)["early"], expected, strict=True):
        assert result["base"] == ORIGIN + want, f"{result} != {ORIGIN + want}"
        assert result["ret"] == want, f"__ADMIN_RETURN_TO not set: {result}"


@pytest.mark.parametrize(
    "search",
    [
        "?next=https://evil.com",       # absolute → different origin
        "?next=//evil.com",             # protocol-relative → different origin
        "?next=/blog",                  # outside the allowlist
        "?next=/administer",            # prefix confusion
        "?next=/administrator",         # prefix confusion
        "?next=/admin/../blog",         # dot-segment escape → normalises to /blog
        "?next=/admin/./../../etc",     # dot-segment escape
        "?next=/admin\\@evil.com",      # backslash authority trick
        "?next=javascript:alert(1)",    # scheme
        "?next=",                       # empty
        "",                             # absent
    ],
)
def test_unsafe_next_is_ignored(search: str) -> None:
    """Anything outside the same-origin /admin allowlist leaves the default alone."""
    (result,) = _run({"early": [[search, ""]], "headGate": [], "claim": []})["early"]
    assert result["base"] is None, f"unsafe return-to honoured: {search!r} → {result['base']!r}"
    assert result["ret"] is None, f"unsafe return-to recorded: {search!r}"


# ── the two targets: navigation vs GoTrue redirect_to ──────────────────────

def test_navigation_target_for_admins_is_the_console() -> None:
    """After a session exists we NAVIGATE to /admin — never back to /auth.

    Both the head gate and the async getSession bounce assign this to
    location.href, so returning `/auth` here would loop into the auth page
    forever.
    """
    (t,) = _run({"early": [], "headGate": [], "claim": [["/admin/blog", ""]]})["claim"]
    assert t["nav"] == f"{ORIGIN}/admin/blog", t["nav"]


def test_oauth_next_path_is_the_admin_return_to() -> None:
    """`/auth/start` must receive the return-to as `next`, as a same-origin PATH.

    Under the BFF the PKCE verifier and the GoTrue `redirect_to` are the server's
    business; the page contributes only the post-login destination. A PATH (not
    an absolute URL) is what `/auth/start` stores and `/auth/callback`
    re-validates with `safeNext`. The old rules still hold in spirit:
      - the destination must survive the round-trip, or /admin is unreachable;
      - it must not point at the auth page itself (a loop);
      - `stale` must never ride it.
    """
    (t,) = _run({"early": [], "headGate": [], "claim": [["/admin/blog", ""]]})["claim"]
    oauth = t["oauth"]
    assert oauth == "/admin/blog", f"the OAuth `next` must be the same-origin return-to, got {oauth!r}"
    assert not oauth.startswith("http"), f"`next` must be a path, not an absolute URL: {oauth!r}"
    assert "stale" not in oauth, f"stale would clear the fresh session: {oauth!r}"
    assert oauth != "/auth", "`next` must not bounce back to the auth page"


def test_targets_unchanged_without_an_admin_return_to() -> None:
    """The non-admin funnel must be unchanged: the app root, or the claim card."""
    (plain,) = _run({"early": [], "headGate": [], "claim": [[None, ""]]})["claim"]
    assert plain["nav"] == APP_ORIGIN, plain["nav"]
    assert plain["oauth"] == "/", plain["oauth"]
    (claiming,) = _run({"early": [], "headGate": [], "claim": [[None, "tt_claim_pending=1"]]})["claim"]
    assert claiming["nav"] == f"{APP_ORIGIN}/?claim=1", claiming["nav"]
    assert claiming["oauth"] == "/?claim=1", claiming["oauth"]


# ── the redirect-loop breaker ──────────────────────────────────────────────

def test_stale_bounce_suppresses_forwarding_without_destroying_the_session() -> None:
    """A server-refused session must stop the loop WITHOUT being destroyed.

    `readValidSession()` only checks expires_at locally, so without suppression
    the head gate replaces to /admin, the gate refuses again, and the browser
    dies with ERR_TOO_MANY_REDIRECTS. But the bounce URL is attacker-forgeable
    (?next=/admin&stale=1), so DELETING the shared parent-domain cookie here
    would be a one-link forced logout of the dashboard too — and an expired
    access token is refreshable client-side, so it is not ours to discard.
    """
    session = {"access_token": "t", "expires_at": 4102444800}  # locally "valid"
    (stale,) = _run({"early": [], "headGate": [["?next=%2Fadmin&stale=1", "", session]], "claim": []})["headGate"]
    assert stale["nav"] == [], f"looped back to the console: {stale['nav']}"
    assert stale["cleared"] == 0, "destroyed a session that may still be refreshable"
    assert stale["stale"] is True, "stale not flagged — the probe will re-loop"
    assert stale["probe"] is False, "the stale bounce must not even start the probe"
    # And the consumer must honour the flag too (the probe is suppressed, but a
    # race must not forward either).
    (suppressed,) = _run({"consumer": [[200, "/admin/blog", "", {"adminStale": True}]]})["consumer"]
    assert suppressed["nav"] == [], f"the consumer forwarded despite the stale flag: {suppressed}"


def test_valid_session_still_reaches_the_console() -> None:
    """A 200 from /api/session forwards to the return-to (the happy path)."""
    (ok,) = _run({"consumer": [[200, "/admin/blog", "", {}]]})["consumer"]
    assert ok["nav"] == [f"{ORIGIN}/admin/blog"], ok["nav"]
    assert ok["errors"] == [], f"a healthy session produced an error: {ok['errors']}"


def test_no_session_stays_on_the_auth_card() -> None:
    (none,) = _run({"consumer": [[401, "/admin", "", {}]]})["consumer"]
    assert none["nav"] == [], "bounced a visitor with no session"
    assert none["errors"] == [], f"a 401 is not an error to display: {none['errors']}"


def test_store_fault_does_not_sign_the_user_out() -> None:
    """A 503 is 'we could not tell' — never 'signed out' (#3485).

    The failure mode it guards is a login loop: treating a store fault as
    signed-out forwards to /admin, which refuses, which bounces back.
    """
    (fault,) = _run({"consumer": [[503, "/admin/blog", "", {}]]})["consumer"]
    assert fault["nav"] == [], f"a store fault bounced the visitor: {fault['nav']}"
    assert fault["errors"], "a store fault must surface a retryable notice"
    (network,) = _run({"consumer": [[0, "/admin/blog", "", {}]]})["consumer"]
    assert network["nav"] == [], f"a network fault bounced the visitor: {network['nav']}"
    assert network["errors"], "a network fault must surface a retryable notice"


# ── static guards on the Function ──────────────────────────────────────────

def test_gate_bounces_with_an_allowlisted_return_to() -> None:
    src = GATE.read_text(encoding="utf-8")
    assert "next=${encodeURIComponent(returnTo)}" in src, (
        "the gate no longer appends a return-to — /admin and /auth would drift apart again (#3080)"
    )
    assert "&stale=1" in src, (
        "the bounce lost its stale marker — the auth page would loop back to /admin (#3080)"
    )
    assert 'path.startsWith("//")' in src, "the protocol-relative guard was removed"
    assert 'path.includes("\\\\")' in src, "the backslash guard was removed"


@pytest.mark.parametrize(
    "configured,token,session,admin,want",
    [
        # not configured → 503, never a silent bounce
        (False, "t", "ok", "admin", "unavailable"),
        # no token → authenticate
        (True, None, "skipped", "skipped", "auth"),
        # Supabase unreachable → 503 (NOT a stale bounce that clears the session)
        (True, "t", "unavailable", "skipped", "unavailable"),
        # server refused the token → stale bounce
        (True, "t", "unauthenticated", "skipped", "auth"),
        # allowlist query failed → 503 (our config problem, not a verdict)
        (True, "t", "ok", "unavailable", "unavailable"),
        # the is_admin() RPC rejected the minted token → re-authenticate
        (True, "t", "ok", "unauthenticated", "auth"),
        # authenticated but not an admin → explicit 403
        (True, "t", "ok", "not-admin", "not-admin"),
        # the happy path
        (True, "t", "ok", "admin", "shell"),
    ],
)
def test_gate_decision_matrix(configured: bool, token, session: str, admin: str, want: str) -> None:
    """Execute the gate's REAL decision table and assert the response mapping.

    This replaces substring assertions, which were decorative: reverting the
    outage→503, isAdmin-outage, or 403 logic each still passed 20/20.
    """
    (got,) = _run({"gate": [[configured, token, session, admin]]})["gate"]
    assert got == want, f"configured={configured} token={token} session={session} admin={admin} → {got!r}, want {want!r}"


def test_token_reason_mapping_separates_dead_from_fault() -> None:
    """Only a genuinely DEAD session means "re-authenticate".

    `getAccessTokenForSession` distinguishes `no_session` (the row is gone or the
    refresh token is dead — re-auth) from `unavailable` (the store or the provider
    is down — 503). Collapsing them is the #3485 class: a transient fault would
    emit stale=1 for every visitor and log them out of the shared cookie, while a
    dead session would 503 forever instead of bouncing to sign-in.
    """
    cases = [
        ("no_session", "unauthenticated"),
        ("unavailable", "unavailable"),
    ]
    got = _run({"tokenReason": [[c[0]] for c in cases]})["tokenReason"]
    for (reason, want), actual in zip(cases, got, strict=True):
        assert actual == want, f"{reason!r} → {actual!r}, want {want!r}"


@pytest.mark.parametrize(
    "ok,count,want",
    [(False, 0, "unavailable"), (True, 0, "not-admin"), (True, 1, "admin"), (True, 3, "admin")],
)
def test_admin_response_mapping(ok: bool, count: int, want: str) -> None:
    """A failing service-role query is OUR config problem, not a user verdict."""
    (got,) = _run({"adminResponse": [[ok, count]]})["adminKind"]
    assert got == want, f"ok={ok} count={count} → {got!r}, want {want!r}"


def test_verify_session_wires_the_classifier() -> None:
    """The classifier leaves are tested behaviourally — pin their CALL SITES too.

    Without this, replacing `tokenReasonKind(token.reason)` with a hardcoded
    `{kind: "unavailable"}` (or "unauthenticated") restores half the #3485 class
    while the behavioural suite stays green: a dead session would 503 forever, or
    a store fault would sign the user out. Same for `adminKind`.
    """
    src = GATE.read_text(encoding="utf-8")
    i = src.find("async function verifySession")
    assert i != -1, "verifySession was removed"
    body = src[i : src.find("\nasync function", i + 10)]
    assert "tokenReasonKind(token.reason)" in body, (
        "verifySession no longer delegates to the token-reason classifier (#3485)"
    )
    j = src.find("async function isAdmin")
    assert j != -1, "isAdmin was removed"
    abody = src[j : src.find("\nasync function", j + 10)]
    assert "adminKind(true, isAdminUser ? 1 : 0)" in abody, (
        "isAdmin no longer delegates to the response classifier (#3080)"
    )


def test_gate_maps_every_decision_to_a_response() -> None:
    """Pin the decision→response switch (the last unmapped layer).

    Reverting `case "unavailable"` back to `redirectToAuth(returnTo)` restores
    the pre-#3080 silent-bounce outage, so it must be asserted somewhere.
    """
    src = GATE.read_text(encoding="utf-8")
    for decision, call in (
        ('case "auth":', "redirectToAuth(returnTo)"),
        ('case "not-admin":', "notAnAdmin()"),
        ('case "shell":', "serveShell(env, request)"),
        ('case "unavailable":', "unavailable()"),
    ):
        i = src.find(decision)
        assert i != -1, f"the gate no longer handles {decision}"
        window = src[i : i + 160]
        assert call in window, f"{decision} does not return {call}: {window[:120]!r}"


def test_server_and_client_allowlists_agree() -> None:
    """Whatever the gate emits, the auth page must accept it.

    These two allowlists drifted once: the server emitted `next=/admin/a:b` and
    the client rejected it for containing ':', so the return-to was silently
    dropped and post-login navigation fell through to the app root — #3080 for
    colon paths. This is the class-level guard, not just that one case.
    """
    corpus = [
        # (path, expected server output). Deep /admin paths must survive intact —
        # a server that degrades them to the fallback would otherwise satisfy a
        # pure agreement check.
        ("/admin", "/admin"),
        ("/admin/", "/admin/"),
        ("/admin/blog", "/admin/blog"),
        ("/admin/a:b", "/admin/a:b"),          # the drift that was found
        ("/admin/edit/1", "/admin/edit/1"),
        ("/admin/sub/deep/path", "/admin/sub/deep/path"),
        ("/admin/assets/index-DS3aDc5i.js", "/admin/assets/index-DS3aDc5i.js"),
        ("/admin/#/edit/1", "/admin/"),         # fragment is not part of the path
        ("/admin/../blog", "/admin"),           # normalises out of the allowlist
        ("/administer", "/admin"),              # not under /admin
        ("/blog", "/admin"),                    # outside the allowlist
    ]
    rows = _run({"corpus": corpus})["corpus"]
    assert len(rows) == len(corpus), (
        f"corpus harness returned {len(rows)} rows for {len(corpus)} cases — a short "
        "result would make this test pass without checking anything"
    )
    for row in rows:
        assert row["server"] == row["expected"], (
            f"server allowlist changed for {row['path']!r}: "
            f"expected {row['expected']!r}, got {row['server']!r}"
        )
        assert row["clientRet"] == row["server"], (
            f"allowlist drift for {row['path']!r}: server emits {row['server']!r}, "
            f"client reads {row['clientRet']!r}"
        )


def test_email_flows_do_not_build_a_client_redirect_target() -> None:
    """Signup/resend confirmation links are the BFF's business now.

    `/auth/signup` and `/auth/resend` call GoTrue with the route's OWN
    `${APP_ORIGIN}/auth/confirm` target, so the page must not build an
    `emailRedirectTo` at all — the old "WELCOME_URL sent the confirmation link to
    the gated /admin" bug (#3080) cannot recur because the page no longer chooses
    the target.
    """
    src = SIGNUP.read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("emailRedirectTo:"), (
            f"the page still builds a GoTrue redirect target: {stripped!r}"
        )
        assert "options: { emailRedirectTo:" not in stripped, (
            f"the page still builds a GoTrue redirect target: {stripped!r}"
        )
    assert "emailRedirectTo: WELCOME_URL" not in src


def test_unguarded_stale_is_inert() -> None:
    """stale=1 without the gate's return-to shape must not arm the stale path."""
    session = {"access_token": "t", "expires_at": 4102444800}  # healthy
    (bare,) = _run({"headGate": [["?stale=1", "", session]]})["headGate"]
    assert bare["stale"] is False, "a bare ?stale=1 armed the stale path"
    assert bare["cleared"] == 0, "a bare ?stale=1 wiped a healthy session"


def test_oauth_call_site_uses_the_bff_start_route() -> None:
    """The signInWithProvider call site must navigate to /auth/start.

    Testing oauthNextPath() alone is not enough: a call site that kept building a
    client-side GoTrue URL would still pass, because the function itself is fine.
    `/auth/start` is what mints the server-side PKCE verifier, so the call site
    is the load-bearing half.
    """
    src = SIGNUP.read_text(encoding="utf-8")
    i = src.find("function signInWithProvider")
    assert i != -1, "signInWithProvider not found"
    block = src[i : i + 900]
    assert '"/auth/start?provider="' in block, (
        f"the OAuth call site does not use the BFF start route: {block[:300]!r}"
    )
    assert "oauthNextPath()" in block, "the call site does not pass the return-to"
    assert "signInWithOAuth" not in block, (
        "the call site still builds a GoTrue URL client-side (its PKCE verifier is invisible to /auth/callback)"
    )


def test_probe_consumer_honours_the_stale_flag() -> None:
    """The probe consumer must not re-enter the loop the bounce broke."""
    src = SIGNUP.read_text(encoding="utf-8")
    i = src.find("if (window.__SESSION_PROBE)")
    assert i != -1, "the session probe consumer was removed"
    block = src[i : i + 1000]
    assert "__ADMIN_STALE" in block, (
        "the probe consumer ignores the stale flag — it will forward straight back to /admin (#3080)"
    )


def test_console_spa_carries_the_return_to() -> None:
    """The SPA's own gate must not bounce to a bare /auth (same defect, one layer in)."""
    spa = REPO_ROOT / "website" / "apps" / "blog-admin" / "src" / "hooks" / "useAuth.ts"
    src = spa.read_text(encoding="utf-8")
    assert "authUrlWithReturn()" in src, "the SPA lost its return-to helper (#3080)"
    assert "window.location.replace(AUTH_URL)" not in src, (
        "the SPA still bounces to a bare /auth — re-login lands on the app root, not the console (#3080)"
    )
    # The helper must send the PATHNAME only. /auth rejects a `next` containing
    # ':' or '\\' anywhere, so pathname+search silently drops the return-to for
    # e.g. /admin?t=12:00 — the #3080 symptom again.
    i = src.find("function authUrlWithReturn")
    assert i != -1, "authUrlWithReturn was removed"
    body = src[i : i + 700]
    assert "window.location.pathname" in body, "the helper does not use the pathname"
    assert "window.location.search" not in body, (
        "the helper appends the query, which /auth rejects — dropping the return-to (#3080)"
    )


# ── #3952 — the console the gate returns you to must actually RENDER ──────

DIST_INDEX = REPO_ROOT / "website" / "apps" / "blog-admin" / "dist" / "index.html"
VITE_CONFIG = REPO_ROOT / "website" / "apps" / "blog-admin" / "vite.config.ts"

# Vite emits exactly two asset-bearing tags into the shell: the module script and
# the stylesheet link. Selecting by TAG (rather than by every `src`/`href` in the
# document) is what scopes this to the refs Vite owns — a hand-authored favicon
# `<link rel="icon">`, an `<a href>`, or a `#/route` fragment cannot be mistaken
# for the bundle.
_VITE_ASSET_TAG = re.compile(
    r'<script\b[^>]*\bsrc="([^"]+)"'
    r'|<link\b[^>]*\brel="stylesheet"[^>]*\bhref="([^"]+)"',
    re.IGNORECASE,
)


def _console_asset_refs() -> list[str]:
    """Vite-emitted asset refs in the COMMITTED console shell.

    Also requires a JS module ref: the stylesheet alone would still satisfy a
    shape check while `<div id="root">` stays empty and the console renders blank
    — the exact outcome this guard exists to catch (#3952).
    """
    html = DIST_INDEX.read_text(encoding="utf-8")
    refs = [a or b for a, b in _VITE_ASSET_TAG.findall(html) if not (a or b).startswith("data:")]
    assert refs, f"no Vite-emitted asset references in {DIST_INDEX}"
    assert any(r.endswith(".js") for r in refs), (
        f"no JS module ref in {DIST_INDEX} — the shell would render no script and "
        "the console would render blank (#3952)"
    )
    return refs


@pytest.mark.parametrize(
    "doc_url",
    [
        # The canonical console URL — and the exact form the gate's next= emits.
        # BROKEN with the old relative base (prefix '/').
        f"{ORIGIN}/admin",
        # Trailing-slash form — renders today; must not regress.
        f"{ORIGIN}/admin/",
        # Even-depth shell route — happened to resolve correctly with './' too.
        f"{ORIGIN}/admin/blog",
        # Odd-depth shell route — BROKEN with the old relative base
        # (prefix '/admin/blog/' → '/admin/blog/assets/...').
        f"{ORIGIN}/admin/blog/edit",
    ],
)
def test_console_bundle_resolves_under_admin_from_every_entry_path(doc_url: str) -> None:
    """#3952: every entry path that serves the shell must be able to load its bundle.

    A RELATIVE base ('./') resolves against the DOCUMENT URL (RFC 3986 §5.2.3
    "Merge Paths"), so whether it worked depended on the document's segment depth.
    At `/admin/` and
    `/admin/blog` the base prefix is `/admin/`, so `./assets/...` resolved to the
    real bundle. At the extensionless `/admin` — the canonical console URL, and the
    very form the gate emits in its own `next=` — the prefix is `/`, so the shell
    requested `/assets/index-...js`, which is never deployed (CI stages the SPA
    into the app project's dist/admin/, so the bundle exists only at
    /admin/assets/). Odd-depth forms
    like `/admin/blog/edit` failed the same way. Result: `<div id="root"></div>`
    with no script = blank page.

    Scope of this assertion: it pins the COMMITTED build snapshot. It is not the
    byte-identical deployed bundle — CI rebuilds with VITE_* env substitution, so
    the deployed JS filename differs. The base that build derives from is pinned
    by `test_vite_base_is_the_console_public_path` below; the staging/mount
    destination is not asserted here (filed as #3954).
    """
    for ref in _console_asset_refs():
        resolved = urlparse(urljoin(doc_url, ref)).path
        # The invariant is DOCUMENT-INDEPENDENCE: the bundle lives at one place,
        # so resolving a reference from any entry path must name that same place.
        # (Asserting merely "starts with /admin/" is too weak — under the old
        # relative base `/admin/blog/edit` resolved to `/admin/blog/assets/...`,
        # which is still under /admin/ but is not where the bundle is deployed.)
        canonical = urlparse(urljoin(f"{ORIGIN}/admin/", ref)).path
        assert canonical.startswith("/admin/"), (
            f"{ref!r} does not resolve under /admin/ — the console bundle is never "
            "deployed outside /admin/assets/ (#3952)"
        )
        assert resolved == canonical, (
            f"{doc_url} → {ref!r} resolves to {resolved!r}, but the bundle is deployed "
            f"at {canonical!r} — resolution depends on the entry path, so a 404 and a "
            "blank console are possible (#3952)"
        )


def test_console_bundle_files_are_present() -> None:
    """#3952: the shell's refs must name files that actually exist in the snapshot.

    A shape-only assertion stays green on the very outcome it exists to prevent: a
    ref to a nonexistent hash is exactly the 404 that blanks the console.
    """
    for ref in _console_asset_refs():
        resolved = urlparse(urljoin(f"{ORIGIN}/admin/", ref)).path
        rel = resolved.removeprefix("/admin/")
        assert (DIST_INDEX.parent / rel).is_file(), (
            f"the shell references {ref!r} but {rel!r} does not exist in "
            f"{DIST_INDEX.parent} — the browser would 404 and the console would "
            "render blank (#3952)"
        )


def test_vite_base_is_the_console_public_path() -> None:
    """#3952: the base must be ABSOLUTE, and equal to the gate's mount path.

    The base path is known and fixed, so the absolute form is the documented
    treatment. Vite documents the relative form as the fallback "if you don't know
    the base path in advance" (vite.dev/guide/build.html → "Relative base").

    The expected value is derived from the gate Function's own directory
    (`functions/admin/[[path]].ts` → `/admin/`), not hardcoded, so a rename of the
    console route cannot silently diverge from the build base.
    """
    cfg = VITE_CONFIG.read_text(encoding="utf-8")
    m = re.search(r"^\s*base:\s*['\"]([^'\"]*)['\"]", cfg, re.MULTILINE)
    assert m, "no `base` in vite.config.ts — the SPA inherits the default '/'"
    mount = f"/{GATE.parent.name}/"
    assert m.group(1) == mount, (
        f"vite base is {m.group(1)!r}, must be the absolute {mount!r} — the path the "
        "gate Function mounts the console at. A relative base re-breaks the "
        "extensionless /admin entry path (#3952)"
    )
