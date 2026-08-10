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
  // Only accept POST from Supabase Auth webhooks
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  try {
    // Read the RAW body first — the hook signature covers the raw bytes,
    // so the body must be verified before it is parsed/re-serialized.
    const rawBody = await req.text();

    // Caller auth (#802): signed auth-hook request OR user JWT matching
    // the provisioning target. The public anon key is NOT sufficient.
    const caller = await authenticateCaller(req, rawBody);
    if (!caller) {
      return new Response(
        JSON.stringify({
          error:
            "Unauthorized: expected a signed Supabase auth-hook request or " +
            "a user JWT matching the provisioning target",
        }),
        { status: 401, headers: { "Content-Type": "application/json" } }
      );
    }

    let body: HookPayload;
    try {
      body = JSON.parse(rawBody) as HookPayload;
    } catch {
      return new Response(
        JSON.stringify({ error: "invalid JSON body" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

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
      return new Response(
        JSON.stringify({
          error:
            "Forbidden: provisioning target does not match the " +
            "authenticated caller",
        }),
        { status: 403, headers: { "Content-Type": "application/json" } }
      );
    }

    // Identity comes from the VERIFIED caller, not the (possibly forged)
    // body fields.
    const user_id = caller.id;
    const email = caller.email;
    const display_name = caller.display_name || body.display_name || undefined;

    // Validate user_id is a UUID before provisioning (avoids orphaned
    // FalkorDB namespaces when called manually with a malformed payload).
    const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!UUID_RE.test(user_id)) {
      return new Response(
        JSON.stringify({ error: "invalid user_id format" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    // Generate team name from provider display name, fallback to email prefix
    const rawName = display_name || email.split("@")[0];
    const teamName = rawName
      .toLowerCase()
      .replace(/[^a-zA-Z0-9_-]/g, "-")
      .replace(/^-+|-+$/g, "")
      .substring(0, 64) || "user";

    // Ensure starts with alphanumeric per Team.name regex
    const safeName = /^[a-zA-Z0-9]/.test(teamName) ? teamName : `u-${teamName}`;

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
      return new Response(
        JSON.stringify({ error: "Provisioning misconfigured (FASTAPI_URL missing). Please contact support." }),
        { status: 500, headers: { "Content-Type": "application/json" } }
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
    const pepper = Deno.env.get("TORTOISE_SECRET_PEPPER") || "";
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
      return new Response(
        JSON.stringify({ error: "Provisioning failed. Please try again." }),
        { status: 502, headers: { "Content-Type": "application/json" } }
      );
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
    await fetch(`${fastApiUrl}/internal/demo`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${fastApiKey}`,
      },
      body: JSON.stringify({ team_id: teamId }),
      signal: AbortSignal.timeout(10_000),
    }).catch((e) => console.error("Demo seed failed:", e));

    const response: ProvisionResponse = {
      team_id: teamId,
      team_name: safeName,
      api_key: apiKey,
      // Must match the value upserted into team_memberships above (team_${teamId}).
      graph_name: `team_${teamId}`,
    };

    return new Response(JSON.stringify(response), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });

  } catch (err) {
    console.error("tenant-provision error:", err);
    return new Response(
      JSON.stringify({ error: "Internal server error" }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
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
