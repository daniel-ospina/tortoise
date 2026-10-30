"""Interface A — the record projection: journal → Postgres, total and idempotent.

WHY THIS MODULE EXISTS
======================
`docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) makes
**Postgres the system of record for every layer** and FalkorDB a **derived hot
cache**. §3 states the invariant that makes that safe:

    derived tables  =  replay(the journal)

`tortoise/projection/` already *is* a replay of the journal — into FalkorDB.
It is therefore the **derived cache's** fold, and a cache rebuilt from a cache
proves nothing. This module is the **record's own** fold (Interface A, epic
`#7879` item 3, issue **#7969**): the same journal, replayed into Postgres.

The issue states four properties, and each is a property of this module rather
than an aspiration:

1. **Every journal event type has a defined Postgres mutation.** Every record
   type in :data:`RECORD_MUTATIONS` carries one — a node materialization, a
   targeted patch, a delete, a relation upsert, or an explicit
   :class:`NoRecordEffect` **with a written reason**.
2. **Idempotent** — a second replay of the same journal produces the same
   record. Every mutation is written so that re-applying it is a no-op: node
   upserts are `ON CONFLICT … DO UPDATE SET` (same values ⇒ same row), patches
   are `UPDATE … SET` to absolute values, deletes are `DELETE`. The journal
   position is *also* recorded (`public.journal_event`, keyed on
   `(org_id, graph_id, seq)`) so the record can say which records it consumed.
3. **Total** — an event with no handler is a **failure, not a skip**. This is
   `#5285`'s rule, applied to the record's destination. `#3585`'s non-folded
   collector made the *graph* fold fail closed; this module is the record's
   equivalent, and it fails at the same place for the same reason: **two
   equally incomplete projections compare equal**, so a fold that silently
   skips an event manufactures a vacuous green.
4. **A field is PRIMARY iff the journal carries it** (§3's per-write test).
   The projection is where that rule becomes executable, so the property→column
   decision is **read from the schema on disk**, never restated here: a property
   with a column is written, a property the schema refuses (the EP-scalar
   carve-out) is **never** written, and a property with neither a column nor a
   carve-out lands in the table's declared `props` tail. A DERIVED value can
   therefore not become authoritative through this door — the schema's
   `chk_*_props_no_derived` CHECK is the backstop, and this module does not rely
   on it being the first line.

WHAT THIS MODULE DELIBERATELY IS NOT
====================================
* **Not the write chokepoint.** §2.3/Interface C is `#7951` — one live path that
  appends the journal, applies the record projection and applies the cache
  projection. This module is the *second* step of that path; it does not route
  live writes.
* **Not the cache projection.** Record → FalkorDB is `#7971` (Interface B).
* **Not a second declaration of the placement table.** `#7968`'s
  `tests/test_record_schema_placement.py` pins §17.5's table to the SQL; this
  module consumes the SQL.
* **No migration-safety apparatus.** Owner scope ruling 2026-10-10: the only
  customer is the dogfood instance. No dual-write, backfill, cutover or
  rollback-parity surface appears here, and none should be added.

TENANCY IS A PER-CALL ARGUMENT AT THIS LAYER, NEVER AMBIENT
===========================================================
Every layer row is keyed by `(org_id, graph_id)` with a foreign key to
`public.graphs`. Both are **required keyword arguments** of every entry point
here (:func:`mutations_for`, :func:`replay_plan`, :func:`replay`), and of the
seam that drives them (`record_writer.RecordWriter.write`, #9194): at this
layer there is deliberately no module-level default, no ContextVar and no
environment fallback. `test_this_module_has_no_ambient_tenant_READER` executes
the half of that a scan can execute — no environment reader and no
`contextvars` — while the parameter half (each coordinate required on its own)
is executed by the refusal leg in `tests/test_record_projection.py` for these
three entry points, and by
`test_the_seam_refuses_a_call_that_cannot_name_its_tenant` for
`RecordWriter.write` / `write_records`, which is what a production caller
touches. What defeats the SCAN, and only the scan, is an ACTIVE obfuscated
read (`org_id = getattr(os, "environ").get("TORTOISE_RECORD_ORG_ID") or
org_id`, `__import__("os")`): spelled that way it still reds the same value
legs as the plain spelling (measured, three of them) — while a DORMANT
spelling of it defeats both, like any dormant read (measured, all green). What
escapes BOTH instruments is a read that never fires on a fixture path — a
body-local fallback to a module constant (`org_id = org_id or
_DEFAULT_TENANT[0]`, signature still required), or a carrier DEFINED ELSEWHERE
(a `ContextVar` imported from another module — for the record's tenant,
`record_writer.use_record_tenant`, whose production caller #9192 installed in
`mcp_auth.OrgResolutionMiddleware`): the scan parses THIS file only, so a read
of that carrier here is invisible to it, and no fixture in this file arms one.
Measured: all legs stay green on both. A signature default, however, IS
caught — by the refusal leg on these entry points, and by the seam leg on
`RecordWriter.write` / `write_records`. These limits are SUMMARISED here and
MEASURED in `tests/test_record_projection.py`, except the signature one, which
is pinned in both test files.

The reason is the failure mode, not taste. The hosted lane builds one SDK per
tenant graph and its capture executor is process-wide, so a tenant resolved
from anything process-global files tenant A's rows under tenant B **with no
error** — the row is well-formed and the FK only checks that the graph exists.
An environment pair is not a mitigation for that: env is process-global, so it
is the same single value for every tenant the process serves. A caller that
cannot name its tenant must refuse.

⚠️ WHERE THE AMBIENT CARRIER ARRIVES, and this module cannot see it (its scan
parses ONE file). The surface #9192 names is `record_writer.use_record_tenant`,
a ContextVar with a `TORTOISE_RECORD_ORG_ID`/`_GRAPH_ID` fallback for a
SINGLE-TENANT deployment. On this tree it EXISTS — the carrier landed in #9231
(`tortoise/record_writer.py`: `_RECORD_TENANT`, `use_record_tenant()`,
`record_tenant()`) — and #9192 supplied the two halves it was missing:

* **The production caller.** `mcp_auth.OrgResolutionMiddleware.dispatch`
  enters `record_writer.use_record_tenant(org_id, graph_id)` around
  `call_next`, from the `(org_id, graph_id)` that lane already resolves per
  request — so a write anywhere under the request, on the loop or on a
  context-copying pool worker, is filed under the REQUEST's tenant. It is
  installed only when the resolution NAMED a graph, and only on the
  CONTROL-PLANE lane: the self-host registry's `graph_id` is a Falkor `g_<hex>`
  node id with no `public.graphs` row, so installing it would be a row the FK
  cannot satisfy. Since #10025 an org-wide credential is no longer outside
  that: its tenant is read from `default_graph_id`, the org's active
  `kind='default'` `public.graphs` row that `20261028000001` materializes and
  provisioning mints (`supabase_control.ensure_default_graph_row`), and an org
  with no such row still refuses (`no_tenant`) rather than guessing. The
  hosted REST lane carries the SAME pair by value rather than through this
  `ContextVar` — `hosted_api._data_sdk` → `sdk._bind_record_tenant` — which is
  the seam its `TortoiseSDK` reads. Since #10046 every request credential is
  named on that lane too (`hosted_api._session_user_org` and
  `tortoise.oauth.resolve_oauth_access_token` stamp the same
  `default_graph_id`). The lanes that build their OWN SDK — the background
  indexing jobs and the seed runners — are wired since #10047
  (`hosted_api._bind_default_graph_record_tenant`, same `record_tenant_for`
  answer, org default graph by construction).
* **The arming gate.** `record_tenant()` reads the `ContextVar` first and
  consults the env pair ONLY when the deployment declared itself single-tenant
  (`record_writer.single_tenant_declared()` → `TORTOISE_RECORD_SINGLE_TENANT`,
  default ABSENT = multi-tenant). Undeclared, the pair is ignored: the armed
  write is refused and counted `no_tenant`, never filed under a process-global
  value. So "scope wins over env" is now a fail-closed rule, not a precedence
  convention — the failure mode left is a named refusal.

The precedence this layer implements is unchanged — **scope always wins**, a
half-configured pair is refused rather than completed — and the arming surface
is pinned in `tests/test_9192_tenant_armed_by_request.py`
(`test_an_undeclared_process_refuses_the_environment_tenant`,
`test_a_declared_single_tenant_deployment_still_arms_by_env`). The regression
pins for THIS layer remain `test_two_tenants_in_one_process_do_not_cross_write`
and `test_two_tenants_through_the_seam_never_cross_write`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = [
    "JOURNAL_EVENT_TYPES",
    "NARRATIVE_RECORD_TYPE",
    "NARRATIVE_TABLE",
    "PACKAGED_MIGRATIONS",
    "RECORD_MUTATIONS",
    "ROLE_POSTURE_SQL",
    "TENANT_GUC",
    "TENANT_GUC_SQL",
    "TENANT_SCOPE_SQL",
    "THE_RECORD_TABLES",
    "TURNS_RECORD_TYPE",
    "TURNS_SNAPSHOT_KEYS",
    "TURNS_SNAPSHOT_OPTIONAL_KEYS",
    "TURNS_SNAPSHOT_UNATTESTABLE_KEYS",
    "RecordProjectionError",
    "RefusedJournalOperation",
    "ReplayReport",
    "UnknownJournalRecordType",
    "mutations_for",
    "refused_props_names",
    "replay",
    "replay_plan",
    "scope_connection",
    "snake",
    "table_columns",
    "to_dollar_placeholders",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

RECORD_MIGRATIONS: tuple[str, ...] = (
    # THE REGISTRY, not merely the set of files that shape a column. Every
    # `*_record_*.sql` the wheel ships must be named here — both directions are
    # pinned by `tests/test_9231_record_migrations_trigger.py` — because the
    # FILENAME is the only total partition of "is this the record's schema".
    # `tests/test_record_projection.py` infers the same set from the SQL's
    # SHAPE, which is a heuristic over spellings: a column added by a spelling
    # it does not recognise (an `ALTER` inside a `DO` block, `ADD` without
    # `COLUMN`) would be shipped by the wheel and read by nobody, with nothing
    # red. An entry that contributes no column therefore still belongs here.
    "20261012000001_record_event_stream.sql",
    "20261012000002_record_semantic.sql",
    "20261012000003_record_epistemic.sql",
    "20261012000004_record_procedural.sql",
    # The erasure RPC (#7968, PR #8179 review P2): a SECURITY DEFINER function,
    # so it contributes NO column to `table_columns()` and no name to
    # `refused_props_names()` (measured: both are identical with and without
    # it). It is listed anyway, per the registry rule above — leaving it out was
    # the 1-of-7 gap #9550 measured.
    "20261012000005_record_org_purge_rpc.sql",
    # A FORWARD migration re-declaring a refusal. Migrations are append-only
    # history, so a later refusal (`subject.props` gains `status`, #8263)
    # arrives as `ALTER TABLE … ADD CONSTRAINT` — and the schema read below
    # must see it, or the record projection would emit a props tail the
    # constraint refuses and hand a caller a crash instead of a drop.
    "20261014000001_record_subject_props_no_derived.sql",
    # A FORWARD migration adding COLUMNS (`ALTER TABLE public.point ADD COLUMN`,
    # #2782's money value). Same reason: without it `table_columns()` reports
    # the pre-ALTER schema, so `_split_properties` would leave an amount in the
    # `props` tail and `cache_projection` would not project it.
    "20261018000001_record_point_money_value_2782.sql",
    # A FORWARD migration NARROWING a column's TYPE (`ALTER TABLE public.session
    # ALTER COLUMN capture_redactions TYPE integer`, #9264). The readers do not
    # parse this form, and deliberately: the projection hands a non-`dict`/
    # `list` value through unchanged, so the column's declared type was the only
    # thing disagreeing with the producer's integer count. It is registered so
    # the schema read and the placement pin still see one authoritative file
    # list — an unregistered migration is invisible to every reader.
    "20261022000001_record_session_capture_redactions_integer.sql",
    # §9.1's narrative home (#9555, R7): a NEW table, not an ALTER, so the
    # reader must see it or `_split_properties` would refuse every narrative
    # column and the projection would write nothing while reporting success.
    "20261023000001_record_session_narrative.sql",
    # #9555/R7: the narrative CARRIER, recorded on the SCHEMA as COMMENT ON
    # (append-only — the table's own creation file cannot be edited, #1095).
    # Contributes no COLUMN, so it is listed for the registry reason above: the
    # filename is the only total partition of "is this the record's schema", and
    # an unlisted migration is invisible to the reverse direction of
    # `tests/test_9231_record_migrations_trigger.py`.
    "20261024000001_record_session_narrative_carrier.sql",
    # The tenancy boundary itself (#9554, epic #7879): the app role, its table
    # grants and the tenant GUC. It contributes NO column and no refusal — and
    # it is listed anyway, per the registry rule above. Leaving it out was the
    # 1-of-8 gap the #9600 review cycle 1 measured: the wheel ships it
    # (`pyproject.toml`'s `*_record_*.sql` glob matches) and
    # `tests/test_9231_record_migrations_trigger.py` reds on the omission, so
    # the registry is the tuple or it is nothing.
    "20261025000001_record_tenancy_role_and_guc.sql",
    # #9967 review P2 — an INDEX, contributing no column and no refusal, listed
    # for the registry reason above: `pyproject.toml`'s `*_record_*.sql` glob
    # ships it, and `tests/test_9231_record_migrations_trigger.py` reds on the
    # omission. It is not decorative to the projection: `#9956`'s row-key
    # resolution adds a `canonical_url = $1` predicate to EVERY source write, and
    # without this index that arm is a tenant-wide sequential scan once per
    # statement (measured: ~8.7 ms per write at 20,000 rows), which makes a
    # journal rebuild O(rows × sources) instead of O(rows).
    "20261025000002_record_source_canonical_index.sql",
    # #9881 — the KEY MOVE: `Object`/`Subject` key on the stable `id` instead of
    # the mutable `name`, which is what lets `EntityMutated op=rename` be a patch.
    # It is listed for the registry reason above AND because it RE-DECLARES
    # `node_identity.key_property`, which `key_properties()` must be able to see:
    # a reader stuck on the CREATE TABLE would hand the cache projection the
    # pre-move contract (`Object -> name`) while the database enforced the
    # post-move one.
    "20261027000001_record_semantic_id_key.sql",
    "20261029000001_record_point_cache_tier.sql",
)

#: The tenancy GUC every record RLS policy reads (`*_read_org`, `*_app_tenant`).
#: One name, one definition: the record migrations read it and
#: :data:`TENANT_GUC_SQL` is the only place production code sets it. The two
#: halves are compared in `tests/test_record_tenancy_guc.py`, because a renamed
#: GUC makes every policy read NULL — which is zero rows and no error.
TENANT_GUC = "app.current_org_id"

#: The ONE statement that establishes the record's tenant on a connection.
#: `is_local` is the third argument and is deliberately `true` — transaction
#: scoped, never session scoped (R6). A session `SET` is the failure mode, not
#: the mechanism: on the shared pooler's transaction mode (:6543) session state
#: does not survive between transactions, so the same code would silently read
#: zero rows; on session mode (:5432, R1's DSN today) it would survive onto a
#: pooled connection reused by the NEXT tenant, which returns another tenant's
#: rows instead. Transaction-local is correct under both, so the DSN can move
#: without a code change. Bound as a parameter: an org id is never interpolated
#: into SQL.
TENANT_GUC_SQL = f"SELECT set_config('{TENANT_GUC}', %s, true)"

#: The OTHER half of the same guarantee, on the same round trip (#9600 review
#: cycle 2, P2): the GUC read-back says the tenant took effect, and this says the
#: CONNECTED ROLE cannot bypass the policies that read it. Both are needed for
#: RLS to be the boundary rather than a decoration, and neither implies the
#: other — a `postgres`/`service_role` DSN sets the GUC perfectly, reads it back
#: perfectly, and evaluates no policy at all.
#:
#: Four ways to bypass, all measured on the connection itself rather than
#: inferred from the DSN string:
#:
#: 1. `rolsuper` — a superuser bypasses RLS unconditionally;
#: 2. `rolbypassrls` — `service_role`'s attribute, and the trap the decision
#:    document names;
#: 3. MEMBERSHIP in any of the above. Policy applicability follows
#:    `has_privs_of_role`, so `GRANT service_role TO tortoise_record_app` makes
#:    `*_service_role_all USING (true)` apply to the app role while
#:    `rolbypassrls` still reads `false` (measured on `postgres:16.15`; the
#:    migration header and the posture suite both pin it). Asserted as
#:    `'USAGE'`, not `'MEMBER'`, because `NOINHERIT` (or a `WITH INHERIT FALSE`
#:    grant) makes the policies inapplicable and `USAGE` false — the two agree.
#:    Generic over every bypassing role, so no role name has to exist for
#:    the check to run — `pg_has_role(current_user, 'service_role', 'USAGE')`
#:    would raise on a plain Postgres that has no such role;
#: 4. TABLE OWNERSHIP (or membership of the owning role). Owners bypass RLS
#:    unless `FORCE ROW LEVEL SECURITY` is set, which is exactly why the
#:    `postgres` DSN in R1's documented pre-change shape was rejected — it is
#:    the owner of every record table (`THE_RECORD_TABLES` — ten, since #9555).
#:
#: `current_setting(...)` keeps the GUC read-back and the role posture in ONE
#: statement rather than two round trips, so the seam executes exactly TWO
#: statements outside the plan: the `set_config` prologue (`TENANT_GUC_SQL`) and
#: this verify (`TENANT_SCOPE_SQL`). Both are tenant-context statements, not
#: record mutations, and a caller that filters on `current_setting` keeps seeing
#: every non-plan statement the seam issues. The org id and the table names
#: travel as bind parameters.

#: The ROLE POSTURE half of `TENANT_SCOPE_SQL` below, on its own, so the SAME four
#: predicates decide "does this connection bypass RLS?" on every path that asks
#: — the tenant scope (which REQUIRES non-bypassing) and R9's backup (which
#: REQUIRES bypassing, one of the three purposes R6 names). Two copies of these
#: predicates would be two answers to one question, and the backup's copy would
#: be the one nobody re-reads: #9801 is exactly that failure — a backup verb
#: connected as the app role, counted ZERO rows under the `*_app_tenant`
#: policies, and reported the record as EMPTY.
_ROLE_POSTURE_SELECT = (
    "       r.rolsuper OR r.rolbypassrls "
    "       OR EXISTS (SELECT 1 FROM pg_roles g "
    "                   WHERE g.oid <> r.oid "
    "                     AND (g.rolsuper OR g.rolbypassrls) "
    "                     AND pg_has_role(r.oid, g.oid, 'USAGE')) "
    "       OR EXISTS (SELECT 1 FROM pg_class c "
    "                    JOIN pg_namespace n ON n.oid = c.relnamespace "
    "                   WHERE n.nspname = 'public' "
    "                     AND c.relname = ANY(%s) "
    "                     AND (c.relowner = r.oid "
    "                          OR pg_has_role(r.oid, c.relowner, 'USAGE'))) "
    "       AS bypassing "  # named so a mapping-shaped row (RealDict) can be read
)

#: The posture ALONE: ``(role, bypassing)`` for ``current_user``, with no GUC in
#: the statement. For a caller whose obligation is the OPPOSITE of the scope's —
#: R9's ``pg_dump``/``pg_restore``, which R6 names as a legitimate bypass purpose
#: because a backup must see every tenant. ``role`` is selected so a refusal can
#: NAME the role instead of blaming the data (#9801). Takes ONE bind parameter:
#: the table set, exactly like :data:`TENANT_SCOPE_SQL`.
ROLE_POSTURE_SQL = (
    "SELECT r.rolname AS role, "
    + _ROLE_POSTURE_SELECT
    + "  FROM pg_roles r "
    " WHERE r.rolname = current_user"
)

TENANT_SCOPE_SQL = (
    f"SELECT current_setting('{TENANT_GUC}', true) AS tenant, "
    + _ROLE_POSTURE_SELECT
    + "  FROM pg_roles r "
    " WHERE r.rolname = current_user"
)

#: Where the schema lives when this module is INSTALLED rather than checked out.
#: `supabase/migrations` is mapped into the wheel as the `tortoise.
#: record_migrations` package (`pyproject.toml` `package-dir`), so a wheel has
#: the very files `table_columns()` parses. Without it every ARMED live write in
#: a wheel install raised before the first statement and was counted
#: `write_failed` — the record stayed empty while the wiring gate read green,
#: because the gate is static and the tests were repo-shaped (#9231 review P2).
PACKAGED_MIGRATIONS = Path(__file__).resolve().parent / "record_migrations"

#: The record's own tables (the procedural layer is views over these).
THE_RECORD_TABLES: tuple[str, ...] = (
    "node_identity",
    "journal_event",
    "source",
    "event",
    "session",
    "object",
    "subject",
    "point",
    "relation",
    # NOT a node table: the §9.1 narrative home (#9555). It is here because
    # `table_columns()` parses `CREATE TABLE` only for the tables this tuple
    # names, and the narrative columns must be readable as COLUMNS (PRIMARY,
    # §3) rather than pushed into a props tail the schema does not keep.
    "session_narrative",
)

#: The per-layer node tables, and the canonical key column each one pins
#: `node_key` to (W1/Interface E: `(label, key)`, never a bare id).
NODE_TABLES: dict[str, tuple[str, str]] = {
    # table: (node_label, key column)
    "source": ("Source", "url"),
    "event": ("Event", "event_id"),
    "session": ("Session", "id"),
    # #9881 — `Object`/`Subject` key on the STABLE `id`, not on `name`. Under
    # #3590 the name is a MUTABLE natural key that two live entities may share,
    # so a name-keyed row cannot distinguish two incarnations, and a rename
    # either duplicates a row or orphans the one it replaced. With `id` as the
    # key a rename is an ordinary column patch (`name = $new`): the key never
    # moves, so nothing has to cascade through `node_identity` and `relation`.
    "object": ("Object", "id"),
    "subject": ("Subject", "id"),
    "point": ("Point", "id"),
}

#: node label → the record table that materializes it (the inverse of
#: :data:`NODE_TABLES`). An edge endpoint names its label, and the relation's
#: `(label, key)` FK is into `node_identity`, so the endpoint's key must be the
#: label's CANONICAL key (`Object`/`Subject`→`id` since #9881, `Source`→`url`,
#: `Point`/`Session`→`id`, `Event`→`event_id`), resolved from the projection's
#: own node row — never the raw journal id.
_LABEL_TABLES: dict[str, str] = {
    label: table for table, (label, _key_column) in NODE_TABLES.items()
}

#: The columns an `EntityMutated` record's identity (`payload["id"]`) is matched
#: against, per node table. The graph fold matches the label's OR-set identity
#: (`_ENTITY_ID_PROP` + `_CANONICAL_ENTITY_SECONDARY_ID_PROPS`), and the record
#: matches the identity it STORES.
#:
#: ⚠️ `object`/`subject` still match on `id` alone, and the #9881 key move did
#: not change that — it made the match and the KEY agree. Before the move the
#: resolve found the row by `id` and then wrote the row's `node_key` (which was
#: its NAME) into the edge: `relation.src_key`/`tgt_key` are an FK into
#: `node_identity`, so the endpoint is filed by whatever key that label holds.
#: The key is the `id` now, so an `aboutObject`/`ownedBy` endpoint carrying the
#: journal id lands the id — and the two rebuild routes agree instead of one
#: filing by name and the other resolving by id.
#:
#: `source` keeps both: a Source's key is `url` while its record identity is
#: usually `id` (`add_document` emits `id` and no `url`).
#:
#: ⛔ NOT EVERY EDGE ENDPOINT IS ADDRESSED THIS WAY, and #9972 is what happens
#: when one silently is. A STRUCTURAL `DirectEdgeRepoint` descriptor (pass-2b)
#: names its target by the node's **replay key** — `stub_key`
#: (`projection/edges.py`) emits a `name` for
#: `aboutSubject`/`aboutObject`/`aboutEvent`/`aboutPoint` and a `url` for
#: `aboutDocument`/`extractedFrom` — because the GRAPH resolves it that way
#: (`resolve_structural_target`). Resolving it through THIS table matched
#: `object.id = 'Acme Corp'`, which is a successful 0-row statement: the
#: successor's edge was never written, in silence. That family has its own
#: resolver (`_structural_target_resolution`) instead.
#:
#: ⛔ AND THE TEMPTING FIX IS WRONG: widening `object`/`subject` to
#: `("id", "name")` here would also loosen `EntityMutated` and `EntityLinked`,
#: whose payloads ARE ids — an unranked `id = %s OR name = %s` is a UNION with no
#: precedence, so it can pick a different row than the fold's `MATCH {id:$id}`
#: (and fan an edge out over two carriers). `_endpoint_resolution`'s `prefer=`
#: lever exists for that ordering problem; this table is not where it belongs.
_ENTITY_MATCH_COLUMNS: dict[str, tuple[str, ...]] = {
    "point": ("node_key",),
    "session": ("node_key",),
    "event": ("node_key",),
    "object": ("id",),
    "subject": ("id",),
    "source": ("id", "node_key"),
}

#: ``_emit_event`` owns these envelope keys; they are never node properties.
ENVELOPE_KEYS: frozenset[str] = frozenset({
    "type", "ts", "event_id", "initiated_by", "projection_version", "seq",
    # ⚠️ `agent_id` is DELIBERATELY NOT here, though the record must not keep it
    # as a NODE prop: this set is not only a props set — `_journal_row` filters
    # the DURABLE `public.journal_event.payload` through it, while the JSONL
    # journal keeps `agent_id` (`sdk._journal_record_payload` subtracts
    # `_JOURNAL_ENVELOPE_KEYS`, which does not name it). Adding it here truncated
    # the record's own event stream on every API-door record — a NEW divergence
    # between the two journals, introduced by the fix whose premise is that they
    # must not diverge (#9078 review). The props strip is `_RECORD_ONLY_KEYS`'
    # job, and `test_agent_id_is_dropped_like_the_fold_drops_it` is the leg that
    # keeps it.
})

#: The ONE record type whose whole job is to carry §9.1's narrative into the
#: record (#9555 / R7). Declared ONCE here and imported by its producer
#: (`hosted_api._narrative_carrier`), so the producer and the registry cannot
#: spell the type differently — the class of defect that made
#: `create_document(type="SourceCreated")` journal nothing at all.
NARRATIVE_RECORD_TYPE = "SessionNarrated"

#: The ONE record type whose whole job is to carry a capture's raw TURN SET into
#: the record (#10017, step 3 of the ruling on that issue). Declared ONCE here and
#: — from step 7, which turns the producer over — imported by it
#: (`sdk._write_capture_turns`), for the same reason as
#: :data:`NARRATIVE_RECORD_TYPE`: a producer and a registry that spell one type
#: two ways is the class of defect that made `create_document(type="SourceCreated")`
#: journal nothing at all.
#:
#: ⛔ NOTHING EMITS IT YET, and that is the change this declaration belongs to:
#: declaring the carrier is INERT until step 7 emits it, so the capture writer's
#: per-turn `PointAdded` records are untouched here.
#:
#: ⛔ AND STEP 7 INHERITS A THIRD STORE, not just the two this type names. The
#: per-turn `PointAdded` records it replaces are ALSO the `:GraphEvent` /
#: claim-transition stream — `sdk._GRAPH_EVENT_TYPES`, `shared_state.events
#: .CLAIM_EVENT_TYPES`, and `docs/event-catalog.md`'s `PointAdded` row ("one per
#: `{session_id}_t{i}` turn Point") — which is what `poll_events` serves. A
#: "move" that simply stops emitting them silently removes one subscriber row per
#: turn, and NOTHING gates that: the catalog is prose and no test pins it. So
#: step 7 must EITHER keep a store-only per-turn emission (the two-store split
#: this same writer already performs for the stale-turn sweep: `PointRetracted`
#: store-only + `EntityMutated` for JSONL) OR record the decision to drop the
#: turn rows from `poll_events`. `TurnsRecorded` must NOT be added to
#: `_GRAPH_EVENT_TYPES` to compensate — that set's shape is a recorded decision.
TURNS_RECORD_TYPE = "TurnsRecorded"

#: ⛔ THE PAYLOAD IS PINNED TO THE WRITER'S, so step 7 is a MOVE and not a
#: reshape. Each entry of the carrier's ``turns`` list is one of the
#: ``_turn_snapshot`` dicts `sdk._write_capture_turns` builds today, verbatim —
#: these keys ALWAYS, plus at most one branch of the optional set below, MINUS
#: (never partially) the ``TURNS_SNAPSHOT_UNATTESTABLE_KEYS`` when the writer could
#: not attest them. The
#: ``contains_session`` link rides the EVENT ENVELOPE rather than the per-turn
#: dicts, exactly as it does for `PointAdded` (#3947: it is a capture-write
#: STRUCTURAL fact, restored by the projection's edge fold, and never a node
#: property the live write does not set).
#:
#: `tests/test_10017_record_carrier.py` reads the snapshots a REAL capture emits
#: — one with no vector, one that ENCODED one, one that PRESERVED one — and
#: compares them to these sets, so the pin is against the producer rather than
#: against this comment.
TURNS_SNAPSHOT_KEYS: frozenset[str] = frozenset({
    "id", "content", "pointKind", "speaker", "is_episodic", "status",
    "createdAt", "embedding",
})
#: ``embedding_preserved`` rides a snapshot ONLY when the live write PRESERVED a
#: prior vector instead of encoding one (#5004 round 3). The three IDENTITY
#: stamps are the other branch: `embeddings.stamp_journal_embedding(payload,
#: creating=True)` adds `embedding_model` / `embedding_revision` /
#: `embedding_text_hash` to the journalled point when the write DID encode one,
#: and deliberately withholds them from a preserved vector — that vector may come
#: from a DIFFERENT model, so attesting the active one would record a false
#: origin, the failure #5004/R1 exists to prevent. So these keys are optional one
#: branch at a time, NOT absent in practice. ⛔ Step 7 must carry them VERBATIM: a
#: carrier that kept `embedding` but dropped the identity would replay a vector
#: with no origin, which is the silent model change the stamps expose.
TURNS_SNAPSHOT_OPTIONAL_KEYS: frozenset[str] = frozenset({
    "embedding_preserved",
    "embedding_model", "embedding_revision", "embedding_text_hash",
})
#: The keys a snapshot MAY OMIT — TOGETHER — when the writer could not attest the
#: stored value (#10021 review P3). ``sdk._write_capture_turns`` journals
#: ``status``/``createdAt`` as what the write LEAVES, and both folds apply them
#: ABSOLUTELY, so a capture whose prior-row read failed must OMIT both keys
#: (``_UNATTESTABLE``) rather than assert its own ``now``/``draft`` — which would
#: re-stamp ``created_at`` and DEMOTE a promoted turn on the store of record.
#:
#: ⛔ WHAT THE OMISSION BUYS IS THE **UPDATE** ARM, NOT BOTH (#10021 review P3).
#: ``_upsert_statement`` builds the ``INSERT ... (columns)`` list and the
#: ``DO UPDATE SET`` list from the SAME ``col_values`` keys (its ``columns =``
#: and ``updates =`` lines), so
#: an omitted key is absent from both: a row that ALREADY exists keeps its values
#: (the half the writer needs), and a row that does NOT exist takes the column's
#: declared DEFAULT — or NULL, since ``public.point.status`` / ``created_at`` are
#: nullable ``text`` with no default (PostgreSQL: "each column not present in the
#: explicit or implicit column list will be filled with a default value, either
#: its declared default value or null if there is none"). The fresh-row NULL is a
#: RECORDED RESIDUAL, stated at its producer (`sdk._write_capture_turns`) and
#: pinned by `tests/test_10000_turns_record_only.py::
#: test_the_omitted_pair_is_safe_on_the_update_arm_only`. ⚠️ A `coalesce(EXCLUDED.
#: c, point.c)` on the conflict arm would NOT close it — on a fresh insert
#: ``EXCLUDED.c`` IS that NULL; only a column DEFAULT would.
#:
#: ⛔ THIS SET IS WHY THE ALWAYS-SET ABOVE CAN KEEP ITS PROMISE. Declaring
#: ``status``/``createdAt`` unconditional while an arm omits them left the pin
#: that reads a REAL capture (``tests/test_10017_record_carrier.py``) passing only
#: because its leg never reached that arm — a capture that did would red it with a
#: message naming the wrong cause. The variance is now declared here instead of
#: living where only the writer knows it.
TURNS_SNAPSHOT_UNATTESTABLE_KEYS: frozenset[str] = frozenset({
    "status", "createdAt",
})

#: The journal's record-type vocabulary. Sourced from the graph fold's own
#: dispatch (`tortoise/projection/`) and the producers' emit sites, and pinned
#: by `tests/test_record_projection.py` against both. A type that appears in a
#: producer but not here is exactly the silent-skip defect totality forbids.
JOURNAL_EVENT_TYPES: frozenset[str] = frozenset({
    "PointAdded",
    "OperatorAdded",
    "PointPromoted",
    "OperatorPromoted",
    "PointRevised",
    "PointRetracted",
    "PointSuperseded",
    "PointInvalidated",
    "PointsMerged",
    "OperatorAnnotated",
    "ConfidenceChanged",
    "EdgeMessageChanged",
    "EntityMutated",
    "ObjectRegistered",
    "ObjectSuperseded",
    "SubjectAdded",
    "EventRecorded",
    "SourceCreated",
    "SourceVersioned",
    "DocumentCreated",
    "SessionRecorded",
    NARRATIVE_RECORD_TYPE,
    TURNS_RECORD_TYPE,
    "SessionDeleted",
    "EntityLinked",
    "DirectEdgeCreated",
    "DirectEdgeRepoint",
    "BatchIdStamped",
    "DedupeRecorded",
    "DedupeRejected",
    "EntityBindingRefused",
    "IngestStarted",
    "CalibrationRecorded",
})


class RecordProjectionError(Exception):
    """Base class — a record projection failure is loud, never a warning."""


class UnknownJournalRecordType(RecordProjectionError):
    """Interface A's *total* clause: an event with no handler is a FAILURE.

    Raised for a record type this projection does not know at all. `#5285`
    asserts `rebuild == live` for the graph; the record's half is that a
    replay which cannot account for every record it consumed cannot claim to
    be the record.
    """


class RefusedJournalOperation(RecordProjectionError):
    """A record this projection knows but will not silently materialize.

    Distinct from :class:`UnknownJournalRecordType` on purpose: a **declared**
    refusal (e.g. an `EntityMutated` op no writer produces yet) is a different
    fact from an **undeclared** type, and collapsing the two would hide which
    one a new producer tripped.
    """


# ════════════════════════════════════════════════════════════════════════════
# The mutation vocabulary. One small algebra, declared per record type.
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Node:
    """A node table, its canonical label, and the column holding the node key."""

    table: str
    label: str
    key_column: str


@dataclass(frozen=True)
class NodeSnapshot:
    """Materialize (upsert) a node row from a property dict.

    ``path`` names the sub-dict that carries the node's properties (`"point"`
    for the snapshot-carrying record types); ``None`` means the record's own
    flat payload *is* the property dict. ``key_from`` names the payload field
    that holds the canonical key when it is not itself inside the snapshot.
    """

    node: Node
    path: str | None
    key_from: str
    drop: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NodePatch:
    """Apply absolute values to an EXISTING row (never creates one).

    A patch on a row the record does not hold is a 0-row no-op — the same
    verdict the graph fold reaches for a revision of an unknown id, and the
    reason a patch must not be an upsert: registering a node from a *mutation*
    record would materialize a row the journal never created.
    """

    node: Node
    key_from: str
    literals: tuple[tuple[str, Any], ...] = ()
    #: Property names taken verbatim from the flat payload. ``None`` means
    #: "every flat payload key that is not record-only" (the `PointRevised`
    #: extras / the `annotator_*` dims); an empty set means "none".
    from_payload: frozenset[str] | None = None
    #: ``(column, payload key)`` renames when the journal's key differs.
    renames: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NodeDelete:
    """Remove an existing row (a re-delete is a 0-row no-op).

    ``companion_deletes`` names rows the same record removes beside the node
    itself — the record's half of the graph's ``DETACH DELETE``. The fold's
    ``SessionDeleted`` removes the session's agentSession ``:Source`` stub
    (``url = session:{id}``) in the SAME step, so the record must too or the
    note claiming it would be describing behaviour the code does not
    implement. The template's ``{key}`` is the deleted node's key.
    """

    node: Node
    key_from: str
    companion_deletes: tuple[tuple[Node, str], ...] = ()


@dataclass(frozen=True)
class NodesDelete:
    """Remove ONE row per key in a LIST payload field (a merge's losers).

    Why a second delete shape rather than a wider :class:`NodeDelete`: the
    field a merge carries is a LIST of ids (``merge_ids``), not one key, and
    overloading ``key_from`` to mean "sometimes one key, sometimes a list"
    would make the two shapes indistinguishable at the declaration. One effect,
    one shape — the ``#5285`` totality rule applies to the vocabulary too.

    ``match`` is the identity the graph fold deletes by, and it is stated per
    declaration for the same reason :data:`_ENTITY_MATCH_COLUMNS` exists: the
    record's ``node_key`` column IS the journal id for Point/Session/Event, but
    Object/Subject key on their ``id`` (since #9881) and Source on ``url``, so a
    ``node_key`` predicate against a journal id silently touches 0 rows.
    """

    node: Node
    keys_from: str
    match: tuple[str, ...] = ("node_key",)


@dataclass(frozen=True)
class RelationUpsert:
    """One row per edge, per `ONTOLOGY.md` §8's reification rule."""

    rel_type_from: str = "edge_type"
    src_from: str = "src"
    tgt_from: str = "tgt"
    attrs: tuple[str, ...] = ("direction", "confidence", "weight", "label",
                              "batch_id", "source_version")


@dataclass(frozen=True)
class RelationDelete:
    """Remove a relation row (the `delete_only` leg of a repoint)."""

    rel_type_from: str = "edge_type"
    src_from: str = "src"
    tgt_from: str = "tgt"


@dataclass(frozen=True)
class NoRecordEffect:
    """A record type that legitimately produces NO record row.

    ``reason`` is mandatory and is the whole point: a declared no-op is a
    decision a reviewer can check, while an undeclared one is the silent skip
    Interface A forbids. Every entry here must be justified by the *record's*
    semantics — not by "we did not get to it".
    """

    reason: str


@dataclass(frozen=True)
class OpDispatch:
    """`EntityMutated` — one record type with an ``op`` discriminator.

    `#3299` chose one record type over one-per-label×operation; the replay
    dispatches on ``op``. Ops not produced by any writer are declared and
    REFUSED rather than guessed (`#3377`/`#4769`) — and since `#9881` that set is
    **empty**: the one member it had, `rename`, was refused on the premise that
    a rename moves the canonical key, and the key moved to the stable `id`
    instead (`NODE_TABLES`), which makes the rename a patch. `refused_ops` stays
    as a field because the declaration is still a decision: an op some future
    writer produces and the fold cannot apply belongs here, with its reason.

    ``never_store`` is the record's own half of the §2.6 split: `Object.status`
    and `Subject.status` are **projection-owned caches** (the fold's docstring),
    and BOTH are refused by the schema's props CHECK (`Object` from #8179,
    `Subject` from the forward migration 20261014000001/#8263) — so the
    projection must not make either authoritative through the props tail. This
    set is the mutation path's second line; the snapshot path is covered by
    `refused_props_names()`, which reads the same CHECKs from disk.
    `Point.status` is NOT in this set: a Point's lifecycle IS journaled
    (`PointRetracted`/`…Superseded`) and `point.status` is a PRIMARY column.

    ⚠️ ``never_store`` is the REFUSAL half of the split and says nothing about
    who COMPUTES the value — #9974 measured the gap that left: the record stored
    nothing, the cache derived nothing, and a record-sourced rebuild
    materialized a superseded entity with NO `status`, which
    `live._terminal_excluded` reads as LIVE. The computing half is
    `cache_projection.DERIVED_FROM_RECORD` (from this module's own PRIMARY
    carriers, `superseded_by` / `superseded_at`, the same OR-predicate the
    record's `public.*_current_status` views use); its docstring names the
    statuses the record carries NO signal for at all (an explicit `restatus`,
    whose only carrier is `public.journal_event.payload`) — those stay an
    ownership decision, not a silent success.
    """

    table_for_label: Mapping[str, str]
    patch_ops: frozenset[str]
    delete_ops: frozenset[str]
    refused_ops: Mapping[str, str]
    never_store: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Mutation:
    """The declared mutation for one record type (a documented union)."""

    effect: (
        NodeSnapshot | NodePatch | NodeDelete | NodesDelete
        | RelationUpsert | RelationDelete | OpDispatch | NoRecordEffect
    )
    note: str = ""


#: **The registry.** Every member of :data:`JOURNAL_EVENT_TYPES` has an entry;
#: `tests/test_record_projection.py` asserts that in both directions, so a new
#: producer fails this suite rather than disappearing at replay time.
RECORD_MUTATIONS: dict[str, Mutation] = {
    # ── Epistemic layer: Points and operator Points ──────────────────────────
    # The `…Added` / `…Promoted` / dedup record types all carry a FULL point
    # snapshot (`payload["point"]`), which is what the graph fold indexes too
    # (`_apply_one`'s `points[p["id"]] = p`), so the record projects the same
    # dict the fold does. `id` rides the envelope OR the snapshot, and this
    # module reads both carriers (`_key_value`'s `fallback`): the capture writer
    # emits `PointAdded` with the id ONLY in `payload["point"]` (#9078, measured
    # on the live door).
    "PointAdded": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
        note="full snapshot under `point`; identity keys are record-only and "
             "are dropped by `drop`",
    ),
    "OperatorAdded": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "PointPromoted": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "OperatorPromoted": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "DedupeRecorded": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
        note="#784 — the payload is the point snapshot the audit inspected",
    ),
    "DedupeRejected": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "EntityBindingRefused": Mutation(
        # AUDIT-ONLY — no record effect, and that is a DECISION, not an omission.
        # The graph folds this type NOWHERE (`projection/__init__.py::
        # _NO_PROJECTION_FOLD`, #1370: "a refused/suspected subject binding is an
        # AUDIT record — the edge it declined to write must NOT be replayed from
        # it"), so a Point mutation here materialized a row no writer ever
        # created: an ungated upsert on a fresh graph produces a stub point whose
        # props are the refusal's forensics (#9078 review, measured — 3 such rows
        # in one capture). The producer is FLAT anyway
        # (`subject_binding._journal_refusal`: `id=point_id, point_id=..., role=...`),
        # so the previous `path="point"` declaration refused every real write
        # while the fixture's invented carrier kept the suite green.
        NoRecordEffect(
            "#1370 — an audit record: the graph folds it nowhere, so the record "
            "must not invent the point it declined to bind. The event stream "
            "keeps the refusal (journal_event), and the point's own write "
            "records carry its state."),
    ),
    # #902/A10: batch_id (+ content_hash / mitigates_operator_id /
    # mitigation_strength) restore, replayed from the batch snapshot.
    "BatchIdStamped": Mutation(
        # FLAT: `sdk._emit_event(_BATCH_ID_RECORD_TYPE, id=point_id, **extra)`
        # carries the batch props on the envelope, never under a `point`. A
        # schema-refused prop (`content_hash`) is DROPPED by `_split_properties`,
        # not raised — see its docstring. A PATCH, never a snapshot: the graph
        # folds this type NOWHERE (`projection/__init__.py::_NO_PROJECTION_FOLD`
        # — #902/A10 restores it in pass-2b with `MATCH (p:Point {id:$pid}) SET
        # p.batch_id = $bid`, a MATCH that cannot create), so an upsert here
        # invented the row. It is the same defect as `EntityBindingRefused`
        # above, and it is reachable: #9187 replays SEGMENTS (`first_seq=41`), so
        # a stamp whose `PointAdded` sits outside the segment is the ordinary
        # case, not a corner. A patch stores the §17.5-PRIMARY `batch_id` and is
        # a 0-row no-op when the point is genuinely absent — the fold's verdict.
        #
        # ⚠️ An explicit allow-list, NOT `from_payload=None`. The producer emits a
        # WIDER flat payload (`sdk._emit_batch_id_record`: `content_hash`,
        # `mitigates_operator_id`, `mitigation_strength`), and pass-2b restores the
        # stamp with `SET p.batch_id = $bid` and nothing else — so
        # `mitigates_operator_id` would land in `point.props` as a key no journal
        # replay ever writes, and a record-sourced rebuild copies the tail verbatim
        # (`cache_projection._props_from_row`). Same defect class as `agent_id`
        # above (#9200 review, P2). `content_hash` is schema-refused and
        # `mitigates_operator_id` is folded nowhere, so neither may pass;
        # `mitigation_strength` is a real `public.point` column.
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  from_payload=frozenset({"batch_id", "mitigation_strength"})),
    ),
    # ⚠️ `new_content` is the journal's own name for the revised `content`; it
    # is NOT a node property of that name, so it is renamed rather than written
    # into `props`. The fold writes `p["content"] = new_content`.
    "PointRevised": Mutation(
        NodePatch(
            Node("point", "Point", "id"),
            key_from="id",
            renames=(("content", "new_content"),),
        ),
        note="extras ride the flat payload verbatim (an `annotator_*` key here "
             "is a node property of that exact name, never aliased)",
    ),
    "OperatorAnnotated": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id"),
        note="#3689 — the JSONL line carries the canonical `annotator_*` names; "
             "the `:GraphEvent` payload's SHORT aliases never reach this door",
    ),
    "PointRetracted": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  literals=(("status", "retracted"),),
                  from_payload=frozenset({"valid_to"})),
        note="tombstone — the node is KEPT (the hard delete is EntityMutated). "
             "#4282: `valid_to` rides the payload when the retraction named a "
             "WINDOW END (the `valid_until`/`invalid_at` field on "
             "`update_knowledge`, beta row 22) — the caller's instant is the "
             "datum, so the record carries it and the fold replays it verbatim "
             "instead of a rebuild losing it; the DECLARATION half is pinned by "
             "`tests/test_4282_phase2_merges.py`, and the PARITY half (the "
             "producer really puts the key on the record, the rebuild really "
             "restores it, and the durability gate stays clean) by "
             "`tests/test_4282_dated_retraction_durability.py` — a declared "
             "`from_payload` on a record that never carries the key is exactly "
             "the vacuous shape that file exists to prevent",
    ),
    "PointSuperseded": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  literals=(("status", "superseded"), ("outdated", True)),
                  # ⚠️ NOT `valid_from`: the producer emits the SUCCESSOR's window
                  # start under that name (#4021) and the fold writes it
                  # verbatim as the PREDECESSOR's `validTo` (`valid_to`), never
                  # as the predecessor's own `validFrom`. The old point's
                  # `validFrom` already rides `PointAdded`; writing the
                  # successor's start here would invert/rewrite the window.
                  from_payload=frozenset({"valid_to", "expired_at",
                                          "valid_to_precision"}),
                  renames=(("superseded_by", "new_id"),)),
        note="§17.5 names this record as the carrier of `validTo`/`expiredAt`/"
             "`outdated`; the fold replays them verbatim "
             "(`SET n.status='superseded', n.outdated=true, n.validTo=$vt, "
             "n.expiredAt=$ea`) — and does NOT touch `validFrom`, which is why "
             "the payload's `valid_from` (the successor's start) is not stored",
    ),
    "PointInvalidated": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  literals=(("outdated", True),),
                  from_payload=frozenset({"valid_to", "expired_at",
                                          "valid_to_precision"}),
                  renames=(("superseded_by", "corrected_by"),)),
        note="#2488 — `outdated` flag + CORRECTS; no status change. §17.5's "
             "carrier for `validTo`/`expiredAt` — the fold replays both "
             "verbatim, so the record's columns carry them too",
    ),
    # ⚠️ THE EP-SCALAR CARVE-OUT, in its executable form: `confidence` is
    # DERIVED (§2.0/§17.5 — recomputed from the structure, "never stored as
    # authority"), and the record has no column for it. The record's mutation
    # for this record type is therefore, correctly, *none* — and it is declared
    # rather than dropped, so the day a writer starts using it for something
    # else the decision is visible.
    "ConfidenceChanged": Mutation(
        NoRecordEffect(
            "EP carve-out (STORAGE-ARCHITECTURE §2.0/§17.5): confidence is "
            "recomputed from the structure and is never stored as authority. "
            "`public.point` has no `confidence` column and its props CHECK "
            "refuses the name, so the record's correct mutation is none. The "
            "`outdated=true` terminal leg rides PointInvalidated's flag, and "
            "`_graph_id`/`_nid`/`reason` are replay bookkeeping (DERIVED)."
        ),
    ),
    "EdgeMessageChanged": Mutation(
        NoRecordEffect(
            "EP carve-out, the EDGE half of `ConfidenceChanged`'s (STORAGE-"
            "ARCHITECTURE §2.0/§17.5): `msg_alpha`/`msg_beta`/`back_msg_*` are "
            "EP runtime message parameters, recomputed by the next "
            "propagation from the structure and never stored as authority — "
            "`public.relation` has no such column and no props tail entry for "
            "them, so the record's correct mutation is none. The record is "
            "GRAPH-REPLAY state only: it makes a FalkorDB rebuild faithful "
            "(#5380, the edge half of #2884), and `derived = replay(journal)` "
            "holds without the RECORD becoming their owner."
        ),
    ),
    "PointsMerged": Mutation(
        # #9892 — the refusal is GONE, and it was refused for the wrong reason.
        #
        # The merge this tree implements is "delete the merged-away Points": the
        # graph fold's WHOLE mutation for the type is `_EntityHandlers._delete(mid)`
        # (`MATCH (n:Point {id:$id}) DETACH DELETE n`) once per `merge_ids` entry
        # (`projection/entities.py`, reached from `apply()`'s arm, `rebuild_all`'s
        # pass-1b and `_apply_one`). `keep_id` is READ NOWHERE in `tortoise/`
        # outside this module — the survivor is not patched, and no unit re-points
        # an edge; `DETACH` drops the loser's incident relations instead.
        #
        # So this pair — the identity parent, then the node row — IS the faithful
        # mutation, and it is the same pair `EntityMutated op=delete` emits. The
        # issue's option 2 (derive a re-point from `keep_id`/`merge_ids` + the
        # `relation` rows) is REJECTED: it would make the record richer than the
        # graph fold, so `rebuild_from_record` would mint edges a journal replay
        # deletes and the two rebuild routes would disagree.
        #
        # The refusal also cost more than a poor record: the plan never
        # materialized, so the merge got no `journal_event` POSITION row either and
        # a later replay could not see it was consumed.
        NodesDelete(Node("point", "Point", "id"), keys_from="merge_ids"),
        note="#9892 — one hard delete per `merge_ids` entry, the graph fold's "
             "own mutation (`DETACH DELETE`); the cascade removes the loser's "
             "incident relations, and `keep_id` is not written because no "
             "unit on the graph side reads it",
    ),
    "EntityMutated": Mutation(
        OpDispatch(
            table_for_label={
                "Point": "point",
                "Object": "object",
                "Subject": "subject",
                "Event": "event",
                "Source": "source",
                "Session": "session",
            },
            patch_ops=frozenset({"revise", "restatus", "supersede", "rename"}),
            delete_ops=frozenset({"delete"}),
            # #9881 — EMPTY, deliberately, and it was emptied by a decision, not
            # by drift. `rename` was the only declared refusal here (#3377/#4769:
            # *"a rename MOVES the canonical key (Object/Subject key = `name`), so
            # it is a key migration, not a patch"*). That premise is now false: the
            # key is the stable `id` (see `NODE_TABLES`), so a rename is the one
            # thing the record CAN express as a patch — `UPDATE … SET name=$new
            # WHERE id=$id`, key untouched, nothing to cascade through
            # `node_identity` or `relation`. The op is listed in `patch_ops` above
            # with the other state ops; the graph fold has implemented this arm all
            # along (`_ENTITY_MUTATION_STATE_OPS`), which is why the record's
            # refusal was the only thing between the producer (#3590's write
            # surface) and its own journal.
            refused_ops={},
            never_store={
                "object": frozenset({"status"}),
                "subject": frozenset({"status"}),
            },
        ),
        note="#3299/#3300/#3311/#3312/#1423/#9881 — one record type, dispatched "
             "on `op`; `state` carries absolute values the graph STORED, and each "
             "state op is a patch against the node's stable KEY (an `op=rename` "
             "writes `state.name` into the `name` COLUMN and leaves `node_key` "
             "alone — the key is the `id`)",
    ),
    # ── Semantic layer ───────────────────────────────────────────────────────
    "ObjectRegistered": Mutation(
        # #9881 — keyed on the id, not the name: under #3590 `name` is mutable and
        # two live entities may share it, so it cannot be a key. A payload with no
        # `id` is REFUSED here (`_key_value`) rather than materializing an id-less
        # row the schema would then reject mid-transaction — the id-less legacy
        # journal line is #3590's open decision D2, and the record cannot represent
        # it, so it says so at the door instead of dropping it.
        NodeSnapshot(Node("object", "Object", "id"), path=None, key_from="id"),
    ),
    "ObjectSuperseded": Mutation(
        NodePatch(
            Node("object", "Object", "id"),
            key_from="id",
            from_payload=frozenset(),
            renames=(("superseded_by", "supersedes_by"),
                     ("superseded_at", "ts")),
        ),
        note="#1350 — `supersededBy` is journal-carried verbatim (PRIMARY); "
             "`Object.status` is NOT written (projection-owned cache, §2.6) "
             "and `supersededAt` comes from the record's own envelope `ts`; "
             "#9881 — keyed by the node's `id`, and there is NO `name` "
             "fallback: a line carrying no `id` is REFUSED at the door by "
             "`_key_value`. That refusal is what keeps the failure LOUD, and "
             "the reason is measured rather than rhetorical: a name-matched "
             "patch would match ZERO rows once `node_key` is the id — SILENCE, "
             "not an error — and the schema's pin cannot catch it either: this "
             "mutation emits ONE `UPDATE` and never rewrites "
             "`name`/`id`/`node_key`, so `chk_object_node_key_is_contract_key` "
             "is re-evaluated on an unchanged key triple and can never fire",
    ),
    "SubjectAdded": Mutation(
        NodeSnapshot(Node("subject", "Subject", "id"), path=None, key_from="id"),
    ),
    # ── The event stream, materialized (the episodic layer) ──────────────────
    "EventRecorded": Mutation(
        NodeSnapshot(Node("event", "Event", "event_id"), path=None,
                     key_from="eventId"),
        note="the key column is `event_id`; the Event is named by the payload's "
             "`eventId`/`id` (the fold's MERGE key), NEVER the `_emit_event` "
             "envelope `event_id` — that is a per-record ULID, record-only",
    ),
    "SourceCreated": Mutation(
        NodeSnapshot(Node("source", "Source", "url"), path=None, key_from="url"),
    ),
    "DocumentCreated": Mutation(
        NodeSnapshot(Node("source", "Source", "url"), path=None, key_from="url",
                     drop=frozenset({"content", "doc_status", "docStatus",
                                     "objectKind", "object_kind",
                                     # `projection/entities.py`: these "ride the
                                     # DocumentCreated event and must never
                                     # persist as node props".
                                     "source_url", "suppress_embedding"})),
        note="§9.3/§9.5 Q3 — a document is a `:Source`; the retired "
             "`content`/`doc_status`/`objectKind` props have no column AND are "
             "scrubbed by the graph fold (`_upsert_document`), so storing them "
             "would leave the record holding state the node removed",
    ),
    "SourceVersioned": Mutation(
        NodeSnapshot(Node("source", "Source", "url"), path=None, key_from="url",
                     drop=frozenset({"previousContentHash", "previousValidTo"})),
        note="#5024/T6 — the prior version's hash and window are RECORD-ONLY "
             "keys (`_SOURCE_HANDLED` keeps them off the node; the single "
             "`:Source` row holds only the current version)",
    ),
    "SessionRecorded": Mutation(
        NodeSnapshot(Node("session", "Session", "id"), path=None, key_from="id"),
        note="four records per capture, merged onto one row — see the "
             "first-writer-wins / last-writer-wins split below. #9599: a BLANK "
             "session id is NOT refused here, deliberately — the RECORD keeps "
             "what the client sent (it is the faithful carrier, and a Postgres "
             "key is addressable), while the GRAPH fold refuses it because the "
             "engine cannot serve a blank indexed key back to an equality "
             "seek. #9599's own body names this asymmetry as the expected "
             "state ('the journal fold then minted a duplicate :Session … "
             "while the Postgres record projection (no such quirk) held the "
             "row'). Pinned by tests/test_9599_blank_session_id.py — do not "
             "'fix' one side to match the other without deciding which "
             "projection is authoritative.",
    ),
    # ── §9.1's narrative carrier (#9555, R7) ─────────────────────────────────
    NARRATIVE_RECORD_TYPE: Mutation(
        NoRecordEffect(
            "#9555/R7 — the §9.1 narrative's own carrier, emitted by the layer-1 "
            "commit path (`hosted_api._execute_commit_writes`) so the narrative "
            "and the raw turn text have a home in the record instead of only a "
            "field on whichever row happened to hold them. It materialises NO "
            "node on purpose: §9.1 places the narrative 'not in the graph, not a "
            "Document node, not a Point', because a narrative is a searchable "
            "string with no confidence and no topological weight. Its record "
            "effect is contributed by the SHAPE-KEYED secondary projection "
            "(`_narrative_statements`, which fires for any record naming its "
            "session and carrying `story_arc`/`capture_redactions`/`turn_count`), "
            "so the statement set for a CARRYING payload is not empty even "
            "though no declared node effect exists — pinned by "
            "tests/test_9555_narrative_carrier.py and by "
            "tests/test_record_projection.py's totality fixture."
        ),
    ),
    # ── §9.1's raw-TURN carrier (#10017) ─────────────────────────────────────
    TURNS_RECORD_TYPE: Mutation(
        NoRecordEffect(
            "#10017 — §9.1's raw-turn carrier: one capture's turn set on ONE "
            "record, where the capture writer emits one `PointAdded` per turn "
            "today. It materialises NO node, and that is the ruling rather than "
            "an omission: §9.1's D1 places the raw transcript 'not in the "
            "graph, not a `Document` node, not a `Point`' (owner, 2026-09-23: "
            "the same rule applies to raw turns). Its RECORD home is "
            "`public.point` — the table `record_read` already serves the "
            "transcript from — with `cache_tier = 'record_only'`; NOT a new "
            "table, and no secondary projection is declared here because "
            "nothing emits this type yet (step 7 does, in its own change). The "
            "tier cannot ride the event payload: `_RECORD_ONLY_KEYS` strips it "
            "before the insert, so step 7 must write it as its own column "
            "statement (#10070). Until then, FOR THIS TYPE, the two rebuild "
            "routes agree by BOTH having nothing to do — "
            "`projection/__init__.py::_NO_PROJECTION_FOLD` folds the type "
            "nowhere, and this entry projects no row. That agreement is about "
            "the CARRIER and says nothing about the per-turn `PointAdded` "
            "records a capture still emits, which BOTH routes still fold into "
            "turn nodes — so do not read it as §9.1 already holding; see "
            "that section's measured note in STORAGE-ARCHITECTURE.md. The "
            "payload is pinned to the writer's per-turn snapshot; see "
            "TURNS_SNAPSHOT_KEYS."
        ),
    ),
    "SessionDeleted": Mutation(
        NodeDelete(
            Node("session", "Session", "id"), key_from="id",
            companion_deletes=(
                (Node("source", "Source", "url"), "session:{key}"),
            ),
        ),
        note="#7951 — the hard-delete carrier; the session's agentSession "
             "`:Source` stub is a `Source` row keyed `session:{id}` and is "
             "removed with it (the fold's `remove_source=True`)",
    ),
    # ── Edges ────────────────────────────────────────────────────────────────
    "EntityLinked": Mutation(
        RelationUpsert(),
        note="#3664 — flat logical endpoints; `edge_type` and both labels are "
             "validated against a frozen vocabulary by the graph fold. The "
             "journal addressing id is the GRAPH id, while `relation`'s FK key "
             "is the label's canonical key, so the endpoint is resolved from "
             "the projection's own node row (an absent endpoint lands no "
             "edge — the fold's `absent` verdict)",
    ),
    "DirectEdgeCreated": Mutation(
        RelationUpsert(),
        note="A10/#1048 — §4.4's canonical flat descriptor, shared with "
             "DirectEdgeRepoint",
    ),
    "DirectEdgeRepoint": Mutation(
        RelationUpsert(),
        note="A10 pass-2b — same flat descriptor; a `delete_only` leg removes "
             "the old edge and is carried by the record's own key",
    ),
    # ── Bookkeeping: declared no-ops, with the reason each one is one ────────
    "IngestStarted": Mutation(
        NoRecordEffect(
            "audit-only run marker (`_NO_PROJECTION_FOLD`): no graph effect, "
            "and the journal position itself is already in "
            "`public.journal_event`, which is the record of what happened"
        ),
    ),
    "CalibrationRecorded": Mutation(
        NoRecordEffect(
            ":Meta milestone marker (audit) — `_NO_PROJECTION_FOLD`; the value "
            "it records is a calibration constant recomputed from the corpus, "
            "not a node property"
        ),
    ),
}

