/**
 * Session store (D1) + cookie issuance.
 *
 * Contract: `SCOPE.md` §8.1–§8.3, §8.5. Session shape **D** — an opaque handle
 * in D1, not a JWT the browser holds.
 *
 * Security properties, each deliberate:
 *   - `__Host-` prefix  -> enforces Secure + Path=/ + NO Domain attribute.
 *     The `__Host-` prefix is how the "host-only, no parent-domain" rule is
 *     enforced by the browser rather than by our code remembering to.
 *   - `HttpOnly`        -> the browser never has the credential; this is the
 *     single change that removes the entire client-side session machinery.
 *   - opaque handle     -> a stolen cookie is revocable immediately; a JWT
 *     would stay valid until expiry.
 *   - explicit Max-Age  -> no session-cookie ambiguity across restarts.
 *
 * Failure semantics (§8.2 — the #3485 class):
 *   "not signed in" (401) and "session store unreachable" (503) MUST NOT be
 *   conflated. A 401 tells the browser it is signed out; returning that while
 *   the database is merely down is what caused the #3485 loop.
 */

export const SESSION_COOKIE = "__Host-session";
export const FLOW_COOKIE = "__Host-authflow";

/** §8.5 — session Max-Age. Owner: SCOPE.md §8.5. */
export const SESSION_MAX_AGE_S = 400 * 24 * 60 * 60; // 400 days

/** §8.3 — flow cookie lifetime must exceed email-confirmation latency. */
export const FLOW_MAX_AGE_S = 30 * 60; // 30 minutes

export interface SessionRow {
  handle: string;
  user_id: string;
  refresh_token: string;
  revoked: number;
  created_at: number;
  expires_at: number;
}

export interface Env {
  SUPABASE_URL?: string;
  SUPABASE_ANON_KEY?: string;
  SESSIONS?: D1Database;
  /** Origin of the app (dashboard). Topology as configuration, not as literals. */
  APP_ORIGIN?: string;
}

/**
 * Idempotently add a column to an existing table.
 *
 * SQLite has no "ADD COLUMN IF NOT EXISTS", and `CREATE TABLE IF NOT EXISTS`
 * CANNOT evolve a table that already exists — so adding a column to a CREATE
 * statement does nothing for any database created before it. That is not
 * hypothetical: adding `next` to auth_flows silently broke /auth/start with a 500
 * on every environment with a pre-existing table, because the INSERT then
 * referenced a column the table did not have.
 *
 * Only the duplicate-column error is swallowed. A genuine DDL failure must
 * propagate — swallowing everything (as the first version did) turns a real
 * schema fault into every request 503ing with no signal anywhere.
 */
