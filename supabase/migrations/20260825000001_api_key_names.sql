-- ============================================================================
-- Migration 20260825000001: api_keys.name — user-facing key label
-- ----------------------------------------------------------------------------
-- Dashboard feature: give each API key an optional name/label so users can
-- remember which key is which ("CI", "staging", "notebook", …).
--
--   api_keys.name  (nullable text, default NULL) — free-text label. NULL =
--     unnamed key (renders '—' in the dashboard). Set at mint time
--     (POST /v1/team/keys) and editable via PATCH /v1/team/keys/{id}. Never
--     part of authentication.
--
-- Additive + idempotent (IF NOT EXISTS), same pattern as the #1148 `enabled`
-- column. The control plane reads/writes via service_role, so no new grants
-- are needed (precedent: api_keys.enabled, 20260813000005).
-- ============================================================================

ALTER TABLE public.api_keys
    ADD COLUMN IF NOT EXISTS name text;

-- Enforce the 64-char label cap at the source (the API/CLI/dashboard all
-- clamp to 64 — this makes the contract enforceable in the DB for direct
-- writes and future clients). DROP-then-ADD keeps re-apply idempotent
-- (CREATE TABLE IF NOT EXISTS skips on re-run, so ADD CONSTRAINT would
-- otherwise fail with "constraint already exists" — same pattern as the
-- 0007 created_via CHECK).
ALTER TABLE public.api_keys
    DROP CONSTRAINT IF EXISTS chk_api_keys_name_max_len;
ALTER TABLE public.api_keys
    ADD CONSTRAINT chk_api_keys_name_max_len
    CHECK (name IS NULL OR char_length(name) <= 64);
