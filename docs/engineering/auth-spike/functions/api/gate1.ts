/**
 * Archive Gate 1 spike — the dependency-free BFF primitives.
 *
 * NOT production code. This exists to falsify one claim:
 *   "The BFF needs `@supabase/ssr` + `nodejs_compat` to verify a session."
 *
 * It performs the three things W1 actually requires, using ONLY built-in
 * Workers/Pages globals (fetch, WebCrypto, D1 binding) — no npm imports:
 *   1. Verify an ES256 JWT locally via WebCrypto (replaces session_auth.py's
 *      Python-only JWKS verifier).
 *   2. Exchange a PKCE code via Supabase's REST API (replaces @supabase/ssr's
 *      createServerClient).
 *   3. Read a D1 binding.
 *
 * If this bundles and runs, Archive Gate 1 is retired: the dependency it gated
 * is unnecessary, which also removes the package.json / nodejs_compat conflict.
 */

interface Env {
  SUPABASE_URL?: string;
  SUPABASE_ANON_KEY?: string;
  SESSIONS?: D1Database;
}

/** Base64url -> bytes. No Buffer, so no `nodejs_compat`. */
function b64urlToBytes(s: string): Uint8Array {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=");
  const raw = atob(b64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** (1) Verify an ES256 JWT against a JWKS-resolved public key. */
async function verifyEs256(jwt: string, jwk: JsonWebKey): Promise<{ sub: string; exp: number }> {
  const [h, p, sig] = jwt.split(".");
  if (!h || !p || !sig) throw new Error("malformed jwt");

  const header = JSON.parse(new TextDecoder().decode(b64urlToBytes(h)));
  if (header.alg !== "ES256") throw new Error(`unsupported alg: ${header.alg}`);

  const key = await crypto.subtle.importKey(
    "jwk",
    { ...jwk, alg: "ES256", ext: true },
    { name: "ECDSA", namedCurve: "P-256" },
    false,
    ["verify"],
  );

  const ok = await crypto.subtle.verify(
    { name: "ECDSA", hash: "SHA-256" },
    key,
    b64urlToBytes(sig),
    new TextEncoder().encode(`${h}.${p}`),
  );
  if (!ok) throw new Error("bad signature");

  const claims = JSON.parse(new TextDecoder().decode(b64urlToBytes(p)));
  if (typeof claims.exp === "number" && claims.exp * 1000 < Date.now()) {
    throw new Error("expired");
  }
  return claims;
}

/** (2) Server-side PKCE exchange. No SDK. */
async function exchangePkce(env: Env, code: string, verifier: string) {
  const res = await fetch(`${env.SUPABASE_URL}/auth/v1/token?grant_type=pkce`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: env.SUPABASE_ANON_KEY ?? "",
    },
    body: JSON.stringify({ auth_code: code, code_verifier: verifier }),
  });
  if (!res.ok) return { ok: false as const, status: res.status };
  const body = (await res.json()) as { access_token: string; refresh_token: string; user: { id: string } };
  return { ok: true as const, body };
}

/** (3) D1 session handle read — the opaque-handle shape from §8.1. */
async function readSession(env: Env, handle: string) {
  return env.SESSIONS!.prepare(
    "SELECT user_id, refresh_token, expires_at FROM sessions WHERE handle = ?1 AND revoked = 0",
  )
    .bind(handle)
    .first();
}

function readCookie(req: Request, name: string): string | null {
  const raw = req.headers.get("Cookie") ?? "";
  for (const part of raw.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return decodeURIComponent(v.join("="));
  }
  return null;
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const { request, env } = ctx;
  const out: Record<string, unknown> = { checks: {} };

  // Prove the primitives exist in this runtime — no npm, no nodejs_compat.
  (out.checks as Record<string, boolean>).webcrypto = typeof crypto?.subtle?.verify === "function";
  (out.checks as Record<string, boolean>).atob = typeof atob === "function";
  (out.checks as Record<string, boolean>).d1Binding = !!env.SESSIONS;
  (out.checks as Record<string, boolean>).fetch = typeof fetch === "function";

  const handle = readCookie(request, "__Host-session");
  if (handle && env.SESSIONS) {
    out.session = await readSession(env, handle);
  }
  out.hasSupabaseConfig = !!(env.SUPABASE_URL && env.SUPABASE_ANON_KEY);

  return new Response(JSON.stringify(out, null, 2), {
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
};

// Referenced so the bundler keeps them and typechecks the signatures.
export { verifyEs256, exchangePkce, b64urlToBytes };
