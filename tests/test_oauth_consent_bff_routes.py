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
import urllib.parse
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

import { DatabaseSync } from "node:sqlite";

const tokenRoute = await import(CONSENT + "/token.ts");
const startRoute = await import(CONSENT + "/start.ts");
const attemptKeyHash = (await import(CONSENT + "/../../_shared/auth/consent-bridge.ts")).attemptKeyHash;

const NOW = Date.now();

// A D1 stand-in: the tables these two routes touch, over in-memory rows. `meta.changes`
// counts what the WHERE matched, as D1 does (a CAS that matches nothing must report 0).
class FakeD1 {
  constructor(tables, opts) {
    this.tables = tables || { sessions: [], consent_attempts: [] };
    this._sqlite = null;
    this._seeded = false;
    // A REAL store fault: without this the 503 arm is unreachable in tests, so nothing would red if
    // the route were ever changed to continue — i.e. if the security gate started failing OPEN.
    this._throws = Boolean(opts && opts.throws);
    // ⛔ A DDL-ONLY fault, and it is a DIFFERENT fault from `throws`. `throws` fires on ANY
    // statement naming the table — including the `CREATE TABLE IF NOT EXISTS` — so under it
    // `ensureColumn`'s `ALTER TABLE` is never reached and the PROPAGATION half is untested: the
    // suite stayed green with `ensureColumn`'s catch widened to `catch { return; }` AND with the
    // call site replaced by a local swallow-all. SQLite reports "duplicate column name" as the
    // same primary result code (SQLITE_ERROR) as a genuine fault, so the message is the ONLY
    // discriminator — which is exactly the property `ensureColumn` claims and nothing pinned.
    //   "fault"     → a genuine DDL failure (the SQLITE_FULL/READONLY shape): must PROPAGATE.
    //   "duplicate" → the already-evolved column: must be SWALLOWED and the route continue.
    this._ddlFault = (opts && opts.ddlFault) || null;
  }
  _maybeThrow(sql) {
    if (this._ddlFault && /^\s*ALTER TABLE/i.test(sql)) {
      throw new Error(this._ddlFault === "duplicate"
        ? "D1_ERROR: duplicate column name: attempt_key_hash"
        : "D1_ERROR: database or disk is full");
    }
    if (this._throws && /consent_attempts/.test(sql)) {
      throw new Error("D1_ERROR: control plane unreachable");
    }
  }
  // ⛔ `exec()` RUNS the statement. It used to be a no-op, which meant the shipped
  // `ensureConsentAttemptTable` DDL — the ONLY definition of this table, there is no migration for
  // it — never executed in any test, while the double carried its own hand-written copy of the
  // schema. Measured: renaming a column in the shipped CREATE left all route tests green and
  // production answering 503 on every `/api/consent/start`. The table is now created by the code
  // under test, including the `ALTER … ADD COLUMN` path for a database an earlier build bootstrapped.
  exec(sql) {
    this._maybeThrow(sql);
    this._db().exec(sql);
    this._maybeSeed();
    return Promise.resolve();
  }
  // ⛔ `consent_attempts` runs on REAL SQLite (node:sqlite), not a hand-written interpreter.
  // The interpreter this replaced restated the SQL's owner rule in JavaScript, and when the two
  // drifted the suite stayed green while the shipped statement was last-writer-wins — a whole
  // round of review found the takeover and the double could not have (it encoded the bypass).
  // Letting the engine decide means the CASE / COALESCE / RETURNING semantics tested are the ones
  // D1 will run; `this.tables.consent_attempts` is then only a MIRROR for the assertions.
  _db() {
    if (this._sqlite) return this._sqlite;
    const db = new DatabaseSync(":memory:");
    this._sqlite = db;
    return db;
  }
  _hasTable() {
    const row = this._db().prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='consent_attempts'").get();
    return row !== undefined;
  }
  // Seeds are imported AFTER the shipped DDL created the table, and only once — a test that seeds a
  // row must still see IT, not a row the route wrote first.
  _maybeSeed() {
    if (this._seeded || !this._hasTable()) return;
    this._seeded = true;
    const db = this._db();
    const cols = db.prepare("PRAGMA table_info(consent_attempts)").all().map((c) => c.name);
    const ins = db.prepare(
      "INSERT INTO consent_attempts (state,client_id,redirect_uri,code_challenge,scope,resource," +
      "user_id,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?)");
    for (const r of this.tables.consent_attempts || []) {
      // A seeded row may deliberately predate the column (`attempt_key_hash: null` means "written
      // by an older build"), and the column may not exist yet at this point.
      ins.run(r.state, r.client_id, r.redirect_uri, r.code_challenge, r.scope ?? null,
              r.resource ?? null, r.user_id ?? null, r.created_at ?? 0, r.expires_at ?? 0);
      if (cols.includes("attempt_key_hash") && r.attempt_key_hash != null) {
        db.prepare("UPDATE consent_attempts SET attempt_key_hash = ? WHERE state = ?")
          .run(r.attempt_key_hash, r.state);
      }
    }
  }
  /** Create the table the way an EARLIER build did — no `attempt_key_hash` — for the ALTER path. */
  _legacySchema() {
    this._db().exec("CREATE TABLE consent_attempts (state TEXT PRIMARY KEY, client_id TEXT NOT NULL, "
      + "redirect_uri TEXT NOT NULL, code_challenge TEXT NOT NULL, scope TEXT, resource TEXT, "
      + "user_id TEXT, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);");
    this._maybeSeed();
  }
  _mirror() {
    this.tables.consent_attempts = this._db().prepare("SELECT * FROM consent_attempts").all()
      .map((r) => ({ ...r }));
  }
  prepare(sql) {
    const db = this;
    return { bind: (...args) => ({
      first: () => Promise.resolve(db._first(sql, args)),
      run: () => Promise.resolve(db._run(sql, args)),
      all: () => Promise.resolve({ results: db._rows(sql, args) }),
    })};
  }
  _isConsent(sql) { return /consent_attempts/.test(sql) && !/FROM sessions/.test(sql); }
  _consent(sql, args) {
    this._maybeThrow(sql);
    this._maybeSeed();
    const stmt = this._db().prepare(sql);
    const out = /^\s*(SELECT|INSERT|DELETE|UPDATE)/i.test(sql) && /RETURNING|^\s*SELECT/i.test(sql)
      ? { rows: stmt.all(...args) }
      : { run: stmt.run(...args) };
    this._mirror();
    return out;
  }
  _rows(sql, args) {
    if (this._isConsent(sql)) {
      const out = this._consent(sql, args);
      return out.rows ?? [];
    }
    const rows = this.tables.sessions || (this.tables.sessions = []);
    if (/FROM sessions/.test(sql)) return rows.filter((r) => r.handle === args[0] && r.revoked === 0);
    return [];
  }
  _first(sql, args) { const rows = this._rows(sql, args); return rows.length ? { ...rows[0] } : null; }
  _run(sql, args) {
    if (this._isConsent(sql)) return this._consent(sql, args).run ?? { meta: { changes: 0 } };
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

// The PROVIDER stand-in. The mint's ONLY network call is the accessor's refresh POST, so counting
// calls here is the whole difference between "asked the provider" and "answered from D1" — the two
// are otherwise indistinguishable (both answer 200 to the caller). `raw` carries a non-JSON body
// verbatim, so the classifier's "a WAF page is not a sign-out" branch stays reachable.
function installFetch(responses) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), body: init && init.body ? JSON.parse(init.body) : null });
    const r = responses[Math.min(calls.length - 1, responses.length - 1)]
      || { status: 500, raw: "" };
    return { ok: r.status < 400, status: r.status,
             text: async () => (r.raw !== undefined ? r.raw : JSON.stringify(r.body)) };
  };
  return calls;
}

function post(path, body, headers) {
  return new Request("https://app.premiselabs.co" + path, {
    method: "POST",
    headers: Object.assign({ "Content-Type": "application/json", Origin: "https://api.premiselabs.co" }, headers || {}),
    body: JSON.stringify(body),
  });
}

// The RAW body TEXT, for the shapes `JSON.stringify` cannot express faithfully: a body is a byte
// string, and `null` must reach the route as the four characters `null`, not as an omitted body.
function postRaw(path, text, headers) {
  return new Request("https://app.premiselabs.co" + path, {
    method: "POST",
    headers: Object.assign({ "Content-Type": "application/json", Origin: "https://api.premiselabs.co" }, headers || {}),
    body: text,
  });
}

const out = { scenario };

