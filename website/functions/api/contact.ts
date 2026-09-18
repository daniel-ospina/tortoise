/**
 * POST /api/contact — the public website contact form.
 *
 * DELIVERY TARGET IS SETTLED (owner ruling, issue #2409, 2026-09-18):
 *   "for outside product should be hello@ for inside product should be support@"
 * The website is the outside-product surface, so this function delivers to
 * `hello@premiselabs.co` — the same address the legal pages already name as the
 * designated channel (privacy §1, DPA §15, ToS §15.5, aviso-privacidad). The
 * recipient is a CODE CONSTANT, never a request field: that is what keeps this
 * form from being an open relay.
 *
 * ⚠️  THE TRANSPORT IS NOT SETTLED (pending owner decision, #2409). This file
 * therefore knows NOTHING about how delivery happens: no provider name, no
 * credential variable, no endpoint, no wire format. The actual send lives
 * behind ONE seam, `deliver()` in `../_shared/contact-transport.ts`, whose
 * payload and result types are provider-neutral; swapping transport is a
 * change to that module alone. This file's job is the transport-independent
 * half: validation, honeypot, rate limit, cross-site refusal — and mapping the
 * seam's outcome to HTTP.
 *
 * FAIL LOUDLY WHEN UNCONFIGURED. If the transport is unconfigured the seam
 * reports `not_configured` and this function answers a visible 503 the visitor
 * can act on — never a 200 that drops the message on the floor. The contact
 * page surfaces the 503 message, which points at the direct mailto fallback.
 *
 * This response is built here, not by `_headers`: Cloudflare does NOT apply
 * `_headers` rules to Pages Function responses, so HSTS is stamped manually
 * (same contract as functions/_middleware.ts).
 */

import { deliver } from "../_shared/contact-transport";

/** The Pages `env`, handed to the transport seam untouched. */
interface ContactEnv {
  [key: string]: unknown;
}

/** Owner-decided destination for the outside-product surface. Not configurable. */
const CONTACT_TO = "hello@premiselabs.co";
const MAX_NAME = 100;
const MAX_EMAIL = 254;
const MAX_MESSAGE = 5000;
/** Body cap — a public endpoint should not buffer an arbitrary payload. */
const MAX_BODY_BYTES = 16_384;
/** Basic abuse protection: N submissions per IP per window, per isolate. */
const RATE_LIMIT = 5;
const RATE_WINDOW_MS = 10 * 60 * 1000;
const MAX_RATE_KEYS = 5000;

const HSTS = { "Strict-Transport-Security": "max-age=31536000; includeSubDomains" };

/**
 * Per-isolate sliding-window rate limit.
 *
 * HONEST SCOPE: this state lives in the isolate, so it is best-effort, not a
 * global limit — Cloudflare may run several isolates and recycle them freely.
 * It is deliberately not called a guarantee: the honeypot and the validation
 * are the always-on layers; this raises the cost of a naive loop. A binding
 * (KV/Durable Object) would make it global; the project carries neither today,
 * and inventing one is not this change's job.
 */
const hits = new Map<string, number[]>();

function rateLimited(ip: string, now: number): boolean {
  const cutoff = now - RATE_WINDOW_MS;
  const recent = (hits.get(ip) || []).filter((t) => t > cutoff);
  if (recent.length >= RATE_LIMIT) {
    hits.set(ip, recent);
    return true;
  }
  recent.push(now);
  // Re-insert so the current key is the MOST RECENT in Map insertion order —
  // otherwise the eviction below could drop the caller's own history and the
  // limit would be bypassable by flooding distinct keys.
  hits.delete(ip);
  hits.set(ip, recent);
  if (hits.size > MAX_RATE_KEYS) {
    // Bound the map by KEY COUNT. A cap that only removed expired entries would
    // bound nothing under sustained traffic from many addresses, and would then
    // run a full O(n) rebuild on every request. Evicting expired keys first is
    // the cheap win; then oldest-first until under the cap. An evicted live key
    // loses its history, which at this scale is an accepted tradeoff — the
    // point of the cap is that memory cannot grow without bound.
    for (const [k, v] of hits) {
      if (v.every((t) => t <= cutoff)) hits.delete(k);
    }
    let excess = hits.size - MAX_RATE_KEYS;
    for (const k of hits.keys()) {
      if (excess <= 0) break;
      hits.delete(k);
      excess--;
    }
  }
  return false;
}

