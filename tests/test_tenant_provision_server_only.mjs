// Runtime harness for the tenant-provision Edge Function's SERVER-ONLY
// contract (#1120).
//
// HISTORY, because this file replaced a CORS harness and the replacement is
// the point: production incident 2026-08-13 found the welcome page calling
// this function DIRECTLY from the browser, and the function answering no CORS
// headers — every signup blocked (#1110/#1111). The #1111 fix added an origin
// allowlist, an OPTIONS preflight answer and ACAO on every response path, and
// `tests/test_tenant_provision_cors.mjs` pinned all of it.
//
// The browser caller is GONE: welcome.html's provisioning pipeline is dead
// (#1566) and the dashboard reaches this function through its same-origin BFF
// route (`website/apps/dashboard/functions/api/provision.ts`), which mints the
// access token server-side from the HttpOnly `__Host-session` cookie and
// STRIPS `Origin` before forwarding. #1120 therefore deletes the CORS surface
// (no allowlist, no preflight, no `Access-Control-*` header on any path) and
// makes "no browser caller" an ENFORCED property: an origin-bearing request is
// refused 403. That is what this harness executes — the REAL handler body with
// its Deno/remote imports rewritten to local stubs — so the contract is proven
// at runtime, not just by source-text assertions in
// `tests/test_provisioning_edge_function.py`.
//
// Two things this harness must keep proving, because both were real failures:
//   - the deterministic per-user org id (#2810/#847): one user provisioned
//     twice lands on ONE org, asserted from the recorded RPC arguments;
//   - the identity match (#802): a caller can mint only for THEMSELVES.
//
// Runs under `node --experimental-strip-types` (the same floor as the other
// edge-function harnesses). The relative/remote imports are rewritten into a
// transient gitignored copy under `tests/.server-only-tmp-*/`, because ESM
// resolves the subject's relative imports from the COPY's own path — and the copy
// is imported below by a specifier relative to this file, so it sits beside it.

import assert from "node:assert";
import { createHash, createHmac } from "node:crypto";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { __resetRpcCalls, __rpcCalls, __setJwtUser, __setRpcResult } from "./edge-function-stubs/supabase-js.ts";

const REPO_ROOT = new URL("..", import.meta.url).pathname;
const EDGE_FN = join(REPO_ROOT, "supabase/functions/tenant-provision/index.ts");
const STUBS = join(REPO_ROOT, "tests/edge-function-stubs");

let src = readFileSync(EDGE_FN, "utf8");
src = src.replace(
  '"https://esm.sh/@supabase/supabase-js@2"',
  '"' + join(STUBS, "supabase-js.ts") + '"'
);
src = src.replace(
  '"../_shared/lookup.ts"',
  '"' + join(STUBS, "_shared/lookup.ts") + '"'
);

const dir = mkdtempSync(join(REPO_ROOT, "tests/.server-only-tmp-"));
const copy = join(dir, "index-under-test.ts");
writeFileSync(copy, src);

// ── Mutable environment + fetch, set per phase ───────────────────────────
const envVars = {};
let handler = null;
globalThis.Deno = {
  serve: (cb) => { handler = cb; },
  env: { get: (k) => envVars[k] },
};
// The success path POSTs to ${FASTAPI_URL}/internal/starter-seed and
// /internal/onboarding-email — stub both as 200s.
globalThis.fetch = async () => new Response(null, { status: 200 });

const USER_ID = "11111111-2222-3333-4444-555555555555";
const EMAIL = "t@t.co";
const PROD = "https://tortoise.premiselabs.co";
const APP = "https://app.premiselabs.co";

const results = [];

function record(name, ok, detail) {
  results.push({ name, ok, detail });
}

/** Assert a response carries NO CORS surface at all. */
function assertNoCors(name, res) {
  for (const header of [
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-allow-credentials",
    "access-control-expose-headers",
    "access-control-max-age",
  ]) {
    const value = res.headers.get(header);
    assert.equal(
      value,
      null,
      `${name}: ${header} must NOT be emitted — the endpoint has no CORS surface (#1120)`
    );
  }
}

async function call(method, headers = {}, rawBody = undefined) {
  return handler(
    new Request("http://localhost/functions/v1/tenant-provision", {
      method,
      headers: new Headers(headers),
      body: rawBody,
    })
  );
}

