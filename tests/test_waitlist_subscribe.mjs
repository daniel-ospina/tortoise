// Behavioral tests for the waitlist-subscribe edge function handler (#373).
//
// Runs the PURE handle.ts (zero imports) under Node with a stubbed global
// fetch — real assertions on the pipeline: CORS (every path), method gate,
// origin allowlist, body cap, honeypot, rate limit, Turnstile both-keys rule,
// PostgREST on_conflict dedup discriminator, Resend send/duplicate-skip.
//
// Run (Node >= 22.6):
//   node --experimental-strip-types tests/test_waitlist_subscribe.mjs

import { handle, __resetRateLimit } from "../supabase/functions/waitlist-subscribe/handle.ts";

// NOTE: no TURNSTILE_SECRET_KEY in the base fixture — captcha is fail-open
// until provisioned (matches production default). Captcha cases set it
// explicitly; when set, a token becomes REQUIRED.
const ENV = {
  SUPABASE_URL: "https://ybetwichurajbfswfeqa.supabase.co",
  SUPABASE_SERVICE_ROLE_KEY: "svc_key",
  RESEND_API_KEY: "re_test",
  RESEND_FROM_EMAIL: "Premise Labs <noreply@premiselabs.co>",
};
const ENV_CAPTCHA = { ...ENV, TURNSTILE_SECRET_KEY: "0xsecret" };

let failures = 0;
let calls = []; // recorded fetch calls

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

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), { status });
}

const ok = (msg) => console.log(`  ✓ ${msg}`);
const fail = (msg) => { failures += 1; console.error(`  ✗ ${msg}`); };

function assert(cond, msg) {
  if (cond) ok(msg); else fail(msg);
}

