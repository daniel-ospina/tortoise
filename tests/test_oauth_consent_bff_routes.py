"""The BFF half of the consent bridge — routes, and the wire format ACROSS both halves.

WHY THIS FILE EXISTS (#3524 §9 step 3)
--------------------------------------
Step 1 (the shared `jti` register) is proved in `tests/test_oauth_consent_jti_store.py`. This
file proves the OTHER end of the contract: the dashboard Pages Functions that MINT the
assertion, and — the point of the whole bridge — that what TypeScript mints is what Python
verifies. Two unit tests that agree with each other would not prove that; a token minted by
`consent-flow.ts::mintConsentToken` is fed to `tortoise.consent_token.verify_consent_token`
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

import pytest

from tests.test_session_bridge_fragment_retention import _require_node

REPO = Path(__file__).resolve().parent.parent
FUNCTIONS = REPO / "website/apps/dashboard/functions"
CONSENT_DIR = FUNCTIONS / "api/consent"

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
        if (user_id != null) existing.user_id = user_id;
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
    ? [] : [sessionRow()];
  const e = env({ SESSIONS: new FakeD1({ sessions, consent_attempts: [] }) });
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


def test_start_is_terminal_on_the_return_leg_when_still_unsigned() -> None:
    """A second hop can only bounce, so it stops — terminal 200, never another 302 (#3485)."""
    out = _run("start_terminal_after_retry")
    assert out["status"] == 200, "a signed-out return leg must not bounce again"
    assert out["body"]["action"] == "terminal"
    assert out["body"]["reason"] == "loop"


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
