#!/usr/bin/env node
/**
 * Mock Supabase Auth for local BFF testing. No npm dependencies.
 *
 * Emulates exactly the surface the BFF uses:
 *   GET  /auth/v1/.well-known/jwks.json   -> real ES256 JWKS (so verification is
 *                                            exercised for real, not stubbed)
 *   POST /auth/v1/token?grant_type=pkce   -> validates the PKCE verifier
 *   POST /auth/v1/token?grant_type=refresh_token
 *   POST /auth/v1/verify                  -> token_hash completion
 *
 * The PKCE verifier check is real: it recomputes S256 and compares. A mock that
 * accepted any verifier would make the end-to-end test worthless, because the
 * whole point is that the verifier is server-held.
 */
import { createServer } from "node:http";
import { createHash, generateKeyPairSync, sign } from "node:crypto";

const PORT = Number(process.env.MOCK_PORT ?? 8791);

// --- real ES256 keypair, exposed as a JWKS ------------------------------------
const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
const jwk = publicKey.export({ format: "jwk" });
const JWKS = { keys: [{ ...jwk, kid: "mock-key-1", alg: "ES256", use: "sig" }] };

const b64url = (buf) =>
  Buffer.from(buf).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/**
 * Injectable faults, so tests can induce real failures instead of asserting a
 * happy path under a 503-shaped name. A test that cannot reach the failure branch
 * is not guarding the property it documents.
 *
 *   authUser   -> GET/POST /auth/v1/user returns 500 (provider outage)
 *   upstream   -> /v1/* returns 500 (the proxied API is down)
 *   upstream401-> /v1/* returns 401 (the upstream rejected our credential,
 *                 while the SESSION itself is still valid — the case that must
 *                 NOT be forwarded verbatim)
 *   refreshDead-> refresh_token grant returns 401 invalid_grant (the session is
 *                 genuinely dead)
 */
const FAULTS = { authUser: false, upstream: false, upstream401: false, refreshDead: false };

/**
 * blog_admins membership.
 *
 * The mock previously had no /rest/v1 route at all, so `checkAdmin` always fell
 * through its `!res.ok -> not_admin` branch on a 404 and the ADMIN branch was
 * never exercised anywhere in the suite. Default empty so "not an admin" stays
 * the default state; tests opt in via /__mock/blog-admin.
 */
const ADMIN_USER_IDS = new Set();

/**
 * Counts refresh_token grants actually performed.
 *
 * The token cache exists to avoid stampeding GoTrue's single-use rotating
 * refresh tokens. Asserting only the HTTP status cannot detect a stall:
 * "does not storm refreshes" is a statement about THIS counter, not about the
 * response code.
 */
let refreshGrants = 0;

function mintAccessToken(sub, expiresIn = 3600) {
  const header = b64url(JSON.stringify({ alg: "ES256", typ: "JWT", kid: "mock-key-1" }));
  const claims = b64url(
    JSON.stringify({ sub, exp: Math.floor(Date.now() / 1000) + expiresIn, role: "authenticated" }),
  );
  const input = `${header}.${claims}`;
  // Node's ECDSA output is DER; the JWS wire format is raw r||s. Convert.
  const der = sign("sha256", Buffer.from(input), privateKey);
  return `${input}.${b64url(derToRaw(der))}`;
}

/** DER SEQUENCE{INTEGER r, INTEGER s} -> 64-byte r||s */
function derToRaw(der) {
  let off = 2;
  if (der[1] & 0x80) off = 2 + (der[1] & 0x7f);
  if (der[off++] !== 0x02) throw new Error("bad DER");
  const rLen = der[off++];
  let r = der.subarray(off, off + rLen);
  off += rLen;
  if (der[off++] !== 0x02) throw new Error("bad DER");
  const sLen = der[off++];
  let s = der.subarray(off, off + sLen);
  const strip = (b) => (b[0] === 0 ? b.subarray(1) : b);
  r = strip(r);
  s = strip(s);
  const out = Buffer.alloc(64);
  r.copy(out, 32 - r.length);
  s.copy(out, 64 - s.length);
  return out;
}

// --- issued state -------------------------------------------------------------
const issuedRefresh = new Map(); // refresh_token -> sub
let refreshCounter = 0;
let verifyCounter = 0;

const json = (res, status, body) => {
  const payload = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(payload);
};

/** PKCE S256 check — the load-bearing assertion. */
function s256(verifier) {
  return b64url(createHash("sha256").update(verifier).digest());
}

function issue(sub) {
  const refresh_token = `mock-refresh-${++refreshCounter}`;
  issuedRefresh.set(refresh_token, sub);
  return {
    access_token: mintAccessToken(sub),
    refresh_token,
    expires_in: 3600,
    user: { id: sub, email: `${sub}@example.test` },
  };
}

