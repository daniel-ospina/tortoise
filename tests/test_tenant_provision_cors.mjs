// Runtime CORS behavioral harness for the tenant-provision Edge Function.
//
// Production incident 2026-08-13: welcome.html calls tenant-provision from the
// browser (JWT path, #527/#802) with `Authorization: Bearer` +
// `Content-Type: application/json`, which forces a CORS preflight. The
// function answered OPTIONS with a bare 405 and no CORS headers on ANY
// response, so every signup was blocked with "No 'Access-Control-Allow-Origin'
// header". This harness executes the REAL handler code (imports stubbed) and
// asserts CORS behavior on every response path a browser can hit — a runtime
// guarantee the pytest source guards (test_provisioning_edge_function.py)
// cannot give.
//
// Runs under node --experimental-strip-types (mirrors the waitlist harness in
// CI). The three remote/relative imports are rewritten to local stubs so the
// function parses and runs outside Deno. Stubs implement REAL behavior where
// the harness needs it (Standard-Webhooks HMAC verify; configurable
// provision_team rpc) and throw where a path is not supposed to be reached.
//
// Two phases:
//   1. Unauthenticated (no AUTH_HOOK_SECRET): preflight/origin/method/auth
//      gates — the browser-facing surface of the incident.
//   2. Authenticated (hook signature valid): the 400 (type guard), 500
//      (FASTAPI_URL missing), 502 (provision_team failure) and 201 (success)
//      paths — every response must still carry ACAO + Vary so the browser can
//      read them.

import assert from "node:assert";
import { createHmac } from "node:crypto";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { __setRpcResult } from "./edge-function-stubs/supabase-js.ts";

const REPO_ROOT = new URL("..", import.meta.url).pathname;
const EDGE_FN = join(REPO_ROOT, "supabase/functions/tenant-provision/index.ts");
const STUBS = join(REPO_ROOT, "tests/edge-function-stubs");

let src = readFileSync(EDGE_FN, "utf8");
// Node's type-stripping resolves relative imports within the project, so the
// rewritten copy lives in a transient tests/.cors-tmp-*/ dir (gitignored) and
// is imported relatively — a tmpdir/file:// import hits ERR_INVALID_TYPESCRIPT_SYNTAX.
src = src.replace(
  '"https://esm.sh/@supabase/supabase-js@2"',
  '"' + join(STUBS, "supabase-js.ts") + '"'
);
src = src.replace(
  '"https://esm.sh/standardwebhooks@1.0.0"',
  '"' + join(STUBS, "standardwebhooks.ts") + '"'
);
src = src.replace(
  '"../_shared/lookup.ts"',
  '"' + join(STUBS, "_shared/lookup.ts") + '"'
);

const dir = mkdtempSync(join(REPO_ROOT, "tests/.cors-tmp-"));
const copy = join(dir, "index-under-test.ts");
writeFileSync(copy, src);

// ── Mutable environment + fetch, set per phase ───────────────────────────
const envVars = {};
globalThis.Deno = {
  serve: (cb) => { handler = cb; },
  env: { get: (k) => envVars[k] },
};
// The success path POSTs to ${FASTAPI_URL}/internal/demo — stub it.
globalThis.fetch = async () => new Response(null, { status: 200 });

let handler = null;

const HOOK_SECRET_BARE = "tstsecret";
const HOOK_SECRET = "v1,whsec_" + HOOK_SECRET_BARE;
const USER_ID = "11111111-2222-3333-4444-555555555555";
const EMAIL = "t@t.co";

