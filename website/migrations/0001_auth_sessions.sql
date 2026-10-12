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
--  self-establishing. Those bootstraps also create `idx_auth_flows_expires`
--  (`ensureAuthFlowIndexes`) and — since #8032 — `idx_sessions_expires`
--  (`ensureSchema`) and `email_flow_pending`'s `idx_email_flow_pending_expires`
--  (`ensurePendingTable`), because a database established by the runtime alone never
--  receives the indexes below, and the expiry sweeps (#3647, #8032) depend on them
--  for cost.)

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

-- #8032: `expires_at` is the predicate `sweepExpiredSessions` deletes through.
-- Without this index that sweep is a full scan of the LARGEST table in this
-- store, on the sign-in path — which is why the sweep and the index ship
-- together (`ensureSchema` creates this one where the table is created, for a
-- database the runtime bootstraps alone established).
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

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

-- The third table in this database: a VERIFIED email link held server-side
-- until the user confirms on the interstitial (`/auth/confirm`). Its own table
-- (not `auth_flows`) because the credential it carries — the refresh token —
-- must never be exposed to, or controllable by, a flow-start request.
--
-- Declared here for the same reason every other table is: the runtime bootstrap
-- in `auth/confirm.ts` / `auth/start.ts` (`ensurePendingTable`) is what actually
-- creates it in production, so the two must agree. It is written by
-- `/auth/confirm` and its expired rows are swept by `/auth/start` (#8032).
CREATE TABLE IF NOT EXISTS email_flow_pending (
  flow_id       TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,
  user_id       TEXT NOT NULL,
  refresh_token TEXT NOT NULL,
  email         TEXT,
  created_at    INTEGER NOT NULL,
  expires_at    INTEGER NOT NULL
);

-- #8032: the predicate `sweepExpiredPending` deletes through, created with the
-- table for the same reason as `idx_auth_flows_expires` above.
CREATE INDEX IF NOT EXISTS idx_email_flow_pending_expires
  ON email_flow_pending(expires_at);
