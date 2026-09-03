---
title: "2146 Enumerate + Clean e2e-live Orphan Teams/Graphs — Runbook & Verified Inventory"
type: operations
domain: operations
doc_status: live
created: 2026-09-02
ownedBy: epistemic-team
---

# 2146 — e2e-live Orphan Cleanup: Runbook & Verified Inventory

> Procedure + **verified live inventory (2026-09-02)** for #2146: enumerate and
> clean the prod Supabase/FalkorDB state minted by the ~14-day red
> welcome-e2e-monitor window (2026-08-19T22:20 → 2026-09-02). Fix PR #2144
> (merged, worktree HEAD `92562f03`) prevents NEW mints; this runbook removes
> the ~100+ orphans that remain.
>
> **Status: enumeration DONE against prod (read-only). Deletion NOT executed —
> operator-gated.** Scripts are dry-run by default.

---

## 1. What happened (mint path, verified in repo)

The monitor's live-signup smoke posts to `/v1/signup/email` (hosted API →
GoTrue Admin, `email_confirm=true`), auto-signs in, then lands on `/welcome`.
During the red window the `/welcome` route stub was **dead** (post-#1566 the
flow cross-site-redirects to `app.premiselabs.co`), so the **real app root**
loaded (~15 s/run) and ran #1566 welcome-mode provisioning:

`apps/dashboard` welcome mode (`website/apps/dashboard/src/main.jsx`
`provisionInApp`) → Supabase edge fn **`tenant-provision`**
(`supabase/functions/tenant-provision/index.ts`) → **`provision_team` RPC**
(migration 0010) → atomic `teams` + `team_memberships` + `api_keys` rows,
then FastAPI `/internal/demo` seeds the FalkorDB **graph `team_{team_id}`**
where `team_id = sha256(user_id).hexdigest()[:26]` (deterministic per user),
team `name`/`email` = `e2e-live-<8 hex>@premise-labs.dev`, key `tt_<64 hex>`
(one api_keys row per team; `created_via='provisioned'`).

Monitor teardown (`tests/e2e/supabase_admin.py delete_user_by_email`, GoTrue
Admin API) deletes only the auth user. FK `team_memberships.user_id →
auth.users` CASCADE removes the membership rows — but **`teams`, `api_keys`
and the FalkorDB graphs are not cascaded and are never cleaned** (no cleanup
endpoint in-repo).

## 2. Verified live inventory (read-only, prod premise-labs project
`ybetwichurajbfswfeqa`, 2026-09-02)

| Resource | Window scope (default) | All e2e-live (with `--all-e2e-live`) |
|---|---|---|
| `public.teams` (`email LIKE 'e2e-live-%@premise-labs.dev'`) | **154** | **222** |
| `auth.users` remaining (`e2e-live-*`) | 1 | **12** |
| `public.api_keys` (orphan teams) | 154 | 222 |
| `public.team_memberships` (orphan teams) | 2 | 2 |
| `public.invitations` | 0 | 0 |
| `public.abuse_events` (`key_create`, orphan teams) | 154 | 222 |
| `public.audit_events` / metering / oauth (FK-cascade or trail) | 0 | 0 |
| `public.analytics_events` (no FK + immutable trigger — check-only) | 0 | 0 |
| FalkorDB graphs `team_*` | **not enumerable from this env** (see §5) | same |

Window = `created_at >= 2026-08-19T22:20:00Z AND < 2026-09-03T00:00:00Z`
(154 teams). The other 68 teams share the exact same mint shape and predate
the window (`min created_at 2026-08-13 15:17:57`, e.g. the #1494/#1499 auth-gate
arc); all 222 rows match the strict regex
`^e2e-live-[0-9a-f]{8}@premise-labs\.dev$` — **no non-test rows**. `tier`
= `free` everywhere, 0 soft-deleted, no Stripe ids.

