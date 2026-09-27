-- Migration 20260927000001: metering_unmetered_increments — a durable
-- representation of an increment the period-keyed ledger CANNOT hold
-- (#4779, leg 2 of the #3981 ruling "PROCEED AND ALERT").
--
-- WHY. #3981 has three legs. Leg 1 (the request proceeds) and leg 3 (an
-- operator alert fires) shipped. Leg 2 — "the increment is recorded as
-- explicitly unmeterable" — had no artifact.
--
-- ``metering._require_period`` raises :class:`QuotaCheckError` for an org whose
-- billing anchor is unusable, and every production caller absorbs the raise and
-- serves the request, so ``record_*`` never runs. The ledger row is therefore
-- simply ABSENT — and absent is what an org that genuinely spent zero produces.
-- Each writer's OWN increment handler has the same shape: it logs at WARNING and
-- returns ``None`` when the increment RPC fails (the window IS known there), so
-- that increment is dropped with the same absence.
--
-- WHY A NEW TABLE AND NOT A ROW IN ``metering_records``. That table's identity
-- IS the window: PK ``(org_id, period_start)`` (20260918000001:138,141) with
-- both ``period_start``/``period_end`` set ``NOT NULL`` (:133,134). A
-- window-unresolvable increment has no window, so it is not representable
-- there *without inventing one* — and inventing a window is the exact thing
-- #3825 exists to forbid (a substituted bucket attributes spend to a row the
-- cap's own read may never look at). A sentinel window is worse than
-- unrepresentable: it lands INSIDE the cap's overlap read
-- (``period_start < E AND period_end > S``) and entangles the two
-- representations, so any future column added to that SUM silently becomes a
-- cap input. Hence a SEPARATE surface, keyed by things that exist when the
-- window does not: **org + lane + drop class + observation time**.
--
-- WHAT THIS IS NOT.
--   * NOT a cap input. ``metering_cohort_spend`` (and
--     ``metering.get_cohort_spend_usd``) keep reading ONLY
--     ``ask_cost_usd + capture_cost_usd`` from ``metering_records``. Nothing
--     here is joined into that SUM; a figure the ceiling cannot bound must not
--     become a ceiling input (#4779 constraint 1).
--   * NOT a dollar amount. ``increments`` is a COUNT of dropped increments —
--     the fact we actually have on every lane (a write-op increment carries no
--     cost at all, so a cross-lane dollar total would be a partial sum
--     presented as a total).
--   * NOT a reset/reconciliation ledger. It is an evidence record: cumulative
--     by design and never cleared, because clearing it is the same loss class
--     this table exists to remove. ``first_observed_at``/``last_observed_at``
--     carry the window-free substitute for attribution — "since when" and "is
--     it still happening" (``last_observed_at`` stops advancing once the org is
--     repaired).
--   * NOT a replacement for leg 3. The alert stays the mitigation; this is the
--     representation. A CP-wide outage drops the increment AND blocks this
--     write (both drop causes are control-plane failures), and the alert
--     channel — R2 + GitHub + Telegram, not the CP — is the backstop there.
--
-- DECLARED DROP CLASSES. ``drop_class`` is a CLOSED vocabulary enforced by
-- CHECK, because an ad-hoc string here would re-create the re-derivation defect
-- at the representation layer (the element #5047 tracks as the shared root).
-- Adding a class is a migration, deliberately:
--   * ``window_unresolvable`` — ``_require_period`` raised; ``lane`` names the
--     caller's swallow site (the same six tokens leg 3 reports);
--   * ``increment_failed``    — the writer's own increment call failed with the
--     window KNOWN; ``lane`` names the writer (``write_op``/``ask_ledger``/
--     ``capture_ledger``).
-- ``lane`` itself is deliberately NOT constrained: the swallow-site inventory is
-- enumerated from source by ``tests/test_metering_window_admission.py``, and a
-- CHECK here would be a second, hand-maintained copy of that list.
--
-- MODE PARITY. ``tortoise/metering.py`` writes this through the Supabase seam in
-- Supabase mode and as a ``:MeteringUnmeteredIncrement`` node in the embedded/
-- registry lane — the same two-lane shape every other metering writer has.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.metering_unmetered_increments (
    org_id            text NOT NULL
        REFERENCES public.organizations(id) ON DELETE CASCADE,
    lane              text NOT NULL,
    drop_class        text NOT NULL,
    increments        integer NOT NULL DEFAULT 0,
    last_error_type   text,
    first_observed_at timestamptz NOT NULL DEFAULT now(),
    last_observed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, lane, drop_class),
    CONSTRAINT metering_unmetered_drop_class_declared
        CHECK (drop_class IN ('window_unresolvable', 'increment_failed')),
    CONSTRAINT metering_unmetered_increments_positive
        CHECK (increments > 0)
);

COMMENT ON TABLE public.metering_unmetered_increments IS
    '#4779 (leg 2 of #3981): increments the period-keyed meter could NOT record, '
    'because the org''s window was unresolvable or the increment write failed. '
    'Keyed by (org_id, lane, drop_class) — all three exist when the window does '
    'not. A COUNT, never a dollar figure, and NEVER read by metering_cohort_spend.';
COMMENT ON COLUMN public.metering_unmetered_increments.lane IS
    '#4779: the metering lane the drop happened on. For window_unresolvable it is '
    'the caller''s swallow site (the same six tokens the UNMETERED_INCREMENT '
    'incident reports); for increment_failed it is the writer '
    '(write_op/ask_ledger/capture_ledger). Unconstrained on purpose — the site '
    'inventory is enumerated from source by tests/test_metering_window_admission.py.';
