-- ============================================================================
-- Migration 20261012000003: the Postgres record — layer 3/4, epistemic + THE EP-SCALAR CARVE-OUT (#7968)
-- ----------------------------------------------------------------------------
-- THE POSTGRES RECORD — layer 3 of 4: EPISTEMIC (#7968).
--
-- :Point + the operators (:Point {is_operator:true}) + IMPL/NAND/INPUT and every
-- other edge, as a RELATIONS TABLE (ONTOLOGY §8's reification rule: an edge
-- carries an operator iff it needs mitigation, or is an epistemic support/
-- contradict between epistemic nodes — so the operator is always a :Point and
-- the edge is a row here, never a column on the Point).
--
-- ⛔ THE EP-SCALAR CARVE-OUT — READ THIS BEFORE ADDING A COLUMN (STORAGE-
-- ARCHITECTURE.md §2.0 + §17.5, ruled 2026-10-08):
--   `ep_*`, `confidence`, `posterior_*`, `baseline_*` are RECOMPUTED FROM THE
--   STRUCTURE and are NEVER STORED AS AUTHORITY. There is no column for them
--   here, and the props CHECK refuses them by name — so a write that tries to
--   persist one FAILS rather than silently making a derived value authoritative.
--   * EP confidence is computed in Python (§2.0 clause (a)); it is not in this
--     schema at all.
--   * `content_hash` is a pure function of `content` (#2795 D2) — recomputed.
--   * `version`/`inherited_at`/`lastDreamedAt` are fold counters/stamps —
--     recomputed, and a divergence there means a MISSING JOURNAL EVENT
--     (#5089), not a missing record column.
--
-- ⚠️ NARROWING (the #7955 correction to §17.5 as first written): an AUTHOR-SET
--   baseline is NOT derived. `baseline_source = 'set-by-author'` is the
--   `set_point_baseline` default and a NON-RECONSTRUCTIBLE HUMAN/AGENT INPUT, so
--   §3's per-write test calls it PRIMARY — the carve-out's own test is
--   "recomputed from the structure", and an author-stated prior is not. It is
--   therefore a column, together with the alpha/beta it names. Only the
--   author-set token is storable (`chk_point_baseline_author_set`), so the
--   recomputable tokens (`system-default`, `inherited-from-source`) can never be
--   written into the record; `baseline_set` (a mirror of "a baseline exists")
--   stays DERIVED and has NO column.
--   The correction is recorded on the graph as
--   CORR-17.5-baseline-author-set-is-primary-7955.
--
-- ⚠️ TWO FIELDS, ONE WORD — `confidence`/`weight` are PRIMARY on the EDGE
--   (the caller's recorded attribute, carried verbatim by DirectEdgeCreated) and
--   DERIVED on the OPERATOR (the propagated belief). §2.7 #7/#8 resolves the
--   tension by HOME, not by polarity, and this schema must not collapse them:
--   public.relation has `confidence`/`weight`; public.point has neither.
--
-- ⛔ NO MIGRATION-SAFETY APPARATUS (owner scope ruling 2026-10-10): no
--   dual-write, no backfill, no cutover, no rollback-seamlessness. Plain
--   additive DDL.
-- ============================================================================

-- ── Epistemic layer: point ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.point (
    org_id     text NOT NULL,
    graph_id   text NOT NULL,
    node_key   text NOT NULL,
    node_label text NOT NULL DEFAULT 'Point' CHECK (node_label = 'Point'),
    id text,
    content text,
    is_operator boolean,
    op_type text,
    point_kind text,
    status text,
    authored_by text,
    created_at text,
    valid_from text,
    valid_to text,
    updated_at text,
    embedding jsonb,
    embedding_verbatim text,
    direction text,
    label text,
    extracted_from text,
    provenance_source text,
    is_episodic boolean,
    source_version_transit text,
    mitigation_strength double precision,
    batch_id text,
    promoted_at text,
    reviewed boolean,
    outdated boolean,
    expired_at text,
    search_keys jsonb,
    speaker text,
    quote text,
    "when" text,
    source_turn_id text,
    tags jsonb,
    span_start text,
    span_end text,
    annotator_bias double precision,
    annotator_precision double precision,
    annotator_consistency double precision,
    annotator_directness double precision,
    goal_state text,
    source_harness text,
    source_session text,
    ingested_at text,
    event_id text,
    source_date text,
    assessor text,
    target_source text,
    grounding text,
    lme_question_id text,
    dedup_candidate boolean,
    dedup_method text,
    dedup_similarity double precision,
    dedup_target_id text,
    dedup_reviewed text,
    temporal_candidate boolean,
    temporal_target_ids jsonb,
    temporal_target_id text,
    temporal_replacement text,
    temporal_reviewed text,
    -- The ONE narrowed exception to the EP carve-out (see the header): the
    -- author-set prior and the alpha/beta it names. NULL = no author prior.
    baseline_source text,
    baseline_alpha  double precision,
    baseline_beta   double precision,
    props      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (org_id, graph_id, node_key),
    FOREIGN KEY (org_id, graph_id, node_label, node_key)
        REFERENCES public.node_identity (org_id, graph_id, node_label, node_key) ON DELETE CASCADE,
    -- `id IS NOT NULL` makes the pin total: a bare `node_key = id` is NULL (not
    -- FALSE) when id is NULL and would be silently satisfied (#8179 review P1).
    CONSTRAINT chk_point_node_key_is_contract_key CHECK (id IS NOT NULL AND node_key = id),
    CONSTRAINT chk_point_baseline_author_set CHECK (baseline_source IS NULL OR baseline_source = 'set-by-author'),
    CONSTRAINT chk_point_baseline_complete CHECK (
        (baseline_source IS NULL) = (baseline_alpha IS NULL)
        AND (baseline_source IS NULL) = (baseline_beta IS NULL)),
    CONSTRAINT chk_point_props_no_derived CHECK (
        NOT (props ?| ARRAY['_graph_id', '_nid', 'baseline_set', 'c_cal', 'confidence', 'content_hash', 'ep_alpha', 'ep_beta', 'ep_dirty', 'ep_dirty_at', 'inherited_at', 'lastDreamedAt', 'posterior_alpha', 'posterior_beta', 'reason', 'version']::text[])
    )
);
COMMENT ON TABLE public.point IS
    'Epistemic layer: claims + operator Points (is_operator). CARVE-OUT (§2.0/§17.5): ep_*, confidence, posterior_*, baseline_set are RECOMPUTED from the structure and are refused as columns AND as props keys. baseline_source/baseline_alpha/baseline_beta are the narrowed PRIMARY exception — an author-set prior is a non-reconstructible input (the #7955 correction to §17.5).';
COMMENT ON COLUMN public.point.baseline_source IS
    'PRIMARY when =''set-by-author'' (#7955 correction); the CHECK refuses the recomputable tokens system-default / inherited-from-source.';
ALTER TABLE public.point ENABLE ROW LEVEL SECURITY;
CREATE POLICY point_read_org ON public.point
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
CREATE POLICY point_service_role_all ON public.point
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
-- SELECT-only for authenticated: every write goes through the single write path
-- (#7951, Interface C), which connects as service_role. RLS is the tenancy
-- boundary; the grant is the second lock on the same door.
REVOKE ALL ON public.point FROM anon, authenticated, public;
GRANT SELECT ON public.point TO authenticated;
CREATE INDEX IF NOT EXISTS idx_point_scope ON public.point (org_id, graph_id);

-- ── The relations table (ONTOLOGY §8 reification) ───────────────────────────
-- Every edge is a row: IMPL / NAND / INPUT (operator edges, src = the operator
-- Point), the structural about*/extractedFrom/references family, mitigated_by,
-- CONTAINS, and the RESPONSIBILITY edges (authoredBy / ownedBy / managedBy).
-- A (label, key) endpoint is a REAL composite foreign key into
-- public.node_identity — this is the identity contract made enforceable
-- (S6: PK/FK correctness), never a bare id.
--
-- Predicate vocabulary (ONTOLOGY §3.9 + §3.1-§3.6): IMPL, NAND, INPUT,
-- extractedFrom, references, aboutSubject, aboutObject, aboutEvent, aboutPoint,
-- aboutDocument, aboutSource, mitigated_by, CORRECTS, hasPart, CONTAINS, TAGGED,
-- performs, produces, uses, authoredBy, ownedBy, managedBy, participatesIn,
-- memberOf, hasMember, holdsRole, reportsTo, dependsOn, related, wasDerivedFrom.
-- The list is deliberately NOT a CHECK here: a closed CHECK would refuse a
-- legitimate predicate the moment the vocabulary grows, and the vocabulary's
-- enforcement home is the write path (ONTOLOGY §3.9), not the record.
CREATE TABLE IF NOT EXISTS public.relation (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id     text NOT NULL,
    graph_id   text NOT NULL,
    rel_type   text NOT NULL,
    src_label  text NOT NULL,
    src_key    text NOT NULL,
    tgt_label  text NOT NULL,
    tgt_key    text NOT NULL,
    idx        integer NOT NULL DEFAULT 0,
    direction       text,
    confidence      double precision,
    weight          double precision,
    label           text,
    batch_id        text,
    source_version  text,
    props      jsonb NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (org_id, graph_id) REFERENCES public.graphs (org_id, id) ON DELETE CASCADE,
    FOREIGN KEY (org_id, graph_id, src_label, src_key)
        REFERENCES public.node_identity (org_id, graph_id, node_label, node_key) ON DELETE CASCADE,
    FOREIGN KEY (org_id, graph_id, tgt_label, tgt_key)
        REFERENCES public.node_identity (org_id, graph_id, node_label, node_key) ON DELETE CASCADE,
    CONSTRAINT uq_relation_identity UNIQUE
        (org_id, graph_id, rel_type, src_label, src_key, tgt_label, tgt_key, idx),
    CONSTRAINT chk_relation_idx_non_negative CHECK (idx >= 0),
    CONSTRAINT chk_relation_props_no_derived CHECK (
        NOT (props ?| ARRAY['back_msg_alpha', 'back_msg_beta', 'msg_alpha', 'msg_beta']::text[])
    )
);
COMMENT ON TABLE public.relation IS
    'One row per edge (ONTOLOGY §8 reification). src/tgt are (label, key) composite FKs into node_identity — Never a bare id. `confidence` and `weight` here are the CALLER''S RECORDED EDGE ATTRIBUTES (PRIMARY, DirectEdgeCreated); the EP-propagated confidence is DERIVED and lives on no column (§2.7 #7/#8: resolved by home, never collapsed).';
COMMENT ON COLUMN public.relation.confidence IS
    'The caller''s recorded edge attribute (PRIMARY). NOT the EP-propagated belief — that is derived from structure and is deliberately absent from every column.';
ALTER TABLE public.relation ENABLE ROW LEVEL SECURITY;
CREATE POLICY relation_read_org ON public.relation
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
CREATE POLICY relation_service_role_all ON public.relation
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
-- SELECT-only for authenticated: every write goes through the single write path
-- (#7951, Interface C), which connects as service_role. RLS is the tenancy
-- boundary; the grant is the second lock on the same door.
REVOKE ALL ON public.relation FROM anon, authenticated, public;
GRANT SELECT ON public.relation TO authenticated;
CREATE INDEX IF NOT EXISTS idx_relation_src ON public.relation (org_id, graph_id, src_label, src_key);
CREATE INDEX IF NOT EXISTS idx_relation_tgt ON public.relation (org_id, graph_id, tgt_label, tgt_key);
CREATE INDEX IF NOT EXISTS idx_relation_type ON public.relation (org_id, graph_id, rel_type);