#: Asserted at import time so a registry edit cannot silently lose a type.
assert set(RECORD_MUTATIONS) == set(JOURNAL_EVENT_TYPES), (
    "RECORD_MUTATIONS and JOURNAL_EVENT_TYPES must agree in both directions"
)


# ════════════════════════════════════════════════════════════════════════════
# The schema is read from disk, never restated. (§3: "a field is PRIMARY iff
# the journal carries it" — the projection is where that becomes executable.)
# ════════════════════════════════════════════════════════════════════════════

_COLUMN_RE = re.compile(r'^\s*("[a-z_][a-z0-9_]*"|[a-z_][a-z0-9_]*)\s+'
                        r'(?:text|jsonb|integer|boolean|bigint|double precision|'
                        r'timestamptz)\b', re.M)
_DERIVED_CHECK_RE = re.compile(
    r"chk_[a-z_]+_props_no_derived\s+CHECK\s*\(\s*NOT\s*\(\s*props\s*\?\|"
    r"\s*ARRAY\[(.*?)\]\s*::text\[\]\s*\)\s*\)",
    re.S,
)
_TABLE_RE = re.compile(
    r"CREATE TABLE IF NOT EXISTS public\.([a-z_]+)\s*\((.*?)\n\);\s*\n", re.S
)
#: The SAME refusal carried by a FORWARD migration: `ALTER TABLE public.<t> …
#: ADD CONSTRAINT chk_<t>_props_no_derived CHECK (…)`. A landed migration is
#: never edited (append-only history), so a later refusal arrives this way —
#: and the schema read must see it, or the projection would emit a props tail
#: the constraint refuses (#8263). `[^;]*?` keeps the match inside one
#: statement (DROP CONSTRAINT …, ADD CONSTRAINT … is a single ALTER).
_ALTER_DERIVED_CHECK_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?public\.([a-z_]+)\b"
    r"[^;]*?ADD\s+CONSTRAINT\s+chk_[a-z_]+_props_no_derived\s+CHECK\s*\("
    r"\s*NOT\s*\(\s*props\s*\?\|"
    r"\s*ARRAY\[(.*?)\]\s*::text\[\]\s*\)\s*\)",
    re.S,
)
#: A FORWARD migration that ADDS a COLUMN: `ALTER TABLE public.<t> ADD COLUMN
#: [IF NOT EXISTS] <col> <type>, …;`. Append-only history means a landed
#: `CREATE TABLE` can only ever GAIN a column this way (#2782: the money value),
#: so a schema read that understands only `CREATE TABLE` reports the record
#: poorer than it is — `_split_properties` would push the value into the `props`
#: tail and `cache_projection` would drop it as unplaced, while §17.5's pin
#: reports the PRIMARY property as MISSING COLUMN.
_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?public\.([a-z_]+)\b([^;]*?);", re.S
)
_ADDED_COLUMN_RE = re.compile(
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([a-z_][a-z0-9_]*)\"?",
    re.I,
)


