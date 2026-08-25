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
