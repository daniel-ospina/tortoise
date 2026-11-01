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
- **The default is movable, and moving it is one verb.** #10180's
  `POST /v1/graphs/{graph_id}/promote` (migration `20261102000001`) moves the
  `kind='default'` row and `organizations.graph_name` to an existing active graph
  atomically — so "the default graph" in this runbook means *the current* one.
  The demoted graph keeps its own namespace, so the move copies no tenant data;
  what it changes is where an ORG-WIDE credential's next capture lands (its
  record tenant is that row's id). Captures already in flight keep the graph they
  resolved at admission. The ONE org-level record that lives inside a graph —
  `:OnboardingState` — IS carried (see §6), and the response reports it.
  ⛔ **The target's namespace must be one the org-wide lanes can FOLLOW.** The
  verb writes the target row's `namespace` into `organizations.graph_name`, and
  every resolver derives the default from that column through ONE shape test
  (`supabase_control.per_graph_namespace_prefixes`): `org_{org_id}_{gid}` (what
  `insert_graph`/`_provision_graph` mint) or the pre-#3543
  `team_{org_id}_{gid}` that no data migration rewrites — plus the org's own
  bare `org_{org_id}`, which the historical open already resolves (that is what
  promoting the ORIGINAL graph back looks like, for an org whose graph 0 carries
  the `org_` spelling — see the legacy note below). ⛔ The BARE `team_{org_id}` is
  NOT accepted: it is a real stored value, but the historical open resolves
  `org_{org_id}`, not `team_{org_id}`, so promoting a legacy org-level row would
  point every lane at a third graph — neither the promoted one nor the demoted
  one — and a legacy org reaches it by the ordinary un-promote flow. Any other
  shape is refused `409 GRAPH_NAMESPACE_UNFOLLOWABLE`: accepting it would
  succeed and then route every org-wide lane back to the graph the same call
  demoted (records filed under the promoted graph, nodes written to the demoted
  one). Check before you promote —
  `SELECT id, name, namespace FROM public.graphs WHERE org_id = '<org>' AND status = 'active'`.
  A target that is already the current default also refuses
  `409 GRAPH_DEFAULT_DRIFT` when the org carries two ACTIVE default rows: that
  arm returns before the demote, so it cannot be the call that converges the
  drift — and neither can promoting either drifted row (both take that same arm
  and get the same 409). The converging call is a promotion of a graph that is
  **not currently a default** (its demote-then-promote clears the extra rows),
  after which the intended graph promotes normally; or tombstone all but the
  intended default row.
  ⛔ **A legacy org's own original row cannot be promoted back** — a one-way door
  worth knowing BEFORE the first promotion. For an org whose graph 0 still
  carries a pre-#3543 namespace (`team_{org_id}`, or a pre-#1903 `org_{name}`),
  promoting a modern graph forward works, but the un-promote is refused
  `409 GRAPH_NAMESPACE_UNFOLLOWABLE`: the historical open resolves `org_{org_id}`,
  not `team_{org_id}`, so the pointer would name a third graph, and following the
  legacy spelling instead is #2023's pin. The graph stays fully addressable with
  a graph-bound key; restoring it as the ORG default is an ops fix, tracked as
  #10505. (For the same reason: the idempotent REPAIR arm is exempt from the
  namespace guard — it converges whatever namespace the default row already
  carries.)
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

## 6. The default graph is TWO facts — and one verb now moves both (#10180)