def _complete(path: Path) -> bool:
    """Does `path` hold every record migration this module parses?"""
    return all((path / name).exists() for name in RECORD_MIGRATIONS)


def _migrations_dir() -> Path:
    """The record's schema directory — the checkout first, the wheel second.

    The repo checkout is the source of truth (a migration edit must be read as
    edited), and the packaged copy is what an INSTALLED `tortoise` has: a wheel
    ships no `supabase/` tree, so without the second location `table_columns()`
    raised `RecordProjectionError` before a single statement and every armed
    live write failed — the record empty, the gate green (#9231 review P2).
    """
    repo = REPO_ROOT / "supabase" / "migrations"
    if _complete(repo):
        return repo
    if _complete(PACKAGED_MIGRATIONS):
        return PACKAGED_MIGRATIONS
    # Neither is complete: name the repo path so the failure reads as the
    # missing-schema case, and `table_columns()` reports BOTH locations.
    return repo


def _read_migrations(migrations_dir: Path | None = None) -> str:
    base = migrations_dir or _migrations_dir()
    return "\n".join(
        (base / name).read_text(encoding="utf-8")
        for name in RECORD_MIGRATIONS
        if (base / name).exists()
    )


def _parse_record_tables(sql: str) -> dict[str, frozenset[str]]:
    tables: dict[str, frozenset[str]] = {}
    for name, body in _TABLE_RE.findall(sql):
        if name not in THE_RECORD_TABLES:
            continue
        columns = {m.group(1).strip('"') for m in _COLUMN_RE.finditer(body)}
        # `props` is spelled `props jsonb NOT NULL DEFAULT …` — matched above.
        tables[name] = frozenset(columns)
    return tables


def _parse_added_columns(sql: str) -> dict[str, frozenset[str]]:
    """`{table: {column}}` from forward `ALTER TABLE … ADD COLUMN` migrations.

    The same forward-migration rule `refused_props_names()` follows for a
    re-declared refusal (#8263), applied to the column set: a landed migration
    is never edited, so a column added after the fact arrives as an ALTER and
    the schema read must see it (#2782's five money columns).
    """
    added: dict[str, set[str]] = {}
    for table, body in _ALTER_ADD_COLUMN_RE.findall(sql):
        if table not in THE_RECORD_TABLES:
            continue
        for column in _ADDED_COLUMN_RE.findall(body):
            added.setdefault(table, set()).add(column)
    return {table: frozenset(cols) for table, cols in added.items()}


def table_columns(migrations_dir: Path | None = None) -> dict[str, frozenset[str]]:
    """`{table: {column}}` for the record's tables, parsed from the migrations.

    Parsed rather than restated: a hand-copied column list is a second
    declaration of the schema, and the second declaration is the one that goes
    stale. `#7968`'s own pin does the same thing for the placement table.

    A `CREATE TABLE` is the base; a FORWARD `ALTER TABLE … ADD COLUMN` is
    unioned over it, because append-only history means a landed table can only
    gain a column that way (#2782, the same rule as `refused_props_names`).
    """
    sql = _read_migrations(migrations_dir)
    tables = _parse_record_tables(sql)
    for table, added in _parse_added_columns(sql).items():
        if table in tables:
            tables[table] = tables[table] | added
    missing = [t for t in THE_RECORD_TABLES if t not in tables]
    if missing:
        raise RecordProjectionError(
            "the record schema is not on disk — no CREATE TABLE found for "
            f"{missing}. The record projection targets `#7968`'s migrations, "
            f"read from {_migrations_dir()} (repo checkout) or from "
            f"{PACKAGED_MIGRATIONS} (installed package — a wheel ships the "
            "record's migrations there)."
        )
    return tables


#: `WHEN '<label>' THEN '<key property>'` — the pairs of `node_identity`'s
#: GENERATED CASE. Anchored on the column definition below rather than applied to
#: the whole SQL, because other record DDL carries CASE expressions of its own
#: (the procedural VIEWs) and an unanchored scan would read those as contract
#: declarations.
_KEY_PROPERTY_WHEN_RE = re.compile(r"WHEN\s+'(\w+)'\s+THEN\s+'([^']+)'")
_KEY_PROPERTY_ANCHOR_RE = re.compile(
    r"key_property\s+text\s+NOT NULL\s+GENERATED ALWAYS AS\s*\(")


def key_properties(migrations_dir: Path | None = None) -> dict[str, str]:
    """`{label: the property that is the label's canonical key}` — from the DDL.

    Read from `public.node_identity`'s generated CASE, never restated: a store
    that disagrees with the W1 contract is not supposed to be able to write the
    row at all, so the DDL is the contract's one home (`#7955`).

    ⚠️ **The LAST declaration wins.** Migrations are append-only history, so a
    key move arrives as a forward `ALTER TABLE … ADD COLUMN … GENERATED …`
    re-declaring the CASE (`#9881` moved `Object`/`Subject` from `name` to `id`),
    not as an edit of the `CREATE TABLE`. A reader that read only the original
    file would report the PRE-move contract while the live database enforced the
    post-move one — the record would key a node by a property its own `node_key`
    no longer holds. This is the same rule `refused_props_names()` applies to a
    forward refusal, applied to a declaration that can CHANGE rather than only
    grow, which is why it takes the last block instead of a union.
    """
    sql = _read_migrations(migrations_dir)
    blocks: list[str] = []
    for anchor in _KEY_PROPERTY_ANCHOR_RE.finditer(sql):
        start = anchor.end()
        end = sql.find("END", start)
        if end > start:
            blocks.append(sql[start:end])
    found = dict(_KEY_PROPERTY_WHEN_RE.findall(blocks[-1])) if blocks else {}
    missing = [label for _table, (label, _col) in NODE_TABLES.items()
               if label not in found]
    if missing:
        raise RecordProjectionError(
            f"public.node_identity declares no key_property for {missing} — the "
            "cache projection's key contract has no home; re-read the parser "
            f"against {_migrations_dir()}"
        )
    return found


def refused_props_names(migrations_dir: Path | None = None) -> dict[str, frozenset[str]]:
    """`{table: {name refused as a props key}}` — the carve-out, from the SQL.

    These are the names the schema itself refuses: the EP scalars (§17.5) and
    the projection-owned caches. The record must not store them as authority,
    and the projection drops them **by name** rather than relying on the CHECK
    to catch it later.
    """
    sql = _read_migrations(migrations_dir)
    out: dict[str, frozenset[str]] = {}
    # Bound each table to ITS OWN `CREATE TABLE … );` body (`_TABLE_RE`, the
    # same regex `table_columns` uses). Splitting on the CREATE headers alone
    # would run the LAST table's block to the end of the concatenated SQL, so a
    # later forward `ALTER` (#8263) would be misread as belonging to whichever
    # table happened to be last.
    for table, body in _TABLE_RE.findall(sql):
        if table not in THE_RECORD_TABLES:
            continue
        names: set[str] = set()
        for arr in _DERIVED_CHECK_RE.findall(body):
            names |= {n.strip().strip("'") for n in arr.split(",") if n.strip()}
        out[table] = frozenset(n for n in names if n)
    # Forward migrations re-declare a refusal rather than rewrite the CREATE
    # TABLE (append-only history). Apply them over the parsed tables — the set
    # only ever GROWS, so an ALTER that names fewer keys cannot silently
    # re-open a name an earlier migration closed.
    for table, arr in _ALTER_DERIVED_CHECK_RE.findall(sql):
        if table not in out:
            continue
        extra = frozenset(n.strip().strip("'") for n in arr.split(",") if n.strip())
        out[table] = out[table] | extra
    return out