if (scenario === "mint_no_session" || scenario === "mint_no_attempt" || scenario === "mint_ok"
    || scenario === "mint_cross_origin" || scenario === "mint_no_secret"
    || scenario === "mint_foreign_attempt" || scenario === "mint_null_owner"
    || scenario === "mint_twice" || scenario === "mint_non_object_body") {
  const seeded = ["mint_ok", "mint_cross_origin", "mint_no_secret", "mint_foreign_attempt",
                  "mint_null_owner", "mint_twice"];
  // #10208: the mint now requires the ELECTRONIC key the creating browser holds. Every mint
  // scenario sends the right one unless it is explicitly testing a wrong/absent key.
  const MINT_KEY = opts.attemptKey === undefined ? "test-attempt-key" : opts.attemptKey;
  const seededHash = opts.seededKeyHash === undefined
    ? await attemptKeyHash("test-attempt-key") : opts.seededKeyHash;
  const e = env({
    noSecret: scenario === "mint_no_secret",
    SESSIONS: new FakeD1({
      sessions: scenario === "mint_no_session" ? []
        : [sessionRow(opts.sessionUserId ? { user_id: opts.sessionUserId } : undefined)],
      // `??`, not `||`: a supplied row set must REPLACE the built-in one (`||` binds tighter than
      // `?:`, so the first version kept the literal seed and dropped the caller's rows — and with
      // them the hash, which made the cross-route key test fail for the wrong reason).
      consent_attempts: opts.seededAttempts ?? (seeded.includes(scenario)
        ? [{ state: "state-xyz", client_id: "client-abc", redirect_uri: "https://client.example/cb",
             code_challenge: "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", scope: "mcp",
             resource: null,
             // The cold-browser row has NO owner yet; the foreign row belongs to someone else.
             user_id: scenario === "mint_foreign_attempt" ? "user-2"
               : (scenario === "mint_null_owner" ? null : "user-1"),
             created_at: NOW, expires_at: NOW + 900_000,
             attempt_key_hash: seededHash }]
        : []),
    }, { throws: Boolean(opts.storeThrows), ddlFault: opts.ddlFault ?? null }),
  });
  // An unprovisioned key is ABSENCE, not a falsy value: delete it rather than set "".
  if (scenario === "mint_no_secret") delete e.CONSENT_TOKEN_SECRET;
  // The `iss` knob's own defaults, so a cross-half test can vary ONE side at a time. `dropIssuer`
  // deletes the key (absence, not ""), and `APP_ORIGIN` is movable independently of it — the whole
  // point of the default-agreement test is that the two must NOT be derivable from each other.
  if (opts.dropIssuer) delete e.CONSENT_TOKEN_ISSUER;
  if (opts.issuer !== undefined) e.CONSENT_TOKEN_ISSUER = opts.issuer;
  if (opts.appOrigin !== undefined) e.APP_ORIGIN = opts.appOrigin;
  if (opts.asOrigin !== undefined) e.CONSENT_AS_ORIGIN = opts.asOrigin;
  const headers = scenario === "mint_no_session" ? {} : { Cookie: "__Host-session=handle-1" };
  if (scenario === "mint_cross_origin") headers.Origin = "https://evil.example";
  const mintBody = opts.omitKey ? { state: "state-xyz" } : { state: "state-xyz", attempt_key: MINT_KEY };
  if (scenario === "mint_non_object_body") {
    // `request.json()` RESOLVES with any JSON value (`null`, an array, a number…), so "the parse
    // succeeded" is not "there is an object to read". Each arm posts the raw TEXT, because
    // `JSON.stringify` would turn a request for the text `null` into a present-or-absent body.
    // A throw is RECORDED rather than allowed to escape: on the pre-fix route the `null` arm threw
    // a TypeError out of the mint handler, and an escaping throw would kill the harness process (an
    // exit-code failure) instead of asserting the status and body a caller actually receives.
    out.cases = [];
    for (const [label, text] of Object.entries(opts.bodies || {})) {
      const one = { label, text };
      try {
        const res = await tokenRoute.onRequest(
          { request: postRaw("/api/consent/token", text, headers), env: e });
        one.status = res.status;
        one.body = await res.json();
      } catch (err) {
        one.threw = String((err && err.name) || err) + ": " + String((err && err.message) || "");
      }
      out.cases.push(one);
    }
    out.status = out.cases.length ? (out.cases[0].status ?? null) : null;
    out.body = out.cases.length ? (out.cases[0].body ?? null) : null;
  } else if (scenario === "mint_twice") {
    // TWO mints of ONE attempt in ONE process: separate `_run`s would each build a fresh in-memory
    // database, so asserting on them proves nothing about re-minting the SAME row.
    const first = await tokenRoute.onRequest(
      { request: post("/api/consent/token", mintBody, headers), env: e });
    out.first = { status: first.status, body: await first.json() };
    const second = await tokenRoute.onRequest(
      { request: post("/api/consent/token", mintBody, headers), env: e });
    out.second = { status: second.status, body: await second.json() };
    out.rowsAfter = e.SESSIONS.tables.consent_attempts;
    out.status = out.second.status;
    out.body = out.second.body;
  } else {
    const res = await tokenRoute.onRequest(
      { request: post("/api/consent/token", mintBody, headers), env: e });
    out.status = res.status;
    out.body = await res.json();
  }
} else if (scenario.startsWith("start_")) {
  const sessions = scenario === "start_cold_browser" || scenario === "start_terminal_after_retry"
    ? [] : [sessionRow(opts.sessionUserId ? { user_id: opts.sessionUserId } : undefined)];
  const seeded = opts.seededAttempts || [];
  const store = new FakeD1({ sessions, consent_attempts: seeded },
                          { throws: Boolean(opts.storeThrows), ddlFault: opts.ddlFault ?? null });
  if (opts.wantLegacySchema) store._legacySchema();
  const e = env({ SESSIONS: store });
  // The AS-origin knob, movable on the start leg too (the mint leg has its own override above).
  // `dropAsOrigin` DELETES the key rather than blanking it, so the default can be exercised.
  if (opts.asOrigin !== undefined) e.CONSENT_AS_ORIGIN = opts.asOrigin;
  if (opts.dropAsOrigin) delete e.CONSENT_AS_ORIGIN;
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
} else if (scenario === "mint_credential_resolution") {
  // The mint's credential resolution, in the two directions that a single sentence can drift in.
  // The corrected comment (review round at `94906c6d0`) claims a BOUND, not a per-mint check: inside
  // the 60 s `REFRESH_SKEW_MS` window the accessor answers from D1 with ZERO provider calls (so a
  // revocation is NOT seen per mint), and once the cache lapses the mint reaches the provider exactly
  // once, so a revoked grant is a 401 `no_session` one refresh later. Asserting the status alone
  // proves neither half, because the cached and refreshed paths answer the SAME 200.
  out.cases = [];
  for (const [label, spec] of Object.entries(opts.cases || {})) {
    const calls = installFetch(spec.responses || []);
    const e = env({
      SESSIONS: new FakeD1({
        sessions: [sessionRow({
          access_token_expires_at: NOW + (spec.expiresInMs === undefined ? 3_600_000 : spec.expiresInMs),
        })],
        consent_attempts: [{ state: "state-xyz", client_id: "client-abc",
          redirect_uri: "https://client.example/cb",
          code_challenge: "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", scope: "mcp",
          resource: null, user_id: "user-1", created_at: NOW, expires_at: NOW + 900_000,
          attempt_key_hash: await attemptKeyHash("test-attempt-key") }],
      }),
      // Without these the accessor answers `unavailable` before it ever reaches `fetch`, which would
      // make the "zero calls" arm pass for the WRONG reason.
      SUPABASE_URL: "https://proj.supabase.co",
      SUPABASE_ANON_KEY: "anon-key",
    });
    const one = { label };
    try {
      const res = await tokenRoute.onRequest({
        request: post("/api/consent/token", { state: "state-xyz", attempt_key: "test-attempt-key" },
                      { Cookie: "__Host-session=handle-1" }),
        env: e });
      one.status = res.status;
      one.body = await res.json();
    } catch (err) {
      one.threw = String((err && err.name) || err) + ": " + String((err && err.message) || "");
    }
    one.fetchCount = calls.length;
    out.cases.push(one);
  }
  out.status = out.cases.length ? out.cases[0].status : null;
  out.body = out.cases.length ? out.cases[0].body : null;
} else if (scenario === "mint_transport") {
  // #10291 — the three arms of `token.ts` that the mint scenarios above never execute: the CORS
  // PREFLIGHT (the mechanism that makes the credentialed cross-origin call possible at all), the
  // media-type gate (what stops a cross-site form post from reaching the mint), and the 405 for
  // every other method. None of them is dead code — deleting one leaves the route either
  // unreachable from the consent page or open to a simple form post — and until this scenario
  // existed, deleting all three left the whole suite green.
  //
  // ⛔ EVERY arm below drives `onRequest`, because `onRequest` is the module's ONLY Pages handler
  // export. That is not a stylistic choice: Cloudflare calls a verb handler IN PREFERENCE to
  // `onRequest`, and calls `onRequest` only when no verb handler is exported at all
  // (developers.cloudflare.com/pages/functions/api-reference). A test that reached a verb export
  // directly would supply the routing decision the platform makes, so it could assert a response
  // the deployed runtime never produces — the vacuous shape this scenario was written to remove.
  // `tokenRoute.onRequest({...})` IS what the platform invokes, for all three methods.
  out.handler_exports = Object.keys(tokenRoute).filter((k) => /^onRequest/.test(k)).sort();
  const e = env({ SESSIONS: new FakeD1({ sessions: [sessionRow()] }) });
  const AS = "https://api.premiselabs.co";
  const URL_ = "https://app.premiselabs.co/api/consent/token";
  out.cases = {};

  // 1. The preflight the consent page's credentialed fetch depends on.
  {
    const res = await tokenRoute.onRequest({
      request: new Request(URL_, { method: "OPTIONS",
        headers: { Origin: AS, "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "content-type" } }),
      env: e });
    out.cases.preflight_allowed = {
      status: res.status,
      allowOrigin: res.headers.get("Access-Control-Allow-Origin"),
      allowCredentials: res.headers.get("Access-Control-Allow-Credentials"),
      allowMethods: res.headers.get("Access-Control-Allow-Methods"),
      allowHeaders: res.headers.get("Access-Control-Allow-Headers"),
      vary: res.headers.get("Vary"),
      body: await res.text(),
    };
  }

  // 2. …and the same preflight from the two SUFFIX look-alikes must be refused with NO CORS
  //    header, or the browser completes the credentialed call to an attacker origin.
  out.cases.preflight_refused = [];
  for (const bad of [AS + ".evil.example", "https://api.premiselabs.co.attacker.tld"]) {
    const res = await tokenRoute.onRequest({
      request: new Request(URL_, { method: "OPTIONS",
        headers: { Origin: bad, "Access-Control-Request-Method": "POST" } }),
      env: e });
    out.cases.preflight_refused.push({
      origin: bad, status: res.status,
      allowOrigin: res.headers.get("Access-Control-Allow-Origin"),
    });
  }

  // 3. A simple cross-site POST can set `text/plain` WITHOUT a preflight, so the media-type gate
  //    is the half that actually stops it. No cookie is sent here on purpose: the refusal must be
  //    the MEDIA TYPE, not `no_session` — otherwise this test would pass on a route that had lost
  //    the gate entirely (the status would just be 401 for a different reason).
  out.cases.wrong_media_type = [];
  for (const ct of ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]) {
    const res = await tokenRoute.onRequest({
      request: post("/api/consent/token", { state: "state-xyz", attempt_key: "k" },
                  { "Content-Type": ct }),
      env: e });
    out.cases.wrong_media_type.push({ mediaType: ct, status: res.status, body: await res.json() });
  }

  // 4. Anything that is not POST/OPTIONS. `onRequest` must answer it ITSELF (405) rather than
  //    hand it to `next()` — `next()` here would go to the ASSET SERVER, whose 404 carries no
  //    `Access-Control-Allow-Origin`, i.e. the unreadable refusal this arm exists to prevent.
  //    The harness's `next()` answers 418 on purpose and records whether it was reached at all.
  out.cases.wrong_method = [];
  for (const method of ["GET", "PUT", "DELETE"]) {
    let nextCalled = false;
    const res = await tokenRoute.onRequest({
      request: new Request(URL_, { method, headers: { Origin: AS } }),
      env: e,
      next: async () => { nextCalled = true; return new Response("NEXT WAS CALLED", { status: 418 }); },
    });
    out.cases.wrong_method.push({ method, status: res.status, body: await res.json(),
                                  nextCalled,
                                  allowOrigin: res.headers.get("Access-Control-Allow-Origin") });
  }

  // 4b. The same arm with NO `Origin` at all. This route is credentialed and mints a
  //     bearer-equivalent assertion, so "no Origin" must not be a way in (#3524 §10) — and the
  //     refusal must carry no `Allow-Origin`, exactly like a foreign origin's.
  out.cases.wrong_method_no_origin = [];
  for (const method of ["GET", "PUT", "DELETE"]) {
    const res = await tokenRoute.onRequest({
      request: new Request(URL_, { method }), env: e,
      next: async () => new Response("NEXT WAS CALLED", { status: 418 }) });
    out.cases.wrong_method_no_origin.push({
      method, status: res.status, body: await res.json(),
      allowOrigin: res.headers.get("Access-Control-Allow-Origin") });
  }

  out.status = out.cases.preflight_allowed.status;
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
    out = json.loads(found[-1])
    # The worker's `console.error` is a production SIGNAL, not noise: a propagated DDL failure is
    # NOT a transient blip, so a log line is the only thing an operator can ever act on.
    out["stderr"] = proc.stderr
    return out


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


def test_the_mint_scenario_actually_runs_as_the_identity_it_is_given() -> None:
    """The refusal above is only meaningful if the caller really IS somebody else.

    The mint scenarios ignored `sessionUserId` and always ran as `user-1`, so "B is refused" was
    measured with B replaced by the legitimate owner — an assertion that passes for the wrong
    reason (and would hide a threading regression). The SAME seeded row, owned by `user-2`, mints
    when the caller IS `user-2`: that is the positive half that proves the identity is threaded.
    """
    as_owner = _run("mint_foreign_attempt", sessionUserId="user-2")
    assert as_owner["status"] == 200, (
        "the owner of the flow was refused its own mint — the scenario is not running as the "
        f"identity it was given: {as_owner}")
    assert _run("mint_foreign_attempt", sessionUserId="user-1")["status"] == 403


def test_the_CORS_preflight_is_the_one_that_makes_the_credentialed_mint_reachable() -> None:
    """#10291 — the preflight was executed by no test, and it is the reachability half.

    The consent page mints from the AS origin with `credentials: "include"`. That call only happens
    if THIS route answers the preflight with the exact AS origin (never `*`), credentials allowed, and
    `POST` in the method list — so deleting or narrowing `preflight` makes the mint
    unreachable from the page while every other test in this file stays green. The arm drives the
    route's one exported handler (`onRequest`), which is what Pages invokes for an OPTIONS request.
    The two suffix
    look-alikes are pinned in the same arm because the refusal must carry NO
    `Access-Control-Allow-Origin`: a 403 with the header set still completes the browser call.
    """
    out = _run("mint_transport")
    allowed = out["cases"]["preflight_allowed"]
    assert allowed["status"] == 204, allowed
    assert allowed["allowOrigin"] == _AS_ORIGIN, (
        f"the preflight answered {allowed['allowOrigin']!r} — a wildcard or a misconfigured origin "
        "either breaks the credentialed mint (browsers refuse `*` with credentials) or opens it"
    )
    assert allowed["allowCredentials"] == "true", allowed
    assert "POST" in (allowed["allowMethods"] or ""), allowed
    assert "Content-Type" in (allowed["allowHeaders"] or ""), allowed
    assert allowed["vary"] == "Origin", (
        "a preflight answer that varies with the Origin header must say so, or a cache can serve "
        "one origin's answer to another"
    )
    for refused in out["cases"]["preflight_refused"]:
        assert refused["status"] == 403, refused
        assert refused["allowOrigin"] is None, (
            f"{refused['origin']} was refused 403 but handed an Allow-Origin header — with the "
            "header present the browser completes the credentialed call anyway"
        )


def test_the_mint_refuses_a_simple_cross_site_POST_by_MEDIA_TYPE_not_by_cookie() -> None:
    """#10291 — the 415 gate: the arm that stops a form post, which no preflight protects.

    `text/plain`, `application/x-www-form-urlencoded` and `multipart/form-data` are the three media
    types a cross-site HTML form can send WITHOUT a preflight, so the media-type check is the control
    that keeps a hostile page from reaching the mint with the visitor's cookie. Every case here is
    driven with **no** session cookie on purpose: the answer must be `415 unsupported_media_type`,
    never `401 no_session`, or the test would pass on a route that had lost the gate and refused for
    an unrelated reason.
    """
    out = _run("mint_transport")
    assert [c["mediaType"] for c in out["cases"]["wrong_media_type"]] == [
        "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"
    ]
    for case in out["cases"]["wrong_media_type"]:
        assert case["status"] == 415, case
        assert case["body"]["error"] == "unsupported_media_type", case


def test_a_method_that_is_neither_POST_nor_OPTIONS_is_answered_405_by_this_route() -> None:
    """#10291 — `onRequest` must own the refusal, not forward it.

    `next()` here would reach the ASSET SERVER (there is no other Function to chain to), whose 404
    carries no `Access-Control-Allow-Origin` — so the arm is driven with a `next()` that is
    FORBIDDEN to succeed: the harness records whether it was reached and answers 418 if it was.
    The 405 is where the refusal's CORS headers are attached, so without them the page sees a CORS
    failure instead of the refusal.
    """
    out = _run("mint_transport")
    for case in out["cases"]["wrong_method"]:
        assert case["status"] == 405, case
        assert case["body"]["error"] == "method_not_allowed", case
        assert case["nextCalled"] is False, (
            "the 405 forwarded to `next()`, which in Pages reaches the static asset server — its "
            f"404 carries no CORS header: {case}"
        )
        assert case["allowOrigin"] == _AS_ORIGIN, (
            "the 405 carries no Allow-Origin, so the page sees a CORS failure instead of the "
            "refusal"
        )


def test_a_non_POST_method_with_NO_origin_is_refused_instead_of_reflected() -> None:
    """The 405 path runs the exact-origin gate FIRST, so a caller with no `Origin` is refused.

    This route is credentialed and mints a bearer-equivalent assertion; §10 records that an absent
    `Origin` is refused here (unlike the BFF's other routes). The 403 must also carry no
    `Allow-Origin` — a refusal that reflects the caller's origin still completes the browser call.
    """
    out = _run("mint_transport")
    for case in out["cases"]["wrong_method_no_origin"]:
        assert case["status"] == 403, case
        assert case["body"]["error"] == "forbidden_origin", case
        assert case["allowOrigin"] is None, case


def test_the_token_route_exports_onRequest_and_no_verb_handler() -> None:
    """⛔ THE ROUTING CONTRACT: `onRequest` is the ONLY Pages handler `token.ts` may export.

    PRIMARY SOURCE — developers.cloudflare.com/pages/functions/api-reference: "The `onRequest`
    method will be called **unless a more specific `onRequestVerb` method is exported**. For
    example, if both `onRequest` and `onRequestGet` are exported, only `onRequestGet` will be
    called for GET requests." And: `onRequest` "will be invoked on all requests no matter what the
    request method is, as long as no specific request verb … is exported."

    So exporting a verb handler is not "a fast path apart" from `onRequest` — it TAKES the verb
    away from `onRequest`. At `cf6ccc129` this route exported `onRequestPost` + `onRequestOptions`
    + `onRequest`, and the 405 arm lived in the last of the three: for `GET`/`PUT`/`DELETE` the
    platform answered with a static 404 carrying no `Access-Control-Allow-Origin`, while the
    suite stayed green because it called `onRequest` directly and supplied the routing decision
    the platform makes. This pin exists because that mutation is INVISIBLE from the responses: no
    behavioural arm can distinguish "the 405 ran" from "the platform answered instead".

    Non-vacuity: at the pre-fix head this set was `['onRequest', 'onRequestOptions',
    'onRequestPost']` and the assertion fails on the first extra entry.
    """
    out = _run("mint_transport")
    assert out["handler_exports"] == ["onRequest"], (
        "the mint route exports a Pages handler besides `onRequest`: "
        f"{out['handler_exports']}. Cloudflare calls a verb export INSTEAD OF `onRequest`, so the "
        "405 arm in `onRequest` is dead for that verb and the refusal degrades to the platform's "
        "static 404 with no CORS header. Keep `mint`/`preflight` module-private and dispatch them "
        "from `onRequest`."
    )


def test_the_mint_route_allows_an_attempt_row_with_NO_owner_yet() -> None:
    """The cold-browser row is deliberately ownerless — pinning it stops a "fix" that refuses.

    A start runs BEFORE sign-in (that is the whole reason the hop exists), so its row cannot
    carry an owner. Refusing a NULL owner would break every cold browser; the null arm is
    therefore an intended pass, and the foreign-identity test above is what bounds it.
    """
    out = _run("mint_null_owner")
    assert out["status"] == 200, out
    assert out["body"]["token"].count(".") == 2


def test_the_mint_does_NOT_revalidate_the_grant_per_call_and_the_accessor_bounds_it() -> None:
    """The mint's comment claimed a provider-liveness check that the cached path never performs.

    Raised at `94906c6d0` as a P2 claim mismatch: the comment said the accessor "proves the session is
    LIVE against the provider, not merely un-expired in D1", while `getAccessTokenForSession` answers
    from D1 whenever the provider-issued token has more than `REFRESH_SKEW_MS` (60 s) left. The
    direction mattered — the claim was wrong in the PERMISSIVE direction, and it is the sentence a
    reader would lean on when deciding where a revoked grant is caught.

    Both halves are pinned, because a comment-only fix would leave either free to drift:
      * `cached_fresh` — inside the window the mint answers 200 with ZERO provider calls, i.e. a
        revocation is NOT detected per mint (the residual now recorded in the spec's §2.3);
      * `cached_then_refreshed` / `cached_then_revoked` — once the cache lapses the mint DOES reach the
        provider exactly once, and a revoked grant (`invalid_grant`) is a 401 `no_session`. That is the
        "detected at the next refresh" half of the corrected comment.

    The status alone would prove nothing: the cached and the refreshed path both answer 200, so the
    fetch COUNT is the only observable that separates them.
    """
    out = _run("mint_credential_resolution", cases={
        "cached_fresh": {"expiresInMs": 3_600_000, "responses": []},
        "cached_then_refreshed": {"expiresInMs": 1_000, "responses": [{"status": 200, "body": {
            "access_token": "a1", "refresh_token": "r1", "expires_in": 3600,
            "user": {"id": "user-1"}}}]},
        "cached_then_revoked": {"expiresInMs": 1_000,
                               "responses": [{"status": 400, "body": {"error": "invalid_grant"}}]},
    })
    by = {case["label"]: case for case in out["cases"]}
    assert set(by) == {"cached_fresh", "cached_then_refreshed", "cached_then_revoked"}, out

    assert "threw" not in by["cached_fresh"], by["cached_fresh"]
    assert by["cached_fresh"]["status"] == 200, by["cached_fresh"]
    assert by["cached_fresh"]["fetchCount"] == 0, (
        "the mint called the provider with a still-fresh cached access token — either "
        "`REFRESH_SKEW_MS` shrank to nothing or the accessor stopped answering from D1; the "
        "comment and the spec both state that this is the cached path, so a provider call here "
        f"means one of them is now wrong: {by['cached_fresh']}")

    assert by["cached_then_refreshed"]["fetchCount"] == 1, (
        "a stale cached token must reach the provider EXACTLY once — zero means the mint never "
        "checks the grant at all (revocation would never be detected), two means a refresh "
        f"stampede: {by['cached_then_refreshed']}")
    assert by["cached_then_refreshed"]["status"] == 200, by["cached_then_refreshed"]

    assert by["cached_then_revoked"]["fetchCount"] == 1, by["cached_then_revoked"]
    assert by["cached_then_revoked"]["status"] == 401, by["cached_then_revoked"]
    assert by["cached_then_revoked"]["body"]["error"] == "no_session", by["cached_then_revoked"]


def test_the_mints_comment_states_the_credential_bound_it_actually_has() -> None:
    """The claim lives in a COMMENT, so the file is where a revert would show up.

    The behavioural test above pins what the code does; this one pins that the sentence a reviewer
    reads says the same thing. The pre-fix text ("LIVE against the provider … a revoked provider
    grant would otherwise still mint") is asserted ABSENT by name, because that is the exact
    wording a future edit would restore from git history — and an over-strong claim here is how a
    real residual stops being looked for.
    """
    lines = (CONSENT_DIR / "token.ts").read_text().splitlines()
    call = next((i for i, line in enumerate(lines)
                 if "await getAccessTokenForSession(" in line), None)
    assert call is not None, "the mint no longer resolves the credential with the accessor"
    block: list[str] = []
    for line in reversed(lines[:call]):
        if not line.strip().startswith("//"):
            break
        block.insert(0, line)
    comment = "\n".join(block)
    assert comment, "no comment explains what the accessor buys at the mint site"
    assert "REFRESH_SKEW_MS" in comment, (
        "the comment must name the cache window it depends on, so the bound is checkable: "
        f"{comment}")
    assert "refresh" in comment.lower(), comment
    for stale_claim in ("LIVE against the provider",
                        "revoked provider grant would otherwise still mint"):
        assert stale_claim not in comment, (
            f"the comment restored the claim the cached path does not honour ({stale_claim!r}); "
            f"the accessor answers from D1 for up to one access-token lifetime: {comment}")


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

    The mint happens in TypeScript (the shipped `consent-bridge.ts::mintConsentToken` — NOT
    `consent-flow.ts`, which the purity guard forbids from holding an `async` function), the
    verification in Python
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


def _issuer_claim(token: str) -> str:
    """The minted token's `iss`, decoded but unverified — the exact bytes the AS compares."""
    import base64

    seg = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))["iss"]


def _presented() -> dict:
    return {
        "client_id": _PARAMS["client_id"],
        "redirect_uri": _PARAMS["redirect_uri"],
        "code_challenge": _PARAMS["code_challenge"],
        "state": _PARAMS["state"],
    }


def test_the_issuer_default_is_the_SAME_on_both_halves_and_ignores_APP_ORIGIN(monkeypatch) -> None:
    """`iss` has ONE knob and ONE default on both halves — and it is NOT `APP_ORIGIN`.

    `APP_ORIGIN` is a declared, `recommended` binding of this project, so an operator may set it
    explicitly. While the minter read it as an `iss` fallback and `consent_token_issuer()` did not,
    pointing it at any other host minted a token the AS then refused: the mint answered `200` and
    every redemption a silent `401 invalid_token`, with no other symptom. This runs the mint with
    `CONSENT_TOKEN_ISSUER` ABSENT and `APP_ORIGIN` set elsewhere, then hands the token to the
    shipped verifier (whose own `CONSENT_TOKEN_ISSUER` is unset too) — acceptance IS the agreement.
    """
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    monkeypatch.delenv("CONSENT_TOKEN_ISSUER", raising=False)

    out = _run("mint_ok", dropIssuer=True, appOrigin="https://preview.example")
    assert out["status"] == 200, out
    token = out["body"]["token"]
    assert _issuer_claim(token) == _ISSUER, (
        f"the minter stamped iss={_issuer_claim(token)!r}; it must default to the SAME literal the "
        "AS does, never to APP_ORIGIN — the AS does not read that binding, so deriving `iss` from it "
        "makes the mint succeed and every redemption fail")

    from tortoise.consent_token import verify_consent_token

    # Raises `401 invalid_token` if the two halves' defaults disagree. This is the assertion the
    # finding was about: the cross-half pin above it hardcodes both sides to the same value, so its
    # `??` short-circuited on `CONSENT_TOKEN_ISSUER` and never exercised a default at all.
    verify_consent_token(token, presented=_presented())


def test_a_padded_issuer_is_normalised_on_BOTH_halves(monkeypatch) -> None:
    """`consent_token_issuer()` does `.strip()`; the minter must trim too.

    The AS compares `iss` byte-exactly, so a padded `CONSENT_TOKEN_ISSUER` on the Pages side is
    another silent `401` — the same rule `CONSENT_TOKEN_SECRET`'s provenance documents at length,
    one field over.
    """
    padded = "  https://app.premiselabs.co  "
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    monkeypatch.setenv("CONSENT_TOKEN_ISSUER", padded)

    out = _run("mint_ok", issuer=padded)
    assert out["status"] == 200, out
    token = out["body"]["token"]
    assert _issuer_claim(token) == _ISSUER, (
        f"a padded issuer was stamped verbatim ({_issuer_claim(token)!r}) — the minter does not "
        "trim, so it disagrees with the AS the moment the value carries whitespace")

    from tortoise.consent_token import verify_consent_token

    verify_consent_token(token, presented=_presented())


def test_an_EMPTY_issuer_falls_through_to_the_default_on_BOTH_halves(monkeypatch) -> None:
    """An empty `CONSENT_TOKEN_ISSUER` is `or`-fallback, not `??`-fallback, on both sides.

    `"" ?? literal` yields `""` (a falsy value already set), while the AS's `configured or DEFAULT`
    yields the literal — so a `??` here would stamp `iss: ""` and fail every redemption. Absence and
    emptiness must resolve identically.
    """
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    monkeypatch.setenv("CONSENT_TOKEN_ISSUER", "")

    out = _run("mint_ok", issuer="")
    assert out["status"] == 200, out
    token = out["body"]["token"]
    assert _issuer_claim(token) == _ISSUER, (
        f"an empty issuer stamped {_issuer_claim(token)!r} instead of falling through to the "
        "default the AS uses")

    from tortoise.consent_token import verify_consent_token

    verify_consent_token(token, presented=_presented())


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


def test_the_loopback_verdict_does_not_depend_on_the_interpreter_patch_level(monkeypatch) -> None:
    """The mapped-loopback verdict is OURS, not the interpreter's (gh-117566).

    `test_the_redirect_uri_rule_agrees_with_the_AS` above carries the mapped-loopback rows, and it is
    therefore only a real guard on interpreters where the stdlib is WRONG. Before CPython 3.12.7
    `IPv6Address.is_loopback` was `self._ip == 1`, so `::ffff:127.0.0.1` was NOT loopback; from 3.12.7
    it is. CI resolves `python-version: '3.12'` to the LATEST patch, i.e. the fixed one — so without
    this test, deleting `_is_loopback`'s `ipv4_mapped` normalisation reds nothing anywhere and the
    regression returns invisibly.

    Pin the VERDICT, not the interpreter: force the pre-3.12.7 semantics and require our rule to be
    unaffected in both directions (a mapped loopback is loopback; a mapped private address is not).
    """
    import ipaddress

    from tortoise.oauth import _is_loopback

    monkeypatch.setattr(ipaddress.IPv6Address, "is_loopback", property(lambda self: self._ip == 1))
    assert _is_loopback("::ffff:127.0.0.1") is True, (
        "a mapped loopback was refused on the interpreter's say-so — `_is_loopback` no longer "
        "normalises `ipv4_mapped` itself, so the verdict returns to depending on the PATCH level"
    )
    assert _is_loopback("::ffff:10.0.0.1") is False, (
        "a mapped private address was accepted as loopback — the normalisation over-reached"
    )


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
    row keyed by `state`. The write is `user_id=COALESCE(consent_attempts.user_id, excluded.user_id)`
    — EXISTING first — precisely so a cold-browser re-entry (or a stale tab) cannot null a known
    owner: with a plain assignment, the next person to arrive signed in as someone else passes the mint's
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
        "created_at": 0, "expires_at": 9_999_999_999_999,
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


def _key_from_authorize(location: str | None) -> str:
    """The attempt key on the AS authorize URL — the carriage the MINT's caller actually has."""
    assert location, "an advance must answer a redirect to the AS"
    keys = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query).get("attempt_key")
    assert keys, (
        "the authorize URL carries NO attempt key — the consent page (the mint's ONLY documented "
        f"caller, §2.3) could never present one, so every mint would answer 403: {location!r}")
    return keys[0]


