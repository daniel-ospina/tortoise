/**
 * JWT / JWKS verification — the TS replacement for `tortoise/session_auth.py`.
 *
 * No npm dependencies, no `nodejs_compat`. Proven in Archive Gate 1:
 * WebCrypto ECDSA emits raw `r||s` (64 bytes), which IS the JWS ES256 wire
 * format — so no DER unwrapping is needed.
 *
 * Properties carried over from `session_auth.py`:
 *   - ES256 only; shared-HMAC (`HS256`) is REJECTED — a symmetric secret that
 *     can verify can also forge.
 *   - JWKS is fetched and cached; key rotation is tolerated by re-fetching on an
 *     unknown `kid`.
 *   - Failures are typed so callers can distinguish "bad token" from
 *     "verifier unavailable" — conflating those IS the #3485 bug class.
 */

export type VerifyFailure =
  | { kind: "malformed"; detail: string }
  | { kind: "unsupported_alg"; detail: string }
  | { kind: "unknown_kid"; detail: string }
  | { kind: "bad_signature" }
  | { kind: "expired" }
  | { kind: "unavailable"; detail: string };

export interface VerifiedClaims {
  sub: string;
  exp: number;
  [k: string]: unknown;
}

const JWKS_TTL_MS = 10 * 60 * 1000;

interface CachedJwks {
  keys: JsonWebKey[];
  fetchedAt: number;
}

let jwksCache: CachedJwks | null = null;

export function __resetJwksCache() {
  jwksCache = null;
}

/** Base64url -> bytes. `atob` is native, so no Buffer / nodejs_compat. */
export function b64urlToBytes(s: string): Uint8Array {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=");
  const raw = atob(b64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export function bytesToB64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function decodeJson<T>(segment: string): T {
  return JSON.parse(new TextDecoder().decode(b64urlToBytes(segment))) as T;
}

async function fetchJwks(supabaseUrl: string): Promise<JsonWebKey[]> {
  if (jwksCache && Date.now() - jwksCache.fetchedAt < JWKS_TTL_MS) {
    return jwksCache.keys;
  }
  const res = await fetch(`${supabaseUrl}/auth/v1/.well-known/jwks.json`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`jwks fetch failed: ${res.status}`);
  const body = (await res.json()) as { keys?: JsonWebKey[] };
  const keys = body.keys ?? [];
  jwksCache = { keys, fetchedAt: Date.now() };
  return keys;
}

/**
 * Verify a Supabase access token locally.
 *
 * Returns a discriminated result rather than throwing, so the caller can tell
 * `unavailable` (-> 503, terminal) from `bad_signature` (-> 401, not signed in).
 */
export async function verifyAccessToken(
  token: string,
  supabaseUrl: string,
): Promise<{ ok: true; claims: VerifiedClaims } | { ok: false; failure: VerifyFailure }> {
  const parts = token.split(".");
  if (parts.length !== 3) return { ok: false, failure: { kind: "malformed", detail: "not a JWT" } };
  const [h, p, sig] = parts as [string, string, string];

  let header: { alg?: string; kid?: string };
  let claims: VerifiedClaims & { exp?: number };
  try {
    header = decodeJson(h);
    claims = decodeJson(p);
  } catch (e) {
    return {
      ok: false,
      failure: { kind: "malformed", detail: e instanceof Error ? e.message : "decode failed" },
    };
  }

  // Reject symmetric algorithms outright — this is a security property, not a
  // compatibility preference.
  if (!header.alg || !header.alg.startsWith("ES")) {
    return { ok: false, failure: { kind: "unsupported_alg", detail: String(header.alg) } };
  }
  if (header.alg !== "ES256") {
    return { ok: false, failure: { kind: "unsupported_alg", detail: String(header.alg) } };
  }

  let keys: JsonWebKey[];
  try {
    keys = await fetchJwks(supabaseUrl);
  } catch (e) {
    // Distinguishing this from a bad token is the whole point.
    return {
      ok: false,
      failure: { kind: "unavailable", detail: e instanceof Error ? e.message : "jwks error" },
    };
  }

  // Prefer the key matching `kid`; on a miss, force a re-fetch once (rotation).
  let candidates = keys.filter((k) => !header.kid || k.kid === header.kid);
  if (candidates.length === 0) {
    jwksCache = null;
    try {
      keys = await fetchJwks(supabaseUrl);
    } catch (e) {
      return {
        ok: false,
        failure: { kind: "unavailable", detail: e instanceof Error ? e.message : "jwks error" },
      };
    }
    candidates = keys.filter((k) => !header.kid || k.kid === header.kid);
    if (candidates.length === 0) {
      return { ok: false, failure: { kind: "unknown_kid", detail: String(header.kid) } };
    }
  }

  const signingInput = new TextEncoder().encode(`${h}.${p}`);
  const signature = b64urlToBytes(sig);

  for (const jwk of candidates) {
    try {
      const key = await crypto.subtle.importKey(
        "jwk",
        { ...jwk, alg: "ES256", ext: true },
        { name: "ECDSA", namedCurve: "P-256" },
        false,
        ["verify"],
      );
      const good = await crypto.subtle.verify(
        { name: "ECDSA", hash: "SHA-256" },
        key,
        signature,
        signingInput,
      );
      if (!good) continue;
      if (typeof claims.exp === "number" && claims.exp * 1000 <= Date.now()) {
        return { ok: false, failure: { kind: "expired" } };
      }
      if (typeof claims.sub !== "string") {
        return { ok: false, failure: { kind: "malformed", detail: "missing sub" } };
      }
      return { ok: true, claims: claims as VerifiedClaims };
    } catch {
      continue;
    }
  }

  return { ok: false, failure: { kind: "bad_signature" } };
}
