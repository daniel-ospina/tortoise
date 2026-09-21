/**
 * Archive Gate 1 spawn-test — end-to-end ES256 verification.
 *
 * Proves the ACTUAL code path the BFF will use, not merely that
 * `crypto.subtle.verify` exists. Generates a P-256 keypair, mints a JWT-shaped
 * token, and verifies it through the same `verifyEs256` the BFF will call.
 *
 * This is the falsifiable form of the gate: if P-256 ECDSA is unavailable or
 * the raw r||s signature format differs, this returns ok:false.
 */
import { verifyEs256, b64urlToBytes } from "./gate1";

function bytesToB64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export const onRequestGet: PagesFunction = async () => {
  const result: Record<string, unknown> = {};

  try {
    // 1. Real P-256 keypair, exactly as Supabase's ES256 JWKS provides.
    const pair = await crypto.subtle.generateKey(
      { name: "ECDSA", namedCurve: "P-256" },
      true,
      ["sign", "verify"],
    );
    const publicJwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
    result.jwkExported = !!publicJwk.x && !!publicJwk.y;
    result.jwkCurve = publicJwk.crv;

    // 2. Mint a JWT-shaped token: base64url(header).base64url(claims).
    const enc = new TextEncoder();
    const header = bytesToB64url(enc.encode(JSON.stringify({ alg: "ES256", typ: "JWT" })));
    const claims = bytesToB64url(
      enc.encode(JSON.stringify({
        sub: "spike-user",
        exp: Math.floor(Date.now() / 1000) + 3600,
      })),
    );
    const signingInput = `${header}.${claims}`;

    // ECDSA in WebCrypto emits raw r||s (64 bytes) — which IS the JWS ES256
    // wire format. No DER unwrapping needed. That is the load-bearing detail.
    const rawSig = new Uint8Array(await crypto.subtle.sign(
      { name: "ECDSA", hash: "SHA-256" },
      pair.privateKey,
      enc.encode(signingInput),
    ));
    result.signatureLength = rawSig.length;
    result.signatureIsRawRs = rawSig.length === 64;

    const jwt = `${signingInput}.${bytesToB64url(rawSig)}`;

    // 3. Verify via the exact function the BFF will use.
    const verified = await verifyEs256(jwt, publicJwk);
    result.verified = true;
    result.sub = verified.sub;

    // 4. Negative control — a tampered payload must FAIL. A verifier that only
    //    ever returns true is not a verifier.
    const tampered = `${header}.${bytesToB64url(enc.encode(JSON.stringify({
      sub: "attacker",
      exp: Math.floor(Date.now() / 1000) + 3600,
    })))}.${bytesToB64url(rawSig)}`;
    try {
      await verifyEs256(tampered, publicJwk);
      result.tamperRejected = false;
      result.verdict = "FAIL — tampered token verified";
    } catch {
      result.tamperRejected = true;
      result.verdict = "PASS — ES256 verify works, tampering rejected";
    }
  } catch (err) {
    result.verdict = "FAIL";
    result.error = err instanceof Error ? err.message : String(err);
  }

  return new Response(JSON.stringify(result, null, 2), {
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
};