def test_the_key_the_page_receives_is_the_key_the_mint_accepts() -> None:
    """THE end-to-end carriage: `start` → the AS page's URL → `POST /api/consent/token` → 200.

    Nothing pinned this before. The mint tests seeded their own row and hash, and `start`'s tests
    only checked that SOME key came back in the app-origin return target — so a key that never
    reached the page (the mint's only caller) would have failed every real mint with
    `403 no_authorize_attempt` the moment step 4 wired the call: the visitor approves, the flow dies
    after consent. Measured here across the two routes, which is the only place the property lives.
    """
    advanced = _run("start_signed_in")            # signed in: the decision is `advance`
    assert advanced["status"] == 302, advanced
    key = _key_from_authorize(advanced["location"])

    minted = _run("mint_ok", seededAttempts=advanced["attempts"], attemptKey=key)
    assert minted["status"] == 200, (
        f"the key handed to the AS page was REFUSED by the mint: {minted['body']}")


def test_the_mints_store_fault_is_also_a_typed_503_with_a_log_line() -> None:
    """The read-fault arm of the MINT, pinned separately from `start`'s.

    Only `start_*` scenarios could throw, so deleting the mint's `console.error` (or turning its
    typed 503 into a 500) left the whole file green: a failed `ALTER TABLE` would then leave every
    `/api/consent/token` answering 503 with nothing in the logs to act on — the symptom the
    DDL-propagation fix exists to remove.
    """
    out = _run("mint_ok", storeThrows=True)
    assert out["status"] == 503, out
    assert out["body"] == {"error": "unavailable", "reason": "consent_store_unavailable"}, out["body"]
    assert "consent attempt read failed" in out["stderr"], (
        f"the mint's store fault was SILENT: {out['stderr'][-600:]!r}")


