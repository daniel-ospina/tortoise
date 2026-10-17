// Deck-view edge function — records the address a reader gives to open a gated
// deck (the /investors page).
//
// A VIEW record, not a subscription: the row lands in `deck_views` and nothing
// is emailed. Joining the mailing list remains a separate, explicit act on the
// same page — the "Stay in touch" form posts to waitlist-subscribe — so a
// reader who traded an address for access is not put on a list they did not
// join.
//
// Pipeline: method gate → CORS/origin allowlist → body cap + JSON parse →
// email validation → honeypot → IP rate limit → PostgREST insert.
//
// Zero imports, like the waitlist handler: the module has to be importable
// under `node --experimental-strip-types` so tests/test_deck_view.mjs can
// exercise it without a Deno runtime.

const ALLOWED_ORIGINS = [
  "https://premiselabs.co",
  "https://tortoise.premiselabs.co",
  "https://app.premiselabs.co",
  "https://premise-labs.pages.dev",
];
const ORIGIN_SUFFIXES = [".premise-labs.pages.dev"];
const LOCAL_ORIGINS = ["http://localhost:8788", "http://127.0.0.1:8788"];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_BODY_BYTES = 8192; // Body-size guard; the real payload is {email, hp, deck}
// A deck name is written into the row an operator reads, so the client picks
// from this list rather than naming its own; an unknown value falls back.
const ALLOWED_DECKS = ["investors"];
const DEFAULT_DECK = "investors";
// Looser than the waitlist's 10/hour: opening a deck is cheaper than a
// subscribe and a reader may legitimately reopen it from a few devices. Still
// bounded, because every accepted call writes a row.
const RATE_LIMIT_MAX = 30;
const RATE_LIMIT_WINDOW_MS = 60 * 60 * 1000;

export type Env = Record<string, string | undefined>;

interface DeckViewBody {
  email?: unknown;
  hp?: unknown;
  deck?: unknown;
}

// ── In-memory rate limit (best-effort, per-isolate). IP keyed on the LAST
//    x-forwarded-for entry — the value the platform gateway appends; the first
//    entry is client-suppliable. ──
const rateLimitHits = new Map<string, number[]>();

export function __resetRateLimit(): void {
  rateLimitHits.clear();
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
    for (const [k, times] of map) {
      if (times.every((t) => now - t >= RATE_LIMIT_WINDOW_MS)) map.delete(k);
    }
    if (map.size > 10_000) map.clear();
  }
  return false;
}

function clientIp(req: Request): string | undefined {
  const xff = req.headers.get("x-forwarded-for");
  if (!xff) return undefined;
  const parts = xff.split(",").map((p) => p.trim()).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : undefined;
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

function json(body: unknown, status: number, corsOrigin: string | null): Response {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  // Never echo an un-allowlisted request origin back (that would grant CORS to
  // arbitrary sites); non-browser callers with no Origin get the first
  // allowlisted origin so their errors stay readable.
  headers["Access-Control-Allow-Origin"] = corsOrigin ?? ALLOWED_ORIGINS[0];
  headers["Vary"] = "Origin";
  return new Response(JSON.stringify(body), { status, headers });
}

// ── Main handler ───────────────────────────────────────────────────────────
export async function handle(req: Request, env: Env): Promise<Response> {
  const requestOrigin = req.headers.get("origin");
  const corsOrigin = originAllowed(requestOrigin);

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
  let body: DeckViewBody;
  try {
    body = JSON.parse(raw) as DeckViewBody;
  } catch {
    return json({ error: "Invalid JSON body" }, 400, corsOrigin);
  }
  // JSON.parse("null"|"42"|"[]") yields non-objects — property access on null
  // throws; guard before coercion (400 with CORS, never a bare 500).
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return json({ error: "Invalid JSON body" }, 400, corsOrigin);
  }

  // Email validation — guard non-string types before coercion.
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!EMAIL_RE.test(email)) {
    return json({ error: "Invalid email address" }, 400, corsOrigin);
  }

  // Deck name — coerced rather than validated-with-a-400, for the same reason
  // the waitlist coerces its source: an unrecognised value is a client bug, not
  // a reason to refuse a real reader.
  const deck = typeof body.deck === "string" && ALLOWED_DECKS.includes(body.deck)
    ? body.deck
    : DEFAULT_DECK;

  // Honeypot — filled by bots; silent success, nothing stored. Non-string hp
  // values are also bots (a JSON-literate bot can send any type).
  const hp = typeof body.hp === "string"
    ? body.hp.trim()
    : (body.hp === undefined ? "" : "BOT");
  if (hp !== "") {
    return json({ ok: true }, 200, corsOrigin);
  }

  // Rate limit (best-effort, per-isolate). Checked before the write so an
  // attacker cannot use the endpoint as a free table-filler.
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

  const supabaseUrl = env.SUPABASE_URL;
  const serviceKey = env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceKey) {
    console.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured");
    return json({ error: "Server configuration error" }, 500, corsOrigin);
  }

  try {
    const res = await fetch(`${supabaseUrl}/rest/v1/deck_views`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": serviceKey,
        "Authorization": `Bearer ${serviceKey}`,
        "Prefer": "return=minimal",
      },
      body: JSON.stringify({ email, deck }),
    });
    if (!res.ok) {
      const text = await res.text();
      console.error("deck_views insert failed:", res.status, text);
      return json({ error: "Could not record the view" }, 500, corsOrigin);
    }
  } catch (err) {
    console.error("deck_views insert error:", err);
    return json({ error: "Could not record the view" }, 500, corsOrigin);
  }

  return json({ ok: true }, 200, corsOrigin);
}
