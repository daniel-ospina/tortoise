-- ============================================================================
-- Migration 20261025000001: the record's TENANCY POSTURE — the connecting role,
-- and the tenant GUC it is scoped by (R6, epic #7879)
-- ----------------------------------------------------------------------------
-- THE DECISION THIS MIGRATION ENCODES (recorded in full, with its evidence, in
-- `docs/epics/2026-10-10-7879-postgres-record/09-record-tenancy-decision.md`):
--
--   1. THE BOUNDARY IS RLS. §4's recorded decision — *"rows scoped by tenant
--      via row-level security"* — is implemented, not merely documented: the
--      record's application path connects as a NON-BYPASS role, so every
--      `*_read_org` / `*_app_tenant` predicate is actually evaluated for it.
--      That sentence is ENFORCED BY CODE, not left to this file's prose: BOTH
--      record doors — the write path (`record_projection.replay()`) and the read
--      door (`cache_projection.read_record_view()`, i.e. `tortoise
--      record-reconcile`) — are scoped by `record_projection.scope_connection()`,
--      which reads `current_user`'s posture back — superuser, BYPASSRLS,
--      membership in either, or ownership of a record table — and REFUSES the
--      connection rather than writing or reading across tenants with the
--      policies unevaluated (#9600 review cycle 2, P2). A DSN string naming
--      `postgres` is therefore a refusal on either door, not a silent bypass.
--      ⚠️ One reader is OUTSIDE both doors: `tortoise/record_read.py` takes a
--      caller-supplied connection and scopes nothing, so it is neither refused
--      nor tenant-scoped under this migration — that is #9555's outstanding
--      read arm, named here rather than implied by "every record connection".
--   2. THE ROLE IS `tortoise_record_app`. `NOBYPASSRLS`, `NOSUPERUSER`, not the
--      table owner. The two other candidates are BYPASSING and are therefore
--      excluded from the application path, not "used with care":
--        · the `postgres` role R1's DSN shape names (the TABLE OWNER — owners
--          bypass RLS unless FORCE is set, and a superuser bypasses regardless);
--        · `service_role`, which the record migrations' own earlier comment
--          expected even while calling RLS "the tenancy boundary" — it is
--          `BYPASSRLS`, so that comment was false exactly where it mattered.
--      Both remain legitimate for what actually needs them: schema, DATABASE-LEVEL
--      backup and restore (R9: `pg_dump`/`pg_restore`), and the org-erasure RPC
--      (`purge_org_record`, migration 20261012000005, SECURITY DEFINER +
--      service_role-only EXECUTE). They are NOT the application path — and ⚠️ NOT
--      `python -m tortoise record-restore` (R8): that verb reads through the read
--      door (`cache_projection.read_record_view()`), so the role gate refuses a
--      bypassing DSN for it exactly as it does for the write path, and its DSN is
--      the app role (`docs/infra-runbook.md` §9).
--   3. THE GUC IS TRANSACTION-LOCAL, and production code sets it — not a
--      session `SET`, not a connection-init hook. `record_projection.replay()`
--      issues `set_config('app.current_org_id', $1, true)` as the FIRST
--      statement on the caller's transaction, from the SAME `org_id` it is
--      about to project. THE SAME prologue is installed by the READ door
--      (`cache_projection.read_record_view()`), because both doors share the
--      one `TORTOISE_RECORD_DSN`; before that, an ARMED reconciler would have
--      read zero rows and reported a coherent pair as divergent (the
--      counterfactual, not an observation: the DSN is unset on the deployment,
--      and no schedule drives the verb) (#9600 review
--      cycle 2, P1). Two consequences, both deliberate:
--        · the writer's `org_id` and the connection's GUC cannot disagree —
--          there is one value, and it feeds both places;
--        · the posture is POOLER-MODE INDEPENDENT. R1's DSN is port 5432 (the
--          shared Supavisor pooler, session mode), where a session `SET` would
--          survive — and that is the hazard, not the guarantee: an
--          application-side pool reusing that connection hands the next tenant
--          the previous tenant's org id (wrong-tenant rows, silently). Port
--          6543 (transaction mode) drops session state between transactions
--          (PgBouncer lists SET/RESET as "never" compatible with transaction
--          pooling), which turns the same code into the other silent failure:
--          a NULL GUC, and therefore zero rows. Transaction-local is correct
--          under BOTH, so the DSN can move without a code change.
--   4. THE FAILURE IS ASYMMETRIC, AND THAT IS THE POINT. A missing GUC makes
--      `current_setting(..., true)` return NULL, which the `USING` predicate
--      treats as "no rows" — a silent empty read, indistinguishable from "no
--      data". `WITH CHECK` treats the same NULL as a policy violation, so a
--      WRITE raises. The break is therefore silent on read and loud on write,
--      which is the one direction that cannot corrupt the record. Do not
--      "improve" the read side into an error: the policies are pinned in
--      `supabase/tests/20261022000001_record_tenancy_posture.sql` precisely so
--      that changing this signature is a red test, not a surprise at cutover.
--
-- WHY THE ROLE IS CREATED HERE AND ITS CREDENTIAL IS NOT.
-- The policies must name the role, so the role has to exist in the same apply
-- pass — hence `NOLOGIN` and no password here. A password in a migration is a
-- credential in git, and every deploy would re-mint it. Granting the role
-- `LOGIN` and setting its password is an OPERATOR act, done once, recorded in
-- the Secrets Matrix (`docs/infra-runbook.md` → "The record's connecting
-- role"), and it is what R1's DSN then authenticates as
-- (`tortoise_record_app.<project-ref>` on the shared pooler). This split —
-- role SHELL and grants in migration, credential operationally — is the
-- documented Supabase guidance for custom roles.
--
-- ⚠️ Membership is the trap, and `NOINHERIT` is not a substitute for avoiding
-- it: policy APPLICABILITY follows `has_privs_of_role`, so membership in any
-- role that carries a permissive policy (`service_role`'s `*_service_role_all
-- USING (true)`, `postgres`) makes that policy apply to this role — measured on
-- `postgres:16.15`: `GRANT svc TO app` (the default `WITH INHERIT TRUE`) made
-- the app role see 2 rows of 2 tenants while `rolbypassrls` still read `false`.
-- `NOINHERIT` on the role (or `WITH INHERIT FALSE` on the grant) does make the
-- policy inapplicable — measured, `GRANT service_role TO tortoise_record_app
-- WITH INHERIT FALSE` leaves `rolbypassrls` false AND the permissive policy
-- unapplied — but this migration forbids the membership OUTRIGHT anyway: the
-- posture suite refuses any `pg_auth_members` row that joins this role to a
-- BYPASSRLS/SUPERUSER role, so `WITH INHERIT FALSE` reddens the build rather
-- than passing quietly. That is deliberate, and it is STRICTER than the
-- runtime gate, which reads `pg_has_role(…, 'USAGE')` and would accept a
-- non-inheriting membership as harmless. Two rules, ordered: no membership at
-- all (suite), and — should one ever appear — no applicable policy (runtime).
-- `INHERIT` is the default on both sides, so the posture must not depend on
-- nobody having chosen it. The boundary is
-- therefore expressed TWICE: by the grants below, and by the prohibition on
-- membership (asserted in
-- `supabase/tests/20261022000001_record_tenancy_posture.sql` and stated for
-- operators in `docs/infra-runbook.md` → "The record's connecting role").
-- ============================================================================

-- ── 1. The application role ─────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tortoise_record_app') THEN
        EXECUTE 'CREATE ROLE tortoise_record_app '
                'NOLOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE';
    END IF;
END $$;

-- Re-assert the attributes on every apply: an operator who flipped BYPASSRLS on
-- this role would silently turn the tenancy boundary off, and this migration is
-- the one place that states what the role IS.
--
-- ⛔ `ALTER ROLE … SUPERUSER/NOSUPERUSER` and `… BYPASSRLS/NOBYPASSRLS` are
-- SUPERUSER-ONLY clauses (#9600 review cycle 1, P1): a bare `ALTER ROLE` here
-- aborted the whole migration on every applier that is not a superuser —
-- including a hosted Postgres whose `postgres` role is a `CREATEROLE`
-- non-superuser (measured as `NOSUPERUSER CREATEROLE`: `ERROR: permission
-- denied to alter role`, and clause-by-clause `NOBYPASSRLS` alone fails too).
-- The PGlite lane runs as superuser, so CI could never see it: the migration
-- applied green there and refused on the deployment that matters.
--
-- So the re-assert is SUPERUSER-ONLY, and a non-superuser applier FAILS CLOSED
-- instead of silently skipping it: if the attributes have drifted and this
-- applier cannot correct them, the migration raises and names the operator step
-- (docs/infra-runbook.md → "The record's connecting role").
DO $$
DECLARE
    _applier_is_super boolean := (SELECT rolsuper FROM pg_roles WHERE rolname = current_user);
    _drifted boolean := (SELECT rolbypassrls OR rolsuper OR rolcreatedb OR rolcreaterole
                           FROM pg_roles WHERE rolname = 'tortoise_record_app');
BEGIN
    IF _applier_is_super THEN
        EXECUTE 'ALTER ROLE tortoise_record_app '
                'NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE';
    ELSIF _drifted THEN
        RAISE EXCEPTION 'tortoise_record_app has drifted from the R6 tenancy decision '
                        '(needs NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE) and this '
                        'applier is not a superuser, so it cannot re-assert them. Fix the role '
                        'as an operator (docs/infra-runbook.md, "The record''s connecting role") '
                        'and re-apply — refusing rather than leaving the tenancy boundary open.';
    END IF;
END $$;

-- `COMMENT ON ROLE` needs superuser or ADMIN OPTION on the target role, which is
-- a DIFFERENT privilege from the ALTER ROLE guard above — a non-superuser
-- applier that did not create the role cannot comment on it, and an unguarded
-- statement would abort the whole tenancy posture with a bare `permission
-- denied`. The comment is documentation, so its absence must never be fatal:
-- swallow exactly that error.
DO $$
BEGIN
    COMMENT ON ROLE tortoise_record_app IS
        'The record''s APPLICATION role (R6, epic #7879) — the only role the record write path may connect as. NOBYPASSRLS by design: RLS is the tenancy boundary, and every record policy reads app.current_org_id, which record_projection.replay() sets transaction-locally. Not for schema, backup/restore or erasure — those use the owner/service_role on purpose. LOGIN + password are provisioned by ops (docs/infra-runbook.md).';
EXCEPTION WHEN insufficient_privilege THEN
    NULL;
END $$;

GRANT USAGE ON SCHEMA public TO tortoise_record_app;

-- ── 2. Table privileges ─────────────────────────────────────────────────────
-- SELECT is granted for the same reason as the writes: the role is the record's
-- one non-bypass door, so a read through it must be RLS-scoped rather than
-- impossible. Every privilege below is bounded by the policy in section 3 — a
-- table grant is never the boundary on its own (RLS runs after privileges).
GRANT SELECT, INSERT, UPDATE, DELETE ON public.node_identity  TO tortoise_record_app;
-- `journal_event` gets SELECT and INSERT ONLY, and that asymmetry is the point.
-- The projection never updates or deletes a journal row — its one journal
-- statement is an INSERT (`record_projection._journal_row`), and the four
-- `DELETE`s in the plan are on `node_identity`, `relation`, the node tables and
-- `session_narrative`. Erasing a tenant goes through `purge_org_record`, which is
-- SECURITY DEFINER and owned by the applier, so it does not need this role's
-- DELETE either.
-- Granting DELETE here would not be inert: `journal_event_is_append_only`
-- (20261012000001) admits DELETE when `app.record_journal_purge` is `on`, and
-- ANY role may `set_config` that GUC transaction-locally. A DELETE/UPDATE grant
-- would therefore let the application role erase its own tenant's journal
-- history, which is exactly what the table COMMENT promises is impossible
-- ("APPEND-ONLY is enforced (trigger), not merely documented") and what
-- `derived = replay(journal)` depends on. The append-only invariant is only a
-- boundary if the boundary role cannot reach the escape hatch.
GRANT SELECT, INSERT ON public.journal_event  TO tortoise_record_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.source         TO tortoise_record_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.event          TO tortoise_record_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.session        TO tortoise_record_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.object         TO tortoise_record_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.subject        TO tortoise_record_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.point          TO tortoise_record_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.relation       TO tortoise_record_app;
-- The tenth table (§9.1 / R7, #9555). Listed here rather than beside its
-- policy at §4b because the ACL and the policy are the same pair for all ten:
-- a table grant without a policy reads zero rows, a policy without a grant
-- raises 42501 before RLS is ever consulted. Its migration is
-- `20261023000001_record_session_narrative.sql`, which sorts before this one.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.session_narrative TO tortoise_record_app;

-- ── 2b. `public.relation`'s IDENTITY sequence needs its OWN grant ─────────
-- A table-level INSERT privilege does NOT confer `nextval` on the sequence that
-- backs an IDENTITY column: the sequence carries a separate ACL, and its owner
-- is the migration applier. `public.relation.id` is
-- `bigint GENERATED ALWAYS AS IDENTITY` (20261012000003:167), so without this
-- grant EVERY app-role INSERT into `relation` raises `permission denied for
-- sequence relation_id_seq` (42501) — and because the statement aborts the
-- replay transaction, the whole edge-bearing record is lost, not just the edge
-- (#9600 review cycle 2, P1).
--
-- The posture suite cannot INFER this from `has_table_privilege(..., 'INSERT')`
-- — that reads true while the insert itself fails — so §3 asserts
-- `has_sequence_privilege` and §7 LANDS an app-role `relation` row (with its
-- refused cross-tenant twin) as the positive proof.
-- `relation` is the only record table with an identity column (measured: one
-- `GENERATED … AS IDENTITY` across the four CREATE-TABLE migrations).
GRANT USAGE ON SEQUENCE public.relation_id_seq TO tortoise_record_app;

-- ── 3. The tenant policy, one per record table ──────────────────────────────
-- `FOR ALL` with BOTH clauses. `USING` gates what the role can see and what a
-- row-targeted write may touch; `WITH CHECK` gates what it may write. Dropping
-- either one leaves a hole the other cannot see:
--   · USING alone  ⇒ an INSERT is not checked, so the writer could file a row
--                    under another tenant;
--   · WITH CHECK alone ⇒ reads and row-targeted UPDATE/DELETE are unbounded.
-- Both read the SAME expression as the `authenticated` read policy, so the two
-- surfaces can never drift into different definitions of "this tenant".
--
-- `current_setting('app.current_org_id', true)` — the `true` is load-bearing:
-- without it a missing GUC raises instead of returning NULL, and every read on
-- a connection that forgot the GUC would fail rather than return nothing.
-- Neither is "safe" on its own; the decision (section 4 of the header) is to be
-- silent on read and loud on write, and the posture suite pins both directions.
DROP POLICY IF EXISTS node_identity_app_tenant ON public.node_identity;
CREATE POLICY node_identity_app_tenant ON public.node_identity
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS journal_event_app_tenant ON public.journal_event;
CREATE POLICY journal_event_app_tenant ON public.journal_event
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS source_app_tenant ON public.source;
CREATE POLICY source_app_tenant ON public.source
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS event_app_tenant ON public.event;
CREATE POLICY event_app_tenant ON public.event
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS session_app_tenant ON public.session;
CREATE POLICY session_app_tenant ON public.session
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS object_app_tenant ON public.object;
CREATE POLICY object_app_tenant ON public.object
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS subject_app_tenant ON public.subject;
CREATE POLICY subject_app_tenant ON public.subject
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS point_app_tenant ON public.point;
CREATE POLICY point_app_tenant ON public.point
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS relation_app_tenant ON public.relation;
CREATE POLICY relation_app_tenant ON public.relation
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

-- ── 4. What this role does NOT get, and why no statement is needed ─────────
-- A role created later inherits everything granted to PUBLIC, and on a Supabase
-- project PUBLIC holds `USAGE` on `public` and EXECUTE on functions created
-- there — so "nothing was granted to it" is not the same as "it can do
-- nothing". The record tables are already `REVOKE ALL … FROM anon,
-- authenticated, public` (the four CREATE-TABLE migrations), which is what
-- keeps PUBLIC out, so the grants above are this role's whole table surface.
-- The posture suite asserts that rather than restating it here: it checks the
-- role's EFFECTIVE privileges (not the statements that produced them), so a
-- future PUBLIC grant reddens a test instead of quietly widening the boundary.

-- ── 4b. The TENTH record table: `public.session_narrative` (§9.1 / R7, #9555) ─
-- The table itself arrives in `20261023000001_record_session_narrative.sql`. This
-- migration sorts AFTER it (20261025 > 20261023) and that ordering is DELIBERATE,
-- not incidental:
--   * the tenancy posture is the LAST thing that widens the app role's surface, so
--     it covers every record table that exists — the alternative is a posture
--     migration that must be revisited every time a table is added, and a new
--     table that silently ships with no tenant policy;
--   * `20261023000001`'s own app-role block is GUARDED on `tortoise_record_app`
--     existing, and on a fresh database it does not exist yet at that point, so
--     without this block NOTHING would grant the role on this table;
--   * it also vacates the version prefix `20261022000001`, which this file used to
--     share with `20261022000001_record_session_capture_redactions_integer.sql`
--     and `20261022000001_blog_indexnow.sql`. `supabase db push` keys migrations
--     by that version and `.github/scripts/check-migration-append-only prefix`
--     refuses a duplicate, so three colliders was strictly worse than two.
--     ⚠️ It does NOT close that gate: the remaining pair is both already on
--     `main`, so the leg stays RED until the IndexNow file is renumbered
--     (`e6e91394d` does that on its own lane, to `20261024000001`). This file's
--     prefix, `20261025000001`, is claimed by no other migration;
--   * ⛔ and it is where the ORDERING CORRECTION of record lives, because
--     `20261023000001`'s own header still states the opposite — that R6 sorts
--     BEFORE it "but is not in this tree yet". That prose is now stale, and it is
--     deliberately NOT edited: migrations are append-only (#1095,
--     `.github/scripts/check-migration-append-only diff`), so a merged file's
--     comments are as immutable as its SQL. Read the paragraphs above as the
--     correction of that header, not as a duplicate of it. Its GUARDED BLOCK's
--     own comment carries the same stale claim in a second place ("R6's
--     migration **sorts first**, so where R6 is applied this branch runs") —
--     equally false, and equally not editable: on a fresh database the role does
--     not exist when `20261023000001` applies, so that block cannot fire and
--     §4b below is what grants and policies that table. Read both stale
--     sentences as corrected here.
--     The header also cites
--     this file by its VACATED name (`20261022000001_record_tenancy_role_and_guc
--     .sql`, which no longer exists): the prefix moved to `20261025000001` in
--     this same change, to stop sharing it with two other migrations, so a reader
--     who greps the header's filename finds nothing. The narrower truth
--     about its guarded block is in that block's own comment: it fires only
--     where `tortoise_record_app` already existed when that migration applied,
--     and it cannot fire on a fresh database.
-- Unconditional here, exactly like the nine above: the table is guaranteed to
-- exist by this migration's own position in the sequence, so a guard would only
-- hide a reordering mistake instead of failing on it.
DROP POLICY IF EXISTS session_narrative_app_tenant ON public.session_narrative;
CREATE POLICY session_narrative_app_tenant ON public.session_narrative
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
