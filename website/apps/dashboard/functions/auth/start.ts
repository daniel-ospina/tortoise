/**
 * POST/GET /auth/start — the flow-start endpoint.
 *
 * SCOPE.md §8.4: REQUIRED. Without it the class-8 (CSRF / session-fixation)
 * mitigation is unimplementable, because there is nothing to bind the
 * returning credential to.
 *
 * Responsibilities (all four must happen before Supabase sees anything):
 *   1. mint a flow id
 *   2. generate the PKCE verifier SERVER-SIDE and keep it server-side
 *   3. persist the flow (verifier + optional consent binding) in D1
 *   4. set `__Host-authflow` bound to that flow id
 *
 * The cookie alone is not the mitigation — the cookie must MATCH the flow row.
 * A cookie that merely exists proves nothing; an attacker who can make the
 * browser hold *a* cookie would satisfy "exists".
 *
 * Consent binding is carried through here too, so the MCP consent surface has
 * the `{client_id, redirect_uri, code_challenge, state}` tie-back that makes
 * the displayed client identity meaningful rather than self-asserted.
 */
import {
  type Env,
  FLOW_COOKIE,
  FLOW_MAX_AGE_S,
  buildCookie,
  ensureColumn,
  json,
  redirect,
  safeNext,
} from "../_shared/auth/session";
import { bytesToB64url } from "../_shared/auth/jwt";

interface FlowEnv extends Env {
  AUTH_CALLBACK_URL?: string;
}

/**
 * The identity providers GoTrue may be asked for.
 *
 * This is an ALLOWLIST, never a passthrough. The value is written into an
 * upstream redirect URL, so an unvalidated `provider` would be an arbitrary
 * parameter injection into the authorize URL — and an attacker-chosen upstream
 * flow. Anything not listed here is refused before the request can reach GoTrue.
 */
const AUTH_PROVIDERS = ["email", "github", "google"] as const;
type AuthProvider = (typeof AUTH_PROVIDERS)[number];

/**
 * Resolve the requested provider.
 *
 * ABSENT is not the same as invalid: a missing param defaults to `email`, which
 * is exactly the single-provider behaviour this endpoint shipped with. A
 * present-but-empty `?provider=` is an unusable value and is refused like any
 * other unknown one.
 */
function resolveProvider(raw: string | null): AuthProvider | null {
  if (raw === null) return "email";
  return AUTH_PROVIDERS.find((p) => p === raw) ?? null;
}

function randomUrlSafe(bytes = 32): string {
  const b = new Uint8Array(bytes);
  crypto.getRandomValues(b);
  return bytesToB64url(b);
}

/** PKCE S256 challenge. `plain` is deliberately not supported. */
export async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return bytesToB64url(new Uint8Array(digest));
}

export async function ensureFlowTable(db: D1Database) {
  await db.exec(
    "CREATE TABLE IF NOT EXISTS auth_flows (" +
      "flow_id TEXT PRIMARY KEY, verifier TEXT NOT NULL, kind TEXT NOT NULL, " +
      "client_id TEXT, redirect_uri TEXT, state TEXT, next TEXT, " +
      "created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);",
  );
  // CREATE ... IF NOT EXISTS cannot add a column to a table that already exists,
  // so `next` must be added explicitly or INSERT breaks on an existing database.
  await ensureColumn(db, "auth_flows", "next", "TEXT");
}

/**
 * Only a same-origin path may be used as a post-login destination.
 *
 * The implementation lives in `session.ts` — a local prefix check here was
 * bypassable with a TAB (see the note there), so there is exactly one copy.
 */

export const onRequestGet: PagesFunction<FlowEnv> = async ({ request, env }) => {
  const url = new URL(request.url);
  const kind = url.searchParams.get("kind") ?? "signin";

  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  try {
    await ensureFlowTable(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // Validated AFTER the store guard (so the existing 503 contract is unchanged)
  // and BEFORE anything is minted: an unsupported provider must not create a
  // flow row, set a cookie, or produce a hop to GoTrue.
  const provider = resolveProvider(url.searchParams.get("provider"));
  if (!provider) return json({ error: "unsupported_provider" }, { status: 400 });

  const flowId = randomUrlSafe(16);
  const verifier = randomUrlSafe(32);
  const challenge = await pkceChallenge(verifier);
  const now = Date.now();

  await env.SESSIONS.prepare(
    "INSERT INTO auth_flows (flow_id,verifier,kind,client_id,redirect_uri,state,next,created_at,expires_at) " +
      "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
  )
    .bind(
      flowId,
      verifier,
      kind,
      url.searchParams.get("client_id"),
      url.searchParams.get("redirect_uri"),
      url.searchParams.get("state"),
      // Persisted, validated on write and re-validated on read.
      safeNext(url.searchParams.get("next"), url.origin),
      now,
      now + FLOW_MAX_AGE_S * 1000,
    )
    .run();

  const redirectTo = env.AUTH_CALLBACK_URL ?? `${url.origin}/auth/callback`;
  const authorize = new URL(`${env.SUPABASE_URL}/auth/v1/authorize`);
  authorize.searchParams.set("provider", provider);
  authorize.searchParams.set("redirect_to", redirectTo);
  authorize.searchParams.set("code_challenge", challenge);
  authorize.searchParams.set("code_challenge_method", "s256");

  // The flow cookie is bound to the flow id. `next` rides in the flow row, not
  // the URL, so it cannot be swapped between flow start and completion.
  return redirect(authorize.toString(), [
    buildCookie(FLOW_COOKIE, flowId, FLOW_MAX_AGE_S),
  ]);
};

export const onRequestPost: PagesFunction<FlowEnv> = (ctx) =>
  (onRequestGet as PagesFunction<FlowEnv>)(ctx);
