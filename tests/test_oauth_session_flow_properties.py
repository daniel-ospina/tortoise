"""Behavioural guards for the four session-flow properties, server-side (#3524 task 3).

WHY THIS FILE EXISTS
--------------------
#3524's task 3 says the four properties of the ONE working reference pattern
(`tortoise/oauth.py::_CONSENT_HTML`) "collapse under HttpOnly" and must be
**re-implemented server-side with equivalent guarantees, and test each**. The
pattern's own four properties are stated by `SCOPE.md` §4 W3:

  1. **single-flight** — one in-flight auth attempt per flow, the others await it;
  2. **refresh-once** — a refresh must not be issued twice concurrently;
  3. **never-sign-out** — a transient failure must never clear the session (#3485);
  4. **auto-advance** — progress the flow without requiring a manual click.

This file EXECUTES the server-side implementations and asserts the guarantee each
one makes. That distinction matters: `tests/test_oauth_consent_bridge.py` pins
WHERE the properties live (static anchors); this file pins WHAT they do. A static
anchor goes red when a token is renamed and stays green when the behaviour behind
it is quietly inverted — which is exactly the failure mode #3524 exists to stop,
because the property was inherited, not re-derived.

Properties 1–3 are the BFF's (`website/apps/dashboard/functions/_shared/auth/`),
landed by #3522/#4054; this file proves them. Property 4's decision is new here
(`consent-flow.ts`) and is NOT yet routed — see that module's header and the PR
body for the recorded gap.

TOOLCHAIN
---------
`token.ts` is TypeScript and its relative imports are extensionless, so it cannot
be imported by Node as-is. The harness writes a resolver hook to a temp dir that
retries `./x` as `./x.ts` and imports the REAL modules with Node's built-in type
stripping — no bundler, no copy of the logic under test. Node is REQUIRED and a
missing node FAILS by name (never skips), reusing `_require_node` from the sibling
harness (#3786): a skipped harness is green, exit 0, zero coverage.

The store and the provider are fakes; `token.ts`, `session.ts` and `supabase.ts`
are the real modules, so the classification under test (`isRefreshTokenDead`, the
`retryable` split, the CAS) is the shipped one.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

# The toolchain contract is owned by the sibling harness — one guard, not a
# second variant. It FAILS (never skips) when node is absent.
from tests.test_session_bridge_fragment_retention import _require_node

REPO = Path(__file__).resolve().parent.parent
AUTH_DIR = REPO / "website/apps/dashboard/functions/_shared/auth"
TOKEN_TS = AUTH_DIR / "token.ts"
CONSENT_FLOW_TS = AUTH_DIR / "consent-flow.ts"

# Resolve `./x` to `./x.ts`: `token.ts` imports `./session` / `./supabase`
# extensionlessly (the Pages runtime resolves it; Node ESM does not), so the
# hook is what lets the real modules load with Node's type stripping.
_HOOKS_MJS = """
export async function resolve(specifier, context, next) {
  if (specifier.startsWith("./") || specifier.startsWith("../")) {
    try { return await next(specifier, context); }
    catch { return await next(specifier + ".ts", context); }
  }
  return next(specifier, context);
}
"""

# The scenario runner. It imports the REAL modules by absolute path and
# substitutes only what a unit test must: the D1 store and the provider fetch.
_DRIVER_MJS = """
import { register } from "node:module";
register("./hooks.mjs", import.meta.url);

const AUTH = process.argv[2];
const scenario = process.argv[3];
const opts = JSON.parse(process.argv[4] || "{}");

const token = await import(AUTH + "/token.ts");
const consent = await import(AUTH + "/consent-flow.ts");

const out = { scenario, fetches: [], updates: [], changeCounts: [] };