**Measured 2026-10-27, line numbers at the head that first shipped this section**
(each row also names its symbol, so a later drift is visible rather than silent;
⚠️ the row `file:line` values are that head's, not live). #10180
asks for the one step a team needs
to start using a new graph: a verb that moves the DEFAULT. Building it surfaced
that "the default graph" is not one fact in this codebase — it is **two**,
resolved in two unrelated ways — and that is why **no verb existed until
`20261102000001`** (see the last subsection for what shipped and why it is not
option 1 below).

| # | reader (file:line) | the question it answers | its source |
|---|---|---|---|
| 1 | `supabase_control.default_graph_id_for` (`:1215`) | which id is the default — the RECORD tenant | the row: `kind='default' AND status='active'` |
| 2 | `supabase_control.default_graph_id_for_credential` (`:1258`) | the same, minting on a miss (#10046) | #1 + `ensure_default_graph_row` |
| 3 | `supabase_control._graph_namespace_for` (`:1190`) | a credential's `graph_namespace` | **`organizations.graph_name`** for an org-wide credential — never the default row |
| 4 | `hosted_api._data_graph_name` (`:611`) / `_data_sdk` (`:7115`), org-wide branch | **which physical graph an org-wide REST credential opens** | **the org-ID derivation** (`_graph_name_for_namespace` → `org_{org_id}`) — neither the row nor the column |
| 5 | `mcp_auth._get_org_sdk` (`:170`), org-wide branch | the same, for MCP | the same derivation (`TortoiseSDK(namespace=org_id)`); its resolver (`sdk.apikey_verify`) carries `graph_namespace = None` for an org-wide key |
| 6 | `supabase_control.graph_metadata` (`:3939`) | the dashboard's default row: name, `stable_id`, namespace | the row for name/id, **the column** for namespace |
| 7 | `backup_sweep.enumerate_org_graphs` (`:558`) → #6 | which namespace the "default" backup slot dumps | follows #6 |
| 8 | the `"default"` sentinel consumers (the default-graph delete guard `:4390`/`:4420`, `sdk.py`, `backup_sweep`, the dashboard; >8 sites) | "is this the default graph" | derived from `kind`, via #6 |
| 9 | `ensure_default_graph_row` (`:4046`) / `_mint_default_graph_row_best_effort` (`:4249`) | who WRITES the default row | creation/provisioning only |
| 10 | `set_graph_name` (`:4484`) / `set_graph_recording` (`:4609`) | rename / recording override on the default | read the `kind='default'` row |
| 11 | `20261028000001` §3 (§2 above) | the default row's namespace must equal `organizations.graph_name` | the column |
| 12 | `hosted_api._sweep_events` (`:1496`) — the boot + hourly event-retention purge | **which graph's events get expired / overflow-capped** | `_make_sdk(namespace=org["org_id"])` — **the derivation**; the row and the column are both ignored (its registry-mode pre-filter, `:1493`, keys on the same `org_{org_id}` literal). Under a promotion the NEW default's events are never purged or capped: `TORTOISE_EVENT_MAX_PER_TEAM` silently unenforced |
| 13 | `hosted_api._dream_worker._open_sdk` (`:3383`), org-keyed branch | **which graph the background dream WRITES** | the queue key, not the control plane: an org-wide credential enqueues with no graph (`:9797-9803`), so `gns is None` → `_make_sdk(namespace=org_id)` — **the derivation**. Splits from the RECORD tenant the same pass binds (row-resolved: `_bind_dream_record_tenant` `:7086`) |
| 14 | `hosted_api._dispatch_webhooks` (`:3410`) → `_dispatch_webhooks_sync` (`:2938`), dream-triggered | **which graph's pending events are pushed** | the same queue key: both `graph_name` and `graph_namespace` are `None`, so `_make_sdk(namespace=org_id)` — **the derivation**. Under a promotion new events go undelivered until the scheduled sweep (#8098), which does resolve the row |
| 15 | `hosted_api.org_info` (`:10519`) — the Overview "Count REAL Points in default graph" | **what the dashboard's memory count reports** | `_make_sdk(namespace=org["org_id"])` — **the derivation**, on a route that already refuses graph-bound credentials (`_reject_graph_bound_org_surface`). Under a promotion the Overview counts the DEMOTED graph |
| 16 | `hosted_api` pack catalog (`:10992`) / manifest pre-check + install (`:11097`) | **which graph holds the team's `PackInstall` / `:PackManifest` state** | the same derivation, behind the same org-wide rejection. Under a promotion installs land on — and are read from — the DEMOTED graph (its own docstring, `:10973`, already names `org_{org_id}` as the install location) |
| 17 | `hosted_api` background indexing + seed lanes — `_run_indexing` (`:36797`), `_relink_sessions_after_index` (`:37147`), `_run_docs_indexing` (`:37370`), `_run_onboarding_seed` (`:32952`), `_run_starter_seed` (`:33129`) | **which graph their writes land in — versus which graph their emitted records are FILED under** | the two facts, one lane: the graph is the derivation, the record tenant is the row (`_bind_default_graph_record_tenant`, `:7014`, → `default_graph_id_for`). Its own docstring (`:7026-7030`) states the equality out loud: *"`namespace=org_id` is the org's DEFAULT graph, BY CONSTRUCTION"* |
| 18 | `quota._count_resource` (`:945-946`) — the live points/sessions gate behind `hosted_api._check_org_limit` (`:9632`, `:9694`, `:9733`, `:15094`, `:18339`, `:33477`) | **which graph the points/sessions quota gate COUNTS** | `_check_org_limit` calls `enforce_org_limit(...)` with no sdk, so `_count_resource` falls back to `_make_sdk(namespace=org_id)` — **the derivation**. Under a promotion the gate counts the DEMOTED graph while the writes it admits land on the promoted one: a stale-low count admits an org past its real cap (the money gate fails OPEN) and a stale-high count refuses writes the org is under the cap for |

Rows 12–18 are **consumers of Fact B, not of the agreement**: each computes
`org_{org_id}` from the org id and never asks which `public.graphs` row is the
default. Measured at this head by AST, the class in `tortoise/hosted_api.py` is
**27 `_make_sdk(namespace=<org id>)` call sites** out of 126 `_make_sdk` calls
(88 registry opens, 2 `namespace=None` opens and 9 `graph_name=<full name>` opens
excluded). **Across the product code (`tortoise/`, `tests/` excluded) the class is
31**: these 27, plus `quota.py:945-946`
(row #18) and three provisioning post-steps in `supabase_control.py` (`:3181`,
`:3277`, `:3284` — pack activation and onboarding-state init, which run inside
provisioning rather than on a steady-state read path). Within the 27, 11 sites
are rows #12–#17, one is row #4's own
org-wide fallback (`_data_sdk`, `:7182`), and the remaining 15 are provisioning /
seed / onboarding / graph-lifecycle helpers (`:5004`, `:11521`, `:11560`,
`:11635`, `:12125` `_seed_demo_graph`, `:21283`, `:21305`, `:26763`, `:27487`,
`:31378`, `:31942`, `:31953`, `:32000`, `:32434`, `:33464`) — 11 + 1 + 15 = 27.
Rows 12–18 are the ones whose post-promotion consequence is not just "the old
graph is still used" but a named operational defect — the record/data SPLIT in
rows #13 and #17, the money gate counting the wrong graph in #18, and a named
reader answering from the demoted graph in the rest.

**Fact A — the control-plane fact** (#1, #6, #9–#11): *which `public.graphs` row
carries `kind='default'`*. It answers the RECORD tenant, the dashboard slot, the
backup slot and the `"default"` sentinel — and it is the fact #10025 gave a
stable id.

**Fact B — the derivation fact** (#4, #5, and the background/auxiliary readers
in #12–#18): *an org-wide credential opens `org_{org_id}`*. Both data-plane lanes
compute it from the org ID and deliberately ignore the control plane. That is a recorded decision, not an
oversight: `_data_sdk`'s docstring says `graph_namespace` (`t.graph_name`) is
**NOT** used for the org-wide open because "the selfhost legacy lane
(`sdk.org_create org_{name}`, #2023) diverges and flipping would silently move
those orgs' data access" (`_data_graph_name` states the same decision in its own
inline note at `hosted_api.py:641`), and
`tests/test_capture_loop_responsiveness.py::test_capture_graph_name_mirrors_the_sdk_mapping`
drives that derivation against the real `_get_proj`.

The two facts agree today **only because provisioning makes them agree by
construction**: all five hosted provisioning sites write
`graph_name = f"org_{org_id}"` (`hosted_api.py:4885`, `:11498`, `:21274`,
`:28349`, `:33378`) — exactly what the derivation produces. ⚠️ That is a
statement about the five **writers**, not about the rows that already exist:
`:33378`'s own inline note marks `org_{org_id}` as **#1903-era parity**, so a row
provisioned before #1903 carries `org_{name}` and would move its data access
under a promotion — the #2023 class, not a hypothetical. One count checks a live
deployment: `SELECT count(*) FROM organizations WHERE graph_name <> 'org_' || id`
(0 = every existing row agrees with the derivation; anything above 0 names orgs a
promotion would move). Nothing tests, constrains or observes that equality
(**#10361** — filed from this measurement).

### Why no verb can be written as "flip a row"

Every coherent promotion shape moves Fact A. A promotion that moves **only** Fact
A leaves an org-wide credential **filing its record under the new graph while
writing its data into the old graph's physical store** — the
two-answers-to-one-question split #10025 exists to remove, one level up. So the
verb cannot be added without deciding how Fact B follows Fact A — and, as
written, the obvious answer (option 1 below, Fact B reads the row
**unconditionally**) reopens #2023's pin, which is an owner decision, not a
lane's. That is why this was blocked, and the subsection at the end of §6 is the
shape that gets past both: Fact B follows the row **only when the data has
moved**, so no existing org is affected and no recorded decision is reversed.

### The options (owner decision on #10180)

1. **The row becomes the single authority** *(recommended)*. **The change set is
the whole class — every `_make_sdk(namespace=<org id>)` default-graph open: 27
call sites in `hosted_api.py`, 31 across `tortoise/`, at this head — not three
functions.** That means the org-wide branches of
`_data_graph_name`/`_data_sdk` and `mcp_auth._get_org_sdk`, **and** the seven
row-groups in #12–#18 (the eleven sites in `hosted_api.py` plus `quota.py`'s
count — retention sweep, dream drain, dream-triggered webhook dispatch, Overview
count, pack catalog/install, the indexing/seed lanes whose record tenant already
comes from the row, and the points/sessions quota check);
`graph_metadata`'s default `namespace` reads the row rather than the column;
promotion = swap `kind` + move
`organizations.graph_name` (which keeps §3's invariant true).

   **Byte-identity is conditional, and it is conditional PER POPULATION: the two
   lanes answer "the row" differently, so only one of them is unchanged by this
   option.**

   **Hosted orgs** (provisioned by the five sites above) — identical **iff**
   `organizations.graph_name = 'org_' || id`, and `public.graphs` is the store that
   answers "the row" for them. The count above is the measurement: 0 rows →
   unchanged; anything above 0 names orgs whose default row does **not** name the
   derivation (the pre-#1903 `org_{name}` shape, `:33378`), and **those orgs move**
   under this option.

   **Registry/selfhost orgs** (`sdk.org_create`) — that lane writes **no
   `public.graphs` default row for Fact B to follow**, so the derivation stays the
   only resolver and these orgs do **not** move. `_graph_create`'s
   Supabase-control-plane arm returns the deterministic id *without persisting* —
   its own docstring names the INSERT as C2/C3, out of C1's scope
   (`sdk.py:35582-35605`) — and its registry arm writes a registry `Graph` node in
   FalkorDB, not a Supabase row. The parenthetical this sentence used to carry ("an
   org-wide registry credential carries `graph_namespace = None`, so the fallback
   IS the derivation") proved only what such an org opens **today**; byte-identity
   needs the stronger claim, and it is the one above: **there is no row to
   disagree with.**

   ⚠️ **The exemption is "orgs with no control-plane row", NOT "orgs created by
   that SDK call".** A `sdk.org_create` org that **also** carries an
   `organizations` row gets a default row minted on its first credential miss, and
   `ensure_default_graph_row` takes that row's namespace from the **column**
   (`supabase_control.py:4151` read, `:4168` insert) — which for that lane is
   `org_{name}` (the user's chosen name, `sdk.py:35415`) while `org_id` is a ULID
   (`:35454`, passed to `_graph_create` at `:35550`). The row then names
   `org_{name}` while the derivation produces `org_{<ULID>}`, so **that org IS
   moved**, and the derivation/column equality must be pinned by a test or a
   constraint rather than counted.

   This is also the E2E-5 "byte-identical" gate, restated honestly: byte-identical
   **for every org whose default row names `org_{org_id}`** — the same condition as
   above, now named as the gate instead of claimed as a blanket.
   ⚠️ **The in-tree proof that a partial change set splits the org**, and the
   reason #12–#18 must be in it: `hosted_api.py:9788-9793` says *"An org-wide API
   KEY is NOT that shape — its drain opens `_make_sdk(namespace=org_id)`, the
   same graph `_data_sdk` opened for this request"* (the same equality is
   restated at `:7023` and `:7102-7103`, and #17's `:7026-7030` states it for five
   more lanes). That sentence is TRUE only because the derivation and the row
   agree today. Convert `_data_sdk` alone and it becomes false:
   `_bind_dream_record_tenant` files the pass's records under the NEW default
   while the drain at #13 writes the OLD one — the record/data split §6 exists to
   prevent, on a lane outside the "three functions" reading.
2. **Promote the record only.** Rejected: splits the record from the data plane
   (above).
3. **Copy on promote.** A data migration that duplicates a team's records across
   two graphs while the record FK is per-graph-id. Not a pointer move; it belongs
   to its own issue — the copy-then-verify-then-delete migration that already
   exists is #10017, and it is the right tool for a team that wants the
   **history** moved as well.
4. **Keep the column as the pointer** (a mirror of 1 sourcing Fact B from
   `organizations.graph_name` instead of the row). Practically identical;
   preferring the row means the record tenant and the data plane read ONE field.

Neither the org-wide verb above nor the per-key alternative substitutes for the
other: a key that is merely **rebound** to a new graph still leaves every
org-wide credential on the old default (**#10362**).

Whatever is chosen, these hold and are not optional: **ids never change**
(#10025 — the id is identity, the name is a label); **each graph keeps its own
`namespace`**, so no points are copied, moved or deleted, and the demoted graph
remains an active custom graph addressed by its own id (#10175's owner ruling
keeps the old graph on purpose); the move is **one transaction** —
`uq_graphs_org_default_active` makes two live defaults impossible and a
demote-then-promote inside one transaction makes zero defaults unobservable;
and it is **journalled** to `audit_events` (append-only) as the operator trail,
while the control plane stays the system of record, so a rebuild reads the new
state and agrees. A capture in flight at the commit resolves both its tenant and
its namespace from the SAME credential dict, so neither can land split; one
resolved before the commit lands under the old graph, one after under the new.

⛔ **`default_graph_id_for`'s docstring used to say "the default graph is the
organization's own graph" — true at creation, false the moment Fact A and Fact B
are allowed to diverge.** It now names the row instead (§6's #1), so the
next lane reads the measured answer rather than the creation-time one.

### What landed: the move is gated on the DATA, so no existing org changes

The verb shipped as `POST /v1/graphs/{graph_id}/promote` (REST-only: no MCP tool,
no public SDK method — #4282), backed by `public.promote_default_graph` in
`supabase/migrations/20261102000001_graphs_promote_default.sql`. It moves Fact A
**and** `organizations.graph_name` in one transaction, so Fact A and Fact B stay
the same graph.

None of options 1–4 above is what landed, and the difference matters: each of
them changes the org-wide resolution **unconditionally**, and that is what the
`hosted_api.py:9788` equality (and #2023's pin) could not survive. The shipped
shape instead makes the flip **conditional on the DATA having moved**:
`supabase_control.moved_default_namespace(org_id, namespace)` returns a namespace
**only** when it is a per-graph namespace of that org — `org_{org_id}_{gid}`, the
shape `insert_graph`/`_provision_graph` mint, which **only a promotion can put in
`organizations.graph_name`**. Everything else keeps the historical open:

| shape of `organizations.graph_name` | what the resolver does |
|---|---|
| `org_{org_id}` (every org that never promoted, incl. all five provisioning sites) | historical open — `namespace=org_id`, the same SDK shape as before |
| `team_{org_id}` (pre-#3543) | historical open — #2023's pin holds |
| `org_{name}` (registry `sdk.org_create`) | historical open, and registry mode never flips at all |
| `org_{org_id}_{gid}` (a promotion) | opens the promoted graph |

So the answer to the count above (`SELECT count(*) FROM organizations WHERE
graph_name <> 'org_' || id`) no longer gates this change: a non-zero count
describes orgs whose default namespace is a legacy name, and those keep the open
they had. #10361 (nothing pins that equality) is still worth landing, but it is
no longer a precondition.

**The class is converted, not just the request path.** The inherited attempt
converted `_data_sdk` alone, which the analysis above already names as worse than
no verb: eleven other default-graph openers would have kept reading and writing
the demoted graph while filing record rows under the promoted one. They now
resolve through ONE function, `hosted_api._org_sdk` (REST: the event-retention
sweep, the dream drain and its webhook dispatch, the Overview memory count, the
pack catalog and manifest install, the onboarding-state lanes, the seed/indexing
runners, and the points/sessions/documents quota count) plus
`mcp_auth._get_org_sdk` (the MCP lane) — one decision,
`moved_default_namespace`, shared by all of them.
`tests/test_10180_promote_default_graph.py` pins the inventory with an AST scan:
a new `_make_sdk(namespace=<org>)` fails the suite unless it is converted or its
reason for staying historical is recorded there.

⚠️ **One consequence worth naming for the switchover:** the MCP lane resolves a
credential through a 60s cache, so a promotion becomes visible to MCP within that
TTL; a capture admitted before it keeps the graph it resolved. Nothing is
re-homed, copied or replayed.

#### The org's onboarding record follows the move (it is the one org-level store kept INSIDE the default graph)

The conversion above is right for graph DATA and wrong for `:OnboardingState`,
the one org-level record stored in a tenant graph: every org-level reader now
opens the promoted default, so a move that left the node behind would serve
`flow_defaults()` over an org that had completed — `onboarding_complete` falls
back to the legacy jsonb answer, the onboarding tool group is re-exposed to
agents, the dashboard wizard re-opens, and the recorded `fork`/`compact` is lost.
So the verb **carries** the record (`hosted_api._carry_onboarding_state` ->
`onboarding.state.copy_org_state`) from the namespace of the row the same
transaction demoted, and reports the outcome in the response:

```json
"onboarding_state": {"migrated": true, "reason": "copied",
                     "from": "org_<org_id>", "to": "org_<org_id>_<gid>",
                     "steps": 4, "status": "complete"}
```

It cannot join the SQL transaction (a graph is a second store), so the response
is where its verdict lives. Read `reason` on every promotion:

| `reason` | meaning | what to do |
|---|---|---|
| `copied` | the record moved | nothing |
| `absent` | the previous default had no node (org never onboarded) | nothing |
| `already_present` / `no_source` / `no_move` | nothing to carry (idempotent promote, or no historical graph listed) | nothing |
| `carry_failed` | **the record is still in the demoted graph** | re-promote the CURRENT default (`POST /v1/graphs/default/promote`): the idempotent arm repairs an absent record from the listed historical name. It never overwrites a present node. |

`_graph_has_org_namespace` follows the default too (a promoted org's existence
verdict is about its default namespace, not `org_{org_id}`), so deleting the
demoted graph — an ordinary custom graph after the move — no longer sends the
onboarding projection down its no-write defaults path.
