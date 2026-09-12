"""#3080 — the /admin return-to guard.

The blog admin gate (`website/functions/admin/[[path]].ts`) bounces an
unauthenticated request to `/auth?next=<path>&stale=1` so the post-login
redirect comes back to the console. Before #3080 the bounce was a bare `/auth`,
so every login landed on the app root and `/admin` was unreachable by
navigation.

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
"""

from __future__ import annotations  # noqa: I001

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNUP = REPO_ROOT / "website" / "signup.html"
GATE = REPO_ROOT / "website" / "functions" / "admin" / "[[path]].ts"

ORIGIN = "https://tortoise.premiselabs.co"
APP_ORIGIN = "https://app.premiselabs.co"

_EARLY = "#3080: admin return-to."
_HEAD_GATE = "#1494: hard gate"


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


def _blocks() -> dict[str, str]:
    html = SIGNUP.read_text(encoding="utf-8")
    return {
        "early": _script_after(html, _EARLY),
        "headGate": _script_after(html, _HEAD_GATE),
        "claim": _function(html, "claimRedirectTarget") + "\n" + _function(html, "gotrueRedirectTarget"),
        "gate": _function(GATE.read_text(encoding="utf-8"), "gateDecision")
        + "\n"
        + _function(GATE.read_text(encoding="utf-8"), "sessionKindForStatus")
        + "\n"
        + _function(GATE.read_text(encoding="utf-8"), "adminKindForResponse"),
    }


_DRIVER = """
const fs = require('fs');
const path = require('path');
const dir = process.argv[2];
const blocks = [];
for (const n of ['early.js', 'headgate.js', 'claim.js', 'gate.js']) {
  blocks.push(fs.readFileSync(path.join(dir, n), 'utf8'));
}
const early = blocks[0], headGate = blocks[1], claimSrc = blocks[2], gateSrc = blocks[3];
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

// Real page order: the early block runs, then the #1494 head gate.
function runHeadGate(search, cookie, session) {
  const e = mkEnv(search, cookie, session);
  new Function('window', 'document', 'URLSearchParams', early)(e.win, e.doc, URLSearchParams);
  new Function('window', 'document', 'URLSearchParams', headGate)(e.win, e.doc, URLSearchParams);
  return { ret: e.win.__ADMIN_RETURN_TO || null, cleared: e.cleared.length, nav: e.navigations, stale: e.win.__ADMIN_STALE || false };
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
    claimSrc + '\\nreturn { claim: claimRedirectTarget, oauth: gotrueRedirectTarget };',
  );
  const fns = make(e.win, e.doc, URLSearchParams, DASHBOARD_URL, WELCOME_URL);
  return { nav: fns.claim(), oauth: fns.oauth() };
}

const out = { early: [], headGate: [], claim: [], gate: [] };
for (const c of cases.early) out.early.push(runEarly(c[0], c[1]));
for (const c of cases.headGate) out.headGate.push(runHeadGate(c[0], c[1], c[2]));
for (const c of cases.claim) out.claim.push(runTargets(c[0], c[1]));

// #3080: execute the gate's real decision table (not a substring check).
const decide = new Function(gateSrc + '\\nreturn { gateDecision: gateDecision, sessionKindForStatus: sessionKindForStatus, adminKindForResponse: adminKindForResponse };')();
for (const c of cases.gate) {
  out.gate.push(decide.gateDecision({ configured: c[0], token: c[1], session: c[2], admin: c[3] }));
}
out.sessionKind = {};
out.sessionKind = cases.sessionStatus.map(function (c) { return decide.sessionKindForStatus(c[0], c[1]); });
out.adminKind = cases.adminResponse.map(function (c) { return decide.adminKindForResponse(c[0], c[1]); });
console.log(JSON.stringify(out));
"""


