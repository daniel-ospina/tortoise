// Waitlist subscription handler for the premiselabs.co landing page (#373).
//
// Pure module — ZERO imports (only globals fetch/Request/Response/URLSearchParams/
// AbortSignal/TextEncoder). This keeps it runnable under `node --experimental-strip-types`
// for behavioral tests (tests/test_waitlist_subscribe.mjs) and inside the
// Deno edge runtime via the thin serve() wrapper in index.ts.
//
// Pipeline: method gate → CORS/origin allowlist → body cap + JSON parse →
// email validation → honeypot → IP + per-email rate limits → Turnstile
// (secret set ⇒ token REQUIRED) → PostgREST insert with on_conflict dedup →
// best-effort Resend confirmation email (fresh inserts only, 2s timeout).

const ALLOWED_ORIGINS = [
  "https://premiselabs.co",
  "https://tortoise.premiselabs.co",
  "https://app.premiselabs.co",
  "https://premise-labs.pages.dev",
];
const ORIGIN_SUFFIXES = [".premise-labs.pages.dev"];
const LOCAL_ORIGINS = ["http://localhost:8788", "http://127.0.0.1:8788"];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_BODY_BYTES = 8192; // Turnstile tokens can reach 2048 chars
const RATE_LIMIT_MAX = 10; // requests per IP per window (best-effort baseline)
const RATE_LIMIT_WINDOW_MS = 60 * 60 * 1000; // 1 hour
const EMAIL_RATE_LIMIT_MAX = 5; // submissions per email per window (email-bomb guard)
const RESEND_TIMEOUT_MS = 2000;

const TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
const RESEND_API_URL = "https://api.resend.com/emails";

export type Env = Record<string, string | undefined>;

interface SubscribeBody {
  email?: unknown;
  hp?: unknown;
  "cf-turnstile-response"?: string;
}

// ── In-memory rate limits (best-effort, per-isolate; Turnstile is the real
//    gate once configured). IP keyed on the LAST x-forwarded-for entry (the
//    value appended by the platform gateway — the first entry is
//    client-suppliable/spoofable). Also a per-email cap so an attacker cannot
//    email-bomb arbitrary third-party addresses or burn Resend quota. ──
const rateLimitHits = new Map<string, number[]>();
const emailRateLimitHits = new Map<string, number[]>();

export function __resetRateLimit(): void {
  rateLimitHits.clear();
  emailRateLimitHits.clear();
}

function limited(map: Map<string, number[]>, key: string, max: number, now: number): boolean {
  const hits = (map.get(key) ?? []).filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
  if (hits.length >= max) {
    map.set(key, hits);
    return true;
  }
  hits.push(now);
  map.set(key, hits);
  for (const [k, times] of map) {
    if (times.every((t) => now - t >= RATE_LIMIT_WINDOW_MS)) map.delete(k);
  }
  // Bound memory in long-lived isolates: rotating keys must not grow the map.
  if (map.size > 10_000) {
    const nowMs = now;
    for (const [k, times] of map) {
      if (times.every((t) => nowMs - t >= RATE_LIMIT_WINDOW_MS)) map.delete(k);
    }
    if (map.size > 10_000) map.clear();
  }
  return false;
}

function clientIp(req: Request): string | undefined {
  const xff = req.headers.get("x-forwarded-for");
  if (!xff) return undefined;
  const entries = xff.split(",").map((s) => s.trim()).filter(Boolean);
  // Last entry = appended by the gateway (trusted); first = client-controlled.
  return entries.length > 0 ? entries[entries.length - 1] : undefined;
}

// ── CORS / origin handling ─────────────────────────────────────────────────
function originAllowed(origin: string | null): string | null {
  if (!origin) return null; // no Origin (curl, server-side) → treated as allowed
  if (ALLOWED_ORIGINS.includes(origin) || LOCAL_ORIGINS.includes(origin)) return origin;
  for (const suffix of ORIGIN_SUFFIXES) {
    if (origin.endsWith(suffix)) return origin;
  }
  return null;
}

