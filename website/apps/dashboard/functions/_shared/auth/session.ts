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

/**
 * How long past its own `expires_at` a `sessions` row survives (#8032).
 *
 * WHY A GRACE AT ALL — on `auth_flows`, `expires_at` encoded the row's entire
 * usable life, so a sweep could key on `expires_at < now` and delete nothing a
 * live flow needed. On `sessions` it does not: two readers deliberately look at
 * a row AFTER its TTL has passed, and both would become unreachable the moment
 * a sweep deleted it.
 *
 *   - `/api/session` and `/api/profile` answer `session_expired` — a DISTINCT
 *     401 from `not_signed_in` — and revoke the row, which is what keeps "the
 *     session timed out" observable rather than indistinguishable from a handle
 *     that was never issued;
 *   - a request already in flight when the TTL passed still holds the row it
 *     read (the same narrow race `auth_flows` accepts).
 *
 * The grace is also what upstream does — Supabase Auth: *"sessions are
 * progressively deleted from the database 24 hours after they expire, which
 * prevents you from causing a high load on your project by accident and allows
 * you some freedom to undo changes without adversely affecting all users."*
 * (read 2026-10-12) — so the D1 store's collection stays legible to anyone who
 * knows the vendor behaviour, and a mis-shipped short TTL gets a rollback window
 * instead of deleting live users' sessions irreversibly.
 *
 * WHY NOT LONGER — after that window the row is a dead handle carrying a
 * `refresh_token`, and nothing in this store reads it: `getSession` filters
 * `revoked = 0`, `/api/session`, `/api/profile` and `/auth/update-password`
 * refuse it on `expires_at`, and the CAS in `revokeSession` can only win on a
 * live row. Keeping it longer is retention with no reader.
 *
 * The window itself is stated once, in `docs/retention-and-deletion.md`.
 */
export const SESSION_SWEEP_GRACE_S = 24 * 60 * 60; // 24 hours

/**
 * Rows one opportunistic sweep may remove (#3647).
 *
 * The sweep runs on a user-facing request path, so its cost must be bounded
 * INDEPENDENTLY of how far behind the backlog has fallen: `LIMIT` caps the work
 * per call and the next call takes the next slice. Without a cap, the first
 * call after a long un-swept period would try to drain the entire backlog
 * inside one user-facing request.
 *
 * Steady state is far below this — a 30-minute expiry against a 5-minute probe
 * is ~150 rows — so the cap is reached only while draining an existing backlog.
 *
 * The name is historical (#3647 was the first sweep). The bound is shared by
 * every sweep in this module — `sweepExpiredSessions` and
 * `sweepExpiredPending` (#8032) as well as `sweepExpiredFlows` — because it is a
 * property of the REQUEST PATH that runs a sweep, not of any one table.
 */
export const FLOW_SWEEP_LIMIT = 500;

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

/**
 * Create the index the sweep's cost argument rests on.
 *
 * `idx_auth_flows_expires` is declared in `website/migrations/0001_auth_sessions.sql`,
 * but a database created by the RUNTIME bootstraps alone (`ensureFlowTable`)
 * never got — and `config/required-bindings.yml` records that production's
 * tables are created exactly that way, so the index's absence there is the
 * live possibility, not the hypothetical one. Since the whole point of
 * `sweepExpiredFlows` being safe to call on a request path is that its range
 * predicate is INDEXED (`SEARCH auth_flows USING INDEX idx_auth_flows_expires`),
 * create it where the table is created. Same shape as `ensureSchema`'s
 * `idx_sessions_user`.
 */
export async function ensureAuthFlowIndexes(db: D1Database): Promise<void> {
  await db.exec(
    "CREATE INDEX IF NOT EXISTS idx_auth_flows_expires ON auth_flows(expires_at);",
  );
}

/**
 * The `email_flow_pending` bootstrap — the third table in this database.
 *
 * Lives HERE, with `ensureSchema` and `ensureAuthFlowIndexes`, rather than in
 * the route that first needed it: it has two consumers now (`/auth/confirm`,
 * which writes the row, and `/auth/start`, which sweeps expired ones — #8032),
 * and the shared module is the seam that keeps them from duplicating DDL that
 * must be identical. `ensureAuthFlowIndexes` is the same shape.
 *
 * A pending email-flow confirmation: the outcome of a VERIFIED `token_hash`,
 * held server-side until the user confirms on the interstitial. Its own table
 * (not `auth_flows`) because the credential it carries — the refresh token —
 * must never be exposed to, or controllable by, a flow-start request.
 *
 * `kind` is the GoTrue `type` the link arrived with, and it is what makes
 * recovery-only behaviour (F15 bulk revoke, the reset panel) apply to recovery
 * and NOT to an ordinary confirmation or magic link.
 *
 * The index is created where the table is (see `sweepExpiredPending`):
 * `CREATE TABLE IF NOT EXISTS` cannot add one to a table that already exists,
 * and a runtime-created database would otherwise never receive it.
 */
