---
title: "Record tenancy — the connecting role and the tenant GUC (R6, epic #7879)"
type: plan
domain: engineering
doc_status: decided
created: 2026-10-22
updated: 2026-10-23
subjects.team: epistemic-team
aboutSubjects: Tortoise Postgres record
aboutObjects: "epic #7879, issue #9554, PR #9600, STORAGE-ARCHITECTURE.md, hosted deployments"
---

# R6 — the record's tenancy posture: the connecting role, and the boundary

**Decision record for [#9554](https://github.com/daniel-ospina/tortoise/issues/9554)** · epic
[#7879](https://github.com/daniel-ospina/tortoise/issues/7879) · plan doc
[`08-hosted-prerequisites.md`](08-hosted-prerequisites.md) §B R6 · recorded **2026-10-22**.

This is the artifact R6's `### Research Needed` asks for: *"which role the record connects as"* and
*"whether RLS is the boundary or defence-in-depth"*. §4 (owner, 2026-10-08) delegates the answer —
*"⚠️ Bypass rule, to state explicitly: RLS is bypassed by the table owner and by `service_role`. Say
which role the app connects as, and why."* — and this document states it, with the evidence, and
names the point at which the *next* lane has to honour it.

**OVERRIDES:** the `service_role` + defence-in-depth recommendation in `#669`'s research brief §4
(*"recommends `service_role` + defence-in-depth at cutover"*), and the `postgres` DSN in
`08-hosted-prerequisites.md` §B R1 — RLS is the boundary here, so the application path must not
connect as a role that bypasses it.

---

## 1. The decision

| Question | Answer | Where it is enforced |
|---|---|---|
| **Which role does the record connect as?** | `tortoise_record_app` — `NOBYPASSRLS`, `NOSUPERUSER`, not the table owner. Created by migration, granted `SELECT`+`INSERT` on every record table (`THE_RECORD_TABLES`, ten of them since #9555 added `public.session_narrative`), plus `UPDATE`+`DELETE` on the **nine that are not `journal_event`** — the journal is append-only, and its trigger's one escape is a GUC (`app.record_journal_purge`) that **any** role may set, so a `DELETE` grant would let the boundary role erase its own audit trail (the posture suite pins the narrow grant). **Not** `postgres` (R1's DSN shape today) and **not** `service_role`. | `supabase/migrations/20261025000001_record_tenancy_role_and_guc.sql` §1–3 |
| **Is RLS the boundary, or defence-in-depth?** | **The boundary**, for both record doors the application path uses — the write path and `tortoise record-reconcile`. ⚠️ One reader is outside them: `tortoise/record_read.py` takes a caller-supplied connection and installs no scope, which is #9555's outstanding read arm. | one `*_app_tenant` policy per record table in `THE_RECORD_TABLES` (ten), `USING` **and** `WITH CHECK`; **and, by code**, `record_projection.scope_connection()` — which refuses a connection whose role can bypass them (superuser, `BYPASSRLS`, membership of either, or owner of a record table) |
| **What sets the tenant?** | `set_config('app.current_org_id', $1, true)` — **transaction-local**, issued by `record_projection.replay()` as the first statement, from the same `org_id` it projects. | `record_projection.TENANT_GUC_SQL` |
| **Who sets it on the READ path?** | The SAME helper: `cache_projection.read_record_view()` (the `tortoise record-reconcile` verb) opens its own connection from the same `TORTOISE_RECORD_DSN`, so it installs the same scope before its first `SELECT`. Without it the policies read NULL — zero rows and no error — and a coherent pair WOULD BE reported divergent (`extra_in_cache` with
`missing_in_cache = 0`; the counterfactual is what the read-back prevents, #9600
review cycle 2). | `record_projection.scope_connection()`, called by both doors |
| **Who may bypass?** | `postgres`/table owner and `service_role`, for three named purposes only: schema, the **database-level** backup/restore (R9: `pg_dump`/`pg_restore`), and the org-erasure RPC. They are not the application path — and ⚠️ **not** `record-restore` (R8), which reads through the read door and so connects as the app role. | `purge_org_record` (`20261012000005`) is `SECURITY DEFINER` + `service_role`-only EXECUTE |
| **Which pooler mode?** | R1's DSN is `:5432` = the shared Supavisor pooler, **session** mode. `:6543` = transaction mode. **The posture does not depend on the answer** — see §3. | `docs/infra-runbook.md` → "The record's connecting role" |

### Why not the two bypassing candidates

Both are real options, and both were on the table. They are rejected on the same ground: choosing
either makes §4's recorded sentence — *"rows scoped by tenant via row-level security"* — **false for
the only path that writes customer data**, while leaving every RLS artifact in place to look like it
is true.

- **`postgres` (the table owner)** — what R1's DSN shape names today. Owners bypass RLS unless
  `FORCE ROW LEVEL SECURITY` is set, and a superuser bypasses it regardless; the epilogue would then
  be an `ALTER TABLE … FORCE` on every record table in `THE_RECORD_TABLES` whose write path must still pass `WITH CHECK`, which is
  the same work as choosing a non-bypass role but with the owner's blast radius kept.
- **`service_role`** — what the record migrations' own comment expected (*"every write goes through
  the single write path (#7951, Interface C), which connects as service_role. RLS is the tenancy
  boundary"*). That comment was **self-contradictory**: `service_role` is `BYPASSRLS`, so the
  boundary it names cannot be the one it uses. This is trap (i) of the issue, and the migration's
  comment has been corrected by the new migration's header rather than left as a claim.

## 2. What the failure looks like — and why both directions are tested

Two traps, both accepted deliberately rather than accidentally:

1. **A missing GUC is silent on read.** `current_setting('app.current_org_id', true)` returns NULL
   when unset; a NULL `USING` expression filters every row and *no error is raised*. So an unscoped
   reader sees **zero rows** — indistinguishable from "this tenant has no data".
2. **A missing GUC is loud on write.** A NULL `WITH CHECK` **raises** (`42501`,
   `insufficient_privilege`). A writer whose `org_id` disagrees with the GUC is therefore refused
   rather than silently filed under another tenant.

The asymmetry is the safety property, and it is pinned **as a signature**, in
`supabase/tests/20261022000001_record_tenancy_posture.sql` §8–9: making the read side *fail loudly
too* reddens that suite. That is intentional — changing the signature is a decision, not a tidy-up.

## 3. Why transaction-local, and why the pooler mode is named anyway

Verified against the vendor pages below (`[V-SB]`, `[V-PGB]`), and carried in full by the research
brief posted on #9554:

- **Session `SET` + transaction pooling (`:6543`) = the silent empty read.** PgBouncer's own feature
  map lists `SET`/`RESET` as **"Never"** compatible with transaction pooling `[V-PGB]`, and Supabase
  states session-level state *"is lost between transactions"* on 6543 `[V-SB, connect]`.
- **Session `SET` + session pooling (`:5432`, R1's DSN) = the *other* silent failure, and it is the
  live one today.** The setting survives on a backend connection an application-side pool (#9558)
  may hand to the next tenant: the policies then evaluate against the **previous** tenant's org id
  and return **another tenant's rows**, with no error at all.
- **`SET LOCAL` / `set_config(…, true)` is correct under both**, because it never outlives the
  transaction that reads it — which is also the shape Supabase's own RLS-scoped client documents
  (`begin; select set_config('request.jwt.claims', …, true); set local role …; …; commit;`, *"so
  nothing leaks onto the pooled connection"*) `[V-SB, server]`.

⇒ the DSN can move between 5432 and 6543, or onto a dedicated pooler, **without a code change**. The
in-tree precedent for the same construct is `20261012000005_record_org_purge_rpc.sql:34`
(`set_config('app.record_journal_purge', 'on', true)`).

## 4. The role is created by migration; its credential is not

`CREATE ROLE … NOLOGIN` and the grants/policies ship in the migration, because a policy has to name
a role that exists in the same apply pass. The **credential** does not: a `LOGIN PASSWORD` in a
migration is a secret in git, re-minted on every deploy. Provisioning `LOGIN` + password once, and
granting/revoking/RLS in migrations, is the split Supabase's guidance describes (vendor-sourced for
the `CREATE ROLE`/password mechanics `[V-SB, roles]`; the *split* itself is stated in a staff-answered
community discussion `[C-SB]`, and is labelled as community here on purpose).

Operator steps: `docs/infra-runbook.md` → "The record's connecting role".

## 5. What this decision changes in the plan doc

- **R1's DSN must name the app role**, not `postgres`:
  `postgresql://tortoise_record_app.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require`.
  R1 is not armed (`TORTOISE_RECORD_DSN` is set in no `fly.toml`, `entrypoint.sh` or deploy workflow —
  re-measured 2026-10-23). The runbook's Secrets Matrix *documents* the variable as
  required-when-armed (#9600); documentation is not provisioning, and the value is what `NOT_ARMED`
  turns on. So this is a correction to a plan, not a migration.
- **R7 (#9555) inherits the posture**: its new table ships the same three policies (read-org,
  `service_role`, app-tenant) or it is a table whose RLS state this decision does not explain.
  `tests/test_record_tenancy_guc.py::test_the_migration_covers_every_record_table` reddens until it
  does — it matches against `record_projection.THE_RECORD_TABLES`.
- **R9 keeps the bypassing roles on purpose** (`pg_dump`/`pg_restore` of the database must use the
  direct connection, never the pooler). ⚠️ **R8 does not**: `tortoise record-restore` reads through
  the read door, so it is tenant-scoped and connects as the app role — the verb restores the same
  rows it did before, but its DSN shape changed with this decision (`docs/infra-runbook.md` §9).
  Nothing in this decision narrows R9.

## 6. Evidence, and what this decision does NOT cover

**Measured at this lane's HEAD (`ec9132efe`, 2026-10-22):**

- The nine original record tables (ten since #9555 added `public.session_narrative`;
  `THE_RECORD_TABLES` is the list, not this sentence), each with `ENABLE ROW LEVEL SECURITY` + a `*_read_org` policy on
  `current_setting('app.current_org_id', true)` + a `service_role` `ALL` policy — unchanged.
- **The identity sequence has its own ACL.** `public.relation.id` is
  `bigint GENERATED ALWAYS AS IDENTITY`, so the app role needs `USAGE` on
  `public.relation_id_seq` in addition to the table `INSERT` grant — a table privilege does not
  confer `nextval`, and `has_table_privilege(..., 'INSERT')` reads TRUE while the insert fails
  (#9600 review cycle 2, measured by executing the insert). Granted by the migration; proven by
  §7b of the posture suite, which lands a real `relation` row through the app role.
- **No production code set that GUC** before this change. The one SETTER is `TENANT_GUC_SQL`
  (`tortoise/record_projection.py:239`, `SELECT set_config('app.current_org_id', %s, true)`), executed in
  exactly one place — `_apply_tenant_scope`, `tortoise/record_projection.py:3152` — which both record doors
  reach through `scope_connection()` (`:3200`; `replay()` at `:3327`, `cache_projection.read_record_view()`
  at `:789`). ⚠️ Those line numbers are as of THIS PR's head: at the `ec9132efe` this section is measured
  at, the statement did not exist at all (introduced by this change: `git log -S TENANT_GUC_SQL
  -- tortoise/` returns exactly one commit). The pathspec is required, and it is the same trap as the
  greps below: unscoped, the search also matches DOCUMENTATION that merely quotes the constant — this
  file among it — so it reports a commit that changed a doc, not a setter. The only setters before R6
  were `supabase/tests/*.sql`. `grep -rn TENANT_GUC_SQL tortoise/` finds the setter and its use.
  ⚠️ Three greps that look like the test and are NOT. The setter interpolates
  `TENANT_GUC`, so `grep -rn "set_config('app.current_org_id" tortoise/` returns **one** line and it is
  PROSE — `replay()`'s docstring at `:3269` quotes the statement — which reads as "the code never sets the
  GUC". A bare `grep -rn --include='*.py' set_config tortoise/` reaches two things that are not this GUC:
  `tortoise/projection/__init__.py:1634`'s `set_config_reset_marker` (a GRAPH marker) and
  `tortoise/record_backup.py`'s `statement_timeout`/`lock_timeout` statements. And
  `grep -rn --include='*.py' current_setting tortoise/` returns only the tenant GUC's own read-back
  (`:277`) and prose about it — it does NOT reach `set_config_reset_marker`, whose name does not contain
  `current_setting`, so a reader using that grep to count "the GUC's uses" is counting something else.
  Scope: the greps are over `tortoise/` only — `supabase/migrations/20261025000001_record_tenancy_role_and_guc.sql:43`
  quotes the same statement, so "one place in the tree" is false as a tree-wide claim and is not made here.
- The `authenticated` **read** surface already had a cross-tenant test
  (`20261012000001_record_schema.sql:410-418`, #7968) — that half was **not** missing. What was
  missing was the posture the *write path* runs under, which is what this lane added.

**Sources (all read 2026-10-22):**

- `[V-SB, connect]` Connect to your database —
  <https://supabase.com/docs/guides/database/connecting-to-postgres>
- `[V-SB, roles]` Postgres Roles —
  <https://supabase.com/docs/guides/database/postgres/roles>
- `[V-SB, server]` Server SDK reference (`withPostgresClient`) —
  <https://supabase.com/docs/reference/server>
- `[V-PGB]` PgBouncer features — <https://www.pgbouncer.org/features.html>
- `[C-SB]` Custom LOGIN role provisioning (staff-answered) —
  <https://github.com/orgs/supabase/discussions/51499>

**Not verified by this lane — stated so it is not read as covered:**

- Whether `postgres` on a hosted Supabase project holds `CREATEROLE` for the `CREATE ROLE` in the
  migration. The vendor documents `create role`/`create role … with login password` as available
  (<https://supabase.com/docs/guides/database/postgres/roles>), and the self-hosted and PGlite lanes
  both apply the migration clean, but **no hosted apply was executed here**. If R2's apply refuses
  it, the remedy is one statement — create the role first — not a design change.
- Whether Supavisor resets session state on connection release (the research brief's own open item 1).
  Immaterial to this decision: the GUC is transaction-local, so it is never the pooler's state.
- **This decision does not arm anything.** `TORTOISE_RECORD_DSN` remains unset (R1), so the posture
  is enforced from the moment R1 arms the DSN with the app role — and, until then, is proven by the
  PGlite suite rather than by production traffic.

## Reopening

If a customer requirement makes a bypassing application role necessary, that is a **reopen of this
decision**, argued here with the evidence — not a quiet DSN change. The shape of the argument that
would win is a *capability* the app role cannot serve (an operation that must cross tenants in one
transaction), because the cost of the current posture is one `set_config` per write and one role to
provision.
