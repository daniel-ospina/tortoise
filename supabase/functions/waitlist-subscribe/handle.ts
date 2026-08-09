// Waitlist subscription handler for the premiselabs.co landing page (#373).
//
// Pure module — ZERO imports (only globals fetch/Request/Response/URLSearchParams/
// AbortSignal). This keeps it runnable under `node --experimental-strip-types`
// for behavioral tests (tests/test_waitlist_subscribe.mjs) and inside the
// Deno edge runtime via the thin serve() wrapper in index.ts.
//
// Pipeline: method gate → CORS/origin allowlist → body cap + JSON parse →
// email validation → honeypot → IP rate limit → Turnstile (both-keys rule) →
// PostgREST insert with on_conflict dedup → best-effort Resend confirmation
// email (fresh inserts only, 2s timeout).

const ALLOWED_ORIGINS = [
  "https://premiselabs.co",
  "https://tortoise.premiselabs.co",
  "https://premise-labs.pages.dev",
];
const ORIGIN_SUFFIXES = [".premise-labs.pages.dev"];
const LOCAL_ORIGINS = ["http://localhost:8788", "http://127.0.0.1:8788"];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_BODY_BYTES = 8192; // Turnstile tokens can reach 2048 chars
const RATE_LIMIT_MAX = 10; // requests per IP per window
const RATE_LIMIT_WINDOW_MS = 60 * 60 * 1000; // 1 hour
const RESEND_TIMEOUT_MS = 2000;

const TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
const RESEND_API_URL = "https://api.resend.com/emails";

export type Env = Record<string, string | undefined>;

interface SubscribeBody {
  email?: string;
  hp?: string;
  "cf-turnstile-response"?: string;
}

// ── In-memory IP rate limit (best-effort baseline — Turnstile is the real
//    gate; documented as per-instance, keyed on the FIRST x-forwarded-for
//    entry, skipped when the header is absent so no global bucket forms) ──
const rateLimitHits = new Map<string, number[]>();

export function __resetRateLimit(): void {
  rateLimitHits.clear();
}

function rateLimited(ip: string, now: number): boolean {
  const hits = (rateLimitHits.get(ip) ?? []).filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
  if (hits.length >= RATE_LIMIT_MAX) {
    rateLimitHits.set(ip, hits);
    return true;
  }
  hits.push(now);
  rateLimitHits.set(ip, hits);
  // Opportunistic prune so the Map never grows unbounded in long-lived isolates
  for (const [key, times] of rateLimitHits) {
    if (times.every((t) => now - t >= RATE_LIMIT_WINDOW_MS)) rateLimitHits.delete(key);
  }
  return false;
}

function clientIp(req: Request): string | undefined {
  const xff = req.headers.get("x-forwarded-for");
  if (!xff) return undefined;
  return xff.split(",")[0].trim() || undefined;
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

// ── Turnstile verification (both-keys rule: verify only when the secret AND
//    a token are both present — no 400s on misconfiguration) ───────────────
async function verifyTurnstile(env: Env, token: string | undefined, ip: string | undefined): Promise<boolean> {
  const secret = env.TURNSTILE_SECRET_KEY;
  if (!secret || !token) return true; // skip (fail-open)
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

  // Body cap + JSON parse (read once)
  let raw: string;
  try {
    raw = await req.text();
  } catch {
    return json({ error: "Invalid request" }, 400, corsOrigin);
  }
  if (raw.length > MAX_BODY_BYTES) {
    return json({ error: "Request too large" }, 400, corsOrigin);
  }
  let body: SubscribeBody;
  try {
    body = JSON.parse(raw) as SubscribeBody;
  } catch {
    return json({ error: "Invalid JSON body" }, 400, corsOrigin);
  }

  // Email validation
  const email = (body.email ?? "").trim().toLowerCase();
  if (!EMAIL_RE.test(email)) {
    return json({ error: "Invalid email address" }, 400, corsOrigin);
  }

  // Honeypot — filled by bots; silent success, nothing stored, no email
  if (body.hp && body.hp.trim() !== "") {
    return json({ ok: true }, 200, corsOrigin);
  }

  // IP rate limit (best-effort)
  const ip = clientIp(req);
  if (ip && rateLimited(ip, Date.now())) {
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

  // Turnstile (both-keys rule)
  const captchaOk = await verifyTurnstile(env, body["cf-turnstile-response"], ip);
  if (!captchaOk) {
    return json({ error: "Captcha verification failed" }, 400, corsOrigin);
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
