-- ============================================================================
-- Migration 20260901000001: graphs table + api_keys graph scope columns (C1)
-- ----------------------------------------------------------------------------
-- Epic #2083 (multi-graph teams), child C1 (#2110) — the data-model substrate:
--   graphs          NEW table — the hosted SOR for team→graph 1:N (registry
--                   twin: the FalkorDB Graph node). status active|deleted
--                   (v1: NO archive — delete = soft tombstone, one-way state
--                   nothing consumes). recording = session_recording override
--                   (NULL = inherit team default; default-ON #1927 preserved).
--   api_keys        +graph_id (NULL = team-wide key → default graph),
--                   +scopes (FLAT jsonb allowlist — shape pinned by the epic
--                   verify gate 2026-09-01: PostgreSQL `?|` on nested objects
--                   matches TOP-LEVEL keys only, which would make the
--                   escalation CHECK vacuous; flat storage makes it fire),
--                   +created_by_key_id (mint lineage), +delegation_depth
--                   (0 = minted cannot-escalate; NULL = owner-minted),
--                   +chk_minted_key_no_escalation (DB-invariant: a MINTED
--                   key can never hold escalation scopes, graph-bound OR
--                   team-wide — only owner-minted keys may).
--
-- Pure additive: existing teams/keys are untouched (graph_id NULL + scopes
-- '[]' + delegation_depth NULL = the legacy full-access class → E2E-5 zero
-- migration). Reversible: DROP CONSTRAINT → DROP columns → DROP TABLE (see
-- plan Task 6 rollback drill). RLS follows the established 0002/0006/0007
-- tenant-GUC pattern; new api_keys columns are management metadata (not
-- secrets — lookup_hash stays the only protected column; no new grants per
-- the 20260825000001 precedent).
--
-- Owner: epistemic-team. Epic: docs/epics/2026-09-01-2083-multi-graph/03-plan.md §4.1.
-- ============================================================================

-- ============================================================================
-- Table: graphs (team→graph 1:N)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.graphs (
    id          text PRIMARY KEY,       -- g_<16hex> — RANDOM, NOT f(team_id,name): name reuse after soft-delete must not collide (partial unique index permits reuse; a deterministic id would PK-violate on re-create). #765 is satisfied by the row existing, not by id determinism.
    team_id     text NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
    name        text NOT NULL,
    kind        text NOT NULL DEFAULT 'custom',   -- 'default' | 'custom'
    namespace   text NOT NULL,                   -- team_{team_id}_{gid}
    status      text NOT NULL DEFAULT 'active',   -- 'active' | 'deleted' (v1: no archive)
    recording   boolean,                          -- session_recording override; NULL = inherit team default
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Name reuse after soft-delete: partial unique index (tombstones don't squat names).
CREATE UNIQUE INDEX IF NOT EXISTS uq_graphs_team_name_active
    ON public.graphs (team_id, name) WHERE status <> 'deleted';

-- Graph-scoped key queries (C2/C3 lifecycle: keys by graph, cascade walk).
CREATE INDEX IF NOT EXISTS idx_graphs_team_id
    ON public.graphs (team_id);

-- ============================================================================
-- RLS: tenant-scoped reads via GUC (precedent: 0006 team_guc_read);
-- service_role manages all (writes go through the platform backend).
-- ============================================================================
ALTER TABLE public.graphs ENABLE ROW LEVEL SECURITY;

CREATE POLICY graph_guc_read ON public.graphs
    FOR SELECT
    TO authenticated
    USING (team_id = current_setting('app.current_team_id', true));

CREATE POLICY graph_service_role_all ON public.graphs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Column-level protection (effective pattern — see 0006 header note): kill
-- table-level grants for public-facing roles, re-grant every column (none
-- are secrets on graphs; RLS GUC scopes rows to the caller's team).
REVOKE ALL ON public.graphs FROM anon, authenticated, public;

GRANT SELECT (id, team_id, name, kind, namespace, status, recording, created_at)
    ON public.graphs TO authenticated;

-- ============================================================================
-- api_keys: graph scope + scopes (FLAT allowlist array) + delegation lineage
-- ============================================================================
ALTER TABLE public.api_keys
    ADD COLUMN IF NOT EXISTS graph_id text
        REFERENCES public.graphs(id) ON DELETE CASCADE;  -- NULL = team-wide key → default graph; CASCADE = keys die with the graph

ALTER TABLE public.api_keys
    ADD COLUMN IF NOT EXISTS scopes jsonb NOT NULL DEFAULT '[]'::jsonb;  -- FLAT allowlist array (see COMMENT below)

ALTER TABLE public.api_keys
    ADD COLUMN IF NOT EXISTS created_by_key_id text
        REFERENCES public.api_keys(id) ON DELETE SET NULL;  -- NULL = minted by owner session/bootstrap; set = minted by this key

ALTER TABLE public.api_keys
    ADD COLUMN IF NOT EXISTS delegation_depth integer;  -- 0 = minted (deleg=0, cannot mint); NULL = owner-minted

COMMENT ON COLUMN public.api_keys.scopes IS
    'FLAT allowlist array ["graphs:read","graphs:write","team:manage"] — default [] = no scopes. Flat by decision (2026-09-01 verify gate): `?|` on nested objects matches top-level keys only, which makes chk_minted_key_no_escalation vacuous; flat storage fires correctly.';

-- DB-invariant for the approved key model: MINTED keys (delegation_depth = 0)
-- can NEVER hold escalation scopes, graph-bound OR team-wide. Only owner-minted
-- keys (delegation_depth IS NULL) may. The jsonb_typeof guard enforces FLAT
-- storage (D1 — a nested object would make `?|` vacuous: it only matches
-- top-level keys), and the escalation set covers ALL admin capabilities in the
-- key-model vocabulary (graphs:create/delete, keys:manage, team:manage).
-- DROP-then-ADD keeps re-apply idempotent (0007 precedent).
ALTER TABLE public.api_keys
    DROP CONSTRAINT IF EXISTS chk_minted_key_no_escalation;
ALTER TABLE public.api_keys
    ADD CONSTRAINT chk_minted_key_no_escalation
    CHECK (delegation_depth IS NULL OR (
        jsonb_typeof(scopes) = 'array'
        AND NOT (scopes ?| array['graphs:create','graphs:delete','keys:manage','team:manage'])));

-- Graph-scoped key listing (dashboard/C3: keys by graph).
CREATE INDEX IF NOT EXISTS idx_api_keys_graph_id
    ON public.api_keys (graph_id);