Remaining users (all synthetic, deleted by teardown on every other run): 11
created 2026-08-11→08-18 (pre-window) + 1 in-window (2026-08-29,
`e2e-live-62fabf83@premise-labs.dev` — that run's teardown failed). Two of the
12 still hold live owner memberships into orphan teams (`…053011ef →
team cc0d7439…`, `…19e8d60d → team cd3b7048…`).

Sample minted row (for expected-output matching):
`teams.id = 436168ff58d…` (26 hex), `name = e2e-live-d5705b49`,
`graph_name = team_436168ff58d…`, `api_keys.id = key_436168ff58d…_…`,
`api_keys.key_prefix = tt_…` first 10 chars.

## 3. Enumeration runbook (the exact queries)

Run against the linked prod project. Two equivalent drivers (both verified
2026-09-02):

```bash
# Driver A — supabase CLI (Management API SQL; needs CLI linked to the project)
supabase db query --linked -o json "<query>"
# Driver B — Management API directly (SUPABASE_ACCESS_TOKEN — gh secret name;
#             value injectable via GH Actions / operator shell)
curl -sS -X POST "https://api.supabase.com/v1/projects/ybetwichurajbfswfeqa/database/query" \
  -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"<query>"}'
```

**Q1 — orphan teams (window):** expect 154 rows; each: `id` (26-hex), `name`,
`email`, `graph_name`, `created_at`.
```sql
SELECT t.id, t.name, t.email, t.graph_name, t.created_at::text, t.deleted_at::text
FROM public.teams t
WHERE t.email LIKE 'e2e-live-%@premise-labs.dev'
  AND t.email ~ '^e2e-live-[0-9a-f]{8}@premise-labs\.dev$'
  AND t.created_at >= '2026-08-19T22:20:00Z'
  AND t.created_at <  '2026-09-03T00:00:00Z'
ORDER BY t.created_at;
```
(Drop the two `created_at` lines for the full 222 — review the 68 pre-window
rows before including them.)

**Q2 — remaining e2e-live auth users:** expect 12 rows (window: 1).
```sql
SELECT u.id::text, u.email, u.created_at::text
FROM auth.users u
WHERE u.email LIKE 'e2e-live-%@premise-labs.dev'
  AND u.email ~ '^e2e-live-[0-9a-f]{8}@premise-labs\.dev$'
ORDER BY u.created_at;
```

**Q3 — children per orphan team** (replace `<ids>` with the Q1 id list):
expect `api_keys` 154, `team_memberships` 2, `invitations` 0, `abuse_events`
154, `analytics_events` (rows minted by real-app-root loads; counted 0 in the
2026-09-02 inventory but re-check here — check-only: the table is append-only
via migration 0004's immutability trigger, so the script counts but never
deletes), `audit_events` 0.
```sql
SELECT 'api_keys' AS kind, count(*) FROM public.api_keys
  WHERE team_id IN (<ids>)
UNION ALL SELECT 'team_memberships', count(*) FROM public.team_memberships
  WHERE team_id IN (<ids>)
UNION ALL SELECT 'invitations', count(*) FROM public.invitations
  WHERE team_id IN (<ids>)
UNION ALL SELECT 'abuse_events', count(*) FROM public.abuse_events
  WHERE team_id IN (<ids>)
UNION ALL SELECT 'analytics_events', count(*) FROM public.analytics_events
  WHERE team_id IN (<ids>)
UNION ALL SELECT 'audit_events', count(*) FROM public.audit_events
  WHERE team_id IN (<ids>);
```

**Q4 — FK catalog sanity (delete-semantics evidence):**
```sql
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid IN ('public.teams','public.api_keys','public.team_memberships',
                   'public.invitations','public.abuse_events')::regclass
  AND contype = 'f';
```

**Q5 — FalkorDB graph listing (operator with `FALKORDB_CLOUD_URI`):**
```bash
export TORTOISE_DB_URI="$FALKORDB_CLOUD_URI"
uv run python - <<'EOF'
from tortoise.sdk import TortoiseSDK
db = TortoiseSDK(namespace="registry")._get_proj().db
print("\n".join(sorted(db.list_graphs())))
EOF
```
Cross-reference the `team_*` names against Q1's `graph_name` column — every
matching `team_<sha256(user_id)[:26]>` graph whose team is in the orphan set is
a delete candidate. Do **not** wildcard-delete `team_*` — the store also holds
legitimate prod teams (create_team/register_user mints).

## 4. Deletion semantics (evidence)

Live FK catalog (`pg_constraint`, 2026-09-02):

| Constraint | Def | Effect of deleting the parent |
|---|---|---|
| `user_teams_user_id_fkey` (on `team_memberships`) | `user_id → auth.users(id) ON DELETE CASCADE` | Deleting an auth user removes their membership rows (this is the ONLY cascade the monitor teardown relies on) |
| `api_keys` FK | `team_id → teams(id) ON DELETE CASCADE` | Deleting the teams row removes its api_keys rows |
| `invitations` FK | `team_id → teams(id) ON DELETE CASCADE` | same |
| `abuse_events_team_id_fkey` | `team_id → teams(id) ON DELETE CASCADE` | same (the 0015 `key_create` rows minted per provisioned key) |
| `team_memberships.team_id` | **no FK** | memberships are NOT removed by a teams-row delete — delete explicitly |
| `analytics_events` (0004) | **no FK + `analytics_events_immutable` trigger** | append-only (BEFORE UPDATE OR DELETE, statement-level, not role-gated) — survives by design; check-only in the script (a bare DELETE would fire the trigger and strand the purge). Operator decision required if rows are ever deleted |
| `audit_events` | **no FK + immutable trigger** | the append-only trail — survives by design; never deleted |
| `metering_records` (0014) / `oauth` (0016) / `agent_signup_tokens` (20260814000001) / `graphs` (20260901000001) | `team_id → teams(id) ON DELETE CASCADE` | die with the teams row — no explicit delete needed |

There is **no in-repo hosted-api delete-team path usable for orphans**: `DELETE
/v1/teams/{team_id}` (`hosted_api.py` — the delete route) is JWT-owner-gated
(soft delete → 24 h grace → purge) and the orphan users are gone — an
operator cannot act as an owner. The sanctioned **purge machinery** is
`purge_team_control_plane` (`supabase_control.py` — deletes
api_keys → team_memberships → invitations, teams row **last** as retry anchor)
+ the in-repo mint-failure compensation graph calls
(`select_graph(name).delete()`, `hosted_api.py` — register compensation and
agent-signup compensation). The cleanup
scripts below mirror that ordering with direct SQL (api_keys →
team_memberships → invitations → abuse_events →
audit_events INSERT → teams LAST), plus an append-only `audit_events` row per
purged team (`operation='e2e_live_orphan_purged'`, per-run uuid in the id so
re-runs after a partial failure never collide) written **before** the
teams-row delete.

NOTE on the sweep's graph drop: `_drop_team_graph_impl` (`hosted_api.py`)
branches on `hasattr(proj.db, 'delete_graph')` — the pip falkordb cloud client
has NO `delete_graph` (only `select_graph`), so that sweep path log-and-skips
on FalkorDB Cloud (tracked as daniel-ospina/tortoise#2163; the sweep helper
`_drop_team_graph` / `_drop_team_graph_strict` is the #2163-broken path).
THIS cleanup's
graph drop uses `select_graph(...).delete()` (GRAPH.DELETE) which works on
both clients.

FalkorDB graphs: no DB-side cascade exists (separate store). Dropped with
`GRAPH.DELETE team_<id>` via the manifest. `tenant-provision` never wrote
control-plane **registry** nodes (E2E-1, and
the registry is deleted post-#669 flip), so no registry cleanup is needed for
these teams — only the per-team `team_<id>` knowledge graphs.

## 5. ACCESS GAP statement (what an operator must supply)

**Reachable from this machine (verified):**
| Access | State | Notes |
|---|---|---|
| Supabase prod SQL (premise-labs `ybetwichurajbfswfeqa`) | **REACHABLE** | `supabase` CLI v2.110.0 is authenticated; project is `linked: true`; `supabase db query --linked` runs as `postgres` (Management API). Full read+write SQL. |
| gh (daniel-ospina/tortoise) | REACHABLE (names only) | `gh secret list` — **values are never returned by GitHub** |
| Local FalkorDB | local dev only | `premise-labs/.env` + local `docker://` container on `localhost:6379` — **dev/test store, never target** |

**NOT reachable from this machine (the gap):**
- **`FALKORDB_CLOUD_URI` value** (prod FalkorDB Cloud). Exists only as a gh
  secret + Fly app secret (entrypoint.sh:91-93 resolves it to
  `TORTOISE_DB_URI`). No cloud creds in any local `.env`; `redis-cli` not
  installed; `fly` CLI not installed. → **FalkorDB graph enumeration/deletion
  must run where the secret is injectable** (GH Actions `workflow_dispatch`,
  Fly machine, or an operator shell).
- **`SUPABASE_URL` / `SUPABASE_SERVICE_KEY` values** (GoTrue Admin user
  deletion). In gh secrets; not in the local tortoise `.env` (which only sets
  OPENROUTER/STRIPE/SUPABASE_AUTH_EXTERNAL_GOOGLE_*/VENICE keys).
- **`SUPABASE_ACCESS_TOKEN` value** — the supabase CLI already holds it
  (keychain), so SQL works here; a CI/other-shell run injects the secret.

**To execute the deletion an operator needs** (all three are gh secrets on
daniel-ospina/tortoise; names: `SUPABASE_ACCESS_TOKEN`, `SUPABASE_URL`,
`SUPABASE_SERVICE_KEY`, `FALKORDB_CLOUD_URI`):
1. `SUPABASE_ACCESS_TOKEN` — control-plane SQL (Management API; the scripts
   also accept the linked supabase CLI as driver).
2. `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` — GoTrue Admin API user deletion
   (the same mechanism the monitor's teardown used; `--delete-users-via sql`
   is the postgres-superuser alternative).
3. `FALKORDB_CLOUD_URI` — `GRAPH.LIST` enumeration + `GRAPH.DELETE`.

## 6. Cleanup procedure (dry-run first; scripts are dry-run by default)

Scripts (this repo, `origin/main` worktree):
- `graph-scripts/2146_e2e_live_orphan_cleanup.py` — Supabase side: users,
  memberships, api_keys, invitations, abuse_events, audit trail, teams.
- `graph-scripts/2146_falkordb_graph_cleanup.py` — FalkorDB side: drop
  `team_<id>` graphs from the manifest.

```bash
cd .worktrees/ops/2146-orphan-cleanup            # or repo root on origin/main

# 1. Enumerate → writes 2146-e2e-live-orphans.manifest.json (no writes)
uv run python3 graph-scripts/2146_e2e_live_orphan_cleanup.py --phase enumerate
#    expect: teams=154 users=1 api_keys=154 memberships=2 invitations=0
#            abuse_events=154 audit_events=0   (+68/11 with --all-e2e-live)
#    REVIEW the manifest; keep it (it is the only post-delete record).

# 2. Dry-run the full delete (prints counts/statements; writes nothing)
uv run python3 graph-scripts/2146_e2e_live_orphan_cleanup.py --phase all

# 3. Execute (needs SUPABASE_URL + SUPABASE_SERVICE_KEY for the GoTrue user
#    delete, or --delete-users-via sql; SQL driver = CLI or SUPABASE_ACCESS_TOKEN)
uv run python3 graph-scripts/2146_e2e_live_orphan_cleanup.py --phase all --execute

# 4. FalkorDB graphs — run where FALKORDB_CLOUD_URI is injectable
uv run python3 graph-scripts/2146_falkordb_graph_cleanup.py --manifest 2146-e2e-live-orphans.manifest.json            # dry-run
uv run python3 graph-scripts/2146_falkordb_graph_cleanup.py --manifest 2146-e2e-live-orphans.manifest.json --execute   # GRAPH.DELETE
```

Delete order (children first, teams last — retry-anchor semantics of the
product purge): **auth users** (GoTrue) → **team_memberships** →
**api_keys** → **invitations** → **abuse_events** → `audit_events` append →
**teams** (last). FalkorDB graphs may run before or after the teams-row delete
(manifest-driven; a graph left behind is a benign orphan with no DB row).

Guards (in code): email/name must match `^e2e-live-[0-9a-f]{8}@premise-labs\.dev$`;
teams are limited to the window unless `--all-e2e-live`; every delete is
enumerated up-front; graphs must match `team_[0-9a-f]{26}` exactly; nothing
runs without `--execute`.

**Rollback notes:** irreversible once executed — the free-tier test teams had
`backup_enabled=false` (no R2 backups). Mitigations: (a) keep the manifest
JSON (full rows); (b) optional `--dump-csv` for a CSV snapshot before any
delete; (c) the audit_events append-only rows survive the team deletion as the
purge trail; (d) re-running any phase is a no-op (idempotent) — with one
exception: the manifest refuses to be overwritten with an EMPTY team list
(post-success re-enumeration) so the FalkorDB phase always has its target
list; remove the manifest file to force a fresh empty enumeration. If an operator
wants DB-level insurance, `pg_dump` the four tables scoped to the orphan ids
first.

**Verify (post-cleanup, expect 0):** re-run Q1–Q3; then
`supabase db query --linked "SELECT count(*) FROM public.teams WHERE email LIKE 'e2e-live-%@premise-labs.dev';"` → 0;
FalkorDB: `GRAPH.LIST` contains none of the manifest `graph_name`s; the
welcome-e2e-monitor's next scheduled run is green (it already route-blocks, #2144).

## 7. Prevent recurrence (issue ask #4)

The #2144 tripwire stops new mints (deployed). To also stop future
accumulation of test-minted state from ANY e2e path, recommend a follow-up
issue for a **service-role purge endpoint** (e.g. `POST /v1/internal/teams/
purge-e2e`, gated by `FASTAPI_INTERNAL_KEY`) that runs the existing
`purge_team_control_plane` + `_drop_team_graph_strict` machinery for teams
whose `email` matches the test domain — rather than a SQL-only reaper. Also
consider teaching the monitor teardown to call it, so a red-window recurrence
self-heals.
