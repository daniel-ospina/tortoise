/**
 * Server-side credential minting for the Token Handler proxy (W6).
 *
 * SCOPE.md §4 W6. The pattern (Duende / IETF OAuth 2.0 BFF / Curity Token
 * Handler): the browser holds only an opaque session cookie; the server holds
 * the real credential and attaches it to upstream API calls. The access token
 * never reaches JavaScript — which is the entire point of the redesign.
 *
 * Why the access token is CACHED IN D1 rather than refreshed per request:
 * Supabase rotates refresh tokens and treats them as single-use, with only a
 * ~10 second reuse tolerance. Refreshing on every proxied call would (a) add a
 * round trip to GoTrue on the hot path of every dashboard API call, and (b)
 * race violently with itself when the dashboard fires concurrent requests —
 * the classic rotating-refresh-token stampede, where parallel calls each burn
 * the other's token and the session dies mid-use.
 *
 * So: store the access token + its expiry; refresh only shortly before it
 * lapses, and serialise concurrent refreshes.
 *
 * NOTE: this adds two columns beyond SCOPE.md §8.1's stated schema. Recorded as
 * a deliberate amendment — §8.1's "not stored" list was about provider_token /
 * identities / user_metadata (the fat fields that caused the SIZE_GUARD defect),
 * not about the short-lived credential the proxy needs.
 */
import type { Env } from "./session";
import { ensureColumn, ensureSchema } from "./session";
import { refreshSession, isRefreshTokenDead } from "./supabase";

/** Refresh this many ms before actual expiry, to avoid using a token mid-flight. */
const REFRESH_SKEW_MS = 60_000;

/**
 * In-flight refreshes, keyed by session handle — property 1, single-flight.
 *
 * Concurrent callers for the SAME handle must share ONE provider refresh
 * instead of each POSTing the same rotating refresh token. Supabase rotates
 * refresh tokens single-use with only a ~10 s reuse tolerance, so two concurrent
 * refreshes outside that window earn `refresh_token_already_used` — which the
 * classifier below reads as a DEAD session, so every racing caller answers 401
 * and clears the cookie. That is exactly the #3485 sign-out these properties
 * exist to prevent.
 *
 * This map is PER ISOLATE. The D1 CAS further down is the cross-isolate half: a
 * second isolate cannot see this map, so it may still reach the provider, and the
 * CAS makes its write a no-op and makes it re-read the winner's token.
 */
const inFlightRefreshes = new Map<string, Promise<TokenResult>>();

export interface TokenRow {
  handle: string;
  refresh_token: string;
  access_token: string | null;
  access_token_expires_at: number | null;
  /** The SESSION's own TTL — enforced on the data path (see getAccessTokenForSession). */
  expires_at: number;
}

export type TokenResult =
  | { ok: true; accessToken: string }
  | { ok: false; reason: "no_session" }
  | { ok: false; reason: "unavailable" };

export async function ensureSchemaTokenColumns(db: D1Database): Promise<void> {
  // The BASE TABLE first, then the columns added on top of it.
  //
  // `ALTER TABLE` on a table that does not exist yet throws `no such table:
  // sessions` — which is NOT a duplicate-column error, so `ensureColumn`
  // rethrows it and the caller's `catch` reports the whole session store as
  // unavailable (the admin gate answers 503 "temporarily unavailable").
  //
  // Every READ path calls this function and none of them calls
  // `ensureSchema` — the gate, `/api/session`, the W6 token-handler proxy,
  // `/api/v1`, provision, profile, set-email, update-password. So the base
  // table had to already exist, and only the six WRITE paths
  // (callback/confirm/password/api-key/signup/link) created it. On any
  // deployment where the migration had not been applied — a fresh local D1,
  // or the e2e harness, which applies the migration straight to the sqlite
  // file and so is never observed by the running runtime — every read path
  // 503'd with the schema looking fine in the file. `ensureSchema` is
  // idempotent (`CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`),
  // so calling it here costs one no-op statement on the normal path.
  await ensureSchema(db);
  // Uses `ensureColumn`, which swallows ONLY the duplicate-column error. The
  // first version caught everything, so a genuine DDL failure was invisible and
  // manifested as every proxied request 503ing with no signal anywhere.
  await ensureColumn(db, "sessions", "access_token", "TEXT");
  await ensureColumn(db, "sessions", "access_token_expires_at", "INTEGER");
  // Bounds how often an upstream rejection may force a refresh (see
  // `invalidateCachedToken`) — without it, a persistent 401 becomes unbounded
  // rotating-refresh-token traffic against GoTrue.
  await ensureColumn(db, "sessions", "token_rejected_at", "INTEGER");
}

export async function invalidateCachedToken(
  env: Env,
  handle: string,
  cooldownMs = 30_000,
  // Injected so the cooldown window is testable without wall-clock dependence:
  // a real-clock test that happens to span the 30 s window reports a false red
  // that is indistinguishable from the genuine "cooldown predicate dropped"
  // regression signature.
  now = Date.now(),
): Promise<void> {
  if (!env.SESSIONS) return;
  try {
    // Only once per cooldown window.
    //
    // Without the guard, a persistent upstream rejection forces a refresh on
    // EVERY proxied request — each one burning a single-use rotating refresh
    // token at GoTrue (measured 1:1, unbounded). That is precisely the stampede
    // the D1 token cache exists to prevent, reintroduced on the error path, and
    // GoTrue's reuse-detection can then kill the entire session family. Within
    // the window the cached token is left alone, so the retry returns the same
    // 503 without touching the provider.
    await env.SESSIONS.prepare(
      "UPDATE sessions SET access_token = NULL, access_token_expires_at = NULL, " +
        "token_rejected_at = ?2 " +
        "WHERE handle = ?1 AND (token_rejected_at IS NULL OR token_rejected_at < ?3)",
    )
      .bind(handle, now, now - cooldownMs)
      .run();
  } catch {
    /* best effort — a failure here only means the next call reuses a stale token */
  }
}

