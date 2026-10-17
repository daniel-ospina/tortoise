-- ============================================================================
-- Migration 20261012000005: the record's org-erasure path (#7968, epic #7879)
-- ----------------------------------------------------------------------------
-- The append-only journal trigger (20261012000001, `journal_event_is_append_only`)
-- refuses DELETE unless the session sets `app.record_journal_purge = 'on'`.
-- Nothing in production ever set that GUC: `purge_org_control_plane` reached the
-- organizations row through a PostgREST table DELETE (`cp.query`), which cannot
-- `SET` a session GUC. So once the journal is populated (#7969) the
-- organizations -> graphs -> journal_event cascade would fire the trigger with
-- the flag unset, the DELETE would raise, `_purge_deleted_orgs` would catch,
-- log and SKIP — and the org row (the retry anchor) would survive forever,
-- violating the retention promise. 03-schema.md §5.6 and the trigger comment
-- claimed the erasure path set the flag; it did not (review of PR #8179, P2).
--
-- This migration gives the erasure a real trigger: a SECURITY DEFINER RPC that
-- sets the flag TRANSACTION-LOCALLY and performs the delete. It is the ONE
-- production path that sets `app.record_journal_purge`, and EXECUTE is
-- restricted to service_role (the purge sweep's credential) — anon/authenticated
-- can neither set the flag nor call the function.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.purge_org_record(p_org_id text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    -- Transaction-local (is_local=true): the flag is visible to the
    -- BEFORE DELETE trigger fired on public.journal_event by the
    -- organizations -> graphs -> journal_event cascade below, and to nothing
    -- outside this transaction — the append-only guarantee is unchanged for
    -- every other session.
    PERFORM set_config('app.record_journal_purge', 'on', true);
    DELETE FROM public.organizations WHERE id = p_org_id;
END $$;

COMMENT ON FUNCTION public.purge_org_record(text) IS
    'The ONE production path that may delete public.journal_event rows: it sets app.record_journal_purge=on transaction-locally and hard-deletes the org, cascading organizations -> graphs -> journal_event. SECURITY DEFINER, service_role-only EXECUTE; called by purge_org_control_plane (#7968, PR #8179 review P2).';

REVOKE ALL ON FUNCTION public.purge_org_record(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.purge_org_record(text) TO service_role;
