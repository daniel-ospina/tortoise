// lookup_hash — instant key-lookup digest for the Supabase control plane
// (#669 plan P1-1). MUST stay byte-identical to tortoise/auth.py lookup_hash().
//
//   lookup_hash := SHA-256(pepper + key), hex-encoded (lowercase)
//
// Construction is "pepper FIRST, then key" — the plan's exact spelling
// ("SHA-256(pepper + key)"). The Python mirror lives in tortoise/auth.py and
// supabase/tests/lookup_parity.test.mjs locks both sides to the same test
// vectors. Do NOT change the order here without updating the mirror and the
// parity vectors.
//
// Pure module (no Deno APIs): runs in the Edge Function (Deno) and in the
// Node parity test (node's globalThis.crypto is WebCrypto-compatible).

/**
 * Compute the SHA-256 lookup hash for an API key.
 * @param key   the raw API key (e.g. "tt_...")
 * @param pepper the TORTOISE_SECRET_PEPPER value (app code, never the DB)
 * @returns lowercase hex digest of SHA-256(pepper + key)
 */
export async function lookupHash(key: string, pepper: string): Promise<string> {
  const bytes = new TextEncoder().encode(pepper + key);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