/**
 * Return a usable access token for a session handle, refreshing if needed.
 *
 * `no_session` and `unavailable` are kept distinct on purpose: the caller must
 * answer 401 for the first and 503 for the second. Collapsing them is the
 * #3485 failure class — a store fault would look like "you are signed out".
 */
export async function getAccessTokenForSession(
  env: Env,
  handle: string,
): Promise<TokenResult> {
  if (!env.SESSIONS) return { ok: false, reason: "unavailable" };

  let row: TokenRow | null;
  try {
    row = await env.SESSIONS.prepare(
      "SELECT handle, refresh_token, access_token, access_token_expires_at, expires_at " +
        "FROM sessions WHERE handle = ?1 AND revoked = 0",
    )
      .bind(handle)
      .first<TokenRow>();
  } catch {
    return { ok: false, reason: "unavailable" };
  }

  if (!row) return { ok: false, reason: "no_session" };

  // The session's own TTL must be enforced HERE, on the data path.
  //
  // Every other consumer already does it — /api/session, /welcome,
  // /auth/update-password, and resolveSession all check `expires_at`. Without
  // this predicate the proxy kept working after expiry (re-minting from a
  // still-valid refresh token indefinitely), so `/api/v1` served authenticated
  // upstream data with a 200 while `/api/session` answered 401 for the same
  // cookie: the #3485 divergence again, with the proxy on the permissive side.
  // An expired session is a dead session, not a refreshable one.
  if (row.expires_at <= Date.now()) return { ok: false, reason: "no_session" };

  const now = Date.now();
  if (row.access_token && row.access_token_expires_at && row.access_token_expires_at - REFRESH_SKEW_MS > now) {
    return { ok: true, accessToken: row.access_token };
  }

  // Property 1 — single-flight. A concurrent caller for the SAME handle joins
  // the running refresh instead of issuing a second one, so exactly one rotating
  // refresh token is spent per stale generation. Checked and set synchronously
  // (no `await` between), so two callers cannot both miss it and both refresh.
  const running = inFlightRefreshes.get(handle);
  if (running) return running;
  const refresh = refreshAndPersist(env, handle, row).finally(() => {
    inFlightRefreshes.delete(handle);
  });
  inFlightRefreshes.set(handle, refresh);
  return refresh;
}

/**
 * The refresh-and-persist body, shared by every concurrent caller for a handle.
 *
 * Rotation-safe in two independent layers:
 *  - this isolate's `inFlightRefreshes` map dedupes concurrent callers here;
 *  - the D1 CAS on the OLD refresh token makes a cross-isolate loser's write a
 *    no-op, and the loser re-reads the winner's token rather than clobbering it.
 */
async function refreshAndPersist(
  env: Env,
  handle: string,
  row: TokenRow,
): Promise<TokenResult> {
  const now = Date.now();
  const refreshed = await refreshSession(env, row.refresh_token);
  if (!refreshed.ok) {
    // Retryable (5xx/429/network) is a SERVER fault.
    if (refreshed.retryable) return { ok: false, reason: "unavailable" };
    // Only the `invalid_grant` family is a genuinely DEAD session. A 403 from an
    // edge/WAF, a 404 from a misconfigured SUPABASE_URL, or an unexpected 400 is
    // infrastructure — classifying those as dead signs every user out during an
    // outage, which is the #3485 class.
    if (isRefreshTokenDead(refreshed.status, refreshed.error)) {
      return { ok: false, reason: "no_session" };
    }
    return { ok: false, reason: "unavailable" };
  }

  const expiresAt = now + refreshed.data.expires_in * 1000;
  try {
    // CAS on the OLD refresh token: if a concurrent request already rotated it,
    // this is a no-op and we simply re-read rather than clobbering the newer one.
    const res = await env.SESSIONS.prepare(
      "UPDATE sessions SET refresh_token = ?2, access_token = ?3, access_token_expires_at = ?4 " +
        "WHERE handle = ?1 AND revoked = 0 AND refresh_token = ?5",
    )
      .bind(
        handle,
        refreshed.data.refresh_token,
        refreshed.data.access_token,
        expiresAt,
        row.refresh_token,
      )
      .run();

    if ((res.meta?.changes ?? 0) === 0) {
      // Someone else won the rotation race. Their token is authoritative —
      // re-read rather than overwrite, or we destroy the live session.
      const fresh = await env.SESSIONS.prepare(
        "SELECT access_token, access_token_expires_at FROM sessions WHERE handle = ?1 AND revoked = 0",
      )
        .bind(handle)
        .first<{ access_token: string | null; access_token_expires_at: number | null }>();
      if (fresh?.access_token) return { ok: true, accessToken: fresh.access_token };
    }
  } catch {
    // Persisting failed but the token is valid — use it, and let the next call
    // refresh again. Do not fail the user's request over a cache write.
  }

  return { ok: true, accessToken: refreshed.data.access_token };
}
