-- Migration 20261114000001: PER-ORG Postgres bytes from what we WRITE (#12762)
--
-- WHAT THIS IS. The storage dimension landed at DATABASE grain in #4492 and
-- recorded why: *"no per-org figure exists, and none is invented. Per-org
-- BYTES would be an ALLOCATION, not a measurement"*. That sentence was right
-- about the method it was written against (apportioning the database total by
-- a proxy) and wrong about every possible method: the rows we write are keyed
-- by `org_id`, so the bytes THEY occupy are attributable to the org that wrote
-- them. This migration supplies the two halves that make that attribution a
-- MEASUREMENT:
--
--   1. `journal_event_bytes_for_rows(org, graph, seqs)` — the METER. Reads the
--      stored size of the journal rows a caller has just written, in the
--      caller's own transaction. Per row, per org, exact.
--   2. `postgres_per_org_bytes()` — the AUDIT / ground truth. Recomputes the
--      same figure for the whole table, per org, independently and later, and
--      publishes the bytes that CANNOT be attributed per org as a **separate,
--      labelled residual** — never folded into a per-org number.
--
--     3. `metering_records.postgres_bytes_written` + its increment RPC, so the
--        accumulated figure rides the SAME per-(org, window) ledger the ask,
--        capture, embed and graph-storage lanes use. No new store.
--
-- ⛔ THE TOAST QUESTION IS SETTLED BY MEASUREMENT (2026-11-14, PG 17.12 and
-- PG 16.9, table shaped exactly like this one; full table in
-- `docs/ops/postgres-per-org-bytes.md`). THE ISSUE'S PREMISE IS FALSIFIED:
-- `pg_column_size()` on an OUT-OF-LINE value does NOT report the 18-byte
-- pointer — it follows the TOAST pointer and reports the value's stored size.
-- Measured: a 19,245-byte payload stored out of line in 10 chunks reports
-- **19,256** (the chunk-sum exactly), not 18. The disagreeing sources resolve
-- to "full stored size", with one refinement: the figure is the COMPRESSED
-- stored size where compression applied (a 15,428-byte vector payload →
-- **7,177**; a 4,045-byte repetitive payload compressed INLINE → **114**).
--
--   ⇒ So `pg_column_size(t.*)` IS a per-row, per-org, stored-byte
--     measurement, and no TOAST pointer can fool it. That is what both
--     functions below sum.
--
-- ⛔ AND THE TRAP THAT *IS* LIVE — DOUBLE COUNTING. `pg_column_size(t.*)`
-- already INCLUDES an out-of-line value's stored bytes. Adding the TOAST
-- chunk bytes to it double counts (measured: 7,241 + 7,177 = 14,418 for a row
-- that occupies 7,241). `chunk_bytes` is therefore returned by BOTH functions
-- **for disclosure only** — it proves the value is inside the row figure and
-- it sizes the chunk overhead the residual must carry — and it is NEVER added
-- to any per-org total. `tests/…_per_org_postgres_bytes.sql` pins that.
--
-- ⛔ NOT ARMED, NOT PRICED. Nothing here prices, caps, throttles, refuses, or
-- sets a tier. `allowance_gate` is untouched and its `free`/`anon` included
-- amounts are still undecided (#12762 is measurement only).
--
-- ⛔ NOT A RETENTION CHANGE. `#3843`/`#3853`'s "no retention window of any
-- length" ruling binds `analytics_events` (migration 0004's immutable
-- trigger); it says nothing about `journal_event` bytes. This migration adds
-- no prune and no sweep either way.
--
-- DEPLOY ORDER: this migration FIRST, the Python second. The wrapper is
-- fail-soft — a missing function is logged and the reading is dropped — so
-- deploying the code first would yield a SILENT ZERO, which is the fail-open
-- this ledger exists to avoid (the same note 20261001000005 carries).
--
-- WHY jsonb VALUES AND NOT ROW SETS: PostgREST silently caps a row LIST at the
-- deployment's `db-max-rows`, and a silently short read understates a
-- footprint. Both functions return ONE jsonb value per call, so the
-- truncation mode is designed out rather than detected (the reason #4492's
-- migration gives for the same choice).

-- ============================================================================
-- 1. The ledger column. An INCREMENT (cumulative workload), not a gauge.
-- ============================================================================
ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS postgres_bytes_written bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS postgres_bytes_records bigint NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.metering_records.postgres_bytes_written IS
    '#12762: bytes the org''s journal rows occupy in Postgres, accumulated at '
    'WRITE TIME from the stored size of the rows just written '
    '(sum(pg_column_size(t.*)) over public.journal_event). This is a '
    'MEASUREMENT of stored content: it is the COMPRESSED stored size where '
    'compression applied and it follows the TOAST pointer (measured PG 17.12: '
    'a 19,245 B out-of-line payload reports 19,256, not an 18 B pointer). '
    'Excludes index bytes, dead tuples, page fill and TOAST chunk headers — '
    'those are DATABASE-grain and are published as a labelled residual by '
    'postgres_per_org_bytes(), never folded into this figure. NOT '
    'invoice-grade: the vendor bill is measured by the platform.';

COMMENT ON COLUMN public.metering_records.postgres_bytes_records IS
    '#12762: the number of journal rows whose bytes are counted in '
    'postgres_bytes_written. Stored beside the byte total so an average bytes '
    'per record is derivable and a counter that stopped advancing is visible.';

-- ============================================================================
-- 2. The METER: the stored bytes of the rows a caller has just written.
-- ============================================================================
-- Read in the CALLER'S transaction, right after the insert, so what it reports
-- is what Postgres stored — not what we handed it. The distinction is
-- measured and load-bearing: `INSERT … RETURNING pg_column_size(payload)`
-- reports the PRE-TOAST, PRE-COMPRESSION value (**24,606** bytes for a payload
-- that occupies **7,177** stored) because it sees the datum before the row is
-- stored. A write-time meter built on RETURNING is therefore a 3.4×
-- OVER-count; this function reads the row back and reports 7,177.
--
-- ⛔ ONLY THE ROWS *THIS TRANSACTION* INSERTED — `xmin = pg_current_xact_id()`.
-- `public.journal_event`'s insert is `ON CONFLICT (org_id, graph_id, seq) DO
-- NOTHING` and the table is append-only, so a write that LOSES a position
-- collision stores nothing — while those positions still exist and still have
-- bytes. Summing by position alone would charge the org a SECOND time for
-- bytes it already paid for, and no row count could reveal it (measured on
-- PGlite 18.3: after a suppressed conflict `count(*) = 1` and the byte total
-- is the full one). `xmin` is the exact discriminator (measured: an own insert
-- has `xmin = pg_current_xact_id()`, a suppressed conflict has the older xid).
--
-- ⇒ CALL IT IN THE WRITE'S OWN TRANSACTION. Outside one it reports 0 rows —
-- that is the contract, not a bug (there is nothing to attribute). Under a
-- SAVEPOINT the row's xmin is the sub-transaction's, so the filter
-- UNDER-counts; that is the safe direction (the bytes reappear in the audit as
-- ``unmetered``), and the record write path takes no savepoints today.
--
-- `p_seqs` is an ARRAY so one call covers a whole write segment: the journal
-- append is contiguous, so the meter costs ONE indexed read per segment, not
-- one per record.
CREATE OR REPLACE FUNCTION public.journal_event_bytes_for_rows(
    p_org_id   text,
    p_graph_id text,
    p_seqs     bigint[]
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
-- ⛔ SECURITY **INVOKER**, deliberately, unlike its audit sibling. This runs on
-- the RECORD's own connection — `tortoise_record_app`, a NOBYPASSRLS role whose
-- tenancy is the `app.current_org_id` GUC the write path sets on that same
-- transaction — so `journal_event`'s tenant policy applies to the read and an
-- org impersonated in `p_org_id` still returns nothing. A SECURITY DEFINER
-- function here would let that role read another org's byte aggregate, and it
-- is not needed: the role already holds SELECT on the table (20261025000001).
SET search_path = ''
AS $$
DECLARE
    v_rows        bigint;
    v_row_bytes   bigint;
    v_payload     bigint;
    v_chunk       bigint;
    v_chunk_ok    boolean;
    v_toast_rel   regclass;
BEGIN
    IF p_org_id IS NULL OR p_org_id = '' THEN
        RAISE EXCEPTION 'journal_event_bytes_for_rows: p_org_id is required';
    END IF;
    IF p_graph_id IS NULL OR p_graph_id = '' THEN
        RAISE EXCEPTION 'journal_event_bytes_for_rows: p_graph_id is required';
    END IF;

    -- `row_bytes` is the ATTRIBUTABLE figure: the whole stored row, which
    -- includes an out-of-line payload's stored bytes (measured, see header).
    SELECT count(*),
           coalesce(sum(pg_catalog.pg_column_size(t.*)), 0),
           coalesce(sum(pg_catalog.pg_column_size(t.payload)), 0)
      INTO v_rows, v_row_bytes, v_payload
      FROM public.journal_event t
     WHERE t.org_id = p_org_id
       AND t.graph_id = p_graph_id
       AND t.seq = ANY (p_seqs)
       -- See the header: only rows THIS transaction inserted. A suppressed
       -- ON CONFLICT leaves the older row in place and it is not our byte.
       -- ⛔ `xmin` is a 32-bit `xid`; `pg_current_xact_id()` is an
       -- EPOC-extended `xid8` (epoch*2^32 + xid). Their ::text renders are
       -- equal only while the cluster's epoch is 0, so the comparison is done
       -- on the 32-bit part explicitly: a cluster that has burnt through 2^32
       -- xids (or was restored from such a base backup) would otherwise have
       -- printed 4294979641 against xmin = 12345 and silently metered 0 rows
       -- on every write, forever.
       AND t.xmin::text::bigint
           = pg_catalog.mod(pg_catalog.pg_current_xact_id()::text::bigint,
                            4294967296);

    -- Chunk detail needs PG17+, and the *function* (not the server version) is
    -- the honest test: a 16.x server has no pg_column_toast_chunk_id (measured)
    -- while a 17.x one does. Absent ⇒ NULL, never 0: "we could not attribute
    -- chunks on this server" must not read as "there are no chunks".
    v_chunk_ok := EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
         WHERE p.proname = 'pg_column_toast_chunk_id');

    IF v_chunk_ok AND v_rows > 0 THEN
        SELECT c.reltoastrelid::regclass
          INTO v_toast_rel
          FROM pg_catalog.pg_class c
         WHERE c.oid = 'public.journal_event'::regclass;
        -- ⛔ THE DISCLOSURE MUST NOT BE ABLE TO ABORT THE WRITE. This block
        -- reads the TOAST relation directly, and the role that calls this in
        -- production (`tortoise_record_app`) holds SELECT on journal_event but
        -- no USAGE on schema `pg_toast` and no SELECT on the chunk table —
        -- measured on PG 17. Unguarded, that raise aborts the CALLER's
        -- transaction, and a COMMIT on an aborted transaction returns SILENTLY
        -- after rolling back (`psycopg2`: no exception, no row). The record
        -- write would be LOST and its wrapper would still report APPLIED. So
        -- the read is guarded twice: a privilege PRE-CHECK that keeps the
        -- normal production path from raising at all, and a subtransaction
        -- (an `EXCEPTION` block — an error here rolls back only this block)
        -- that degrades to NULL/absent, which is what "we could not attribute
        -- chunks here" is supposed to look like. `WHEN OTHERS` is deliberate:
        -- this figure is DISCLOSURE ONLY and is never added to any total, so
        -- the safe degenerate is the labelled absence, never a raise.
        IF pg_catalog.has_schema_privilege(current_user, 'pg_toast', 'USAGE')
           AND pg_catalog.has_table_privilege(current_user, v_toast_rel, 'SELECT')
        THEN
            BEGIN
                EXECUTE format(
                    'SELECT coalesce(sum(octet_length(ch.chunk_data)), 0)
                       FROM public.journal_event t
                       JOIN %s ch ON ch.chunk_id = pg_catalog.pg_column_toast_chunk_id(t.payload)
                      WHERE t.org_id = $1 AND t.graph_id = $2 AND t.seq = ANY ($3)
                        -- mod(), not the modulo OPERATOR: this string is a format()
                        -- template, where a bare percent sign starts a specifier.
                        AND t.xmin::text::bigint
                              = pg_catalog.mod(
                                    pg_catalog.pg_current_xact_id()::text::bigint,
                                    4294967296)',
                    v_toast_rel)
                  INTO v_chunk
                  USING p_org_id, p_graph_id, p_seqs;
            EXCEPTION WHEN OTHERS THEN
                v_chunk := NULL;
                v_chunk_ok := false;
            END;
        ELSE
            -- The caller may not read TOAST at all: report ABSENT, not zero.
            v_chunk := NULL;
            v_chunk_ok := false;
        END IF;
    END IF;

    RETURN jsonb_build_object(
        'org_id',       p_org_id,
        'graph_id',     p_graph_id,
        'rows',         v_rows,
        'row_bytes',    v_row_bytes,
        'payload_bytes', v_payload,
        -- DISCLOSURE ONLY. Never add this to row_bytes: row_bytes already
        -- contains it (the 2× double count measured in the header).
        'chunk_bytes',  v_chunk,
        'chunk_detail', v_chunk_ok,
        'captured_at',  pg_catalog.now());
END;
$$;

REVOKE ALL ON FUNCTION public.journal_event_bytes_for_rows(text, text, bigint[])
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.journal_event_bytes_for_rows(text, text, bigint[])
    TO service_role;
-- ⛔ AND THE ROLE THAT ACTUALLY CALLS IT. The record write path connects as
-- `tortoise_record_app` (NOBYPASSRLS — `.env.example`, and
-- `record_projection._apply_tenant_scope` REFUSES an owner/superuser/BYPASSRLS
-- role), so a grant to `service_role` alone left every live write at
-- `permission denied for function` — swallowed by the fail-soft wrapper, i.e.
-- the meter would have been a silent no-op in production that the dimension
-- registry still advertised as a live writer. GUARDED on the role's existence
-- (the pattern 20261023000001 uses): the record-tenancy role is created by an
-- earlier migration, and where it is absent there is nothing to grant.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tortoise_record_app') THEN
        GRANT EXECUTE ON FUNCTION public.journal_event_bytes_for_rows(
            text, text, bigint[]) TO tortoise_record_app;
    END IF;
END $$;

-- ============================================================================
-- 3. The AUDIT: per-org stored bytes for the whole table + the residual.
-- ============================================================================
-- Independence is the point: this can be run later, by anyone, without the
-- counter — so it catches rows the meter never saw (a replay/backfill, a write
-- through a door that skipped the seam) and a counter that double-counted.
--
-- WHAT IS *NOT* PER-ORG, and is therefore published SEPARATELY:
--   * heap page bytes beyond the tuples (page fill factor, line pointers),
--   * dead tuples not yet vacuumed,
--   * the primary-key index,
--   * TOAST chunk tuple headers and the toast relation's own page slack.
-- These are DATABASE-grain: attributing them to an org would need an
-- allocation, so they ride `residual` and never touch `total_bytes` per org.
CREATE OR REPLACE FUNCTION public.postgres_per_org_bytes()
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_orgs        jsonb;
    v_rows        bigint;
    v_attributed  bigint;
    v_heap        bigint;
    v_index       bigint;
    v_toast_rel   regclass;
    v_toast       bigint;
    v_total       bigint;
    v_chunk       bigint;
    v_chunk_ok    boolean;
    v_residual    bigint;
BEGIN
    v_chunk_ok := EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
         WHERE p.proname = 'pg_column_toast_chunk_id');

    v_toast_rel := (SELECT c.reltoastrelid::regclass
                      FROM pg_catalog.pg_class c
                     WHERE c.oid = 'public.journal_event'::regclass);

    IF v_chunk_ok THEN
        EXECUTE format(
            'WITH per_org AS (
                SELECT t.org_id,
                       count(*) AS rows,
                       coalesce(sum(pg_catalog.pg_column_size(t.*)), 0) AS row_bytes,
                       coalesce(sum(pg_catalog.pg_column_size(t.payload)), 0) AS payload_bytes,
                       coalesce(sum((
                           SELECT coalesce(sum(octet_length(ch.chunk_data)), 0)
                             FROM %s ch
                            WHERE ch.chunk_id
                                  = pg_catalog.pg_column_toast_chunk_id(t.payload)
                       )), 0) AS chunk_bytes
                  FROM public.journal_event t
                 GROUP BY t.org_id)
             SELECT coalesce(jsonb_agg(jsonb_build_object(
                        ''org_id'',        org_id,
                        ''rows'',          rows,
                        ''row_bytes'',     row_bytes,
                        ''payload_bytes'', payload_bytes,
                        ''chunk_bytes'',   chunk_bytes)
                        ORDER BY row_bytes DESC), ''[]''::jsonb),
                    coalesce(sum(rows), 0),
                    coalesce(sum(row_bytes), 0)
               FROM per_org',
            v_toast_rel)
          INTO v_orgs, v_rows, v_attributed;

        EXECUTE format(
            'SELECT coalesce(sum(octet_length(ch.chunk_data)), 0) FROM %s ch',
            v_toast_rel)
          INTO v_chunk;
    ELSE
        WITH per_org AS (
            SELECT t.org_id,
                   count(*) AS rows,
                   coalesce(sum(pg_catalog.pg_column_size(t.*)), 0) AS row_bytes,
                   coalesce(sum(pg_catalog.pg_column_size(t.payload)), 0) AS payload_bytes
              FROM public.journal_event t
             GROUP BY t.org_id)
        SELECT coalesce(jsonb_agg(jsonb_build_object(
                    'org_id',        org_id,
                    'rows',          rows,
                    'row_bytes',     row_bytes,
                    'payload_bytes', payload_bytes,
                    'chunk_bytes',   NULL)
                    ORDER BY row_bytes DESC), '[]'::jsonb),
                coalesce(sum(rows), 0),
                coalesce(sum(row_bytes), 0)
          INTO v_orgs, v_rows, v_attributed
          FROM per_org;
    END IF;

    v_heap  := pg_catalog.pg_relation_size('public.journal_event'::regclass);
    v_index := pg_catalog.pg_indexes_size('public.journal_event'::regclass);
    v_toast := pg_catalog.pg_relation_size(v_toast_rel);
    -- ⛔ NOT heap + index + toast. That sum OMITS the TOAST relation's own
    -- INDEX and both relations' FSM/VM forks — measured on PG 18.3: heap 8,192
    -- + parent index 16,384 + toast 40,960 = 65,536 against a
    -- pg_total_relation_size of 106,496, i.e. the residual would have been
    -- understated by 40% and `attributed + residual = total` would have
    -- reconciled against a partial total. `pg_total_relation_size` is the
    -- relation's whole footprint as Postgres reports it (the per-part figures
    -- above stay, as components of it).
    v_total := pg_catalog.pg_total_relation_size('public.journal_event'::regclass);

    -- Clamp at 0: attributed ABOVE the relation total would mean the two were
    -- read at different instants (or a concurrent write), and a negative
    -- residual is not a measurement — it is a contradiction.
    v_residual := greatest(0, v_total - v_attributed);

    RETURN jsonb_build_object(
        'captured_at',      pg_catalog.now(),
        'orgs',             v_orgs,
        'rows',             v_rows,
        'attributed_bytes', v_attributed,
        'residual', jsonb_build_object(
            'heap_relation_bytes',   v_heap,
            'index_bytes',           v_index,
            'toast_relation_bytes',  v_toast,
            'total_relation_bytes',  v_total,
            'residual_bytes',        v_residual,
            'residual_ratio',        CASE WHEN v_total > 0
                                          THEN round(v_residual::numeric
                                                     / v_total::numeric, 4)
                                          ELSE 0 END,
            -- Disclosure, not attribution: Σchunk_data is INSIDE
            -- attributed_bytes already. The gap to toast_relation_bytes is
            -- chunk tuple headers + toast page slack, i.e. overhead the
            -- residual carries. NULL when the server cannot report chunks.
            'toast_chunk_data_bytes',   v_chunk,
            'toast_chunk_overhead_bytes',
                CASE WHEN v_chunk IS NULL THEN NULL
                     ELSE greatest(0, v_toast - v_chunk) END),
        'chunk_detail', CASE WHEN v_chunk_ok THEN 'measured' ELSE
                        'unavailable (pg_column_toast_chunk_id needs PG17+; '
                        'chunk_bytes is NULL, not 0)' END);
END;
$$;

REVOKE ALL ON FUNCTION public.postgres_per_org_bytes()
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.postgres_per_org_bytes() TO service_role;

-- ============================================================================
-- 4. The ledger increment — the SAME per-(org, window) row every lane uses.
-- ============================================================================
CREATE OR REPLACE FUNCTION public.metering_increment_postgres_bytes(
    p_org_id       text,
    p_period_start timestamptz,
    p_period_end   timestamptz,
    p_bytes        bigint,
    p_records      bigint DEFAULT 0
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- A degenerate window mints a row no reader can ever match (the cohort
    -- aggregate is a strict overlap test). Refuse rather than create orphan
    -- measurement — the discipline this ledger already applies.
    IF p_period_end <= p_period_start THEN
        RAISE EXCEPTION
            'metering_increment_postgres_bytes: period_end (%) must be after '
            'period_start (%)', p_period_end, p_period_start;
    END IF;
    -- Negative bytes cannot be `unwritten`; a counter that could go BACKWARDS
    -- is a bug the ledger must refuse rather than absorb (the
    -- measure_write_ops rule: a negative count is corruption, not a
    -- measurement).
    IF p_bytes < 0 OR p_records < 0 THEN
        RAISE EXCEPTION
            'metering_increment_postgres_bytes: refusing negative bytes (%) '
            'or records (%)', p_bytes, p_records;
    END IF;
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end,
         postgres_bytes_written, postgres_bytes_records)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end, p_bytes, p_records)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        period_end             = EXCLUDED.period_end,
        postgres_bytes_written = coalesce(
            public.metering_records.postgres_bytes_written, 0) + p_bytes,
        postgres_bytes_records = coalesce(
            public.metering_records.postgres_bytes_records, 0) + p_records,
        updated_at             = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_postgres_bytes(
    text, timestamptz, timestamptz, bigint, bigint)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_postgres_bytes(
    text, timestamptz, timestamptz, bigint, bigint)
    TO service_role;
