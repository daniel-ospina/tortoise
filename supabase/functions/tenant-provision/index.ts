// Tenant provisioning Edge Function
// Triggered on auth.users INSERT via Supabase Auth webhook
// Creates Team node in registry graph, provisions FalkorDB namespace, generates API key

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

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

interface ProvisionResponse {
  team_id: string;
  team_name: string;
  api_key: string;
  graph_name: string;
}

Deno.serve(async (req: Request) => {
  // Only accept POST from Supabase Auth webhooks
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  try {
    const body = await req.json() as HookPayload;

    // Auth hook sends user object nested under `user`; direct payloads send top-level fields.
    const user = body.user;
    const user_id = user?.id || body.user_id;
    const email = user?.email || body.email;
    const display_name =
      user?.user_metadata?.display_name || body.display_name || undefined;

    if (!user_id || !email) {
      return new Response(
        JSON.stringify({ error: "user_id and email required" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

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

    // Generate ULID for team_id
    const teamId = crypto.randomUUID().replace(/-/g, "").substring(0, 26);

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

    // Hash once, reuse for both consumers (each call mints a fresh salt —
    // calling twice would store two different hashes for the same key).
    const keyHash = await hashApiKey(apiKey);

    const provisionRes = await fetch(`${fastApiUrl}/internal/provision`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${fastApiKey}`,
      },
      body: JSON.stringify({
        team_id: teamId,
        team_name: safeName,
        api_key_hash: keyHash,
        created_by: user_id,
      }),
    });

    if (!provisionRes.ok) {
      const errText = await provisionRes.text();
      console.error(`Provision failed for ${safeName}: ${errText}`);
      return new Response(
        JSON.stringify({ error: "Provisioning failed. Please try again." }),
        { status: 502, headers: { "Content-Type": "application/json" } }
      );
    }

    // ── Fix #7852: Write plaintext API key to team_memberships ─────────────
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
    );
    // M:N placeholder semantics (plan §4.1 step 6): the on_auth_user_created
    // trigger pre-inserted a placeholder row (team_id='', key_hash='pending').
    // Under uq_member_team (user_id, team_id), a direct upsert keyed on user_id
    // would fail (no user_id-unique constraint) and an (user_id, team_id) upsert
    // would create a PHANTOM second row. The update_user_team RPC updates the
    // placeholder row (WHERE user_id = X AND team_id = '') and flips team_id to
    // the real value in the same statement — exactly one membership row.
    const { error: userTeamsError } = await supabase.rpc("update_user_team", {
      p_user_id: user_id,
      p_team_id: teamId,
      p_team_name: safeName,
      p_api_key: apiKey,  // plaintext — shown once on welcome page
      p_key_hash: keyHash,
      p_graph_name: `team_${teamId}`,
    });
    if (userTeamsError) {
      console.error("Failed to write team_memberships:", userTeamsError);
      // Don't fail the whole provisioning — the key is already in the response
    }

    // ── Fix #7854: Trigger demo graph seeding ──────────────────────────
    // (demo-404 fix: the FastAPI route is /internal/demo, NOT /v1/internal/demo)
    await fetch(`${fastApiUrl}/internal/demo`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${fastApiKey}`,
      },
      body: JSON.stringify({ team_id: teamId }),
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