const PKCE_CHALLENGES = new Map();
let authorizeCounter = 0;

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);

  // GET /auth/v1/authorize — the realistic redirect hop. Supabase stores the
  // PKCE challenge against the code it issues; the mock must do the same or the
  // end-to-end test would not actually exercise server-held verifier checking.
  if (url.pathname === "/auth/v1/authorize") {
    const code = `mock-code-${++authorizeCounter}`;
    const challenge = url.searchParams.get("code_challenge");
    const redirectTo = url.searchParams.get("redirect_to");
    if (!challenge || !redirectTo) return json(res, 400, { error: "missing_param" });
    PKCE_CHALLENGES.set(code, challenge);
    const dest = new URL(redirectTo);
    dest.searchParams.set("code", code);
    res.writeHead(302, { Location: dest.toString() });
    return res.end();
  }

  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    const parsed = body ? JSON.parse(body) : {};

    if (url.pathname === "/auth/v1/.well-known/jwks.json") {
      return json(res, 200, JWKS);
    }

    // GET /auth/v1/user — the profile endpoint /api/session consults, and the
    // legacy bearer path verifies against. Both must survive an outage.
    if (url.pathname === "/auth/v1/user") {
      if (FAULTS.authUser) return json(res, 500, { error: "injected_provider_outage" });
      const auth = req.headers.authorization ?? "";
      if (!/^Bearer\s+/.test(auth)) return json(res, 401, { error: "missing_token" });
      // Pull the subject out of the token we minted, so the profile corresponds
      // to the session rather than being a constant.
      let sub = "user-123";
      try {
        const claims = JSON.parse(Buffer.from(auth.split(" ")[1].split(".")[1], "base64url").toString());
        if (claims.sub) sub = claims.sub;
      } catch { /* keep the default */ }
      return json(res, 200, {
        id: sub,
        email: `${sub}@example.test`,
        user_metadata: { display_name: `Display ${sub}` },
      });
    }

    if (url.pathname === "/auth/v1/token") {
      const grant = url.searchParams.get("grant_type");

      if (grant === "pkce") {
        // Store the verifier against the challenge the BFF registered.
        const challenge = PKCE_CHALLENGES.get(parsed.auth_code);
        if (!challenge) return json(res, 401, { error: "invalid_grant", detail: "unknown code" });
        if (s256(parsed.code_verifier) !== challenge) {
          return json(res, 401, { error: "invalid_grant", detail: "PKCE verifier mismatch" });
        }
        PKCE_CHALLENGES.delete(parsed.auth_code);
        return json(res, 200, issue("user-123"));
      }

      if (grant === "refresh_token") {
        refreshGrants++;
        // A genuinely dead session: GoTrue's invalid_grant.
        if (FAULTS.refreshDead) return json(res, 401, { error_code: "invalid_grant" });
        const sub = issuedRefresh.get(parsed.refresh_token);
        if (!sub) return json(res, 401, { error: "invalid_grant" });
        return json(res, 200, issue(sub));
      }

      if (grant === "password") {
        return json(res, 200, issue("user-123"));
      }
      return json(res, 400, { error: "unsupported_grant" });
    }

    if (url.pathname === "/auth/v1/verify") {
      // Emulate the deprecated-value trap: `signup`/`magiclink` must be refused.
      if (parsed.type === "signup" || parsed.type === "magiclink") {
        return json(res, 400, { error: "invalid_type", detail: "deprecated verify type" });
      }
      if (!parsed.token_hash) return json(res, 401, { error: "invalid_token" });
      verifyCounter++;
      return json(res, 200, issue("user-456"));
    }

    // Upstream API echo for the W6 proxy tests: reports the credential the proxy
    // attached, so "did the server hold the token?" is observed, not assumed.
    if (url.pathname.startsWith("/v1/")) {
      if (FAULTS.upstream) return json(res, 500, { error: "injected_upstream_outage" });
      if (FAULTS.upstream401) return json(res, 401, { error: "injected_upstream_401" });
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({
        path: url.pathname,
        method: req.method,
        authorization: req.headers.authorization ?? null,
        cookieLeaked: (req.headers.cookie ?? "").includes("__Host-session"),
        echo: parsed && Object.keys(parsed).length ? parsed : null,
      }));
    }

    if (url.pathname === "/__mock/register-pkce") {
      PKCE_CHALLENGES.set(parsed.code, parsed.challenge);
      return json(res, 200, { ok: true });
    }

    // blog_admins membership — what `checkAdmin` queries.
    if (url.pathname === "/rest/v1/blog_admins") {
      const uid = (url.searchParams.get("user_id") ?? "").replace(/^eq\./, "");
      return json(res, 200, ADMIN_USER_IDS.has(uid) ? [{ user_id: uid }] : []);
    }

    // Fault injection control. POST {"authUser":true} / {"upstream":true} /
    // {"upstream401":true} / {"refreshDead":true}.
    if (url.pathname === "/__mock/fault") {
      for (const k of ["authUser", "upstream", "upstream401", "refreshDead"]) {
        if (typeof parsed[k] === "boolean") FAULTS[k] = parsed[k];
      }
      return json(res, 200, { ok: true, faults: { ...FAULTS } });
    }

    // Grant/revoke admin membership so both branches of checkAdmin are reachable.
    if (url.pathname === "/__mock/blog-admin") {
      if (parsed.userId) ADMIN_USER_IDS.add(String(parsed.userId));
      if (parsed.clear) ADMIN_USER_IDS.clear();
      return json(res, 200, { ok: true, admins: [...ADMIN_USER_IDS] });
    }

    // Observability the tests assert on: how many refresh grants happened.
    if (url.pathname === "/__mock/stats") {
      if (parsed.reset) refreshGrants = 0;
      return json(res, 200, { refreshGrants });
    }

    // Cross-origin cookie probe. Echoes what the browser actually sent, so the
    // "is a host-only __Host- cookie sent to a sibling origin?" question is
    // answered by observation rather than by argument.
    if (url.pathname === "/__mock/echo-cookie") {
      res.writeHead(200, {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "http://localhost:8993",
        "Access-Control-Allow-Credentials": "true",
      });
      return res.end(JSON.stringify({ cookie: req.headers.cookie ?? null }));
    }

    return json(res, 404, { error: "not_found", path: url.pathname });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`mock supabase listening on http://127.0.0.1:${PORT}\n`);
});
