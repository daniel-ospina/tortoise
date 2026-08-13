// Runtime CORS behavioral harness for the tenant-provision Edge Function.
//
// Production incident 2026-08-13: welcome.html calls tenant-provision from the
// browser (JWT path, #527/#802) with `Authorization: Bearer` +
// `Content-Type: application/json`, which forces a CORS preflight. The
// function answered OPTIONS with a bare 405 and no CORS headers on ANY
// response, so every signup was blocked with "No 'Access-Control-Allow-Origin'
// header". This harness executes the REAL handler code (imports stubbed) and
// asserts CORS behavior on every browser-reachable response path — a runtime
// guarantee the pytest source guards (test_provisioning_edge_function.py)
// cannot give.
//
// Runs under node --experimental-strip-types (mirrors the waitlist harness in
// CI). The three remote/relative imports are rewritten to local stubs so the
// function parses and runs outside Deno; the stubs throw if a path they
// replace is ever reached, proving the tested paths are import-independent.

import assert from "node:assert";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";

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

let handler = null;
globalThis.Deno = {
  serve: (cb) => { handler = cb; },
  env: { get: () => undefined }, // AUTH_HOOK_SECRET unset → Path 2 fails CLOSED (401)
};

await import("./" + dir.split("/").pop() + "/index-under-test.ts");

assert.ok(handler, "Deno.serve registered a handler");

async function run(method, origin, headers = {}) {
  const h = new Headers(headers);
  if (origin) h.set("origin", origin);
  return handler(
    new Request("http://localhost/functions/v1/tenant-provision", {
      method,
      headers: h,
      body:
        method === "POST"
          ? JSON.stringify({
              user_id: "00000000-0000-0000-0000-000000000000",
              email: "t@t.co",
            })
          : undefined,
    })
  );
}

const PROD = "https://tortoise.premiselabs.co";
const EVIL = "https://evil.example.com";
const FALLBACK = "https://premiselabs.co"; // ALLOWED_ORIGINS[0]

const checks = [
  ["preflight from welcome-page origin → 204 + allowlisted ACAO",
    await run("OPTIONS", PROD),
    { status: 204, acao: PROD, allowMethods: "POST, OPTIONS", allowHeaders: "authorization, content-type", vary: true }],
  ["preflight from unknown origin → 403 (never echo evil origin)",
    await run("OPTIONS", EVIL),
    { status: 403, acao: FALLBACK, vary: true }],
  ["POST unauthenticated (no JWT, no hook secret) → 401 readable by browser",
    await run("POST", PROD),
    { status: 401, acao: PROD, vary: true }],
  ["POST from unknown origin → 403 with CORS",
    await run("POST", EVIL),
    { status: 403, acao: FALLBACK, vary: true }],
  ["GET (wrong method) → 405 with CORS",
    await run("GET", PROD),
    { status: 405, acao: PROD, vary: true }],
  ["POST server-side (no Origin) → 401 with ACAO fallback",
    await run("POST", null),
    { status: 401, acao: FALLBACK, vary: true }],
  ["GET server-side (no Origin) → 405 with ACAO fallback",
    await run("GET", null),
    { status: 405, acao: FALLBACK, vary: true }],
];

let failures = 0;
for (const [name, res, expect] of checks) {
  const acao = res.headers.get("access-control-allow-origin");
  const vary = res.headers.get("vary");
  const ok =
    res.status === expect.status &&
    acao === expect.acao &&
    (expect.allowMethods === undefined || res.headers.get("access-control-allow-methods") === expect.allowMethods) &&
    (expect.allowHeaders === undefined || res.headers.get("access-control-allow-headers") === expect.allowHeaders) &&
    (expect.vary ? vary !== null && vary.toLowerCase().includes("origin") : true);
  if (!ok) failures += 1;
  console.log(`${ok ? "  ✓" : "  ✗"} ${name}  (status=${res.status} acao=${acao} vary=${vary})`);
}

console.log(`\n${checks.length - failures}/${checks.length} runtime CORS checks passed`);
rmSync(dir, { recursive: true, force: true });
if (failures > 0) process.exit(1);
