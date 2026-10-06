/**
 * Archive Gate 1b spike — D1 session-store semantics, no wrangler.toml.
 *
 * Proves the §8.1 contract works with a CLI-flag binding:
 *   - schema creation (handle PK, indexed user_id)
 *   - insert handle
 *   - read by handle
 *   - CAS revoke (conditional update) — the revocation primitive
 *   - bulk revoke by user_id — F15
 *   - read-after-write consistency (primary-only default)
 *
 * Each is asserted, not assumed: the response carries a per-check boolean.
 */
interface Env { SESSIONS?: D1Database }

export const onRequestGet: PagesFunction<Env> = async ({ env }) => {
  const db = env.SESSIONS;
  const r: Record<string, unknown> = {};

  if (!db) {
    return new Response(JSON.stringify({ d1Binding: false, verdict: "NO BINDING" }), {
      headers: { "Content-Type": "application/json" },
    });
  }
  r.d1Binding = true;

  try {
    await db.exec(
      "CREATE TABLE IF NOT EXISTS sessions (" +
      "handle TEXT PRIMARY KEY, user_id TEXT NOT NULL, refresh_token TEXT NOT NULL, " +
      "revoked INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);",
    );
    await db.exec("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);");
    r.schema = true;

    const now = Date.now();
    await db.prepare(
      "INSERT OR REPLACE INTO sessions (handle,user_id,refresh_token,revoked,created_at,expires_at) VALUES (?1,?2,?3,0,?4,?5)",
    ).bind("h1", "u1", "rt1", now, now + 86400000).run();
    await db.prepare(
      "INSERT OR REPLACE INTO sessions (handle,user_id,refresh_token,revoked,created_at,expires_at) VALUES (?1,?2,?3,0,?4,?5)",
    ).bind("h2", "u1", "rt2", now, now + 86400000).run();

    // read-after-write on the primary-only default (the W1 precondition)
    const read = await db.prepare("SELECT user_id FROM sessions WHERE handle=?1 AND revoked=0")
      .bind("h1").first<{ user_id: string }>();
    r.readAfterWrite = read?.user_id === "u1";

    // CAS revoke — conditional update; meta.changes tells us if it won
    const cas = await db.prepare("UPDATE sessions SET revoked=1 WHERE handle=?1 AND revoked=0")
      .bind("h1").run();
    r.casRevokeChanges = cas.meta?.changes;
    const cas2 = await db.prepare("UPDATE sessions SET revoked=1 WHERE handle=?1 AND revoked=0")
      .bind("h1").run();
    r.casIdempotent = cas2.meta?.changes === 0; // second attempt is a no-op

    // bulk revoke by user_id (F15) — leaves h1 already revoked, revokes h2
    const bulk = await db.prepare("UPDATE sessions SET revoked=1 WHERE user_id=?1 AND revoked=0")
      .bind("u1").run();
    r.bulkRevokeChanges = bulk.meta?.changes;

    const live = await db.prepare("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?1 AND revoked=0")
      .bind("u1").first<{ n: number }>();
    r.allRevoked = live?.n === 0;

    r.verdict = (r.readAfterWrite && r.casIdempotent && r.allRevoked)
      ? "PASS — D1 contract works without wrangler.toml"
      : "FAIL — see flags";
  } catch (err) {
    r.verdict = "FAIL";
    r.error = err instanceof Error ? err.message : String(err);
  }

  return new Response(JSON.stringify(r, null, 2), {
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
};