// Single response helper — ACAO + Vary: Origin on EVERY path so the browser
// can always read error bodies (400/403/405/429/500).
function json(body: unknown, status: number, corsOrigin: string | null): Response {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  // Never echo an un-allowlisted request origin back (would grant CORS to
  // arbitrary sites); non-browser callers with no Origin get the first
  // allowlisted origin so their errors are still readable.
  headers["Access-Control-Allow-Origin"] = corsOrigin ?? ALLOWED_ORIGINS[0];
  headers["Vary"] = "Origin";
  return new Response(JSON.stringify(body), { status, headers });
}

// ── Resend confirmation email (best-effort, awaited 2s timeout — isolates
//    can freeze after the response, so fire-and-forget would drop sends) ──
async function sendConfirmationEmail(
  env: Env,
  email: string,
): Promise<void> {
  if (!env.RESEND_API_KEY) {
    console.error("RESEND_API_KEY not configured — skipping confirmation email");
    return;
  }
  const from = env.RESEND_FROM_EMAIL || "Premise Labs <noreply@premiselabs.co>";
  const html = [
    "<p>Thanks for joining the Premise Labs waitlist.</p>",
    "<p>We'll let you know when Tortoise is ready for you — no spam, and you can",
    ' <a href="mailto:hello@premiselabs.co?subject=Unsubscribe%20from%20waitlist">unsubscribe</a>',
    " at any time.</p>",
  ].join("");
  try {
    await fetch(RESEND_API_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from,
        to: [email],
        subject: "You're on the Premise Labs launch list",
        html,
      }),
      signal: AbortSignal.timeout(RESEND_TIMEOUT_MS),
    });
  } catch (err) {
    // Row is the source of truth — never fail the submission on email error.
    console.error("Confirmation email failed:", err);
  }
}

// ── Turnstile verification. Fail-open ONLY on operator misconfiguration
//    (secret unset). When the secret IS configured, a token is REQUIRED —
//    an attacker omitting the token must not bypass the captcha. ───────────
async function verifyTurnstile(env: Env, token: string | undefined, ip: string | undefined): Promise<boolean> {
  const secret = env.TURNSTILE_SECRET_KEY;
  if (!secret) return true; // operator misconfiguration — skip (logged at call site)
  if (!token) return false; // secret set but no token → require the captcha
  try {
    const params = new URLSearchParams({ secret, response: token });
    if (ip) params.set("remoteip", ip);
    const res = await fetch(TURNSTILE_VERIFY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString(),
    });
    const result = await res.json();
    return result?.success === true;
  } catch (err) {
    console.error("Turnstile verification error:", err);
    return false;
  }
}

