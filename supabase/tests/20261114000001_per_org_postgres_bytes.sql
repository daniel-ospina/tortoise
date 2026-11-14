-- Assertion suite for migration 20261114000001 (#12762): per-org Postgres
-- bytes measured from what we WRITE, plus the TOAST-aware audit.
--
-- Runs against the REAL SQL functions (the harness applies every migration
-- first), on a real PostgreSQL engine — so the TOAST behaviour these
-- assertions depend on is the engine's own, not a mock. Every check RAISEs on
-- failure; the harness is ON_ERROR_STOP.
--
-- ⛔ TRANSACTION DISCIPLINE IS PART OF THE CONTRACT. The meter counts only the
-- rows THIS transaction inserted (`xmin = pg_current_xact_id()`), because a
-- losing `ON CONFLICT DO NOTHING` leaves the OLDER row in place and its bytes
-- are not this write's. So every row the meter must see is inserted INSIDE the
-- same `DO` block that asserts on it (one DO = one transaction), and §2b uses
-- the opposite case deliberately: a row committed by an EARLIER statement is
-- invisible to the meter for the same position.
--
-- ⛔ THIS SUITE CARRIES THE FALSIFIER. #12762 was filed on the premise that
-- `sum(pg_column_size(...))` reports the 18-byte TOAST pointer for a
-- moved-out value, a ~100× undercount. MEASURED (PG 17.12, 16.9 and this
-- harness's 18.3) THAT PREMISE IS FALSE: `pg_column_size` follows the TOAST
-- pointer and reports the value's stored size (compressed where compression
-- applies). The pin that "fails under the naive implementation" therefore
-- cannot be the one the issue describes — it would be green on both methods,
-- which is exactly the pin-that-cannot-fail failure of #11859.
--
-- So the falsifier here tests the SAME property (a multi-KB TOASTed payload is
-- counted at its full stored size) against the naive write-time measure that
-- measurement shows DOES fail:
--
--   naive:  `INSERT … RETURNING pg_column_size(payload)` — the datum BEFORE
--           the row is stored (pre-TOAST, pre-compression, and for jsonb its
--           uncompressed binary form): 24,606 bytes for a payload that
--           occupies 7,177. A 3.4× OVER-count.
--   ours:   the stored row, read back in the same transaction: 7,177 — the
--           chunk sum exactly.
--
-- §4 asserts that gap, so the naive method is RED here and ours is GREEN.
-- §3 asserts the issue's own premise is false, with its numbers, so no later
-- reader restores it.

-- ── Seed: three orgs, one graph each (committed by THIS statement, i.e. an
--    EARLIER transaction than the DO block below — which §2b relies on) ─────
INSERT INTO public.organizations (id, name, graph_name) VALUES
    ('org_12762_a', 'per-org-bytes-a', 'per-org-bytes-a'),
    ('org_12762_b', 'per-org-bytes-b', 'per-org-bytes-b'),
    ('org_12762_c', 'per-org-bytes-c', 'per-org-bytes-c')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.graphs (id, org_id, name, namespace) VALUES
    ('g_12762_a', 'org_12762_a', 'per-org-bytes-a', 'org_org_12762_a_g_12762_a'),
    ('g_12762_b', 'org_12762_b', 'per-org-bytes-b', 'org_org_12762_b_g_12762_b'),
    ('g_12762_c', 'org_12762_c', 'per-org-bytes-c', 'org_org_12762_c_g_12762_c')
ON CONFLICT (id) DO NOTHING;

-- A record committed in an EARLIER transaction at seq 1 — the "already there"
-- row a second write must not charge for again.
INSERT INTO public.journal_event (org_id, graph_id, seq, event_type, payload) VALUES
    ('org_12762_c', 'g_12762_c', 1, 'PointAdded',
     (SELECT jsonb_build_object('type', 'PointAdded',
                                'props', jsonb_build_object('blob',
                                        string_agg(md5(random()::text), '')))
        FROM generate_series(1, 600)));

-- ⛔ An EXPLICIT COMMIT, so the seed above is genuinely an EARLIER transaction
-- than the block below: the harness may otherwise run this whole file inside
-- one implicit transaction, in which case §2b's suppression would be a
-- same-transaction conflict and could not distinguish the two cases. (Under a
-- harness that does NOT wrap the file, PG reports "no transaction in progress"
-- as a warning — harmless.)
COMMIT;

DO $$
DECLARE
    m             jsonb;
    v_row_bytes   bigint;
    v_payload     bigint;
    v_big_logical bigint;
    v_big_stored  bigint;
    v_big_chunk   bigint;
    v_big_chunk_id bigint;
    v_row_figure  bigint;
    v_chunk_ok    boolean;
BEGIN
    -- ── 0. This transaction writes: a ~19 KB incompressible record (out of
    --      line, the shape the pointer claim was about), a 4 KB compressible
    --      one, and a small one on another org. ─────────────────────────────
    INSERT INTO public.journal_event (org_id, graph_id, seq, event_type, payload) VALUES
        ('org_12762_c', 'g_12762_c', 10, 'PointAdded',
         (SELECT jsonb_build_object('type', 'PointAdded',
                                    'props', jsonb_build_object('blob',
                                            string_agg(md5(random()::text), '')))
            FROM generate_series(1, 600))),
        ('org_12762_b', 'g_12762_b', 10, 'PointAdded',
         jsonb_build_object('type', 'PointAdded',
                            'props', jsonb_build_object('text', repeat('b', 4000)))),
        ('org_12762_a', 'g_12762_a', 10, 'PointAdded',
         jsonb_build_object('type', 'PointAdded',
                            'props', jsonb_build_object('label', 'small')));

    -- ── 1. The meter reports the stored bytes of the rows just written ──────
    m := public.journal_event_bytes_for_rows('org_12762_c', 'g_12762_c', ARRAY[10::bigint]);

    IF m IS NULL OR jsonb_typeof(m) <> 'object' THEN
        RAISE EXCEPTION '#12762: meter did not return a jsonb object: %', m;
    END IF;
    IF (m ->> 'rows')::bigint <> 1 THEN
        RAISE EXCEPTION '#12762: meter counted % rows, expected 1: %', m ->> 'rows', m;
    END IF;

    SELECT sum(pg_catalog.pg_column_size(t.*))::bigint,
           sum(pg_catalog.pg_column_size(t.payload))::bigint
      INTO v_row_bytes, v_payload
      FROM public.journal_event t
     WHERE t.org_id = 'org_12762_c' AND t.graph_id = 'g_12762_c' AND t.seq = 10;

    IF (m ->> 'row_bytes')::bigint <> v_row_bytes THEN
        RAISE EXCEPTION '#12762: meter row_bytes % disagreed with the stored '
            'row sum % — the meter must measure what Postgres stored',
            m ->> 'row_bytes', v_row_bytes;
    END IF;
    IF (m ->> 'payload_bytes')::bigint <> v_payload THEN
        RAISE EXCEPTION '#12762: meter payload_bytes % <> stored sum %',
            m ->> 'payload_bytes', v_payload;
    END IF;

    -- ── 2. Attribution: a seq the org did not write in THIS transaction is not
    --      counted, so a neighbouring org's bytes cannot leak into the figure.
    m := public.journal_event_bytes_for_rows(
            'org_12762_a', 'g_12762_a', ARRAY[10::bigint, 11::bigint]);
    IF (m ->> 'rows')::bigint <> 1 THEN
        RAISE EXCEPTION '#12762: a seq the org did not write was counted '
            '(rows=%): a neighbouring org''s bytes would be attributed to it',
            m ->> 'rows';
    END IF;

    -- ── 2b. ⛔ THE SUPPRESSED-CONFLICT GUARD (a lost position collision must
    --       NOT be charged twice). seq 1 was committed by an EARLIER
    --       transaction, so this insert is suppressed and stores nothing —
    --       yet the row exists with bytes. Summing by position alone would
    --       charge the org again; the meter must report 0.
    INSERT INTO public.journal_event (org_id, graph_id, seq, event_type, payload) VALUES
        ('org_12762_c', 'g_12762_c', 1, 'PointAdded',
         '{"type":"PointAdded","props":{"blob":"second write, must not count"}}'::jsonb)
    ON CONFLICT (org_id, graph_id, seq) DO NOTHING;

    m := public.journal_event_bytes_for_rows('org_12762_c', 'g_12762_c', ARRAY[1::bigint]);
    IF (m ->> 'rows')::bigint <> 0 OR (m ->> 'row_bytes')::bigint <> 0 THEN
        RAISE EXCEPTION '#12762: a SUPPRESSED conflict at seq 1 was metered '
            '(rows=%, row_bytes=%) — the org would be charged twice for bytes '
            'it already paid for. The meter must count only the rows THIS '
            'transaction inserted (xmin = pg_current_xact_id()).',
            m ->> 'rows', m ->> 'row_bytes';
    END IF;
    -- …and the row really does exist with bytes, so the guard above is not
    -- passing because there was nothing to double count.
    IF NOT EXISTS (SELECT 1 FROM public.journal_event t
                    WHERE t.org_id = 'org_12762_c' AND t.graph_id = 'g_12762_c'
                      AND t.seq = 1) THEN
        RAISE EXCEPTION '#12762 seed: seq 1 is absent, so the suppression '
            'assertion proves nothing';
    END IF;

    -- ── 3. THE PREMISE, FALSIFIED WITH ITS NUMBERS ─────────────────────────
    -- A 19 KB incompressible payload IS moved out of line. Under the issue's
    -- premise `pg_column_size(payload)` would report ~18 bytes (the pointer).
    -- Measured, it reports the full stored size — so the pointer never fools
    -- the figure, and THE NAIVE EXPRESSION IS NOT THE DEFECT THIS SUITE WAS
    -- ASKED TO PIN.
    SELECT octet_length(t.payload::text)::bigint,
           pg_catalog.pg_column_size(t.payload)::bigint,
           pg_catalog.pg_column_toast_chunk_id(t.payload)
      INTO v_big_logical, v_big_stored, v_big_chunk_id
      FROM public.journal_event t
     WHERE t.org_id = 'org_12762_c' AND t.graph_id = 'g_12762_c' AND t.seq = 10;

    IF v_big_logical < 15000 THEN
        RAISE EXCEPTION '#12762 seed: expected a ~19 KB payload, got % bytes',
            v_big_logical;
    END IF;
    IF v_big_chunk_id IS NULL THEN
        RAISE EXCEPTION '#12762 seed: the payload was NOT moved out of line, '
            'so the TOAST assertions below would prove nothing';
    END IF;
    IF v_big_stored < 1000 THEN
        RAISE EXCEPTION '#12762: pg_column_size() reported % bytes for a % B '
            'out-of-line payload — a pointer-shaped reading. The measured '
            'behaviour (PG 16.9/17.12/18.3) is the full stored size; if an '
            'engine ever does report the pointer, THE METER must not be built '
            'on it.', v_big_stored, v_big_logical;
    END IF;

    -- ── 4. THE FALSIFIER: the naive write-time measure is WRONG, ours isn't ─
    -- `INSERT … RETURNING pg_column_size(payload)` sees the datum BEFORE the
    -- row is stored: pre-TOAST, pre-compression, and for jsonb in its
    -- UNCOMPRESSED BINARY form. It is the natural "free" write-time measure and
    -- it OVER-counts.
    --
    -- ⚠️ THE GAP IS SHAPE-DEPENDENT, and the shape must be OUR record shape.
    -- Measured while writing this suite: on a 19 KB INCOMPRESSIBLE random-text
    -- payload the naive figure is 19,260 against 19,236 stored — a 1.001×
    -- difference that would make this pin toothless (and it DID fail here
    -- first, which is why the row below carries a 1,536-float VECTOR instead).
    -- jsonb encodes numbers in binary `numeric` form, so a vector payload's
    -- uncompressed binary size is far ABOVE its JSON text: RETURNING 24,606 vs
    -- 7,177 stored — a 3.4× over-count. That is the payload shape this product
    -- actually writes, so that is the shape the falsifier must use.
    CREATE TEMP TABLE IF NOT EXISTS t12762_returning (
        returning_bytes bigint, seq bigint);
    DELETE FROM t12762_returning;
    WITH ins AS (
        INSERT INTO public.journal_event (org_id, graph_id, seq, event_type, payload)
        VALUES ('org_12762_c', 'g_12762_c', 11, 'PointAdded',
                jsonb_build_object('type', 'PointAdded',
                    'props', jsonb_build_object('label', 'finding',
                        'embedding', (SELECT jsonb_agg(round((random())::numeric, 6))
                                        FROM generate_series(1, 1536)))))
        RETURNING pg_catalog.pg_column_size(payload) AS returning_bytes)
    INSERT INTO t12762_returning SELECT returning_bytes, 11 FROM ins;

    SELECT t.returning_bytes INTO v_big_stored FROM t12762_returning t;
    SELECT pg_catalog.pg_column_size(j.payload)::bigint
      INTO v_big_logical
      FROM public.journal_event j
     WHERE j.org_id = 'org_12762_c' AND j.graph_id = 'g_12762_c' AND j.seq = 11;

    IF v_big_stored <= v_big_logical THEN
        RAISE EXCEPTION '#12762 FALSIFIER DID NOT FIRE: RETURNING reported % '
            'and the stored size is % — the naive write-time measure must '
            'OVER-count on this engine or the suite proves nothing',
            v_big_stored, v_big_logical;
    END IF;
    -- The over-count must be SUBSTANTIAL, not a rounding difference: a pin that
    -- only distinguishes 19,260 from 19,236 cannot fail for the right reason.
    IF v_big_stored < v_big_logical * 2 THEN
        RAISE EXCEPTION '#12762 FALSIFIER IS TOOTHLESS: RETURNING % vs stored '
            '% is under 2x — pick a payload shape whose stored size truly '
            'diverges from its pre-TOAST binary size',
            v_big_stored, v_big_logical;
    END IF;

    m := public.journal_event_bytes_for_rows('org_12762_c', 'g_12762_c', ARRAY[11::bigint]);
    IF (m ->> 'row_bytes')::bigint >= v_big_stored THEN
        RAISE EXCEPTION '#12762 FALSIFIER: the meter reported % against the '
            'naive RETURNING figure % — the two must DIFFER for this pin to '
            'have teeth', m ->> 'row_bytes', v_big_stored;
    END IF;

    -- ── 5. The double-count trap (the live one) ────────────────────────────
    -- `pg_column_size(t.*)` ALREADY contains the out-of-line value's bytes, so
    -- adding the chunk bytes double counts. Pin the containment: for the big
    -- row the difference between the row figure and the chunk figure is only
    -- the row's own header/other columns.
    v_chunk_ok := EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
                           WHERE p.proname = 'pg_column_toast_chunk_id');
    IF v_chunk_ok THEN
        m := public.journal_event_bytes_for_rows('org_12762_c', 'g_12762_c', ARRAY[10::bigint]);
        IF (m ->> 'chunk_detail')::boolean IS NOT TRUE THEN
            RAISE EXCEPTION '#12762: chunk_detail must be true on a server '
                'that HAS pg_column_toast_chunk_id (it is missing from the reply)';
        END IF;
        v_big_chunk := (m ->> 'chunk_bytes')::bigint;
        v_row_figure := (m ->> 'row_bytes')::bigint;
        IF coalesce(v_big_chunk, 0) <= 0 THEN
            RAISE EXCEPTION '#12762: chunk_bytes is % for a TOASTed row — '
                'the audit cannot disclose chunk overhead', v_big_chunk;
        END IF;
        -- The value is INSIDE row_bytes: row_bytes minus chunk_data is the
        -- row's own small overhead, never a second copy of the payload.
        IF v_row_figure < v_big_chunk THEN
            RAISE EXCEPTION '#12762: row_bytes (%) < chunk_bytes (%) — the '
                'meter does not contain the out-of-line value',
                v_row_figure, v_big_chunk;
        END IF;
        IF v_row_figure - v_big_chunk > 500 THEN
            RAISE EXCEPTION '#12762: row_bytes exceeds chunk_bytes by % bytes '
                'for a single row — that gap is the signature of the '
                'double-count trap (row + chunks) this meter must avoid',
                v_row_figure - v_big_chunk;
        END IF;
        -- A per-org figure that equals row + chunks is the double count made
        -- visible. It would nearly DOUBLE the figure (measured 14,418 against
        -- 7,241), so "at least the row figure plus half the chunk figure"
        -- catches any partial folding of chunks into the total, not just an
        -- exact 2x. (The exact 2x check this replaces read a variable that the
        -- lines above had already overwritten — a vacuous assertion of the
        -- #11859 class.)
        IF v_row_figure >= v_big_chunk * 2 THEN
            RAISE EXCEPTION '#12762: row_bytes (%) is at least twice '
                'chunk_bytes (%) — the row+chunks double count is folded in',
                v_row_figure, v_big_chunk;
        END IF;
    ELSE
        -- PG < 17: chunk attribution is UNAVAILABLE, and must read as such —
        -- never as "no chunks".
        m := public.journal_event_bytes_for_rows('org_12762_c', 'g_12762_c', ARRAY[10::bigint]);
        IF (m ->> 'chunk_detail')::boolean IS NOT FALSE
           OR (m ->> 'chunk_bytes') IS NOT NULL THEN
            RAISE EXCEPTION '#12762: on a server WITHOUT '
                'pg_column_toast_chunk_id, chunk detail must be false and '
                'chunk_bytes NULL (absent, never zero): %', m;
        END IF;
    END IF;

    -- ── 6. A position this transaction did not insert is a measured ZERO ────
    m := public.journal_event_bytes_for_rows('org_12762_a', 'g_12762_a', ARRAY[999::bigint]);
    IF (m ->> 'rows')::bigint <> 0 OR (m ->> 'row_bytes')::bigint <> 0 THEN
        RAISE EXCEPTION '#12762: an unwritten seq must read as 0 rows / 0 bytes, '
            'got %', m;
    END IF;

    -- ── 7. The AUDIT: per-org, and the residual kept SEPARATE ──────────────
    DECLARE
        a            jsonb;
        v_attributed bigint;
        v_sum_orgs   bigint;
        v_total      bigint;
        v_residual   bigint;
        v_orgs       jsonb;
        v_org_bytes  bigint;
        v_heap       bigint;
        v_part_sum   bigint;
    BEGIN
        a := public.postgres_per_org_bytes();
        v_attributed := (a ->> 'attributed_bytes')::bigint;
        v_orgs := a -> 'orgs';
        v_total := (a -> 'residual' ->> 'total_relation_bytes')::bigint;
        v_residual := (a -> 'residual' ->> 'residual_bytes')::bigint;

        IF jsonb_typeof(v_orgs) <> 'array' THEN
            RAISE EXCEPTION '#12762: audit orgs must be an array, got %',
                jsonb_typeof(v_orgs);
        END IF;

        SELECT coalesce(sum((o ->> 'row_bytes')::bigint), 0)
          INTO v_sum_orgs
          FROM jsonb_array_elements(v_orgs) o;
        IF v_sum_orgs <> v_attributed THEN
            RAISE EXCEPTION '#12762: the audit''s per-org sum (%) disagrees '
                'with its own attributed total (%) — a truncated or '
                'inconsistent payload', v_sum_orgs, v_attributed;
        END IF;

        -- The audit's per-org figure AGREES with (and cannot be BELOW) the
        -- meter for an org whose rows were all written through the meter. Two
        -- independent reads of the same property: the point of the pair.
        SELECT (o ->> 'row_bytes')::bigint
          INTO v_org_bytes
          FROM jsonb_array_elements(v_orgs) o
         WHERE o ->> 'org_id' = 'org_12762_c';
        m := public.journal_event_bytes_for_rows(
                'org_12762_c', 'g_12762_c', ARRAY[10::bigint, 11::bigint]);
        IF v_org_bytes < (m ->> 'row_bytes')::bigint THEN
            RAISE EXCEPTION '#12762: the audit reports % bytes for org_12762_c '
                'but the write-time meter accumulated % — the audit must be '
                'the ground truth, never below the meter',
                v_org_bytes, m ->> 'row_bytes';
        END IF;

        -- ⛔ The total must be the relation's WHOLE footprint. heap + parent
        -- index + toast omits the toast INDEX and both FSM/VM forks (measured:
        -- 65,536 against pg_total_relation_size 106,496), which would understate
        -- the residual and still "reconcile".
        v_heap := pg_catalog.pg_total_relation_size('public.journal_event'::regclass);
        v_part_sum := (a -> 'residual' ->> 'heap_relation_bytes')::bigint
                    + (a -> 'residual' ->> 'index_bytes')::bigint
                    + (a -> 'residual' ->> 'toast_relation_bytes')::bigint;
        IF v_total <> v_heap THEN
            RAISE EXCEPTION '#12762: the audit total (%) is not '
                'pg_total_relation_size (%) — the TOAST index and FSM/VM are '
                'missing from it', v_total, v_heap;
        END IF;
        IF v_total <= v_part_sum THEN
            RAISE EXCEPTION '#12762: the audit total (%) is not above the sum '
                'of its own parts (%) — the whole-footprint figure is not '
                'being computed', v_total, v_part_sum;
        END IF;

        -- The residual is SEPARATE and LABELLED, and it cannot be folded into
        -- a per-org figure without an allocation.
        IF v_residual <= 0 THEN
            RAISE EXCEPTION '#12762: audit residual is % — a footprint table '
                'always has index/page overhead beyond the tuples, so a '
                'non-positive residual means it is not being computed',
                v_residual;
        END IF;
        IF v_attributed + v_residual <> v_total THEN
            RAISE EXCEPTION '#12762: attributed (%) + residual (%) <> total '
                '(%) — the residual must be the remainder, not a guess',
                v_attributed, v_residual, v_total;
        END IF;
        IF (a -> 'residual' ->> 'index_bytes')::bigint <= 0 THEN
            RAISE EXCEPTION '#12762: the audit reports no index bytes; the '
                'primary key index must be disclosed in the residual';
        END IF;
        IF v_attributed >= v_total THEN
            RAISE EXCEPTION '#12762: attributed bytes (%) reached the whole '
                'relation (%) — indexes/page overhead cannot be attributed '
                'per org, so this means the residual was folded in',
                v_attributed, v_total;
        END IF;
    END;
END $$;

-- ── 8. The ledger increment accumulates, and refuses corruption ─────────────
DO $$
DECLARE
    v_a timestamptz := '2026-11-01T00:00:00+00:00'::timestamptz;
    v_b timestamptz := '2026-12-01T00:00:00+00:00'::timestamptz;
    v_ok boolean := false;
BEGIN
    PERFORM public.metering_increment_postgres_bytes(
        'org_12762_a', v_a, v_b, 1000, 3);
    PERFORM public.metering_increment_postgres_bytes(
        'org_12762_a', v_a, v_b, 500, 1);

    IF (SELECT postgres_bytes_written FROM public.metering_records
         WHERE org_id = 'org_12762_a' AND period_start = v_a) <> 1500 THEN
        RAISE EXCEPTION '#12762: the ledger did not ACCUMULATE (expected 1500)';
    END IF;
    IF (SELECT postgres_bytes_records FROM public.metering_records
         WHERE org_id = 'org_12762_a' AND period_start = v_a) <> 4 THEN
        RAISE EXCEPTION '#12762: the record counter did not accumulate (expected 4)';
    END IF;
    IF (SELECT period_end FROM public.metering_records
         WHERE org_id = 'org_12762_a' AND period_start = v_a) <> v_b THEN
        RAISE EXCEPTION '#12762: the ledger row''s window is not the supplied one';
    END IF;

    -- A degenerate window mints a row no reader can match — must RAISE.
    BEGIN
        PERFORM public.metering_increment_postgres_bytes(
            'org_12762_a', v_b, v_a, 1, 1);
    EXCEPTION WHEN others THEN v_ok := true;
    END;
    IF NOT v_ok THEN
        RAISE EXCEPTION '#12762: a degenerate (end <= start) window was ACCEPTED';
    END IF;

    -- Bytes cannot be un-written: a negative increment is corruption.
    v_ok := false;
    BEGIN
        PERFORM public.metering_increment_postgres_bytes(
            'org_12762_a', v_a, v_b, -1, 1);
    EXCEPTION WHEN others THEN v_ok := true;
    END;
    IF NOT v_ok THEN
        RAISE EXCEPTION '#12762: a NEGATIVE byte increment was accepted — the '
            'counter could then be driven backwards';
    END IF;
END $$;

-- ── 9. The dimension is registered but NOT armed ───────────────────────────
-- Nothing in this migration may price, cap or throttle. The gate's arming is a
-- separate, owner-gated change (#12762: the free/anon included amounts are
-- undecided), so this pin fires if someone wires the meter straight into
-- enforcement.
DO $$
DECLARE
    v_cols integer;
BEGIN
    SELECT count(*) INTO v_cols
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'metering_records'
       AND column_name IN ('postgres_bytes_written', 'postgres_bytes_records');
    IF v_cols <> 2 THEN
        RAISE EXCEPTION '#12762: the metering ledger is missing the byte '
            'columns (found %)', v_cols;
    END IF;

    -- ⛔ THE ROLE THAT CALLS IT MUST BE ABLE TO. The record write path
    -- connects as `tortoise_record_app` (NOBYPASSRLS; `_apply_tenant_scope`
    -- refuses an owner/superuser/BYPASSRLS role), so a grant to `service_role`
    -- alone leaves every live write at `permission denied for function` — a
    -- silent no-op behind the fail-soft wrapper. Where the role exists in this
    -- cluster, assert it can EXECUTE and that the meter is INVOKER-rights (so
    -- the org's own tenant policy applies to the read).
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tortoise_record_app') THEN
        IF NOT has_function_privilege(
                'tortoise_record_app',
                'public.journal_event_bytes_for_rows(text, text, bigint[])',
                'EXECUTE') THEN
            RAISE EXCEPTION '#12762: tortoise_record_app CANNOT execute the '
                'meter — every live write would meter nothing and the failure '
                'would be swallowed by the fail-soft wrapper';
        END IF;
        IF (SELECT p.prosecdef FROM pg_catalog.pg_proc p
             WHERE p.oid =
                   'public.journal_event_bytes_for_rows(text, text, bigint[])'::regprocedure)
           IS NOT FALSE THEN
            RAISE EXCEPTION '#12762: the meter must be SECURITY INVOKER — a '
                'DEFINER function lets the tenancy role read another org''s '
                'byte aggregate past its own RLS policy';
        END IF;
    END IF;

    -- The included-amount / cap columns this lane must NOT have written.
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'metering_records'
                  AND column_name IN ('postgres_bytes_included',
                                      'postgres_bytes_overage')) THEN
        RAISE EXCEPTION '#12762: an included-amount/overage column landed — '
            'this change is measurement only and must NOT arm enforcement';
    END IF;
END $$;

-- ── 10. THE RUN, not the grant: the meter must EXECUTE as the tenancy role ──
-- ⛔ `has_function_privilege` (asserted above) is a PROXY, and this suite runs
-- as the migration applier — a superuser — so a proxy is all it was: the
-- function can hold the grant and STILL fail for the role that calls it. It
-- did. The chunk-disclosure read joins `%s` (the pg_toast chunk relation),
-- and a NOBYPASSRLS app role holds neither USAGE on schema `pg_toast` nor
-- SELECT on that relation: measured on PG 17 the call raised
-- `permission denied for schema pg_toast` on EVERY healthy write. Unguarded
-- that raise aborts the CALLER's transaction, and a COMMIT on an aborted
-- transaction returns silently after rolling back — so the record write would
-- have been LOST while its wrapper reported APPLIED, and the meter would have
-- read 0 forever. Green-on-a-grant is how that shipped twice.
--
-- So this block reproduces the PRODUCTION posture — a fresh NOBYPASSRLS role
-- holding exactly what the record role holds, reading through RLS with the
-- tenant GUC set — and asserts the RUN: rows and bytes are metered, and the
-- chunk disclosure degrades to ABSENT (never 0, never a raise).
DO $$
DECLARE
    v_seq   bigint;
    v_out   jsonb;
    v_role  text := 't12762_record_app';
    v_prior text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
        EXECUTE format('DROP OWNED BY %I', v_role);
        EXECUTE format('DROP ROLE %I', v_role);
    END IF;
    EXECUTE format('CREATE ROLE %I NOLOGIN NOBYPASSRLS', v_role);
    -- Exactly the record role's posture: read the journal, run the meter.
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', v_role);
    EXECUTE format('GRANT SELECT ON public.journal_event TO %I', v_role);
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION '
        'public.journal_event_bytes_for_rows(text, text, bigint[]) TO %I',
        v_role);

    v_prior := current_user;
    BEGIN
        EXECUTE format('SET LOCAL ROLE %I', v_role);
        -- The GUC the tenancy policy reads; the app sets it per transaction.
        PERFORM set_config('app.current_org_id', 'org_12762_a', true);

        SELECT max(seq) INTO v_seq
          FROM public.journal_event
         WHERE org_id = 'org_12762_a';

        -- ⛔ THE ASSERTION THAT WOULD HAVE CAUGHT THE LOST-WRITE DEFECT: a
        -- raise here (or a 0-row answer) is red, not a swallowed no-op.
        v_out := public.journal_event_bytes_for_rows(
            'org_12762_a', 'graph_12762_a', ARRAY[v_seq]);

        IF (v_out->>'rows')::bigint < 1 THEN
            RAISE EXCEPTION '#12762: the meter returned % row(s) when run AS '
                'the tenancy role — every live write would meter nothing', 
                v_out->>'rows';
        END IF;
        IF (v_out->>'row_bytes')::bigint <= 0 THEN
            RAISE EXCEPTION '#12762: the meter returned row_bytes=% as the '
                'tenancy role', v_out->>'row_bytes';
        END IF;
        -- The role cannot read pg_toast, so the DISCLOSURE is absent — and it
        -- must be ABSENT, not 0, and never an error. Nothing is billed from
        -- it, so this is the correct degenerate.
        IF (v_out->>'chunk_detail')::boolean IS TRUE THEN
            RAISE EXCEPTION '#12762: chunk_detail is TRUE for a role that '
                'cannot read pg_toast — the figure cannot be trusted';
        END IF;
        IF v_out->'chunk_bytes' <> 'null'::jsonb THEN
            RAISE EXCEPTION '#12762: chunk_bytes is % for a role that cannot '
                'read pg_toast — absent must never read as zero',
                v_out->'chunk_bytes';
        END IF;

        -- And the transaction is still USABLE after the call: this is the
        -- property the subtransaction guard exists to give the caller.
        PERFORM 1;
    EXCEPTION WHEN others THEN
        EXECUTE format('RESET ROLE');
        EXECUTE format('DROP OWNED BY %I', v_role);
        EXECUTE format('DROP ROLE %I', v_role);
        RAISE;
    END;
    EXECUTE 'RESET ROLE';
    EXECUTE format('DROP OWNED BY %I', v_role);
    EXECUTE format('DROP ROLE %I', v_role);
END $$;

-- ── Clean up ───────────────────────────────────────────────────────────────
-- `journal_event` refuses DELETE unless the tenant-erasure flag is set (the
-- append-only trigger, migration 20261012000001) — the same flag the cascade
-- from `public.organizations` is designed to ride.
SET app.record_journal_purge = 'on';
DELETE FROM public.organizations WHERE id IN
    ('org_12762_a', 'org_12762_b', 'org_12762_c');
RESET app.record_journal_purge;
DROP TABLE IF EXISTS t12762_returning;