COMMENT ON COLUMN public.metering_unmetered_increments.drop_class IS
    '#4779: the DECLARED drop vocabulary (CHECK-enforced): window_unresolvable | '
    'increment_failed. Adding a class is a migration, not an ad-hoc literal.';
COMMENT ON COLUMN public.metering_unmetered_increments.increments IS
    '#4779: how many increments this org+lane+class dropped. CUMULATIVE and never '
    'reset (a reset erases the evidence). Not money and not a cap input.';
COMMENT ON COLUMN public.metering_unmetered_increments.last_error_type IS
    '#4779: the exception CLASS of the most recent drop (e.g. QuotaCheckError). A '
    'diagnostic payload: a record that cannot name what broke is a filing defect.';
COMMENT ON COLUMN public.metering_unmetered_increments.first_observed_at IS
    '#4779: when this org+lane+class was FIRST seen dropping. Preserved across '
    'upserts. Together with last_observed_at it is the window-free substitute for '
    'period attribution: since-when, and (by whether last_observed_at still '
    'advances) whether it is still happening.';
COMMENT ON COLUMN public.metering_unmetered_increments.last_observed_at IS
    '#4779: when the most recent drop was observed. Advances on every upsert, and '
    'stops advancing once the org is repaired.';

-- RLS: service_role manages all; no browser surface reads metering.
ALTER TABLE public.metering_unmetered_increments ENABLE ROW LEVEL SECURITY;

-- DROP-then-CREATE so a re-apply (an out-of-band / unrecorded deploy) is
-- idempotent rather than a duplicate-policy error.
DROP POLICY IF EXISTS metering_unmetered_service_role_all
    ON public.metering_unmetered_increments;
CREATE POLICY metering_unmetered_service_role_all
    ON public.metering_unmetered_increments
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- THE WRITER — atomic, so two concurrent drops of one org+lane+class cannot
-- lose an update (the same reason ``metering_increment`` is an RPC and not a
-- GET-then-PATCH; see 0014's review P2, PR #911).
--
-- STRICT ARGUMENTS. An empty org_id, an empty lane, or a non-positive count is
-- refused rather than recorded: a row with no org cannot be read alongside
-- anything, and a zero increment is precisely the "indistinguishable from zero"
-- state this table exists to distinguish. The Python writer swallows the raise
-- (metering must never block a request) and logs it.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.metering_record_unmetered(
    p_org_id     text,
    p_lane       text,
    p_drop_class text,
    p_error_type text,
    p_n          integer DEFAULT 1
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE v_total integer;
BEGIN
    IF p_org_id IS NULL OR btrim(p_org_id) = '' THEN
        RAISE EXCEPTION 'metering_record_unmetered: p_org_id is required';
    END IF;
    IF p_lane IS NULL OR btrim(p_lane) = '' THEN
        RAISE EXCEPTION 'metering_record_unmetered: p_lane is required';
    END IF;
    IF p_n IS NULL OR p_n < 1 THEN
        RAISE EXCEPTION
            'metering_record_unmetered: p_n must be >= 1 (got %) — a zero '
            'increment is the state this table exists to distinguish', p_n;
    END IF;
    INSERT INTO public.metering_unmetered_increments
        (org_id, lane, drop_class, increments, last_error_type)
    VALUES (p_org_id, p_lane, p_drop_class, p_n, p_error_type)
    ON CONFLICT (org_id, lane, drop_class)
    DO UPDATE SET
        increments       = public.metering_unmetered_increments.increments + p_n,
        last_error_type  = p_error_type,
        last_observed_at = now()
    -- first_observed_at is deliberately ABSENT from the update: it is the
    -- since-when of the episode and must survive every subsequent drop.
    RETURNING increments INTO v_total;
    RETURN v_total;
END;
$$;

REVOKE ALL ON FUNCTION public.metering_record_unmetered(text, text, text, text,
    integer) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_record_unmetered(text, text, text,
    text, integer) TO service_role;

-- ============================================================================
-- THE READERS — two, and both are deliberately row-cap-proof.
--
-- PostgREST silently caps a row LIST at the project's ``db-max-rows``. A
-- truncated read here UNDERSTATES how long an org has been unmeterable, which
-- is the one question this table exists to answer (the same failure mode that
-- made ``metering_cohort_spend`` an RPC, 20260917000001 §"The cap's two READS").
-- So: a per-org read, bounded by construction at lanes x classes (<= 12 rows),
-- and a cohort read that returns a SINGLE scalar.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.metering_unmetered_for_org(p_org_id text)
RETURNS TABLE (
    lane              text,
    drop_class        text,
    increments        integer,
    last_error_type   text,
    first_observed_at timestamptz,
    last_observed_at  timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT m.lane, m.drop_class, m.increments, m.last_error_type,
           m.first_observed_at, m.last_observed_at
      FROM public.metering_unmetered_increments AS m
     WHERE m.org_id = p_org_id
     ORDER BY m.lane, m.drop_class;
$$;

REVOKE ALL ON FUNCTION public.metering_unmetered_for_org(text)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_unmetered_for_org(text)
    TO service_role;

-- One scalar. Read this ALONGSIDE ``metering_cohort_spend`` — never inside it:
-- a count is not spend, and folding it into a spend ceiling is the behaviour
-- change #4779 declares out of scope.
CREATE OR REPLACE FUNCTION public.metering_unmetered_total(p_org_ids text[])
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT coalesce(sum(m.increments), 0)::bigint
      FROM public.metering_unmetered_increments AS m
     WHERE m.org_id = ANY(p_org_ids);
$$;

REVOKE ALL ON FUNCTION public.metering_unmetered_total(text[])
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_unmetered_total(text[])
    TO service_role;
