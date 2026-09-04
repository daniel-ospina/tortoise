# Multi-graph migration runbook — no-forced-migration path + rollback (epic #2083)

C8 #2117 (docs + drill). Applies to the epic #2083 changes shipped in
C1–C7 (#2110–#2116): `graphs` table / `:Graph` nodes, per-graph API keys
(`api_keys.graph_id`/`scopes`/`delegation_depth`/`created_by_key_id`),
per-graph recording (C6). Owner decision (Gate #2, 2026-09-01): pro/team
graphs unlimited — **pricing.json unchanged**.

## 1. The no-forced-migration path (existing teams land with ZERO actions)

The Heroku-primary / Neon-default / GCP-default pattern: the **default
graph is graph 0** — the team's pre-epic namespace, derived, never
backfilled:

| Mode | Default graph source | Notes |
|---|---|---|
| Supabase | `teams.graph_name` (derived id literal `'default'`; NO `graphs` row) | `graph_metadata`/`default_graph_id` derive it |
| Registry | a `kind='default'` Graph node whose namespace IS the team namespace | created at team provisioning (pre-existing, #518 — predates the epic) |

Consequences (E2E-5 exit gate):
- **No data move** — tenant points/sessions never touch the registry
  migration; the default graph's namespace is unchanged.
- **No key rotation** — every pre-epic key is `graph_id NULL` +
  `scopes []` + `delegation_depth NULL` = the `legacy_full_access` class
  (resolution derives full team/default-graph access; byte-identical
  behavior).
- **No forced action** — nothing prompts, no re-auth, no re-mint.
- The default graph occupies **quota slot 1** (`max_graphs_per_team`:
  free=1, solo=2, pro/team ∞) and is **not deletable** (server 403 + UI
  lock).
- Per-graph keys do NOT exist for the default graph: its keys ARE the
  team-wide rows (`_ensure_graph_exists` 404s `kind='default'` nodes —
  "there is no per-graph key for the default graph").

## 2. Migration mechanics (Supabase lane)

`supabase/migrations/20260901000001_graphs_and_key_scopes.sql` is
**pure-additive**:

```sql
CREATE TABLE public.graphs (id, team_id, name, kind, namespace, status,
                            recording, created_at)  + RLS + column grants
ALTER TABLE public.api_keys ADD COLUMN graph_id text,          -- NULL = team-wide
                            ADD COLUMN scopes jsonb DEFAULT '[]', -- FLAT allowlist
                            ADD COLUMN created_by_key_id text,
                            ADD COLUMN delegation_depth integer; -- 0 = minted
ALTER TABLE public.api_keys ADD CONSTRAINT chk_minted_key_no_escalation
    CHECK (delegation_depth IS NULL OR (flat-array AND NOT scopes ?| escalation));
CREATE INDEX idx_api_keys_graph_id ON public.api_keys (graph_id);
```

Idempotent (IF NOT EXISTS / DROP-then-ADD on the CHECK, 0007 precedent) —
re-apply safe. Registry lane needs no schema migration: Graph nodes are
created per team by provisioning (C2); the registry key resolution reads
the same fields.

## 3. Rollback path (R18 reversibility)

Drop the C1 additions in reverse:
```sql
ALTER TABLE public.api_keys
    DROP COLUMN IF EXISTS graph_id,
    DROP COLUMN IF EXISTS scopes,
    DROP COLUMN IF EXISTS created_by_key_id,
    DROP COLUMN IF EXISTS delegation_depth;
DROP INDEX IF EXISTS idx_api_keys_graph_id;
DROP TABLE IF EXISTS public.graphs CASCADE;
```
Restored behavior: no `graphs` rows, api_keys without graph columns,
`teams.graph_name` intact → `graph_metadata` derives the default only,
every key is the legacy full-access class. **No application change
required**: the resolution code's legacy branch is the pre-epic behavior.
(Registry lane: dropping the migration's table is a Supabase-mode concern;
registry Graph nodes can be deleted per team without a data-store effect.)

## 4. Rollback drill (CI)

The drill is part of the PGlite harness (`supabase/tests/pglite/validate.mjs`),
wired as the `schema-drill` CI job on `migrations`-surface changes:

```text
apply ALL migrations → run ALL assertion suites
  → drill: drop C1 additions → assert pre-C1 shape (graphs gone,
    api_keys graph columns gone, teams.graph_name intact)
  → re-apply migration 20260901000001 → re-run its assertion suite
✅ ROLLBACK DRILL PASSED (apply → rollback → re-apply round trip)
```

Local: `npm --prefix supabase/tests/pglite run validate`.

## 5. Operational notes

- **No staging hold:** graph creates for pro/team are unlimited — the
  409 quota gate fires only at solo's finite cap (free/anon are 402
  tier-blocked BEFORE the quota gate — `_GRAPH_TIER_BLOCKED`); the per-team
  lock serializes count-then-insert (E2E-11 no oversubscription).
- **Deletes cascade keys:** `DELETE /v1/graphs/{id}` tombstones the graph
  and revokes its keys (idempotent; a client retry converges). The default
  graph 403s.
- **Sweeps (C5 residual, tracked):** backup/event-retention sweeps still
  enumerate the DEFAULT graph only — per-graph sweep enumeration + state
  keying is a follow-up (R13 audit owns retention amplification).
- **docs:** schema details in `docs/registry-graph-schema.md`; the key
  permission model in `docs/auth-architecture.md` §6.