/** One runtime check: status + the absence of any CORS header. */
async function check(name, callPromise, expectedStatus) {
  const res = await callPromise;
  const corsFree = (() => {
    try {
      assertNoCors(name, res);
      return true;
    } catch {
      return false;
    }
  })();
  record(name, res.status === expectedStatus && corsFree, `status=${res.status} cors-free=${corsFree}`);
  return res;
}

const JSON_HEADERS = { "content-type": "application/json" };
const BODY = JSON.stringify({ user_id: USER_ID, email: EMAIL });

try {
  await import("./" + dir.split("/").pop() + "/index-under-test.ts");
  assert.ok(handler, "Deno.serve registered a handler");
  assert.ok(
    !/"standardwebhooks@/.test(src),
    "the Standard-Webhooks import must be gone with the auth-hook path (#1120)"
  );

  // ── Phase 1: server-only gate, before any auth ─────────────────────────
  // A browser CORS preflight can never succeed: OPTIONS is not a method this
  // endpoint answers, and nothing advertises CORS.
  const preflight = await check(
    "OPTIONS (a browser preflight) → 405, no CORS header (preflight can never pass)",
    call("OPTIONS", { origin: PROD, "access-control-request-method": "POST" }),
    405
  );
  assert.equal(preflight.headers.get("allow"), "POST", "the 405 must still say which method is accepted");

  // The monitor greps this literal, and a browser's PREFLIGHT is answered by the
  // method gate, so the refusal must be LOGGED there — otherwise the count can
  // never fire for the class it names (review finding F2). Captured, not assumed:
  // console.error is stubbed for the call and the emitted text is inspected.
  {
    const capture = async (method, headers) => {
      const logged = [];
      const realError = console.error;
      console.error = (...args) => { logged.push(args.map(String).join(" ")); };
      try {
        await call(method, headers);
      } finally {
        console.error = realError;
      }
      return logged;
    };
    const preflightLogged = await capture("OPTIONS", {
      origin: PROD, "access-control-request-method": "POST",
    });
    const preflightHit = preflightLogged.some((l) =>
      l.includes("refused a browser-origin call"));
    record(
      "an origin-bearing PREFLIGHT is logged (the monitor counts that literal)",
      preflightHit, "logged=" + JSON.stringify(preflightLogged)
    );
    assert.ok(
      preflightHit,
      "an origin-bearing OPTIONS must log 'refused a browser-origin call' — a " +
        "preflighted browser call never reaches the origin gate, so without this " +
        "the monitor's count can never fire for the outage it watches"
    );
    // Negative control: an origin-less wrong-method call (curl, a scanner on
    // GET) must stay UNLOGGED, or every scanner probe pages on the >=5 arm.
    const quietLogged = await capture("GET", {});
    record(
      "an origin-less GET logs NOTHING (a scanner probe must not page)",
      quietLogged.length === 0, "logged=" + JSON.stringify(quietLogged)
    );
    assert.equal(
      quietLogged.length, 0,
      "an origin-less wrong-method call must not log the browser-origin refusal"
    );
  }

  // The enforced property: an origin-bearing request is a browser call.
  await check(
    "POST carrying Origin (browser call) → 403 server-only, even unauthenticated",
    call("POST", { ...JSON_HEADERS, origin: PROD }, BODY),
    403
  );
  await check(
    "POST carrying the app origin (the old #1566 in-app caller) → 403 server-only",
    call("POST", { ...JSON_HEADERS, origin: APP }, BODY),
    403
  );
  // The method gate is FIRST, so a wrong method reads as 405 even with Origin.
  await check(
    "GET with Origin → 405 (method gate precedes the origin gate)",
    call("GET", { origin: PROD }),
    405
  );
  await check(
    "POST server-side with no auth → 401, no CORS header (nothing to read cross-origin)",
    call("POST", JSON_HEADERS, BODY),
    401
  );
  await check("GET server-side → 405", call("GET"), 405);

  // ── Phase 2: the auth-hook path is GONE ───────────────────────────────
  // `after_user_created` is not a supported Supabase auth hook, so the
  // AUTH_HOOK_SECRET / Standard-Webhooks path was deleted (#1120).
  //
  // A literal `v1,AAAA` made this arm tautological: the deleted handler
  // returned 401 for an UNVERIFIABLE signature anyway (its stub threw
  // "signature mismatch" → the fail-closed return), so the arm passed
  // IDENTICALLY whether or not the hook path existed. Only a signature the
  // deleted code would have ACCEPTED can fail when that path returns.
  //
  // ⛔ THERE ARE TWO SUCH SIGNATURES, because there are two key semantics. The
  // handler's ARGUMENT is the same either way
  // (origin/main:supabase/functions/tenant-provision/index.ts:183:
  //   new Webhook(hookSecret.replace(/^v1,whsec_/, ""))  → the base64 text),
  // but the two verifiers hash that argument DIFFERENTLY:
  //   - the deleted TEST STUB used it as ASCII
  //     (`createHmac("sha256", this.secret)`), whereas
  //   - the real `standardwebhooks@1.0.0` library BASE64-DECODES it
  //     (`this.key = base64.decode(secret)` — dist/index.js:44, read from the
  //     published package).
  // So a restore using the real library REJECTS a stub-shaped signature and
  // answers the same 401 — the arm stays tautological for that restore class.
  // Both are pinned below. The secret is VALID base64 so both semantics are
  // well-defined. Do not "simplify" this back to one arm or to a literal.
  //
  // ⛔ AND THE TIMESTAMP MUST BE FRESH. `standardwebhooks@1.0.0` calls
  // `verifyTimestamp` BEFORE it hashes (dist/index.js:58, ahead of `sign` at
  // :59) and throws `Message timestamp too old` once
  // `now - timestamp > WEBHOOK_TOLERANCE_IN_SECONDS` (300s, dist/index.js:7,95).
  // A frozen `1700000000` therefore makes the library-shaped arm REJECT before
  // the key is ever used — the arm cannot fail for the verifier it names, i.e.
  // the same tautology one layer down. `Date.now()` keeps it inside tolerance so
  // the HMAC compare is actually reached. (The stub arm has no tolerance check —
  // it just hashes the timestamp into the signed string — so a fresh ts is
  // harmless there; it stays a signature the stub accepts.)
  const HOOK_SECRET_B64 = "MDEyMzQ1Njc4OWFiY2RlZmdoaWprbG1u";
  const HOOK_SECRET_ENV = "v1,whsec_" + HOOK_SECRET_B64; // what AUTH_HOOK_SECRET held
  const hookId = "msg_1";
  const hookTs = String(Math.floor(Date.now() / 1000)); // inside the 300s tolerance
  const hookBody = JSON.stringify({ user: { id: USER_ID, email: EMAIL } });
  const hookSig = (key) =>
    "v1," +
    createHmac("sha256", key)
      .update(`${hookId}.${hookTs}.${hookBody}`)
      .digest("base64");
  const hookReq = (signature) =>
    call(
      "POST",
      {
        ...JSON_HEADERS,
        "webhook-id": hookId,
        "webhook-timestamp": hookTs,
        "webhook-signature": signature,
      },
      hookBody
    );
  envVars["AUTH_HOOK_SECRET"] = HOOK_SECRET_ENV;
  await check(
    "POST with a webhook signature the deleted STUB would accept → 401 (hook path gone)",
    hookReq(hookSig(HOOK_SECRET_B64)),
    401
  );
  await check(
    "POST with a webhook signature the real library would accept → 401 (hook path gone)",
    hookReq(hookSig(Buffer.from(HOOK_SECRET_B64, "base64"))),
    401
  );

  // ── Phase 3: server-side config + upstream failures ───────────────────
  __setJwtUser({ id: USER_ID, email: EMAIL, user_metadata: { display_name: "Test" } });
  const jwtHeaders = { authorization: "Bearer test-token", ...JSON_HEADERS };

  envVars["SUPABASE_URL"] = "https://x.supabase.co";
  envVars["SUPABASE_ANON_KEY"] = "anon";
  envVars["SUPABASE_SERVICE_ROLE_KEY"] = "service";
  // Set the pepper BEFORE the FASTAPI_URL row: otherwise the 500 could come from
  // `hashApiKey` throwing "TORTOISE_SECRET_PEPPER is not set" instead of from the
  // guard being tested, and the row would print ✓ for a fail-closed arm that had
  // been deleted (review finding F2 — the ordering was carried over from the old
  // CORS harness, where it was already unearned).
  envVars["TORTOISE_SECRET_PEPPER"] = "pepper";

  const noFastapi = await check(
    "POST authenticated but FASTAPI_URL missing → 500 naming the cause",
    call("POST", jwtHeaders, BODY),
    500
  );
  // The status alone is NOT the assertion: the outer catch returns a BARE 500
  // (`{"error":"Internal server error"}`, `index.ts` `:526-529`), so a 500 can
  // come from anything that throws above it, while this row's own label promises
  // a NAMED cause.
  //
  // ⛔ NOT because "status alone keeps the row green with the guard deleted" —
  // measured with `if (!fastApiUrl)` changed to `if (false)`, the request falls
  // through to `provision_team` and returns **502** (`index.ts` `:445-451`), which
  // the status pin above already records as a failure. (The pepper IS set above,
  // so `hashApiKey` cannot 500 here either.) This pin keeps the row's NAME true;
  // it is not what catches a deleted guard.
  assert.match(
    await noFastapi.text(),
    /FASTAPI_URL missing/,
    "the 500 must name FASTAPI_URL — a bare 500 from the outer catch says only "
      + "'Internal server error', which leaves the operator no condition to act on"
  );

  envVars["FASTAPI_URL"] = "http://fastapi.test";
  envVars["FASTAPI_INTERNAL_KEY"] = "key";

  __setRpcResult({ error: new Error("rpc down") });
  await check(
    "POST authenticated, provision_team error → 502",
    call("POST", jwtHeaders, BODY),
    502
  );

  // ── #1981: the one-free-org refusal is TERMINAL, and this is the path a
  //    client actually reaches (the auth-hook path is inert post-#832). A 502
  //    here is the one status a client is entitled to retry, for a condition no
  //    retry can clear — so the RPC's marker must surface as a 402 carrying the
  //    product payload. This case is BEHAVIOURAL on purpose: the static pin in
  //    test_provisioning_edge_function.py cannot see where the marker is read
  //    from, and a mutant computing `markerAt` from `provisionError.details`
  //    instead of `.message` leaves the branch unreachable while every static
  //    assertion still passes (measured on the review of 577ed5822fe5). Here
  //    that mutant reds, because the response goes back to 502.
  __setRpcResult({
    error: { message: "Supabase control-plane RPC failed (provision_team): HTTP 402: one_free_org_limit:org-blocker" },
  });
  const limitedRes = await check(
    "POST authenticated, provision_team one-free-org refusal → 402 (terminal, not a retryable 502) (#1981)",
    call("POST", jwtHeaders, BODY),
    402
  );
  const limitedBody = JSON.parse(await limitedRes.text());
  assert.equal(limitedBody.code, "one_free_org_limit",
    "the refusal must carry the product's machine-readable code (#2789)");
  assert.equal(limitedBody.org_id, "org-blocker",
    "org_id must name the blocking org the dialog's upgrade action targets");
  assert.ok(
    String(limitedBody.message).startsWith("You can only have one free organization"),
    "message must be the product sentence, got: " + limitedBody.message);

  // The generic lane must SURVIVE: only the marker is a 402. A bare error is
  // still a 502 — otherwise every failure is laundered into a refusal.
  __setRpcResult({ error: { message: "permission denied for table organizations" } });
  await check(
    "POST authenticated, non-marker provision_team error → 502 (not laundered into a 402) (#1981)",
    call("POST", jwtHeaders, BODY),
    502
  );

  __setRpcResult({ error: null });
  const okRes = await check(
    "POST authenticated, target matches → 201",
    call("POST", jwtHeaders, BODY),
    201
  );
  const okBody = JSON.parse(await okRes.text());
  assert.ok(
    okBody.api_key && okBody.api_key.startsWith("tt_"),
    "201 body must include the minted api_key"
  );
  assert.ok(okBody.org_id, "201 body must include org_id");
  // ⛔ KEEP THE ORG-NAME ASSERTION. The deleted harness pinned it
  // (`origin/main:tests/test_tenant_provision_cors.mjs:202` —
  // `jwtBody.org_name === "test"`, "team name derives from display_name") and
  // this replacement dropped it, so neutering the provider-display-name lookup
  // left the harness fully green while the org name, the starter-seed
  // person_name and the onboarding-email greeting all silently changed
  // (measured: setting that lookup to `undefined` kept 17/17 green).
  assert.equal(
    okBody.org_name,
    "test",
    "team name derives from the provider display_name (Test), lowercased"
  );
  console.log(
    "      (success body: org_id=" + okBody.org_id + " api_key=" + okBody.api_key.slice(0, 10) + "…)"
  );

  // ── Phase 4: identity match (#802) + payload type guards (#1111) ──────
  await check(
    "POST authenticated, target mismatch (user A mints for user B) → 403",
    call(
      "POST",
      jwtHeaders,
      JSON.stringify({
        user_id: "99999999-9999-9999-9999-999999999999",
        email: "other@x.co",
      })
    ),
    403
  );
  await check(
    "POST authenticated, type-confused secondary field → 400 (never a 500)",
    call(
      "POST",
      jwtHeaders,
      JSON.stringify({ user_id: USER_ID, email: EMAIL, display_name: 123 })
    ),
    400
  );
  await check(
    'POST authenticated, body `null` → 400 (type guard, never a 500)',
    call("POST", jwtHeaders, "null"),
    400
  );
  await check(
    "POST authenticated, invalid JWT (getUser returns no user) → 401",
    (async () => {
      __setJwtUser(null);
      const res = await call("POST", jwtHeaders, BODY);
      __setJwtUser({ id: USER_ID, email: EMAIL, user_metadata: { display_name: "Test" } });
      return res;
    })(),
    401
  );
  // The server-only invariant holds with a VALID credential too: a browser
  // holding a legitimate token still cannot mint, because the endpoint is
  // reached server-side only.
  await check(
    "POST with a VALID JWT that also carries Origin → 403 server-only",
    call("POST", { ...jwtHeaders, origin: PROD }, BODY),
    403
  );

  // Non-string provider metadata display_name must NOT crash the handler
  // (#1132): the org name falls back to the email prefix.
  __setJwtUser({ id: USER_ID, email: EMAIL, user_metadata: { display_name: 123 } });
  __setRpcResult({ error: null });
  const dnRes = await check(
    "POST authenticated, non-string metadata display_name → 201 (no 500, email-prefix org)",
    call("POST", jwtHeaders, BODY),
    201
  );
  const dnBody = JSON.parse(await dnRes.text());
  assert.equal(dnBody.org_name, "t", "org name falls back to the email prefix (t@t.co)");
  __setJwtUser({ id: USER_ID, email: EMAIL, user_metadata: { display_name: "Test" } });

  // ── Phase 5 (#2810): the deterministic org id survives the caller change ──
  // The org id is sha256(user_id)[:26], so a retried request (lost response,
  // double submit) converges on ONE org through provision_team's ON CONFLICT.
  // Asserted here from the RPC ARGUMENTS the real handler sends; the SQL
  // invariant itself is pinned in supabase/tests/0010_provisioning_rpcs.sql §3.
  __resetRpcCalls();
  const twice1 = await call("POST", jwtHeaders, BODY);
  const twice2 = await call("POST", jwtHeaders, BODY);
  const orgIds = __rpcCalls().map((c) => c.params?.p_org_id);
  const expectedOrgId = createHash("sha256").update(USER_ID).digest("hex").slice(0, 26);
  const deterministic =
    twice1.status === 201 &&
    twice2.status === 201 &&
    orgIds.length === 2 &&
    orgIds[0] === expectedOrgId &&
    orgIds[0] === orgIds[1];
  record(
    "ONE user provisioned twice → same deterministic p_org_id (sha256(user_id)[:26])",
    deterministic,
    "p_org_id=" + orgIds[0]
  );
  assert.ok(
    deterministic,
    `deterministic org id violated: p_org_ids=${JSON.stringify(orgIds)} expected=${expectedOrgId}`
  );

  // ── Report ────────────────────────────────────────────────────────────
  for (const r of results) {
    console.log(`${r.ok ? "  ✓" : "  ✗"} ${r.name}  (${r.detail})`);
  }
  const failures = results.filter((r) => !r.ok).length;
  console.log(
    `\n${results.length - failures}/${results.length} runtime server-only checks passed`
  );
  if (failures > 0) process.exitCode = 1;
} finally {
  rmSync(dir, { recursive: true, force: true });
}