def snake(prop: str) -> str:
    """camelCase → snake_case, preserving leading underscores.

    The SAME rule `#7968`'s schema↔placement pin uses, and preserving the
    leading underscore matters for the same reason: `_nid`/`_graph_id` are
    replay bookkeeping (DERIVED), while `graph_id` is the record's tenancy
    column — collapsing the underscore would let a DERIVED name look satisfied.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", prop).lower()


#: Journal keys that name the record itself rather than a node property: the
#: `_emit_event` envelope, the snapshot carrier, and the JSONL-only link/batch
#: markers. They are never written as columns and never fall into `props`.
#:
#: ⚠️ Deliberately NARROW. `id`, `name`, `label`, `key`, `op` and `state` are
#: NOT here even though `EntityMutated`/`EntityLinked` treat them as record
#: coordinates: for a `Point` snapshot `label` is a real PRIMARY property
#: (operator label), for `Object`/`Subject` `name` is a PRIMARY column but NOT
#: the key — the key is `id` since #9881 — and for `Event` `id` mirrors
#: `eventId` (#122). Those two dispatch paths read
#: their coordinates directly, so a global drop would silently lose a PRIMARY
#: field from the layer rows — exactly the "record poorer than the history"
#: failure Interface A forbids.
_RECORD_ONLY_KEYS: frozenset[str] = frozenset({
    "type", "ts", "event_id", "initiated_by", "projection_version", "seq",
    # #9078 review: the graph's ONE global skip-set (`projection/entities.py::
    # _META_KEYS`) strips `agent_id` before it reaches a node, so a record that
    # kept it wrote a props key a journal replay never does — and a
    # record-sourced rebuild copies the props tail VERBATIM
    # (`cache_projection._props_from_row`), so the two rebuild paths diverged on
    # every API-door record. ⚠️ `actor_user_id` is DELIBERATELY NOT here: §17.5
    # places it as a PRIMARY record property, so the record stores it even
    # though the cache node does not carry it (the record is the truth; the
    # cache is the projection). `test_record_projection`'s storable-primary leg
    # is what keeps that distinction honest.
    "agent_id",
    # #10017 R2: a PLACEMENT declaration, not a derived value.
    # ⛔ This is the RECORD's strip set, and deleting it as "redundant now that
    # the column exists and the schema refuses it" would be a silent data
    # disaster: `_split_properties` checks `column in cols` BEFORE `refused`, so
    # a payload `cache_tier` is written to the COLUMN — the refusal cannot catch
    # it. Without this entry any `PointAdded` could set
    # `cache_tier='record_only'` and hide a real graph point from the cache.
    # The GRAPH half is `projection/entities.py::_META_KEYS` (the `agent_id`
    # precedent — the record strip alone was never sufficient for that key).
    # ⛔ And because this strips it, the tier can NOT be set through an event
    # payload: it is dropped before the insert, so the row lands the DEFAULT
    # `graph` and the turn would be materialised again. Step 7 must write it as
    # its own column statement.
    "cache_tier",
    "point", "contains_session", "keep_id", "merge_ids", "corrects",
    # #5097 review P2: the capture path's STUB-COMPLETION discriminator. It
    # selects `_upsert_source`'s write behaviour (suppresses the ON CREATE
    # creator-only defaults and admits the capture replay props), is listed in
    # `_META_KEYS` so it never reaches a `:Source` node, and `create_source` /
    # `_sanitize_props` / MCP refuse it as a caller key. The RECORD layer is the
    # third home and it was missing: a `SourceCreated` snapshot is a
    # `path=None` NodeSnapshot, so the record's flat payload IS the property
    # dict, and the key fell into the `source.props` jsonb tail — which
    # `cache_projection` copies back onto the node verbatim. Same class as the
    # sibling `contains_session` above.
    "_stub_completion",
    # #9451: the sibling discriminator on the same record — "this record's
    # `ingestedAt` is the instant the live write STORED", which selects
    # `_upsert_source`'s ON MATCH behaviour. Same three homes as `_stub_completion`
    # above (`projection/entities.py::_META_KEYS`, `sdk._sanitize_props` /
    # `sdk.create_source`, MCP `_SERVER_MANAGED_PROPS`); the RECORD layer is the
    # fourth, and without it the key would fall into the `source.props` jsonb
    # tail that `cache_projection._props_from_row` copies back onto the node
    # verbatim.
    "_ingestedAtStored",
    # Creating-record embedding identity (a DESCRIPTION of the record, never a
    # node property — the fold's `_POINT_HANDLED` drops them too).
    "embedding_model", "embedding_revision", "embedding_text_hash",
    "embedding_preserved",
})


# ════════════════════════════════════════════════════════════════════════════
# Statement emission
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Statement:
    """One parameterized SQL statement plus its parameters."""

    sql: str
    params: tuple[Any, ...]

    def render(self) -> str:
        """A readable one-liner for logs and hover-less debugging."""
        return f"{self.sql} -- {self.params!r}"


@dataclass(frozen=True)
class _SqlExpr:
    """A value the DATABASE computes, in place of a literal parameter.

    A plan statement normally binds one `%s` per parameter. Some values cannot
    be decided when the plan is built because they are a function of the record
    the plan is being applied ON TOP OF — the key of a URL-variant `:Source` row
    is the name of whichever row already holds that canonical identity, and that
    only the store can answer (#9956, ``_source_key_resolution``).

    ``sql`` is a self-contained fragment (here: a scalar subquery) whose own
    placeholders are ``params``; the statement builders interleave it in place
    of the `%s` they would otherwise emit. Executed inside the caller's
    transaction, AFTER ``replay``'s tenant prologue, so it reads exactly what the
    plan is about to write against — and it sees rows written by EARLIER
    statements of the same replay, which is what makes journal order the fold's
    order.

    ``_render_value`` is the ONLY reader: a builder that stringified an
    `_SqlExpr` instead would emit a dataclass repr into SQL.
    """

    sql: str
    params: tuple[Any, ...] = ()


def _render_value(value: Any) -> tuple[str, tuple[Any, ...]]:
    """`(placeholder, params)` for a literal OR a database-computed value."""
    if isinstance(value, _SqlExpr):
        return value.sql, value.params
    return "%s", (value,)


def _jsonb_or_scalar(column: str, value: Any) -> Any:
    """`jsonb` columns take serialized JSON; everything else passes through.

    The caller supplies the value; this module does not coerce, because a
    journal carrying a string where the record's column is `boolean` is a
    journal/record disagreement and coercing it here would hide it.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


#: The record's own bookkeeping columns — supplied by the projection, never by
#: a journal payload. A payload property whose `snake()` form collides with one
#: of these must land in the `props` tail: routing it to the column either
#: emitted that column TWICE (`INSERT … (…, props, props)` — Postgres refuses
#: `column "props" specified more than once`) or silently rewrote the row's
#: identity (`node_key` via a `NodePatch`). `props` is itself the open-set tail,
#: so a node property literally named `props` nests inside it, preserving the
#: graph's property instead of dropping it.
_BOOKKEEPING_COLUMNS: frozenset[str] = frozenset({
    "org_id", "graph_id", "props",
    "node_label", "node_key",
    "rel_type", "src_label", "src_key", "tgt_label", "tgt_key", "idx",
})


def _split_properties(
    table: str,
    props: Mapping[str, Any],
    *,
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
    drop: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a journal property dict into `(columns, props_tail)`.

    Three destinations, and no fourth:
      * a property with a column is written to that column — UNLESS the
        column is the record's own bookkeeping (`_BOOKKEEPING_COLUMNS`),
        which a payload may never write;
      * a property the schema refuses as a props key is **dropped** (it is
        DERIVED/VOLATILE — recomputed from the structure, never authority);
      * everything else lands in the declared `props` tail.

    An explicit ``drop`` set carries the RECORD-ONLY keys — journal keys that
    describe the record rather than the node (`previousContentHash`,
    `previousValidTo`, a snapshot's identity keys).
    """
    cols = columns[table]
    refused_here = refused.get(table, frozenset())
    # ⚠️ PER-TABLE, because §17.5 places `actor_user_id` as a PRIMARY property of
    # the EPISODIC (session) layer ONLY, and `session` is the one table with the
    # column. Everywhere else the graph's global skip-set (`projection/entities.py::
    # _META_KEYS`, "rebuild replay must NOT leak it as a node property") strips
    # it, so a record that kept it in the props tail made a journal replay and a
    # record-sourced rebuild disagree about the same node (#9078 review). It
    # cannot go in `_RECORD_ONLY_KEYS`: that would drop the Session PRIMARY the
    # storable-property pin exists to protect.
    table_columns_here = columns.get(table, frozenset())
    col_values: dict[str, Any] = {}
    tail: dict[str, Any] = {}
    for prop, value in props.items():
        if prop in _STORED_ONLY_IF_COLUMNED and prop not in table_columns_here:
            continue
        if prop in drop:
            continue
        column = snake(prop)
        if column in _BOOKKEEPING_COLUMNS:
            # A payload may never write the record's own coordinates. The
            # property is preserved as DATA in the tail instead — dropping it
            # would make the record poorer than the graph, which is the
            # failure Interface A forbids.
            tail[prop] = value
        elif column in cols:
            col_values[column] = value
        elif prop in refused_here or column in refused_here:
            # DERIVED/VOLATILE — the carve-out. Dropping is the correct
            # mutation; storing it would make a recomputed value authority.
            continue
        else:
            tail[prop] = value
    return col_values, tail


def _node_table(node: Node, columns: Mapping[str, frozenset[str]]) -> str:
    if node.table not in columns:
        raise RecordProjectionError(
            f"unknown record table {node.table!r} — not one of {sorted(columns)}"
        )
    return node.table


def _key_value(node: Node, payload: Mapping[str, Any], key_from: str,
               *, fallback: Mapping[str, Any] | None = None) -> str:
    """The node key, and the schema's own pin that it is the label's key.

    A record whose key column is absent cannot be projected: the schema's
    `chk_*_node_key_is_contract_key` would refuse the write anyway, and a
    `node_key = <NULL>` comparison is SQL NULL (not FALSE), so the failure
    would be silent. Fail here instead, where the record is known.

    *fallback* is the record's own SNAPSHOT (`record[effect.path]`), consulted
    only when the envelope carries no usable key. Measured on #9078's lane: the
    capture writer emits `PointAdded`/`PointPromoted` with the id ONLY inside
    `payload["point"]` and no envelope `id` at all, while the comment on
    `RECORD_MUTATIONS` asserts the opposite ("`id` rides the envelope, not the
    snapshot"). The graph fold read `inner = event.get("point") or event` all
    along, so the snapshot was always a legitimate source. Reading the envelope
    FIRST keeps every producer that does carry the id byte-identical.
    """
    value = payload.get(key_from)
    if value is None and key_from in ("event_id", "eventId"):
        # The Event's canonical key is the payload's `eventId` (the fold's
        # MERGE key is `inner.get("id") or inner.get("eventId")`); the
        # envelope `event_id` is a per-record ULID and must never be used.
        value = payload.get("eventId") or payload.get("id")
    # A `:Source`'s MERGE key is `url`, and `_upsert_source` resolves it as
    # `raw_key = url or sid` — so a document record that names its identity `id`
    # (the `EventAPI.add_document` shape: `id=doc_id`, no `url`) keys the node by
    # that id LIVE. The record must key the row the SAME way, or every ingested
    # document is refused and the record loses it (#9078).
    #
    # ⚠️ The gate is ABSENCE (`key_from not in payload`), never unusability. A
    # `url` that is PRESENT but unusable (`""`, `None`) means the producer named
    # an identity it cannot express, and there are two ways to be wrong about it:
    # resolving `id` while the *props* still carry the unusable `url` emits a row
    # whose `node_key` and `url` column disagree — refused by the schema's own
    # `chk_source_node_key_is_contract_key`, AFTER the `node_identity` insert has
    # already run inside the caller's transaction — and silently repairing the
    # column instead would store a `url` the producer never emitted, hiding its
    # bug. Refuse here, where the record is known (this docstring's rule).
    # Absence is the one shape that is a real producer: `add_document` emits `id`
    # and no `url` at all.
    if key_from == "url" and key_from not in payload:
        value = payload.get("id")
    if fallback is not None and (not isinstance(value, str) or not value):
        value = fallback.get(key_from)
        if value is None and key_from in ("event_id", "eventId"):
            value = fallback.get("eventId") or fallback.get("id")
    if not isinstance(value, str) or not value:
        raise RecordProjectionError(
            f"record has no usable {key_from!r} key for {node.label} "
            f"(got {value!r}) — a row whose node_key is not the label's key "
            "resolves to nothing, in silence"
        )
    return value


def _key_list(record: Mapping[str, Any], keys_from: str) -> list[str]:
    """The ids a record carries under `keys_from`, in the fold's own reading.

    This is `_norm`'s carrier rule, not a new one: the graph fold reads the
    NORMALIZED record, and `_norm` is `{**ev, **ev["point"]}` when `point` is a
    dict — so `{"type": "PointsMerged", "point": {"merge_ids": [...]}}` is a
    SUPPORTED journal shape (`tests/test_projection.py::test_falkor_apply_
    points_merged_nested_format`, #325), and its ids sit one level down **and
    WIN over an envelope copy of the same key**. Read nested first, then flat,
    for exactly that reason; a non-dict `point` is ignored, as `_norm` ignores
    it.

    The entry filter is the fold's, verbatim (`for mid in ev.get("merge_ids")
    or []` + `isinstance(mid, str)`): a non-str entry is skipped in both. The two
    must agree on a MALFORMED record for the same reason they must agree on a
    well-formed one — a divergence is a rebuild that produces a different graph
    than the journal.

    An absent/empty field is `[]`, which is a 0-statement no-op: the fold's
    `merge_ids: []`/missing arm deletes nothing and does not raise, and
    `tests/test_projection.py` pins that a `PointsMerged` with no ids must not
    error.
    """
    nested = record.get("point")
    value = (nested.get(keys_from) if isinstance(nested, Mapping) else None)
    if value is None:
        value = record.get(keys_from)
    if value is None:
        return []
    if not isinstance(value, Iterable) or isinstance(value, Mapping):
        # Not a shape any producer emits and not one the fold can iterate as
        # ids — it would raise a bare TypeError mid-replay with no record named.
        # ⚠️ ONE deliberate divergence, named: a MAPPING is refused here while
        # the fold would iterate its KEYS and delete by them. No producer emits
        # that shape, and deleting Points named by a dict's keys is the kind of
        # silent loss this module exists to refuse.
        raise RecordProjectionError(
            f"record's {keys_from!r} is not an iterable of ids "
            f"(got {type(value).__name__}: {value!r})"
        )
    return [key for key in value if isinstance(key, str)]


def _identity_statement(org_id: str, graph_id: str, node: Node,
                        key: str | _SqlExpr) -> Statement:
    """The `(label, key)` FK parent. Written first, always.

    `public.node_identity` is why a relation's endpoints are real composite
    foreign keys (S6). Every node write is preceded by its identity row, and
    the insert is `DO NOTHING` on conflict so a re-replay is a no-op.

    *key* may be an `_SqlExpr` (#9956): a `:Source` row's key is a RESOLUTION of
    the record's raw spelling, so the identity parent must be filed under the
    same resolved key as the node row — a literal here and a resolution there
    would put the two rows under two identities and the composite FK on
    `public.source` would refuse the pair.
    """
    key_sql, key_params = _render_value(key)
    return Statement(
        "INSERT INTO public.node_identity "
        f"(org_id, graph_id, node_label, node_key) VALUES (%s, %s, %s, {key_sql}) "
        "ON CONFLICT (org_id, graph_id, node_label, node_key) DO NOTHING",
        (org_id, graph_id, node.label, *key_params),
    )


def _upsert_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str | _SqlExpr,
    col_values: Mapping[str, Any],
    tail: Mapping[str, Any],
) -> Statement:
    columns = ["org_id", "graph_id", "node_label", "node_key", *col_values, "props"]
    # `key` and any column value may be a database-computed `_SqlExpr` (#9956);
    # `_render_value` is the one place that turns either shape into SQL+params.
    key_sql, key_params = _render_value(key)
    placeholders_list: list[str] = ["%s", "%s", "%s", key_sql]
    params: list[Any] = [org_id, graph_id, node.label, *key_params]
    for column, value in col_values.items():
        fragment, values = _render_value(
            value if isinstance(value, _SqlExpr)
            else _jsonb_or_scalar(column, value))
        placeholders_list.append(fragment)
        params.extend(values)
    placeholders_list.append("%s")
    params.append(json.dumps(tail))
    placeholders = ", ".join(placeholders_list)
    # IDEMPOTENCE, stated once: re-applying the same record writes the same
    # absolute values onto the same row. A second replay of the journal
    # therefore produces the same record (Interface A's second clause).
    # FIDELITY: the `props` tail is MERGED, not replaced — the graph fold
    # applies an open-set tail with `SET n += $props` (entities.py), so a
    # partial carrier (a second `SessionRecorded`, a `SourceVersioned`) that
    # omitted a prop an earlier record landed would otherwise drop it from the
    # record while the graph preserved it.
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in col_values)
    if updates:
        updates += ", "
    updates += f"props = {node.table}.props || EXCLUDED.props"
    sql = (
        f"INSERT INTO public.{node.table} ({', '.join(columns)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (org_id, graph_id, node_key) DO UPDATE SET {updates}"
    )
    return Statement(sql, tuple(params))


def _patch_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    col_values: Mapping[str, Any],
    tail: Mapping[str, Any],
    match: tuple[str, ...] = ("node_key",),
) -> Statement:
    sets: list[str] = []
    params: list[Any] = []
    for column, value in col_values.items():
        sets.append(f"{column} = %s")
        params.append(_jsonb_or_scalar(column, value))
    if tail:
        sets.append("props = props || %s::jsonb")
        params.append(json.dumps(tail))
    if not sets:
        # A patch with nothing to write is a 0-row no-op by construction; make
        # it a real statement so the caller still sees the record was consumed.
        sets.append("node_key = node_key")
    params.extend([org_id, graph_id, *([key] * len(match))])
    predicate = " OR ".join(f"{c} = %s" for c in match)
    if len(match) > 1:
        predicate = f"({predicate})"
    sql = (
        f"UPDATE public.{node.table} SET {', '.join(sets)} "
        f"WHERE org_id = %s AND graph_id = %s AND {predicate}"
    )
    return Statement(sql, tuple(params))


def _delete_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    match: tuple[str, ...] = ("node_key",),
) -> Statement:
    predicate = " OR ".join(f"{c} = %s" for c in match)
    if len(match) > 1:
        predicate = f"({predicate})"
    return Statement(
        f"DELETE FROM public.{node.table} "
        f"WHERE org_id = %s AND graph_id = %s AND {predicate}",
        (org_id, graph_id, *([key] * len(match))),
    )


def _identity_delete_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    match: tuple[str, ...] = ("node_key",),
) -> Statement:
    """The FK parent's removal — the record's half of the graph's `DETACH DELETE`.

    `node_identity` is the composite-FK parent of BOTH the node row and every
    incident `relation` row, and the schema declares both FKs `ON DELETE
    CASCADE` for exactly this purpose. A bare node `DELETE` therefore left the
    identity row and each incident edge alive — the record kept edges to a node
    it had removed, while the graph's `MATCH … DETACH DELETE n` (the fold's
    `_delete_entity_by_id`) dropped the node AND its edges.

    An Object/Subject delete matches on the `id` column — which since #9881 IS
    that label's canonical key — but the match column and the key column are not
    the same column for every label (`source` matches on `id` or `node_key`
    while its key is `url`; `session` keys `id` but matches `node_key`), so the
    identity row's key is resolved from the node row — which still exists at
    this point, because this statement MUST run before `_delete_statement`. The
    subquery reads the node table, so the cascade then removes that row and
    every incident relation.
    """
    predicate = " OR ".join(f"{c} = %s" for c in match)
    if len(match) > 1:
        predicate = f"({predicate})"
    return Statement(
        "DELETE FROM public.node_identity "
        "WHERE org_id = %s AND graph_id = %s AND node_label = %s "
        f"AND node_key IN (SELECT node_key FROM public.{node.table} "
        f"WHERE org_id = %s AND graph_id = %s AND {predicate})",
        (org_id, graph_id, node.label, org_id, graph_id, *([key] * len(match))),
    )


def _delete_statements(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    match: tuple[str, ...] = ("node_key",),
) -> list[Statement]:
    """A hard delete: the identity parent FIRST (so the subquery sees the node
    row), then the node row. The identity delete cascades the incident edges,
    matching the graph's `DETACH DELETE`; the explicit node delete is the
    child-first form and a no-op once the cascade has run."""
    return [
        _identity_delete_statement(org_id, graph_id, node, key, match),
        _delete_statement(org_id, graph_id, node, key, match),
    ]


def _relation_coordinates(
    rel: RelationUpsert | RelationDelete,
    payload: Mapping[str, Any],
) -> tuple[str, str, str, str, str, Any]:
    """The `(rel_type, src_label, src, tgt_label, tgt, idx)` of an edge record."""
    rel_type = payload.get(rel.rel_type_from) or payload.get("type_of_edge")
    src = payload.get(rel.src_from) or payload.get("source_id")
    tgt = payload.get(rel.tgt_from) or payload.get("target_id")
    if not isinstance(rel_type, str) or not rel_type:
        raise RecordProjectionError(
            f"edge record has no usable edge type (got {rel_type!r}) — an "
            "edge with no predicate is not a relation"
        )
    src_label = payload.get("source_label") or payload.get("src_label") or "Point"
    tgt_label = payload.get("target_label") or payload.get("tgt_label") or "Point"
    if not isinstance(src, str) or not isinstance(tgt, str) or not src or not tgt:
        raise RecordProjectionError(
            f"edge record has no usable endpoints (src={src!r}, tgt={tgt!r})"
        )
    return rel_type, str(src_label), src, str(tgt_label), tgt, payload.get("idx", 0)


def _endpoint_resolution(
    org_id: str,
    graph_id: str,
    label: str,
    journal_id: str,
    alias: str = "",
    *,
    match: tuple[str, ...] | None = None,
    values: Sequence[Any] | None = None,
    order_by: str | None = None,
    prefer: tuple[str, Any] | None = None,
) -> tuple[str, list[Any]]:
    """The node row an edge endpoint addresses, keyed by the label's key column.

    The graph fold addresses an `EntityLinked` endpoint by **`id`** —
    `MATCH (t:{label} {id:$tid})` in
    `projection/entities.py::_fold_entity_linked_reason` — and that is what the
    producers emit (`sdk.py::link_entity` passes `o.id`;
    `subject_binding._resolve_target` returns `n.id`). But the RECORD's
    canonical key is `Point`/`Session`→`id`, `Event`→`event_id`,
    `Source`→`url`, `Object`/`Subject`→`id` (since #9881 — `name` is a mutable
    natural key, so it is the LOOKUP and never the identity;
    `node_identity.key_property`, re-declared by migration `20261027000001`), and
    `relation.src_key`/`tgt_key` are composite FKs into `node_identity`.

    Writing the raw journal id straight through therefore attached the edge to
    a PHANTOM identity (`Object/o1` where the row is keyed `release-1.0`), which
    the hard-delete cascade could never reach — the record kept an edge to a
    node it had removed. This resolves `node_key` from the projection's own node
    row, matching the journal id with the SAME predicate the hard-delete
    cascade uses (:data:`_ENTITY_MATCH_COLUMNS`), and the caller joins on the
    result: an endpoint with no row produces **no relation at all**, which is
    the fold's own `"absent"` verdict rather than a minted phantom.

    *match* overrides the label's default predicate (:data:`_ENTITY_MATCH_COLUMNS`)
    for a family whose fold matches ONE property. `extractedFrom` is that family
    (#9553 class 1): `_link_source` writes
    `MATCH (n:Point {id:…}), (s:Source {url:$url}) MERGE (n)-[:extractedFrom]->(s)`
    — url ONLY — while the `Source` default is `id = %s OR url = %s`. Without the
    override a point whose `extractedFrom` ref happens to be a Source's `id`
    (a document reference: `create_document` keys the `url` but also stores the
    `id`) would resolve onto that document row, where the fold would have minted
    a stub `(s:Source {url:<ref>})` and attached the edge to the STUB: the record
    would wire the edge to a different node than the graph did.

    *values* binds one value per *match* column (defaults to *journal_id* for
    every column). `extractedFrom` needs it because its two columns are matched
    against TWO DIFFERENT values — the raw ref and its canonical form — to
    mirror `source_identity.resolve_source_key`, which the fold calls before it
    MERGEs (see `_point_property_relation_statements`).

    *order_by* appends `ORDER BY <column> COLLATE "C" LIMIT 1`. A multi-column
    match can hit more than one row (several Sources may share a canonical
    form), and `resolve_source_key` picks the LOWEST `url` on ties — without the
    clause the record would pick arbitrarily and could wire the edge to a
    different Source than the graph did, on the same journal line.

    ⚠️ THE `COLLATE "C"` IS LOAD-BEARING, NOT DECORATION (#9967 review P1).
    `resolve_source_key`'s `ORDER BY s.url LIMIT 1` is the GRAPH's ordering, and
    FalkorDB compares strings by BYTE order. A bare `ORDER BY node_key` is
    ordered by the DATABASE's collation instead, and that is neither byte order
    nor even stable across builds — measured on the exact pair this file's
    `#9956` tests use (`https://example.com/a/b` + the `Example.COM/…` variant,
    both sharing the canonical form):

    * `postgres:16` (glibc `en_US.utf8`) → the lowercase spelling sorts first;
    * `postgres:16-alpine` (musl `en_US.utf8`) → the `Example.COM` spelling
      sorts first;
    * both images with `COLLATE "C"` → `Example.COM` first, which is byte order
      ('E' = 0x45 < 'e' = 0x65) and therefore FalkorDB's answer.

    So a host-case variant — the COMMONEST URL variant, and the shape of the
    pre-`#9956` duplicate population this resolver folds — would pick a
    DIFFERENT row than the graph depending only on which Postgres image the
    record runs on. The row COUNT was never affected; WHICH row absorbs the
    folded `title`/`document_kind` was. `COLLATE "C"` is the one spelling of
    "the graph's own order" that both consumers (row key and edge endpoint)
    inherit from this single fragment.

    *prefer* orders one matched COLUMN ahead of the rest: `ORDER BY CASE WHEN
    <column> = %s THEN 0 ELSE 1 END[, <order_by>] LIMIT 1`. A `match` predicate
    is a UNION, and a union has no precedence — but `resolve_source_key` does:
    it queries `canonicalUrl` FIRST and returns there, considering the raw
    spelling only afterwards (adopt-on-touch, then a second canonical query). A
    plain `ORDER BY <key>` over the union therefore expresses a different rule,
    and on a record holding two Sources for one identity it can wire the edge to
    the node the fold would not have chosen — the same class of divergence the
    `id`-fallback exclusion above exists to stop.
    """
    table = _LABEL_TABLES.get(label)
    if table is None:
        raise RecordProjectionError(
            f"edge endpoint names label {label!r}, which is not a record node "
            f"label ({sorted(_LABEL_TABLES)})"
        )
    if match is None:
        match = _ENTITY_MATCH_COLUMNS.get(table, ("node_key",))
    predicate = " OR ".join(f"{column} = %s" for column in match)
    if len(match) > 1:
        predicate = f"({predicate})"
    ordering: list[str] = []
    preferred: list[Any] = []
    if prefer is not None:
        preferred_column, preferred_value = prefer
        ordering.append(f"CASE WHEN {preferred_column} = %s THEN 0 ELSE 1 END")
        preferred.append(preferred_value)
    if order_by:
        # BYTE order, always (#9967 review P1): the tiebreak is
        # `resolve_source_key`'s `ORDER BY s.url LIMIT 1`, and FalkorDB orders
        # strings by bytes while a default-collation `ORDER BY` orders them by
        # the DATABASE's locale — which differs between glibc and musl builds.
        ordering.append(f'{order_by} COLLATE "C"')
    tail = f" ORDER BY {', '.join(ordering)} LIMIT 1" if ordering else ""
    bound = list(values) if values is not None else [journal_id] * len(match)
    if len(bound) != len(match):
        raise RecordProjectionError(
            f"endpoint {alias} binds {len(bound)} value(s) for "
            f"{len(match)} match column(s) {match!r}"
        )
    # An empty *alias* yields a bare parenthesised SELECT — i.e. a SCALAR
    # subquery, which is how the node-key resolver below reuses this one
    # resolution rule instead of restating it (#9956).
    return (
        f"(SELECT node_key FROM public.{table} "
        f"WHERE org_id = %s AND graph_id = %s AND {predicate}{tail})"
        f"{' ' + alias if alias else ''}",
        [org_id, graph_id, *bound, *preferred],
    )


def _source_key_resolution(org_id: str, graph_id: str,
                           raw_key: str) -> _SqlExpr:
    """`source_identity.resolve_source_key` as a SQL scalar subquery (#9956).

    THE DEFECT THIS CLOSES. The graph folds a URL-VARIANT document identity onto
    ONE `:Source`: `_upsert_document` (`projection/entities.py`) passes its id
    through `source_identity.resolve_source_key` before it MERGEs (#5140 finding
    12, #9952). The record layer does not read a graph — it is journal-derived —
    so it keyed `public.source` on the record's RAW id, and a `DocumentCreated`
    whose id is a variant of an already-registered source planned a SECOND row
    for the identity the graph keeps as ONE node. Measured on a real Postgres
    16: `SourceCreated('https://example.com/a/b')` + `DocumentCreated` on
    `'https://Example.COM/a/b/?utm_source=x#frag'` → **2 record rows, 1 graph
    node** — so a record-sourced rebuild resurrected a duplicate source row, in
    silence, with the graph's variant resolution lost on the record side.

    WHY THIS IS A SQL TWIN AND NOT A CALL TO THE RESOLVER. `resolve_source_key`
    takes a projection graph handle `g` and queries the GRAPH. This layer holds
    a Postgres connection and no graph at all (Interface A: journal → record,
    rebuildable from the journal alone), so importing it would either require a
    graph handle the record seam does not have or make the record's rebuild
    depend on the graph it must be independent of. The smallest honest move is
    therefore the one already taken in this file for the EDGE endpoint of the
    same identity: `_endpoint_resolution` resolves a `Source` from the record's
    own rows, in SQL, with the resolver's own precedence. This function is that
    resolver applied to the ROW KEY — not a second rule:

    * **step 1/3** — an already-registered row whose `canonical_url` is this
      identity's canonical form, returning ITS key (`resolve_source_key` queries
      `MATCH (s:Source {canonicalUrl:$cu}) RETURN s.url`);
    * on ties, the LOWEST key — `resolve_source_key`'s `ORDER BY s.url LIMIT 1`,
      and `chk_source_node_key_is_contract_key` makes `node_key` the `url`;
    * **step 2** (adopt-on-touch) — a row stored under the inbound RAW spelling
      **or under the canonical spelling itself**.
      Both candidates, because the graph has both: `resolve_source_key`
      (`source_identity.py`, "for candidate in ([url, canonical] …)") adopts by
      either spelling, and its own comment says the canonical one is "the common
      API/connector case — its url was already canonical, so the inbound variant
      never matches it by url". Dropping the second candidate would leave every
      row this layer wrote before the `canonical_url` derive (2026-10-23, #9553)
      invisible to a variant — a duplicate for exactly the population the graph
      adopts. This is the SAME triple `_endpoint_resolution` binds for an
      `extractedFrom` edge endpoint, so the row key and the edge can never
      resolve one identity onto two different rows. (The graph's tier re-reads
      after adopting; the record does not need to — its predicate is a union of
      the same three match columns in ONE statement.)

      ⚠️ **THE EMITTED PREDICATE CARRIES NO `canonical_url IS NULL` GUARD, and
      that is a DELIBERATE divergence from the graph's step 2 — stated here
      because an earlier cycle claimed the guard was bound when it was not
      (#9967 review P2).** The graph's adopt query has the guard because
      ADOPTING IS A WRITE there (it sets `canonicalUrl`, and the predicate makes
      the re-run a no-op); here the tier only SELECTS.

      The two are equivalent for every row THIS LAYER writes, by an invariant
      the writer holds: `canonical_url` is derived unconditionally as
      `_normalize_source_url(node_key)` (the `col_values.setdefault` in
      `_declared_mutations`), so `node_key = <raw>` implies
      `canonical_url = normalize(<raw>) = <canonical>` — the row is then a
      CANONICAL match, which the `prefer` clause ranks first, exactly as the
      graph returns from its `canonicalUrl` query before it adopts. And a
      pre-#9553 row has `canonical_url IS NULL`, which is the state the graph's
      guard admits.

      The declared residual: that invariant is not ENFORCED by the schema. A
      journal line whose payload carries an explicit `canonicalUrl` reaches the
      column through `_split_properties` (the `column in cols` arm runs BEFORE
      the refused-key check), so it can produce a row with
      `canonical_url != normalize(node_key)`. Against such a row this subquery
      adopts where the graph would MERGE a new node. That population is not
      producible by any writer in this repo, and binding the guard here would
      break the row-key/edge parity above, so the divergence is NAMED rather
      than papered over.
    * **step 4** — nothing registered: the raw key, exactly as the graph MERGEs
      a new node. `COALESCE(…, %s)` is that fallback; without it a miss would
      bind NULL to `node_key` (NOT NULL) instead of creating the row.

    `canonical_url` is `_normalize_source_url` — the record's pinned twin of
    `normalize_source_url` (`test_the_source_canonicaliser_is_the_folds_own`),
    so the canonicaliser is not duplicated here either.

    Consequences, stated rather than left to be discovered (see
    `_declared_mutations` and the PR): this is a RESOLUTION at execution time,
    so it inherits the live-vs-replay exposure #9952 names for every resolving
    writer; it does not re-key a single existing row; and it makes the record
    agree with the graph on WHICH row one identity is.
    """
    canonical = _normalize_source_url(raw_key)
    select, params = _endpoint_resolution(
        org_id, graph_id, "Source", raw_key,
        # Three candidates, in the resolver's own order of concern: the RAW
        # spelling, the CANONICAL spelling (both pre-canonical-adoption), then a
        # row already stamped with the canonical identity — which `prefer`
        # ranks first, because a union has no precedence and
        # `resolve_source_key` returns from its `canonicalUrl` query BEFORE it
        # considers either spelling.
        match=("node_key", "node_key", "canonical_url"),
        values=(raw_key, canonical, canonical),
        order_by="node_key",
        prefer=("canonical_url", canonical),
    )
    return _SqlExpr(f"COALESCE({select}, %s)", (*params, raw_key))


def _relation_upsert_statement(
    org_id: str,
    graph_id: str,
    rel: RelationUpsert,
    payload: Mapping[str, Any],
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
    *,
    src_match: tuple[str, ...] | None = None,
    tgt_match: tuple[str, ...] | None = None,
    tgt_values: Sequence[Any] | None = None,
    tgt_order_by: str | None = None,
    tgt_prefer: tuple[str, Any] | None = None,
) -> list[Statement]:
    """One `relation` row, with both endpoints RESOLVED from record rows.

    *src_match* / *tgt_match* / *tgt_values* / *tgt_order_by* / *tgt_prefer* are
    pass-throughs to :func:`_endpoint_resolution` for a family whose fold
    addresses an endpoint by something other than the label's default predicate
    (`extractedFrom` → the Source's canonical identity, #9553 class 1). All are
    keyword-only with `None` defaults, so every pre-existing call site emits
    byte-identical SQL.

    ON CONFLICT is a REPLAY, not a re-link — with ONE exception.
    `source_version` (the #5256 read-version anchor) is written by the fold
    through `_anchor_on_create` (`ON CREATE SET r.sourceVersion = …`), whose rule
    `_link_source` states: *"a re-link must not ADVANCE a recorded version"*.
    Emitting `source_version = EXCLUDED.source_version` on the conflict path
    would let a LATER record for the same edge (a re-emitted `PointAdded` —
    `create_point` appends one before a DETACH DELETE and may append a second; a
    promote snapshot; a second ref of one Point resolving onto the same Source)
    overwrite an anchor the journal route keeps, and the two rebuild routes would
    then disagree about the same journal. So the anchor is written ON CREATE
    ONLY: `source_version` is dropped from the update list entirely, which is
    the fold's own rule rather than an approximation of it. Every OTHER
    attribute stays latest-record-wins (`direction`/`confidence`/`weight`/
    `label`/`batch_id` are edge STATE the later record legitimately corrects,
    not a recorded read version).
    """
    rel_type, src_label, src, tgt_label, tgt, idx = _relation_coordinates(rel, payload)
    col_values, tail = _split_properties(
        "relation", {k: payload[k] for k in rel.attrs if k in payload},
        columns=columns, refused=refused,
    )
    # The endpoints are RESOLVED, never minted: the fold's `MATCH … MERGE`
    # creates no endpoints, and an infinite source of phantom `node_identity`
    # rows is exactly what let a link survive its object's hard delete. The
    # `FROM` join yields ZERO rows when either endpoint is absent, so the whole
    # insert is a no-op — the fold's `"absent"` verdict, not a dropped edge
    # that still materializes a key.
    src_select, src_params = _endpoint_resolution(
        org_id, graph_id, src_label, src, "src", match=src_match)
    tgt_select, tgt_params = _endpoint_resolution(
        org_id, graph_id, tgt_label, tgt, "tgt", match=tgt_match,
        values=tgt_values, order_by=tgt_order_by, prefer=tgt_prefer)
    columns_ = ["org_id", "graph_id", "rel_type", "src_label", "src_key",
                "tgt_label", "tgt_key", "idx", *col_values, "props"]
    select_list = ", ".join([
        "%s", "%s", "%s", "%s", "src.node_key", "%s", "tgt.node_key", "%s",
        *(["%s"] * len(col_values)), "%s",
    ])
    params: list[Any] = [
        org_id, graph_id, rel_type, src_label, tgt_label, idx,
        *(_jsonb_or_scalar(c, v) for c, v in col_values.items()), json.dumps(tail),
        *src_params, *tgt_params,
    ]
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}"
        for column in [*col_values, "props"]
        # ON CREATE ONLY (#5256 / #5199): the anchor is written by the INSERT
        # and never by the conflict path — see the docstring. Dropping it here
        # (rather than filling a NULL) is literally `_anchor_on_create`: the
        # fold does not touch `r.sourceVersion` on a re-link, so a row whose
        # edge already existed keeps whatever it was created with.
        if column != "source_version"
    )
    return [Statement(
        f"INSERT INTO public.relation ({', '.join(columns_)}) "
        f"SELECT {select_list} FROM {src_select}, {tgt_select} "
        "ON CONFLICT (org_id, graph_id, rel_type, src_label, src_key, "
        f"tgt_label, tgt_key, idx) DO UPDATE SET {updates}",
        tuple(params),
    )]


def _relation_delete_statement(
    org_id: str,
    graph_id: str,
    rel: RelationUpsert | RelationDelete,
    payload: Mapping[str, Any],
) -> Statement:
    """Remove one relation row — the `delete_only` leg of a `DirectEdgeRepoint`.

    A `delete_only` descriptor (#2489, the supersede no-self-edge guard)
    describes the edge the graph DELETES: the OLD point (`src`) and the
    successor point its edge terminated at (`tgt`). BOTH endpoints are
    `:Point`, so the descriptor's `target_label` — which names the STRUCTURAL
    rel family, not the edge's actual target — is deliberately ignored here;
    using it would target a nonexistent `(Point, Source, …)` row (and the
    upsert path additionally minted a phantom `node_identity` for it). No
    identity row is minted: a delete materializes nothing.
    """
    rel_type, _src_label, src, _tgt_label, tgt, idx = _relation_coordinates(
        rel, payload)
    return Statement(
        "DELETE FROM public.relation WHERE org_id = %s AND graph_id = %s "
        "AND rel_type = %s AND src_label = 'Point' AND src_key = %s "
        "AND tgt_label = 'Point' AND tgt_key = %s AND idx = %s",
        (org_id, graph_id, rel_type, src, tgt, idx),
    )


# ════════════════════════════════════════════════════════════════════════════
# §4.4 pass-2b: a STRUCTURAL `DirectEdgeRepoint` is a MOVE, not an upsert
# (#9972 + #9973 — the two halves of ONE mutation)
# ════════════════════════════════════════════════════════════════════════════
#
# `DirectEdgeRepoint` carries TWO descriptor shapes with DIFFERENT meanings, and
# the record treated them as one (`RelationUpsert()` at the descriptor's own
# `src`):
#
#   * 2a DIRECT (`edge_type` ∉ the structural rel set) — `src` is already the
#     SUCCESSOR (`sdk.py` `src=new_id`) and `tgt` is an endpoint ID. An upsert at
#     `src` is right; nothing moves.
#   * 2b STRUCTURAL (`edge_type` ∈ the structural rel set) — `src` is the point
#     being SUPERSEDED (`sdk.py` `src=old_id`) and `tgt` is the target's
#     **replay key**, which `stub_key` (`projection/edges.py`) mints as a NAME
#     for `aboutSubject`/`aboutObject`/`aboutEvent`/`aboutPoint` and as a `url`
#     for `aboutDocument`/`extractedFrom`. The descriptor means MOVE.
#
# Two silent defects followed, both measured on a real Postgres (PGlite, every
# migration applied — `supabase/tests/pglite/validate.mjs`):
#
#   #9972 — the target was resolved against the label's KEY column (`object.id`)
#           while the descriptor names its `name`, so the `INSERT … SELECT`
#           yielded no row: a successful 0-row statement, no error, nothing on
#           `record_write_failures`, and the graph holding the edge.
#   #9973 — nothing was ever DELETED and the row that did land was filed at the
#           SUPERSEDED point: `relation` kept an edge the graph removed and the
#           successor never got it, so a record-sourced rebuild resurrects it.
#
# Either half alone is unobservable (a target-only fix writes the same wrong row
# with its attributes erased by the `ON CONFLICT` path; a move-only fix deletes
# and re-creates against a target that resolved to nothing), which is why they
# land together. The graph side is the oracle and is mirrored, not re-invented:
# `projection/__init__.py` pass-2b resolves the target through
# `resolve_structural_target`, deletes the resurrection at `old`, and re-creates
# it at its DIRECT successor — and skips the whole descriptor when
# `_final(src) == src` or when the resolved target IS the destination (the
# E2E-11.6 no-self-edge contract).

#: rel → the label of the STRUCTURAL target, restated from
#: `projection/edges.py::STRUCTURAL_REL_LABELS`. ⛔ The label is derived from the
#: REL and never read off the descriptor's `target_label`: that is what the graph
#: does (`STRUCTURAL_REL_LABELS[etype]` — the journal fold IGNORES the field), and
#: a `target_label` that disagreed with the rel would otherwise file the row
#: under a label the fold never resolved. The map is also the ROUTER: membership
#: is what distinguishes a 2b structural descriptor from a 2a direct one.
#:
#: Restated rather than imported for the STANDALONE reason every other constant
#: in this block is (the PGlite harness `spec_from_file_location`s this module,
#: so a package-relative import raises there) and pinned to the fold's own map —
#: both directions — by
#: `tests/test_record_projection.py::test_the_structural_repoint_map_is_the_folds_own`.
_STRUCTURAL_REL_LABELS: dict[str, str] = {
    "aboutSubject": "Subject",
    "aboutObject": "Object",
    "aboutEvent": "Event",
    "aboutPoint": "Point",
    "aboutDocument": "Source",
    "extractedFrom": "Source",
}

#: The five EDGE attributes a structural `DirectEdgeRepoint` carries, restated
#: from `projection/edges.py::DIRECT_EDGE_ATTR_KEYS` — the ONE vocabulary the
#: live carry (`sdk.supersede_point`), the pass-2b replay fold and this move all
#: read (#10139). Restated rather than imported for the STANDALONE reason above
#: (the PGlite harness `spec_from_file_location`s this module with the stdlib
#: only, so a `tortoise.…` import would not resolve there) and pinned to the
#: fold's own tuple — both directions — by
#: `tests/test_record_projection.py::test_the_structural_carry_reads_the_folds_own_vocabulary`.
#:
#: ⛔ `source_version` is deliberately NOT a member, on either side: the fold
#: writes this anchor as `sourceVersion` and its transfer/replay drops it, so a
#: member here would make the record CARRY an attribute the graph loses — a new
#: divergence in place of the old one. The record contract also forbids
#: re-applying it on a re-link ("a re-link must not ADVANCE a recorded version").
_DIRECT_EDGE_ATTR_KEYS: tuple[str, ...] = (
    "direction", "confidence", "weight", "label", "batch_id",
)

#: The statuses the graph's terminal gate EXCLUDES, restated from
#: `live.TERMINAL_EXCLUDED_STATUSES` (same STANDALONE reason; pinned by
#: `test_the_terminal_status_set_is_the_canonical_ones`).
#:
#: It is here because the graph's structural delete-leg is guarded by
#: `projection/__init__.py::_src_is_terminal`, which asks
#: `live._terminal_excluded` (a code-review P1: under id-reuse `src` may be a
#: RE-CREATED **live** incarnation whose edges are legitimately its own, and
#: moving them clobbers a live point). `_terminal_excluded`'s vocabulary is
#: `TERMINAL_EXCLUDED_STATUSES` — `TERMINAL_STATUS_VALUES` **plus `deprecated`**,
#: which is not a legal create-time status (`live.py`) but IS present in legacy
#: graphs and must never be served as current. Pinning this set to
#: `TERMINAL_STATUS_VALUES` alone made the record's gate NARROWER than the fold's
#: by exactly that member, so a legacy `deprecated` point was moved by the graph
#: and skipped by the record.
_TERMINAL_POINT_STATUSES: tuple[str, ...] = (
    "archived", "deprecated", "outdated", "retracted", "superseded",
)

#: The bound on the `superseded_by` chase. The graph's `_final` walks its fold
#: map, which cannot cycle; the record's is a table walk, so the recursion is
#: both depth-bounded and `path`-guarded. The MOVE reads ONE hop (`depth = 2`,
#: the direct successor live named); the deeper hops exist because a chain's
#: earlier descriptors each resolve at their own successor.
#:
#: ⚠️ NO DESTINATION ⇒ NOTHING MOVES, and that promise is only kept because the
#: DELETE carries the same "a destination exists" conjunct as the INSERT: gating
#: the delete on `_src_is_terminal(src)` + "src HAS a successor" alone fired the
#: DELETE while the INSERT's `dest` subquery was empty, so a chain the record
#: cannot resolve DELETED the row and wrote no replacement.
_SUPERSEDE_CHAIN_DEPTH = 32


def _terminal_point_sql(alias: str) -> str:
    """The TERMINAL half of `live._terminal_excluded` over one `public.point`
    alias, as SQL.

    The statuses are INLINED rather than bound: they are a fixed vocabulary this
    module declares (never caller input), and keeping them out of the parameter
    list is what lets the two statements below reuse one parameter block. They
    are pinned to the canonical set by the test named on
    :data:`_TERMINAL_POINT_STATUSES`, so an inlined literal cannot drift silently.

    The legacy flag is read from the `outdated` COLUMN, not from `props`: the
    record writes it as a column (`PointSuperseded`/`PointInvalidated` declare it
    as a `NodePatch` literal, and `_split_properties` routes a payload key to the
    column whenever the table has one — measured: `PointAdded{point:{…,
    outdated:true}}` emits `(…, status, outdated, props) VALUES (…, 'live', true,
    '{}')`). A `props ->> 'outdated'` leg is therefore DEAD — it was always NULL,
    so a `PointInvalidated` point (flag set, status untouched) read as LIVE here
    while the fold read it as terminal.
    """
    literals = ", ".join(f"'{status}'" for status in _TERMINAL_POINT_STATUSES)
    return (f"({alias}.status IN ({literals}) "
            f"OR coalesce({alias}.outdated, false) = true)")


def _supersede_chain_cte(org_id: str, graph_id: str,
                        src: str) -> tuple[str, list[Any]]:
    """`projection/__init__.py::_final(src)` as a recursive CTE (#9973).

    The record already stores the successor link the chase needs —
    `public.point.props->>'superseded_by'`, written by the `PointSuperseded`
    patch (`renames=(("superseded_by", "new_id"), …)`) — so no graph read is
    involved. `node_key` IS the Point's `id`
    (`chk_point_node_key_is_contract_key`), which is why the root of the walk and
    its hops use the same column.

    Columns: `node_key` (this hop), `next_id` (its successor, NULL at the end),
    `depth` (1 = `src`), `path` (the cycle guard — Postgres forbids a second
    reference to the recursive CTE, so the walk carries its own path), and
    `terminal` (`live.is_terminal_status` for THIS hop, which is what
    `_src_is_terminal` reads on the graph side).
    """
    terminal = _terminal_point_sql("n")
    return (
        "WITH RECURSIVE succ(node_key, next_id, depth, path, terminal) AS ("
        "SELECT p.node_key, NULLIF(p.props ->> 'superseded_by', ''), 1, "
        f"ARRAY[p.node_key], {_terminal_point_sql('p')} "
        "FROM public.point p "
        "WHERE p.org_id = %s AND p.graph_id = %s AND p.node_key = %s "
        "UNION ALL "
        "SELECT n.node_key, NULLIF(n.props ->> 'superseded_by', ''), "
        f"s.depth + 1, s.path || n.node_key, {terminal} "
        "FROM succ s JOIN public.point n "
        "ON n.org_id = %s AND n.graph_id = %s AND n.node_key = s.next_id "
        "WHERE s.depth < %s AND NOT (n.node_key = ANY (s.path))"
        ") ",
        [org_id, graph_id, src, org_id, graph_id, _SUPERSEDE_CHAIN_DEPTH],
    )


def _structural_target_resolution(org_id: str, graph_id: str, label: str,
                                 rel: str, key: str,
                                 alias: str) -> tuple[str, list[Any]]:
    """`projection/edges.py::resolve_structural_target` as a scalar subquery.

    The four name-keyed labels resolve by **`name`** — never by `id`, which is
    the whole of #9972 — and a name held by TWO OR MORE carriers resolves to
    NOTHING (`HAVING count(*) = 1`), never to an arbitrary pick. That is #5509's
    discipline, which the graph applies in `_refuse_ambiguous_target` (it records
    the non-folded entry and skips the descriptor); a name that does not name ONE
    identity has no target, and `LIMIT 1` would let the record land an edge on a
    different carrier than the graph did.

    Per label (each verified against the fold's own arm):

    * `Subject`/`Object`/`Event` → `name` (a PRIMARY column on all three).
    * `Point` → `props->>'name'`: `public.point` has **no `name` column** (its key
      is the id), while the graph's generic arm matches `MATCH (x:Point
      {name:$key})` — a Point property, which the record stores in the props
      tail. Without this arm an `aboutPoint` descriptor would fall back to an
      id match against a NAME and land 0 rows (or worse, an unrelated point).
    * `Source` + `aboutDocument` → `url` AND `document_kind IS NOT NULL`, the
      kind test `resolve_structural_target` states is LOAD-BEARING on the
      descriptor route (a descriptor keyed on `url` alone cannot tell a document
      from a provenance `:Source`; refusing skips the descriptor instead of
      mis-pointing it). Resolve-only, as there: never invented.
    * `Source` + `extractedFrom` → the CANONICAL identity triple
      (`resolve_source_key`'s precedence, the same `match`/`prefer`/`order_by`
      the point-property family already passes) — reused rather than restated,
      so the repoint arm and the `extractedFrom` create arm can never resolve one
      Source identity onto two different rows.

    ⛔ NOTHING IS MINTED. The fold mints a `Subject`/`Source` stub on a miss; the
    record never does — its standing rule for every edge family is that an
    endpoint is RESOLVED from the record's own rows and an absent endpoint lands
    no row (`_relation_upsert_statement`). Minting from an edge directive would
    materialize a node the journal never created, which is the divergence class
    #9078 refuses. The residual is named rather than papered over: for the two
    stubbable labels the GRAPH may hold a stub the RECORD does not, so a
    descriptor that resolves in the graph can land no row here.
    """
    if label == "Source" and rel == "extractedFrom":
        canonical = _normalize_source_url(key)
        return _endpoint_resolution(
            org_id, graph_id, label, key, alias,
            match=("node_key", "node_key", "canonical_url"),
            values=(key, canonical, canonical),
            order_by="node_key",
            prefer=("canonical_url", canonical),
        )
    table = _LABEL_TABLES.get(label)
    if table is None:
        raise RecordProjectionError(
            f"structural edge {rel!r} names target label {label!r}, which is "
            f"not a record node label ({sorted(_LABEL_TABLES)})"
        )
    if label == "Source":
        # aboutDocument: RESOLVE-ONLY, and only a DOCUMENT. `node_key` IS the
        # Source's `url` (`chk_source_node_key_is_contract_key`), which is the
        # property the fold's `MATCH (s:Source {url:$url})` addresses.
        return (
            f"(SELECT min(t.node_key) AS node_key FROM public.{table} t "
            "WHERE t.org_id = %s AND t.graph_id = %s AND t.node_key = %s "
            "AND t.document_kind IS NOT NULL HAVING count(*) = 1)"
            f"{' ' + alias if alias else ''}",
            [org_id, graph_id, key],
        )
    column = "props ->> 'name'" if label == "Point" else "name"
    return (
        f"(SELECT min(t.node_key) AS node_key FROM public.{table} t "
        f"WHERE t.org_id = %s AND t.graph_id = %s AND t.{column} = %s "
        "HAVING count(*) = 1)"
        f"{' ' + alias if alias else ''}",
        [org_id, graph_id, key],
    )


def _structural_repoint_statements(
    org_id: str,
    graph_id: str,
    record: Mapping[str, Any],
    columns: Mapping[str, frozenset[str]] | None = None,
    refused: Mapping[str, frozenset[str]] | None = None,
) -> list[Statement] | None:
    """The MOVE a 2b structural `DirectEdgeRepoint` describes (#9972/#9973).

    Returns `None` for every other shape — a 2a direct descriptor, a malformed
    one, or a `delete_only` guard — so those keep the existing path byte for byte
    (a 2a `src` is ALREADY the successor and an upsert `DELETE`s nothing).

    Two statements, in this order, and both gated on the SAME condition: the
    descriptor has an effect only when `src` is (a) TERMINAL and (b) has a
    surviving successor — i.e. exactly `_src_is_terminal(src)` and
    `_final(src) != src` on the graph side. Without the gate the pair would
    delete a live point's own edge and re-create it under a successor that a
    dropped supersede fold never produced; with it, the no-move case (a live
    re-created incarnation, a self-supersede) is a clean no-op instead of the
    delete-then-insert churn that erased `confidence` in the measured
    target-only attempt.

    1. `DELETE` the row at the SUPERSEDED point — the resurrection the graph's
       delete-leg removes. Keyed on the resolved target, as the fold's re-query
       is (`MATCH (old)-[r]->(t) WHERE ID(t)=$tid`); `idx` is deliberately NOT in
       the predicate, because the graph deletes the edge by its INTERNAL id (the
       descriptor carries no `idx`, and the structural writers all leave it 0).
    2. `INSERT` the same row at the DIRECT successor — `succ[src]`, the point
       the supersede that emitted this descriptor actually named — WITH the
       descriptor's TRANSFERRED attributes (the `⛔ #10303` block below this
       docstring). ⛔ ONE HOP, not `_final(src)` (the terminal endpoint of the
       chain): the graph's transfer is `supersede_point(old, new)`'s
       `MERGE (new)-[:<rel>]->(t) ON CREATE SET nr += $attrs`, so the edge is
       created at `new` and each LATER supersede carries it one more hop with
       the value it holds at that moment. Resolving straight to the terminal
       collapses a chain's descriptors onto one destination row — and a sibling
       pair's too — and the two shapes then need OPPOSITE precedence: a chain's
       LAST transfer created the final edge (so its payload must win), a sibling
       pair's FIRST did (so its payload must win). One INSERT per descriptor, at
       ITS OWN successor, gets both right for free: the `ON CONFLICT` clause does
       the job `ON CREATE SET` does on the graph side, and the next link's DELETE
       removes the intermediate row exactly as live's supersede removes the
       intermediate edge.
       `ON CONFLICT … DO NOTHING`, not `DO UPDATE`, because since `#10139` the
       graph's transfer is `MERGE (s)-[:<rel>]->(t) ON CREATE SET nr += $attrs`
       (`projection/__init__.py`, the pass-2b structural replay leg): `ON
       CREATE`, so #9417's rule holds — a successor's OWN live value wins — and
       `DO NOTHING` is that SAME precedence on this side of the house. A row the
       successor does not have is INSERTED carrying the retired edge's
       attributes; a row it already has is left untouched, byte for byte,
       exactly as `ON CREATE SET` leaves it. `DO UPDATE` would overwrite the
       successor's value with the retired point's — the measured 0.91 → 0.42
       regression `#9417`'s review P1 exists to prevent — and `<rel>` is the
       INTERPOLATED `etype`, not a parameter: Cypher
       refuses a parameter as a relationship type (`-[:$param]->` is invalid —
       Neo4j Cypher Manual, Syntax → Parameters), which is why the writer
       f-string-formats the type into the pattern. The no-self-edge predicate
       (`destination != resolved target`) mirrors the fold's `E2E-11.6` guard:
       a chain that collapses the endpoints onto one node must not mint
       `(X)-[:aboutPoint]->(X)`.

    ⛔ #10303 (P1): the moved row CARRIES the transferred attributes — the same
    `_DIRECT_EDGE_ATTR_KEYS` set the fold replays, read FLAT off the descriptor
    exactly as `sdk.supersede_point` emits them (`_relation_upsert_statement`'s
    consumer reads an attribute payload the same way, `{k: payload[k] for k in
    rel.attrs}`). Before this, the descriptor carried the values (#10139) and
    this route still wrote an empty row: the journal-rebuild and record-rebuild
    routes then disagreed about the same journal on a data-bearing property,
    which is the `#9553` class this module polices. They are routed by
    `_split_properties`, not by this function: `direction`/`confidence`/
    `weight`/`label`/`batch_id` are COLUMNS of `public.relation`, so they land on
    the row's columns (and the `props` tail stays `{}` for them); a name with no
    column would land in the tail instead. ⚠️ So the record's own `props` tail is
    NOT where to look for the carry — the columns are.

    ⚠️ NAMED RESIDUALS, not fixed here: (a) `sourceVersion` is NOT carried — the
    fold's transfer and replay drop it too (a pre-existing `extractedFrom`
    anchor loss `projection/edges.py` states), so the two routes agree by both
    losing it rather than the record keeping an attribute the graph does not
    have; (b) `#2488`'s
    survivor-filtered `succ` map has no record-side expression, so an id-reuse
    journal (a point re-created live under a used id) is resolved here as
    `point.status` says at the moment the record runs — which is the LIVE order,
    not the rebuild's post-filter one; (c) a CYCLIC supersede does NOT agree
    with the graph route once it carries a descriptor PER HOP. This walk is
    `path`-guarded and moves ONE hop per descriptor (ending at the LAST hop's
    successor), while pass-2b's `_final` returns the first REPEATED node, so
    `_final(src) == src` and its create is SKIPPED — after the delete-leg above
    that guard has already removed the edge the previous hop created, leaving the
    graph with NO edge. The two agree only on the ONE-HOP cycle the PGlite
    `9972-cycle` case pins (both land on that hop's successor, `y`), which is
    what live did; `#2498` makes the multi-hop shape unproducible by a live
    writer (the closing hop is refused — a dead target cannot be
    re-terminalized), so this is a fabricated-journal-only divergence, named
    here rather than described as agreement. Pinned by
    `tests/test_pointsuperseded_rebuild.py`::
    `test_a_cycle_with_a_descriptor_per_hop_loses_the_edge_at_rebuild`.
    """
    etype = record.get("edge_type")
    label = _STRUCTURAL_REL_LABELS.get(etype) if isinstance(etype, str) else None
    if label is None:
        return None
    src, tgt = record.get("src"), record.get("tgt")
    if not (isinstance(src, str) and src and isinstance(tgt, str) and tgt):
        return None  # malformed: the existing path raises the same refusal
    rel_type, _src_label, _src, _tgt_label, _tgt, idx = _relation_coordinates(
        RelationUpsert(), record)
    cte, cte_params = _supersede_chain_cte(org_id, graph_id, src)
    # TWO renderings of the SAME resolution: the DELETE compares one column to a
    # bare SCALAR subquery, the INSERT joins the aliased one as a FROM item. One
    # rule, one helper — the alias is the only difference, so neither can drift.
    tgt_scalar, tgt_scalar_params = _structural_target_resolution(
        org_id, graph_id, label, rel_type, tgt, "")
    tgt_select, tgt_params = _structural_target_resolution(
        org_id, graph_id, label, rel_type, tgt, "tgt")
    # The move exists only for a TERMINAL src with a surviving successor AND a
    # reachable DESTINATION — the DIRECT successor's own row (`depth = 2`). The
    # third conjunct is what makes the pair atomic: a successor whose `point` row
    # the record does not hold (or a path-guarded cycle that never leaves `src`)
    # leaves `dest` empty, so without it the DELETE ran and the INSERT wrote
    # nothing: the row left the superseded point and never landed anywhere.
    moved = ("(SELECT terminal FROM succ WHERE depth = 1) "
             "AND EXISTS (SELECT 1 FROM succ WHERE next_id IS NOT NULL) "
             "AND EXISTS (SELECT 1 FROM succ WHERE depth = 2)")
    delete = Statement(
        f"{cte}DELETE FROM public.relation "
        "WHERE org_id = %s AND graph_id = %s AND rel_type = %s "
        "AND src_label = 'Point' "
        "AND src_key = (SELECT node_key FROM succ WHERE depth = 1) "
        f"AND tgt_label = %s AND tgt_key = {tgt_scalar} "
        f"AND {moved}",
        [*cte_params, org_id, graph_id, rel_type, label, *tgt_scalar_params],
    )
    carried = {k: record[k] for k in _DIRECT_EDGE_ATTR_KEYS if k in record}
    col_values, tail = _split_properties(
        "relation", carried,
        columns=columns if columns is not None else table_columns(),
        refused=refused if refused is not None else refused_props_names(),
    )
    attr_columns = list(col_values)
    insert_columns = ["org_id", "graph_id", "rel_type", "src_label",
                      "src_key", "tgt_label", "tgt_key", "idx",
                      *attr_columns, "props"]
    select_list = ", ".join([
        "%s", "%s", "%s", "'Point'", "dest.node_key", "%s", "tgt.node_key",
        "%s", *(["%s"] * len(attr_columns)), "%s",
    ])
    insert = Statement(
        f"{cte}INSERT INTO public.relation ({', '.join(insert_columns)}) "
        f"SELECT {select_list} "
        "FROM (SELECT node_key FROM succ WHERE depth = 2) dest, "
        f"{tgt_select} "
        "WHERE dest.node_key <> tgt.node_key "
        f"AND {moved} "
        "ON CONFLICT (org_id, graph_id, rel_type, src_label, src_key, "
        "tgt_label, tgt_key, idx) DO NOTHING",
        [*cte_params, org_id, graph_id, rel_type, label, idx,
         *(_jsonb_or_scalar(c, v) for c, v in col_values.items()),
         json.dumps(tail), *tgt_params],
    )
    return [delete, insert]


def _op_dispatch_statements(
    org_id: str,
    graph_id: str,
    dispatch: OpDispatch,
    payload: Mapping[str, Any],
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
) -> list[Statement]:
    op = payload.get("op")
    label = payload.get("label")
    if not isinstance(op, str):
        raise RecordProjectionError("EntityMutated record carries no `op`")
    if op in dispatch.refused_ops:
        raise RefusedJournalOperation(f"EntityMutated op={op!r}: {dispatch.refused_ops[op]}")
    table = dispatch.table_for_label.get(str(label))
    if table is None:
        raise RecordProjectionError(
            f"EntityMutated names label {label!r}, which is not a record node "
            f"label ({sorted(dispatch.table_for_label)})"
        )
    node = Node(table, str(label), NODE_TABLES[table][1])
    state = payload.get("state")
    state = state if isinstance(state, Mapping) else {}
    # The record's identity is the journal `id` — the SAME key the graph fold
    # matches (`_ENTITY_ID_PROP`). Since #9881 that is also the table's `node_key`
    # for every label this dispatch reaches; `state.name`/`state.url` remain a
    # defensive fallback for a LEGACY state record that carries no id (a Source
    # keys `node_key` on `url`, an Object/Subject row written before the key move
    # still resolves through its `name` column).
    key = (payload.get("id") or payload.get("key")
           or state.get("name") or state.get("url"))
    if not isinstance(key, str) or not key:
        raise RecordProjectionError(f"EntityMutated op={op!r} carries no key")
    match = _ENTITY_MATCH_COLUMNS.get(table, ("node_key",))
    if op in dispatch.delete_ops:
        return _delete_statements(org_id, graph_id, node, key, match=match)
    if op in dispatch.patch_ops:
        # #9881 — `state` is the write's FULL applied map, and for a rename that
        # map contains the new `name`. It used to be filtered out of the patch
        # because `name` WAS the key: writing it would have moved the key without
        # moving `node_key`, which the schema's `chk_*_node_key_is_contract_key`
        # refuses. The key is the `id` now, so `name` is an ORDINARY mutable
        # column and the applied map is written as it stands — a rename moves the
        # name and nothing else.
        #
        # ⛔ A rename names its entity by its STABLE ID and nothing else. The
        # `state.name` fallback below exists for state ops that name a legacy
        # node; for a rename it would be exactly backwards ("rename the entity
        # whose name is X to Y" is the pre-#3590 model, and it cannot say WHICH
        # of two same-name entities was renamed). Refuse it here, where the
        # record is known, instead of emitting a patch that matches 0 rows.
        if op == "rename" and not isinstance(payload.get("id"), str):
            raise RecordProjectionError(
                "EntityMutated op='rename' carries no `id`: a rename names its "
                "entity by the STABLE id (#3590/#9881), so a record that only "
                "carries a name cannot say which entity moved, and patching by "
                "name would rename 0 rows — or the wrong one — in silence")
        col_values, tail = _split_properties(
            table, dict(state),
            columns=columns, refused=refused,
            # #10017 R2: `cache_tier` is a placement declaration, so the
            # `EntityMutated` path must drop it too. Without it a `state` map
            # carrying the tier writes `object.props`/`subject.props`, and a
            # record-sourced rebuild sets it on the node while the journal
            # replay strips it (`_META_KEYS`) — the two rebuild routes diverging
            # on a property, which is the class this change exists to close.
            drop=dispatch.never_store.get(table, frozenset()) | {"cache_tier"},
        )
        return [_patch_statement(org_id, graph_id, node, key, col_values, tail,
                                 match=match)]
    raise RecordProjectionError(
        f"EntityMutated op={op!r} is neither patched nor deleted nor declared "
        "refused — an undeclared op is the silent-skip defect"
    )


#: Record properties that are authority ONLY where the table has a column for
#: them. `actor_user_id` is §17.5-PRIMARY on the Episodic (session) layer and
#: nowhere else; the graph's `_META_KEYS` skip-set strips it from every other
#: node, so storing it in another table's props tail would make a record-sourced
#: rebuild diverge from a journal replay (see `_split_properties`).
_STORED_ONLY_IF_COLUMNED = frozenset({"actor_user_id"})

#: The envelope name a producer may NEST its node under instead of flattening it.
#: ONE entry, because only one exists: the fold unwraps exactly this carrier —
#: `inner = event.get("event", event)` (`projection/entities.py`, and the same
#: shape in `projection/__init__.py`) — and `ConversationMiner` is the producer
#: that uses it. Do NOT widen this list speculatively: an entry no fold reads
#: would materialize a row the folder never makes, which is the case the
#: snapshot guard below exists to prevent.
_NESTED_NODE_CARRIERS = ("event",)


def _nested_node_carrier(record: Mapping[str, Any],
                         key_from: str) -> Mapping[str, Any] | None:
    """The nested mapping that CARRIES *key_from*, or None.

    Only consulted when the envelope itself cannot be keyed, so it can widen the
    accepted producer shapes and never re-point a record that already resolves.
    """
    for name in _NESTED_NODE_CARRIERS:
        nested = record.get(name)
        if isinstance(nested, Mapping) and nested.get(key_from) is not None:
            return nested
    return None


#: `(record type, envelope key, rel type, source label, target label)` — the
#: edges a record carries as a *structural directive on its envelope* rather
#: than as a node property, so no `NodeSnapshot` column can hold them.
#:
#: `contains_session` (#3947) is the capture turn loop's
#: `(:Session)-[:CONTAINS]->(:Point)` link: the SDK/hosted writer MERGEs the
#: edge live and journals the session id on the `PointAdded`/`OperatorAdded`
#: ENVELOPE (`sdk.py::_write_capture_turns`), deliberately NOT in the point
#: payload, because a Point property of that name would let a caller forge a
#: container link on replay.
#:
#: ⛔ THE GAP THIS CLOSES (R5 / #9553, class 2). `_RECORD_ONLY_KEYS` drops
#: `contains_session` — correctly, it must never become a node prop — and that
#: drop ALSO dropped the EDGE: the record materialized the turn Point and no
#: `relation` row, so a **journal** rebuild restored the CONTAINS link (its fold
#: reads the same envelope key, `projection/__init__.py` →
#: `_upsert_point_edges(contains_session=…)`) while a **record-sourced** rebuild
#: did not. A capture therefore landed `relation` rows = 0 and lost the whole
#: session container on the S3/S9 route.
#:
#: The set is keyed on the RECORD TYPE, not on the node label, because the fold
#: honours the key for exactly `PointAdded`/`OperatorAdded`
#: (`projection/__init__.py::apply`). A label-keyed rule would let a stray
#: `contains_session` on any other Point record mint a `relation` row the graph
#: never wrote — the record richer than the graph, which is the one direction
#: Interface A forbids.
#:
#: ⛔ NOT covered here, named so the next reader does not infer it. Point-PROPERTY
#: edges are carried by their OWN declarations, not this one:
#: `_POINT_PROPERTY_RELATIONS` (class 1, `extractedFrom`) and
#: `_OPERATOR_RELATION_RECORDS` (class 3 — the operator's `IMPL`/`NAND`/`hasPart`,
#: its reverse `INPUT`, and the canonical `mitigated_by` mirror).
#:
#: The STRUCTURAL predicates (`ownedBy`/`hasPart`/`dependsOn`/`wasDerivedFrom`,
#: `references`, the bare `about*` mints) are class 4, and they are carried by the
#: `DirectEdgeCreated` descriptor this module already handles below — the missing
#: half was the PRODUCER, not this fold. Since `#9553` the structural-edge lane's
#: two mints journal that record (`projection/edges.py::_record_structural_edges`
#: ← `TortoiseSDK._record_structural_edge`), so those edges land a `relation` row
#: with `source_label`/`target_label` naming the endpoints' tables. What stays
#: missing is NAMED, not implied:
#:   * `tags` → `TAGGED`: the target is a `:Tag` node and `NODE_TABLES` has no
#:     `tag` table, so the record holds no `(Tag, key)` identity for
#:     `relation.tgt_key`'s composite FK to point at — a schema decision, not a
#:     projection one;
#:   * a `:Source`-anchored `references` (`link_source_to_entity`): the replay
#:     fold addresses endpoints by sweeping `EPISTEMIC_LABELS` on `p.id`, and a
#:     `:Source` is url-keyed and outside it. The lane therefore DECLINES to
#:     journal such a write and WARNs once, with the reason, rather than filing a
#:     record the journal route cannot replay (`projection/edges.py`,
#:     `RECORD_ADDRESSABLE_EDGE_LABELS`).
#:   * `about*` when no `EntityLinked` record exists is now carried by the bare
#:     `create_about_edge` mint, on the same `DirectEdgeCreated` route.
#:   * ⚠️ **THE `idx` RESIDUAL (#9844), measured, not inferred.** A
#:     `DirectEdgeCreated` descriptor carries no `idx`, so
#:     `_relation_coordinates` defaults it to `0` — while `relation`'s conflict key
#:     INCLUDES `idx` and an operator's own `hasPart`/`IMPL`/`NAND` rows carry
#:     `idx = enumerate(inputs)`. Mint the edge the operator's record already
#:     carries at `idx >= 1` and the record gains a SECOND row for ONE graph edge
#:     (measured: `create_operator("composedOf", a, [b])` then
#:     `create_edge("hasPart", op, b)` leaves the LIVE graph unchanged at two rows
#:     — the `MERGE` is property-agnostic and dedups onto the `idx=1` edge — while
#:     the record route computes `(op, b, hasPart, 0)` beside `(op, b, hasPart, 1)`).
#:     The PRODUCER cannot fix it (the mint takes no property parameter, §9.6, and
#:     cannot read the `idx` the existing edge carries), so the fix belongs to this
#:     layer and needs its own test: insert only when no row already exists for the
#:     `(org, graph, rel_type, src, tgt)` triple, for the `DirectEdgeCreated` family
#:     only, so `DirectEdgeRepoint` and the re-linking families are untouched.
#:     Filed as **#9844**.
_ENVELOPE_RELATIONS: tuple[tuple[str, str, str, str, str], ...] = (
    ("PointAdded", "contains_session", "CONTAINS", "Session", "Point"),
    ("OperatorAdded", "contains_session", "CONTAINS", "Session", "Point"),
)


def _writable_endpoint_id(value: Any) -> bool:
    """True when the FOLD would key a node on `value` — `_writable_container_id`.

    ⛔ The fold is the authority. `_upsert_point_edges` gates a
    `contains_session` on truthiness (`isinstance(contains_session, str) and
    contains_session`) and then hands it to `_link_session`, whose gate is
    `projection._writable_container_id` — `_writable_id` (rejects a non-str, the
    empty string, a NUL and a lone surrogate) PLUS the BLANK refusal. This
    predicate must stay value-for-value equal to it; a stricter rule here would
    make the record POORER than the graph and a looser one would make it
    RICHER, and both are rebuild-lane divergences.

    The BLANK refusal is measured, not stylistic, and it is why an earlier draft
    of this module could not simply call `_writable_id` (#9553 review P2): on an
    indexed graph (`_ensure_indexes` creates `CREATE INDEX FOR (s:Session) ON
    (s.id)`) FalkorDB's equality filter does not match a whitespace-only string
    (#9599) — `MATCH (s:Session) RETURN s.id` lists `'   '` while `MATCH
    (s:Session {id:'   '})` returns 0 — so `_link_session` re-MERGEs a DUPLICATE
    `:Session` and then lands NO edge, while Postgres (no such quirk) would
    happily hold the row. Streaming the row anyway would rebuild a relation the
    graph never wrote, so the refusal lives in `_writable_container_id` and BOTH
    folds use it.

    Restated rather than imported because this module is loaded STANDALONE —
    the PGlite harness `spec_from_file_location`s it, so a package-relative
    import raises there. The two are pinned EQUAL over a value table by
    `tests/test_record_projection.py::test_the_envelope_endpoint_gate_is_the_folds_own_rule`,
    so a drift in either one is a red.

    The NUL / lone-surrogate refusals are not cosmetic on THIS side either:
    Postgres refuses a NUL in `text` and the driver cannot encode a lone
    surrogate, so admitting either would turn a fold SKIP into a failed
    replay.
    """
    if not isinstance(value, str) or not value or not value.strip():
        return False
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False  # lone surrogate
    return True


def _envelope_relation_statements(
    org_id: str,
    graph_id: str,
    record: Mapping[str, Any],
    record_type: Any,
    target_key: str,
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
) -> list[Statement]:
    """The `relation` rows a record's ENVELOPE directs, as upserts.

    Returned AFTER the node upsert by the caller so the target endpoint's
    `node_identity` row exists before the insert — `relation.src_key`/`tgt_key`
    are composite FKs into it, and an FK violation here lands on
    `record_write_failures`, invisible without #9551 (the ordering half of
    G4).

    The SOURCE endpoint is resolved, never minted (`_endpoint_resolution`), so a
    session the record does not hold produces ZERO rows instead of a phantom
    identity — the fold's own `"absent"` verdict. That is the honest answer for
    a pre-#3664 journal (no `SessionRecorded`, so no `session` row) and it
    matches the fold only partially: `_link_session` MINTs a minimal
    `:Session` stub on that path. The record does NOT copy that fallback —
    minting a node from an edge directive would materialize a `session` row no
    record created, which is the divergence class #9078 review refused.

    An endpoint id is admitted by `_writable_endpoint_id` — the fold's own
    rule (`projection._writable_container_id`), NOT a stricter one. A
    non-string, an EMPTY or BLANK string, a NUL or a lone surrogate emits
    nothing; the blank case is the one that LOOKS like a divergence and is not
    (see that predicate's docstring: an indexed graph cannot match a
    whitespace-only key, so the fold lands no edge for it either).

    A SAME-ID pair (`contains_session == the Point's id`) is NOT refused. The
    fold's `_link_session` MATCHes `(s:Session {id:$sid})` and
    `(t:Point {id:$tid})` as two separate BOUND variables, so with `sid == tid`
    the differing labels still bind two distinct nodes and the MERGE writes the
    edge. `relation` holds it just as well — `node_identity` (and the composite
    FK behind both endpoint columns) is keyed on `(label, key)`, so
    `(Session, x)` and `(Point, x)` are two identities, not one. An earlier
    draft refused this pair on the false premise that "one node cannot be both
    ends"; the producer cannot emit it today (`_capture_turn_id` suffixes the
    session id) but the record's job is to mirror the fold, not to encode
    today's producer.
    """
    statements: list[Statement] = []
    for rel_type_of, env_key, rel_type, src_label, tgt_label in _ENVELOPE_RELATIONS:
        if rel_type_of != record_type or tgt_label != "Point":
            continue
        source = record.get(env_key)
        if not _writable_endpoint_id(source):
            continue
        statements.extend(_relation_upsert_statement(
            org_id, graph_id, RelationUpsert(),
            {
                "edge_type": rel_type,
                "src": source,
                "tgt": target_key,
                "source_label": src_label,
                "target_label": tgt_label,
            },
            columns=columns, refused=refused,
        ))
    return statements


#: R5 / #9553 **class 1** — the edges the graph fold derives from a Point's own
#: PROPERTIES, keyed `(record type, prop, rel_type, target label)`.
#:
#: ⛔ WHY A DECLARATION AND NOT A SPECIAL CASE. `_upsert_point_edges`
#: (`projection/entities.py`) is the fold's ONE edge writer. What it derives from
#: the payload:
#:
#:   * `extractedFrom` → `(Point)-[:extractedFrom]->(Source)`, one edge per ref,
#:     fanned out many-to-many (#3263) by `_link_source` — **carried HERE**;
#:   * `contains_session` → CONTAINS — carried by the ENVELOPE directive above
#:     (`_ENVELOPE_RELATIONS`, class 2, #9594);
#:   * `p.operator` → `IMPL`/`NAND`/`hasPart` + the reverse `INPUT` + the
#:     canonical `mitigated_by` reverse (`edges.py::_create_edges`) — **carried
#:     by `_OPERATOR_RELATION_RECORDS`, class 3**: the carrier is the Point's own
#:     descriptor, and the pair is emitted together there, so the record's edge
#:     census agrees with the journal's for both halves. Its endpoints resolve
#:     across FOUR labels (:Point/:Subject/:Object/:Event, #7902), which is why
#:     that family builds its own row statement (labels as subselect columns);
#:   * `aboutEntities` → `about*` — NOT carried here: `_create_about_edges`
#:     resolves a NAME through a Subject→Object→Event→Source→Point cascade
#:     against the LIVE graph, so the record would have to re-run that cascade
#:     over its own tables to pick a label, and the `EntityLinked` record (#3664)
#:     already carries the edge whenever the capture resolved it. The stored-vs-
#:     derived ruling for `about*` is KEEP (owner, 2026-09-23), which is about
#:     the STORE, not about inventing a second resolver here;
#:   * `tags` → `TAGGED` — NOT carried here: the target is a `:Tag` node, and
#:     `NODE_TABLES` has no `tag` table, so the record holds no `(Tag, key)`
#:     identity for `relation.tgt_key`'s composite FK to point at. That family
#:     needs a record-side `:Tag` node first, which is a schema decision, not a
#:     projection one.
#:
#: The ONE family carried above (`extractedFrom`) covers the materialisation
#: types below; the rest are named above and in `04-projection.md` §3 /
#: `08-hosted-prerequisites.md` §R5 so the next reader meets the boundary
#: instead of inferring it from a green suite.
#:
#: ⛔ FOUR TYPES, NOT TWO — AND THE FOLD'S TWO ROUTES DO NOT AGREE ABOUT THEM.
#: `apply()` materialises a Point from FOUR arms, and three of them reach
#: `_upsert_point_edges` (which reads `extractedFrom` off the payload with no
#: type gate of its own):
#:
#:   * `PointAdded` / `OperatorAdded` → `self._upsert(p)`;
#:   * `PointPromoted` → `self._upsert(gated_promote_payload(p, …))`;
#:   * `OperatorPromoted` → `self._upsert(_promotion_point_with_operator(p))`.
#:
#: Neither wrapper drops `extractedFrom` (`gated_promote_payload` withholds
#: `status` only; `_promotion_point_with_operator` only SYNTHESISES the nested
#: `operator` key), and promote emitters journal the full `get_point(id)`
#: property set (`sdk.promote_point`) — so a promote-only record (the #2256
#: capture case, where the promote IS the operator's only durable record)
#: re-derives the provenance edge on the `apply()` route. `_JOURNAL_POINT_
#: MATERIALIZATION_TYPES` names exactly these four, and the record follows it:
#: declining the two promote rows would leave the record POORER than the graph
#: on the engine `rebuild` / `recover_from_log` actually run.
#:
#: The two rebuild routes genuinely diverge here — `rebuild_all`'s pass 2 gates
#: on `PointAdded`/`OperatorAdded` only (`projection/__init__.py`, "Pass 2 edge
#: wiring") — so on THAT route the promote-derived edge is not re-derived at all.
#: The record follows `apply()` deliberately: it is the chronological, journal-
#: order engine, and carrying the edge cannot be wrong when the record holds the
#: payload that derives it (the row is FK-bound to a Point and a Source the
#: record itself materialised — nothing is minted). The pass-2 gate is named in
#: `04-projection.md` §3 so the narrower route is visible rather than inferred.
#: A normal `PointAdded` → `PointPromoted` journal converges on ONE row: the
#: relation's UNIQUE identity keys the upsert, and a promote snapshot carrying a
#: NEW ref adds a second row exactly as the fold's `MERGE` adds a second edge.
_POINT_PROPERTY_RELATIONS: tuple[tuple[str, str, str, str], ...] = (
    ("PointAdded", "extractedFrom", "extractedFrom", "Source"),
    ("OperatorAdded", "extractedFrom", "extractedFrom", "Source"),
    ("PointPromoted", "extractedFrom", "extractedFrom", "Source"),
    ("OperatorPromoted", "extractedFrom", "extractedFrom", "Source"),
)


#: ``tortoise/source_identity.py``'s canonicalisation constants, restated for the
#: STANDALONE load (the PGlite harness `spec_from_file_location`s this module, so
#: a package-relative import raises there). Pinned to the originals over a value
#: table by `tests/test_record_projection.py::test_the_source_canonicaliser_is_the_folds_own`.
_SOURCE_NETWORK_SCHEMES: frozenset = frozenset({"http", "https"})
_SOURCE_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}
_SOURCE_TRACKING_PARAMS: frozenset = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_creative_format",
    "utm_marketing_tactic", "utm_source_platform",
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid",
    "mc_cid", "mc_eid", "igshid", "mkt_tok", "_hsenc", "_hsmi",
    "yclid", "twclid", "vero_id",
})


def _normalize_source_url(url: Any) -> Any:
    """`source_identity.normalize_source_url` — the Source's canonical identity.

    The fold does NOT address a ``:Source`` by the raw ``extractedFrom`` ref:
    `_mint_source_stub` calls `resolve_source_key` FIRST, which canonicalises the
    ref and returns the ``url`` of any Source already registered under that
    canonical form, and only then MERGEs. So a point whose ref is a TRACKING-PARAM
    or trailing-slash VARIANT of a registered Source lands its edge on the
    REGISTERED node, not on the variant spelling.

    A record that matched the raw ref alone would therefore resolve onto no row
    and land NOTHING while the journal rebuild restored the edge — a silent
    provenance loss on exactly the family this change carries (#9553 review P1).
    This restates the canonicaliser so the record can mirror that resolution.

    Restated rather than imported for the same STANDALONE reason as the sibling
    helpers; pinned value-for-value to the original over a table that exercises
    every branch (network/non-network, tracking params, query sort, default port,
    IPv6, fragment, trailing slash, malformed) by
    `test_the_source_canonicaliser_is_the_folds_own`.
    """
    if not isinstance(url, str) or not url:
        return url
    scheme_sep = url.find(":")
    if scheme_sep <= 0:
        return url
    scheme = url[:scheme_sep].lower()
    if scheme not in _SOURCE_NETWORK_SCHEMES:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.scheme.lower() not in _SOURCE_NETWORK_SCHEMES:
        return url
    host = (parts.hostname or "").lower()
    if not host:
        return url
    if ":" in host:  # IPv6 literal — urlsplit strips the brackets
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        return url
    if port is not None and port != _SOURCE_DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"
    path = parts.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        path = "/"
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _SOURCE_TRACKING_PARAMS
    ]
    kept.sort()
    return urlunsplit((scheme, host, path, urlencode(kept), ""))


def _point_raw_refs(value: Any) -> list[Any]:
    """`_point_source_refs`'s normalisation: the refs the fold would FAN OUT.

    A bare `str` is ONE ref (never iterated character-wise), any other value is a
    sequence, and falsy members are skipped. The CONTAINER gate is
    `_link_source`'s: a `str` or a `list`/`tuple` is a ref collection, and
    anything ELSE fans out to NOTHING (`#8171` review P1: a dict was
    `list()`-ed into its KEYS and each key minted as its own `:Source` —
    invented provenance). Restated rather than imported because this module is
    loaded STANDALONE (`spec_from_file_location`, where a package-relative
    import raises).
    """
    if isinstance(value, str):
        refs = [value]
    elif isinstance(value, (list, tuple)):
        refs = list(value)
    else:
        return []
    return [ref for ref in refs if ref]


def _point_property_refs(value: Any) -> list[str]:
    """The refs of a point PROPERTY that can land a `relation` row, per ref.

    On top of :func:`_point_raw_refs`, `_link_source` skips a non-`str` member
    and `_mint_source_stub` refuses an id `_writable_id` rejects (empty, NUL,
    lone surrogate) and then MATCHes `(s:Source {url:$url})` with `None`, landing
    no edge. Those refs land nothing on either route; `_writable_endpoint_id` is
    that same predicate PLUS the blank refusal, and blank is the measured #9599
    case: `:Source(url)` carries a range index (`_RANGE_INDEX_LABEL_PROPS`), so
    an indexed equality filter does not match a whitespace-only key and the fold
    writes no edge for one either.
    """
    return [ref for ref in _point_raw_refs(value)
            if _writable_endpoint_id(ref)]


def _point_source_versions(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """The `{raw ref: hash}` anchors the fold stamps on those edges (#5256).

    `_link_source` writes `r.sourceVersion` from the Point's OWN
    `sourceVersionTransit` carrier, through the shared `_point_source_transit`
    selector, whose decision is FOUR rules and all four are restated here:

    1. the carrier is accepted ALL-OR-NOTHING — one malformed pair (not a
       2-element `[str, str]` with both members non-blank) and the payload
       contributes NO anchor, rather than the well-formed half of one;
    2. no `extractedFrom` ref at all ⇒ no anchor (the carrier is a transit for
       an edge that will be written, so a stray carrier writes nothing);
    3. a pair naming a ref this Point does NOT carry is dropped
       (`kept = [[k, h] for k, h in pairs if any(k == r for r in refs)]`), so a
       carrier whose every pair is foreign yields NO anchor;
    4. a pair is keyed by the RAW ref spelling — the journal-stable key — and a
       DUPLICATE keeps the LAST pair (both sides build their map by
       comprehension over the same pair order).

    Restated rather than imported for the same standalone reason as
    :func:`_point_raw_refs`; the two are pinned equal over a value table by
    `tests/test_record_projection.py::test_the_source_version_selector_is_the_folds_own`.
    """
    pairs = snapshot.get("sourceVersionTransit")
    if not isinstance(pairs, (list, tuple)) or not pairs:
        return {}
    for pair in pairs:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2
                and isinstance(pair[0], str) and pair[0].strip()
                and isinstance(pair[1], str) and pair[1].strip()):
            return {}
    refs = _point_raw_refs(snapshot.get("extractedFrom"))
    if not refs:
        return {}
    return {pair[0]: pair[1] for pair in pairs if pair[0] in refs}


def _point_property_relation_statements(
    org_id: str,
    graph_id: str,
    record_type: Any,
    snapshot: Mapping[str, Any] | None,
    target_key: str,
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
) -> list[Statement]:
    """The `relation` rows a record's own POINT PROPERTIES derive (class 1).

    Returned AFTER the node upsert by the caller — `relation`'s endpoints are
    composite FKs into `node_identity` — and both endpoints are RESOLVED from the
    record's own node rows, never minted.

    The target is matched on the Source's canonical identity, mirroring
    `_mint_source_stub` — never on the entity `id` fallback. The fold does not
    address a `:Source` by the raw ref: `_mint_source_stub` calls
    `resolve_source_key` first (canonicalise, then take the `url` of any Source
    already registered under that canonical form) and only then MERGEs
    `(s:Source {url:$url})`. So the record matches `node_key = <raw ref>`, OR
    `node_key = <canonical ref>` (the graph's adopt-on-touch by the canonical
    spelling — `source_identity.py`: "for candidate in ([url, canonical] …)"),
    OR `canonical_url = <normalised ref>`, PREFERRING the canonical match and
    ordered by `node_key` within it — which is `resolve_source_key`'s own order
    (it queries `canonicalUrl` first and returns there; the raw spelling is
    consulted afterwards, adopt-on-touch) and its own tiebreak
    (`ORDER BY s.url LIMIT 1`, and `chk_source_node_key_is_contract_key` forces
    `node_key = url`). The ORDER BY is expressed rather than inherited: with two
    Sources for one canonical identity the preference decides WHICH node the
    edge lands on, and a plain `ORDER BY node_key` over the union would be free
    to pick the node the fold would not have. Matching the raw ref ALONE
    resolved a variant onto no row and landed NOTHING while the journal rebuild
    restored the edge — a silent provenance loss on this very family (#9553
    review P1). The `id` fallback is still excluded: it would attach the row to
    a document row the fold never chose (see `_endpoint_resolution`'s `match`).

    ⛔ TWO HONEST GAPS, stated rather than papered over — neither is the
    variant case above, which is now RESOLVED to the fold's own answer. A THIRD
    one, about which RECORDS carry this family at all, is named at the end.

    (a) ORDERING. The row lands only when the Source's own record precedes the
    Point's — the capture path materialises the session Source BEFORE extraction
    (`sdk.py::_materialize_session_source`), and a document/link ref names a
    Source journaled when it was created, so the live order holds. For a journal
    that orders them the other way the record's insert finds no `source` row and
    lands nothing, while `rebuild_all`'s pass 2 re-derives edges AFTER every node
    is folded and would restore it. That is the `absent` verdict this projection
    gives every relation rather than a fabricated endpoint, and a record-sourced
    rebuild is expected to be fed in journal order (#9190).

    (b) NO RECORD ROW AT ALL. A ref naming a Source the record never saw — the
    fold's `_link_source` MINTS an UNJOURNALED stub for one (`_mint_source_stub`,
    #5024) — has no `source` row and so no `(Source, key)` identity for
    `relation.tgt_key`'s composite FK. The record lands no row, by design: minting
    a node from an edge directive would materialise a Source no record created,
    the record richer than the graph. Same absent verdict, same reason, as the
    CONTAINS envelope family (#9594).

    (c) THE FOLD'S TWO ROUTES DISAGREE ABOUT THE RECORD SET, and the record
    follows `apply()`. `apply()` re-derives this family from FOUR point-
    materialisation arms (`PointAdded`, `OperatorAdded`, `PointPromoted`,
    `OperatorPromoted` — none of the payload wrappers drops `extractedFrom`),
    while `rebuild_all`'s pass 2 re-derives it for the two CREATION types only
    (`projection/__init__.py`, "Pass 2 edge wiring"). `_POINT_PROPERTY_RELATIONS`
    declares all four: the record is a durable capture of the journal, so
    declining the promote rows would make it POORER than the graph on the
    chronological engine `rebuild` / `recover_from_log` run — the direction
    Interface A forbids — for exactly the #2256 population whose promote IS the
    operator's only record. The divergence that follows is on the pass-2 side
    (a full rebuild re-derives no promote edge), named here and in
    `04-projection.md` §3 rather than left to be discovered.

    ⛔ AND ONE RULE THE RECORD NOW SHARES WITH `_anchor_on_create`: the anchor
    is written ON CREATE ONLY — a replay of the same edge neither advances nor
    fills `source_version` (`_relation_upsert_statement` omits it from the
    update list, which is literally `ON CREATE SET`). For two refs of one Point
    resolving onto the SAME Source with different hashes, the first ref's anchor
    therefore wins on both routes, instead of the last INSERT winning.
    """
    if not isinstance(snapshot, Mapping):
        return []
    declared = [(prop, rel_type, tgt_label)
                for type_of, prop, rel_type, tgt_label in _POINT_PROPERTY_RELATIONS
                if type_of == record_type]
    if not declared:
        return []
    statements: list[Statement] = []
    for prop, rel_type, tgt_label in declared:
        versions = _point_source_versions(snapshot)
        for ref in _point_property_refs(snapshot.get(prop)):
            # Mirror `resolve_source_key`: the raw ref, the canonical SPELLING,
            # and its canonical FORM, with the CANONICAL match preferred (that
            # is the order it queries in: canonicalUrl first, return there;
            # the raw spelling is the adopt-on-touch fallback). All THREE
            # candidates, because the resolver adopts a pre-canonical node by
            # either spelling (`source_identity.py`: "for candidate in ([url,
            # canonical] …)") — a target that bound only the raw spelling
            # would leave a row filed under the canonical spelling invisible,
            # and the edge would land nowhere while a variant `DocumentCreated`
            # folded onto that very row (#9956). It is the same triple
            # `_source_key_resolution` binds for the ROW KEY, so one identity
            # cannot resolve to two different rows inside this layer.
            # `node_key` is the tiebreak inside each tier, exactly as its
            # `ORDER BY s.url LIMIT 1`.
            canonical = _normalize_source_url(ref)
            payload: dict[str, Any] = {
                "edge_type": rel_type,
                "src": target_key,
                "tgt": ref,
                "source_label": "Point",
                "target_label": tgt_label,
            }
            # #5256: the read-version anchor the fold stamps ON CREATE — and the
            # record writes through ON CONFLICT, so the anchor is the ONE
            # attribute the upsert never rewrites (`_relation_upsert_statement`
            # drops it from the update list). It is a PRIMARY column
            # (`relation.source_version` → the cache projection's
            # `sourceVersion` property, `cache_projection.placement_columns`),
            # not a props key, and absent/empty means NO anchor on both routes.
            anchor = versions.get(ref)
            if anchor:
                payload["source_version"] = anchor
            statements.extend(_relation_upsert_statement(
                org_id, graph_id, RelationUpsert(), payload,
                columns=columns, refused=refused,
                tgt_match=("node_key", "node_key", "canonical_url"),
                tgt_values=(ref, canonical, canonical),
                tgt_order_by="node_key",
                tgt_prefer=("canonical_url", canonical),
            ))
    return statements


#: The epistemic label set, in the order the record's endpoint resolver tries it
#: (#7902: an operator endpoint may carry ANY of these four, owner ruling
#: #7813). Restated for the STANDALONE load and pinned to the original —
#: `tortoise/live.py::EPISTEMIC_LABELS` — by
#: `tests/test_record_projection.py::test_the_epistemic_label_order_is_the_folds_own`,
#: because the ORDER decides which label a row is filed under when one id
#: somehow exists in two tables and the fold's own probe is unordered.
_EPISTEMIC_LABELS: tuple[str, ...] = ("Point", "Subject", "Object", "Event")

#: #9553 **class 3** — the typed edge `_create_edges` derives from an operator's
#: own `{op_type, inputs}` descriptor (`projection/edges.py`). The mapping is the
#: fold's literal, restated: an op_type the fold does not know mints no typed
#: edge at all, only the reverse `INPUT`.
_OPERATOR_REL_TYPES: dict[str, str] = {
    "NAND": "NAND",
    "IMPL": "IMPL",
    # The four composition spellings all land the ONE `hasPart` predicate.
    "composedOf": "hasPart",
    "decomposesInto": "hasPart",
    "contains": "hasPart",
    "wraps": "hasPart",
}

#: How the operator descriptor is read from a record's snapshot. `nested` is
#: `snapshot["operator"]`; `synthesized` additionally mirrors
#: `projection._promotion_point_with_operator`, which rebuilds the nested shape
#: from FLAT props because promotion emitters journal `get_point` snapshots.
_NESTED_OPERATOR = "nested"
_SYNTHESIZED_OPERATOR = "synthesized"

#: `(record type, how its operator descriptor is read)` — EXACTLY the records
#: whose materialisation calls `_upsert_point_edges` → `_create_edges`.
#:
#: ⛔ WHY THESE FOUR. `apply()` reaches the edge writer from four
#: point-materialisation arms (`projection/__init__.py:11563` for
#: `PointAdded`/`OperatorAdded`, `:11643` for `PointPromoted`, `:11673` for
#: `OperatorPromoted`), while `rebuild_all`'s pass 2 re-derives them for the two
#: CREATION types only. The record follows `apply()` — the chronological engine
#: `rebuild` / `recover_from_log` run — because declining a promote row would
#: make the record POORER than the graph on the engine that folds in order, for
#: exactly the #2256 population whose promote IS the operator's only record.
#: The divergence that follows is on the pass-2 side (a full rebuild re-derives
#: no promote edge) and is named in `04-projection.md` §3, not left to be found.
#:
#: ⚠️ `PointPromoted` is `nested`, NOT `synthesized`: `apply()` applies the
#: promotion synthesis (`_promotion_point_with_operator`) to the
#: `OperatorPromoted` arm ONLY, so a flat `PointPromoted` snapshot reaches
#: `_create_edges` with no `operator` key and the fold writes no edge. Reading
#: `op_type`/`inputs` there would mint a row the graph never wrote.
_OPERATOR_RELATION_RECORDS: tuple[tuple[str, str], ...] = (
    ("PointAdded", _NESTED_OPERATOR),
    ("OperatorAdded", _NESTED_OPERATOR),
    ("PointPromoted", _NESTED_OPERATOR),
    ("OperatorPromoted", _SYNTHESIZED_OPERATOR),
)


def _operator_descriptor(
    record_type: Any,
    snapshot: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """The `{op_type, inputs}` descriptor `_create_edges` would read, or None.

    ⛔ BOTH gates are the fold's, and the second one is a `dict` test on purpose.
    A truthy non-`dict` degrades to no edges there (`_create_edges`:
    `if not isinstance(op, dict): return`) and to no rows here, so a custom
    `Mapping` — which passes `isinstance(x, Mapping)` but is not a `dict` — must
    NOT be accepted: the record would be richer than the graph.

    The synthesis gate is KEY PRESENCE, not `None`-ness. `_upsert_point_edges`
    reaches `_create_edges` only `if p.get("operator")` (so a falsy descriptor
    withholds the call), and `_promotion_point_with_operator` refuses to
    synthesize when the key EXISTS at all (`"operator" in p`). A snapshot
    carrying `{"operator": None, "is_operator": True, "op_type": "IMPL"}` is
    therefore edge-less on BOTH fold halves — reading `descriptor is None` here
    would synthesize one and emit `IMPL`/`INPUT` rows the graph never wrote, a
    silent topology change from a record the fold ignores.
    """
    how = dict(_OPERATOR_RELATION_RECORDS).get(record_type) \
        if isinstance(record_type, str) else None
    if how is None or not isinstance(snapshot, Mapping):
        return None
    descriptor = snapshot.get("operator")
    # `_promotion_point_with_operator(p)`: `if p.get("is_operator") and
    # p.get("op_type")` → `{"op_type": p["op_type"], "inputs": p.get("inputs")
    # or []}`. Restated verbatim: the polarity and the `or []` both decide
    # whether a real edge is restored.
    if (how == _SYNTHESIZED_OPERATOR and "operator" not in snapshot
            and snapshot.get("is_operator") and snapshot.get("op_type")):
        descriptor = {"op_type": snapshot["op_type"],
                      "inputs": snapshot.get("inputs") or []}
    return descriptor if isinstance(descriptor, dict) else None


def _writable_epistemic_id(value: Any) -> bool:
    """True when the FOLD would key an operator input on `value`.

    `projection._writable_id`, restated: a non-`str`, the EMPTY string, a NUL and
    a lone surrogate are refused — and a WHITESPACE-ONLY value is deliberately
    ADMITTED (#9599), which is the difference between this predicate and
    :func:`_writable_endpoint_id`.

    ⛔ WHY THE DIFFERENCE IS LOAD-BEARING, and why the Session family's rule
    cannot be reused here. `_writable_container_id` refuses a blank key because
    `_link_session` addresses the container with an INLINE property MATCH
    (`(s:Session {id:$sid})`), which FalkorDB answers from the `:Session(id)`
    range index, and an indexed equality filter does not match a
    whitespace-only string (#9599). This family's probe is not that shape:
    `_create_edges` addresses an input with a LABEL DISJUNCTION plus a `WHERE`
    (`MATCH (o:Point {id:$oid}), (s) WHERE (s:Point OR … ) AND s.id = $sid`) —
    a SCAN, where the ordinary string comparison applies. MEASURED on the fold
    through this code path (review round 2): a blank input id gets a `:Point`
    stub minted and BOTH edges landed (`IMPL`, `INPUT`), and the same holds when
    a node already carries the blank key.

    So refusing a blank input here would make the record POORER than the graph
    on exactly the edge this change exists to carry — the direction Interface A
    forbids — and would do it on a stated-but-false reason. The refusal set is
    therefore `_writable_id`'s, value for value; pinned by
    `test_the_operator_input_gate_is_the_folds_own_rule`.

    The NUL / lone-surrogate refusals are not cosmetic on THIS side either:
    Postgres refuses a NUL in `text` and the driver cannot encode a lone
    surrogate, so admitting either would turn a fold skip into a failed replay.
    A ≥20-character id bearing one is the case where the fold's own `else`
    branch has no gate and its probe RAISES (measured: `ResponseError: Failed to
    parse query parameter`); the record skips it instead, which is the fail-safe
    half of the same verdict — no edge on either route, and no aborted write.
    """
    if not isinstance(value, str) or not value:
        return False
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False  # lone surrogate
    return True


def _is_non_operator_snapshot(snapshot: Mapping[str, Any]) -> bool:
    """`projection/entities.py::is_non_operator_payload`, restated.

    The polarity is load-bearing: `not p.get("is_operator")` is ALSO true when
    the key is ABSENT, and the EventAPI / extractor / ingest producer emits an
    `OperatorAdded` with no flag at all — that form would authorise a
    `mitigated_by` row for every generic IMPL operator on the main write path.
    Pinned value-for-value against the fold by
    `test_the_mitigation_gates_are_the_folds_own`.
    """
    return snapshot.get("is_operator") is False


def _has_usable_mitigation_strength(snapshot: Mapping[str, Any]) -> bool:
    """`projection/entities.py::has_usable_mitigation_strength`, restated.

    `mitigate_operator`'s own accepted domain: a real `int`/`float` (a `bool`
    included on purpose — the writer accepts one) inside `[0, 1]`. A map / list /
    None is dropped by the writer's passthrough, a non-numeric value persists but
    is unreadable, and an out-of-band value is refused by the writer's band
    check — none of them is a mitigation a live call could have written, so none
    of them may authorise the reconstructed dampening.

    Refusal is TOTAL and fail-safe: `isinstance` admits an `int`/`float`
    SUBCLASS whose comparison raises, and a hand-edited record must land NO row
    rather than abort the record write after the engine has started replaying.
    The band comparison IS the finiteness check (`10**400` fails it by
    comparison, never by `OverflowError`), exactly as the fold states.
    """
    value = snapshot.get("mitigation_strength")
    if not isinstance(value, (int, float)):
        return False
    try:
        return bool(0 <= value <= 1)
    except Exception:
        return False


def _epistemic_endpoint(
    org_id: str,
    graph_id: str,
    journal_id: str,
    alias: str,
) -> tuple[str, list[Any]]:
    """The `(label, node_key)` row an operator input addresses, resolved.

    ⛔ WHY THIS IS NOT `_endpoint_resolution`. That helper takes the LABEL as an
    argument, because every other edge family knows it: an `EntityLinked`
    record names both labels, a `DirectEdgeCreated` descriptor defaults to
    `Point`, and the class-1 target is a `Source`. An operator's input does NOT
    come with one: `_create_edges`
    probes `MATCH (s) WHERE (s:Point OR s:Subject OR s:Object OR s:Event) AND
    s.id = $sid`, so the label is whatever the graph happened to hold. The
    record therefore has to answer label AND key from one lookup, and
    `relation.src_label`/`tgt_label` are bound as VALUES by
    `_relation_upsert_statement` — hence a resolver that returns both, and a
    statement built for it below.

    The probe is the fold's, `s.id = $sid`, over the four tables; first label in
    :data:`_EPISTEMIC_LABELS` order wins. The KEY is each label's canonical one,
    read from :data:`NODE_TABLES` (`Point`/`Subject`/`Object` → `id`, `Event` →
    `event_id`), because `relation.src_key`/`tgt_key` are composite FKs into
    `node_identity` — writing the raw journal id for a Subject would address a
    phantom identity the hard-delete cascade can never reach (the same defect
    `_endpoint_resolution` exists to prevent).

    ⛔ WHICH COLUMN carries `s.id` is per label, and it is NOT always the
    table's `id` column — :data:`_ENTITY_MATCH_COLUMNS` is the one answer, and
    this resolver DERIVES from it rather than restating it (a second spelling is
    how this family drifted onto `event.id` in the first place). Every label here
    agrees with it (`Point` → `node_key`, `Subject`/`Object` → `id`, `Event` →
    `node_key`). `Event` is the label where the two DISAGREE and
    where assuming `id` cost the record the edge: the schema pins
    `event.node_key = event_id` ``NOT NULL`` (`chk_event_node_key_is_contract_key`)
    while `event.id` is a plain nullable column, and the dominant
    `EventRecorded` producers — `mining._make_event` (nested `{eventId: …}`, no
    `id`) and `github_map.issue_to_event`/`pr_to_event` (`eventId` only) — never
    fill it. The graph still keys such a node `id == eventId`
    (`_upsert_event`'s unconditional `props["id"] = eid`, `eid = inner.get("id")
    or inner.get("eventId")`), so the fold's `s.id = $sid` probe matched and
    wrote both edges while an `id`-matching record write filed no `relation` row
    at all: R5 class 3, silently open on the Event arm (`#9553` review).

    ⚠️ RESIDUAL, named: a payload carrying BOTH `id` and a DIFFERENT `eventId`
    would key the record row on the `eventId` while the fold's MERGE key is the
    `id` — the record and the graph would then disagree about the node's
    identity, which is upstream of this resolver and not something a match
    column can repair. No producer emits that shape (both dominant producers
    emit `eventId` alone), so it is stated, not handled.

    ⚠️ BOUND, stated rather than discovered — and it is the record that is
    POORER, not two arbitrary-but-equal choices. The fold's probe BINDS `s` to
    every node holding that id (`_create_edges`' `MATCH … WHERE (s:Point OR
    s:Subject OR s:Object OR s:Event) AND s.id = $sid` then `MERGE`), so a
    duplicated id yields one typed/`INPUT` edge pair PER node, while this
    resolver files the row under ONE label (`ORDER BY pref LIMIT 1`). Ids are
    ULIDs minted per node, so two nodes share one only through a hand-written
    graph. The row is absent-but-one rather than wrong; a one-row-per-candidate
    -label form cannot express "every label that matched", so the bound is the
    record's, not a choice the graph also makes.
    """
    selects: list[str] = []
    params: list[Any] = []
    for pref, label in enumerate(_EPISTEMIC_LABELS):
        table = _LABEL_TABLES[label]
        # Each label's CANONICAL key, taken from :data:`NODE_TABLES` — the table's
        # declared key column, the same one `_key_value` writes.
        #
        # ⛔ DERIVED, not spelled. This line used to read
        # `"node_key" if label in ("Point","Event") else "name"`, a SECOND
        # declaration of the key contract, and #9881's key move (Object/Subject
        # `name` → `id`) made it wrong: the resolver selected the row's `name`
        # while `node_identity` held its `id`, so every operator edge over a
        # Subject/Object landed a `tgt_key` the FK refused. `Point`→`id` and
        # `Event`→`event_id` are the same columns the old expression reached (a
        # Point's `id` IS its `node_key`, `event_id` is the Event's pin), so this
        # is one rule for every label rather than a special case per label.
        key_column = NODE_TABLES[table][1]
        # The FOLD's match column is `s.id`, and WHICH record column carries it
        # is `_ENTITY_MATCH_COLUMNS`' own answer — DERIVED here, never restated.
        # A second spelling of that fact is exactly how the Event arm came to
        # match `event.id`, the one column its dominant producers leave NULL, and
        # silently filed no row (#9553 review). Matching the key also keeps the
        # per-(label, id) index instead of a column no producer must fill.
        # This family resolves ONE column per label: the row carries one journal
        # id per endpoint, and the pair-per-input fan-out assumes the fixed
        # parameter block. A multi-column entry is REFUSED rather than silently
        # read as "first".
        match_columns = _ENTITY_MATCH_COLUMNS[table]
        if len(match_columns) != 1:
            raise RecordProjectionError(
                f"the {label} endpoint resolves on {len(match_columns)} columns "
                f"{match_columns} — an operator input is ONE journal id, so "
                "this family can match exactly one"
            )
        match_column = match_columns[0]
        selects.append(
            f"SELECT '{label}' AS node_label, {key_column} AS node_key, "
            f"{pref} AS pref FROM public.{table} "
            f"WHERE org_id = %s AND graph_id = %s AND {match_column} = %s"
        )
        params.extend([org_id, graph_id, journal_id])
    return (
        f"(SELECT node_label, node_key, pref FROM ({' UNION ALL '.join(selects)}) "
        f"epistemic ORDER BY pref LIMIT 1) {alias}",
        params,
    )


def _operator_relation_statement(
    org_id: str,
    graph_id: str,
    rel_type: str,
    src_id: str,
    tgt_id: str,
    idx: int,
) -> Statement:
    """One `relation` row whose endpoints carry their OWN labels.

    The same shape `_relation_upsert_statement` emits, with two differences the
    family forces:

    * the labels come from the endpoint subselects, so they are COLUMNS of the
      `SELECT` rather than parameters — an operator input's label is a fact the
      graph holds, not one the record may assume;
    * `ON CONFLICT … DO NOTHING`. These rows carry NO attribute: the fold writes
      `{idx}` on the typed/reverse edges and nothing at all on `mitigated_by`
      (`projection/edges.py::_create_edges`), and `idx` is part of the conflict
      key — so a replay has nothing to correct, and `DO NOTHING` is exactly the
      idempotent form. (`DO UPDATE SET props = EXCLUDED.props`, which the other
      families use, is for rows whose attributes a later record legitimately
      supersedes; it would be a no-op here and would misstate the contract.)
    """
    src_select, src_params = _epistemic_endpoint(org_id, graph_id, src_id, "src")
    tgt_select, tgt_params = _epistemic_endpoint(org_id, graph_id, tgt_id, "tgt")
    return Statement(
        "INSERT INTO public.relation (org_id, graph_id, rel_type, src_label, "
        "src_key, tgt_label, tgt_key, idx) "
        "SELECT %s, %s, %s, src.node_label, src.node_key, tgt.node_label, "
        "tgt.node_key, %s "
        f"FROM {src_select}, {tgt_select} "
        "ON CONFLICT (org_id, graph_id, rel_type, src_label, src_key, "
        "tgt_label, tgt_key, idx) DO NOTHING",
        (org_id, graph_id, rel_type, idx, *src_params, *tgt_params),
    )


def _operator_relation_statements(
    org_id: str,
    graph_id: str,
    record_type: Any,
    snapshot: Mapping[str, Any] | None,
    target_key: str,
) -> list[Statement]:
    """The `relation` rows an OPERATOR's own descriptor derives (class 3).

    ⛔ THE GAP THIS CLOSES (R5 / #9553, class 3). An operator's `IMPL`/`NAND`/
    `hasPart` edge, its reverse `INPUT`, and (for a mitigation) the canonical
    `mitigated_by` mirror exist ONLY as `_create_edges` Cypher, driven from the
    `{op_type, inputs}` descriptor in the Point's snapshot. The record
    materialised the operator's `point` row and emitted no `relation` row, so a
    **journal** rebuild restored the epistemic topology while a
    **record**-sourced rebuild restored the claims and none of their structure —
    the loss the epic's "destroyed and rebuilt without data loss" acceptance is
    about, since `compute_operator_weight` reads exactly these edges.

    For each input, mirroring `_create_edges` VALUE FOR VALUE:

    * a KNOWN `op_type` → `(operator)-[:<REL> {idx}]->(input)` AND
      `(input)-[:INPUT {idx}]->(operator)`;
    * an UNKNOWN `op_type` → the reverse `INPUT` only (`rel_type` is None);
    * `IMPL` from a NON-operator Point carrying a usable
      `mitigation_strength` → additionally `(input)-[:mitigated_by]->
      (operator)`, the canonical reverse `mitigate_operator` writes (#5048,
      #4937). Both gates are the fold's own, restated above.

    ⚠️ Iteration mirrors `enumerate(op.get("inputs") or [])` verbatim, INCLUDING
    its tolerance of a container the fold never guards: a dict iterates its keys
    and a string its characters there, and the same members are read here. They
    simply land no row, because the fold mints a STUB node for an input that does
    not resolve and a stub has no record row to resolve from — the `absent`
    verdict this projection gives every relation, never a minted endpoint.
    """
    descriptor = _operator_descriptor(record_type, snapshot)
    if descriptor is None:
        return []
    try:
        rel_type = _OPERATOR_REL_TYPES.get(descriptor.get("op_type"))
    except TypeError:
        # An unhashable `op_type` (a corrupt record) raises out of `dict.get`,
        # and this function is reached from a fold declared TOTAL — a record
        # write must never abort mid-replay after the engine has started. The
        # fold raises on the same payload; the record fails SAFE instead (no
        # typed edge, and the reverse `INPUT` below, which is what an unknown
        # op_type gets there too).
        rel_type = None
    mitigation = (
        rel_type == "IMPL"
        and _is_non_operator_snapshot(snapshot)
        and _has_usable_mitigation_strength(snapshot)
    )
    try:
        members = list(enumerate(descriptor.get("inputs") or []))
    except TypeError:
        # A non-iterable `inputs` is a malformed record: the fold would raise on
        # it, and a record write must never abort mid-replay after the engine has
        # started — no rows, no invented endpoints (fail-safe, like every other
        # refusal on this path).
        return []
    statements: list[Statement] = []
    for idx, source in members:
        # The fold's own member gate: a non-string member is malformed and
        # skipped (`if not isinstance(src, str): continue`), and an unwritable
        # id is skipped. The id predicate is `_writable_id`'s, NOT the
        # blank-refusing container rule — see :func:`_writable_epistemic_id`:
        # this family's probe is a label-disjunctive SCAN, so the fold really
        # does land the edges for a blank key, and refusing it here would make
        # the record poorer than the graph.
        if not isinstance(source, str) or not _writable_epistemic_id(source):
            continue
        if rel_type is not None:
            statements.append(_operator_relation_statement(
                org_id, graph_id, rel_type, target_key, source, idx))
            statements.append(_operator_relation_statement(
                org_id, graph_id, "INPUT", source, target_key, idx))
            if mitigation:
                statements.append(_operator_relation_statement(
                    org_id, graph_id, "mitigated_by", source, target_key, 0))
        else:
            statements.append(_operator_relation_statement(
                org_id, graph_id, "INPUT", source, target_key, idx))
    return statements


def mutations_for(
    record: Mapping[str, Any],
    *,
    org_id: str,
    graph_id: str,
    columns: Mapping[str, frozenset[str]] | None = None,
    refused: Mapping[str, frozenset[str]] | None = None,
) -> list[Statement]:
    """The Postgres mutations for ONE journal record. **Total by construction.**

    Raises :class:`UnknownJournalRecordType` for a record type the registry
    does not declare, and :class:`RefusedJournalOperation` for a declared
    refusal. Both are failures — neither is a skip.

    The record's DECLARED mutation comes first; the §9.1 narrative home
    (:func:`_narrative_statements`, #9555) is appended when THIS record
    carries the narrative or the capture counts that home is keyed on. The
    append is per-record and idempotent, so a replay in journal order builds
    the same row the live path built.
    """
    return [
        *_declared_mutations(
            record, org_id=org_id, graph_id=graph_id,
            columns=columns, refused=refused,
        ),
        *_narrative_statements(record, org_id, graph_id),
    ]


def _declared_mutations(
    record: Mapping[str, Any],
    *,
    org_id: str,
    graph_id: str,
    columns: Mapping[str, frozenset[str]] | None = None,
    refused: Mapping[str, frozenset[str]] | None = None,
) -> list[Statement]:
    """The mutation :data:`RECORD_MUTATIONS` declares for ONE record."""
    columns = columns if columns is not None else table_columns()
    refused = refused if refused is not None else refused_props_names()
    record_type = record.get("type")
    mutation = RECORD_MUTATIONS.get(record_type) if isinstance(record_type, str) else None
    if mutation is None:
        raise UnknownJournalRecordType(
            f"{record_type!r} has no declared record mutation — Interface A is "
            "TOTAL: an event with no handler is a failure, not a skip (#5285's "
            "rule applied to the record). Declare it in RECORD_MUTATIONS."
        )
    effect = mutation.effect
    if isinstance(effect, NoRecordEffect):
        return []
    if isinstance(effect, OpDispatch):
        return _op_dispatch_statements(org_id, graph_id, effect, record,
                                       columns, refused)
    if isinstance(effect, RelationUpsert):
        if record.get("delete_only") is True:
            # #2489/#7969: a `delete_only` descriptor is the graph's pass-2b
            # delete of a resurrected phantom edge. An UPSERT here re-created
            # the edge the graph removed; a DELETE is the faithful mutation.
            return [_relation_delete_statement(org_id, graph_id, effect, record)]
        if record_type == "DirectEdgeRepoint":
            # #9972/#9973: the STRUCTURAL (2b) shape means MOVE — resolve the
            # target by its replay key, remove the row at the superseded point,
            # and re-create it at the DIRECT successor live named. `None` = not that shape (the 2a
            # direct descriptor, whose `src` is already the successor), and it
            # keeps the plain upsert below byte for byte.
            moved = _structural_repoint_statements(
                org_id, graph_id, record, columns, refused)
            if moved is not None:
                return moved
        return _relation_upsert_statement(org_id, graph_id, effect, record,
                                          columns, refused)
    if isinstance(effect, RelationDelete):
        return [_relation_delete_statement(org_id, graph_id, effect, record)]
    if isinstance(effect, NodeSnapshot):
        node = effect.node
        _node_table(node, columns)
        snapshot = record.get(effect.path) if effect.path is not None else None
        if effect.path is not None and not isinstance(snapshot, Mapping):
            raise RecordProjectionError(
                f"{record_type!r} carries no `{effect.path}` snapshot to "
                "project — registering from a record that does not carry "
                "the node would materialize a row the journal never wrote"
            )
        # Envelope key FIRST (every producer that carries one is unchanged),
        # then the snapshot's — the capture writer puts the id ONLY in the
        # snapshot (#9078, measured; see `_key_value`).
        try:
            key = _key_value(
                node, record, effect.key_from,
                fallback=snapshot if isinstance(snapshot, Mapping) else None)
        except RecordProjectionError:
            # A producer may NEST its node under its own envelope key instead of
            # flattening it — `ConversationMiner` emits
            # `{type: "EventRecorded", event: {...}}`, and the fold handles both
            # carriers explicitly (`projection/entities.py`: "Handles both nested
            # (…event:{eventId:…}) and flat (eventId at top level) formats
            # transparently"). Without this leg every mined event was refused
            # (#9078 review, measured) — the record would lose them in silence.
            nested = _nested_node_carrier(record, effect.key_from) if effect.path is None else None
            if nested is None:
                raise
            key = _key_value(node, nested, effect.key_from)
            props = {k: v for k, v in nested.items() if k not in _RECORD_ONLY_KEYS}
            props.setdefault(effect.key_from, key)
        else:
            if effect.path is None:
                props = {k: v for k, v in record.items() if k not in _RECORD_ONLY_KEYS}
                props.setdefault(effect.key_from, key)
            else:
                props = {k: v for k, v in snapshot.items() if k not in _RECORD_ONLY_KEYS}
                props[effect.key_from] = key
        col_values, tail = _split_properties(node.table, props, columns=columns,
                                            refused=refused, drop=effect.drop)
        if node.key_column not in col_values:
            col_values[node.key_column] = key
        # ⛔ THE `:Source` ROW KEY IS A RESOLUTION, NOT THE RECORD'S RAW SPELLING
        # (#9956 — the record half of #5140 finding 12, whose graph half landed
        # in #9952). The graph folds a URL-variant document identity onto ONE
        # `:Source`; this layer keyed the row on the record's raw id, so a
        # `DocumentCreated` whose id was a variant of an already-registered
        # source planned a SECOND `public.source` row for one graph node
        # (measured on Postgres 16: 2 rows / 1 node), and a record-sourced
        # rebuild resurrected the duplicate in silence.
        #
        # `_source_key_resolution` is this layer's SQL twin of
        # `source_identity.resolve_source_key` — the SAME resolution the edge
        # endpoints below already apply, per its own docstring — applied to the
        # ROW KEY. It is a subquery, so it is answered at EXECUTION time against
        # the rows earlier statements of this replay already wrote: journal
        # order is the fold's order, and a live single-record write resolves
        # against the committed rows the same way. The fallback inside
        # `COALESCE` is the raw key — the graph's own step 4 (MERGE a new node).
        #
        # It applies to EVERY `source` NodeSnapshot (`SourceCreated`,
        # `DocumentCreated`, `SourceVersioned`), not just documents: the graph
        # resolves on every source writer (`_upsert_source`, `_mint_source_stub`,
        # `link_source_to_entity`, `_upsert_document`), and a record that
        # resolved only one of them would drift on the others. Non-network ids
        # (`session:<id>`, `corpus://…`, `doc_<path>`) come back from the
        # canonicaliser unchanged and resolve onto themselves.
        #
        # ⚠️ `chk_source_node_key_is_contract_key` makes the `url` COLUMN equal
        # `node_key`, so `url` must take the resolved key too — the raw spelling
        # is the identity this row is filed UNDER, not a column of it. Where the
        # graph keeps the raw spelling it keeps it in `urlAliases`, which this
        # layer does not populate: that is #9710's class (a non-constant property
        # missing on a rebuilt node), NOT this issue's (a row that should not
        # exist at all), and it is named here so the loss is not silent. The
        # same sentence covers the `id` column, which the upsert overwrites with
        # the folded record's id where the fold `coalesce`s.
        #
        # ⚠️ AND THE COLLISION THIS MAKES REACHABLE ON THE RECORD, the same one
        # the graph half names (`_upsert_document`): a document id whose canonical
        # form equals an EXISTING non-document `:Source` now folds onto that row
        # too, so the record's `public.source` row gains `document_kind`/`title`
        # exactly as the node gains `documentKind`/`title`. It is not a new rule
        # here — the identically-spelled form already behaved this way, and #9952
        # pinned the graph side as CURRENT behaviour — but a VARIANT now reaches
        # it. Whether promotion should also move the type axis is #9536's
        # contract, not this projection's.
        #
        # ⚠️ REPLAY AXIS, stated where the next reader will ask: this changes
        # what a plan COMPUTES, never the meaning of a row already written. No
        # re-key, no backfill, no migration — an existing record keeps every row
        # it has (a STORE that already holds the duplicate keeps it until
        # someone coalesces it deliberately; a rebuild from an empty record now
        # yields ONE row, where it used to yield two).
        resolved_key: str | _SqlExpr = key
        if node.table == "source":
            resolved_key = _source_key_resolution(org_id, graph_id, key)
            col_values[node.key_column] = resolved_key
        # #9553 review P1: the Source's CANONICAL identity is a pure function of
        # its MERGE key (`normalize_source_url(url)`), and the record's
        # `canonical_url` column is fed by NO payload:
        # `projection/entities.py::_EntityHandlers._SOURCE_HANDLED` puts
        # `canonicalUrl` among the writer-DERIVED keys that `_persist_extra_props`
        # drops, so no journal line can express it. Left unpopulated, the column
        # is dead weight AND the class-1 resolver below cannot mirror
        # `source_identity.resolve_source_key`, which is what makes a URL-VARIANT
        # `extractedFrom` ref land on the registered Source in the graph.
        # Deriving it here is the same rule the fold applies
        # (`_mint_source_stub` sets `canonicalUrl = normalize(key)` on create,
        # `coalesce`d on match), stated from the record's own key — so the two
        # rebuild routes agree on the node's identity instead of only on its url.
        #
        # ⛔ DELIBERATE, PINNED DERIVE — stated, not lurking. `canonical_url` is
        # derived for EVERY `source` row, including a `DocumentCreated` one. The
        # value is the pure canonical form of the row's own key.
        #
        # #5140 finding 12 UPDATE: the live `_upsert_document` fold now WRITES
        # `canonicalUrl` too (it adopts the shared `resolve_source_key`), so the
        # PROPERTY divergence this note used to record is gone. #9956 (above)
        # closed the second axis — the ROW KEY — by resolving it through this
        # layer's own SQL twin of that resolver, so a URL-VARIANT
        # `DocumentCreated` now FOLDS onto the registered row instead of
        # planning a second one. The derive is still UNCONDITIONAL, and for a
        # sharper reason than before: the resolution above MATCHES on
        # `canonical_url`, so a `source` row without it would be invisible to the
        # very subquery that now decides every source row's key (and, for edges,
        # to `_relation_upsert_statement`'s `tgt_match=("node_key",
        # "canonical_url")`). Its value is the pure canonical form of the
        # record's own key, so it is the same value the resolved key would give.
        # Pinned by `test_a_document_source_row_derives_its_canonical_identity_too`.
        if (node.table == "source"
                and "canonical_url" in columns.get("source", frozenset())):
            col_values.setdefault("canonical_url", _normalize_source_url(key))
        return [
            _identity_statement(org_id, graph_id, node, resolved_key),
            _upsert_statement(org_id, graph_id, node, resolved_key, col_values,
                              tail),
            # AFTER the identity + node upsert: an edge endpoint must already
            # be in `node_identity` or the composite FK refuses the insert.
            *_envelope_relation_statements(
                org_id, graph_id, record, record_type, key, columns, refused),
            # AND the edges this record's own PROPERTIES derive (R5 class 1).
            # Same ordering rule for the same reason; the target Source row is
            # resolved from the record, never minted.
            *_point_property_relation_statements(
                org_id, graph_id, record_type,
                snapshot if isinstance(snapshot, Mapping) else None,
                key, columns, refused),
            # AND the epistemic topology this record's own OPERATOR descriptor
            # derives (R5 class 3): the typed edge, its reverse INPUT, and the
            # canonical mitigated_by mirror. Same ordering rule again, and the
            # endpoints are resolved across the four epistemic labels — never
            # minted, so an input the fold stubs lands no row.
            *_operator_relation_statements(
                org_id, graph_id, record_type,
                snapshot if isinstance(snapshot, Mapping) else None, key),
        ]
    if isinstance(effect, NodePatch):
        node = effect.node
        key = _key_value(node, record, effect.key_from)
        props: dict[str, Any] = dict(effect.literals)
        if effect.from_payload:
            props.update({k: v for k, v in record.items()
                          if k not in _RECORD_ONLY_KEYS and k in effect.from_payload})
        elif effect.from_payload is None:
            # `PointRevised` / `OperatorAnnotated`: the extras ride the flat
            # payload VERBATIM, so every non-record key is a node property —
            # except a key that is the rename's own source (`new_content`
            # names the revised `content`; it is not a property of its own).
            renamed_sources = {src for _, src in effect.renames}
            props.update({k: v for k, v in record.items()
                          if k not in _RECORD_ONLY_KEYS
                          and k not in renamed_sources})
        for column, source_key in effect.renames:
            if source_key in record:
                props[column] = record[source_key]
        col_values, tail = _split_properties(node.table, props, columns=columns,
                                            refused=refused)
        return [_patch_statement(org_id, graph_id, node, key, col_values, tail)]
    if isinstance(effect, NodeDelete):
        node = effect.node
        key = _key_value(node, record, effect.key_from)
        statements = _delete_statements(org_id, graph_id, node, key)
        for companion, template in effect.companion_deletes:
            statements += _delete_statements(
                org_id, graph_id, companion, template.format(key=key)
            )
        return statements
    if isinstance(effect, NodesDelete):
        statements = []
        for key in _key_list(record, effect.keys_from):
            statements += _delete_statements(org_id, graph_id, effect.node, key,
                                             match=effect.match)
        return statements
    raise RecordProjectionError(
        f"{record_type!r} declares an effect this engine does not implement: "
        f"{type(effect).__name__}"
    )


# ════════════════════════════════════════════════════════════════════════════
# §9.1's narrative home (#9555, R7) — the SECONDARY projection
# ════════════════════════════════════════════════════════════════════════════
# The narrative is NOT a node (§9.1: "not in the graph, not a `Document` node,
# not a `Point`") so it has no `node_identity` row and no `NodeSnapshot` — that
# effect emits an identity row first, by design. It is keyed on the SESSION
# instead, and it is built by TWO different records: the narrative-carrying
# `:Source`/`:Event` record owns the text, and the capture's `SessionRecorded`
# owns the counts. The one rule that makes those two idempotent in either order
# is that each upsert names only ITS OWN columns in the `DO UPDATE SET`.

#: The record's narrative table (§9.1 / migration 20261023000001).
NARRATIVE_TABLE = "session_narrative"


#: The payload keys naming the session a narrative/count record belongs to.
#: Both spellings are live: `sessionId` on the hosted commit's `:Source`/`:Event`
#: records (hosted_api's §4.1 derived path), `session_id` on the capture's own.
_SESSION_COORDINATE_KEYS: tuple[str, ...] = ("sessionId", "session_id")

#: The payload key carrying §9.1's narrative — the commit's own name for the
#: "connected prose form of a captured session", written onto the transcript
#: `:Source`/`:Event` by the layer-1 commit.
_NARRATIVE_KEY = "story_arc"

#: The INTEGER fields the narrative home reproduces beside the text.
_NARRATIVE_INT_KEYS: tuple[str, ...] = ("capture_redactions", "turn_count")

#: The largest value the record's `integer` columns accept. A count above it
#: fits the carrier's own `jsonb` column and would raise `integer out of range`
#: INSIDE the carrier's transaction — see the projection's guard.
_INT4_MAX = 2**31 - 1


def _session_coordinate(record: Mapping[str, Any]) -> str | None:
    """The session this record is a narrative/count carrier for, if any.

    Two spellings, because two producers name it differently: `sessionId` on
    the commit's `:Source`/`:Event` records, `session_id` on the capture's own.
    A record whose DECLARED node IS the Session (`SessionRecorded`) names it by
    `id` — read from the registry rather than guessed, so an ordinary Point
    (whose `id` is a point) can never be mistaken for a session.
    """
    for key in _SESSION_COORDINATE_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    mutation = RECORD_MUTATIONS.get(record.get("type"))
    node = getattr(mutation.effect, "node", None) if mutation else None
    if isinstance(node, Node) and node.table == "session":
        value = record.get("id")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _narrative_upsert_statement(
    org_id: str,
    graph_id: str,
    session_id: str,
    values: Mapping[str, Any],
    resets: Sequence[str] = (),
) -> Statement:
    """Upsert the narrative row, touching ONLY the columns this record owns.

    THE SET LIST IS THE POINT. If either carrier wrote the whole column set
    (NULLs for the fields it does not carry), the second record to arrive
    would ERASE the first one's half — an order-dependent record, i.e. a
    replay that is not idempotent and a live/rebuild divergence.

    ``resets`` are columns THIS record carries but cannot represent (a count the
    INTEGER refuses). They are written NULL on conflict rather than left alone:
    the count is this carrier's own column, rebuilt by every capture, so
    "carried but unrepresentable" must read as *no count stated* — leaving the
    previous capture's number in place would serve a value the latest record
    contradicts, which is the same false claim as writing 0.

    ⛔ THAT RESET APPLIES ONLY WHEN THE SAME RECORD ALSO CARRIES A REPRESENTABLE
    VALUE. `_narrative_statements` returns `[]` when a record's whole payload
    falls outside this home, so a COUNTS-ONLY record (the shape
    `{"type": "SessionRecorded", "id": …, "capture_redactions": {"total": 7}}`)
    emits no statement at all and an existing row keeps the previous capture's
    number. That is a deliberate refusal, not the unconditional rule the rest of
    this paragraph would otherwise promise, and the reason is the row: emitting
    the reset without a value means an INSERT with no payload column, which on a
    FRESH session is exactly the all-NULL row this home refuses to invent — it
    reads as "session known, no text", a claim no record made. The cost is
    bounded and unreachable from today's producers: `turn_count` is
    `len(windowed)` and `capture_redactions` a summed span count (both far inside
    `[0, _INT4_MAX]`), `session.capture_redactions` has been `integer` since
    `20261022000001`, and a count is meaningful only beside the text whose
    carrier writes it — that carrier carries a representable value, so the reset
    fires there.
    """
    columns = ["org_id", "graph_id", "session_id", *values, "props"]
    params: list[Any] = [
        org_id, graph_id, session_id,
        *(_jsonb_or_scalar(c, v) for c, v in values.items()),
        json.dumps({}),
    ]
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(
        [f"{c} = EXCLUDED.{c}" for c in values] + [f"{c} = NULL" for c in resets]
    )
    sql = (
        f"INSERT INTO public.{NARRATIVE_TABLE} ({', '.join(columns)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (org_id, graph_id, session_id) DO UPDATE SET "
        f"{updates}, "
        f"props = public.{NARRATIVE_TABLE}.props || EXCLUDED.props"
    )
    return Statement(sql, tuple(params))


def _narrative_statements(
    record: Mapping[str, Any],
    org_id: str,
    graph_id: str,
) -> list[Statement]:
    """The §9.1 narrative-home upsert a record contributes, if it carries one.

    KEYED ON THE SHAPE, NOT ON THE RECORD TYPE. Several producers write a
    narrative onto a record (`SourceCreated`, `DocumentCreated`, the layer-1
    commit's `:Event` record) and a fourth would arrive from a producer nobody
    listed; a type whitelist misses exactly that one, and a missed narrative is
    the silent gap #9555 exists to close. So the carrier contract IS the shape:
    a record that names its session AND carries a field this table holds.

    Returns `[]` for the overwhelming majority of records (an ordinary Point, a
    relation, a session with no narrative yet), which is why appending it to
    every record's batch is free.

    THE ENVELOPE MAY NEST. `ConversationMiner` emits
    `{type: "EventRecorded", event: {…}}` and the declared mutation already
    accepts that shape (`_nested_node_carrier`), so the shape rule must read it
    too — a carrier this projection cannot see is the silent drop it exists to
    prevent.
    """
    carrier: Mapping[str, Any] = record
    if _session_coordinate(record) is None:
        nested = _nested_node_carrier(record, "eventId")
        if nested is None:
            return []
        carrier = nested
    session_id = _session_coordinate(carrier)
    if session_id is None:
        return []

    # A DELETED SESSION TAKES ITS NARRATIVE WITH IT. The record's own delete path
    # (`hosted_api._delete_session_sync`) emits, in one journal segment, one
    # `EntityMutated` (`op=delete`) per removed turn Point PLUS one
    # `SessionDeleted`; `SessionDeleted`'s declared mutation removes the session
    # row and the `session:` Source stub (the turns go with their own records,
    # `delete_ops={"delete"}`). This table is not a node table (no
    # `node_identity` parent), so it cannot ride `companion_deletes` and carries
    # its own statement — attached to `SessionDeleted` because that is the
    # record that means "this session no longer exists". Without it
    # §9.4's verbatim path would serve the text of a session the graph no longer
    # has, and a same-id re-capture would inherit the deleted incarnation's
    # narrative. Journal order still decides: a narrative record arriving AFTER
    # the delete re-creates the row, which is the graph fold's own semantics.
    if record.get("type") == "SessionDeleted":
        return [Statement(
            f"DELETE FROM public.{NARRATIVE_TABLE} "
            "WHERE org_id = %s AND graph_id = %s AND session_id = %s",
            (org_id, graph_id, session_id),
        )]

    values: dict[str, Any] = {}
    # THE SAME RULE AS THE COUNTS BELOW: project what the column can hold, never
    # coerce, and never fail the carrier. ⚠️ The primary path does NOT refuse a
    # non-string `story_arc` — `_jsonb_or_scalar` serialises a list/dict into the
    # text column, so `public.source.story_arc` can itself hold a non-prose value
    # (a pre-existing defect of the carrier, filed separately). This home simply
    # refuses to propagate a non-string as §9.1 prose, and refuses to raise: a
    # value the column cannot hold faithfully is an ABSENCE here.
    narrative = carrier.get(_NARRATIVE_KEY)
    if isinstance(narrative, str) and narrative.strip():
        values["narrative"] = narrative

    # ⛔ `summary` is projected ONLY from a record that ALSO carries the
    # narrative. The capture's own session `:Source` sets `summary` to the
    # transcript's first substantive utterance (`sdk._session_source_metadata`),
    # which is NOT the S1 narrative — projecting it unconditionally would let an
    # order-independent row hold a prose snippet in the narrative's companion
    # column, and would make `SessionText.verbatim_available` true for a session
    # whose narrative is unknown. One carrier owns both columns, so they cannot
    # mean different things.
    if "narrative" in values:
        summary = carrier.get("summary")
        if isinstance(summary, str) and summary.strip():
            values["summary"] = summary

    resets: list[str] = []
    for key in _NARRATIVE_INT_KEYS:
        value = carrier.get(key)
        # PROJECTED ONLY WHEN THE VALUE FITS THE COLUMN — and never coerced.
        # The secondary home must not be able to FAIL the carrier's own write:
        # `session.capture_redactions` is jsonb and accepts shapes AND an
        # unbounded integer (`[2**31, inf)`), both of which this table's INTEGER
        # refuses (`#9264`/R4 is the shape half, one table over), so raising or
        # overflowing here would turn an additive projection into a NEW way for
        # a live capture to fail — the INSERT raises `integer out of range`
        # inside the carrier's own transaction, taking the row and the journal
        # position with it. A value the column cannot hold is therefore an
        # ABSENCE in this home — the column reads NULL and the reader returns
        # None — never a 0 (a false claim about the capture) and never a coerced
        # value (which would hide the disagreement). The field's authority
        # remains the carrier's own column.
        if (isinstance(value, int) and not isinstance(value, bool)
                and 0 <= value <= _INT4_MAX):
            values[key] = value
        elif value is not None:
            # CARRIED, BUT UNREPRESENTABLE. On a FRESH row an omitted column is
            # already NULL; on a RE-CAPTURE the previous value would survive in
            # the `DO UPDATE` branch's untouched column, so the reset is what
            # makes "no count stated" true in both orders.
            resets.append(key)

    # A record whose whole content this home cannot represent contributes NO row:
    # a carrier whose only payload is an unrepresentable count is not a narrative,
    # and inserting an all-NULL row would turn an honest absence into a row that
    # reads as "session known, no text". `turn_count`/`capture_redactions` are
    # only meaningful beside the text they belong to, and the carrier that
    # rebuilds them carries that text.
    if not values:
        return []
    return [_narrative_upsert_statement(org_id, graph_id, session_id, values,
                                        resets)]


# ════════════════════════════════════════════════════════════════════════════
# The journal-position record, and the replay driver
# ════════════════════════════════════════════════════════════════════════════


def _journal_row(record: Mapping[str, Any], seq: int, org_id: str,
                 graph_id: str) -> Statement:
    """The record of WHAT the record consumed — `public.journal_event`.

    Keyed on `(org_id, graph_id, seq)`, which the schema calls *"the
    idempotency key: applying a position twice is a no-op"*. `ON CONFLICT DO
    NOTHING` because the table is append-only by TRIGGER: an update would be
    refused, and correctly so — a consumed position is not revisable.
    """
    return Statement(
        "INSERT INTO public.journal_event "
        "(org_id, graph_id, seq, event_type, payload, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())) "
        "ON CONFLICT (org_id, graph_id, seq) DO NOTHING",
        (
            org_id, graph_id, seq, record.get("type"),
            json.dumps({k: v for k, v in record.items() if k not in ENVELOPE_KEYS}),
            record.get("ts"),
        ),
    )


@dataclass
class ReplayReport:
    """What a replay consumed. A report, never a substitute for the failure."""

    events: int = 0
    statements: int = 0
    no_effects: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (f"replayed {self.events} journal records → {self.statements} "
                f"record mutations ({len(self.no_effects)} declared no-op)")


def _apply_tenant_scope(cursor: Any, org_id: str) -> None:
    """Set the tenant GUC transaction-locally, then VERIFY the whole scope.

    Two properties, one place, because a caller that had to remember either
    would eventually remember only one:

    - the connection IS scoped to ``org_id`` — the read-back (#9600 review
      cycle 1): a ``set_config(..., true)`` on an autocommit connection, a
      renamed GUC, or a pooler that dropped the context sets nothing, and on an
      `UPDATE`/`DELETE`-only plan that is a silent zero-row success;
    - the connection's ROLE cannot bypass the policies that read it (#9600
      review cycle 2): a bypassing DSN satisfies every other check and
      evaluated no tenancy predicate at all.

    Raises :class:`RecordProjectionError` on either, BEFORE a single plan
    statement — or, on the read path, a single row — is touched. A failure here
    is a misconfiguration to refuse, not a degraded mode to continue in.
    """
    cursor.execute(TENANT_GUC_SQL, (org_id,))
    # One round trip for both halves: the GUC read-back and the role posture.
    cursor.execute(TENANT_SCOPE_SQL, (list(THE_RECORD_TABLES),))
    row = cursor.fetchone()
    if not row:
        raise RecordProjectionError(
            "the record's scope could not be verified — the read-back returned "
            "no row, so neither the tenant GUC nor the connecting role's "
            "posture could be read. An unverifiable role is not a verified "
            "one: refusing rather than assuming the connection is "
            "non-bypassing (RLS is the record's tenancy boundary, R6 / epic "
            "#7879, and #9600 review cycle 2)")
    # The row is positional on a plain cursor and mapping-shaped on a
    # `RealDict` one — `read_record_view` advertises both shapes (its `_rows()`
    # handles `Mapping`), so unpacking this by index alone would raise
    # `KeyError: 0` on the read door before its first SELECT. Both columns are
    # aliased (`tenant`, `bypassing`) so the mapping form is readable.
    if isinstance(row, Mapping):
        applied, bypassing = row["tenant"], row["bypassing"]
    else:
        applied, bypassing = row[0], row[1]
    if str(applied) != str(org_id):
        raise RecordProjectionError(
            f"the tenant GUC {TENANT_GUC!r} is {applied!r} after setting it to "
            f"{org_id!r} — refusing to apply the plan unscoped. A "
            "transaction-local set does nothing on a connection in autocommit "
            "mode, and an UPDATE/DELETE gated only by RLS USING would then "
            "affect ZERO rows without raising (#9600 review cycle 1)")
    if bypassing is None:
        raise RecordProjectionError(
            "the connecting role's posture could not be read (no pg_roles row "
            "for current_user) — refusing rather than assuming it is "
            "non-bypassing. RLS is the record's tenancy boundary (R6, epic "
            "#7879), and an unverifiable role is not a verified one (#9600 "
            "review cycle 2)")
    if bypassing is not False:
        raise RecordProjectionError(
            "the record's connection runs as a role that BYPASSES RLS — "
            "superuser, BYPASSRLS, a member of such a role, or the owner of a "
            "record table. RLS is this record's tenancy boundary (R6, epic "
            "#7879), so every read and write on that connection would cross "
            "tenants with the policies never evaluated. Arm "
            "TORTOISE_RECORD_DSN with tortoise_record_app "
            '(docs/infra-runbook.md -> "The record\'s connecting role"); '
            "refusing rather than applying a replay that cannot be scoped "
            "(#9600 review cycle 2)")


def scope_connection(conn: Any, org_id: str) -> Any:
    """The R6 tenant scope on ``conn`` — for the WRITE door and the READ door.

    ``replay()`` and ``cache_projection.read_record_view()`` are the record's two
    doors and they share ONE DSN (``TORTOISE_RECORD_DSN``). The prologue lived
    only on the write door, so an ARMED reconciler WOULD have read ZERO rows
    from every record table — the policies treat a missing GUC as "no rows",
    never as an error — landing every cache key ``extra_in_cache`` with
    ``missing_in_cache = 0`` — the chokepoint-violation / second-writer
    signature, not a lag — and WOULD have exited ``3`` ("divergence
    OBSERVED") on a coherent pair. That is the counterfactual, not an observed
    event: R1 has not armed the DSN. A wrong verdict from the one observer §17.2 relies on is
    worse than no verdict, so BOTH doors install the scope through this ONE
    function (#9600 review cycle 2, P1).

    Returns the cursor it used, so a caller continues on the same handle.
    """
    cursor = conn.cursor()
    _apply_tenant_scope(cursor, org_id)
    return cursor


def replay(records: Sequence[Mapping[str, Any]], conn: Any, *, org_id: str,
           graph_id: str, start_seq: int = 1,
           position: bool = True) -> ReplayReport:
    """Replay a journal into the record, on the CALLER's transaction.

    `§3`: *"One transaction covers the journal and its projection — no dual
    write between them."* The transaction that invariant names spans the journal
    APPEND and this projection, and this module does not perform the append —
    the chokepoint (`#7951`, Interface C) does. Only the caller can therefore
    own the boundary: a `commit()` here would commit the projection ALONE and
    leave the append and the record in two transactions, which is the dual
    write §3 forbids. `replay()` executes the plan on `conn` and neither commits
    nor rolls back; the caller commits on success and rolls back on failure, and
    an exception propagates with the caller's transaction still open.

    It is the companion of :func:`replay_plan`, not an alternative to it: the
    chokepoint may use either, because the transaction boundary lives on the
    caller's side in both.

    IDEMPOTENCE. Every emitted mutation is absolute: an upsert writes the same
    values onto the same row, a patch writes the same values, a delete finds
    nothing, and the journal position is `DO NOTHING`. A second replay of the
    same journal therefore produces the same record — and the assertion the
    caller should make is exactly `#5285`'s, moved to the record: **replay
    twice and compare the tables, not the code paths.**

    TOTALITY. The whole journal is materialized into statements *before* the
    first one executes, so an undeclared record type fails the replay with the
    transaction untouched rather than leaving a half-applied record.

    THE POSITION WATERMARK. ``start_seq`` is the journal position of
    ``records[0]`` — the caller's fact, not this module's invention (see
    :func:`replay_plan`). It defaults to ``1``, which reproduces every call
    made before the parameter existed byte for byte.

    THE POSITION ROW IS OPTIONAL (#9190). ``position=False`` emits the LAYER
    mutations and no ``journal_event`` row. It is the shape for a caller with NO
    journal ordinal — an in-memory log, or a lane that is not a journal at all;
    for such a caller a row written here would have to invent a number, which is
    the silent collision #9190 exists to remove. The LIVE path is NOT that
    caller: `EventLog.append` reports the record's position and
    `EventLog.position()` exposes the count, so `write_live_record` passes
    ``position=seq is not None`` and layer 1 is populated by production traffic,
    not only by a replay. ``True`` (the default) keeps every caller that
    predates the parameter byte-identical.

    THE TENANT GUC (R6). The FIRST statement executed is
    :data:`TENANT_GUC_SQL` — ``set_config('app.current_org_id', $1, true)`` —
    carrying the SAME ``org_id`` the plan is about to project. That is the whole
    mechanism by which RLS can be the record's tenancy boundary rather than a
    decoration, and it is placed HERE rather than in a caller for one reason:
    the org id the policies compare against and the org id written into the rows
    are then one value, so they cannot disagree in silence (test-design surface
    S2). Two properties follow, and both are pinned in
    ``tests/test_record_tenancy_guc.py``:

    - it is TRANSACTION-LOCAL (``is_local=true``), so it neither survives the
      transaction nor depends on a session surviving a pooler — a session ``SET``
      under transaction pooling reads as NULL, which the ``USING`` predicates
      turn into zero rows rather than an error;
    - it runs AFTER the plan is materialized, so the totality guarantee is
      untouched: an undeclared record type still raises having executed no
      statement at all.

    ⚠️ WRITE ON ``true``. A caller-supplied ``conn`` in autocommit mode makes
    ``set_config(..., true)`` set nothing, and the read-back below REFUSES it
    (`tenant GUC … is <not your org> after setting it to <org>`) before a
    single plan statement. The observed value is the empty string on an
    autocommit connection (the set is reported but does not persist) and NULL
    when the context was dropped entirely (a pooler) — so the `WITH CHECK` predicates are never reached
    on that shape. That is deliberate and it is the point of the read-back:
    a NULL GUC is loud on `INSERT` and SILENT on an `UPDATE`/`DELETE`-only
    plan, which would affect zero rows and report success (#9600 review cycle
    1). The seam's own connections are not in autocommit
    (``_default_connect`` never turns it on), so the live path is unaffected.

    ⛔ VERIFIED, NOT ASSUMED (#9600 review cycle 1, P2). Executing the prologue
    is not the same event as the GUC being in force, and the failure mode of
    the gap is SILENT in one shape: every plan statement's ``INSERT`` carries a
    ``WITH CHECK`` that raises under a NULL GUC, but an `UPDATE`/`DELETE` is
    gated by ``USING`` alone and simply affects 0 rows — so a patch- or
    delete-only record (reachable with ``position=False``, which has no
    ``journal_event`` insert left to fail) would be dropped while this function
    returned an APPLIED report. The prologue is therefore read back
    (``current_setting(..., true)``) and a mismatch RAISES: an autocommit
    connection, a renamed GUC, or a pooler that dropped the transaction-local
    set can never be mistaken for a successful replay.

    ⛔ AND THE ROLE IS VERIFIED, NOT ASSUMED (#9600 review cycle 2, P2). A
    correct GUC on a connection that BYPASSES the policies is the one shape in
    which every check passes and the boundary is not there: `postgres` (the
    table owner, and R1's documented pre-change DSN shape) or `service_role`
    reads and writes every tenant's rows while the report says APPLIED. The
    scope (:func:`scope_connection`) therefore refuses a connection whose
    ``current_user`` is superuser, ``BYPASSRLS``, a member of either, or the
    owner of a record table — see :data:`TENANT_SCOPE_SQL`. Arming the record
    with the wrong DSN now fails closed on the first write instead of silently
    turning RLS off.
    """
    plan = replay_plan(records, org_id=org_id, graph_id=graph_id,
                       start_seq=start_seq, position=position)
    # The tenant, before anything it scopes. NOT part of `plan`: the plan stays
    # the pure journal→record declaration, and this statement is the session
    # context the plan is applied in — plus the role gate that decides whether
    # the context can mean anything at all.
    cursor = scope_connection(conn, org_id)
    for statement in plan:
        cursor.execute(statement.sql, statement.params)
    report = ReplayReport(events=len(records), statements=0)
    for statement in plan:
        if _JOURNAL_INSERT_PREFIX in statement.sql:
            continue
        report.statements += 1
    return report


#: Distinguishes the journal-position write from the record's own mutations,
#: so a report can count what the projection produced.
_JOURNAL_INSERT_PREFIX = "INSERT INTO public.journal_event"


def replay_plan(records: Sequence[Mapping[str, Any]], *, org_id: str,
                graph_id: str, start_seq: int = 1,
                position: bool = True) -> list[Statement]:
    """Every statement a full replay emits, in order, WITHOUT executing any.

    The totality gate lives here: an undeclared record type raises before a
    single statement has run, so a replay either materializes the whole journal
    or leaves the record exactly as it found it. It is also the seam the PGlite
    harness (`supabase/tests/pglite/validate.mjs`) and the tests use to look at
    the *whole* statement set rather than one record at a time.

    THE POSITION WATERMARK (#9187). `_journal_row` keys the consumed position on
    `(org_id, graph_id, seq)`, where `seq` is a record's index in the batch — so
    a batch is a *contiguous segment of the journal*, and its first position is
    a fact only the caller holds: the journal append is the chokepoint's, not
    this module's. `start_seq` therefore defaults to 1 (the whole journal, or a
    first segment) and a caller replaying a LATER segment passes the offset it
    read from the journal. Without it, replaying a tail segment would renumber
    every record to seq 1.. and `ON CONFLICT DO NOTHING` would silently absorb
    the position writes of records that were never numbered — the layer rows
    would land, but the record's own claim about what it consumed would be a
    lie. Since the journal is append-only, a re-run after a crash re-derives the
    SAME offset from the same durable facts, so a retry never renumbers.

    `position=False` omits the per-record row entirely (see :func:`replay`):
    the shape for a caller with NO journal ordinal. The LIVE path is not that
    caller — `EventLog.append` reports the record's position (#9190), and
    `record_writer.write_live_record` writes the row from it — so layer 1 is
    populated by production traffic, not only by a replay. The layer mutations
    are unaffected either way.
    """
    if start_seq < 1:
        raise RecordProjectionError(
            f"start_seq is the journal position of records[0] and must be >= 1 "
            f"(got {start_seq!r})"
        )
    columns = table_columns()
    refused = refused_props_names()
    plan: list[Statement] = []
    for seq, record in enumerate(records, start=start_seq):
        plan.extend(mutations_for(record, org_id=org_id, graph_id=graph_id,
                                  columns=columns, refused=refused))
        if position:
            plan.append(_journal_row(record, seq, org_id, graph_id))
    return plan


def to_dollar_placeholders(statement: Statement) -> tuple[str, list[Any]]:
    """Rewrite DB-API `%s` placeholders to Postgres `$n`.

    The engine emits DB-API (`%s`) parameters because that is what the Python
    driver on the write path takes. A harness that speaks the *native* protocol
    (PGlite, `psql`) needs `$n`, and this is the one converter for both — a
    second hand-written copy is how the two drift.
    """
    parts = statement.sql.split("%s")
    if len(parts) - 1 != len(statement.params):
        raise RecordProjectionError(
            f"statement has {len(parts) - 1} placeholders for "
            f"{len(statement.params)} parameters: {statement.sql}"
        )
    out = [parts[0]]
    for index, tail in enumerate(parts[1:], start=1):
        out.append(f"${index}")
        out.append(tail)
    return "".join(out), list(statement.params)