function signedHeaders(rawBody) {
  const id = "msg_" + Date.now();
  const ts = String(Math.floor(Date.now() / 1000));
  const sig = "v1," + createHmac("sha256", HOOK_SECRET_BARE)
    .update(`${id}.${ts}.${rawBody}`)
    .digest("base64");
  return {
    "webhook-id": id,
    "webhook-timestamp": ts,
    "webhook-signature": sig,
  };
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

const PROD = "https://tortoise.premiselabs.co";
const EVIL = "https://evil.example.com";
const FALLBACK = "https://premiselabs.co"; // ALLOWED_ORIGINS[0]
const checks = [];
const results = [];

function check(name, res, expect) {
  const acao = res.headers.get("access-control-allow-origin");
  const vary = res.headers.get("vary");
  const ok =
    res.status === expect.status &&
    acao === expect.acao &&
    (expect.allowMethods === undefined ||
      res.headers.get("access-control-allow-methods") === expect.allowMethods) &&
    (expect.allowHeaders === undefined ||
      res.headers.get("access-control-allow-headers") === expect.allowHeaders) &&
    (expect.vary ? vary !== null && vary.toLowerCase().includes("origin") : true);
  results.push({ name, ok, status: res.status, acao, vary });
  checks.push(name);
}

try {
  await import("./" + dir.split("/").pop() + "/index-under-test.ts");
  assert.ok(handler, "Deno.serve registered a handler");

  // ── Phase 1: unauthenticated (env empty) — browser-facing gates ────────
  check("preflight from welcome-page origin → 204 + allowlisted ACAO",
    await call("OPTIONS", { origin: PROD }),
    { status: 204, acao: PROD, allowMethods: "POST, OPTIONS", allowHeaders: "authorization, content-type", vary: true });
  check("preflight from unknown origin → 403 (never echo evil origin)",
    await call("OPTIONS", { origin: EVIL }),
    { status: 403, acao: FALLBACK, vary: true });
  check("POST unauthenticated (no JWT, no hook secret) → 401 readable by browser",
    await call("POST", { origin: PROD, "content-type": "application/json" }, JSON.stringify({ user_id: USER_ID, email: EMAIL })),
    { status: 401, acao: PROD, vary: true });
  check("POST from unknown origin → 403 with CORS",
    await call("POST", { origin: EVIL, "content-type": "application/json" }, JSON.stringify({ user_id: USER_ID, email: EMAIL })),
    { status: 403, acao: FALLBACK, vary: true });
  check("GET (wrong method) → 405 with CORS",
    await call("GET", { origin: PROD }),
    { status: 405, acao: PROD, vary: true });
  check("POST server-side (no Origin) → 401 with ACAO fallback",
    await call("POST", { "content-type": "application/json" }, JSON.stringify({ user_id: USER_ID, email: EMAIL })),
    { status: 401, acao: FALLBACK, vary: true });
  check("GET server-side (no Origin) → 405 with ACAO fallback",
    await call("GET"),
    { status: 405, acao: FALLBACK, vary: true });
  check("any localhost port is allowlisted (welcome.html isLocal semantics)",
    await call("OPTIONS", { origin: "http://localhost:3000" }),
    { status: 204, acao: "http://localhost:3000", allowMethods: "POST, OPTIONS", vary: true });

  // ── Phase 2: authenticated hook caller (valid Standard-Webhooks HMAC) ──
  envVars["AUTH_HOOK_SECRET"] = HOOK_SECRET;
  const hookBody = JSON.stringify({
    user: { id: USER_ID, email: EMAIL, user_metadata: { display_name: "Test" } },
  });

  check("hook POST, body `null` → 401 (auth fails first, no 500) with CORS",
    await call("POST", signedHeaders("null"), "null"),
    { status: 401, acao: FALLBACK, vary: true });
  check("hook POST, type-confused user_id (no valid user) → 401, no 500",
    await call("POST", signedHeaders('{"user_id":123,"email":"t@t.co"}'), '{"user_id":123,"email":"t@t.co"}'),
    { status: 401, acao: FALLBACK, vary: true });
  check("hook POST, valid user + type-confused secondary field → 400 type guard",
    await call("POST", signedHeaders('{"user":{"id":"' + USER_ID + '","email":"' + EMAIL + '"},"user_id":123}'), '{"user":{"id":"' + USER_ID + '","email":"' + EMAIL + '"},"user_id":123}'),
    { status: 400, acao: FALLBACK, vary: true });
  check("hook POST, FASTAPI_URL missing → 500 with CORS (browser-readable)",
    await call("POST", signedHeaders(hookBody), hookBody),
    { status: 500, acao: FALLBACK, vary: true });

  envVars["FASTAPI_URL"] = "http://fastapi.test";
  envVars["FASTAPI_INTERNAL_KEY"] = "key";
  envVars["TORTOISE_SECRET_PEPPER"] = "pepper";
  envVars["SUPABASE_URL"] = "https://x.supabase.co";
  envVars["SUPABASE_SERVICE_ROLE_KEY"] = "service";

  check("hook POST, provision_team error → 502 with CORS",
    await call("POST", signedHeaders(hookBody), hookBody),
    { status: 502, acao: FALLBACK, vary: true });

  __setRpcResult({ error: null });
  const okRes = await call("POST", signedHeaders(hookBody), hookBody);
  check("hook POST, success → 201 with CORS + minted body",
    okRes,
    { status: 201, acao: FALLBACK, vary: true });
  const okBody = JSON.parse(await okRes.text());
  assert.ok(okBody.api_key && okBody.api_key.startsWith("tt_"), "201 body must include the minted api_key");
  assert.ok(okBody.team_id, "201 body must include team_id");
  console.log("      (success body: team_id=" + okBody.team_id + " api_key=" + okBody.api_key.slice(0, 10) + "…)");

  // ── Report ─────────────────────────────────────────────────────────────
  for (const r of results) {
    console.log(`${r.ok ? "  ✓" : "  ✗"} ${r.name}  (status=${r.status} acao=${r.acao} vary=${r.vary})`);
  }
  const failures = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failures}/${results.length} runtime CORS checks passed`);
  if (failures > 0) process.exitCode = 1;
} finally {
  rmSync(dir, { recursive: true, force: true });
}
