---
title: "Multi-Graph Migration Runbook"
type: operations
domain: platform
doc_status: live
created: 2026-09-01
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutObjects:
- tortoise-hosted-platform
---

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
| Supabase | `teams.graph_name` (namespace) + an active `kind='default'` `graphs` row carrying the **stable id** — minted for every organization that existed when `20261028000001` (#10025) ran, **and for every organization created afterwards** by `supabase_control.ensure_default_graph_row`, which runs inside `provision_org` / `provision_org_with_token` — the RPC wrappers every provisioning lane routes through (`/v1/register`, `POST /v1/organizations`, agent signup, the onboarding sub-org, the checkout replay) | `graph_metadata` keeps the `'default'` sentinel in `graph_id` and adds `stable_id` beside it; the record tenant is read from that row, never derived from the name |
| Registry | a `kind='default'` Graph node whose namespace IS the team namespace | created at team provisioning (pre-existing, #518 — predates the epic) |

Consequences (E2E-5 exit gate):

- **Two writes, one direction.** The backfill (`20261028000001`) is idempotent and
  safe to re-run — it is the **explicit healing path** if a mint ever failed at
  provisioning. Re-running it mints a row for any organization that lacks an
  active `kind='default'` one — **except** for the two blocked shapes in the
  residual list below, where the mint collides on a unique index instead of
  fighting it (it does not guess): a graph already *named* `default`, and a
  `kind='default'` row whose status is neither `active` nor `deleted`. The
  second is reported by the migration as a WARNING naming the org(s), so a
  re-run that cannot heal an org says so rather than reporting a silent success.

- **A failed mint never fails the organization.** `ensure_default_graph_row`
  itself raises (a mint must not claim success for an unpersisted graph), but
  both provisioning wrappers call it through
  `supabase_control._mint_default_graph_row_best_effort`, which logs at ERROR and
  returns `None`. Raising out of `provision_org` would be worse than the missing
  row: its RPC has already committed org + membership + api_keys, while every
  caller reads a raise as "nothing was provisioned" and compensates
  destructively — `/v1/register` deletes the data-plane graph, agent signup
  loses the only copy of the plaintext key, and the retry is then refused
  (`409 already_registered`). The mint cannot run before the RPC either:
  `graphs.team_id` is `NOT NULL REFERENCES public.teams(id)`, so the org row it
  names does not exist until the RPC commits.

- **A missed mint heals itself on first use.** `resolve_api_key` calls the mint
  when `default_graph_id_for` finds no row for an org-wide key, so the unnamed
  window is one request long rather than permanent (and the migration re-run
  above stays available for an org that never authenticates). Since #10046 the
  same self-heal serves the other two request credentials —
  `hosted_api._session_user_org` and `oauth.resolve_oauth_access_token` both go
  through `supabase_control.default_graph_id_for_credential`, the one home for
  "the org's default row, minted on a miss". Until it is healed the record keeps
  refusing that org `no_tenant` — fail-closed, and now logged, never silent.

- **Two real residuals.** Both leave an organization with no id for its
  default graph, so the record keeps refusing it `no_tenant` (fail-closed, and
  it never files under a guessed tenant). Neither is produced by a supported
  path; both are reported rather than healed.

  1. **A graph already *named* `default`.** A second row would violate
     `uq_graphs_org_name_active`. Remediation: rename the colliding graph, then
     re-run the migration.
  2. **A `kind='default'` row whose status is neither `active` nor `deleted`**
     (e.g. `'archived'` — `graphs.status` is free text with no CHECK, and
     `soft_delete_graph` only ever writes `'deleted'`). The backfill's guards
     test `status='active'` while `uq_graphs_org_default_active` tests
     `status <> 'deleted'`, so the insert collides and is skipped: the org gets
     **no** active row, and the runtime self-heal cannot fix it either
     (`ensure_default_graph_row`'s adopt arm requires `status='active'` and
     re-raises otherwise). The migration emits a **WARNING naming the
     organization(s)** — a tenant that refuses every write must not be silent.
     Remediation: correct that row's status (or tombstone it), then re-run the
     migration (idempotent).

- **"The default row" is singular, and the database now says so.** Every reader
  takes the FIRST row (`default_graph_id_for`, `graph_metadata`,
  `set_graph_name`/`set_graph_recording`), and until #10025 nothing enforced
  that: `uq_graphs_org_name_active` is on `(org_id, name)`, while
  `set_graph_name` inserts the user's CHOSEN name and the backfill/mint insert
  `'default'` — different names, so two *live* `kind='default'` rows for one org
  were legal and reachable from two ordinary supported paths committing
  concurrently. `20261028000001` adds
  `uq_graphs_org_default_active ON public.graphs (org_id) WHERE kind = 'default'
  AND status <> 'deleted'` as the backstop: a second live row now collides
  (both the resolver's self-heal and `set_graph_name`'s insert arm already
  converge onto the winner), and a **tombstone beside an active row stays
  legal** — the defensive shape the code's readers are written around.
  ⚠️ If the migration aborts, its own pre-check named the offending org(s):
  that database already carried two live default rows, which no supported path
  produces. Resolve them first (`UPDATE public.graphs SET status = 'deleted'
  WHERE ...` on all but the row `default_graph_id_for` names), then re-run — the
  file is one transaction, so nothing was half-applied.

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

`20261028000001_graphs_default_row_stable_id.sql` (#10025) — the default
row's id, and the invariant its readers assume:

```sql
-- fail-closed pre-check: name the org(s) that already carry two live default
-- rows, then stop the whole file (Supabase applies a migration in ONE
-- transaction, so a raise mid-file leaves nothing half-applied)
DO $$ … RAISE EXCEPTION '… organization(s) % already carry more than one
        live kind='default' graphs row' … $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_graphs_org_default_active
    ON public.graphs (org_id) WHERE kind = 'default' AND status <> 'deleted';
INSERT INTO public.graphs (id, org_id, name, kind, namespace, status, recording)
SELECT … FROM public.organizations o WHERE … NOT EXISTS (…) …
ON CONFLICT DO NOTHING;   -- a concurrent writer's row IS the row we wanted
```

The index is built BEFORE the backfill, so the backfill's conflict arm is
arbitrated by it too (Graphs held 1 row in production, so the non-concurrent
build is instant; `CREATE INDEX CONCURRENTLY` cannot run inside the single
transaction Supabase applies a migration file in — which is the property the
`ON CONFLICT` safety relies on). Both halves are executed against real
Postgres by `supabase/tests/20261028000001_graphs_default_row_stable_id.sql`.

`supabase/migrations/20260901000001_graphs_and_key_scopes.sql` is
**pure-additive**:

```sql
CREATE TABLE public.graphs (id, org_id, name, kind, namespace, status,
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
  graph 403s. The restore window is `docs/retention-and-deletion.md`.
- **Sweeps (#2313 landed):** the hosted backup sweep now enumerates EVERY
  active graph (default + custom) — graph-keyed archives
  (`backups/{org_id}/{graph}/{ts}_{rnd}/...`), per-graph state/retention, and
  per-graph watcher staleness (see `docs/ops/registry-backup-dr.md`).
  Residual: a graph-bound SELF-SERVICE restore surface for custom graphs is
  a follow-up (#2339); the internal DR operators (drill/re-baseline/ACL
  reconcile) run against the FalkorDB registry control plane — supabase-lane
  operation is a follow-up (#2340).
- **docs:** schema details in `docs/registry-graph-schema.md`; the key
  permission model in `docs/auth-architecture.md` §6.
