// Behavioral tests for the deck-view edge function handler.
//
// Runs the PURE handle.ts (zero imports) under Node with a stubbed global
// fetch — real assertions on the pipeline: the method gate, origin allowlist,
// body cap, email validation, honeypot, deck coercion, rate limit, the PostgREST
// insert shape, and the CORS headers on every path that builds its OWN response —
// the OPTIONS 204 and the 429, the two that do not go through the shared `json()`
// helper, so a missing ACAO on either would be silent.
//
// The property this file exists to protect: a view is NOT a subscription. The
// function writes `deck_views` and never touches `waitlist_subscribers`, so a
// reader who traded an address to open the deck is not put on the mailing list.
//
// Run (Node >= 22.6):
//   node --experimental-strip-types tests/test_deck_view.mjs

import { handle, __resetRateLimit } from "../supabase/functions/deck-view/handle.ts";

const ENV = {
  SUPABASE_URL: "https://ybetwichurajbfswfeqa.supabase.co",
  SUPABASE_SERVICE_ROLE_KEY: "svc_key",
};
const ORIGIN = "https://tortoise.premiselabs.co";

let failures = 0;
let calls = [];

function stubFetch(handler) {
  globalThis.fetch = async (url, init = {}) => {
    const call = { url: String(url), init, body: init.body ?? null };
    calls.push(call);
    return handler(call, init);
  };
}

function reset() {
  calls = [];
  __resetRateLimit();
}

const jsonResponse = (status, body) => new Response(JSON.stringify(body), { status });
const post = (body, headers = {}) => new Request("http://x", {
  method: "POST",
  headers: { "Content-Type": "application/json", Origin: ORIGIN, ...headers },
  body: JSON.stringify(body),
});
const ok = (msg) => console.log(`  ✓ ${msg}`);
const fail = (msg) => { failures += 1; console.error(`  ✗ ${msg}`); };
const assert = (cond, msg) => { if (cond) ok(msg); else fail(msg); };

// ── 1. CORS preflight ─────────────────────────────────────────────────────
reset();
stubFetch(() => jsonResponse(201, null));
{
  const req = new Request("http://x", { method: "OPTIONS", headers: { Origin: ORIGIN } });
  const res = await handle(req, ENV);
  assert(res.status === 204, "OPTIONS → 204");
  assert(res.headers.get("access-control-allow-origin") === ORIGIN, "OPTIONS echoes the allowlisted origin");
  assert(res.headers.get("access-control-allow-methods") === "POST, OPTIONS", "OPTIONS Allow-Methods");
  assert(res.headers.get("vary") === "Origin", "OPTIONS Vary: Origin");
}

// ── 2. Gates: method, origin, body ────────────────────────────────────────
reset();
stubFetch(() => jsonResponse(201, null));
{
  const res = await handle(new Request("http://x", { method: "GET", headers: { Origin: ORIGIN } }), ENV);
  assert(res.status === 405 && res.headers.get("access-control-allow-origin"), "GET → 405 + ACAO");
}
{
  const res = await handle(post({ email: "a@b.co" }, { Origin: "https://evil.example" }), ENV);
  assert(res.status === 403, "un-allowlisted origin → 403");
  assert(res.headers.get("access-control-allow-origin") === "https://premiselabs.co",
    "un-allowlisted origin gets the primary allowlisted origin, never its own");
}
{
  // The preflight rejects a hostile origin the same way the POST does — a
  // browser would otherwise see the CORS failure, not the 403.
  const res = await handle(new Request("http://x", {
    method: "OPTIONS", headers: { Origin: "https://evil.example" },
  }), ENV);
  assert(res.status === 403 && res.headers.get("access-control-allow-origin") === "https://premiselabs.co",
    "OPTIONS from an un-allowlisted origin → 403 + the primary allowlisted origin");
}
{
  const res = await handle(post({ email: "a@b.co", pad: "x".repeat(9000) }), ENV);
  assert(res.status === 400 && res.headers.get("access-control-allow-origin"), "oversize body → 400 + ACAO");
}
{
  // A JSON scalar/array parses fine but is not a body we can read a field from.
  const res = await handle(post(["a@b.co"]), ENV);
  assert(res.status === 400 && res.headers.get("access-control-allow-origin"), "non-object body → 400 + ACAO");
}
{
  // A body read that throws is a clean 400, not an uncaught crash: the page
  // treats 4xx as "the gate stays shut", so a 500 would fail it OPEN instead.
  const res = await handle({
    method: "POST",
    headers: new Headers({ Origin: ORIGIN }),
    text: () => Promise.reject(new Error("stream reset")),
  }, ENV);
  assert(res.status === 400 && res.headers.get("access-control-allow-origin"), "failed body read → 400 + ACAO");
}
{
  const res = await handle(new Request("http://x", {
    method: "POST", headers: { Origin: ORIGIN }, body: "not json",
  }), ENV);
  assert(res.status === 400 && res.headers.get("access-control-allow-origin"), "non-JSON body → 400 + ACAO");
}
{
  const res = await handle(post({ email: "a@b.co", hp: "bot" }), ENV);
  assert(res.status === 200 && calls.length === 0, "honeypot → silent 200, nothing written");
  assert(res.headers.get("access-control-allow-origin"), "honeypot 200 carries ACAO");
}
{
  // The handler ignores unknown fields, and the cap is generous enough that an
  // extra one cannot reject a reader's real submission.
  reset();
  stubFetch(() => jsonResponse(201, null));
  const res = await handle(post({
    email: "token@example.com", deck: "investors", "cf-turnstile-response": "x".repeat(2048),
  }), ENV);
  assert(res.status === 200 && calls.length === 1,
    "a body carrying an extra 2048-char field is accepted, not rejected as oversize");
}