export async function ensurePendingTable(db: D1Database): Promise<void> {
  await db.exec(
    "CREATE TABLE IF NOT EXISTS email_flow_pending (" +
      "flow_id TEXT PRIMARY KEY, " +
      "kind TEXT NOT NULL, " +
      "user_id TEXT NOT NULL, " +
      "refresh_token TEXT NOT NULL, " +
      "email TEXT, " +
      "created_at INTEGER NOT NULL, " +
      "expires_at INTEGER NOT NULL);",
  );
  await db.exec(
    "CREATE INDEX IF NOT EXISTS idx_email_flow_pending_expires " +
      "ON email_flow_pending(expires_at);",
  );
}

/**
 * Delete expired `auth_flows` rows — the garbage collection this table never had (#3647).
 *
 * WHY THIS EXISTS. Every deletion of a flow row was coupled to the flow
 * COMPLETING: `/auth/callback` deletes on a successful return trip, and
 * `/auth/link` deletes when the upstream call never started a flow. A flow that
 * is started and ABANDONED reaches neither, so its row stays forever — expired,
 * unusable, and never removed. The scheduled `/auth/start` probe (#3628) starts
 * a flow every 5 minutes and deliberately does NOT follow the 302 (reading the
 * redirect's `Location` is what lets it catch the #3616 class), so it is a
 * steady producer of exactly those rows: 288/day from the watchdog alone, plus
 * every deploy-gate and e2e probe. Deletion is now coupled to the flow's
 * LIFETIME instead of to a completion an abandoned flow never reaches.
 *
 * WHY IT IS SAFE. `expires_at` already encodes the row's entire usable life:
 * `/auth/callback` refuses a row with `expires_at <= Date.now()`, and
 * `/auth/start` never reads a pre-existing row at all — it only inserts. So a
 * sweep cannot delete a row a live flow still needs. The row just inserted has
 * `expires_at = now + FLOW_MAX_AGE_S`, so it is never its own victim.
 *
 * WHY IT NEVER THROWS. This is housekeeping on the sign-in path. A sweep
 * failure means rows linger — the status quo this replaces, not a fault the
 * caller can act on — so propagating it would turn a collection problem into a
 * 503 on sign-in, breaking the user-visible path in order to report a
 * non-fault. It is LOGGED rather than swallowed silently: a sweep that never
 * succeeds is otherwise invisible, which is `ensureColumn`'s lesson read from
 * the other direction.
 */
export async function sweepExpiredFlows(db: D1Database, now = Date.now()): Promise<number> {
  try {
    const res = await db
      .prepare(
        "DELETE FROM auth_flows WHERE flow_id IN (" +
          "SELECT flow_id FROM auth_flows WHERE expires_at < ?1 ORDER BY expires_at LIMIT ?2)",
      )
      .bind(now, FLOW_SWEEP_LIMIT)
      .run();
    return res.meta?.changes ?? 0;
  } catch (e) {
    console.error("auth_flows sweep failed", e);
    return 0;
  }
}

/**
 * Delete `sessions` rows that are past their own life — the second table of the
 * #3647 class, and the one the issue behind it (#8032) called a policy
 * question rather than a port.
 *
 * WHY THIS EXISTS. Nothing ever deleted a `sessions` row. Sign-out and F15
 * "sign out everywhere" are `UPDATE sessions SET revoked = 1` (`revokeSession`,
 * `revokeAllForUser`), and `DELETE FROM sessions` appeared nowhere: every row
 * ever minted was kept forever — revoked or not — in the largest table in this
 * store, and (with `email_flow_pending`) one of the two carrying no `expires_at`
 * index. A row is not dead weight only: it carries a `refresh_token`, and our
 * copy of a credential should not outlive the session that owned it by an
 * unbounded margin.
 *
 * WHY IT IS SAFE. The predicate is `expires_at`, NEVER `revoked`: a revoked row
 * survives exactly as long as the session it revoked would have lived, so
 * revocation stays observable (the CAS in `revokeSession` is only meaningful on
 * a live row) and abuse forensics keep the whole declared window. No live
 * session can be collected: `SESSION_SWEEP_GRACE_S` is measured from the row's
 * own expiry, not from now, and the row `createSession` just inserted has
 * `expires_at = now + SESSION_MAX_AGE_S`, so it is never its own victim.
 *
 * WHY IT NEVER THROWS — the same argument as `sweepExpiredFlows`: this is
 * housekeeping on the sign-in path, so propagating a failure would turn a
 * collection problem into a 503 on sign-in, breaking the user-visible path in
 * order to report a non-fault. It is LOGGED rather than swallowed silently.
 */