const ORIGIN = "https://premiselabs.co";
function post(body, extra = {}) {
  const headers = { "Content-Type": "application/json", ...(extra.headers ?? {}) };
  if (extra.origin !== undefined) {
    if (extra.origin === null) delete headers["Origin"]; else headers["Origin"] = extra.origin;
  } else {
    headers["Origin"] = ORIGIN;
  }
  return new Request("https://ybetwichurajbfswfeqa.supabase.co/functions/v1/waitlist-subscribe", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

function findCall(urlPart) {
  return calls.find((c) => c.url.includes(urlPart));
}

// ── 1. CORS preflight ─────────────────────────────────────────────────────
reset();
stubFetch(() => jsonResponse(201, [{ id: "x", email: "a@b.co" }]));
{
  const req = new Request("http://x", { method: "OPTIONS", headers: { Origin: ORIGIN } });
  const res = await handle(req, ENV);
  assert(res.status === 204, "OPTIONS → 204");
  assert(res.headers.get("access-control-allow-origin") === ORIGIN, "OPTIONS ACAO echoes allowlisted origin");
  assert(res.headers.get("access-control-allow-methods") === "POST, OPTIONS", "OPTIONS Allow-Methods");
  assert(res.headers.get("access-control-allow-headers") === "content-type", "OPTIONS Allow-Headers");
  assert(res.headers.get("vary") === "Origin", "OPTIONS Vary: Origin");
}

// ── 2. Fresh insert → success + Resend called with unsubscribe footer ─────
reset();
stubFetch((call) => {
  if (call.url.includes("/rest/v1/")) {
    assert(call.url.includes("on_conflict=email"), "insert URL carries ?on_conflict=email");
    const pre = call.init.headers.Prefer ?? call.init.headers.prefer ?? "";
    assert(pre.includes("return=representation") && pre.includes("resolution=ignore-duplicates"),
      "Prefer header carries return=representation + resolution=ignore-duplicates");
    assert(typeof call.body === "string", "insert body is a string");
    const parsed = JSON.parse(call.body);
    assert(parsed.email === "user@example.com", "email lowercased/trimmed");
    return jsonResponse(201, [{ id: "x", email: "user@example.com" }]);
  }
  if (call.url.includes("api.resend.com")) {
    assert(typeof call.body === "string", "resend body is a string");
    const parsed = JSON.parse(call.body);
    assert(parsed.to[0] === "user@example.com", "resend to: email");
    assert(parsed.from === "Premise Labs <noreply@premiselabs.co>", "resend from");
    assert(parsed.subject.length > 0 && parsed.html.toLowerCase().includes("unsubscribe"),
      "resend template contains unsubscribe footer");
    assert(call.init.signal !== undefined, "resend uses an abort timeout");
    return jsonResponse(200, { id: "mail_1" });
  }
  return jsonResponse(500, { error: "unexpected" });
});
{
  const res = await handle(post({ email: "  USER@Example.com " }), ENV);
  assert(res.status === 200 && (await res.json()).ok === true, "fresh insert → {ok:true}");
  assert(findCall("api.resend.com") !== undefined, "fresh insert → Resend called once");
}

// ── 3. Duplicate → "Already subscribed", NO Resend re-fire ─────────────────
reset();
stubFetch(() => jsonResponse(201, [])); // ignore-duplicates returns empty array
{
  const res = await handle(post({ email: "dup@example.com" }), ENV);
  const body = await res.json();
  assert(res.status === 200 && body.ok === true && body.message === "Already subscribed",
    "duplicate → {ok:true, message:'Already subscribed'}");
  assert(findCall("api.resend.com") === undefined, "duplicate → NO Resend call");
}

// ── 4. Honeypot filled → silent 200, no store, no email ───────────────────
reset();
stubFetch(() => { throw new Error("should not be called"); });
{
  const res = await handle(post({ email: "bot@example.com", hp: "filled-by-bot" }), ENV);
  assert(res.status === 200 && (await res.json()).ok === true, "honeypot → silent 200");
  assert(calls.length === 0, "honeypot → zero outbound calls");
}

// ── 5. Rate limit: 11th request from same IP → 429 with Retry-After ───────
reset();
stubFetch(() => jsonResponse(201, [{ id: "x" }]));
{
  let lastStatus = 0;
  for (let i = 0; i < 11; i++) {
    const req = post({ email: `rl${i}@example.com` }, { headers: { "x-forwarded-for": "203.0.113.7" } });
    lastStatus = (await handle(req, ENV)).status;
  }
  assert(lastStatus === 429, "11th request from same IP → 429");
  const req = post({ email: "rlx@example.com" }, { headers: { "x-forwarded-for": "203.0.113.7" } });
  const res = await handle(req, ENV);
  assert(res.status === 429, "rate limit persists");
  assert(res.headers.get("retry-after") === "3600", "429 carries Retry-After");
  assert(res.headers.get("access-control-allow-origin") !== null, "429 carries ACAO");
}

// ── 6. No x-forwarded-for → requests pass (no shared bucket) ──────────────
reset();
stubFetch(() => jsonResponse(201, [{ id: "x" }]));
{
  let okCount = 0;
  for (let i = 0; i < 15; i++) {
    const req = post({ email: `noxff${i}@example.com` }, { headers: {} });
    if ((await handle(req, ENV)).status === 200) okCount += 1;
  }
  assert(okCount === 15, "no x-forwarded-for → no shared bucket, all pass");
}

// ── 7. Turnstile both-keys rule + failed verify ───────────────────────────
reset();
stubFetch((call) => {
  if (call.url.includes("siteverify")) {
    const hs = call.init.headers;
    const ct = hs instanceof Headers ? hs.get("content-type") : (hs["content-type"] ?? hs["Content-Type"]);
    assert(ct === "application/x-www-form-urlencoded",
      "siteverify body is form-encoded");
    const params = new URLSearchParams(call.body);
    assert(params.get("secret") === "0xsecret" && params.get("response") === "tok123",
      "siteverify carries secret + response");
    assert(params.get("remoteip") !== null, "siteverify carries remoteip");
    return jsonResponse(200, { success: false });
  }
  return jsonResponse(201, [{ id: "x" }]);
});
{
  // token + secret → verified (fails here) → 400, no insert/email
  const res = await handle(post({ email: "cap@example.com", "cf-turnstile-response": "tok123" }, { headers: { "x-forwarded-for": "198.51.100.9" } }), ENV_CAPTCHA);
  assert(res.status === 400, "failed siteverify → 400");
  assert(findCall("/rest/v1/") === undefined && findCall("api.resend.com") === undefined,
    "failed captcha → zero insert/email");
  assert(res.headers.get("access-control-allow-origin") !== null, "400 carries ACAO");
}
reset();
stubFetch((call) => {
  if (call.url.includes("siteverify")) return jsonResponse(200, { success: true });
  return jsonResponse(201, [{ id: "x" }]);
});
{
  const res = await handle(post({ email: "cap2@example.com", "cf-turnstile-response": "tok123" }), ENV_CAPTCHA);
  assert(res.status === 200, "successful siteverify → 200");
}
reset();
stubFetch(() => jsonResponse(201, [{ id: "x" }]));
{
  // token WITHOUT secret → skip verify, proceed (fail-open only on misconfig)
  const res = await handle(post({ email: "nosk@example.com", "cf-turnstile-response": "tok123" }),
    { ...ENV, TURNSTILE_SECRET_KEY: undefined });
  assert(res.status === 200, "token without secret → skip verify");
  assert(findCall("siteverify") === undefined, "no siteverify call when secret missing");
}
reset();
stubFetch(() => jsonResponse(201, [{ id: "x" }]));
{
  // secret set but NO token → 400 (captcha is REQUIRED when configured)
  const res = await handle(post({ email: "notok@example.com" }), ENV_CAPTCHA);
  assert(res.status === 400 && (await res.json()).error === "Captcha verification failed",
    "secret without token → 400 (no bypass)");
  assert(findCall("/rest/v1/") === undefined && findCall("api.resend.com") === undefined,
    "missing token → zero insert/email");
}

// ── 8. Bad JSON / invalid email / GET → 400/400/405 with ACAO ─────────────
reset();
stubFetch(() => jsonResponse(201, [{ id: "x" }]));
{
  const bad = new Request("http://x", { method: "POST", headers: { Origin: ORIGIN, "Content-Type": "application/json" }, body: "not-json" });
  const res = await handle(bad, ENV);
  assert(res.status === 400 && res.headers.get("access-control-allow-origin"), "bad JSON → 400 + ACAO");

  const inv = await handle(post({ email: "not-an-email" }), ENV);
  assert(inv.status === 400 && inv.headers.get("vary") === "Origin", "invalid email → 400 + Vary");

  const get = new Request("http://x", { method: "GET", headers: { Origin: ORIGIN } });
  const gres = await handle(get, ENV);
  assert(gres.status === 405 && gres.headers.get("access-control-allow-origin"), "GET → 405 + ACAO");
}

// ── 9. Origin allowlist ────────────────────────────────────────────────────
reset();
stubFetch(() => jsonResponse(201, [{ id: "x" }]));
{
  const foreign = post({ email: "x@y.co" }, { origin: "https://evil.example.com" });
  const res = await handle(foreign, ENV);
  assert(res.status === 403, "non-allowlisted Origin → 403");
  assert(res.headers.get("access-control-allow-origin") !== "https://evil.example.com",
    "403 never echoes the request origin");
  assert(res.headers.get("access-control-allow-origin") !== null, "403 still carries ACAO");

  const opt = new Request("http://x", { method: "OPTIONS", headers: { Origin: "https://evil.example.com" } });
  const ores = await handle(opt, ENV);
  assert(ores.status === 403, "OPTIONS from non-allowlisted Origin → 403");

  const bare = post({ email: "bare@pages.dev" }, { origin: "https://premise-labs.pages.dev" });
  const bres = await handle(bare, ENV);
  assert(bres.status === 200, "bare pages.dev apex allowed");

  const noOrigin = post({ email: "curl@example.com" }, { origin: null });
  const cres = await handle(noOrigin, ENV);
  assert(cres.status === 200, "no Origin (curl) allowed");
}

// ── 10. Type-confused fields → clean 400 (no uncaught TypeError) ──────────
reset();
stubFetch(() => jsonResponse(201, [{ id: "x" }]));
{
  const bad = post({ email: 123 });
  const res = await handle(bad, ENV);
  assert(res.status === 400 && res.headers.get("access-control-allow-origin"),
    "email: 123 → 400 + ACAO");
  const bad2 = post({ email: [], hp: {} });
  const res2 = await handle(bad2, ENV);
  assert(res2.status === 400, "email: [] / hp: {} → 400");
  assert(calls.length === 0, "type-confused inputs → zero outbound calls");
}

// ── 11. Per-email rate limit (email-bomb guard) ───────────────────────────
reset();
stubFetch(() => jsonResponse(201, [{ id: "x" }]));
{
  let last = 0;
  for (let i = 0; i < 6; i++) {
    const req = post({ email: "bomb@example.com" });
    last = (await handle(req, ENV)).status;
  }
  assert(last === 429, "6th submission for same email → 429");
}

// ── 12. Supabase failure → 500; oversize body → 400 ───────────────────────
reset();
stubFetch(() => { throw new Error("supabase down"); });
{
  const res = await handle(post({ email: "fail@example.com" }), ENV);
  assert(res.status === 500 && res.headers.get("access-control-allow-origin"), "supabase reject → 500 + ACAO");
}
reset();
stubFetch(() => jsonResponse(201, [{ id: "x" }]));
{
  const big = post({ email: "big@example.com", pad: "x".repeat(9000) });
  const res = await handle(big, ENV);
  assert(res.status === 400 && res.headers.get("access-control-allow-origin"), "oversize body → 400 + ACAO");
}

console.log(failures === 0 ? `\n✅ All ${"waitlist"} handler tests passed` : `\n❌ ${failures} assertion(s) failed`);
process.exit(failures === 0 ? 0 : 1);