/**
 * Is this request a cross-site browser submission?
 *
 * A CORS-simple cross-site `<form>` post is sent WITHOUT a preflight, so the
 * implicit protection that covers the JSON path (an un-granted preflight, since
 * `OPTIONS` returns no `Access-Control-Allow-Origin`) does not cover it: any
 * third-party page could drive submissions from its visitors' IPs, each a fresh
 * rate-limit key. `Sec-Fetch-Site` is the direct signal; `Origin` is the
 * fallback for older browsers. A request with neither (curl, a server, a
 * same-origin post) is allowed — that is the debug/CLI path, and refusing it
 * would make the endpoint untestable.
 */
function isCrossSite(request: Request): boolean {
  if (request.headers.get("Sec-Fetch-Site") === "cross-site") return true;
  const origin = request.headers.get("Origin");
  if (!origin) return false;
  try {
    return new URL(origin).host !== (request.headers.get("Host") || "");
  } catch {
    return true; // unparseable Origin is not a same-origin one
  }
}

function json(body: Record<string, unknown>, status: number, extra: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...HSTS,
      ...extra,
    },
  });
}

function fail(status: number, error: string, message: string, extra: Record<string, string> = {}) {
  return json({ ok: false, error, message }, status, extra);
}

/** Strip control characters (incl. CR/LF) — the header-injection defence. */
function singleLine(s: string): string {
  // eslint-disable-next-line no-control-regex
  return s.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
}

/**
 * An address this form is willing to name as `Reply-To`.
 *
 * Rejecting control characters is not cosmetic: an unvalidated value here is a
 * header-injection primitive (a CR/LF in the address can append headers or a
 * second recipient). The recipient itself is fixed above, so this is the only
 * address a submitter can influence.
 */
function validEmail(raw: string): boolean {
  if (raw.length === 0 || raw.length > MAX_EMAIL) return false;
  if (/[\u0000-\u0020\u007f]/.test(raw)) return false;
  // One @, a non-empty local part, a dotted domain, no empty labels.
  if (!/^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/.test(raw)) return false;
  return true;
}

async function readPayload(request: Request): Promise<
  { ok: true; data: Record<string, unknown> } | { ok: false; status: number; error: string; message: string }
> {
  const raw = (request.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
  const declared = Number(request.headers.get("content-length") || "0");
  if (declared > MAX_BODY_BYTES) {
    return { ok: false, status: 413, error: "payload_too_large", message: "That message is too large to submit." };
  }
  if (raw === "application/json") {
    let text: string;
    try {
      text = await request.text();
    } catch {
      return { ok: false, status: 400, error: "invalid_body", message: "We could not read that submission." };
    }
    if (text.length > MAX_BODY_BYTES) {
      return { ok: false, status: 413, error: "payload_too_large", message: "That message is too large to submit." };
    }
    try {
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return { ok: false, status: 400, error: "invalid_body", message: "We could not read that submission." };
      }
      return { ok: true, data: parsed as Record<string, unknown> };
    } catch {
      return { ok: false, status: 400, error: "invalid_body", message: "We could not read that submission." };
    }
  }
  // The no-JS fallback path: a plain <form> post. Kept working so the page
  // degrades instead of dumping a 415 JSON blob on a visitor.
  if (raw === "application/x-www-form-urlencoded" || raw === "multipart/form-data") {
    try {
      const fd = await request.formData();
      const data: Record<string, unknown> = {};
      for (const [k, v] of fd.entries()) data[k] = typeof v === "string" ? v : "";
      return { ok: true, data };
    } catch {
      return { ok: false, status: 400, error: "invalid_body", message: "We could not read that submission." };
    }
  }
  return {
    ok: false,
    status: 415,
    error: "unsupported_media_type",
    message: "Send that as a form submission or JSON.",
  };
}