// ── 3. Email validation ───────────────────────────────────────────────────
reset();
stubFetch(() => jsonResponse(201, null));
for (const [value, label] of [[undefined, "absent"], ["", "empty"], ["nope", "no TLD"], [42, "non-string"]]) {
  const res = await handle(post({ email: value }), ENV);
  assert(res.status === 400 && res.headers.get("access-control-allow-origin"),
    `invalid email (${label}) → 400 + ACAO`);
}
assert(calls.length === 0, "no insert attempted for any invalid address");

// ── 4. The write: deck_views, lowercased, minimal response ────────────────
reset();
stubFetch((call) => {
  assert(call.url.includes("/rest/v1/deck_views"), "insert targets deck_views");
  assert(!call.url.includes("waitlist_subscribers"), "a view never touches the mailing list");
  const parsed = JSON.parse(call.body);
  assert(parsed.email === "reader@example.com", "email trimmed + lowercased");
  assert(parsed.deck === "investors", "deck recorded");
  assert(parsed.source === undefined, "no subscription source on a view row");
  assert(String(call.init.headers.Prefer).toLowerCase().includes("return=minimal"),
    "insert asks for no representation");
  return jsonResponse(201, null);
});
{
  const res = await handle(post({ email: "  READER@Example.com " }), ENV);
  assert(res.status === 200 && (await res.json()).ok === true, "recorded view → {ok:true}");
  assert(res.headers.get("access-control-allow-origin") === ORIGIN, "200 echoes the allowlisted origin");
}

// ── 5. Deck name is coerced, not trusted ──────────────────────────────────
reset();
stubFetch(() => jsonResponse(201, null));
{
  await handle(post({ email: "d@example.com", deck: "payroll" }), ENV);
  assert(JSON.parse(calls[0].body).deck === "investors", "unknown deck falls back to investors");
}
reset();
stubFetch(() => jsonResponse(201, null));
{
  await handle(post({ email: "d@example.com", deck: 7 }), ENV);
  assert(JSON.parse(calls[0].body).deck === "investors", "non-string deck falls back");
}

// ── 6. Failures: insert rejected, config missing, rate limit ──────────────
reset();
stubFetch(() => new Response("nope", { status: 500 }));
{
  const res = await handle(post({ email: "fail@example.com" }), ENV);
  assert(res.status === 500 && res.headers.get("access-control-allow-origin"), "insert reject → 500 + ACAO");
}
reset();
stubFetch(() => { throw new Error("network down"); });
{
  const res = await handle(post({ email: "boom@example.com" }), ENV);
  assert(res.status === 500, "thrown fetch → 500, not an uncaught crash");
  assert(res.headers.get("access-control-allow-origin"), "thrown fetch → 500 + ACAO");
}
reset();
stubFetch(() => jsonResponse(201, null));
{
  const res = await handle(post({ email: "cfg@example.com" }), { SUPABASE_URL: undefined });
  assert(res.status === 500, "missing service config → 500");
  assert(res.headers.get("access-control-allow-origin"), "config 500 + ACAO");
}
reset();
stubFetch(() => jsonResponse(201, null));
{
  let last = null;
  for (let i = 0; i < 31; i++) {
    last = await handle(post({ email: `r${i}@example.com` }, { "x-forwarded-for": "9.9.9.9" }), ENV);
  }
  assert(last.status === 429, "31st request from one IP → 429");
  assert(last.headers.get("retry-after") === "3600", "429 carries Retry-After");
  assert(last.headers.get("access-control-allow-origin") === ORIGIN, "429 carries ACAO");
}
reset();
stubFetch(() => jsonResponse(201, null));
{
  let last = 0;
  for (let i = 0; i < 31; i++) {
    last = (await handle(post({ email: `s${i}@example.com` }, { "x-forwarded-for": `10.0.0.${i}` }), ENV)).status;
  }
  assert(last === 200, "the limit is per-IP: distinct IPs are not throttled together");
}

console.log(failures === 0 ? "\n✅ All deck-view handler tests passed" : `\n❌ ${failures} assertion(s) failed`);
process.exit(failures === 0 ? 0 : 1);
