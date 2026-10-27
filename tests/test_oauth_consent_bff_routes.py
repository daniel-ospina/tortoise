"""The BFF half of the consent bridge — routes, and the wire format ACROSS both halves.

WHY THIS FILE EXISTS (#3524 §9 step 3)
--------------------------------------
Step 1 (the shared `jti` register) is proved in `tests/test_oauth_consent_jti_store.py`. This
file proves the OTHER end of the contract: the dashboard Pages Functions that MINT the
assertion, and — the point of the whole bridge — that what TypeScript mints is what Python
verifies. Two unit tests that agree with each other would not prove that; a token minted by
`consent-bridge.ts::mintConsentToken` is fed to `tortoise.consent_token.verify_consent_token`
here, so a drift in the header/payload shape, the base64url encoding, the HMAC or the key
handling is a red test rather than a dead production flow.

WHAT IS ASSERTED ABOUT THE ROUTES
  * `POST /api/consent/token` refuses without `__Host-session` (401, never 503: the #3485
    split, one layer along);
  * it refuses a request whose authorize attempt this BFF never started (403) — the token is
    bound to a RECORDED flow, not to whatever the caller put in the body;
  * it refuses an `Origin` that is not the AS origin (403) — this route is credentialed and
    host-only, so it is the one place an exact-origin allowlist is load-bearing;
  * it answers 503 — never 401 — when the signing key is unprovisioned (§2.3: no default
    secret, because a default secret is a forgeable consent);
  * `GET /api/consent/start` records the attempt, then advances to the AS authorize URL
    rebuilt from validated params; a cold browser is bounced to `/auth` with this same flow as
    the return target carrying `consent_retry=1`; and a return leg that STILL has no session is
    terminal 200 rather than a second hop.

TOOLCHAIN — same harness contract as `tests/test_oauth_session_flow_properties.py`: Node with
built-in type stripping, a resolver hook for extensionless `./x` imports, and a D1 stand-in.
The fakes substitute the STORE and the PROVIDER only; the route modules under test are the
shipped ones.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_session_bridge_fragment_retention import _require_node

REPO = Path(__file__).resolve().parent.parent
FUNCTIONS = REPO / "website/apps/dashboard/functions"
CONSENT_DIR = FUNCTIONS / "api/consent"
DASHBOARD = FUNCTIONS.parent
SIGNUP_HTML = DASHBOARD / "public/signup.html"
AUTH_BOUNCE = DASHBOARD / "src/authBounce.js"

_TEST_SECRET = "test-consent-secret-0123456789abcdef"
_ISSUER = "https://app.premiselabs.co"
_AS_ORIGIN = "https://api.premiselabs.co"
_PARAMS = {
    "client_id": "client-abc",
    "redirect_uri": "https://client.example/cb",
    "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    "code_challenge_method": "S256",
    "state": "state-xyz",
    "scope": "mcp",
}

_HOOKS_MJS = """
export async function resolve(specifier, context, next) {
  if (specifier.startsWith("./") || specifier.startsWith("../")) {
    try { return await next(specifier, context); }
    catch { return await next(specifier + ".ts", context); }
  }
  return next(specifier, context);
}
"""

_DRIVER_MJS = r"""
import { register } from "node:module";
register("./hooks.mjs", import.meta.url);

const CONSENT = process.argv[2];
const scenario = process.argv[3];
const opts = JSON.parse(process.argv[4] || "{}");

const tokenRoute = await import(CONSENT + "/token.ts");
const startRoute = await import(CONSENT + "/start.ts");

const NOW = Date.now();

// A D1 stand-in: the tables these two routes touch, over in-memory rows. `meta.changes`
// counts what the WHERE matched, as D1 does (a CAS that matches nothing must report 0).
class FakeD1 {
  constructor(tables) { this.tables = tables || { sessions: [], consent_attempts: [] }; }
  exec() { return Promise.resolve(); }
  prepare(sql) {
    const db = this;
    return { bind: (...args) => ({
      first: () => Promise.resolve(db._first(sql, args)),
      run: () => Promise.resolve(db._run(sql, args)),
      all: () => Promise.resolve({ results: db._rows(sql, args) }),
    })};
  }
  _table(sql) {
    if (/FROM sessions/.test(sql)) return "sessions";
    if (/consent_attempts/.test(sql)) return "consent_attempts";
    return null;
  }
  _rows(sql, args) {
    const t = this._table(sql);
    if (!t) return [];
    const rows = this.tables[t] || (this.tables[t] = []);
    if (t === "sessions") return rows.filter((r) => r.handle === args[0] && r.revoked === 0);
    if (/WHERE state = \?1/.test(sql)) return rows.filter((r) => r.state === args[0]);
    return rows;
  }
  _first(sql, args) { const rows = this._rows(sql, args); return rows.length ? { ...rows[0] } : null; }
  _run(sql, args) {
    if (/INSERT INTO consent_attempts/.test(sql)) {
      const rows = this.tables.consent_attempts;
      const [state, client_id, redirect_uri, code_challenge, scope, resource, user_id, created_at, expires_at] = args;
      const existing = rows.find((r) => r.state === state);
      if (existing) {
        // ⛔ THIS DOUBLE DELEGATES THE OWNER RULE TO THE SQL IT IS GIVEN, it does not restate it.
        // The real statement writes `user_id=COALESCE(excluded.user_id, consent_attempts.user_id)`;
        // a double that hardcoded "only overwrite with non-null" would keep the suite green if that
        // COALESCE were deleted, and the nulling is exactly what lets a SECOND identity pass the
        // mint's `attempt.user_id !== row.user_id` check for a flow someone else began.
        if (/COALESCE\(\s*consent_attempts\.user_id\s*,\s*excluded\.user_id\s*\)/i.test(sql)) {
          // FIRST known owner wins: fills an ownerless row, never re-assigns a known one.
          if (existing.user_id == null) existing.user_id = user_id;
        } else if (/user_id\s*=\s*COALESCE/i.test(sql)) {
          // the other order is last-writer-wins — the double must model WHICH order, or the
          // difference is invisible to every test that goes through it.
          if (user_id != null) existing.user_id = user_id;
        } else {
          existing.user_id = user_id;
        }
        existing.expires_at = expires_at;
        return { meta: { changes: 1 } };
      }
      rows.push({ state, client_id, redirect_uri, code_challenge, scope, resource, user_id, created_at, expires_at });
      return { meta: { changes: 1 } };
    }
    if (/DELETE FROM consent_attempts/.test(sql)) {
      const [now, limit] = args;
      const rows = this.tables.consent_attempts;
      const dead = rows.filter((r) => r.expires_at < now).slice(0, limit);
      this.tables.consent_attempts = rows.filter((r) => !dead.includes(r));
      return { meta: { changes: dead.length } };
    }
    return { meta: { changes: 0 } };
  }
}