export async function sweepExpiredSessions(db: D1Database, now = Date.now()): Promise<number> {
  try {
    const res = await db
      .prepare(
        "DELETE FROM sessions WHERE handle IN (" +
          "SELECT handle FROM sessions WHERE expires_at < ?1 ORDER BY expires_at LIMIT ?2)",
      )
      .bind(now - SESSION_SWEEP_GRACE_S * 1000, FLOW_SWEEP_LIMIT)
      .run();
    return res.meta?.changes ?? 0;
  } catch (e) {
    console.error("sessions sweep failed", e);
    return 0;
  }
}

/**
 * Delete expired `email_flow_pending` rows (#8032) — the third and last table
 * of the #3647 class, and the one where the leak is a live credential rather
 * than a dead record.
 *
 * WHY THIS EXISTS. A pending record is written when a VERIFIED email link is
 * held for the user's explicit confirmation, and its ONLY deletion was on
 * CONSUMPTION (`/auth/confirm` POST). A user who lands on the interstitial and
 * closes the tab reaches no other deletion, so the row stayed forever. That
 * matters more here than on `auth_flows`: the row's payload is the flow's
 * `refresh_token` — a usable credential — and `RECOVERY_MAX_AGE_S` bounds
 * replay of the RECORD, not the token it carries (the table's own comment says
 * the credential "must never be exposed to, or controllable by, a flow-start
 * request"). An expired row therefore held a usable credential for an
 * unbounded time.
 *
 * WHY IT IS SAFE, WITH NO GRACE. `expires_at` already encodes this record's
 * entire usable life: `/auth/confirm` POST — the only reader — refuses a row
 * with `expires_at <= Date.now()`, and nothing else reads the table — so,
 * exactly as with `auth_flows`, the sweep cannot delete a record a live flow
 * still needs.
 * Unlike `sessions`, no reader deliberately looks at a row past its TTL, so
 * there is no reason to keep one: the credential's life in our store ends at the
 * expiry plus at most one sweep interval.
 *
 * WHERE IT RUNS. From `/auth/start`, beside `sweepExpiredFlows` — not from
 * `/auth/confirm`, the route that writes the row. `/auth/start` is the one path
 * a schedule already drives (the #3628 probe, every 5 minutes), and the whole
 * point of this sweep is to bound how long an ABANDONED interstitial's
 * credential survives: sweeping only on the next `/auth/confirm` would leave
 * the last abandoned link's row in place until someone opened another email
 * link. No new route and no new schedule.
 *
 * WHY IT NEVER THROWS — as above.
 */
export async function sweepExpiredPending(db: D1Database, now = Date.now()): Promise<number> {
  try {
    const res = await db
      .prepare(
        "DELETE FROM email_flow_pending WHERE flow_id IN (" +
          "SELECT flow_id FROM email_flow_pending WHERE expires_at < ?1 " +
          "ORDER BY expires_at LIMIT ?2)",
      )
      .bind(now, FLOW_SWEEP_LIMIT)
      .run();
    return res.meta?.changes ?? 0;
  } catch (e) {
    console.error("email_flow_pending sweep failed", e);
    return 0;
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

  // #8032: expired rows accumulated because deletion was only ever a `revoked`
  // FLAG, never a delete — so every row ever minted stayed, revoked or not.
  // Swept HERE, in the function every session-minting route calls (callback,
  // confirm, password, api-key, signup), rather than from those five routes:
  // collection is then coupled to the writer at the narrowest shared waist, a
  // sixth route inherits it for free, and the five call sites cannot drift.
  // The row above has already landed and is 400 days from expiry, so it cannot
  // be its own victim.
  await sweepExpiredSessions(db, now);

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
  // #8032: `sweepExpiredSessions`' range predicate. Created here WITH the table
  // because a database established by the runtime bootstraps alone never
  // receives the migration's copy — and without the index the sweep's DELETE
  // degrades to a full scan of the largest table in this store.
  await db.exec("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);");
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
