// Tenant provisioning Edge Function
// Triggered on auth.users INSERT via the Supabase Auth `after_user_created`
// webhook. Writes the master list into Supabase ONLY — teams +
// team_memberships + api_keys in ONE atomic transaction via the
// provision_team SECURITY DEFINER RPC (migration 0010, #669 plan Task 2 /
// #770), then seeds the team's demo knowledge graph via the FastAPI data
// plane (/internal/demo — FalkorDB knowledge graphs stay untouched; the
// control_plane registry graph is NEVER written here: E2E-1).
//
// ── CALLER AUTH (#802) ─────────────────────────────────────────────────
// The function MUST be deployed with verify_jwt=false (--no-verify-jwt):
// Supabase Auth hooks carry NO user JWT — the platform signs the hook
// request itself with the hook secret (Standard Webhooks / Svix signature).
// Because of that the function verifies the caller itself, and the public
// anon key alone is NOT sufficient to mint teams/API keys:
//
//   1. Auth-hook calls → raw-body Standard-Webhooks signature verified
//      against AUTH_HOOK_SECRET (the secret configured on the
//      after_user_created hook; format `v1,whsec_...`, generated in
//      Dashboard → Authentication → Hooks). See Supabase docs
//      "Auth Hooks → Send email hook" for the canonical pattern.
//   2. Direct calls → must present a USER JWT (Authorization: Bearer)
//      whose identity (id + email) matches the user_id/email being
//      provisioned — a user can only provision FOR THEMSELVES.
//
// Anything else → 401. Missing/unverifiable signature fails CLOSED
// (mirrors the FASTAPI_URL / TORTOISE_SECRET_PEPPER fail-closed pattern).
// NOTE: the CORS origin gate (see below) runs BEFORE this auth block:
// browser requests from non-allowlisted origins are rejected 403 "Origin not
// allowed" without reaching auth; server-side callers with no Origin (auth
// hook, curl) skip the gate and hit exactly the 401/403 rules written here.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { Webhook } from "https://esm.sh/standardwebhooks@1.0.0";
import { lookupHash } from "../_shared/lookup.ts";

// Supabase Auth hook payload: { metadata: {...}, user: { id, email, user_metadata, ... } }
// Also tolerates a direct { user_id, email, display_name } payload for manual testing.
interface HookPayload {
  metadata?: Record<string, unknown>;
  user?: {
    id: string;
    email: string;
    user_metadata?: { display_name?: string; [k: string]: unknown };
    [k: string]: unknown;
  };
  user_id?: string;
  email?: string;
  display_name?: string;
  // #2323 (Option B): wizard-typed org name for name-first provisioning.
  team_name?: string;
}

// Caller identity established by authentication (hook signature or JWT).
interface CallerIdentity {
  id: string;
  email: string;
  display_name?: string;
}

interface ProvisionResponse {
  team_id: string;
  team_name: string;
  api_key: string;
  graph_name: string;
}

// Env var holding the after_user_created hook secret (v1,whsec_...).
// Set via: supabase secrets set --project-ref ybetwichurajbfswfeqa \
//   AUTH_HOOK_SECRET='v1,whsec_...'   (value from Dashboard → Auth → Hooks)
const AUTH_HOOK_SECRET_ENV = "AUTH_HOOK_SECRET";