function sessionRow(over) {
  return Object.assign({
    handle: "handle-1", user_id: "user-1", refresh_token: "rt",
    access_token: "at", access_token_expires_at: NOW + 3_600_000,
    revoked: 0, created_at: NOW - 1000, expires_at: NOW + 400 * 86400_000,
  }, over || {});
}

function env(over) {
  return Object.assign({
    SESSIONS: new FakeD1(opts.tables ? JSON.parse(JSON.stringify(opts.tables)) : undefined),
    CONSENT_TOKEN_SECRET: opts.noSecret ? undefined : "test-consent-secret-0123456789abcdef",
    CONSENT_TOKEN_ISSUER: "https://app.premiselabs.co",
    CONSENT_AS_ORIGIN: "https://api.premiselabs.co",
    APP_ORIGIN: "https://app.premiselabs.co",
  }, over || {});
}

function post(path, body, headers) {
  return new Request("https://app.premiselabs.co" + path, {
    method: "POST",
    headers: Object.assign({ "Content-Type": "application/json", Origin: "https://api.premiselabs.co" }, headers || {}),
    body: JSON.stringify(body),
  });
}

const out = { scenario };

if (scenario === "mint_no_session" || scenario === "mint_no_attempt" || scenario === "mint_ok"
    || scenario === "mint_cross_origin" || scenario === "mint_no_secret"
    || scenario === "mint_foreign_attempt" || scenario === "mint_null_owner") {
  const seeded = ["mint_ok", "mint_cross_origin", "mint_no_secret", "mint_foreign_attempt", "mint_null_owner"];
  const e = env({
    noSecret: scenario === "mint_no_secret",
    SESSIONS: new FakeD1({
      sessions: scenario === "mint_no_session" ? [] : [sessionRow()],
      consent_attempts: seeded.includes(scenario)
        ? [{ state: "state-xyz", client_id: "client-abc", redirect_uri: "https://client.example/cb",
             code_challenge: "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", scope: "mcp",
             resource: null,
             // The cold-browser row has NO owner yet; the foreign row belongs to someone else.
             user_id: scenario === "mint_foreign_attempt" ? "user-2"
               : (scenario === "mint_null_owner" ? null : "user-1"),
             created_at: NOW, expires_at: NOW + 900_000 }]
        : [],
    }),
  });
  // An unprovisioned key is ABSENCE, not a falsy value: delete it rather than set "".
  if (scenario === "mint_no_secret") delete e.CONSENT_TOKEN_SECRET;
  const headers = scenario === "mint_no_session" ? {} : { Cookie: "__Host-session=handle-1" };
  if (scenario === "mint_cross_origin") headers.Origin = "https://evil.example";
  const res = await tokenRoute.onRequestPost({ request: post("/api/consent/token", { state: "state-xyz" }, headers), env: e });
  out.status = res.status;
  out.body = await res.json();
} else if (scenario.startsWith("start_")) {
  const sessions = scenario === "start_cold_browser" || scenario === "start_terminal_after_retry"
    ? [] : [sessionRow(opts.sessionUserId ? { user_id: opts.sessionUserId } : undefined)];
  const seeded = opts.seededAttempts || [];
  const e = env({ SESSIONS: new FakeD1({ sessions, consent_attempts: seeded }) });
  const source = opts.params || {
    client_id: "client-abc", redirect_uri: "https://client.example/cb",
    code_challenge: "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    code_challenge_method: "S256", state: "state-xyz", scope: "mcp",
  };
  const qs = new URLSearchParams(source);
  if (scenario === "start_terminal_after_retry") qs.set("consent_retry", "1");
  const headers = scenario === "start_cold_browser" || scenario === "start_terminal_after_retry"
    ? {} : { Cookie: "__Host-session=handle-1" };
  const request = new Request("https://app.premiselabs.co/api/consent/start?" + qs.toString(), { headers });
  const res = await startRoute.onRequestGet({ request, env: e });
  out.status = res.status;
  out.location = res.headers.get("Location");
  out.body = null;
  try { out.body = await res.json(); } catch { out.body = null; }
  out.attempts = e.SESSIONS.tables.consent_attempts;
  if (opts.wantValidated) {
    // The params `validate()` RETURNS (not just its verdict) — what the route records, binds into
    // the token and forwards to the AS. The AS matches `redirect_uri` byte-exactly, so the VALUE is
    // the property under test, not the accept/refuse bit.
    const v = startRoute.validate(new URLSearchParams(source));
    out.validated = typeof v === "string" ? null : v;
  }
} else if (scenario === "redirect_uri_table") {
  // The ROUTE's own `validate`, so this cannot pass if it ever stops delegating to the mirror.
  const base = {
    client_id: "client-abc",
    code_challenge: "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    code_challenge_method: "S256",
    state: "state-xyz",
  };
  out.verdicts = (opts.uris || []).map((uri) => {
    const params = new URLSearchParams(Object.assign({}, base, { redirect_uri: uri }));
    const result = startRoute.validate(params);
    return { uri, accepted: typeof result !== "string", reason: typeof result === "string" ? result : null };
  });
} else if (scenario === "native_schemes") {
  // The AS's private-use-scheme allowlist has a MIRROR here, and a one-sided extension is what
  // would re-enable the `cursor://` incident: the AS registers and authorizes the redirect, then
  // `/api/consent/start` answers 400 AFTER the visitor consented. So the two sets are compared.
  const bridge = await import(CONSENT + "/../../_shared/auth/consent-bridge.ts");
  out.schemes = [...bridge.NATIVE_REDIRECT_SCHEMES];
} else if (scenario === "allowlist_check") {
  // THE PAGE'S OWN ALLOWLIST, read out of the page — not a copy of it. `signup.html` is what
  // `/auth` serves, and its return-to block DROPS a `next` that its literals do not admit, so
  // asserting the `Location` string this BFF builds only proves the bounce is well-formed.
  const { readFileSync } = await import("node:fs");
  const html = readFileSync(opts.html, "utf8");
  const grab = (name) => {
    const m = new RegExp("var " + name + " = \\[([^\\]]*)\\]").exec(html);
    if (!m) throw new Error("the page no longer declares " + name);
    return m[1].split(",").map((s) => s.trim()).filter(Boolean)
      .map((s) => s.replace(/^[\"']|[\"']$/g, ""));
  };
  const routes = grab("APP_RETURN_ROUTES");
  const subtrees = grab("APP_SUBTREE_ROUTES");
  const path = opts.pathname;
  // The page's matching rule, reproduced verbatim from signup.html (exact, trailing slash, or
  // subtree for a subtree route).
  let pageAllowed = false;
  for (const route of routes) {
    if (path === route || path === route + "/") { pageAllowed = true; break; }
    if (subtrees.includes(route) && path.startsWith(route + "/")) { pageAllowed = true; break; }
  }
  const bounce = await import(opts.authBounce);
  out.routes = routes;
  out.subtrees = subtrees;
  out.pageAllowed = pageAllowed;
  out.moduleAllowed = bounce.isAppReturnPath(path);
} else {
  throw new Error("unknown scenario " + scenario);
}

console.log("RESULT " + JSON.stringify(out));
"""


def _run(scenario: str, **opts) -> dict:
    _require_node()
    assert (CONSENT_DIR / "token.ts").is_file(), "the mint route is gone"
    assert (CONSENT_DIR / "start.ts").is_file(), "the start route is gone"
    with tempfile.TemporaryDirectory(prefix="3524-bff-") as td:
        tdp = Path(td)
        (tdp / "hooks.mjs").write_text(_HOOKS_MJS)
        (tdp / "driver.mjs").write_text(_DRIVER_MJS)
        proc = subprocess.run(
            ["node", str(tdp / "driver.mjs"), str(CONSENT_DIR), scenario, json.dumps(opts)],
            capture_output=True, text=True, timeout=120, cwd=str(tdp),
        )
    if proc.returncode != 0:
        raise AssertionError(
            f"harness scenario {scenario!r} failed (exit {proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    found = re.findall(r"^RESULT (\{.*\})$", proc.stdout, re.MULTILINE)
    assert found, f"no RESULT line for {scenario!r}:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(found[-1])


# ── the mint route's refusals ───────────────────────────────────────────────

def test_the_mint_route_refuses_without_the_host_session_cookie() -> None:
    """401 `no_session` — and NOT 503. The split is property 3's whole value.

    A route that answered 503 here would be indistinguishable from a store outage, and the
    page's remedy ("go and sign in") would be attempted against a broken backend.
    """
    out = _run("mint_no_session")
    assert out["status"] == 401
    assert out["body"]["error"] == "no_session"


def test_the_mint_route_refuses_a_flow_this_bff_never_started() -> None:
    """The bind comes from the RECORDED attempt, so an unstarted flow cannot be minted for.

    Without this, the mint would be a general "sign any bind you like with the visitor's
    identity" oracle — which is not what an identity assertion may be.
    """
    out = _run("mint_no_attempt")
    assert out["status"] == 403
    assert out["body"]["error"] == "no_authorize_attempt"


def test_the_mint_route_refuses_a_flow_a_DIFFERENT_identity_started() -> None:
    """`consent_attempts.user_id` is load-bearing, not decorative.

    Without this refusal, a visitor signed in as B could mint an assertion into a flow A began
    — i.e. assert B's identity into A's approval. Deleting the branch used to leave the suite
    green, so this pins it.
    """
    out = _run("mint_foreign_attempt")
    assert out["status"] == 403, out
    assert out["body"]["error"] == "no_authorize_attempt"


def test_the_mint_route_allows_an_attempt_row_with_NO_owner_yet() -> None:
    """The cold-browser row is deliberately ownerless — pinning it stops a "fix" that refuses.

    A start runs BEFORE sign-in (that is the whole reason the hop exists), so its row cannot
    carry an owner. Refusing a NULL owner would break every cold browser; the null arm is
    therefore an intended pass, and the foreign-identity test above is what bounds it.
    """
    out = _run("mint_null_owner")
    assert out["status"] == 200, out
    assert out["body"]["token"].count(".") == 2


def test_the_mint_route_refuses_an_origin_that_is_not_the_as() -> None:
    """This is the one route the BFF opens cross-origin, so its allowlist is load-bearing."""
    out = _run("mint_cross_origin")
    assert out["status"] == 403
    assert out["body"]["error"] == "forbidden_origin"


def test_the_mint_route_fails_closed_when_the_signing_key_is_absent() -> None:
    """§2.3: no default secret. Absent ⇒ 503 with a NAMED reason, never a minted token."""
    out = _run("mint_no_secret")
    assert out["status"] == 503, "an unprovisioned key must not mint anything"
    assert out["body"]["reason"] == "not_configured"


# ── the bridge itself: what TS mints, Python verifies ──────────────────────

def test_a_token_minted_by_the_bff_is_accepted_by_the_AS(monkeypatch) -> None:
    """THE test of this file, and the one that proves a BRIDGE rather than two agreeing mocks.

    The mint happens in TypeScript (the shipped `consent-flow.ts`), the verification in Python
    (the shipped `tortoise/consent_token.py`). Any drift between them — the key order, the
    base64url encoding, the `aud`, the bind set, the HMAC input — reds this, where two
    same-language unit tests would both stay green and production would answer `invalid_token`
    with no other symptom.
    """
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    monkeypatch.setenv("CONSENT_TOKEN_ISSUER", _ISSUER)

    out = _run("mint_ok")
    assert out["status"] == 200, out
    token = out["body"]["token"]
    assert token.count(".") == 2, f"not a compact JWS: {token[:40]}"
    # `exp` is echoed so the page can show a stale-approval notice; it must match §2.2's TTL.
    assert isinstance(out["body"]["exp"], int)

    from tortoise.consent_token import verify_consent_token

    assertion = verify_consent_token(token, presented={
        "client_id": _PARAMS["client_id"],
        "redirect_uri": _PARAMS["redirect_uri"],
        "code_challenge": _PARAMS["code_challenge"],
        "state": _PARAMS["state"],
    })
    # Identity is the SESSION's user, never anything the caller sent.
    assert assertion["sub"] == "user-1"
    # ...and the token is bound to the flow the BFF recorded.
    assert assertion["bind"] == {
        "client_id": _PARAMS["client_id"],
        "redirect_uri": _PARAMS["redirect_uri"],
        "code_challenge": _PARAMS["code_challenge"],
        "state": _PARAMS["state"],
    }
    assert assertion["email"] is None, "email is display-only and must not be invented"


def test_the_minted_token_carries_a_fresh_single_use_nonce() -> None:
    """Two mints must never share a `jti`; reusing one would make the second redemption a replay.

    Cheap to check and expensive to get wrong: a constant/derived `jti` (e.g. hashed from the
    bind) would make every consent after the first answer "already redeemed".
    """
    first = _run("mint_ok")["body"]["token"]
    second = _run("mint_ok")["body"]["token"]
    import base64

    def payload(tok: str) -> dict:
        seg = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))

    assert payload(first)["jti"] != payload(second)["jti"]


# ── the start route: record, advance, bounce, terminate ────────────────────

def test_start_advances_to_the_as_authorize_url_rebuilt_from_validated_params() -> None:
    """The advance target is the AS route, never this route.

    Advancing to the caller re-enters it and 302s forever (`ERR_TOO_MANY_REDIRECTS`) — the
    third P1 that shipped from PR #8438. This pins the target's origin and path.
    """
    out = _run("start_advance")
    assert out["status"] == 302
    location = out["location"]
    assert location.startswith(_AS_ORIGIN + "/oauth/authorize?"), location
    # The PKCE challenge and state survive the hop — losing either breaks the flow silently.
    assert "code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM" in location
    assert "state=state-xyz" in location
    assert "code_challenge_method=S256" in location
    # ...and the attempt was RECORDED, with the owner taken from the session.
    assert len(out["attempts"]) == 1
    assert out["attempts"][0]["user_id"] == "user-1"


def test_start_bounces_a_cold_browser_to_sign_in_and_returns_to_this_flow() -> None:
    """The case #3524 was reopened for: no cookies at all.

    The bounce must be same-origin and must carry this flow back as the return target, with
    `consent_retry=1` as the loop guard's carrier — so the return leg can tell "first attempt"
    from "we already tried and failed".
    """
    out = _run("start_cold_browser")
    assert out["status"] == 302
    location = out["location"]
    assert location.startswith("https://app.premiselabs.co/auth?"), location
    assert "consent_retry%3D1" in location or "consent_retry=1" in location
    assert "state%3Dstate-xyz" in location or "state=state-xyz" in location


def test_the_sign_in_bounce_lands_on_a_path_the_AUTH_PAGE_actually_keeps() -> None:
    """The bounce is only a bounce if the page it hands the visitor to KEEPS the return target.

    THIS IS THE TEST THAT WAS MISSING. The previous one asserts the `Location` string this route
    builds — which proves the bounce is well-formed and nothing about whether it survives. It did
    not: `/auth` serves `public/signup.html`, whose return-to block is allowlist-only and DROPS any
    `next` it does not admit, so a cold browser signed in and landed on the app root with the
    consent flow abandoned and `consent_retry=1` never coming back (the terminal-loop guard could
    therefore never fire). "Verify the artifact, not the send", one layer along.

    So this reads the ALLOWLIST OUT OF THE PAGE (the `var APP_RETURN_ROUTES`/`APP_SUBTREE_ROUTES`
    literals in `signup.html`, matched by the page's own rule) and asserts the exact pathname this
    route emits is admitted — plus that the SPA mirror agrees. It reds if the route's return target
    changes, or if the page's list loses it.
    """
    out = _run("start_cold_browser")
    assert out["status"] == 302, out
    next_value = parse_qs(urlparse(out["location"]).query)["next"][0]
    return_path = urlparse(next_value)
    assert return_path.path.startswith("/api/consent/start"), next_value

    verdict = _run(
        "allowlist_check",
        html=str(SIGNUP_HTML),
        authBounce=str(AUTH_BOUNCE),
        pathname=return_path.path,
    )
    assert verdict["pageAllowed"], (
        f"`/auth` (public/signup.html) DROPS the return target {return_path.path!r}; its allowlist "
        f"is {verdict['routes']} (subtrees {verdict['subtrees']}). The visitor signs in and lands "
        "on the app root with the consent flow abandoned."
    )
    assert verdict["moduleAllowed"], (
        "the SPA mirror (src/authBounce.js) disagrees with the page it mirrors"
    )


def test_start_is_terminal_on_the_return_leg_when_still_unsigned() -> None:
    """A second hop can only bounce, so it stops — terminal 200, never another 302 (#3485)."""
    out = _run("start_terminal_after_retry")
    assert out["status"] == 200, "a signed-out return leg must not bounce again"
    assert out["body"]["action"] == "terminal"
    assert out["body"]["reason"] == "loop"


_CROSS_HALF_URIS = [
    # https — the ordinary case
    "https://client.example/cb",
    # RFC 8252 §7.3 loopback, in every form the AS accepts
    "http://localhost:6277/cb",
    "http://127.0.0.1:1234/cb",
    "http://127.0.0.5:9999/cb",          # anywhere in 127.0.0.0/8, not just .0.0.1
    "http://[::1]:6277/cb",              # WHATWG keeps the brackets: `=== "::1"` is dead code
    "http://[0:0:0:0:0:0:0:1]:6277/cb",  # expanded IPv6 loopback
    # the beta's only native-scheme harness (oauth.py::_NATIVE_REDIRECT_SCHEMES)
    "cursor://anysphere.cursor-mcp/oauth/callback",
    # refused by both — http on a non-loopback host
    "http://client.example/cb",
    # refused by both — schemes that are neither https, loopback http, nor allowlisted native
    "ftp://client.example/cb",
    "javascript:alert(1)",
    "data:text/html,x",
    "https://",
    # refused by both — a fragment is never a valid redirect component (RFC 6749 §3.1.2),
    # including the BARE trailing `#` whose fragment parses as empty
    "https://client.example/cb#frag",
    "https://client.example/cb#",
    # refused by both — bytes we refuse to reason about (a real parser differential)
    "https://client.example/cb\\x",
    # refused by both — unspecified, not loopback
    "http://0.0.0.0:1/cb",
    # ── the IPv4-MAPPED IPv6 forms: the AS ACCEPTS these (`ipaddress.is_loopback` is True), so a
    # mirror that only knew `::1` refused a redirect the client had already registered.
    "http://[::ffff:127.0.0.1]:1234/cb",
    "http://[::ffff:7f00:1]:1234/cb",
    "http://[::ffff:127.1.2.3]/cb",
    # ⛔ EVERY zero-padded / compressed spelling of `::1` must be accepted, because `ipaddress`
    # normalises before it decides. Enumeration is unbounded, so the code expands the address —
    # these rows are what makes that observable (13 spellings were refused while the table stayed
    # green, which is how the enumerated version survived a review round).
    "http://[::01]:6277/cb",
    "http://[::0001]:6277/cb",
    "http://[0::1]:6277/cb",
    "http://[::0:0:1]:6277/cb",
    "http://[0000::1]:6277/cb",
    "http://[0:0:0:0:0:0:0:01]:6277/cb",
    "http://[0:0:0:0:0:ffff:127.0.0.1]:6277/cb",
    # `fill = 1` — the maximally compressed spelling, which is the boundary of the `::` expansion
    "http://[::0:0:0:0:0:0:1]:6277/cb",
    # the mapped rule's REFUSAL half: without these, `return isV6Loopback || mapped` keeps the suite
    # green while accepting `http://[::ffff:126.0.0.1]/cb` — an http redirect to a NON-loopback host
    # that the AS refuses.
    "http://[::ffff:126.0.0.1]/cb",
    "http://[::ffff:10.0.0.1]/cb",
    # leading/trailing SPACES are stripped by BOTH parsers, so these must be ACCEPTED (refusing
    # them was a real divergence: the AS registers and accepts the uri, and a browser navigates it).
    " https://client.example/cb",
    "http://127.0.0.1:1234/cb ",
    # the USERINFO/HOST boundary is the LAST `@` (`urlsplit` and WHATWG agree). A single-`@` row
    # cannot pin that: both choices give the same verdict, so `indexOf("@")` survived the suite
    # while refusing a registrable loopback redirect.
    "http://a@b@127.0.0.1:6277/cb",
    "http://localhost@evil.example/cb",
    # stray brackets in the authority: `urlsplit` RAISES, so the AS refuses; WHATWG parses it, so an
    # unguarded mirror ACCEPTED it and 302d to an AS that answers 400.
    "http://u[ser@127.0.0.1/cb",
    "https://u[ser:pw@client.example/cb",
    # a native scheme must NAME a destination, or the consent page navigates an app handler to
    # nothing. These are refused by both; without them the whole branch could be deleted verbosely.
    "cursor:",
    "cursor://",
    "cursor:?q",
    # the IPv4 guards: leading-zero octets are ambiguous to `ipaddress` (it REFUSES them) while
    # WHATWG reads them as decimal, and an octet above 255 makes WHATWG refuse the parse entirely.
    "http://127.000.000.001/cb",
    "http://127.0.0.256/cb",
    # ── spellings where WHATWG and `urlsplit` see DIFFERENT HOSTS. For **http** that changes the
    # verdict: `new URL` rewrites these into 127.0.0.1, but `urlsplit` reports them verbatim so
    # `_is_loopback` says no and the AS REFUSES — a BFF that accepted one would advance a flow the
    # AS then refuses.
    "http://127.1/cb",
    "http://2130706433/cb",
    "http://127.0.0.1./cb",
    "http://0.0.0.0x7f000001/cb",
    "https:client.example/cb",
    "http:localhost:6277/cb",
    # ⛔ ...but for **https** it does NOT: the AS's https branch only requires a truthy hostname and
    # never consults `_is_loopback`, so it accepts every normalised spelling. Refusing them here
    # was a real defect (round 4): a client that registered one could never start the bridge, and
    # the message blamed its `redirect_uri`. Same family, opposite verdict — so both are pinned.
    "https://127.1/cb",
    "https://2130706433/cb",
    "https://127.0.0.1./cb",
    "https://client%2Eexample/cb",
    "https://münchen.example/cb",
    # both accept (case, userinfo and an explicit default port are read the same by both)
    "HTTP://LOCALHOST:6277/cb",
    "https://user:pw@client.example/cb",
]

# ⛔ DELIBERATELY ONE-DIRECTIONAL, and named rather than tolerated. `urlsplit` never validates the
# port, so the AS accepts a URI no browser can navigate (WHATWG refuses to parse it, which is why
# the BFF answers "must be an absolute URI"). Being STRICTER here costs a client a 400 at consent
# start; being laxer would hand the browser a target it cannot reach. Asserted in the direction that
# matters (BFF refuses, AS accepts) so the divergence cannot silently grow into a class.
_BFF_STRICTER_THAN_AS = [
    "https://client.example:99999/cb",
    "https://client.example:abc/cb",
    "https://127.0.0.256:6277/cb",
]


def test_the_redirect_uri_rule_agrees_with_the_AS() -> None:
    """ONE table, BOTH halves. A BFF that refuses what the AS accepts kills a consented flow.

    The BFF's first predicate was an approximation of `tortoise/oauth.py::_valid_redirect_uri`,
    and every way it differed was silent in the same direction: it refused `cursor://` (so the
    beta's only native-scheme harness could not use the bridge AT ALL), it was dead code on IPv6
    loopback (`new URL("http://[::1]:9/cb").hostname` is `"[::1]"`, brackets included), and it
    dropped the rest of `127.0.0.0/8`. The visitor consents, then the flow dies at the BFF with a
    message blaming their redirect URI.

    Two copies cannot be prevented — Python cannot import TypeScript — so the pair is pinned HERE:
    hand the same list to both and require the same verdict. Adding a scheme to one side alone now
    reds this test instead of producing that silent refusal.
    """
    from tortoise.oauth import _valid_redirect_uri

    out = _run("redirect_uri_table", uris=_CROSS_HALF_URIS)
    disagreements = []
    for row in out["verdicts"]:
        as_verdict = _valid_redirect_uri(row["uri"])
        if bool(row["accepted"]) != as_verdict:
            disagreements.append((row["uri"], row["accepted"], as_verdict, row["reason"]))
    assert not disagreements, (
        "the BFF's redirect_uri rule disagrees with the AS's on: "
        + ", ".join(
            f"{uri!r} (BFF accepted={bff}, AS accepted={as_}){'' if bff else f' — {why}'}"
            for uri, bff, as_, why in disagreements
        )
    )
    # NON-VACUITY: the table must contain both verdicts, or it proves only one direction.
    accepted = {row["accepted"] for row in out["verdicts"]}
    assert accepted == {True, False}, f"the table is one-sided: {accepted}"


def test_the_BFF_is_stricter_than_the_AS_only_where_a_parse_differs_hopelessly() -> None:
    """The named, one-directional divergence — pinned so it cannot grow into a class.

    A row exists here only if a browser could not navigate the URI either, and the check below
    proves that CLAIM rather than restating it: the last assertion requires WHATWG (the parser every
    one of those browsers runs) to refuse the uri outright. Stricter-then costs a client a 400 at
    start; laxer would hand the browser a target it cannot parse.
    """
    from tortoise.oauth import _valid_redirect_uri

    out = _run("redirect_uri_table", uris=_BFF_STRICTER_THAN_AS)
    for row in out["verdicts"]:
        assert not row["accepted"], f"expected the BFF to refuse {row['uri']!r}: {row}"
        assert _valid_redirect_uri(row["uri"]), (
            f"{row['uri']!r} is no longer accepted by the AS either — remove it from "
            "_BFF_STRICTER_THAN_AS, it is no longer a divergence"
        )
        parsed = subprocess.run(
            ["node", "-e", "try{new URL(process.argv[1]);console.log('PARSED')}"
             "catch(e){console.log('REFUSED')}", row["uri"]],
            capture_output=True, text=True, timeout=60,
        )
        assert parsed.stdout.strip() == "REFUSED", (
            f"{row['uri']!r} IS parseable by WHATWG, so a browser can navigate it — refusing it "
            "is a real divergence, not the tolerated kind"
        )


def test_the_return_leg_never_nulls_a_recorded_owner() -> None:
    """A re-entry WITHOUT a session must not erase the owner an earlier leg recorded.

    The start route records the attempt on BOTH legs, and on the return leg it re-records the SAME
    row keyed by `state`. The write is `user_id=COALESCE(excluded.user_id, consent_attempts.user_id)`
    precisely so a cold-browser re-entry (or a stale tab) cannot null a known owner: with a plain
    assignment, the next person to arrive signed in as someone else passes the mint's
    `attempt.user_id !== row.user_id` check — which is the check
    `test_the_mint_route_refuses_a_flow_a_DIFFERENT_identity_started` exists to prove — and mints an
    assertion into a flow another identity began. Nothing pinned that COALESCE before: the D1
    double restated the rule in JavaScript, so deleting it from the SQL left every test green.
    """
    owner = "user-1"
    seeded = [{
        "state": "state-xyz", "client_id": "client-abc",
        "redirect_uri": "https://client.example/cb",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "scope": "mcp", "resource": None, "user_id": owner,
        "created_at": 0, "expires_at": 0,
    }]
    out = _run("start_terminal_after_retry", seededAttempts=seeded)
    assert out["status"] == 200, out
    rows = [r for r in out["attempts"] if r["state"] == "state-xyz"]
    assert len(rows) == 1, rows
    assert rows[0]["user_id"] == owner, (
        f"the return leg nulled the owner ({rows[0]['user_id']!r}) — a different identity could "
        "then mint into this flow, which is what the mint's foreign-owner refusal prevents"
    )


def test_the_native_scheme_allowlist_is_the_same_set_on_both_halves() -> None:
    """A one-sided extension of `_NATIVE_REDIRECT_SCHEMES` must red HERE, not in production.

    The table below cannot catch it: it only covers the schemes already in it, so adding `vscode`
    to one half alone left every test green — and the incident that shape re-enables is `cursor://`:
    the AS registers and authorizes the native-scheme redirect, then `/api/consent/start` answers
    400 "must use https" AFTER the visitor has consented. `oauth.py`'s own note invites the one-line
    extension ("Extending it is a reviewed one-line change with a test"), so the test has to exist.
    """
    from tortoise.oauth import _NATIVE_REDIRECT_SCHEMES

    out = _run("native_schemes")
    ts_schemes = set(out["schemes"])
    as_schemes = set(_NATIVE_REDIRECT_SCHEMES)
    assert ts_schemes == as_schemes, (
        "the private-use scheme allowlist drifted between the halves: "
        f"BFF-only={sorted(ts_schemes - as_schemes)}, AS-only={sorted(as_schemes - ts_schemes)}. "
        "An AS-only scheme means /api/consent/start refuses a redirect the AS just authorized."
    )
    # ...and each one must actually be ACCEPTED by the BFF, so the set equality cannot hold over a
    # rule that refuses natives outright.
    uris = [f"{scheme}://client.example/cb" for scheme in sorted(as_schemes)]
    for row in _run("redirect_uri_table", uris=uris)["verdicts"]:
        assert row["accepted"], f"the BFF refuses the AS's own native scheme: {row}"


def test_the_redirect_uri_is_forwarded_BYTE_FOR_BYTE_to_the_AS() -> None:
    """The AS matches the presented `redirect_uri` against the registered one BYTE-EXACTLY.

    `_valid_redirect_uri` ACCEPTS a registered value carrying whitespace (` https://client.example/cb`,
    a trailing NBSP), and `_redirect_uri_matches`' exact-string arm then compares the uri the route
    forwards against that registered string. So trimming before forwarding turns an accepted
    registration into `400 redirect_uri is not registered for this client` at the AS — the flow dies
    AFTER the visitor consented, with a message blaming their registration, and no predicate-level
    test can see it because both predicates say "fine".

    This asserts the VALUE, not the verdict: what `validate()` returns must be the raw input.
    """
    from tortoise.oauth import _redirect_uri_matches

    for raw in (" https://client.example/cb", "https://client.example/cb ",
                "https://client.example/cb\u00a0"):
        params = dict(_PARAMS, redirect_uri=raw)
        out = _run("start_validate", params=params, wantValidated=True)
        # The route ACCEPTS it (the AS accepts it at registration)...
        assert out.get("validated") is not None, (raw, out)
        forwarded = out["validated"]["redirect_uri"]
        assert forwarded == raw, (
            f"the route forwards {forwarded!r} for a registered {raw!r} — the AS compares "
            "byte-exactly, so every redemption of this consent answers "
            "`redirect_uri is not registered for this client`")
        assert _redirect_uri_matches(raw, forwarded), (
            f"the forwarded value does not match its own registration: {raw!r} vs {forwarded!r}")


def test_a_second_identity_cannot_take_over_a_flow_by_re_entering_it() -> None:
    """THE bypass the previous test could not see: ``COALESCE(excluded, existing)`` is last-writer-wins.

    That order preserves a known owner against `null` (which is all the signed-out return-leg test
    exercised) and does NOT preserve it against a DIFFERENT identity. So one GET from B —
    `/api/consent/start?<A's params>&state=<A's state>` — rewrote the row to B, and B's mint then
    passed the foreign-owner refusal: an assertion asserting B, bound to A's redirect_uri /
    code_challenge / state, redeemed into a code delivered to A's client carrying B's identity.
    A's own mint started answering 403 on A's own flow.

    The owner is now the FIRST one recorded. This test drives that with a SESSION for a second
    identity — not a signed-out leg, which both orders satisfy.
    """
    seeded = [{
        "state": "state-xyz", "client_id": "client-abc",
        "redirect_uri": "https://client.example/cb",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "scope": "mcp", "resource": None, "user_id": "user-1",
        "created_at": 0, "expires_at": 0,
    }]
    out = _run("start_signed_in", seededAttempts=seeded, sessionUserId="user-2")
    rows = [r for r in out["attempts"] if r["state"] == "state-xyz"]
    assert len(rows) == 1, rows
    assert rows[0]["user_id"] == "user-1", (
        f"a second identity (user-2) TOOK OVER a flow user-1 began — the row now says "
        f"{rows[0]['user_id']!r}, so user-2 can mint an assertion into user-1's consent "
        "(identity injection: the foreign-owner refusal at token.ts becomes unreachable by one GET)")


def test_start_forwards_the_opaque_state_RAW_so_the_clients_csrf_check_survives() -> None:
    """`state` is opaque and is echoed to the client, which compares it BYTE-FOR-BYTE.

    `validate()` used to `trim()` it, so a client whose `state` carries padding — a legitimate,
    RFC 6749-opaque value — received a different `state` back after the visitor consented and
    (correctly) abandoned the callback as a CSRF mismatch. Nothing in the predicate layer shows it:
    the route accepts, the AS accepts, and the flow dies after consent. Same class as `redirect_uri`
    two lines above, which is forwarded raw for the same reason.
    """
    for raw in ("  state-xyz  ", "\tstate-xyz", "state-xyz\n", "\u00a0state-xyz"):
        out = _run("start_validate", params=dict(_PARAMS, state=raw), wantValidated=True)
        assert out.get("validated") is not None, (raw, out)
        assert out["validated"]["state"] == raw, (
            f"the route recorded/forwarded state {out['validated']['state']!r} for a client's "
            f"{raw!r}: the CLIENT compares state byte-for-byte after consent, so this flow is "
            "abandoned as a CSRF mismatch after the visitor already approved")


@pytest.mark.parametrize("broken,needle", [
    ({"code_challenge": "short"}, "code_challenge"),
    ({"state": ""}, "state"),
    ({"redirect_uri": "javascript:alert(1)"}, "redirect_uri"),
    ({"redirect_uri": "https://client.example/cb#frag"}, "fragment"),
])
def test_start_validates_the_params_the_way_the_AS_will(broken: dict, needle: str) -> None:
    """A request this route advances must not be one the AS then refuses.

    The `code_challenge` length floor is RFC 7636 §4.2's, and the loopback exception to https
    is RFC 8252 §7.3's — a `javascript:` redirect is refused outright rather than normalised.
    """
    params = dict(_PARAMS, **broken)
    out = _run("start_validate", params=params)
    assert out["status"] == 400, out
    assert out["body"]["error"] == "invalid_request"
    assert needle in out["body"]["message"], out["body"]["message"]
