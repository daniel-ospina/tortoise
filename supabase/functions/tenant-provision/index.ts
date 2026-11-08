// Tenant provisioning Edge Function
// Reached SERVER-SIDE only, by the dashboard's same-origin BFF route
// `website/apps/dashboard/functions/api/provision.ts` (#3501/#4054): the
// browser posts to `/api/provision`, the BFF mints the caller's access token
// from the HttpOnly `__Host-session` cookie and attaches it here, and the
// browser never sees a token or a publicly-callable minting endpoint (#1120).
// Writes the master list into Supabase ONLY — organizations +
// org_memberships + api_keys in ONE atomic transaction via the
// provision_team SECURITY DEFINER RPC (migration 0010, #669 plan Task 2 /
// #770), then seeds the org's REAL starter graph via the FastAPI data
// plane (/internal/starter-seed — #2360 re-spec: the signing-up user as a
// Subject + their Organization as a Subject, connected memberOf; the old
// demo sample auto-seed is GONE — sample content is never auto-seeded
// into a fresh org, so the Overview digest never counts fake demo points
// as the user's own filings). FalkorDB knowledge graphs stay
// untouched by the registry; the control_plane registry graph is NEVER
// written here (E2E-1).
//
// ── CALLER AUTH (#802) ─────────────────────────────────────────────────
// The function is deployed with verify_jwt=false (--no-verify-jwt) so the
// gateway does not pre-empt the identity/rate logic below; the caller is
// verified HERE, and the public anon key alone is NOT sufficient to mint
// orgs/API keys:
//
//   A USER JWT (Authorization: Bearer) whose identity (id + email) matches
//   the user_id/email being provisioned — a user can only provision FOR
//   THEMSELVES. The dashboard BFF mints it server-side (#3501/#4054), so no
//   browser ever holds the credential it mints with.
//
// Anything else → 401. A missing/unverifiable token fails CLOSED (mirrors
// the FASTAPI_URL / TORTOISE_SECRET_PEPPER fail-closed pattern).
//
// ⛔ The `after_user_created` auth-hook path (AUTH_HOOK_SECRET /
// Standard-Webhooks signature) was DELETED by #1120. `after_user_created` is
// not a supported Supabase auth hook: the docs list Before User Created,
// Custom Access Token, Send SMS, Send Email, MFA Verification Attempt and
// Password Verification Attempt, and nothing that fires AFTER a user row is
// created. #839's "Error invoking hook" 500 was a platform-capability gap,
// not the configuration gap #802 assumed, so the path could never be
// re-enabled once #832 removed its secret.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { lookupHash } from "../_shared/lookup.ts";

// Provisioning payload: the Supabase-user shape `{ user: { id, email,
// user_metadata } }` the BFF forwards, plus the flat `{ user_id, email,
// display_name }` form kept for manual server-side testing.
interface ProvisionPayload {
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
  org_name?: string;
}

// Caller identity established by the verified user JWT (#802).
interface CallerIdentity {
  id: string;
  email: string;
  display_name?: string;
}

interface ProvisionResponse {
  org_id: string;
  org_name: string;
  api_key: string;
  graph_name: string;
}

// ── NO CORS SURFACE (#1120) ─────────────────────────────────────────────
// This function used to answer a CORS preflight and carry an origin
// allowlist, because the welcome page called it DIRECTLY from the browser
// (#527/#802, production incident 2026-08-13 / #1111). That caller no longer
// exists: welcome.html's provisioning pipeline is dead (#1566) and the
// dashboard reaches this function through the same-origin BFF, which STRIPS
// `Origin` before forwarding (`STRIP_REQUEST` in
// website/apps/dashboard/functions/api/provision.ts). So the allowlist, the
// preflight branch and every `Access-Control-*` header are GONE — #1120's
// target is zero CORS surface, i.e. no preflight to answer and no origin
// list to maintain. Do not reintroduce one: a browser caller is refused 403
// below, so a cross-origin client could never be a legitimate caller.

/** Every response goes through here — JSON, and nothing else. No CORS. */
function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Authenticate the caller. Returns the verified caller identity, or null
 * when the request cannot be authenticated (→ the handler answers 401).
 */
