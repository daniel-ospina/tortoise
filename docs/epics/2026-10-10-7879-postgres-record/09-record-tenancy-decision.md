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
| **Which role does the record connect as?** | `tortoise_record_app` — `NOBYPASSRLS`, `NOSUPERUSER`, not the table owner. Created by migration, granted `SELECT/INSERT/UPDATE/DELETE` on exactly the 9 record tables. **Not** `postgres` (R1's DSN shape today) and **not** `service_role`. | `supabase/migrations/20261022000001_record_tenancy_role_and_guc.sql` §1–3 |
| **Is RLS the boundary, or defence-in-depth?** | **The boundary**, for every surface the application path touches (write *and* read). | the 9 `*_app_tenant` policies, `USING` **and** `WITH CHECK` |
| **What sets the tenant?** | `set_config('app.current_org_id', $1, true)` — **transaction-local**, issued by `record_projection.replay()` as the first statement, from the same `org_id` it projects. | `record_projection.TENANT_GUC_SQL` |
| **Who may bypass?** | `postgres`/table owner and `service_role`, for three named purposes only: schema, backup/restore (R8/R9), and the org-erasure RPC. They are not the application path. | `purge_org_record` (`20261012000005`) is `SECURITY DEFINER` + `service_role`-only EXECUTE |
| **Which pooler mode?** | R1's DSN is `:5432` = the shared Supavisor pooler, **session** mode. `:6543` = transaction mode. **The posture does not depend on the answer** — see §3. | `docs/infra-runbook.md` → "The record's connecting role" |

### Why not the two bypassing candidates

Both are real options, and both were on the table. They are rejected on the same ground: choosing
either makes §4's recorded sentence — *"rows scoped by tenant via row-level security"* — **false for
the only path that writes customer data**, while leaving every RLS artifact in place to look like it
is true.

- **`postgres` (the table owner)** — what R1's DSN shape names today. Owners bypass RLS unless
  `FORCE ROW LEVEL SECURITY` is set, and a superuser bypasses it regardless; the epilogue would then
  be an `ALTER TABLE … FORCE` on nine tables whose write path must still pass `WITH CHECK`, which is
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
  R1 is not armed (`TORTOISE_RECORD_DSN` is in no `fly.toml`, `entrypoint.sh`, deploy workflow or
  Secrets Matrix — re-measured 2026-10-22), so this is a correction to a plan, not a migration.
- **R7 (#9555) inherits the posture**: its new table ships the same three policies (read-org,
  `service_role`, app-tenant) or it is a table whose RLS state this decision does not explain.
  `tests/test_record_tenancy_guc.py::test_the_migration_covers_every_record_table` reddens until it
  does — it matches against `record_projection.THE_RECORD_TABLES`.
- **R8/R9 keep the bypassing roles on purpose** (`pg_dump`/restore must use the direct connection,
  never the pooler). Nothing in this decision narrows them.

## 6. Evidence, and what this decision does NOT cover

**Measured at this lane's HEAD (`ec9132efe`, 2026-10-22):**

- 9 record tables, each with `ENABLE ROW LEVEL SECURITY` + a `*_read_org` policy on
  `current_setting('app.current_org_id', true)` + a `service_role` `ALL` policy — unchanged.
- **No production code set that GUC** before this change (`grep` over `tortoise/`, `apps/`, `tools/`,
  `services/`: zero `current_setting`/`set_config`; the only setters were `supabase/tests/*.sql`).
- The `authenticated` **read** surface already had a cross-tenant test
  (`20261012000001_record_schema.sql:373-414`, #7968) — that half was **not** missing. What was
  missing was the posture the *write path* runs under, which is what this lane added.

**Sources (all read 2026-10-22):**
`[V-SB, connect]` Connect to your database — https://supabase.com/docs/guides/database/connecting-to-postgres ·
`[V-SB, roles]` Postgres Roles — https://supabase.com/docs/guides/database/postgres/roles ·
`[V-SB, server]` Server SDK reference (`withPostgresClient`) — https://supabase.com/docs/reference/server ·
`[V-PGB]` PgBouncer features — https://www.pgbouncer.org/features.html ·
`[C-SB]` Custom LOGIN role provisioning (staff-answered) — https://github.com/orgs/supabase/discussions/51499

**Not verified by this lane — stated so it is not read as covered:**

- Whether `postgres` on a hosted Supabase project holds `CREATEROLE` for the `CREATE ROLE` in the
  migration. The vendor documents `create role`/`create role … with login password` as available
  (https://supabase.com/docs/guides/database/postgres/roles), and the self-hosted and PGlite lanes
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
