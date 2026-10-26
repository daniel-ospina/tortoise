-- #10025 — the default graph gets a ROW, and therefore a stable id.
--
-- Until this migration the default graph was SYNTHETIC: `graph_metadata`
-- emitted `{"graph_id": "default", ...}` (the literal string, not an id) and
-- `delete_graph_row`'s docstring said so outright — *"no row exists for it —
-- it is derived from organizations.graph_name"* (that sentence is now false and
-- has been corrected by this change; it is quoted here as the state that
-- produced the measurement). A `kind='default'` row
-- existed only as a side effect of `set_graph_recording` or a rename, so live
-- production had **1 row across 80 organizations**.
--
-- That is not cosmetic. The record's layer tables FK
-- `(org_id, graph_id) -> public.graphs (org_id, id)`, so with no row the id an
-- org-wide credential must name does not exist:
--
--   * `supabase_control._graph_namespace_for()` resolved the same org-wide
--     credential to `organizations.graph_name` happily, and
--   * `record_writer.record_tenant_for()` refused it as `no_tenant`,
--
-- two answers to one question. The deployment read `record: {armed: true,
-- ok: true, write_failures: {}}` while refusing every write, because the
-- health counters are a DECLINE channel only (see `hosted_api`'s own note that
-- a zero there does not evidence a write).
--
-- OWNER RULING (2026-10-26): a graph's identity is a fixed id; its name is a
-- user-editable label, for every graph. So the default graph is a graph like
-- any other and needs an id of its own. The NAME is not load-bearing and is
-- not what this migration keys on — `namespace` stays the org's data-plane
-- storage key (`organizations.graph_name`), exactly as `set_graph_name`
-- guarantees ("the namespace is NEVER touched by a rename").
--
-- ── Why the id is RANDOM, not derived from org_id ──────────────────────────
-- `20260901000001`'s own column comment is a recorded decision this migration
-- must not contradict: *"id ... RANDOM, NOT f(team_id,name): name reuse after
-- soft-delete must not collide (partial unique index permits reuse; a
-- deterministic id would PK-violate on re-create)."* So: random 16 hex, the
-- same shape `set_graph_name` mints (`g_{uuid4().hex[:16]}`), and idempotency
-- is expressed as *existence* (below), never as a reproducible value.

-- ── 1. One `kind='default'` row per organization that lacks one ────────────
-- Idempotent by the NOT EXISTS guard: re-running inserts nothing.
-- The existence guard tests `status = 'active'` — the SAME predicate
-- `default_graph_id_for` uses to name a tenant. The two must be identical, not
-- merely both status-aware: `graphs.status` is free text with NO check
-- constraint (`20260901000001`: `status text NOT NULL DEFAULT 'active', --
-- 'active' | 'deleted'` is a COMMENT, not a constraint), so a row carrying any
-- other value would count as "present" to a `<> 'deleted'` guard while the
-- reader rejects it — leaving that organization permanently unnamed
-- (`no_tenant` for ever) while this migration reported success, the fail-closed
-- reading that is indistinguishable from "nothing to do". `soft_delete_graph`
-- refuses `kind='default'`, so such a row is not produced by any supported
-- path; the guard is written so that if one exists anyway the org gets a real
-- active row rather than silence.
-- The second guard keeps the migration from fighting the live-name uniqueness
-- index (`uq_graphs_org_name_active` on (org_id, name) WHERE status <>
-- 'deleted'): an organization that already has a graph *named* 'default' keeps
-- the row it has, and its default graph stays unnamed — fail-closed, and the
-- record refuses for that org rather than this migration picking a name the
-- user already took. That is a REAL residual (see
-- docs/ops/multi-graph-migration-runbook.md) — the operator remediates it by
-- renaming the colliding graph.
INSERT INTO public.graphs (id, org_id, name, kind, namespace, status, recording)
SELECT 'g_' || substr(md5(random()::text || clock_timestamp()::text || o.id), 1, 16),
       o.id,
       'default',
       'default',
       o.graph_name,
       'active',
       NULL
  FROM public.organizations o
 WHERE o.graph_name IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM public.graphs g
         WHERE g.org_id = o.id AND g.kind = 'default'
           AND g.status = 'active')
   AND NOT EXISTS (
        SELECT 1 FROM public.graphs g2
         WHERE g2.org_id = o.id AND g2.name = 'default'
           AND g2.status <> 'deleted');

-- ── 2. The storage key, re-asserted ────────────────────────────────────────
-- A `kind='default'` row's namespace must BE the org's graph name. This is the
-- row's only data-plane field and a drifted value would point the default
-- graph at another org's data. Scoped to rows this migration shape created
-- (kind='default'), and only where the org still has a graph_name.
UPDATE public.graphs g
   SET namespace = o.graph_name
  FROM public.organizations o
 WHERE g.org_id = o.id
   AND g.kind = 'default'
   AND o.graph_name IS NOT NULL
   AND g.namespace IS DISTINCT FROM o.graph_name;
