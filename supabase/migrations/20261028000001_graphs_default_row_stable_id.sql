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

-- ── 1. The invariant, given a database backstop ───────────────────────────
-- "The org's default row" is SINGULAR everywhere it is consumed —
-- `default_graph_id_for` returns `rows[0]['id']`, `graph_metadata` reads
-- `drow[0]` for `stable_id`/name/recording, `ensure_default_graph_row` returns
-- the first loop row, `set_graph_name`/`set_graph_recording` PATCH `rows[0]` —
-- and until now NOTHING enforced it. The only uniqueness on the table is
-- `uq_graphs_org_name_active ON (org_id, name) WHERE status <> 'deleted'`, so
-- two ACTIVE `kind='default'` rows with DIFFERENT names were legal, and two
-- ordinary supported paths can produce exactly that pair concurrently:
--
--   * `set_graph_name` inserts a `kind='default'` row named the USER'S choice
--     (`supabase_control.set_graph_name`, the no-active-row branch);
--   * the backfill below (and the provisioning/auth mint) inserts one named
--     'default'.
--
-- Neither statement can see the other's uncommitted row, and the two names do
-- not collide, so both commit. `read committed` then answers "the default
-- row" with two unordered rows: one org's record rows split across two
-- `graph_id`s, `stable_id` and the displayed default name flicker per request,
-- and `set_graph_*` writes land on whichever row came back first — the same
-- silent-divergence class this issue exists to close, one level up.
--
-- So state the invariant the code already relies on. `status <> 'deleted'`
-- keeps the DEFENSIVE shape legal — a tombstone beside an active row stays
-- permitted (`soft_delete_graph` refuses `kind='default'`, so no supported path
-- mints the tombstone, but a raw-SQL/ops one must not be turned into a failed
-- deploy) — while two LIVE rows become impossible.
--
-- Order matters: the index is built BEFORE the backfill, so the backfill's
-- conflict arm is arbitrated by it too and cannot itself create the pair when a
-- concurrent writer lands between its snapshot and its index insertion (see
-- §2). Production held **1 `graphs` row across 80 organizations** when this
-- landed, so the non-concurrent build is instant; `CREATE INDEX CONCURRENTLY`
-- would be the wrong tool here anyway — it cannot run inside the single
-- transaction Supabase applies a migration file in, which is the property §2
-- relies on.
--
-- Fail-closed on drift rather than aborting with an opaque message: a database
-- that ALREADY carries the pair cannot build the index, and a bare unique-index
-- error names nothing an operator can act on. The check below names the orgs
-- and stops the whole file, so the backfill cannot half-apply behind a schema
-- the code's singular reads cannot trust.
DO $$
DECLARE
    offenders text;
BEGIN
    SELECT string_agg(d.org_id, ', ') INTO offenders
      FROM (SELECT g.org_id
              FROM public.graphs g
             WHERE g.kind = 'default' AND g.status <> 'deleted'
             GROUP BY g.org_id
            HAVING count(*) > 1) d;
    IF offenders IS NOT NULL THEN
        RAISE EXCEPTION
            'uq_graphs_org_default_active cannot be created: organization(s) % already carry more than one live kind=''default'' graphs row', offenders
            USING HINT = 'Resolve the duplicates first (keep one row ACTIVE, tombstone the rest: UPDATE public.graphs SET status = ''deleted'' WHERE ...), then re-run this migration — nothing in this file was applied.';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_graphs_org_default_active
    ON public.graphs (org_id) WHERE kind = 'default' AND status <> 'deleted';

-- ── 2. One `kind='default'` row per organization that lacks one ────────────
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
           AND g2.status <> 'deleted')
-- The guards above are a READ-THEN-WRITE, and Postgres does not re-evaluate a
-- NOT EXISTS at index-insertion time (INSERT docs: "ON CONFLICT can be used to
-- specify an alternative action to raising a unique constraint ... violation
-- error"). The migration runs against a LIVE deployment, and the writers that
-- insert exactly this row for exactly these orgs are shipped code, not this
-- PR's: `set_graph_recording`'s upsert and the auth/provisioning mint
-- (`ensure_default_graph_row`, also reached from `resolve_api_key`'s self-heal).
-- If either commits between the snapshot above and this statement's index
-- insertion, the insert hits `uq_graphs_org_name_active` (or the new
-- `uq_graphs_org_default_active`) and raises. Supabase applies a migration file
-- in ONE transaction, so that aborts the backfill for EVERY org, not just the
-- colliding one.
-- A conflict here IS the row this migration wanted to exist, so skip it and
-- move on. Untargeted on purpose: `ON CONFLICT DO NOTHING` without a
-- conflict_target handles "conflicts with all usable constraints (and unique
-- indexes)" — both classes — where a conflict_target would pin one index and
-- let the other raise. The Python mint models the same race one layer up
-- (`ensure_default_graph_row` adopts the winner on
-- `uq_graphs_org_name_active`); this is its SQL counterpart.
ON CONFLICT DO NOTHING;

-- ── 3. The storage key, re-asserted ────────────────────────────────────────
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
