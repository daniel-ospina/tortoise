-- ============================================================================
-- Migration 20261023000001: the §9.1 narrative home (R7, #9555, epic #7879)
-- ----------------------------------------------------------------------------
-- PLACEMENT RULING this migration implements:
--   docs/architecture/STORAGE-ARCHITECTURE.md §9.1 (owner, D1) — "The S1
--   narrative (the connected prose form of a captured session) is stored as
--   TEXT in Postgres, together with the raw agent-session transcript it was
--   derived from — not in the graph, not a `Document` node, not a `Point`."
--   §9.4 makes the link load-bearing: "at answer time, send the source's
--   VERBATIM text, not the extracted claim."
--
-- THE GAP THIS CLOSES. Before this migration the narrative text had NO
-- narrative-shaped home in the record: it existed only as DATA — `public.
-- source.story_arc` / `public.event.story_arc` on the row that happened to
-- carry it, plus the `public.point.content` rows of the captured turns. A
-- record-sourced rebuild could therefore reproduce the *rows* but had nothing
-- that says "this is the session's narrative, with its redaction count", and
-- `public.session` carries `turn_count` and no text. `#9567` (the epic's
-- capstone) names this as D7: "the S1 narrative and raw turn text come back".
--
-- WHY ONE TABLE AND NOT A TURN TABLE. The raw turns are ALREADY first-class
-- graph-carried rows in this record (`public.point`, `is_episodic = true`,
-- `node_key = '<session>_t<i>'`), each with its own identity and journal
-- carrier. A second per-turn table would duplicate a carrier that exists —
-- the "double the anchor count for the same input" cost §9.1 rejects one
-- level up — and two copies of the same text can only drift. What has no home
-- is the SESSION-LEVEL narrative and its capture facts, so that is what this
-- table holds; the transcript is served from the turn rows by
-- `tortoise.record_read` (see that module), not copied here.
--
-- WHY THE COLUMNS ARE WHAT THEY ARE (§3's per-write test, in the four-class
-- form the layer-1 migration states at its head). Two of them are PRIMARY
-- because their journal carrier exists TODAY; two are PRIMARY-WITHOUT-CARRIER
-- and the difference is stated rather than glossed:
--   * `capture_redactions` <- `SessionRecorded.capture_redactions`, the
--     per-window redaction count (`#4911`) — journal-carried today. INTEGER on
--     purpose: the count IS an integer. The `jsonb` sibling on `public.session`
--     WAS the schema↔producer disagreement `#9264` (R4) exists to remove, and
--     `20261022000001` has since narrowed it to `integer` — so the carrier's
--     `SessionRecorded` reaches this table instead of being refused by the
--     column type. A count is not an envelope.
--   * `turn_count` <- `SessionRecorded.turn_count`, riding a DIFFERENT and
--     EARLIER `SessionRecorded` than `capture_redactions` above (that one is
--     emitted by `_write_capture_turns`, this one by `_write_session_and_turns`'
--     session record, which never carries `capture_redactions`). That two-record
--     shape is exactly WHY each upsert names only its own columns: the halves
--     arrive separately, so a whole-column-set write would erase whichever
--     landed first, and the replay would then depend on arrival order.
--   * `narrative`  <- the commit's `story_arc`, and `summary` <- the same
--     records' `summary`: **PRIMARY-WITHOUT-CARRIER today.** §9.1 places this
--     text here, so the COLUMN is right — but no producer journals it (see the
--     ⚠️ CARRIER GAP block below: the layer-1 commit writes it to the graph by
--     raw Cypher, which is not a journal record). Under the four-class rule
--     that means “the journal must be extended”, and extending it is R7's
--     remainder (#9555), not a claim that the carriage already exists.
--
-- TENANCY. Same posture as the other record tables (`#7968`): RLS enabled, an
-- `authenticated` read policy on `app.current_org_id`, a `service_role` policy
-- for the write path, and — per R6's RECORDED decision (#9554, decision
-- comment 2026-10-22, implemented by migration 20261022000001) — an app-role
-- policy with BOTH `USING` and `WITH CHECK` so the hosted application path
-- (`tortoise_record_app`, NOBYPASSRLS) is scoped to the GUC tenant rather than
-- bypassing the boundary. That block is GUARDED on the role existing: R6's
-- migration (`20261022000001_record_tenancy_role_and_guc.sql`, recorded by
-- #9554's decision, PR #9600) creates the role and sorts BEFORE this one —
-- but it is **not in this tree yet**, so state the requirement as a DEPENDENCY
-- rather than as fact:
--   * R6 lands first (a fresh database): the role exists, this branch creates
--     the policy, and the ordering holds;
--   * a database upgraded INCREMENTALLY never re-runs an applied migration, so
--     whichever of the two lands second must add what the other could not. R6's
--     own test asserts a policy for every table in `THE_RECORD_TABLES`, which
--     this migration extends — so R6 reds until it covers this table, i.e. the
--     gap is visible on R6's suite rather than silent. If R6 ever lands with a
--     LATER timestamp, R6 must create this table's policy explicitly.
-- The guard can only ever ADD a policy for a role another migration created; it
-- cannot widen one, and where the role is absent nothing is granted.
-- The three-policy assertion in `supabase/tests/20261012000001_record_
-- schema.sql` is written the same way R6 writes it: the app policy is asserted
-- only where the role exists (and in today's harness, where it does not, the
-- assertion is `0 = 0` BY DESIGN — it becomes meaningful the moment R6 lands).
--
-- ERASURE. Two paths, and they are different on purpose.
--   * TENANT erasure: `purge_org_record` (20261012000005) deletes the
--     organization, which cascades organizations -> graphs -> THIS table (the
--     FK below is ON DELETE CASCADE), so a tenant's narrative dies with the
--     tenant. The append-only journal guarantee is untouched: this table is not
--     the journal.
--   * SESSION deletion: the record's own delete path
--     (`hosted_api._delete_session_sync`) journals one `EntityMutated`
--     (`op=delete`) per removed turn Point AND one `SessionDeleted`, whose
--     declared mutation removes the session row and the `session:` Source stub
--     (the turns go with their own records) — so the narrative row must go with
--     them.
--     It does, explicitly: `record_projection._narrative_statements` emits a
--     DELETE for `SessionDeleted` (the table has no `node_identity` parent for
--     a companion-delete template, so it carries its own statement). A narrative
--     row surviving a deleted session would be served by §9.4's verbatim path
--     for a session the graph no longer has, and a same-id re-capture would
--     attach the deleted incarnation's narrative to the new one.
--
-- ⚠️ THE CARRIER GAP, STATED SO THIS TABLE IS NOT OVER-READ. `story_arc` (the
-- `narrative`) and its `summary` are written to the GRAPH by the layer-1 commit's
-- raw Cypher (`hosted_api.py`: `SET s.story_arc=$arc, s.sessionId=$sid …`), and
-- raw Cypher is **not** a journal record (§17.1's unjournalled-writer ledger):
-- `grep -rn story_arc tortoise/` finds commit *payloads* and those raw writes,
-- and no producer journals either. So on a live capture today this row lands
-- with its two COUNTS filled and its two TEXT columns NULL: `turn_count` and
-- `capture_redactions` each ride a `SessionRecorded` the capture emits, and
-- `capture_redactions` reaches this table as an integer because
-- `20261022000001` narrowed `public.session.capture_redactions` off `jsonb`
-- (#9264/R4, settled) — the projection is correct and total, but the narrative
-- carrier does not exist yet. That is exactly why the header above classes the
-- two text columns as PRIMARY-WITHOUT-CARRIER rather than PRIMARY. The producer
-- (journal the derived `:Source`/`:Event` write with `summary`/`story_arc`/
-- `sessionId`) and the wiring of the read arm into R8's restore verb / the ask
-- path are the remainder of #9555 — recorded on the issue, deliberately not
-- faked here by writing a NULL-looking value or by scraping the graph (which
-- would make the record's text depend on the cache it is supposed to survive).
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.session_narrative (
    org_id      text NOT NULL,
    graph_id    text NOT NULL,
    -- The Session's canonical key (Interface E: Session -> `id`).
    session_id  text NOT NULL CHECK (session_id <> ''),
    -- §9.1's narrative: the connected prose form of the captured session.
    narrative   text,
    -- The one-line name the narrative carries on its Source/Event records.
    summary     text,
    -- The per-window redaction count (`#4911`'s `capture_redactions`). Named
    -- after its journal key (§3: the column IS the payload key, snaked) — the
    -- `redactions` in the prose elsewhere is the same value under its
    -- human-facing name.
    capture_redactions integer CHECK (capture_redactions IS NULL
                                      OR capture_redactions >= 0),
    turn_count  integer CHECK (turn_count IS NULL OR turn_count >= 0),
    props       jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (org_id, graph_id, session_id),
    -- THE TENANCY AND ERASURE ANCHOR IS THE GRAPH, not the session row.
    -- ⛔ It must NOT be a foreign key into `public.session`: a narrative's
    -- carrier is a `:Source`/`:Event` record that names its session, and the
    -- record's `source.session_id` column has NO such constraint either — the
    -- live capture writes the transcript Source through raw Cypher, so a
    -- session id can be named before (or without) a `public.session` row. An
    -- FK here would abort the WHOLE record batch (the source row, the journal
    -- position, everything) on the live door and wedge a journal replay of
    -- that record — a schema constraint that fails the write it exists to
    -- enrich. The graph FK keeps what matters: the row cannot exist outside a
    -- tenant graph, and organizations -> graphs -> this table gives the
    -- erasure path above its cascade.
    FOREIGN KEY (org_id, graph_id) REFERENCES public.graphs (org_id, id)
        ON DELETE CASCADE
);

COMMENT ON TABLE public.session_narrative IS
    'The §9.1 narrative home (R7/#9555): the S1 narrative + summary per session, with the capture''s redaction count and turn count. Narrative-shaped, not data-shaped: before this table the narrative existed only as `source.story_arc`/`event.story_arc` on whichever row carried it. The raw turns are NOT duplicated here — they are the episodic `public.point` rows, served by tortoise.record_read.';
COMMENT ON COLUMN public.session_narrative.narrative IS
    'The commit''s `story_arc` — §9.1''s "connected prose form of a captured session". Not in the graph, not a Point: it has no confidence and takes part in no operator (§9.1).';
COMMENT ON COLUMN public.session_narrative.capture_redactions IS
    'The per-window redaction count from `SessionRecorded.capture_redactions` (#4911). INTEGER: the count IS an integer (the jsonb column on public.session was the #9264/R4 disagreement, narrowed to integer by 20261022000001, so the carrier reaches this table rather than being refused).';

ALTER TABLE public.session_narrative ENABLE ROW LEVEL SECURITY;

CREATE POLICY session_narrative_read_org ON public.session_narrative
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));

CREATE POLICY session_narrative_service_role_all ON public.session_narrative
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);

-- SELECT-only for authenticated: every write goes through the single write
-- path (#7951, Interface C). RLS is the tenancy boundary; the grant is the
-- second lock on the same door.
REVOKE ALL ON public.session_narrative FROM anon, authenticated, public;
GRANT SELECT ON public.session_narrative TO authenticated;

CREATE INDEX IF NOT EXISTS idx_session_narrative_scope
    ON public.session_narrative (org_id, graph_id);

-- R6's app role (#9554). GUARDED on the role's existence — R6's migration
-- sorts first, so where R6 is applied this branch runs and the third policy is
-- created; where it is not, the role does not exist and there is nothing for
-- the branch to grant. See the header for why the guard cannot weaken the
-- boundary.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tortoise_record_app') THEN
        CREATE POLICY session_narrative_app_tenant ON public.session_narrative
            FOR ALL TO tortoise_record_app
            USING (org_id = current_setting('app.current_org_id', true))
            WITH CHECK (org_id = current_setting('app.current_org_id', true));
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON public.session_narrative TO tortoise_record_app;
    END IF;
END $$;