def _run(cases: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    for key in ("early", "headGate", "claim", "gate"):
        cases.setdefault(key, [])
    cases.setdefault("sessionStatus", [])
    cases.setdefault("adminResponse", [])
    blocks = _blocks()
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for key, name in (("early", "early.js"), ("headGate", "headgate.js"), ("claim", "claim.js"), ("gate", "gate.js")):
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
    for result, want in zip(_run(cases)["early"], expected):
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


def test_oauth_target_for_admins_is_absolute_auth_without_stale() -> None:
    """GoTrue's redirect_to must be an absolute /auth url carrying the return-to.

    Three separate regressions are guarded here:
      - relative → GoTrue drops it and the return-to is lost;
      - `/admin` → the #access_token fragment never reaches the server, so the
        gate bounces and the session is lost;
      - `stale=1` → the auth page would clear the token that just arrived.
    """
    (t,) = _run({"early": [], "headGate": [], "claim": [["/admin/blog", ""]]})["claim"]
    oauth = t["oauth"]
    parsed = urlparse(oauth)
    assert oauth.startswith("https://"), f"GoTrue drops a relative redirect_to: {oauth!r}"
    assert parsed.netloc == "tortoise.premiselabs.co", f"wrong host: {oauth!r}"
    assert parsed.path == "/auth", f"the provider must return to /auth, not {parsed.path!r}"
    q = parse_qs(parsed.query)
    assert q.get("next") == ["/admin/blog"], f"return-to lost: {oauth!r}"
    assert "stale" not in q, f"stale=1 would clear the fresh session: {oauth!r}"


def test_targets_unchanged_without_an_admin_return_to() -> None:
    """The non-admin funnel must be byte-identical to pre-#3080 behaviour."""
    (plain,) = _run({"early": [], "headGate": [], "claim": [[None, ""]]})["claim"]
    assert plain["nav"] == APP_ORIGIN, plain["nav"]
    assert plain["oauth"] == APP_ORIGIN, plain["oauth"]
    (claiming,) = _run({"early": [], "headGate": [], "claim": [[None, "tt_claim_pending=1"]]})["claim"]
    assert claiming["nav"] == f"{APP_ORIGIN}/?claim=1", claiming["nav"]
    assert claiming["oauth"] == f"{APP_ORIGIN}/?claim=1", claiming["oauth"]


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
    assert stale["stale"] is True, "stale not flagged — the async getSession bounce will re-loop"


def test_valid_session_still_reaches_the_console() -> None:
    """The loop breaker must not disable the happy path (and the OAuth landing)."""
    session = {"access_token": "t", "expires_at": 4102444800}
    (ok,) = _run({"early": [], "headGate": [["?next=%2Fadmin%2Fblog", "", session]], "claim": []})["headGate"]
    assert ok["nav"] == ["/admin/blog"], ok["nav"]
    assert ok["cleared"] == 0, "cleared a session the server had accepted"
    assert ok["stale"] is False, "a healthy session was marked stale"


def test_no_session_stays_on_the_auth_card() -> None:
    (none,) = _run({"early": [], "headGate": [["?next=%2Fadmin&stale=1", "", None]], "claim": []})["headGate"]
    assert none["nav"] == [], "bounced a visitor with no session"


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


def test_session_status_mapping_matches_supabase() -> None:
    """Only a bad USER token means "re-authenticate".

    Supabase answers 401 "Invalid API key" when OUR apikey is rotated and 403
    bad_jwt when the user's token is bad. Reading the 401 as a bad session would
    emit stale=1 for every visitor on a key rotation — an outage-class failure
    that must never look like a logout.
    """
    cases = [
        (403, '{"error_code":"bad_jwt"}', "unauthenticated"),
        (401, '{"error_code":"bad_jwt"}', "unauthenticated"),
        (401, '{"message":"Invalid API key"}', "unavailable"),
        (429, "", "unavailable"),
    ]
    got = _run({"sessionStatus": [[c[0], c[1]] for c in cases]})["sessionKind"]
    for (status, body, want), actual in zip(cases, got):
        assert actual == want, f"HTTP {status} {body!r} → {actual!r}, want {want!r}"


@pytest.mark.parametrize(
    "ok,count,want",
    [(False, 0, "unavailable"), (True, 0, "not-admin"), (True, 1, "admin"), (True, 3, "admin")],
)
def test_admin_response_mapping(ok: bool, count: int, want: str) -> None:
    """A failing service-role query is OUR config problem, not a user verdict."""
    (got,) = _run({"adminResponse": [[ok, count]]})["adminKind"]
    assert got == want, f"ok={ok} count={count} → {got!r}, want {want!r}"


def test_verify_session_wires_the_classifier() -> None:
    """The classifier leaf is tested behaviourally — pin its CALL SITE too.

    Without this, replacing `sessionKindForStatus(res.status, body)` with a
    hardcoded `{kind: "unauthenticated"}` restores the #3080 bug (a rotated
    apikey read as a per-user auth verdict) while the suite stays green.
    """
    src = GATE.read_text(encoding="utf-8")
    i = src.find("async function verifySession")
    assert i != -1, "verifySession was removed"
    body = src[i : src.find("\nasync function", i + 10)]
    assert "sessionKindForStatus(res.status, body)" in body, (
        "verifySession no longer delegates to the status/body classifier (#3080)"
    )
    j = src.find("async function isAdmin")
    assert j != -1, "isAdmin was removed"
    abody = src[j : src.find("\nasync function", j + 10)]
    assert "adminKindForResponse(false, 0)" in abody and "adminKindForResponse(true, rows.length)" in abody, (
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


def test_email_flows_use_the_gotrue_target_not_the_console() -> None:
    """signUp/resend must not point GoTrue at the gated /admin.

    WELCOME_URL is derived from the (now overloaded) __DASHBOARD_BASE_URL, so
    using it as emailRedirectTo sent confirmation links to /admin — where the
    fragment is invisible to the server, the gate bounces, and the stale branch
    deletes the freshly-confirmed session.
    """
    src = SIGNUP.read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("emailRedirectTo:") or stripped.startswith("options: { emailRedirectTo:"):
            assert "gotrueRedirectTarget()" in stripped, (
                f"emailRedirectTo must use the GoTrue target, not a navigation one: {stripped!r}"
            )
    assert "emailRedirectTo: WELCOME_URL" not in src, "a confirmation email still redirects to the console (#3080)"


def test_unguarded_stale_is_inert() -> None:
    """stale=1 without the gate's return-to shape must not arm the stale path."""
    session = {"access_token": "t", "expires_at": 4102444800}  # healthy
    (bare,) = _run({"headGate": [["?stale=1", "", session]]})["headGate"]
    assert bare["stale"] is False, "a bare ?stale=1 armed the stale path"
    assert bare["cleared"] == 0, "a bare ?stale=1 wiped a healthy session"


def test_oauth_call_site_uses_the_gotrue_target() -> None:
    """The signInWithOAuth call site must hand GoTrue the /auth target.

    Testing gotrueRedirectTarget() alone is not enough: reverting the call site
    back to claimRedirectTarget() (the primary Google/GitHub path) would still
    pass, because the function itself is fine.
    """
    src = SIGNUP.read_text(encoding="utf-8")
    i = src.find("signInWithOAuth(")
    assert i != -1, "signInWithOAuth call site not found"
    block = src[i : i + 600]
    assert "redirectTo: gotrueRedirectTarget()" in block, (
        f"the OAuth call site does not use the GoTrue target: {block[:200]!r}"
    )


def test_async_bounce_honours_the_stale_flag() -> None:
    """The async getSession bounce must not re-enter the loop the head gate broke."""
    src = SIGNUP.read_text(encoding="utf-8")
    i = src.find("supabaseClient.auth.getSession().then")
    assert i != -1, "the async getSession bounce was removed"
    block = src[i : i + 400]
    assert "__ADMIN_STALE" in block, (
        "the async bounce ignores the stale flag — it will forward straight back to /admin (#3080)"
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