// A D1 stand-in with the two write shapes token.ts issues, and CAS semantics
// faithful to D1 (`meta.changes` counts the rows the WHERE actually matched).
class FakeD1 {
  constructor(row) { this.row = row; }
  exec() { return Promise.resolve(); }
  prepare(sql) {
    const db = this;
    return {
      bind: (...args) => ({
        first: () => db._first(sql, args),
        run: () => db._run(sql, args),
      }),
    };
  }
  _first(sql, args) {
    if (/FROM sessions WHERE handle = \\?1 AND revoked = 0/.test(sql)) {
      const r = this.row;
      return Promise.resolve(r && r.handle === args[0] && r.revoked === 0 ? { ...r } : null);
    }
    if (/SELECT access_token, access_token_expires_at/.test(sql)) {
      return Promise.resolve({ ...this.row });
    }
    throw new Error("unexpected SELECT: " + sql);
  }
  _run(sql, args) {
    if (/UPDATE sessions SET access_token = NULL/.test(sql)) {
      const [handle, now, cutoff] = args;
      const r = this.row;
      // D1 applies the WHERE the SQL actually carries: the cooldown is the
      // `token_rejected_at < ?3` predicate, so the fake must honour its PRESENCE,
      // not re-implement the intent. A fake that always bounds would pass even
      // when the guard is deleted from the SQL.
      const bounded = /token_rejected_at < \\?3/.test(sql);
      let changes = 0;
      if (
        r && r.handle === handle &&
        (!bounded || r.token_rejected_at == null || r.token_rejected_at < cutoff)
      ) {
        r.access_token = null;
        r.access_token_expires_at = null;
        r.token_rejected_at = now;
        changes = 1;
      }
      out.updates.push({ sql: "invalidate", changes });
      return Promise.resolve({ meta: { changes } });
    }
    if (/UPDATE sessions SET refresh_token = \\?2/.test(sql)) {
      const [handle, newRefresh, access, exp, oldRefresh] = args;
      const r = this.row;
      // The single-flight guard is the `refresh_token = ?5` predicate. Same
      // rule: the fake honours the SQL it is handed, so deleting the clause
      // from token.ts makes the second write land.
      const cas = /AND refresh_token = \\?5/.test(sql);
      let changes = 0;
      if (
        r && r.handle === handle && r.revoked === 0 &&
        (!cas || r.refresh_token === oldRefresh)
      ) {
        r.refresh_token = newRefresh;
        r.access_token = access;
        r.access_token_expires_at = exp;
        changes = 1;
      }
      out.updates.push({ sql: "cas", newRefresh, oldRefresh, changes });
      return Promise.resolve({ meta: { changes } });
    }
    throw new Error("unexpected UPDATE: " + sql);
  }
}

function installFetch(responses) {
  let i = 0;
  globalThis.fetch = async (url, init) => {
    out.fetches.push({ url: String(url), body: init && init.body ? JSON.parse(init.body) : null });
    const r = responses[Math.min(i, responses.length - 1)];
    i += 1;
    if (!r) throw new Error("no fake provider response was supplied");
    return { ok: r.status < 400, status: r.status, text: async () => JSON.stringify(r.body) };
  };
}

const env = { SUPABASE_URL: "https://proj.supabase.co", SUPABASE_ANON_KEY: "anon-key" };

function row(over) {
  const now = Date.now();
  return Object.assign({
    handle: "h1",
    refresh_token: "r0",
    access_token: "a0",
    access_token_expires_at: now + 1_000,
    expires_at: now + 3_600_000,
    revoked: 0,
    token_rejected_at: null,
  }, over);
}

function provider(access, refresh, expiresIn) {
  return { status: 200, body: { access_token: access, refresh_token: refresh, expires_in: expiresIn, user: { id: "u" } } };
}

