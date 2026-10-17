-- ============================================================================
-- Migration 20261012000004: the Postgres record — layer 4/4, procedural (views, no table) (#7968)
-- ----------------------------------------------------------------------------
-- THE POSTGRES RECORD — layer 4 of 4: PROCEDURAL (#7968).
--
-- The procedural layer OWNS NO PROPERTY NAME OF ITS OWN (02-identity-and-
-- placement.md §2.6). Its entity is "Event + folded Object.status" (ONTOLOGY §2),
-- and the fields it reads are classified where they physically live:
--   * supersededBy / supersededAt -> PRIMARY, columns already on public.object
--     and public.subject (layers 2 and 3);
--   * status -> a write-through CACHE of the lifecycle events, DERIVED, and
--     therefore NOT a record column at all (ONTOLOGY §2/§4.3: "the event stream
--     is the reconstruction source"; §11 cache doctrine). Capstone S5 asks for
--     exactly this: the derived/volatile field is not in the record.
--
-- So the procedural layer is a VIEW/READ — never a fourth table. A maintainer
-- looking for "the procedural table" must not find one: adding one would make a
-- cache authoritative, which is the defect the two-store split exists to remove.
--
-- ⚠️ NAMED GAP (not silently dropped): the connector work-item states
-- (in_progress / completed) are part of the Object.status cache, and they are
-- NOT derivable from the Object row's PRIMARY columns. The reconstruction source
-- is the journal, which the record now holds (public.journal_event, layer 1), so
-- the fold can be extended — but the per-event payload keys are the projection's
-- contract (#7969) and are deliberately not guessed here: a guessed key returns
-- NULL in silence, which is the failure mode this epic exists to remove. The
-- view below claims exactly the two states the record can reconstruct.
--
-- The EP scalars are not here and are not anywhere in this schema: §2.0 clause
-- (a) — EP confidence is computed in Python.
-- ============================================================================

-- ── The record's event stream, in relational form ───────────────────────────
-- The envelope keys are the journal's own (RecordedEvent: event_id / ts / type /
-- initiated_by / projection_version); the entity snapshot rides `point`. This
-- view types nothing about the payload — it exposes it, so the projection can
-- read the record's own history without guessing a per-event key.
CREATE OR REPLACE VIEW public.record_event_stream
WITH (security_invoker = true) AS
SELECT org_id,
       graph_id,
       seq,
       event_type,
       node_label,
       node_key,
       COALESCE(payload ->> 'ts', recorded_at::text) AS event_instant,
       payload ->> 'event_id'                        AS journal_event_id,
       payload
FROM public.journal_event;
COMMENT ON VIEW public.record_event_stream IS
    'The record''s event stream (the journal in relational form). security_invoker = the caller''s RLS on public.journal_event applies — a view is not a tenancy hole.';

-- ── The procedural fold: Object / Subject current status ────────────────────
-- Folds ONLY the PRIMARY lifecycle columns the record carries. `status` is the
-- cache; it is computed here and stored nowhere.
CREATE OR REPLACE VIEW public.object_current_status
WITH (security_invoker = true) AS
SELECT org_id,
       graph_id,
       node_key AS object_key,
       CASE WHEN superseded_by IS NOT NULL OR superseded_at IS NOT NULL
            THEN 'superseded'
            ELSE 'live' END AS status,
       superseded_by,
       superseded_at
FROM public.object;

CREATE OR REPLACE VIEW public.subject_current_status
WITH (security_invoker = true) AS
SELECT org_id,
       graph_id,
       node_key AS subject_key,
       CASE WHEN superseded_by IS NOT NULL OR superseded_at IS NOT NULL
            THEN 'superseded'
            ELSE 'live' END AS status,
       superseded_by,
       superseded_at
FROM public.subject;
COMMENT ON VIEW public.object_current_status IS
    'Procedural layer: the fold of the Object lifecycle. status is a CACHE (ONTOLOGY §2/§4.3) — derived here, never a record column (§17.5). Named gap: the connector work-item states (in_progress/completed) are not reconstructible from the Object row and are the projection''s (#7969) to fold from public.journal_event.';
COMMENT ON VIEW public.subject_current_status IS
    'Procedural layer: the Subject twin of the Object status fold (#1423). status is a cache; superseded_by/superseded_at are the PRIMARY halves.';

-- ── The procedural layer is READ-ONLY ───────────────────────────────────────
-- A view must never become a write path onto a derived value: `status` is
-- computed, so nothing may set it. Revoke everything, grant SELECT.
REVOKE ALL ON public.record_event_stream, public.object_current_status, public.subject_current_status
    FROM anon, authenticated, public;
GRANT SELECT ON public.record_event_stream, public.object_current_status, public.subject_current_status
    TO authenticated;