// ── CORS / origin allowlist ─────────────────────────────────────────────
// The welcome page (website/welcome.html) calls this function DIRECTLY from
// the browser via the JWT path (#527/#802): fetch(PROVISION_URL, { method:
// "POST", headers: { Authorization: Bearer <jwt>, Content-Type: json } }).
// A non-simple Content-Type forces a CORS preflight (OPTIONS), so without
// CORS headers the browser blocks EVERY signup with "No
// 'Access-Control-Allow-Origin' header" (production incident 2026-08-13).
//
// ALLOWED_ORIGINS + ORIGIN_SUFFIXES are kept identical to waitlist-subscribe
// (parity test: tests/test_provisioning_edge_function.py — change both
// together). LOCAL_ORIGINS handling deliberately DIVERGES: tenant-provision
// accepts ANY localhost/127.0.0.1 port because welcome.html's `isLocal`
// treats every local port as local (wrangler --port, python http.server...),
// while waitlist keeps a fixed 8788. Accepting any local port is safe: minting
// still requires the caller's OWN valid JWT + identity match (#802), and a
// local attacker page cannot read another local origin's storage to obtain it.
//
// The after_user_created auth-hook path (Path 2) is currently INERT in
// production: #832 (2026-08-10) removed AUTH_HOOK_SECRET from Supabase
// secrets, so Path 2 fails CLOSED (401) — the JWT path is the only live
// consumer. The CORS gate treats a MISSING Origin as allowed (server-side
// callers: auth hook, curl, tests), and the literal Origin: "null" sent by
// sandboxed iframes is NOT allowlisted → 403 fail-closed.
const ALLOWED_ORIGINS = [
  "https://premiselabs.co",
  "https://tortoise.premiselabs.co",
  "https://app.premiselabs.co",
  "https://premise-labs.pages.dev",
];
const ORIGIN_SUFFIXES = [".premise-labs.pages.dev"];
// Any-port localhost/127.0.0.1 (see divergence note above).
const LOCAL_ORIGIN_RE = /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/;

function originAllowed(origin: string | null): string | null {
  if (!origin) return null; // no Origin (auth hook, curl, server-side) → treated as allowed
  if (ALLOWED_ORIGINS.includes(origin) || LOCAL_ORIGIN_RE.test(origin)) return origin;
  for (const suffix of ORIGIN_SUFFIXES) {
    if (origin.endsWith(suffix)) return origin;
  }
  return null;
}

// Single response helper — ACAO + Vary: Origin on EVERY path so the browser
// can read success AND error bodies from allowlisted origins. Never echo an
// un-allowlisted request origin back (would grant CORS to arbitrary sites);
// server-side callers with no Origin get the first allowlisted origin. Note:
// a 403 to a NON-allowlisted origin is intentionally NOT browser-readable
// (ACAO is the static fallback) — the browser surfaces it as a CORS error,
// and the deny branches log the origin server-side for diagnosis.
function json(body: unknown, status: number, corsOrigin: string | null): Response {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  headers["Access-Control-Allow-Origin"] = corsOrigin ?? ALLOWED_ORIGINS[0];
  headers["Vary"] = "Origin";
  return new Response(JSON.stringify(body), { status, headers });
}

/**
 * Authenticate the caller. Returns the verified caller identity, or null
 * when the request cannot be authenticated (→ the handler answers 401).
 */
async function authenticateCaller(
  req: Request,
  rawBody: string
): Promise<CallerIdentity | null> {
  // ── Path 1: user JWT (direct callers) ────────────────────────────────
  // Verify the token against Supabase Auth. The identity match against the
  // provisioning target is enforced by the caller (see handler) so user A
  // cannot mint teams/keys for user B.
  const authz = req.headers.get("authorization") ?? "";
  if (authz.startsWith("Bearer ")) {
    const token = authz.slice("Bearer ".length).trim();
    if (!token) return null;
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SUPABASE_ANON_KEY") ?? "",
      { auth: { persistSession: false, autoRefreshToken: false } }
    );
    const { data, error } = await supabase.auth.getUser(token);
    if (error || !data.user?.id || !data.user.email) return null;
    return {
      id: data.user.id,
      email: data.user.email,
      display_name:
        (data.user.user_metadata as { display_name?: string } | undefined)
          ?.display_name,
    };
  }

  // ── Path 2: Supabase Auth hook (after_user_created) ─────────────────
  // GoTrue signs the raw body with the hook secret (Standard Webhooks:
  // webhook-id / webhook-timestamp / webhook-signature headers). Fail
  // CLOSED while the secret is unprovisioned so the function can never
  // fall back to trusting unsigned requests.
  const hookSecret = Deno.env.get(AUTH_HOOK_SECRET_ENV);
  if (!hookSecret) {
    console.error(
      "tenant-provision: AUTH_HOOK_SECRET is not set (#802). Configure the " +
        "after_user_created hook secret (Dashboard → Authentication → Hooks) " +
        "and run: supabase secrets set --project-ref ybetwichurajbfswfeqa " +
        "AUTH_HOOK_SECRET='v1,whsec_...'"
    );
    return null;
  }
  try {
    const webhook = new Webhook(hookSecret.replace(/^v1,whsec_/, ""));
    // Headers → plain object (webhook-signature / webhook-id / webhook-timestamp).
    const headers: Record<string, string> = {};
    req.headers.forEach((v, k) => { headers[k] = v; });
    const payload = webhook.verify(rawBody, headers) as HookPayload;
    const user = payload.user;
    if (!user?.id || !user.email) return null;
    return {
      id: user.id,
      email: user.email,
      display_name: user.user_metadata?.display_name,
    };
  } catch (err) {
    console.error("tenant-provision: hook signature verification failed:", err);
    return null;
  }
}