async function authenticateCaller(
  req: Request
): Promise<CallerIdentity | null> {
  // ── The only path: a user JWT (minted server-side by the dashboard BFF) ──
  // Verify the token against Supabase Auth. The identity match against the
  // provisioning target is enforced by the caller (see handler) so user A
  // cannot mint orgs/keys for user B.
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

  // No Authorization header at all, or a non-Bearer scheme: unauthenticated.
  return null;
}

Deno.serve(async (req: Request) => {
  const requestOrigin = req.headers.get("origin");

  // Method gate FIRST, and the only thing an OPTIONS probe ever gets: a plain
  // 405 with no `Access-Control-*` header anywhere, so a browser's CORS
  // preflight cannot succeed and the POST behind it never leaves the browser
  // (#1120 — zero CORS surface, nothing to answer a preflight with).
  //
  // ⛔ LOG THE REFUSAL HERE TOO. A browser client must send `Authorization` +
  // `Content-Type: application/json` (both non-safelisted), so it PREFLIGHTS
  // first — and the preflight is answered by THIS branch, never reaching the
  // origin gate below. Without this line the monitor's
  // `refused a browser-origin call` count (tenant-provision-monitor.yml) can
  // never fire for the class it watches (#1120 review F2): a client regressing
  // to a page-based call would produce zero log lines, and the #1110 shape — a
  // user discovers it — would be restored. An origin-less wrong-method call
  // (curl, a scanner on GET) stays unlogged, so it cannot page anyone.
  if (req.method !== "POST") {
    if (requestOrigin) {
      console.error(
        "tenant-provision: refused a browser-origin call:",
        requestOrigin,
        req.method
      );
    }
    const res = json({ error: "Method not allowed" }, 405);
    res.headers.set("Allow", "POST");
    return res;
  }

  // SERVER-ONLY invariant (#1120): a browser call carries an `Origin` header;
  // the dashboard BFF calls this server-side and strips it. Refusing an
  // origin-bearing request keeps "no browser caller" a property of the
  // endpoint rather than of the (now deleted) CORS headers — and it fails
  // CLOSED if a future client tries to call this from a page.
  if (requestOrigin) {
    console.error("tenant-provision: refused a browser-origin call:", requestOrigin);
    return json({ error: "Forbidden: this endpoint is server-only" }, 403);
  }

  try {
    // Read the RAW body first — the identity match below is decided on the
    // payload as sent, exactly as the caller serialized it.
    const rawBody = await req.text();

    // Caller auth (#802): a user JWT matching the provisioning target. The
    // public anon key is NOT sufficient.
    const caller = await authenticateCaller(req);
    if (!caller) {
      return json(
        {
          error:
            "Unauthorized: a user JWT matching the provisioning target is " +
            "required",
        },
        401
      );
    }

    let body: ProvisionPayload;
    try {
      body = JSON.parse(rawBody) as ProvisionPayload;
    } catch {
      return json({ error: "invalid JSON body" }, 400);
    }

    // Post-parse type guard (code-review P2, PR #1111 — ports waitlist's
    // #723 fix): JSON.parse("null") → null and type-confused fields
    // ({user_id: 123}) must answer a clean 400, never throw into the outer
    // catch (500). The caller is already authenticated at this point, so this
    // is robustness, not security.
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
      (parsedObj.org_name === undefined || isStr(parsedObj.org_name));
    if (!bodyOk) {
      return json({ error: "invalid JSON body" }, 400);
    }
    body = parsed as ProvisionPayload;

    // The provisioning target must BE the authenticated caller. The JWT
    // caller may only provision for themselves — user A cannot mint orgs
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
        403
      );
    }

    // Identity comes from the VERIFIED caller, not the (possibly forged)
    // body fields.
    const user_id = caller.id;
    const email = caller.email;
    // display_name may arrive type-confused from PROVIDER metadata
    // (user_metadata.display_name is unvalidated — e.g. a numeric value from
    // a social provider). Coerce to a string or drop it so the org-name
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
      return json({ error: "invalid user_id format" }, 400);
    }

    // Generate the org name from the wizard-typed name (#2323 Option B: name-first
    // provisioning — the org-create step is the provisioning door, so a fresh
    // user never sees a display-name phantom org), falling back to provider
    // display name / email prefix when the caller is an older client that sends
    // no org_name.
    // The override is validated here with the same regex POST
    // /v1/onboarding/team enforces; an invalid/absent override falls back
    // (never 500s) so a stale caller cannot regress. An override that passes
    // the regex needs NO further sanitization and keeps its case ("Acme" stays
    // "Acme" — code-review P3: the display-name fallback keeps lowercasing).
    const bodyOrgName =
      typeof body.org_name === "string" ? body.org_name.trim() : "";
    const ORG_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_ -]{0,63}$/;
    let safeName = "";
    if (bodyOrgName && ORG_NAME_RE.test(bodyOrgName)) {
      safeName = bodyOrgName;
    } else {
      const rawName = display_name || email.split("@")[0];
      const orgName = rawName
        .toLowerCase()
        .replace(/[^a-zA-Z0-9_-]/g, "-")
        .replace(/^-+|-+$/g, "")
        .substring(0, 64) || "user";
      // Ensure starts with alphanumeric per Organization.name regex
      safeName = /^[a-zA-Z0-9]/.test(orgName) ? orgName : `u-${orgName}`;
    }

    // Generate a DETERMINISTIC org_id per user — SHA-256(user_id) truncated
    // to 26 hex chars. Retries (hook redelivery after a lost response, or a
    // direct-JWT re-invocation) must be true same-payload retries: a fresh
    // random id on every call would let provision_team's step-3 INSERT create
    // a SECOND org + membership + api_keys row (code-review P2, PR #847 —
    // the old update_user_team placeholder-only UPDATE could never duplicate,
    // so this is a new idempotency requirement introduced by the RPC).
    const orgIdDigest = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(user_id)
    );
    const orgId = Array.from(new Uint8Array(orgIdDigest))
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
        500
      );
    }
    const fastApiKey = Deno.env.get("FASTAPI_INTERNAL_KEY") || "";

    // #2406: one-time onboarding-call offer email — fires AFTER the demo seed
    // and INDEPENDENTLY of it (its own loop + error handling; a demo-seed
    // failure never suppresses the email and vice versa). Two attempts (0s /
    // +2s), bounded, logs every non-definitive outcome, and NEVER throws —
    // provisioning has already committed; a failed offer email must never
    // fail the signup. display_name is the PERSON display name (caller/body),
    // never safeName (the org slug) — the greeting heuristic is server-side.
    async function fireOnboardingEmail(
      orgId: string,
      personDisplayName: string | undefined,
    ): Promise<void> {
      const body = JSON.stringify({
        org_id: orgId,
        display_name: personDisplayName ?? undefined,
      });
      for (let attempt = 0; attempt < 2; attempt++) {
        let res: Response | null = null;
        try {
          res = await fetch(`${fastApiUrl}/internal/onboarding-email`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Authorization": `Bearer ${fastApiKey}`,
            },
            body,
            signal: AbortSignal.timeout(5_000),
          });
        } catch (e) {
          console.error("Onboarding email failed:", e);
        }
        if (res && res.ok) {
          let status = "";
          try {
            const parsed = await res.json();
            status = typeof parsed?.status === "string" ? parsed.status : "";
          } catch { /* response body drained below */ }
          if (status === "sent" || status === "already_sent"
              || status === "in_flight") {
            // Definitive: provider accepted, previously sent, or a concurrent
            // send is already in flight — never double-fire.
            return;
          }
          if (status) {
            console.error(
              "Onboarding email not sent: /internal/onboarding-email " +
                "status=" + status);
          }
        } else if (res) {
          const errBody = await res.text().catch(() => "");
          console.error(
            "Onboarding email failed: /internal/onboarding-email returned " +
              res.status + (errBody ? ": " + errBody : ""));
        }
        if (attempt === 0) {
          await new Promise((r) => setTimeout(r, 2_000));
        }
      }
    }

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
    // transaction (organizations + org_memberships + api_keys) via the
    // provision_team SECURITY DEFINER RPC (migration 0010). The RPC is
    // idempotent: it reconciles the on_auth_user_created placeholder row
    // (org_id='' / key_hash='pending') in place, so exactly one membership
    // row and one api_keys row exist per provisioned org (plan §4.1 step 6,
    // P2-5 concurrency contract). key_hash (salted PBKDF2, continuity) and
    // lookup_hash (SHA-256(pepper + key) — the auth lookup anchor, E2E-6)
    // are computed HERE, never in SQL (the pepper lives in app code only).
    //
    // Failure is FATAL now (unlike the old best-effort update_user_team
    // write): the FalkorDB registry is no longer written, so without this
    // transaction the org exists NOWHERE. The user can retry — the RPC is
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
      p_org_id: orgId,
      p_org_name: safeName,
      p_api_key: apiKey, // plaintext — handed back once, then nulled
      p_key_hash: keyHash,
      p_lookup_hash: lookupHashHex,
      p_graph_name: `org_${orgId}`,
      p_email: email,
      p_key_prefix: apiKey.slice(0, 10),
    });
    if (provisionError) {
      console.error(
        "provision_team failed for " + safeName + ":",
        provisionError
      );
      return json({ error: "Provisioning failed. Please try again." }, 502);
    }

    // ── #2360: REAL starter seed (supersedes the demo auto-seed) ──────
    // Data plane ONLY: /internal/starter-seed files the org's REAL starter
    // graph (FalkorDB, graph org_{org_id} — created on first write): the
    // signing-up user as a naturalPerson Subject + their Organization as an
    // organization Subject, connected memberOf (canonical two-anchor seed,
    // tortoise/onboarding/seed.py). The old demo auto-seed wrote
    // 12 fake demo Points + a _demo_sentinel into every fresh org — sample
    // content the Overview digest counted as the user's own activity
    // (#2360). Identity data here is the VERIFIED caller's own: org_name =
    // the user-confirmed org name, person_name = the user's auth display
    // name when present (never silently email-derived — the interactive W3
    // seed completes the person anchor with a user-confirmed name when no
    // display name exists). Never touches the control_plane registry graph
    // (E2E-1: zero registry writes). AbortSignal.timeout bounds the await so
    // a slow seed can never blow the caller's request budget mid-retry
    // (code-review P2, PR #847 — the RPC has already committed at this
    // point; a retried request is a same-payload retry, and deterministic
    // org_id makes that a harmless no-op).
    // #1860 (P3-1): bind the response — a non-2xx body used to resolve
    // "successfully" (the old .catch() only handled transport/abort), so a
    // failed seed left the first-timer's graph silently missing starter
    // data.
    const starterBody: Record<string, string> = {
      org_id: orgId,
      org_name: safeName,
      person_user_id: user_id,
      person_email: email,
    };
    if (typeof display_name === "string" && display_name.trim()) {
      starterBody.person_name = display_name.trim();
    }
    const starterRes = await fetch(`${fastApiUrl}/internal/starter-seed`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${fastApiKey}`,
      },
      body: JSON.stringify(starterBody),
      signal: AbortSignal.timeout(10_000),
    }).catch((e) => {
      console.error("Starter seed failed:", e);
      return null;
    });
    if (starterRes && !starterRes.ok) {
      // HTTP error (4xx/5xx) — the provision RPC already committed, so the
      // org exists but its graph lacks the starter seed. Log loudly (incl.
      // the error body, which also drains the connection back to the pool);
      // do NOT fail the whole provisioning (the user can still be onboarded).
      const starterErrBody = await starterRes.text().catch(() => "");
      console.error(
        "Starter seed failed: /internal/starter-seed returned " + starterRes.status +
          (starterErrBody ? ": " + starterErrBody : "")
      );
    }

    // ── #2406: fire the one-time onboarding-call offer email ─────────────
    // Independent of the demo seed (fires even when demo seeding threw) and
    // fail-soft: fireOnboardingEmail never throws, so this can never turn a
    // committed provisioning into a 500 for the user. Exactly-once is the
    // server's job (marker + in-flight gate + provider Idempotency-Key); the
    // +2s retry covers the lost-response / transient-provider window.
    await fireOnboardingEmail(orgId, display_name);

    const response: ProvisionResponse = {
      org_id: orgId,
      org_name: safeName,
      api_key: apiKey,
      // Must match the value upserted into org_memberships above (org_${orgId}).
      graph_name: `org_${orgId}`,
    };

    return json(response, 201);

  } catch (err) {
    console.error("tenant-provision error:", err);
    return json({ error: "Internal server error" }, 500);
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
