-- ============================================================================
-- Migration 20261012000002: the Postgres record — layer 2/4, semantic (#7968)
-- ----------------------------------------------------------------------------
-- THE POSTGRES RECORD — layer 2 of 4: SEMANTIC (#7968).
--
-- :Object and :Subject — who/what exists. Every column below is a PRIMARY
-- property of the §17.5 placement table (journal-carried), and every
-- DERIVED/VOLATILE name is absent BY CONSTRUCTION and refused in props by the
-- CHECK. :Source is NOT here: it is an append-only truth row the journal writes
-- and is built in layer 1 (02-identity-and-placement.md §2.3).
--
-- Object.status and Subject.status ARE DELIBERATELY ABSENT — the ontology names
-- them a write-through CACHE of the lifecycle events (ONTOLOGY §2/§4.3, "the
-- event stream is the reconstruction source"), so they are DERIVED and live in
-- the procedural layer (migration 4) as a fold, never as record authority
-- (capstone S5). Their PRIMARY halves — supersededBy / supersededAt — ARE
-- columns, because the journal carries them verbatim (§2.6).
-- ============================================================================

-- ── Semantic layer: object ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.object (
    org_id     text NOT NULL,
    graph_id   text NOT NULL,
    node_key   text NOT NULL,
    node_label text NOT NULL DEFAULT 'Object' CHECK (node_label = 'Object'),
    id text,
    name text,
    object_kind text,
    created_at text,
    title text,
    superseded_by text,
    superseded_at text,
    canonical_name text,
    repo text,
    issue_number integer,
    url text,
    routed_team text,
    routed_role text,
    routed_product text,
    complexity text,
    ux_rating text,
    props      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (org_id, graph_id, node_key),
    FOREIGN KEY (org_id, graph_id, node_label, node_key)
        REFERENCES public.node_identity (org_id, graph_id, node_label, node_key) ON DELETE CASCADE,
    -- `name IS NOT NULL` makes the pin total: a bare `node_key = name` is NULL
    -- (not FALSE) when name is NULL and would be silently satisfied (#8179 P1).
    CONSTRAINT chk_object_node_key_is_contract_key CHECK (name IS NOT NULL AND node_key = name),
    CONSTRAINT chk_object_props_no_derived CHECK (
        NOT (props ?| ARRAY['embedding', 'status']::text[])
    )
);
ALTER TABLE public.object ENABLE ROW LEVEL SECURITY;
CREATE POLICY object_read_org ON public.object
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
CREATE POLICY object_service_role_all ON public.object
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
-- SELECT-only for authenticated: every write goes through the single write path
-- (#7951, Interface C), which connects as service_role. RLS is the tenancy
-- boundary; the grant is the second lock on the same door.
REVOKE ALL ON public.object FROM anon, authenticated, public;
GRANT SELECT ON public.object TO authenticated;
CREATE INDEX IF NOT EXISTS idx_object_scope ON public.object (org_id, graph_id);

-- ── Semantic layer: subject ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.subject (
    org_id     text NOT NULL,
    graph_id   text NOT NULL,
    node_key   text NOT NULL,
    node_label text NOT NULL DEFAULT 'Subject' CHECK (node_label = 'Subject'),
    id text,
    name text,
    subject_kind text,
    created_at text,
    -- #1423 (landed after the 2026-10-10 placement pass) mirrors the Object
    -- supersession lane; §2.6's deciding test classifies these two exactly as it
    -- classifies Object.supersededBy/supersededAt — the journal carries them
    -- verbatim (EntityMutated state map) -> PRIMARY. Subject.status (the fold
    -- cache) is NOT here, for the same reason Object.status is not.
    superseded_by text,
    superseded_at text,
    props      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (org_id, graph_id, node_key),
    FOREIGN KEY (org_id, graph_id, node_label, node_key)
        REFERENCES public.node_identity (org_id, graph_id, node_label, node_key) ON DELETE CASCADE,
    -- see the NULL note on chk_object_node_key_is_contract_key (#8179 review P1)
    CONSTRAINT chk_subject_node_key_is_contract_key CHECK (name IS NOT NULL AND node_key = name),
    CONSTRAINT chk_subject_props_no_derived CHECK (
        NOT (props ?| ARRAY['embedding']::text[])
    )
);
ALTER TABLE public.subject ENABLE ROW LEVEL SECURITY;
CREATE POLICY subject_read_org ON public.subject
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
CREATE POLICY subject_service_role_all ON public.subject
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
-- SELECT-only for authenticated: every write goes through the single write path
-- (#7951, Interface C), which connects as service_role. RLS is the tenancy
-- boundary; the grant is the second lock on the same door.
REVOKE ALL ON public.subject FROM anon, authenticated, public;
GRANT SELECT ON public.subject TO authenticated;
CREATE INDEX IF NOT EXISTS idx_subject_scope ON public.subject (org_id, graph_id);
