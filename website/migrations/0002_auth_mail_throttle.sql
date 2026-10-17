-- #4161 — the BFF's email-request throttle (Cloudflare D1)
--
-- Why this table exists: `/auth/reset` and `/auth/resend` fold a provider 429
-- into an enumeration-safe 200, so the page's `429` branch was unreachable and a
-- throttled user was told an email had been sent when the provider had sent
-- nothing. The honest 429 has to be emitted BY THE BFF, which needs its own
-- counter — see `website/apps/dashboard/functions/_shared/auth/mail-throttle.ts`
-- for the full argument, including why the key is the caller and never the
-- address (an address-keyed limiter is the same existence oracle the fold
-- exists to prevent).
--
-- The table is created at RUNTIME by `ensureThrottleTable` in that module (the
-- same self-establishing shape as `ensureSchema`/`ensureFlowTable`), because a
-- database the runtime bootstraps alone never receives a file-applied migration.
-- This file is the explicit, reviewable statement of the shape the runtime
-- creates; the two must agree.
--
-- Apply:  wrangler d1 execute SESSIONS --file website/migrations/0002_auth_mail_throttle.sql
-- (add --local for a local database)

CREATE TABLE IF NOT EXISTS auth_mail_throttle (
  -- `<scope>:<pseudonym-of-caller>:<window-start-ms>`. One row per caller per
  -- route per fixed window. The caller is stored as a SHA-256 PSEUDONYM salted
  -- with the window — no raw address is written, and the pseudonym is not
  -- linkable across windows.
  bucket       TEXT PRIMARY KEY,
  -- Requests charged so far in this window. Post-increment: the row is written
  -- even for the request that trips the limit, so `hits > LIMIT` is the
  -- throttled state.
  hits         INTEGER NOT NULL,
  -- Fixed-window start (epoch ms). Rows older than one window are dead; the
  -- throttle reclaims a bounded slice of them when it trips.
  window_start INTEGER NOT NULL
);

-- The prune's predicate. Rows expire by window, and the sweep is cheap only if
-- this range scan is indexed.
CREATE INDEX IF NOT EXISTS idx_auth_mail_throttle_window
  ON auth_mail_throttle(window_start);
