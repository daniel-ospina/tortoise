-- #3501 W1 — auth session store (Cloudflare D1)
--
-- Session shape D: an opaque handle the browser holds, with the real session
-- material server-side. See SCOPE.md §8.1.
--
-- Apply:  wrangler d1 execute SESSIONS --file website/migrations/0001_auth_sessions.sql
-- (or --local for local development; the runtime bootstrap in
--  website/apps/dashboard/functions/_shared/auth/ keeps the same shape idempotently —
--  `ensureSchema()` for this table, `ensureSchemaTokenColumns()` (token.ts) for the three
--  token-cache columns, and the per-route flow-table bootstraps in `auth/start.ts` /
--  `auth/confirm.ts` for `auth_flows` / `email_flow_pending` — so a fresh local database is
--  self-establishing.)

CREATE TABLE IF NOT EXISTS sessions (
  handle        TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  refresh_token TEXT NOT NULL,
  revoked       INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL,
  expires_at    INTEGER NOT NULL,
  -- Cached access token for the W6 Token Handler proxy. Refreshing on EVERY
  -- proxied request would stampede Supabase's rotating single-use refresh
  -- tokens, so the proxy caches the access token until REFRESH_SKEW_MS before
  -- expiry. NOTE: this is the SESSION access token, not a provider token —
  -- §8.1's "not stored" list is about provider_token/identities/user_metadata.
  -- Declared here because `ensureSchemaTokenColumns` used to add these at
  -- runtime while swallowing every error, so a real DDL failure was invisible.
  access_token            TEXT,
  access_token_expires_at INTEGER,
  -- Bounds how often an upstream rejection may force a refresh
  -- (`invalidateCachedToken`); without it a persistent 401 becomes unbounded
  -- rotating-refresh-token traffic against GoTrue.
  token_rejected_at       INTEGER
);

-- F15 bulk revoke ("sign out everywhere" on password recovery) filters by
-- user_id. Without this index that is a table scan on every recovery.
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Pre-auth flows: the PKCE verifier must live server-side, and the flow row is
-- what `__Host-authflow` is BOUND to. The binding is the class-8 mitigation — a
-- cookie that merely exists proves nothing.
--
-- `expires_at` must exceed email-confirmation latency, or a legitimate slow
-- confirmation is treated as a fixation attempt.
CREATE TABLE IF NOT EXISTS auth_flows (
  flow_id      TEXT PRIMARY KEY,
  verifier     TEXT NOT NULL,
  kind         TEXT NOT NULL,
  client_id    TEXT,
  redirect_uri TEXT,
  state        TEXT,
  -- Post-login destination. Stored server-side so it rides with the flow and
  -- cannot be swapped in the URL. Only same-origin paths are accepted, and the
  -- value is re-validated on read (`safeNext` in start.ts / callback.ts).
  next         TEXT,
  created_at   INTEGER NOT NULL,
  expires_at   INTEGER NOT NULL
);

-- Expired flows are dead weight and a replay surface; recovery deletes rows,
-- so this index keeps that cheap.
CREATE INDEX IF NOT EXISTS idx_auth_flows_expires ON auth_flows(expires_at);