Deno.serve(async (req: Request) => {
  const corsOrigin = originAllowed(req.headers.get("origin"));
  const requestOrigin = req.headers.get("origin");

  // CORS preflight — respond BEFORE the method gate so the browser's
  // OPTIONS probe succeeds (a 405 on preflight blocks the POST too).
  if (req.method === "OPTIONS") {
    if (requestOrigin && !originAllowed(requestOrigin)) {
      console.error("tenant-provision: rejected origin not in allowlist:", requestOrigin);
      return json({ error: "Origin not allowed" }, 403, corsOrigin);
    }
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": corsOrigin ?? ALLOWED_ORIGINS[0],
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "authorization, content-type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
      },
    });
  }

  // Only accept POST (Supabase Auth webhooks / welcome-page JWT calls)
  if (req.method !== "POST") {
    return json({ error: "Method not allowed" }, 405, corsOrigin);
  }

  // Origin gate (browser requests carry Origin; auth hooks/curl omit it)
  if (requestOrigin && !originAllowed(requestOrigin)) {
    console.error("tenant-provision: rejected origin not in allowlist:", requestOrigin);
    return json({ error: "Origin not allowed" }, 403, corsOrigin);
  }

  try {
    // Read the RAW body first — the hook signature covers the raw bytes,
    // so the body must be verified before it is parsed/re-serialized.
    const rawBody = await req.text();

    // Caller auth (#802): signed auth-hook request OR user JWT matching
    // the provisioning target. The public anon key is NOT sufficient.
    const caller = await authenticateCaller(req, rawBody);
    if (!caller) {
      return json(
        {
          error:
            "Unauthorized: expected a signed Supabase auth-hook request or " +
            "a user JWT matching the provisioning target",
        },
        401, corsOrigin
      );
    }

    let body: HookPayload;
    try {
      body = JSON.parse(rawBody) as HookPayload;
    } catch {
      return json({ error: "invalid JSON body" }, 400, corsOrigin);
    }

    // Post-parse type guard (code-review P2, PR #1111 — ports waitlist's
    // #723 fix): JSON.parse("null") → null and type-confused fields
    // ({user_id: 123}) must answer a clean 400 with CORS, never throw into
    // the outer catch (500). The caller is already authenticated at this
    // point, so this is robustness, not security.
    const isObject = (v: unknown): v is Record<string, unknown> =>
      typeof v === "object" && v !== null;
    const isStr = (v: unknown): v is string => typeof v === "string";
    const parsed = JSON.parse(rawBody) as unknown;
    const parsedObj = isObject(parsed) ? parsed : null;
    const userField = parsedObj ? parsedObj.user : undefined;
    const bodyOk =
      parsedObj !== null &&
      (userField === undefined || isObject(userField)) &&
      (userField === undefined || userField.id === undefined || isStr(userField.id)) &&
      (userField === undefined || userField.email === undefined || isStr(userField.email)) &&
      (parsedObj.user_id === undefined || isStr(parsedObj.user_id)) &&
      (parsedObj.email === undefined || isStr(parsedObj.email)) &&
      (parsedObj.display_name === undefined || isStr(parsedObj.display_name)) &&
      (parsedObj.team_name === undefined || isStr(parsedObj.team_name));
    if (!bodyOk) {
      return json({ error: "invalid JSON body" }, 400, corsOrigin);
    }
    body = parsed as HookPayload;

    // The provisioning target must BE the authenticated caller. A signed
    // hook payload only ever names the user GoTrue just created, and a JWT
    // caller may only provision for themselves — user A cannot mint teams
    // or API keys for user B (#802).
    const targetId = (body.user?.id || body.user_id || "").toLowerCase();
    const targetEmail = (body.user?.email || body.email || "").toLowerCase();
    if (
      !targetId ||
      !targetEmail ||
      targetId !== caller.id.toLowerCase() ||
      targetEmail !== caller.email.toLowerCase()
    ) {
      return json(
        {
          error:
            "Forbidden: provisioning target does not match the " +
            "authenticated caller",
        },
        403, corsOrigin
      );
    }

    // Identity comes from the VERIFIED caller, not the (possibly forged)
    // body fields.
    const user_id = caller.id;
    const email = caller.email;
    // display_name may arrive type-confused from PROVIDER metadata
    // (user_metadata.display_name is unvalidated — e.g. a numeric value from
    // a social provider). Coerce to a string or drop it so the team-name
    // derivation falls back to the email prefix instead of crashing
    // rawName.toLowerCase() → 500 (issue #1132). body.display_name is
    // already string-guarded by the #1111 post-parse type guard.
    const display_name =
      (typeof caller.display_name === "string" ? caller.display_name : undefined) ||
      body.display_name ||
      undefined;

    // Validate user_id is a UUID before provisioning (avoids orphaned
    // FalkorDB namespaces when called manually with a malformed payload).
    const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!UUID_RE.test(user_id)) {
      return json({ error: "invalid user_id format" }, 400, corsOrigin);
    }

    // Generate team name from the wizard-typed name (#2323 Option B: name-first
    // provisioning — the org-create step is the provisioning door, so a fresh
    // user never sees a display-name phantom org), falling back to provider
    // display name / email prefix when the caller is an older client that sends
    // no team_name.
    // The override is validated here with the same regex POST
    // /v1/onboarding/team enforces; an invalid/absent override falls back
    // (never 500s) so a stale caller cannot regress. An override that passes
    // the regex needs NO further sanitization and keeps its case ("Acme" stays
    // "Acme" — code-review P3: the display-name fallback keeps lowercasing).
    const bodyTeamName =
      typeof body.team_name === "string" ? body.team_name.trim() : "";
    const TEAM_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/;
    let safeName = "";
    if (bodyTeamName && TEAM_NAME_RE.test(bodyTeamName)) {
      safeName = bodyTeamName;
    } else {
      const rawName = display_name || email.split("@")[0];
      const teamName = rawName
        .toLowerCase()
        .replace(/[^a-zA-Z0-9_-]/g, "-")
        .replace(/^-+|-+$/g, "")
        .substring(0, 64) || "user";
      // Ensure starts with alphanumeric per Team.name regex
      safeName = /^[a-zA-Z0-9]/.test(teamName) ? teamName : `u-${teamName}`;
    }

    // Generate a DETERMINISTIC team_id per user — SHA-256(user_id) truncated
    // to 26 hex chars. Retries (hook redelivery after a lost response, or a
    // direct-JWT re-invocation) must be true same-payload retries: a fresh
    // random id on every call would let provision_team's step-3 INSERT create
    // a SECOND team + membership + api_keys row (code-review P2, PR #847 —
    // the old update_user_team placeholder-only UPDATE could never duplicate,
    // so this is a new idempotency requirement introduced by the RPC).
    const teamIdDigest = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(user_id)
    );
    const teamId = Array.from(new Uint8Array(teamIdDigest))
      .map(b => b.toString(16).padStart(2, "0"))
      .join("")
      .substring(0, 26);

    // Generate API key
    const apiKeyBytes = new Uint8Array(32);
    crypto.getRandomValues(apiKeyBytes);
    const apiKeyHex = Array.from(apiKeyBytes)
      .map(b => b.toString(16).padStart(2, "0"))
      .join("");
    const apiKey = `tt_${apiKeyHex}`;

    // Call internal FastAPI to provision the namespace
    const fastApiUrl = Deno.env.get("FASTAPI_URL") || "";
    if (!fastApiUrl) {
      // localhost:8000 was the old default — it points at the edge
      // function's own container and threw uncaught (500 "Error invoking
      // hook"). Fail with a clear, diagnosable error instead (dogfood 2026-08-08).
      console.error("tenant-provision: FASTAPI_URL is not set in Supabase secrets");
      return json(
        { error: "Provisioning misconfigured (FASTAPI_URL missing). Please contact support." },
        500, corsOrigin
      );
    }
    const fastApiKey = Deno.env.get("FASTAPI_INTERNAL_KEY") || "";

    // Fail CLOSED on a missing pepper at the lookupHash call site (not just
    // via hashApiKey's own guard): without the pepper, lookup_hash would be
    // SHA-256(key) — a digest that can never match tortoise/auth.py's
    // lookup_hash() (real/dev pepper) → every provisioned key silently fails
    // Task 3 auth. Explicit guard so reordering the two calls can't regress
    // this (code-review P2, PR #847).
    const pepper = Deno.env.get("TORTOISE_SECRET_PEPPER") || "";
    if (!pepper) {
      throw new Error(
        "TORTOISE_SECRET_PEPPER is not set. Set it in Supabase secrets — " +
        "provisioned lookup_hash values cannot be verified without a stable pepper."
      );
    }
    // ── Fix #7852/#770: write the master list to Supabase in ONE atomic
    // transaction (teams + team_memberships + api_keys) via the
    // provision_team SECURITY DEFINER RPC (migration 0010). The RPC is
    // idempotent: it reconciles the on_auth_user_created placeholder row
    // (team_id='' / key_hash='pending') in place, so exactly one membership
    // row and one api_keys row exist per provisioned team (plan §4.1 step 6,
    // P2-5 concurrency contract). key_hash (salted PBKDF2, continuity) and
    // lookup_hash (SHA-256(pepper + key) — the auth lookup anchor, E2E-6)
    // are computed HERE, never in SQL (the pepper lives in app code only).
    //
    // Failure is FATAL now (unlike the old best-effort update_user_team
    // write): the FalkorDB registry is no longer written, so without this
    // transaction the team exists NOWHERE. The user can retry — the RPC is
    // idempotent.
    const keyHash = await hashApiKey(apiKey);
    const lookupHashHex = await lookupHash(apiKey, pepper);

    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
    );
    const { error: provisionError } = await supabase.rpc("provision_team", {
      p_user_id: user_id,
      p_identity: null,
      p_team_id: teamId,
      p_team_name: safeName,
      p_api_key: apiKey, // plaintext — shown once on welcome page, then nulled
      p_key_hash: keyHash,
      p_lookup_hash: lookupHashHex,
      p_graph_name: `team_${teamId}`,
      p_email: email,
      p_key_prefix: apiKey.slice(0, 10),
    });
    if (provisionError) {
      console.error(
        "provision_team failed for " + safeName + ":",
        provisionError
      );
      return json({ error: "Provisioning failed. Please try again." }, 502, corsOrigin);
    }

    // ── Fix #7854: Trigger demo graph seeding ──────────────────────────
    // Data plane ONLY: /internal/demo writes the team's knowledge graph
    // (FalkorDB, graph team_{team_id} — created on first write). It never
    // touches the control_plane registry graph (E2E-1: zero registry
    // writes). The old /internal/provision call is GONE — it wrote the
    // registry (Team/APIKey/Membership nodes), which the plan moves to
    // Supabase entirely. AbortSignal.timeout bounds the await so a slow
    // demo seed can never blow the hook deadline mid-retry (code-review P2,
    // PR #847 — the RPC has already committed at this point; the hook
    // redelivers the whole request, and deterministic team_id makes that a
    // harmless no-op).
    // #1860 (P3-1): bind the response — a non-2xx body used to resolve
    // "successfully" (the old .catch() only handled transport/abort), so a
    // failed seed left the first-timer's graph silently missing demo data.
    const demoRes = await fetch(`${fastApiUrl}/internal/demo`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${fastApiKey}`,
      },
      body: JSON.stringify({ team_id: teamId }),
      signal: AbortSignal.timeout(10_000),
    }).catch((e) => {
      console.error("Demo seed failed:", e);
      return null;
    });
    if (demoRes && !demoRes.ok) {
      // HTTP error (4xx/5xx) — the provision RPC already committed, so the
      // team exists but its graph lacks demo data. Log loudly (incl. the
      // error body, which also drains the connection back to the pool); do
      // NOT fail the whole provisioning (the user can still be onboarded).
      const demoErrBody = await demoRes.text().catch(() => "");
      console.error(
        "Demo seed failed: /internal/demo returned " + demoRes.status +
          (demoErrBody ? ": " + demoErrBody : "")
      );
    }

    const response: ProvisionResponse = {
      team_id: teamId,
      team_name: safeName,
      api_key: apiKey,
      // Must match the value upserted into team_memberships above (team_${teamId}).
      graph_name: `team_${teamId}`,
    };

    return json(response, 201, corsOrigin);

  } catch (err) {
    console.error("tenant-provision error:", err);
    return json({ error: "Internal server error" }, 500, corsOrigin);
  }
});

// PBKDF2-HMAC-SHA256 — MUST match tortoise/auth.py hash_api_key() exactly.
// auth.py: per-key 32-byte random salt + pepper mixed into KEY MATERIAL
// (not as salt), returns "salt_hex:hash_hex". If this diverges, provisioned
// API keys will never validate against the API (they hash differently).
async function hashApiKey(key: string): Promise<string> {
  const pepper = Deno.env.get("TORTOISE_SECRET_PEPPER") || "";
  // Fail fast if pepper is missing: without it, hashes can never match
  // auth.py (which either raises in prod or uses a dev-only pepper), so
  // every provisioned key would silently fail API auth.
  if (!pepper) {
    throw new Error(
      "TORTOISE_SECRET_PEPPER is not set. Set it in Supabase secrets — " +
      "provisioned API keys cannot be verified without a stable pepper."
    );
  }
  // Per-key 32-byte random salt (matches auth.py: secrets.token_bytes(32))
  const perKeySalt = crypto.getRandomValues(new Uint8Array(32));
  // Pepper mixed into key material: key.encode() + pepper (matches auth.py)
  const keyBytes = new TextEncoder().encode(key);
  const pepperBytes = new TextEncoder().encode(pepper);
  const keyMaterialBytes = new Uint8Array(keyBytes.length + pepperBytes.length);
  keyMaterialBytes.set(keyBytes, 0);
  keyMaterialBytes.set(pepperBytes, keyBytes.length);

  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    keyMaterialBytes,
    "PBKDF2",
    false,
    ["deriveBits"]
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt: perKeySalt,
      iterations: 100_000,
    },
    keyMaterial,
    256
  );
  const hashHex = Array.from(new Uint8Array(bits))
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
  const saltHex = Array.from(perKeySalt)
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
  return `${saltHex}:${hashHex}`;
}
