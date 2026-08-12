-- Migration 0015: abuse_events — abuse detection substrate (#308)
-- Epic: Tortoise Hosted platform (docs lost in migration; reconstructed from
-- issue body) · Issue: #308
--
-- Durable abuse telemetry + enforcement for the hosted platform:
--   * abuse_events     — event log (point_create / key_create / auth_ip /
--                        flag / suspend / unsuspend), weighted rows
--   * api_keys trigger — surface-complete key_create telemetry: the ONLY seam
--                        that sees BOTH dashboard mints (insert_api_key) and
--                        the signup provision_team RPC (migration 0010)
--   * teams.suspended_at / teams.flagged_at — durable enforcement + staging
--   * abuse_suspend / abuse_unsuspend / abuse_cleanup RPCs
--
-- Design contract: docs/scoping/scoping-308-abuse-prevention.md (approach A)
-- and docs/plans/2026-08-11-308-abuse-prevention.md.

CREATE TABLE IF NOT EXISTS public.abuse_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id     text NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
    event_type  text NOT NULL,
    -- weighted rows: a bulk op records ONE row with weight=N; rule evaluation
    -- uses SUM(weight). Row-per-point would put N synchronous INSERTs in the
    -- request path and count(*) on one weighted row would undercount bulk ops.
    weight      integer NOT NULL DEFAULT 1,
    key_id      text,
    country     text,
    -- rule this row belongs to (point_create/key_create staging rows set it;
    -- per-rule flag episodes need it indexed — see abuse.py _evaluate)
    rule        text,
    details     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_abuse_events_team_type_time
    ON public.abuse_events (team_id, event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_abuse_events_team_rule_time
    ON public.abuse_events (team_id, rule, created_at DESC);

-- RLS: service_role manages all; no browser surface reads abuse telemetry.
ALTER TABLE public.abuse_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY abuse_events_service_role_all ON public.abuse_events
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Suspension + staging state on teams.
ALTER TABLE public.teams ADD COLUMN IF NOT EXISTS suspended_at timestamptz;
ALTER TABLE public.teams ADD COLUMN IF NOT EXISTS flagged_at timestamptz;

-- ── key_create trigger ─────────────────────────────────────────────────────
-- Surface completeness: sees BOTH key-create paths (dashboard insert_api_key
-- and the signup provision_team RPC). Bootstrap session keys (24h ephemeral,
-- cap-exempt) are EXCLUDED — normal dashboard session churn of 3-4 users
-- would otherwise exceed the 10/24h threshold and false-suspend (scoping
-- delta 9). Recovery mints, provision keys, and dashboard keys count.
--
-- SAFETY: provision_team (0010) is SECURITY DEFINER SET search_path='' — the
-- trigger body runs with an EMPTY search_path, so every reference must be
-- fully schema-qualified or EVERY signup fails. The body is a plain INSERT
-- (no external calls) so a trigger fault surface is minimal; a trigger error
-- aborts the parent statement by design (loud, never silent).
--
-- NOTE (backfill guard): any future migration that backfills api_keys rows
-- will emit spurious key_create events — disable this trigger for the
-- backfill statement (ALTER TABLE ... DISABLE TRIGGER ... ).
CREATE OR REPLACE FUNCTION public.trg_api_keys_abuse_fn()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.abuse_events (team_id, event_type, key_id)
    VALUES (NEW.team_id, 'key_create', NEW.id);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_api_keys_abuse ON public.api_keys;
CREATE TRIGGER trg_api_keys_abuse
    AFTER INSERT ON public.api_keys
    FOR EACH ROW
    WHEN (NEW.created_via IS DISTINCT FROM 'bootstrap')
    EXECUTE FUNCTION public.trg_api_keys_abuse_fn();

-- ── Enforcement RPCs ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.abuse_suspend(p_team_id text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    UPDATE public.teams
       SET suspended_at = now()
     WHERE id = p_team_id AND suspended_at IS NULL;
    INSERT INTO public.abuse_events (team_id, event_type)
    VALUES (p_team_id, 'suspend');
END;
$$;

CREATE OR REPLACE FUNCTION public.abuse_unsuspend(p_team_id text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- Clear BOTH enforcement and stage-1 staging state: an un-suspended team
    -- starts clean (a lingering flagged_at would let the next single-window
    -- breach escalate to suspension without a fresh flag).
    UPDATE public.teams
       SET suspended_at = NULL, flagged_at = NULL
     WHERE id = p_team_id;
    INSERT INTO public.abuse_events (team_id, event_type)
    VALUES (p_team_id, 'unsuspend');
    -- End every flag episode: without this, the first post-recovery burst
    -- would auto-suspend on the stale flag row (delta-13 regression).
    INSERT INTO public.abuse_events (team_id, event_type, rule)
    VALUES (p_team_id, 'flag_clear', 'point_create'),
           (p_team_id, 'flag_clear', 'key_create');
END;
$$;

-- Retention: ops-run cleanup (no scheduler in-product; documented in the PR
-- and .env.example). 90d keeps evidence for post-mortems and appeals.
CREATE OR REPLACE FUNCTION public.abuse_cleanup(p_days integer DEFAULT 90)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE v_deleted integer;
BEGIN
    DELETE FROM public.abuse_events
     WHERE created_at < now() - make_interval(days => p_days);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

REVOKE ALL ON FUNCTION public.abuse_suspend(text) FROM public, anon, authenticated;
REVOKE ALL ON FUNCTION public.abuse_unsuspend(text) FROM public, anon, authenticated;
REVOKE ALL ON FUNCTION public.abuse_cleanup(integer) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.abuse_suspend(text) TO service_role;
GRANT EXECUTE ON FUNCTION public.abuse_unsuspend(text) TO service_role;
GRANT EXECUTE ON FUNCTION public.abuse_cleanup(integer) TO service_role;