def test_a_GENUINE_DDL_fault_PROPAGATES_and_the_duplicate_column_does_not() -> None:
    """`ensureColumn` swallows ONLY `duplicate column name` — the mint half of that property.

    The tests above (and `test_a_store_fault_is_a_typed_503_and_never_a_redirect`) pin the
    OBSERVABILITY half of a store fault: the log line, the typed body, the missing `Location`. They
    cannot pin the PROPAGATION half, because `FakeD1`'s `storeThrows` fires on ANY statement naming
    the table — including the `CREATE TABLE IF NOT EXISTS` — so `ensureColumn`'s `ALTER TABLE` is
    never even reached. Measured: with the old double both of these left the file fully green: (a)
    `ensureColumn`'s catch widened to `catch { return; }`, (b) the call site replaced by a local
    swallow-all. Both are the same shipped regression — a genuinely failed `ALTER TABLE` swallowed,
    leaving a half-evolved schema that 503s with nothing anywhere to act on, which is precisely the
    outcome `start.ts:232-238` exists to remove.

    SQLite makes this a MESSAGE question and not a code one: a duplicate column and a genuine DDL
    failure both surface as SQLITE_ERROR (https://sqlite.org/rescode.html §4 — the primary result
    code is the same; only the text differs), so `ddlFault` injects both texts on `ALTER TABLE`
    only, and the two arms below are the two directions of the `only`.
    """
    # A genuine fault must PROPAGATE to the route's own catch — typed 503, logged, never a mint.
    faulted = _run("mint_ok", ddlFault="fault")
    assert faulted["status"] == 503, (
        f"a genuine DDL failure was SWALLOWED and the mint carried on: {faulted}")
    assert faulted["body"] == {
        "error": "unavailable", "reason": "consent_store_unavailable"}, faulted["body"]
    assert "consent attempt read failed" in faulted["stderr"], (
        f"a propagated DDL failure answered 503 with NO log line: {faulted['stderr'][-600:]!r}")
    # ... and the duplicate-column error (what every already-evolved database produces) must NOT be
    # propagated: it is the normal path, so swallowing it is load-bearing in its own direction.
    evolved = _run("mint_ok", ddlFault="duplicate")
    assert evolved["status"] == 200, (
        f"`duplicate column name` was PROPAGATED, so an already-evolved database could never mint: {evolved}")


