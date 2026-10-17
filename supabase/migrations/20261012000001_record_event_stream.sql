-- ============================================================================
-- Migration 20261012000001: the Postgres record — layer 1/4, the event stream (#7968)
-- ----------------------------------------------------------------------------
-- THE POSTGRES RECORD — layer 1 of 4: the EVENT STREAM (#7968, epic #7879).
--
-- Ruling: docs/architecture/STORAGE-ARCHITECTURE.md §2.0 (owner, 2026-10-08) —
--   Postgres is the SYSTEM OF RECORD for every layer; FalkorDB is a DERIVED HOT
--   CACHE. §17.1-§17.5 are that ruling's consequences.
-- Placement: §17.5 + the field-level placement table
--   (docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md,
--   W2/#7955) — 216 property names: 187 PRIMARY / 29 DERIVED-VOLATILE / 0 U.
--   A field is PRIMARY iff the journal carries it (§3's per-write test, applied
--   in the §2.1 four-class form: carried -> PRIMARY; not carried but
--   deterministically recomputable -> DERIVED; not carried, not recomputable,
--   transient -> VOLATILE; not carried and a fact the record must keep ->
--   PRIMARY-WITHOUT-CARRIER, i.e. the journal must be extended).
-- Identity: the canonical id contract (W1/#7955, PR #8138) — the key is fixed by
--   label (Point->id, Event->eventId, Subject/Object->name, Source->url), so a
--   FOREIGN KEY IS (label, key), NEVER A BARE id. Encoded here twice: in
--   public.node_identity.key_property (generated from the label) and by the
--   per-table CHECK (node_key = <that label's key column>).
-- Scope: owner scope ruling 2026-10-10 — there are no existing customers (only
--   the dogfood instance). This migration contains NO migration-safety
--   apparatus: no dual-write window, no zero-downtime backfill, no blue/green
--   cutover, no rollback-seamlessness requirement. It is plain additive DDL.
--
-- BUILD ORDER — the event stream first, because it is the journal's own shape
-- and lets the projection (#7969, Interface A journal -> Postgres) be written
-- against it immediately.
--
-- Two senses of "event stream", kept distinct (the W2 table warns about the
-- overload, and consuming it wrongly is how a layer gets built twice):
--   (a) THE JOURNAL — the graph's own append-only change records (GraphEvent),
--       §3's truth set. That is public.journal_event below.
--   (b) THE ONTOLOGY'S EPISODIC LAYER — :Source, :Event, :Session, the domain
--       occurrences the journal materializes. That is the other three tables.
-- Both are the append-only truth and both are built here.
-- ============================================================================

-- ── Identity substrate (Interface E) ────────────────────────────────────────
-- One row per node in EVERY layer. It exists because the contract's foreign key
-- is (label, key) and SQL cannot reference two tables from one column: without a
-- single key table the contract is unenforceable by the database, and S6's
-- "PK/FK correctness" degrades to a naming convention. The cost is one row per
-- node; the benefit is that a relation table's (label, key) endpoints are real
-- foreign keys.
--
-- public.graphs(org_id, id) is made unique below so every row's tenancy can be
-- checked against the graph it belongs to in the SAME constraint.
CREATE UNIQUE INDEX IF NOT EXISTS uq_graphs_org_id_id ON public.graphs (org_id, id);

CREATE TABLE IF NOT EXISTS public.node_identity (
    org_id     text NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    graph_id   text NOT NULL,
    node_label text NOT NULL CHECK (node_label IN ('Point','Event','Subject','Object','Source','Session')),
    node_key   text NOT NULL CHECK (node_key <> ''),
    -- The contract, generated from the label: a store that disagrees with W1
    -- cannot write the row at all.
    key_property text NOT NULL GENERATED ALWAYS AS (
        CASE node_label
            WHEN 'Point'   THEN 'id'
            WHEN 'Event'   THEN 'eventId'
            WHEN 'Subject' THEN 'name'
            WHEN 'Object'  THEN 'name'
            WHEN 'Source'  THEN 'url'
            WHEN 'Session' THEN 'id'
        END) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, graph_id, node_label, node_key),
    FOREIGN KEY (org_id, graph_id) REFERENCES public.graphs (org_id, id) ON DELETE CASCADE
);
COMMENT ON TABLE public.node_identity IS
    'Interface E: the canonical node key per label (Point=id, Event=eventId, Subject/Object=name, Source=url, Session=id). A foreign key in this schema is (node_label, node_key), never a bare id (#7955). The ~9,700 legacy ids are OPAQUE AND VALID and are NOT normalised here (W1b, P2/P1-severe per #7955 §1.4).';

-- ── The journal, in the record ──────────────────────────────────────────────
-- Interface A must be REPLAYABLE, IDEMPOTENT and TOTAL (§2.1). A record that
-- cannot say which journal events it has consumed can prove none of the three,
-- so the journal's own shape is materialized here, append-only by construction
-- (a trigger refuses UPDATE always, and DELETE except on the one tenant-erasure
-- path — an invariant of the layer, not migration-safety apparatus).
CREATE TABLE IF NOT EXISTS public.journal_event (
    org_id      text NOT NULL,
    graph_id    text NOT NULL,
    seq         bigint NOT NULL CHECK (seq > 0),
    event_type  text NOT NULL,
    node_label  text,
    node_key    text,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL,
    PRIMARY KEY (org_id, graph_id, seq),
    FOREIGN KEY (org_id, graph_id) REFERENCES public.graphs (org_id, id) ON DELETE CASCADE
);
COMMENT ON TABLE public.journal_event IS
    'The journal''s own shape — the append-only record of what happened (GraphEvent). APPEND-ONLY is enforced (trigger), not merely documented. seq is the per-graph journal position and the idempotency key: applying a seq twice is a no-op. An event_type with no defined mutation is a FAILURE, never a skip (Interface A totality). node_label/node_key name the subject of the event and carry NO foreign key — the journal precedes the node it creates.';

CREATE OR REPLACE FUNCTION public.journal_event_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- One legitimate exception, and only one: erasing a whole tenant. The
    -- journal is append-only as a RECORD of what happened, not as a legal
    -- barrier — a deletion request must be satisfiable, and the cascade from
    -- public.organizations is the only path that sets this. A direct
    -- UPDATE/DELETE with the flag unset is refused.
    IF TG_OP = 'DELETE' AND current_setting('app.record_journal_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'public.journal_event is append-only: % is refused (a tenant erasure sets app.record_journal_purge=on)', TG_OP;
END $$;

DROP TRIGGER IF EXISTS trg_journal_event_append_only ON public.journal_event;
CREATE TRIGGER trg_journal_event_append_only
    BEFORE UPDATE OR DELETE ON public.journal_event
    FOR EACH ROW EXECUTE FUNCTION public.journal_event_is_append_only();

ALTER TABLE public.node_identity ENABLE ROW LEVEL SECURITY;
CREATE POLICY node_identity_read_org ON public.node_identity
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
CREATE POLICY node_identity_service_role_all ON public.node_identity
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
-- SELECT-only for authenticated: every write goes through the single write path
-- (#7951, Interface C), which connects as service_role. RLS is the tenancy
-- boundary; the grant is the second lock on the same door.
REVOKE ALL ON public.node_identity FROM anon, authenticated, public;
GRANT SELECT ON public.node_identity TO authenticated;

ALTER TABLE public.journal_event ENABLE ROW LEVEL SECURITY;
CREATE POLICY journal_event_read_org ON public.journal_event
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
CREATE POLICY journal_event_service_role_all ON public.journal_event
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
-- SELECT-only for authenticated: every write goes through the single write path
-- (#7951, Interface C), which connects as service_role. RLS is the tenancy
-- boundary; the grant is the second lock on the same door.
REVOKE ALL ON public.journal_event FROM anon, authenticated, public;
GRANT SELECT ON public.journal_event TO authenticated;

CREATE INDEX IF NOT EXISTS idx_journal_event_node ON public.journal_event (org_id, graph_id, node_label, node_key);

-- ── Episodic layer: source ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.source (
    org_id     text NOT NULL,
    graph_id   text NOT NULL,
    node_key   text NOT NULL,
    node_label text NOT NULL DEFAULT 'Source' CHECK (node_label = 'Source'),
    id text,
    url text,
    canonical_url text,
    url_aliases jsonb,
    source_kind text,
    content_hash text,
    title text,
    ingested_at text,
    version integer,
    external_id text,
    format text,
    source_path text,
    raw_state text,
    raw_state_at text,
    _search_text text,
    updated_at text,
    source_date text,
    credibility_tier text,
    is_episodic boolean,
    name text,
    team text,
    provenance_spans jsonb,
    domain text,
    summary text,
    topics jsonb,
    status text,
    embedding jsonb,
    about_entities jsonb,
    document_kind text,
    session_id text,
    captured_at text,
    event_id text,
    needs_extraction boolean,
    story_arc text,
    document_knowledge_domain text,
    authored_by text,
    owned_by text,
    managed_by text,
    governing_agreement text,
    created_at text,
    props      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (org_id, graph_id, node_key),
    FOREIGN KEY (org_id, graph_id, node_label, node_key)
        REFERENCES public.node_identity (org_id, graph_id, node_label, node_key) ON DELETE CASCADE,
    -- `url IS NOT NULL` is NOT redundant: a bare `node_key = url` evaluates to
    -- SQL NULL (not FALSE) when url is NULL, so the constraint would be
    -- SATISFIED and node_key would resolve to nothing in silence (#7968 review
    -- of PR #8179 P1). The key column must be present for the pin to mean.
    CONSTRAINT chk_source_node_key_is_contract_key CHECK (url IS NOT NULL AND node_key = url),
    CONSTRAINT chk_source_props_no_derived CHECK (
        NOT (props ?| ARRAY['__runId', 'previousContentHash', 'reliability', 'reliabilityComponents', 'reliability_derived_at']::text[])
    )
);
ALTER TABLE public.source ENABLE ROW LEVEL SECURITY;
CREATE POLICY source_read_org ON public.source
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
CREATE POLICY source_service_role_all ON public.source
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
-- SELECT-only for authenticated: every write goes through the single write path
-- (#7951, Interface C), which connects as service_role. RLS is the tenancy
-- boundary; the grant is the second lock on the same door.
REVOKE ALL ON public.source FROM anon, authenticated, public;
GRANT SELECT ON public.source TO authenticated;
CREATE INDEX IF NOT EXISTS idx_source_scope ON public.source (org_id, graph_id);

-- ── Episodic layer: event ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.event (
    org_id     text NOT NULL,
    graph_id   text NOT NULL,
    node_key   text NOT NULL,
    node_label text NOT NULL DEFAULT 'Event' CHECK (node_label = 'Event'),
    id text,
    event_id text,
    event_kind text,
    subject text,
    object text,
    started_at text,
    ended_at text,
    parent_event text,
    participants jsonb,
    classification_level text,
    format text,
    embedding jsonb,
    event_status text,
    name text,
    summary text,
    story_arc text,
    keywords jsonb,
    captured_at text,
    session_id text,
    is_episodic boolean,
    is_operator boolean,
    source_file text,
    source_ref text,
    updated_at text,
    mined_at text,
    mined_hash text,
    embedding_repair_failed_at text,
    content text,
    props      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (org_id, graph_id, node_key),
    FOREIGN KEY (org_id, graph_id, node_label, node_key)
        REFERENCES public.node_identity (org_id, graph_id, node_label, node_key) ON DELETE CASCADE,
    -- see the NULL note on chk_source_node_key_is_contract_key (#8179 review P1)
    CONSTRAINT chk_event_node_key_is_contract_key CHECK (event_id IS NOT NULL AND node_key = event_id),
    CONSTRAINT chk_event_props_no_derived CHECK (
        NOT (props ?| ARRAY['confidence']::text[])
    )
);
ALTER TABLE public.event ENABLE ROW LEVEL SECURITY;
CREATE POLICY event_read_org ON public.event
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
CREATE POLICY event_service_role_all ON public.event
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
-- SELECT-only for authenticated: every write goes through the single write path
-- (#7951, Interface C), which connects as service_role. RLS is the tenancy
-- boundary; the grant is the second lock on the same door.
REVOKE ALL ON public.event FROM anon, authenticated, public;
GRANT SELECT ON public.event TO authenticated;
CREATE INDEX IF NOT EXISTS idx_event_scope ON public.event (org_id, graph_id);

-- ── Episodic layer: session ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.session (
    org_id     text NOT NULL,
    graph_id   text NOT NULL,
    node_key   text NOT NULL,
    node_label text NOT NULL DEFAULT 'Session' CHECK (node_label = 'Session'),
    id text,
    created_at text,
    turn_count integer,
    is_episodic boolean,
    harness text,
    capture_lane text,
    client_captured_at text,
    client_captured_at_source text,
    actor_user_id text,
    machine_id text,
    model text,
    capture_redactions jsonb,
    entity_links_attempted integer,
    entity_links_created integer,
    capture_ok boolean,
    capture_extractor text,
    commit_count integer,
    draft_count integer,
    value_nodes_created integer,
    value_nodes_held integer,
    updated_at text,
    props      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (org_id, graph_id, node_key),
    FOREIGN KEY (org_id, graph_id, node_label, node_key)
        REFERENCES public.node_identity (org_id, graph_id, node_label, node_key) ON DELETE CASCADE,
    -- see the NULL note on chk_source_node_key_is_contract_key (#8179 review P1)
    CONSTRAINT chk_session_node_key_is_contract_key CHECK (id IS NOT NULL AND node_key = id),
    CONSTRAINT chk_session_props_no_derived CHECK (
        NOT (props ?| ARRAY[]::text[])
    )
);
ALTER TABLE public.session ENABLE ROW LEVEL SECURITY;
CREATE POLICY session_read_org ON public.session
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
CREATE POLICY session_service_role_all ON public.session
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
-- SELECT-only for authenticated: every write goes through the single write path
-- (#7951, Interface C), which connects as service_role. RLS is the tenancy
-- boundary; the grant is the second lock on the same door.
REVOKE ALL ON public.session FROM anon, authenticated, public;
GRANT SELECT ON public.session TO authenticated;
CREATE INDEX IF NOT EXISTS idx_session_scope ON public.session (org_id, graph_id);
