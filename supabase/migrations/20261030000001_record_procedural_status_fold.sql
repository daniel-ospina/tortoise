-- ============================================================================
-- Migration 20261030000001: the procedural `status` fold reads the record's OWN
-- event stream, not just the row (#9974)
-- ----------------------------------------------------------------------------
-- WHY. `20261012000004_record_procedural.sql` made the procedural layer a VIEW
-- and computed `Object.status` / `Subject.status` from the two PRIMARY halves the
-- record carries (`superseded_by` / `superseded_at`). That fold has exactly one
-- instrument, so it answers exactly one lifecycle transition: SUPERSESSION. Every
-- other status a writer can set — `EntityMutated op=restatus` (`deprecated`,
-- `archived`, `retracted`, …) and the Subject supersession, which is an
-- `EntityMutated op=supersede` rather than an `ObjectSuperseded` — reads `live`
-- from this view, i.e. a node the live graph records as TERMINAL reads CURRENT
-- on this door. `tortoise/live.py::_terminal_excluded` states the rule that
-- turns that absence into liveness (*"legacy nodes without a stored status are
-- LIVE"*), so the wrong answer is not a missing value: it is a live one.
--
-- WHY HERE, AND NOT A COLUMN. §2.6's class for `status` is DERIVED (a cache of
-- the event stream) and it is not re-opened here: no column is added, and the
-- `chk_object_props_no_derived` / `chk_subject_props_no_derived` refusals stand.
-- The reconstruction source the placement table names is *"the event stream"*,
-- and the record HOLDS that stream (`public.journal_event`, layer 1, written by
-- the live path since #9190). So the fold is extended over it — which is the
-- extension `20261012000004`'s own COMMENT already named as the remaining work,
-- not a new doctrine: *"The reconstruction source is the journal, which the
-- record now holds (public.journal_event, layer 1), so the fold can be
-- extended."*
--
-- WHAT THE FOLD IS, EXACTLY. A journal replay (`rebuild_all`) reaches
-- `Object.status` through three writers, and the record carries all three:
--
--   1. CREATION — `_upsert_object`/`_upsert_subject` write
--      `status=coalesce($st, 'live')` in the `ON CREATE` clause ONLY, so a
--      RE-registration never moves it. The fold therefore treats the FIRST
--      `ObjectRegistered`/`SubjectAdded` for a node as a writer and later ones
--      as non-writers — the `ON MATCH` asymmetry, mirrored rather than guessed.
--   2. SUPERSESSION — `ObjectSuperseded` (folded by `_fold_object_superseded`)
--      and the Subject twin, which journals `EntityMutated op=supersede` with
--      `state.status='superseded'` (`commit_ops`' subject lane).
--   3. `EntityMutated op=restatus` — and, deliberately, NOT gated on `op`:
--      `classify_entity_mutation_op` labels a write by its PRIMARY INTENT, and
--      its own docstring says a consumer wanting EVERY status transition *"must
--      read `state["status"]` and must NOT filter on `op`"* (a mixed
--      `supersededBy` + `status` write is labelled `supersede`). Reading
--      `state->>'status'` regardless of `op` is that instruction, not a
--      shortcut.
--
-- ORDER. Last writer by journal `seq` wins. That is the replay's answer, and it
-- is why: `rebuild_all` applies `EntityMutated` state ops inline AND re-folds
-- every state op whose `seq` is after the node's last supersede (#4743), so the
-- net end state of the two families is exactly "the highest-seq status writer".
-- The `superseded_by` fallback below is retained UNDER the fold for the rows a
-- journal cannot speak for (a non-journaled producer, a pre-#2194 registration,
-- a row this record received by replay of a journal since rotated away): it is
-- the PRIMARY half the row itself carries, and dropping it would make the view
-- answer `live` for the one transition it can still prove.
--
-- ⚠️ NAMED GAP — the CONNECTOR / PACK LIFECYCLE fold is NOT here, and this is a
-- declaration, not an oversight. `_fold_object_status` (plus `status_folds.py`'s
-- pack-declared registry) sets `Object.status` from `EventRecorded` payloads by
-- matching the Object's `objectKind` against a per-pack `statuses`/`folds`
-- declaration. That vocabulary is DECLARED BY PACKS AT RUNTIME — it is not a
-- constant this view can name — so folding it in SQL would be a second
-- declaration of a registry that cannot be imported here, and a guessed key
-- returns NULL in silence. Those Objects (connector work items: `in_progress`,
-- `completed`, and every pack-declared lifecycle) still read `live` from this
-- view, exactly as they did before this migration; tracked as #10198, which owns
-- teaching the record the pack vocabulary. The three arms above are the ones the
-- record can reconstruct WITHOUT guessing, and they are the ones this file
-- claims. One visible cost of the gap, named rather than discovered later:
-- `tortoise/reconciliation.py` compares the record to the cache, so a
-- connector-tracked Object reports a `field_mismatches` entry on `status` —
-- which is the TRUTH (the record genuinely cannot account for that value), not
-- a second writer.
--
-- A FORWARD migration, never an edit of 20261012000004: migrations are
-- append-only history and `check-migration-append-only` refuses an edit to a
-- landed file. The round-trip this file's own suite drills (create → restatus →
-- supersede) is `supabase/tests/20261030000001_record_status_fold.sql`.
-- ============================================================================

-- ── Object: the fold over the record's event stream ─────────────────────────
CREATE OR REPLACE VIEW public.object_current_status
WITH (security_invoker = true) AS
WITH status_writes AS (
    SELECT j.org_id,
           j.graph_id,
           j.payload ->> 'id' AS node_id,
           j.seq,
           j.event_type,
           CASE j.event_type
               WHEN 'ObjectSuperseded' THEN 'superseded'
               WHEN 'ObjectRegistered' THEN j.payload ->> 'status'
               WHEN 'EntityMutated'    THEN j.payload -> 'state' ->> 'status'
           END AS status
      FROM public.journal_event j
     WHERE j.event_type IN ('ObjectRegistered', 'ObjectSuperseded', 'EntityMutated')
       -- The node is addressed by its STABLE id (20261027000001 made `id` the
       -- key). A legacy id-less line names no row here — and the record refuses
       -- it at the projection door (#9881), so the two agree about what the
       -- record can speak for.
       AND j.payload ->> 'id' IS NOT NULL
       -- `EntityMutated` is ONE record type for EVERY label, so its own `label`
       -- is the discriminator. `ObjectRegistered`/`ObjectSuperseded` are
       -- Object-only by name (`record_projection.RECORD_MUTATIONS`).
       AND (j.event_type <> 'EntityMutated' OR j.payload ->> 'label' = 'Object')
),
first_registration AS (
    SELECT org_id, graph_id, node_id, min(seq) AS seq
      FROM status_writes
     WHERE event_type = 'ObjectRegistered'
     GROUP BY org_id, graph_id, node_id
),
effective_writes AS (
    SELECT w.org_id, w.graph_id, w.node_id, w.seq, w.status
      FROM status_writes w
      LEFT JOIN first_registration f
             ON f.org_id = w.org_id
            AND f.graph_id = w.graph_id
            AND f.node_id = w.node_id
     -- A NULL status is not a write: `EntityMutated state` carries the applied
     -- map, and a write that did not touch `status` leaves the column absent.
     WHERE w.status IS NOT NULL
       -- `ON CREATE` only — see the header's writer 1.
       AND (w.event_type <> 'ObjectRegistered' OR w.seq = f.seq)
),
last_write AS (
    SELECT DISTINCT ON (org_id, graph_id, node_id)
           org_id, graph_id, node_id, status
      FROM effective_writes
     ORDER BY org_id, graph_id, node_id, seq DESC
)
SELECT o.org_id,
       o.graph_id,
       o.node_key AS object_key,
       COALESCE(l.status,
                CASE WHEN o.superseded_by IS NOT NULL
                       OR o.superseded_at IS NOT NULL
                     THEN 'superseded' END,
                'live') AS status,
       o.superseded_by,
       o.superseded_at
  FROM public.object o
  LEFT JOIN last_write l
         ON l.org_id = o.org_id
        AND l.graph_id = o.graph_id
        AND l.node_id = o.id;

COMMENT ON VIEW public.object_current_status IS
    'Procedural layer: the fold of the Object lifecycle over the RECORD''S OWN event stream (public.journal_event) — creation, supersession and EntityMutated state.status, last writer by seq (#9974). status is a CACHE (ONTOLOGY §2/§4.3): derived here, never a record column (§17.5). Named gap: the pack/connector lifecycle fold (status_folds.py: in_progress/completed and every pack-declared status) is declared by packs at RUNTIME and is not reconstructible by a constant fold here — tracked as #10198.';

-- ── Subject: the same fold, over its own vocabulary ────────────────────────
-- Structurally identical to the Object view, and deliberately NOT collapsed
-- into one parameterised view: the two labels' writers are different RECORD
-- TYPES (`SubjectAdded` vs `ObjectRegistered`; a Subject's supersession is an
-- `EntityMutated op=supersede` and has no `ObjectSuperseded` twin), so a shared
-- body would need a `CASE` per label anyway and would read as if the two
-- vocabularies were one.
CREATE OR REPLACE VIEW public.subject_current_status
WITH (security_invoker = true) AS
WITH status_writes AS (
    SELECT j.org_id,
           j.graph_id,
           j.payload ->> 'id' AS node_id,
           j.seq,
           j.event_type,
           CASE j.event_type
               WHEN 'SubjectAdded'  THEN j.payload ->> 'status'
               WHEN 'EntityMutated' THEN j.payload -> 'state' ->> 'status'
           END AS status
      FROM public.journal_event j
     WHERE j.event_type IN ('SubjectAdded', 'EntityMutated')
       AND j.payload ->> 'id' IS NOT NULL
       AND (j.event_type <> 'EntityMutated' OR j.payload ->> 'label' = 'Subject')
),
first_registration AS (
    SELECT org_id, graph_id, node_id, min(seq) AS seq
      FROM status_writes
     WHERE event_type = 'SubjectAdded'
     GROUP BY org_id, graph_id, node_id
),
effective_writes AS (
    SELECT w.org_id, w.graph_id, w.node_id, w.seq, w.status
      FROM status_writes w
      LEFT JOIN first_registration f
             ON f.org_id = w.org_id
            AND f.graph_id = w.graph_id
            AND f.node_id = w.node_id
     WHERE w.status IS NOT NULL
       AND (w.event_type <> 'SubjectAdded' OR w.seq = f.seq)
),
last_write AS (
    SELECT DISTINCT ON (org_id, graph_id, node_id)
           org_id, graph_id, node_id, status
      FROM effective_writes
     ORDER BY org_id, graph_id, node_id, seq DESC
)
SELECT s.org_id,
       s.graph_id,
       s.node_key AS subject_key,
       COALESCE(l.status,
                CASE WHEN s.superseded_by IS NOT NULL
                       OR s.superseded_at IS NOT NULL
                     THEN 'superseded' END,
                'live') AS status,
       s.superseded_by,
       s.superseded_at
  FROM public.subject s
  LEFT JOIN last_write l
         ON l.org_id = s.org_id
        AND l.graph_id = s.graph_id
        AND l.node_id = s.id;

COMMENT ON VIEW public.subject_current_status IS
    'Procedural layer: the Subject twin of the Object status fold (#1423/#9974) — creation, EntityMutated state.status (its supersession journals `supersede` with state.status=''superseded''), last writer by seq. status is a cache; superseded_by/superseded_at are the PRIMARY halves.';

-- ⚠️ NO INDEX IS ADDED FOR THIS FOLD, deliberately. `journal_event`'s only
-- index is (org_id, graph_id, node_label, node_key) and `_journal_row` writes
-- neither of those columns (they are NULL for every live row), so the fold's
-- scan is a sequential one over the tenant's stream. That is proportionate to
-- the path that reads it: both rebuild routes are whole-journal reads by
-- construction (`rebuild_all` replays every record; `rebuild_from_record`
-- reads every layer row), so the fold adds one more pass over the same stream
-- rather than an index point-read. A partial expression index on
-- `payload->>'id'` would be paid for by EVERY journal append instead, and no
-- measurement exists for either side — so the trade is left to the measurement
-- that would justify it rather than guessed here.

-- The procedural layer stays READ-ONLY: `status` is computed, so nothing may
-- write it. CREATE OR REPLACE VIEW keeps the existing grants, but re-state them
-- so a reader does not have to trust that: REVOKE first, then grant SELECT.
REVOKE ALL ON public.object_current_status, public.subject_current_status
    FROM anon, authenticated, public;
GRANT SELECT ON public.object_current_status, public.subject_current_status
    TO authenticated;