def test_the_keys_HOLDER_renews_the_attempt() -> None:
    """The mirror of the test below, and the half nothing pinned.

    `CONSENT_ATTEMPT_TTL_S` exists to outlive the sign-in round trip, and the return leg's renewal
    is the only thing that gives a slow round trip that window. Measured gap: setting the renewal
    arm to a no-op (`THEN consent_attempts.expires_at`) left every route test green, so a visitor
    whose provider round trip outlived 15 minutes would come back to an expired row and get
    `403 no_authorize_attempt` AFTER approving — with no test able to red it.
    """
    # The hash is plain SHA-256 hex — computed here independently of the shipped helper, so this
    # also pins the digest choice the row is keyed on.
    import hashlib
    from_hash = hashlib.sha256(b"holder-key").hexdigest()
    seeded = [{
        "state": "state-xyz", "client_id": "client-abc",
        "redirect_uri": "https://client.example/cb",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "scope": "mcp", "resource": None, "user_id": None,
        "created_at": 0, "expires_at": 1_000, "attempt_key_hash": from_hash,
    }]
    out = _run("start_signed_in", seededAttempts=seeded, sessionUserId="user-1",
               params=dict(_PARAMS, attempt_key="holder-key"))
    row = out["attempts"][0]
    assert row["expires_at"] > 1_000, (
        "the key's HOLDER did not renew the attempt — a sign-in round trip that outlives the TTL "
        f"returns to a dead row and the visitor is refused after approving: {row}")