async function handlePost(request: Request, env: ContactEnv): Promise<Response> {
  // Cross-site browser submissions are refused outright — the honeypot and the
  // rate limit are only meaningful for traffic that cannot pick its own key.
  if (isCrossSite(request)) {
    return fail(403, "cross_site_blocked", "This form only accepts submissions from pages on this site.");
  }

  const payload = await readPayload(request);
  if (!payload.ok) return fail(payload.status, payload.error, payload.message);

  const body = payload.data;

  // ── Honeypot ───────────────────────────────────────────────────────────
  // The `hp` field is hidden from humans and invisible to a screen reader
  // (aria-hidden + off-screen in contact.html). Anything in it is a bot. The
  // response is a plain success on purpose: telling a bot it was detected only
  // teaches it to leave the field empty.
  if (typeof body.hp === "string" && body.hp.trim() !== "") {
    return json({ ok: true, message: "Thanks — your message is on its way." }, 200);
  }

  const name = singleLine(typeof body.name === "string" ? body.name : "");
  const email = (typeof body.email === "string" ? body.email : "").trim();
  const message = (typeof body.message === "string" ? body.message : "").trim();

  const problems: string[] = [];
  if (name.length === 0 || name.length > MAX_NAME) problems.push("a name (up to 100 characters)");
  if (!validEmail(email)) problems.push("a valid reply-to email address");
  if (message.length === 0 || message.length > MAX_MESSAGE) problems.push("a message (up to 5000 characters)");
  if (problems.length > 0) {
    return fail(400, "invalid_input", `Please provide ${problems.join(", ")}.`);
  }

  // ── Rate limit (after validation: a malformed payload costs nothing) ────
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (rateLimited(ip, Date.now())) {
    return fail(
      429,
      "rate_limited",
      "Too many messages from this connection. Please wait a few minutes, or email hello@premiselabs.co directly.",
      { "Retry-After": String(Math.ceil(RATE_WINDOW_MS / 1000)) },
    );
  }

  // ── Delivery (the one transport seam) ───────────────────────────────────
  // Everything provider-specific lives behind `deliver()`; this file only maps
  // its provider-neutral outcome to HTTP. The gate that decides
  // `not_configured` lives INSIDE the seam (it is transport-specific), and it
  // precedes the send there, so a silent success on the unconfigured path is
  // structurally impossible.
  const outcome = await deliver({ to: CONTACT_TO, replyTo: email, name, message }, env);

  if (outcome.status === "not_configured") {
    // Visible and actionable, and honest about the cause. The visitor gets the
    // fallback; the missing variable's name is operator-facing, logged by the
    // seam, never shown here.
    return fail(
      503,
      "not_configured",
      "Email delivery is not configured on this deployment, so your message was not sent. " +
        "Please email hello@premiselabs.co directly — we are sorry for the detour.",
    );
  }

  if (outcome.status === "failed") {
    return fail(
      502,
      "delivery_failed",
      "We could not deliver your message, so it was not sent. Please try again, or email hello@premiselabs.co directly.",
    );
  }

  return json({ ok: true, message: "Thanks — your message is on its way. We'll reply to the address you gave." }, 200);
}

/**
 * ONE entry point. Cloudflare Pages' precedence between a catch-all
 * `onRequest` and a method-specific export is the kind of thing two readers
 * disagree about, so this file exports exactly one handler and dispatches by
 * method itself — no precedence to get wrong.
 */
export const onRequest: PagesFunction<ContactEnv> = async ({ request, env }) => {
  if (request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: { Allow: "POST, OPTIONS", ...HSTS },
    });
  }
  if (request.method !== "POST") {
    return fail(405, "method_not_allowed", "This endpoint accepts POST only.", { Allow: "POST, OPTIONS" });
  }
  return handlePost(request, env);
};
