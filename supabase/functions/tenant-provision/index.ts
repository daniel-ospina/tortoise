// Tenant provisioning Edge Function
// Triggered on auth.users INSERT via Supabase Auth webhook
// Creates Team node in registry graph, provisions FalkorDB namespace, generates API key

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

interface ProvisionRequest {
  user_id: string;
  email: string;
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
    const { user_id, email, display_name } = await req.json() as ProvisionRequest;

    if (!user_id || !email) {
      return new Response(
        JSON.stringify({ error: "user_id and email required" }),
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
    const fastApiUrl = Deno.env.get("FASTAPI_URL") || "http://localhost:8000";
    const fastApiKey = Deno.env.get("FASTAPI_INTERNAL_KEY") || "";

    const provisionRes = await fetch(`${fastApiUrl}/internal/provision`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${fastApiKey}`,
      },
      body: JSON.stringify({
        team_id: teamId,
        team_name: safeName,
        api_key_hash: await hashApiKey(apiKey),
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

    // ── Fix #7852: Write plaintext API key to user_teams ───────────────
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
    );
    const { error: userTeamsError } = await supabase.from("user_teams").insert({
      user_id: user_id,
      team_id: teamId,
      team_name: safeName,
      api_key: apiKey,  // plaintext — shown once on welcome page
      graph_name: `team_${teamId}`,
    });
    if (userTeamsError) {
      console.error("Failed to write user_teams:", userTeamsError);
      // Don't fail the whole provisioning — the key is already in the response
    }

    // ── Fix #7854: Trigger demo graph seeding ──────────────────────────
    await fetch(`${fastApiUrl}/v1/internal/demo`, {
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
      graph_name: `team_${safeName}`,
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

// SHA-256 hash with optional pepper (mirrors tortoise/auth.py)
async function hashApiKey(key: string): Promise<string> {
  const pepper = Deno.env.get("TORTOISE_SECRET_PEPPER") || "";
  const data = new TextEncoder().encode(key);

  if (pepper) {
    // PBKDF2-HMAC-SHA256 with pepper
    const pepperKey = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(pepper),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    );
    const signature = await crypto.subtle.sign("HMAC", pepperKey, data);
    return Array.from(new Uint8Array(signature))
      .map(b => b.toString(16).padStart(2, "0"))
      .join("");
  }

  // Plain SHA-256 (no pepper)
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash))
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
}