def test_a_keyless_re_entry_does_not_renew_the_attempt() -> None:
    """`expires_at` is refreshed only by the key's holder.

    Refreshing it unconditionally made every TTL a lie in one direction: any caller naming a `state`
    — including a DEAD pre-column row's — could keep the row alive forever, so "expires in 15
    minutes" bounded nothing and a dead row shadowed the visitor's retries indefinitely.
    """
    seeded = [{
        "state": "state-xyz", "client_id": "client-abc",
        "redirect_uri": "https://client.example/cb",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "scope": "mcp", "resource": None, "user_id": "user-1",
        "created_at": 0, "expires_at": 9_999_999_999_998, "attempt_key_hash": "a" * 64,
    }]
    out = _run("start_signed_in", seededAttempts=seeded, sessionUserId="user-2",
               params=dict(_PARAMS, attempt_key="b-attempt-key"))
    assert out["attempts"][0]["expires_at"] == 9_999_999_999_998, (
        "a caller holding no valid key RENEWED the attempt's lifetime "
        f"({out['attempts'][0]['expires_at']}) — a dead or foreign row can then be kept alive forever")


def test_the_attempt_can_be_MINTED_AGAIN_for_step_4s_recovery() -> None:
    """The residual, PINNED so it can be neither claimed away nor narrowed silently.

    `jti` bounds a TOKEN: one token cannot yield two authorization codes. It does NOT bound the
    APPROVAL — this route does not consume the attempt — and that is deliberate, because step 2
    spends the nonce BEFORE the insert and therefore costs a RE-MINT rather than a re-login when
    `issue_auth_code` refuses afterwards (`hosted_api.py`: "step 4's page mints again on load").
    A second mint for the same attempt is the recovery path, so it must keep working; the comment on
    the `jti` field and `consent_token.py`'s docstring now say exactly that instead of claiming the
    stronger bound. Making the attempt single-use would be a step-2/step-3 contract change, and this
    test is what forces that to be a decision rather than an accident.
    """
    out = _run("mint_twice")
    first, second = out["first"], out["second"]
    assert first["status"] == 200, first
    assert second["status"] == 200, (
        f"a re-mint of the SAME attempt was refused, so step 4's post-spend recovery is broken: "
        f"{second['body']}")
    assert first["body"]["token"] != second["body"]["token"], (
        "the same assertion was returned twice — a replayed token, not a fresh jti")
    assert len(out["rowsAfter"]) == 1, (
        "the second mint created a second attempt row — the re-mint must reuse the recorded one")

    # ⚠️ ONE PROCESS, ONE DATABASE. This test previously called `_run("mint_ok")` twice, which spawns
    # a fresh Node process and a fresh in-memory `DatabaseSync` per call — it asserted 200/200 about
    # two UNRELATED flows and could never red for a single-use mutation. `mint_twice` drives both
    # requests inside one driver run, so this now pins the real behaviour: the attempt is not
    # consumed, and making it single-use (the deferred contract change named in §2.2 and
    # `consent_token.py`) would red this instead of shipping silently into step 4.


def test_a_json_body_that_is_not_an_object_is_a_typed_400_not_an_untyped_500() -> None:
    """`request.json()` resolves with ANY JSON value — `null` included — so "the parse succeeded"
    is not "there is an object to read".

    The route's own contract is typed refusals (`401 no_session` / `503 unavailable` + named 4xx),
    and the sibling route names an untyped 500 as the state a page cannot tell apart from a bug in
    itself. The body is read BEFORE the session cookie, so this arm is reachable with no credential
    at all: `Origin` (an ordinary request header) plus `Content-Type: application/json` and the
    four bytes `null`. Pre-fix that last shape threw `TypeError: Cannot read properties of null`
    out of the mint handler, which Pages answers as an untyped 500.

    The message is pinned, not just the status: the simpler-looking fix
    `((await request.json()) ?? {}) as Record<string, unknown>` also stops the throw, but it reports
    a *missing `state`* for a body that was the wrong SHAPE — so a revert to it reds here even
    though it would pass a status-only assertion.

    Nothing in the suite sent such a body before (every mint scenario supplies a well-formed
    object), so this arm was unpinned in both directions.
    """
    bodies = {
        "null": "null",
        "array": "[]",
        "number": "5",
        "string": '"state-xyz"',
        "boolean": "true",
    }
    out = _run("mint_non_object_body", bodies=bodies)
    assert len(out["cases"]) == len(bodies), out
    for case in out["cases"]:
        assert "threw" not in case, (
            f"the mint THREW on the JSON body {case['text']!r} instead of refusing it "
            f"({case.get('threw')}) — an uncaught throw out of the handler is an UNTYPED 500, and "
            "this route answers a caller who holds no credential")
        assert case["status"] == 400, (
            f"JSON body {case['text']!r} answered {case['status']}, not a typed 400: {case}")
        assert case["body"] == {
            "error": "invalid_request", "message": "Body must be a JSON object."}, (
            f"JSON body {case['text']!r} was refused for the wrong reason: {case['body']}")
    # …and the guard is not a blanket refusal of the route: the object form still mints.
    assert _run("mint_ok")["status"] == 200