// ── Main handler ───────────────────────────────────────────────────────────
export async function handle(req: Request, env: Env): Promise<Response> {
  const corsOrigin = originAllowed(req.headers.get("origin"));
  const requestOrigin = req.headers.get("origin");

  // CORS preflight
  if (req.method === "OPTIONS") {
    if (requestOrigin && !originAllowed(requestOrigin)) {
      return json({ error: "Origin not allowed" }, 403, corsOrigin);
    }
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": corsOrigin ?? ALLOWED_ORIGINS[0],
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
        "Vary": "Origin",
      },
    });
  }

  // Method gate
  if (req.method !== "POST") {
    return json({ error: "Method not allowed" }, 405, corsOrigin);
  }

  // Origin gate (browser requests carry Origin; curl/server-side omit it)
  if (requestOrigin && !originAllowed(requestOrigin)) {
    return json({ error: "Origin not allowed" }, 403, corsOrigin);
  }

  // Body cap + JSON parse (read once; byte-accurate cap via TextEncoder)
  let raw: string;
  try {
    raw = await req.text();
  } catch {
    return json({ error: "Invalid request" }, 400, corsOrigin);
  }
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
    return json({ error: "Request too large" }, 400, corsOrigin);
  }
  let body: SubscribeBody;
  try {
    body = JSON.parse(raw) as SubscribeBody;
  } catch {
    return json({ error: "Invalid JSON body" }, 400, corsOrigin);
  }
  // JSON.parse("null"|"42"|"[]") yields non-object values — property access
  // on null throws; guard before coercion (400 with CORS, never a bare 500).
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return json({ error: "Invalid JSON body" }, 400, corsOrigin);
  }

  // Email validation — guard non-string types before coercion (a type-confused
  // field must 400 cleanly with CORS, not throw an uncaught TypeError)
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!EMAIL_RE.test(email)) {
    return json({ error: "Invalid email address" }, 400, corsOrigin);
  }

  // Honeypot — filled by bots; silent success, nothing stored, no email.
  // Non-string hp values are also bots (a JSON-literate bot can send any type).
  const hp = typeof body.hp === "string"
    ? body.hp.trim()
    : (body.hp === undefined ? "" : "BOT");
  if (hp !== "") {
    return json({ ok: true }, 200, corsOrigin);
  }

  // Rate limits (best-effort). IP limit keyed on the gateway-appended XFF
  // entry and checked BEFORE Turnstile (protects the verify endpoint); the
  // per-email limit is checked AFTER captcha so a captcha-free attacker
  // cannot burn a victim's email quota without solving the challenge.
  const ip = clientIp(req);
  const now = Date.now();
  if (ip && limited(rateLimitHits, `ip:${ip}`, RATE_LIMIT_MAX, now)) {
    return new Response(JSON.stringify({ error: "Too many requests" }), {
      status: 429,
      headers: {
        "Content-Type": "application/json",
        "Retry-After": "3600",
        "Access-Control-Allow-Origin": corsOrigin ?? ALLOWED_ORIGINS[0],
        "Vary": "Origin",
      },
    });
  }

  // Turnstile — when the secret is configured, a valid token is REQUIRED
  const token = typeof body["cf-turnstile-response"] === "string"
    ? body["cf-turnstile-response"]
    : undefined;
  const captchaOk = await verifyTurnstile(env, token, ip);
  if (!captchaOk) {
    return json({ error: "Captcha verification failed" }, 400, corsOrigin);
  }

  // Per-email limit (after captcha): guards email-bombing third parties +
  // Resend quota burn on genuinely passing submissions.
  if (limited(emailRateLimitHits, `email:${email}`, EMAIL_RATE_LIMIT_MAX, now)) {
    return new Response(JSON.stringify({ error: "Too many requests" }), {
      status: 429,
      headers: {
        "Content-Type": "application/json",
        "Retry-After": "3600",
        "Access-Control-Allow-Origin": corsOrigin ?? ALLOWED_ORIGINS[0],
        "Vary": "Origin",
      },
    });
  }

  // PostgREST insert with dedup. on_conflict is a URL QUERY PARAM; the Prefer
  // header carries both the ignore-duplicates resolution and return=representation
  // so an ignored conflict returns [] (the duplicate discriminator).
  const supabaseUrl = env.SUPABASE_URL;
  const serviceKey = env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceKey) {
    console.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured");
    return json({ error: "Server configuration error" }, 500, corsOrigin);
  }

  let inserted = false;
  try {
    const res = await fetch(
      `${supabaseUrl}/rest/v1/waitlist_subscribers?on_conflict=email`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "apikey": serviceKey,
          "Authorization": `Bearer ${serviceKey}`,
          "Prefer": "return=representation, resolution=ignore-duplicates",
        },
        body: JSON.stringify({ email, source: "landing_page" }),
      },
    );
    const text = await res.text();
    let parsed: unknown = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = null;
    }
    if (res.ok && Array.isArray(parsed) && parsed.length > 0) {
      inserted = true; // fresh insert
    } else if (res.ok) {
      // [] / empty = duplicate (ignore-duplicates) — already subscribed
      return json({ ok: true, message: "Already subscribed" }, 200, corsOrigin);
    } else {
      console.error("Supabase insert failed:", res.status, text.slice(0, 300));
      return json({ error: "Failed to subscribe" }, 500, corsOrigin);
    }
  } catch (err) {
    console.error("Supabase insert error:", err);
    return json({ error: "Failed to subscribe" }, 500, corsOrigin);
  }

  // Confirmation email — fresh inserts only (duplicates never re-fire)
  if (inserted) {
    await sendConfirmationEmail(env, email);
  }

  return json({ ok: true }, 200, corsOrigin);
}