switch (scenario) {
  case "single_flight": {
    // A stale cached token so BOTH concurrent callers refresh from the same
    // generation (the stampede the CAS exists for).
    const db = new FakeD1(row({ access_token_expires_at: Date.now() + 1_000 }));
    installFetch([provider("a1", "r1", 3600), provider("a2", "r2", 3600)]);
    env.SESSIONS = db;
    const results = await Promise.all([
      token.getAccessTokenForSession(env, "h1"),
      token.getAccessTokenForSession(env, "h1"),
    ]);
    out.results = results;
    out.storedRefresh = db.row.refresh_token;
    out.fetchCount = out.fetches.length;
    out.changeCounts = out.updates.map((u) => u.changes);
    break;
  }
  case "refresh_once_cooldown": {
    const db = new FakeD1(row({ access_token: "a0", access_token_expires_at: 0 }));
    env.SESSIONS = db;
    await token.invalidateCachedToken(env, "h1");
    const first = db.row.token_rejected_at;
    await token.invalidateCachedToken(env, "h1");
    const second = db.row.token_rejected_at;
    out.results = { first, second };
    // The cooldown is the WHERE clause: the second call matches zero rows, so
    // the stamp it would write is never written and the timestamp is unchanged.
    out.changeCounts = out.updates.filter((u) => u.sql === "invalidate").map((u) => u.changes);
    break;
  }
  case "refresh_once_skew": {
    const now = Date.now();
    const db = new FakeD1(row({ access_token_expires_at: now + opts.expiresInMs }));
    installFetch([provider("a1", "r1", 3600)]);
    env.SESSIONS = db;
    out.results = await token.getAccessTokenForSession(env, "h1");
    out.fetchCount = out.fetches.length;
    break;
  }
  case "never_sign_out": {
    const overrides = {};
    if (opts.sessionTtlMs !== undefined) overrides.expires_at = Date.now() + opts.sessionTtlMs;
    const db = new FakeD1(row(overrides));
    installFetch(opts.responses);
    env.SESSIONS = db;
    if (opts.dropEnv) delete env.SUPABASE_URL;
    out.results = await token.getAccessTokenForSession(env, "h1");
    break;
  }
  case "auto_advance": {
    out.results = opts.cases.map((c) => consent.decideConsentAdvance(c));
    break;
  }
  default:
    throw new Error("unknown scenario: " + scenario);
}