def test_the_four_refusals_are_byte_identical() -> None:
    """Same-403-is-not-an-oracle, pinned by BODY, not by status.

    A mutant that answered a distinguishable body for the mismatch arm (`attempt_key_mismatch`)
    passed the status-only assertions while handing a `state`-guesser a way to tell a real flow from
    a guess. All four arms — no key, wrong key, a pre-column row, an unrecorded `state` — must be
    the same bytes.
    """
    arms = {
        "absent": _run("mint_ok", omitKey=True),
        "wrong": _run("mint_ok", attemptKey="not-the-key"),
        "pre_column": _run("mint_ok", seededKeyHash=None),
        "unknown_state": _run("mint_no_attempt"),
    }
    bodies = {}
    for name, out in arms.items():
        assert out["status"] == 403, (name, out)
        bodies[name] = json.dumps(out["body"], sort_keys=True)
    assert len(set(bodies.values())) == 1, (
        f"the refusals are DISTINGUISHABLE, so the mint is a `state`/key oracle: {bodies}")


def test_a_store_fault_is_a_typed_503_and_never_a_redirect() -> None:
    """The gate must fail CLOSED when the store cannot answer.

    `FakeD1` never threw, so nothing redded if the route were changed to continue past a store
    fault — which would make the ownership check fail OPEN (no row read, no key verified). The
    contract is the same as every other store path here: typed 503, named reason, and NO Location
    header (a redirect on an infrastructure blip reads as a sign-out, #3485).
    """
    out = _run("start_signed_in", storeThrows=True)
    assert out["status"] == 503, out
    assert "consent attempt write failed" in out["stderr"], (
        "a store fault answered 503 with NO log line anywhere — a genuinely failed ALTER TABLE is "
        "indistinguishable from a transient blip and nothing in the logs can ever flag it: "
        f"{out['stderr'][-600:]!r}")
    assert out["body"] == {"error": "unavailable", "reason": "consent_store_unavailable"}, out["body"]
    assert out["location"] is None, f"a store fault produced a REDIRECT: {out['location']!r}"


def test_a_GENUINE_DDL_fault_PROPAGATES_on_start_too() -> None:
    """The `start` half of the same property — `ensureColumn` must not swallow a real DDL failure.

    Same gap and same double, stated once at the mint arm above: `storeThrows` throws on the
    `CREATE TABLE` so the `ALTER TABLE` never runs, which is why widening `ensureColumn`'s catch (or
    a local swallow-all at the call site) left this file green. `start` is the route that RECORDS,
    so it is the leg where a swallowed DDL failure would leave the flow half-written and every
    later mint 403 on a missing column.
    """
    faulted = _run("start_signed_in", ddlFault="fault")
    assert faulted["status"] == 503, (
        f"a genuine DDL failure was SWALLOWED and `start` carried on to a redirect: {faulted}")
    assert faulted["body"] == {
        "error": "unavailable", "reason": "consent_store_unavailable"}, faulted["body"]
    assert faulted["location"] is None, (
        f"a genuine DDL failure produced a REDIRECT: {faulted['location']!r}")
    assert "consent attempt write failed" in faulted["stderr"], (
        f"a propagated DDL failure answered 503 with NO log line: {faulted['stderr'][-600:]!r}")
    # The duplicate-column error is the NORMAL path on every already-evolved database: swallowed.
    evolved = _run("start_signed_in", ddlFault="duplicate")
    assert evolved["status"] == 302, (
        f"`duplicate column name` was PROPAGATED, so an already-evolved database could never start: {evolved}")


def _key_from(location: str | None) -> str:
    """The attempt key the route handed back in its `/auth?next=` return target."""
    assert location, "the cold-browser leg must answer a redirect, not a body"
    next_param = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["next"][0]
    keys = urllib.parse.parse_qs(urllib.parse.urlsplit(next_param).query).get("attempt_key")
    assert keys, f"the return target carries NO attempt key: {next_param!r}"
    return keys[0]


def test_the_ownerless_window_is_closed_by_the_attempt_key() -> None:
    """#10208: the flow belongs to a BROWSER, not to whoever knows the `state`.

    Before this, the cold-browser row was mintable — and owner-claimable — by any signed-in
    identity that named the `state`: visitor A starts a flow with no session (row `user_id` NULL),
    B issues ONE signed-in `GET /api/consent/start?…&state=<A's state>` and becomes the "first known
    owner", A's return leg can no longer fill the row, A's mint answers 403, and B mints an
    assertion asserting B bound to A's `redirect_uri`/`code_challenge`/`state`.

    The row now carries the hash of a key only A's browser holds (it rides in the return target —
    never a cookie, per §2.3), the key is insert-only, and BOTH the owner fill and the mint are
    gated on it. Measured here end to end, on real SQLite: the fill is refused for B, the mint is
    refused for B, and A's own return leg still fills it.
    """
    # 1. A starts the flow cold. The row exists with no owner and WITH a key hash.
    a_entry = _run("start_cold_browser")
    assert a_entry["status"] == 302, a_entry
    row = a_entry["attempts"][0]
    assert row["user_id"] is None, "the cold-browser row must start ownerless"
    assert row["attempt_key_hash"], "the attempt was recorded WITHOUT a key — nothing to prove"
    a_key = _key_from(a_entry["location"])

    # 2. B, signed in as somebody else, names the same `state` with NO key. The row must stay
    #    ownerless — B is not the browser that created it — and B is refused before consent.
    b = _run("start_signed_in", seededAttempts=a_entry["attempts"], sessionUserId="user-2")
    assert b["status"] == 403, (
        "a second identity was ADVANCED into a flow it does not hold — it would reach the AS's "
        f"consent page and then be unable to mint: {b}")
    assert b["body"] == {"error": "no_authorize_attempt", "reason": "flow_not_yours"}, b["body"]
    assert b["attempts"][0]["user_id"] is None, (
        f"a second identity CLAIMED the ownerless row — owner is now {b['attempts'][0]['user_id']!r}")
    assert b["attempts"][0]["attempt_key_hash"] == row["attempt_key_hash"], (
        "the attempt key was ROTATED by a later caller — rotation is the takeover")

    # 3. B cannot mint for it either, key or no key: the row is ownerless AND B holds no key.
    assert _run("mint_null_owner", sessionUserId="user-2", omitKey=True)["status"] == 403
    assert _run("mint_null_owner", sessionUserId="user-2", attemptKey="b-guess")["status"] == 403

    # 4. A's return leg (signed in as A, carrying the key) fills the owner and advances.
    a_back = _run("start_signed_in", seededAttempts=b["attempts"],
                  params=dict(_PARAMS, attempt_key=a_key))
    assert a_back["status"] == 302, f"A's own return leg was refused: {a_back}"
    assert "api.premiselabs.co/oauth/authorize" in (a_back["location"] or ""), a_back["location"]
    assert a_back["attempts"][0]["user_id"] == "user-1", (
        f"the legitimate owner could not fill their own flow: {a_back['attempts'][0]}")


def test_the_mint_requires_the_key_the_creating_browser_holds() -> None:
    """The owner column alone cannot fail closed — an ownerless row minted for anyone (#10208)."""
    # The right key: minted.
    assert _run("mint_ok")["status"] == 200
    # Absent, wrong, and "right key but the row predates the column" are the SAME 403 — so the
    # route is not an oracle telling a guesser their `state` is real.
    assert _run("mint_ok", omitKey=True)["status"] == 403
    assert _run("mint_ok", attemptKey="not-the-key")["status"] == 403
    assert _run("mint_ok", seededKeyHash=None)["status"] == 403