export async function ensureColumn(
  db: D1Database,
  table: string,
  column: string,
  type: string,
): Promise<void> {
  try {
    await db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${type}`);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/duplicate column name/i.test(msg)) return;
    throw e;
  }
}

export function randomHandle(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** Build a Set-Cookie value. `__Host-` forbids Domain, so it is never emitted. */
export function buildCookie(
  name: string,
  value: string,
  maxAge: number,
  opts: { secure?: boolean } = {},
): string {
  const parts = [
    `${name}=${encodeURIComponent(value)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${maxAge}`,
  ];
  // Only meaningful over https; localhost is treated as a secure context by
  // browsers, but emitting Secure on plain http breaks local dev in some agents.
  if (opts.secure !== false) parts.push("Secure");
  return parts.join("; ");
}

export function clearCookie(name: string, opts: { secure?: boolean } = {}): string {
  return buildCookie(name, "", 0, opts);
}

export function readCookie(request: Request, name: string): string | null {
  const raw = request.headers.get("Cookie") ?? "";
  for (const part of raw.split(";")) {
    const idx = part.indexOf("=");
    if (idx === -1) continue;
    if (part.slice(0, idx).trim() === name) {
      const value = part.slice(idx + 1).trim();
      try {
        return decodeURIComponent(value);
      } catch {
        // An undecodable value (e.g. a bare `%`) is simply not a handle we
        // issued. Treating it as "no cookie" keeps the endpoint's 401/503
        // contract intact; letting URIError escape turned every endpoint into a
        // 500 on four bytes of client-controlled header.
        return null;
      }
    }
  }
  return null;
}

export async function getSession(
  db: D1Database,
  handle: string,
): Promise<SessionRow | null> {
  return db
    .prepare(
      "SELECT handle,user_id,refresh_token,revoked,created_at,expires_at " +
        "FROM sessions WHERE handle = ?1 AND revoked = 0",
    )
    .bind(handle)
    .first<SessionRow>();
}

export async function createSession(
  db: D1Database,
  userId: string,
  refreshToken: string,
  ttlSeconds = SESSION_MAX_AGE_S,
): Promise<string> {
  const handle = randomHandle();
  const now = Date.now();
  await db
    .prepare(
      "INSERT INTO sessions (handle,user_id,refresh_token,revoked,created_at,expires_at) " +
        "VALUES (?1,?2,?3,0,?4,?5)",
    )
    .bind(handle, userId, refreshToken, now, now + ttlSeconds * 1000)
    .run();
  return handle;
}

/**
 * CAS revoke. `meta.changes === 1` means we won the race; 0 means it was
 * already revoked (idempotent) or never existed.
 */
export async function revokeSession(db: D1Database, handle: string): Promise<boolean> {
  const res = await db
    .prepare("UPDATE sessions SET revoked = 1 WHERE handle = ?1 AND revoked = 0")
    .bind(handle)
    .run();
  return (res.meta?.changes ?? 0) === 1;
}

/** F15 — revoke every session for a user (password change, recovery). */
export async function revokeAllForUser(db: D1Database, userId: string): Promise<number> {
  const res = await db
    .prepare("UPDATE sessions SET revoked = 1 WHERE user_id = ?1 AND revoked = 0")
    .bind(userId)
    .run();
  return res.meta?.changes ?? 0;
}

export async function updateRefreshToken(
  db: D1Database,
  handle: string,
  refreshToken: string,
): Promise<boolean> {
  const res = await db
    .prepare("UPDATE sessions SET refresh_token = ?2 WHERE handle = ?1 AND revoked = 0")
    .bind(handle, refreshToken)
    .run();
  return (res.meta?.changes ?? 0) === 1;
}

/** Every cookie-setting response must be uncacheable (§9 hygiene). */
export const NO_STORE = "no-store, no-cache, must-revalidate, private";

/**
 * Idempotent schema bootstrap.
 *
 * The BFF assumed `sessions` already existed; it did not, and the first real
 * end-to-end run failed with `D1_ERROR: no such table: sessions`. Production
 * gets these from a migration (`website/migrations/`); this exists so local runs
 * and a fresh database are self-establishing rather than mysteriously broken.
 *
 * `CREATE TABLE IF NOT EXISTS` is deliberately the only DDL here — this is not a
 * migration system and must never attempt a destructive change.
 */
export async function ensureSchema(db: D1Database): Promise<void> {
  await db.exec(
    "CREATE TABLE IF NOT EXISTS sessions (" +
      "handle TEXT PRIMARY KEY, " +
      "user_id TEXT NOT NULL, " +
      "refresh_token TEXT NOT NULL, " +
      "revoked INTEGER NOT NULL DEFAULT 0, " +
      "created_at INTEGER NOT NULL, " +
      "expires_at INTEGER NOT NULL);",
  );
  // F15 bulk revoke is a per-user scan; without this index it is a table scan.
  await db.exec("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);");
}

export function json(
  body: unknown,
  init: { status?: number; cookies?: string[] } = {},
): Response {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": NO_STORE,
  });
  for (const c of init.cookies ?? []) headers.append("Set-Cookie", c);
  return new Response(JSON.stringify(body), { status: init.status ?? 200, headers });
}

export function redirect(location: string, cookies: string[] = [], status = 302): Response {
  const headers = new Headers({ Location: location, "Cache-Control": NO_STORE });
  for (const c of cookies) headers.append("Set-Cookie", c);
  return new Response(null, { status, headers });
}

/**
 * Accept only a same-origin PATH as a post-login destination.
 *
 * A prefix check is not sufficient, and this was proven on the real stack: a TAB
 * survives `startsWith("/")` / `startsWith("//")`, and WHATWG URL parsing STRIPS
 * it, so `/\t/evil.example` resolves to `https://evil.example/`. The victim
 * completes a normal login and is landed on the attacker's origin with a live
 * session. Prefix-matching is the wrong primitive — resolve against the origin
 * and compare origins instead.
 *
 * Control characters are rejected outright as well: they are never legitimate in
 * a path, and CR/LF additionally corrupts the Location header (or throws when
 * the Headers object is constructed).
 */
export function safeNext(
  raw: string | null | undefined,
  base: string,
): string | null {
  if (!raw) return null;
  if (/[\u0000-\u001f\u007f]/.test(raw)) return null;
  try {
    const resolved = new URL(raw, base);
    if (resolved.origin !== new URL(base).origin) return null;
    const out = resolved.pathname + resolved.search + resolved.hash;
    // Re-check the RESULT, not just the input. `/..//evil.example` resolves
    // same-origin (so the origin check passes) but serialises to `//evil.example`
    // — a network-path reference, which the BROWSER then re-interprets as
    // `https://evil.example/`. Validating the input alone is not sufficient;
    // the value that reaches the Location header is what matters.
    if (out.startsWith("//")) return null;
    return out;
  } catch {
    return null;
  }
}