console.log("RESULT " + JSON.stringify(out));
"""


def _run(scenario: str, **opts) -> dict:
    _require_node()
    assert TOKEN_TS.is_file(), f"the server-side token module is gone: {TOKEN_TS}"
    assert CONSENT_FLOW_TS.is_file(), f"the consent-flow module is gone: {CONSENT_FLOW_TS}"
    with tempfile.TemporaryDirectory(prefix="3524-props-") as td:
        tdp = Path(td)
        (tdp / "hooks.mjs").write_text(_HOOKS_MJS)
        (tdp / "driver.mjs").write_text(_DRIVER_MJS)
        proc = subprocess.run(
            ["node", str(tdp / "driver.mjs"), str(AUTH_DIR), scenario, json.dumps(opts)],
            capture_output=True, text=True, timeout=120, cwd=str(tdp),
        )
    if proc.returncode != 0:
        raise AssertionError(
            f"harness scenario {scenario!r} failed (exit {proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    m = re.findall(r"^RESULT (\{.*\})$", proc.stdout, re.MULTILINE)
    assert m, f"no RESULT line for {scenario!r}:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(m[-1])


# ── property 1 — single-flight ───────────────────────────────────────────────

def test_property_1_single_flight_the_loser_never_clobbers_the_winner() -> None:
    """One in-flight attempt wins the rotation; the others must not destroy it.

    The reference page holds `previewInFlight` and returns the RUNNING flow, so a
    second caller can never start a second attempt at the same generation. The
    server equivalent is the D1 CAS on the OLD refresh token: both callers may
    still reach the provider (GoTrue's ~10s reuse tolerance makes that safe), but
    only the FIRST may write, and the loser must re-read the winner's token rather
    than overwrite it with its own now-dead one.

    Removing the property is removing `AND refresh_token = ?5` from the UPDATE:
    the second write then succeeds and the live session is destroyed. That is what
    the `storedRefresh`/`accessToken` assertions below detect — verified by
    mutation (see the PR body).
    """
    out = _run("single_flight")
    # Both callers get a USABLE token — the loser is served, not failed.
    assert out["results"] == [
        {"ok": True, "accessToken": "a1"},
        {"ok": True, "accessToken": "a1"},
    ], (
        "the rotation race did not resolve to the winner's token: "
        f"{out['results']!r} (the loser must re-read, not fail and not clobber)"
    )
    # The WINNER's refresh token is what survives in the store.
    assert out["storedRefresh"] == "r1", (
        f"the store holds {out['storedRefresh']!r} — the loser overwrote the winner, "
        "which is the rotating-refresh-token stampede the CAS exists to prevent"
    )
    # Two attempts, one write: the CAS matched exactly the winner.
    assert out["changeCounts"] == [1, 0], (
        f"CAS outcomes were {out['changeCounts']!r} — expected one write and one no-op"
    )


# ── property 2 — refresh-once ────────────────────────────────────────────────

def test_property_2_refresh_once_is_bounded_by_skew_and_cooldown() -> None:
    """A refresh must not be issued twice concurrently, nor unboundedly on error.

    Two halves, both server-side:

      * the 60 s `REFRESH_SKEW_MS` — a cached token is used until it is within a
        minute of expiry, so the hot path does not refresh at all;
      * the `invalidateCachedToken` cooldown — a persistent upstream rejection
        may force at most ONE refresh per 30 s window, so a 401 storm cannot
        become unbounded rotating-refresh-token traffic against GoTrue.

    Removing either half changes an observable here: the skew inside its band, and
    the cooldown by the second write matching a row it must not match. Verified by
    mutation (see the PR body).
    """
    cooldown = _run("refresh_once_cooldown")
    assert cooldown["changeCounts"] == [1, 0], (
        f"the second invalidation inside the cooldown wrote a row "
        f"({cooldown['changeCounts']!r}) — a 401 storm would refresh unboundedly"
    )
    assert cooldown["results"]["second"] == cooldown["results"]["first"], (
        "the cooldown stamp moved on the second call — the WHERE clause is not bounding it"
    )

    # Comfortably inside the cached window: no provider call at all.
    wide = _run("refresh_once_skew", expiresInMs=300_000)
    assert wide["fetchCount"] == 0, (
        "a token 5 minutes from expiry was refreshed — the skew no longer holds, so "
        "the hot path refreshes on every request"
    )
    # INSIDE the skew band (30 s < 60 s): refresh, so the token is never used mid-flight.
    narrow = _run("refresh_once_skew", expiresInMs=30_000)
    assert narrow["fetchCount"] == 1, (
        "a token 30 s from expiry was NOT refreshed — the skew is gone, so a request "
        "can be authenticated with a token that expires in flight"
    )


# ── property 3 — never-sign-out ──────────────────────────────────────────────

_NEVER_SIGN_OUT_CASES = [
    # (what the provider answered, expected reason, why)
    ([{"status": 500, "body": {"error": "boom"}}], "unavailable", "5xx is a server fault"),
    ([{"status": 429, "body": {}}], "unavailable", "429 is a server fault"),
    ([{"status": 403, "body": {"message": "forbidden"}}], "unavailable", "edge/WAF 403 is infrastructure"),
    ([{"status": 404, "body": {"message": "not found"}}], "unavailable", "404 is a misconfigured URL"),
    ([{"status": 400, "body": {"error": "invalid_credentials"}}], "unavailable", "not the refresh family"),
    ([{"status": 200, "body": {}}], "unavailable", "a malformed 2xx is a fault, not a token"),
    ([{"status": 400, "body": {"error": "invalid_grant"}}], "no_session", "the session IS dead"),
    ([{"status": 400, "body": {"error_code": "refresh_token_already_used"}}], "no_session", "reuse kill"),
]


def test_property_3_never_sign_out_a_transient_failure_is_not_a_dead_session() -> None:
    """A transient failure must NEVER clear the session — the #3485 class.

    The reference page's `showExpiredSignin()` carries "NEVER call the auth
    logout API here": a refused refresh re-renders the sign-in view and leaves the
    shared cookie alone. The server equivalent is the `no_session` / `unavailable`
    split at the ONE place that decides: only a positive `invalid_grant` signal is
    a dead session; everything else — including an unreadable or unexpected body,
    and a missing configuration — is a fault, and a fault is a 503, never a 401.

    Removing the property is collapsing the two reasons (or classifying by status
    alone), which the table below detects: each infrastructure answer would come
    back as `no_session` and sign the user out during an outage. Verified by
    mutation (see the PR body).
    """
    for responses, expected, why in _NEVER_SIGN_OUT_CASES:
        out = _run("never_sign_out", responses=responses)
        assert out["results"]["reason"] == expected, (
            f"{why}: provider answered {responses[0]['status']} and the BFF classified it "
            f"{out['results']['reason']!r}, expected {expected!r} — "
            "a fault read as a session verdict is the #3485 sign-out"
        )

    # A missing binding is a MISCONFIGURATION, not "this user has no session".
    out = _run("never_sign_out", responses=[{"status": 200, "body": {}}], dropEnv=True)
    assert out["results"]["reason"] == "unavailable", (
        "a missing SUPABASE_URL was classified as a dead session — the server would "
        "answer 401 and clear the cookie while the session is fine"
    )
    # ... and a genuinely expired session is still a dead one (the negative control:
    # without it, "always unavailable" would pass every case above).
    out = _run("never_sign_out", responses=[], sessionTtlMs=-1_000)
    assert out["results"]["reason"] == "no_session", (
        "an expired session was not classified as dead — the never-sign-out rule "
        "must not swallow the real case"
    )


# ── property 4 — auto-advance ────────────────────────────────────────────────

_FLOW = "https://app.premiselabs.co/api/consent/start?client_id=c1&state=s1"
_AUTH = "https://app.premiselabs.co/auth?next=/api/consent/start%3Fclient_id%3Dc1"


def test_property_4_auto_advance_without_a_manual_click() -> None:
    """The flow advances to the consent decision with no click and no reload.

    The reference page runs the flow on load AND on every `INITIAL_SESSION` /
    `SIGNED_IN` event, so a session that lands during a sign-in hop is picked up
    without the user doing anything. Under HttpOnly no event ever fires, so the
    equivalent is deterministic: a usable session ALWAYS redirects onward (the
    return to the consent URL *is* the advance), an absent session makes exactly
    ONE hop to sign-in carrying this flow as its return target, a store fault is a
    terminal 503 (never a redirect — property 3's edge), and a second round trip
    with still no session stops instead of bouncing forever (#3485/#3503).

    Removing the property is a decision table that no longer advances — verified
    by mutation (see the PR body).
    """
    cases = [
        {"session": "usable", "flow_url": _FLOW, "auth_url": _AUTH, "attempted": False},
        {"session": "usable", "flow_url": _FLOW, "auth_url": _AUTH, "attempted": True},
        {"session": "none", "flow_url": _FLOW, "auth_url": _AUTH, "attempted": False},
        {"session": "none", "flow_url": _FLOW, "auth_url": _AUTH, "attempted": True},
        {"session": "unavailable", "flow_url": _FLOW, "auth_url": _AUTH, "attempted": False},
    ]
    out = _run("auto_advance", cases=cases)
    assert out["results"] == [
        # A usable session advances — on the FIRST pass and on the RETURN from
        # sign-in alike. The return is the advance.
        {"action": "advance", "status": 302, "to": _FLOW},
        {"action": "advance", "status": 302, "to": _FLOW},
        # No session: exactly one hop, carrying the flow as the return target.
        {"action": "sign_in", "status": 302, "to": _AUTH},
        # Returned with no session: terminal, never a second bounce.
        {"action": "terminal", "status": 200, "reason": "loop"},
        # A store fault is terminal and is never a redirect.
        {"action": "unavailable", "status": 503},
    ], f"the advance decision table changed: {out['results']!r}"


# ── the module contract itself ───────────────────────────────────────────────

def test_the_consent_flow_module_is_the_server_side_property_four_decision() -> None:
    """The module must stay a pure decision, not grow a request/response body.

    Property 4 is the ONE property with no server-side equivalent before this
    change; the module exists to hold that decision where it can be tested without
    a store, a network or a browser. If it acquires I/O (a `fetch`, an `async`
    body, a D1 call) it stops being the guarantee and becomes a second, untested
    route — so the shape is pinned, not just the behaviour. Comments are stripped
    first: the module's own docstring QUOTES the reference JS (which is `async`),
    and prose is not I/O.
    """
    code = re.sub(r"/\*.*?\*/", "", CONSENT_FLOW_TS.read_text(), flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    assert "export function decideConsentAdvance(" in code
    for forbidden in ("fetch(", "D1Database", "async "):
        assert forbidden not in code, (
            f"consent-flow.ts now contains {forbidden!r} in CODE — property 4's decision "
            "is no longer pure, so it can no longer be tested as the guarantee itself"
        )