def test_the_owner_fill_is_refused_by_a_FOREIGN_key_even_when_the_owner_is_known() -> None:
    """Insert-only key + gated fill: the second identity cannot even rewrite an OWNED row."""
    seeded = [{
        "state": "state-xyz", "client_id": "client-abc",
        "redirect_uri": "https://client.example/cb",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "scope": "mcp", "resource": None, "user_id": "user-1",
        "created_at": 0, "expires_at": 9_999_999_999_999, "attempt_key_hash": "a" * 64,
    }]
    out = _run("start_signed_in", seededAttempts=seeded, sessionUserId="user-2",
               params=dict(_PARAMS, attempt_key="b-attempt-key"))
    assert out["status"] == 403, out
    assert out["attempts"][0]["user_id"] == "user-1", out["attempts"][0]
    assert out["attempts"][0]["attempt_key_hash"] == "a" * 64, out["attempts"][0]


def test_a_misconfigured_AS_origin_is_a_TYPED_503_and_never_an_untyped_500() -> None:
    """A bad `CONSENT_AS_ORIGIN` must not turn the public consent entry into a 500.

    THE BUG THIS PINS: `authorizeUrl` calls `new URL("/oauth/authorize", asOrigin)`, and the URL
    constructor throws a `TypeError` on a base that does not parse (WHATWG URL §6.1). Nothing
    wrapped that call, so a scheme-less value — the natural slip for a binding this project tells
    operators to set per deployment (`config/required-bindings.yml`) — escaped as an UNTYPED 500
    with no log line, on the one route a cold browser hits first. The page cannot tell that apart
    from a bug in itself, which is precisely the class this file's property 3 refuses for the store.

    TWO arms, because they fail DIFFERENTLY on the pre-fix code, and both were MEASURED with `node -e`:
    a scheme-less/hostless value and `javascript:…` both THROW (`new URL("/oauth/authorize", base)`
    → `Invalid URL`, because a relative reference cannot resolve against a non-special-scheme base —
    so on the pre-fix route the throw escapes `onRequestGet`, kills the driver process, and this test
    reds with a harness exit failure rather than a status mismatch), while `ftp://…` is a SPECIAL
    scheme: it resolves to `ftp://api.premiselabs.co/oauth/authorize` and the pre-fix route answers a
    **302 to an `ftp:` URL**. The assertion is therefore on the outcome a caller actually receives,
    not on a throw.
    """
    for bad in (
        "api.premiselabs.co",              # scheme-less: the operator slip, throws pre-fix
        "not a url at all",                # unparseable, throws pre-fix
        "javascript:alert(1)",             # parses alone, but cannot base a relative ref: throws pre-fix
        "ftp://api.premiselabs.co",        # special scheme, wrong protocol: 302 pre-fix
    ):
        out = _run("start_signed_in", asOrigin=bad)
        assert out["status"] == 503, (bad, out)
        assert out["body"] == {"error": "unavailable", "reason": "as_origin_misconfigured"}, (
            bad, out["body"])
        assert out["location"] is None, (
            f"{bad!r} produced a REDIRECT to {out['location']!r} instead of failing closed")
        # Refused BEFORE the store: a deployment that cannot build an authorize URL must not leave
        # attempt rows behind that no browser can ever complete.
        assert out["attempts"] == [], (bad, out["attempts"])
        # ...and the operator's only signal. A silent refusal is indistinguishable from an outage.
        assert "CONSENT_AS_ORIGIN" in out["stderr"], (bad, out["stderr"][-600:])


def test_the_AS_origin_default_still_works_when_the_binding_is_ABSENT() -> None:
    """The refusal above must not have turned an UNPROVISIONED knob into an outage.

    `CONSENT_AS_ORIGIN` is a `recommended`, not `required`, binding at
    `config/required-bindings.yml:391` — every deployment that never set it must keep working, so
    absence resolves to the documented default rather than to `as_origin_misconfigured`.
    """
    out = _run("start_signed_in", dropAsOrigin=True)
    assert out["status"] == 302, out
    assert (out["location"] or "").startswith(_AS_ORIGIN + "/oauth/authorize?"), out["location"]


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

    The owner is now the FIRST one recorded, AND the attempt key decides who may write it at all.
    This test drives both with a SESSION for a second identity — not a signed-out leg, which both
    COALESCE orders satisfy.

    ⚠️ The row is seeded WITH a key hash on purpose. Seeding it without one (as this test first
    did) let it pass while a foreign caller's key was written into the row: the `user_id` CASE
    happened to preserve the already-set owner, so the assertion held even though the row's KEY had
    been taken over — which is the precondition for the next write to succeed. Hence the hash
    assertion below.
    """
    owned = [{
        "state": "state-xyz", "client_id": "client-abc",
        "redirect_uri": "https://client.example/cb",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "scope": "mcp", "resource": None, "user_id": "user-1",
        "created_at": 0, "expires_at": 9_999_999_999_999, "attempt_key_hash": "a" * 64,
    }]
    out = _run("start_signed_in", seededAttempts=owned, sessionUserId="user-2",
               params=dict(_PARAMS, attempt_key="b-attempt-key"))
    rows = [r for r in out["attempts"] if r["state"] == "state-xyz"]
    assert len(rows) == 1, rows
    assert rows[0]["user_id"] == "user-1", (
        f"a second identity (user-2) TOOK OVER a flow user-1 began — the row now says "
        f"{rows[0]['user_id']!r}, so user-2 can mint an assertion into user-1's consent "
        "(identity injection: the foreign-owner refusal at token.ts becomes unreachable by one GET)")
    assert rows[0]["attempt_key_hash"] == "a" * 64, (
        "the attempt KEY was overwritten by a caller that does not hold it — the takeover is one "
        "step away: whoever owns the hash owns every later write to this row")
    assert out["status"] == 403, f"a foreign key was ADVANCED into the flow: {out}"


def test_a_row_from_an_OLD_BUILD_is_dead_rather_than_adoptable() -> None:
    """#10208, the rollout window: rows written BEFORE the key column existed have `NULL` there.

    `COALESCE(consent_attempts.attempt_key_hash, excluded.attempt_key_hash)` ADOPTED a caller's key
    into such a row, and `owns_flow` then compared against the value it had just installed — so the
    first caller to name the `state` OWNED the flow, while `token.ts` read the same NULL-hash row as
    nobody's. The two legs disagreed about one row, and the invariant this commit exists to
    establish did not hold for every attempt in flight during a deploy (≤15 min, extended by each
    re-entry). Such a row is now dead: the key column is written ONLY on insert, and a NULL hash
    fails the fill's `IS NOT NULL` guard, which is exactly the mint's verdict.

    The table here is the LEGACY one (no `attempt_key_hash`), so this also exercises the shipped
    `ALTER … ADD COLUMN` bootstrap path.
    """
    legacy = [{
        "state": "state-xyz", "client_id": "client-abc",
        "redirect_uri": "https://client.example/cb",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "scope": "mcp", "resource": None, "user_id": None,
        "created_at": 0, "expires_at": 9_999_999_999_999,
    }]
    out = _run("start_signed_in", seededAttempts=legacy, sessionUserId="user-2",
               wantLegacySchema=True, params=dict(_PARAMS, attempt_key="b-attempt-key"))
    rows = [r for r in out["attempts"] if r["state"] == "state-xyz"]
    assert len(rows) == 1, rows
    assert rows[0]["attempt_key_hash"] is None, (
        "a caller ADOPTED a pre-column row by presenting its own key — from that moment `owns_flow` "
        "is true for it and the row's ownership is decided by whoever asked first")
    assert rows[0]["user_id"] is None, f"a pre-column row was filled by {rows[0]['user_id']!r}"
    assert out["status"] == 403, f"a dead pre-column flow was advanced instead of refused: {out}"


def test_the_shipped_DDL_is_what_creates_the_table() -> None:
    """The fakes substitute the STORE and the PROVIDER only — never the schema.

    `FakeD1.exec()` used to be a no-op while the double carried its own copy of the CREATE TABLE,
    so the shipped DDL never ran in any test: renaming a column in it left every route test green
    while production answered 503 on every `/api/consent/start`. Here the table is created by the
    code under test, and the assertion is on a column only that DDL defines.
    """
    out = _run("start_cold_browser")
    assert out["status"] == 302, out
    row = out["attempts"][0]
    assert row.get("attempt_key_hash"), (
        "the shipped CREATE TABLE did not define `attempt_key_hash` — the double must not supply "
        f"the schema: {sorted(row)}")


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
