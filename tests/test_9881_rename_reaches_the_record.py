"""#9881 — a rename MOVES the record's name, or the record says so. It never
silently keeps the old one.

THE DEFECT THIS FILE PINS (measured, red-first). The refusal half of #9881
landed in #9948 (`0cbeb1918`): `rename` left `Dispatch.refused_ops`, and
`Object`/`Subject` were re-keyed onto the stable `id`, which is what makes a
rename an ordinary column patch. What did NOT land is the end-to-end proof, and
one hole survived the key move:

    $ rp.mutations_for({"type": "EntityMutated", "id": "o1", "label": "Object",
                        "op": "rename", "name": "New"}, org_id="o", graph_id="g")
    [Statement('UPDATE public.object SET node_key = node_key WHERE org_id = %s
                AND graph_id = %s AND id = %s', ('o', 'g', 'o1'))]

`SET node_key = node_key` is a ZERO-EFFECT update. The record keeps the OLD
name, reports the mutation consumed, and nothing anywhere is red — while the
GRAPH's fold is loud about the same record (`_warn_entity_mutation_fold_miss`:
*"a state-op miss is not exempt"*, #3585) and `sdk._journal_entity_mutation`
says in so many words that a record carrying only a top-level `name` *"unfolds
and is reported as a fold miss"*. So one half reports and the other swallows: a
replay and a rebuild come to disagree about a node's name with no error at any
layer. `name` IS a reserved envelope key (`projection._ENTITY_MUTATION_RESERVED_KEYS`,
accepted by `_journal_entity_mutation(name=…)`), so the shape is one this tree
declares, not a typo.

WHY THE 0-EFFECT FALLBACK IS RIGHT ELSEWHERE AND WRONG HERE.
`_patch_statement`'s "a patch with nothing to write is a 0-row no-op by
construction" fallback is DELIBERATE for an op whose content lives in a column
the layer table refuses: #9974's status-only `restatus` has exactly that shape
(`never_store={"status"}`, tracked separately and NOT changed here). A RENAME has
no such shape — its entire content IS the new name — so the same fallback can
only ever mean "the old name is kept in silence".

THE THREE PROPERTIES THIS FILE ESTABLISHES.

1. **A rename that applies no name is REFUSED, by name** (DB-free, and the
   red-first leg — it reds on the pre-fix tree with the `SET node_key =
   node_key` above in the failure output).
2. **The producer's own rename record is never refused** (DB-free): the ONE
   classification (`projection.classify_entity_mutation_op`) returns `"rename"`
   only when the applied map carries `name`, for every node label — so the
   refusal is a guard on an unproduced shape, never a block on a real write.
3. **End to end, through the public SDK door, on a real Postgres** (`TEST_RECORD_DB_URI`,
   the `test-record-postgres` job's shape, #9551): `sdk.update_entity(id,
   name=…)` journals `EntityMutated op=rename` on the stable id, the record row's
   `name` MOVES, its `node_key` does not, the old name is GONE from the tenant,
   the record→cache read returns the new name, and replaying the same journal
   onto a fresh tenant lands the SAME row (`rebuild == live`, indicator 4).

WHAT THIS FILE DOES NOT CLAIM. It does not re-open the key move or the missing
`name`-column work (#9948 owns those), and it does not touch the `restatus`
no-op (#9974 criterion 2, still open). It also does not make a rename match 0
rows loud: a rename whose target row the record does not hold stays a 0-row
patch — that residual is stated in the PR body rather than half-fixed here,
because idempotent replay legitimately re-applies records to rows it no longer
has, and telling those two apart is #9974's decision, not this leg's.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from test_9078_record_written_by_production_path import (
    RECORD_DB_ENV,
    _app_role_dsn,
    _ensure_record_schema,
    _record_db,
    _seed_tenant,
)

from tortoise import record_projection as rp
from tortoise import record_writer
from tortoise.projection import classify_entity_mutation_op
from tortoise.record_writer import use_record_tenant
from tortoise.sdk import TortoiseSDK


#: The ONE record the producer emits for a rename, built exactly the way
#: `sdk._journal_entity_mutation` builds it (`_update_entity`'s non-Point
#: branch): identity as written, `state` = the write's own applied map.
def _producer_rename(oid: str, new_name: str, label: str = "Object") -> dict:
    applied = {"name": new_name}
    return {"type": "EntityMutated", "ts": "2026-11-06T00:00:00Z",
            "id": oid, "op": classify_entity_mutation_op(applied),
            "label": label, "state": applied}


@pytest.fixture(autouse=True)
def _no_ambient_record_env(monkeypatch):
    """Hermetic against the record's four arming surfaces.

    A developer lane configured the way the single-tenant instructions say
    (`TORTOISE_RECORD_DSN` + the `_ORG_ID`/`_GRAPH_ID` pair + the
    `TORTOISE_RECORD_SINGLE_TENANT` declaration) would otherwise arm and
    attribute every leg here — including the DB-FREE ones, which would then
    start writing to a live record from a unit test.
    """
    for name in (record_writer.RECORD_DSN_ENV, record_writer.RECORD_ORG_ENV,
                 record_writer.RECORD_GRAPH_ENV,
                 record_writer.RECORD_SINGLE_TENANT_ENV):
        monkeypatch.delenv(name, raising=False)
    yield


# ════════════════════════════════════════════════════════════════════════════
# 1. DB-free — the silent keep, and the two shapes that must NOT be refused
# ════════════════════════════════════════════════════════════════════════════


def test_a_rename_that_applies_no_name_is_refused_by_name():
    """RED-FIRST. A rename whose applied map carries no `name` kept the old one.

    Measured on the pre-fix tree (`12f4ce48e5`) — this is the reproduction, and
    both shapes below emitted a ZERO-EFFECT statement instead of raising:

        >>> rp.mutations_for({"type": "EntityMutated", "id": "o1",
        ...                   "label": "Object", "op": "rename",
        ...                   "name": "New"}, org_id="o", graph_id="g")
        [Statement('UPDATE public.object SET node_key = node_key WHERE
                    org_id = %s AND graph_id = %s AND id = %s', ('o','g','o1'))]

        >>> rp.mutations_for({"type": "EntityMutated", "id": "o1",
        ...                   "label": "Object", "op": "rename",
        ...                   "state": {}}, org_id="o", graph_id="g")
        [Statement('UPDATE public.object SET node_key = node_key ...')]

    A caller that replays such a record is told the rename was consumed. The
    row's `name` is untouched, so every later read is built on the name the
    writer believed it had replaced.

    The refusal must NAME THE OP (the brief's rule: a message naming the op),
    because the operator's next question is which record to re-emit — and it
    must say where the name has to arrive instead, since the envelope `name` is
    the shape that produced it.
    """
    for record in (
        # the envelope's RESERVED `name` — declared, accepted, and unread by
        # the graph fold (#4769), so the record must not get ahead of it either
        {"type": "EntityMutated", "ts": "T", "id": "o1", "label": "Object",
         "op": "rename", "name": "New"},
        # a rename that applies nothing at all
        {"type": "EntityMutated", "ts": "T", "id": "o1", "label": "Object",
         "op": "rename", "state": {}},
        # a rename whose applied map moves something else but not the name
        {"type": "EntityMutated", "ts": "T", "id": "o1", "label": "Object",
         "op": "rename", "state": {"objectKind": "org"}},
    ):
        with pytest.raises(rp.RecordProjectionError) as excinfo:
            rp.mutations_for(record, org_id="o", graph_id="g")
        message = str(excinfo.value)
        assert "op='rename'" in message or 'op="rename"' in message, (
            "the refusal does not name the op it refused, so an operator cannot "
            f"tell which record to re-emit: {message!r}")
        assert "state" in message and "name" in message, (
            "the refusal does not say WHERE the new name has to arrive "
            f"(`state['name']`, the writer's applied map): {message!r}")


def test_the_producers_rename_record_is_never_refused_on_any_label():
    """Non-vacuity for the refusal: the REAL producer shape must still apply.

    `classify_entity_mutation_op` returns `"rename"` only where the applied map
    carries `name` (`projection/__init__.py`: `if "name" in props: return
    "rename"`), so the guard added for the block above can never bite a write
    any producer emits — but "can never" is a claim about a mapping, and a
    mapping drifts. So this leg runs the classifier itself over each label's
    applied map and requires the record to be APPLIED (not refused, and not a
    0-effect patch) for every one of them.

    A blanket refusal would also make the whole write half of #3590 impossible,
    which is the exact defect #9948 removed — so a regression to "refuse every
    rename" has to red here too, not only the narrow shapes above.
    """
    for _table, (label, _key_column) in rp.NODE_TABLES.items():
        applied = {"name": "AcmeRenamed"}
        op = classify_entity_mutation_op(applied)
        assert op == "rename", (
            "the classifier no longer labels a name-bearing write `rename`, so "
            f"this leg is asserting the wrong op ({label}: {op!r})")
        statements = rp.mutations_for(
            {"type": "EntityMutated", "ts": "T", "id": "id-1", "label": label,
             "op": op, "state": applied},
            org_id="o", graph_id="g")
        assert statements, f"a producer's rename on {label} produced no statement"
        sql = " ".join(s.sql for s in statements)
        applied_sql = " ".join(s.sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
                               for s in statements if " SET " in s.sql)
        # The name reaches the row either as its own column (`Object`/`Subject`
        # have one) or as the properties tail (`Point`/`Event`/`Session` do not)
        # — what must never happen is neither. So the assertion is on the VALUE
        # being bound, not on a column name the label may not have.
        bound = [str(param) for statement in statements
                 for param in statement.params]
        assert any("AcmeRenamed" in value for value in bound), (
            f"a producer's rename on {label} no longer writes the name "
            f"({label}: {sql!r} / {bound!r})")
        assert "node_key = node_key" not in applied_sql, (
            f"a producer's rename on {label} fell through to the 0-effect "
            f"patch — the silent keep is back ({sql!r})")
        # ...and the key never moves: the whole premise of the id key (#9881).
        assert "node_key =" not in applied_sql.replace("node_key = node_key", ""), (
            f"a rename MOVED the row's key on {label} — the one thing #9881 "
            f"establishes it must never do ({applied_sql!r})")


# ════════════════════════════════════════════════════════════════════════════
# 2. The end-to-end proof — a real SDK door, a real Postgres
# ════════════════════════════════════════════════════════════════════════════


def _new_tenant(prefix: str) -> tuple[str, str]:
    token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return (f"org-9881-{prefix}-{token}", f"graph-9881-{prefix}-{token}")


def _journal_records(path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _object_rows(conn, tenant: tuple[str, str]) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, node_key, name FROM public.object "
            "WHERE org_id=%s AND graph_id=%s ORDER BY id", tenant)
        return [tuple(row) for row in cur.fetchall()]


def test_the_public_rename_door_moves_the_records_name_live_and_on_replay(
        monkeypatch, tmp_path):
    """Indicators (2) and (4) of #9881, end to end, through the PUBLIC door.

    Indicator (2) — *"a rename journaled by `sdk._update_entity` lands a record
    row that MOVES the name rather than refusing"* — and indicator (4) —
    *"`rebuild == live` still holds for the record projection"* — are the two
    the issue's close-out comment left unevidenced (it could only pin the SQL
    the projection *would* emit, not a produced-and-replayed rename). So this is
    deliberately not a unit test of the fold:

    * the write goes through the SDK's public `update_entity`, with the record
      ARMED as the application role and the tenant carried by the writer's own
      `use_record_tenant` (no probe module, no monkeypatched writer);
    * the assertion is on the RECORD ROW — `select id, node_key, name` — so the
      re-keyed key (`node_key = id`) and the moved column are both read back;
    * the REPLAY leg replays the same journal onto a tenant no other leg has
      used and requires the SAME row, which is what makes this "rebuild ==
      live" instead of "live wrote something".

    The old name is asserted GONE from the tenant, not merely absent from one
    row: a record that moved the name onto a second row, or left a by-name
    ghost behind, would satisfy "the new name is there" and fail here.
    """
    conn = _record_db()
    try:
        _ensure_record_schema(conn)
        live = _new_tenant("live")
        replay_tenant = _new_tenant("replay")
        _seed_tenant(conn, live)
        _seed_tenant(conn, replay_tenant)

        # The WRITER connects as the app role: `record_projection.scope_connection`
        # refuses the provisioning superuser, exactly as production would (#9554).
        monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                           _app_role_dsn(os.environ[RECORD_DB_ENV]))
        # The record's write half must not depend on a live extractor; this leg's
        # subject is the rename, not extraction (#3695/#5477 class).
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")

        journal_path = tmp_path / "journal.jsonl"
        sdk = TortoiseSDK(str(tmp_path / "graph.db"),
                          event_log_path=str(journal_path))
        with use_record_tenant(*live):
            created = sdk.create_entity("object", name="Acme", objectKind="org")
            object_id = created["node"]["id"]
            sdk.update_entity(object_id, name="AcmeRenamed")

        # ── (a) the journal carries the producer's rename, on the STABLE id ──
        records = _journal_records(journal_path)
        renames = [record for record in records
                   if record.get("type") == "EntityMutated"
                   and record.get("op") == "rename"]
        assert [record["id"] for record in renames] == [object_id], (
            "the rename was not journaled as `EntityMutated op=rename` on the "
            f"stable id — journal types: "
            f"{[(r.get('type'), r.get('op')) for r in records]}")
        # #12120: a rename APPLIES the fold beside the surface name (the writer's
        # key space is the folded union), so the record carries both — and the
        # replay leg below is what proves that is right rather than noise: the
        # same map lands the same row on a fresh tenant.
        assert renames[0]["state"] == {"name": "AcmeRenamed",
                                       "canonical_name": "acmerenamed"}, \
            renames[0]
        assert renames[0]["label"] == "Object", renames[0]

        # ── (b) LIVE: the name moved, the key did not, the old name is GONE ──
        live_rows = _object_rows(conn, live)
        assert live_rows == [(object_id, object_id, "AcmeRenamed")], (
            "the record did not apply the rename: the row must be keyed by its "
            f"stable id and carry the NEW name (got {live_rows})")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM public.object WHERE org_id=%s AND "
                "graph_id=%s AND name=%s", (*live, "Acme"))
            assert cur.fetchone()[0] == 0, (
                "the OLD name is still in the record — a rename that leaves the "
                "old name addressable is the silent divergence #9881 is about")

        # ── (c) the record→cache READ returns the new name ──
        import tortoise.cache_projection as cp
        row = {"id": live_rows[0][0], "node_key": live_rows[0][1],
               "name": live_rows[0][2], "object_kind": "org"}
        assert cp.node_from_row("Object", row).props.get("name") == "AcmeRenamed", (
            "the record row carries the new name but the record's own read path "
            "does not return it")

        # ── (d) REBUILD == LIVE: the same journal, a fresh tenant, one row ──
        import psycopg2
        replay_conn = psycopg2.connect(
            _app_role_dsn(os.environ[RECORD_DB_ENV]))
        try:
            rp.replay(records, replay_conn, org_id=replay_tenant[0],
                      graph_id=replay_tenant[1], position=False)
            replay_conn.commit()
        finally:
            replay_conn.close()
        replayed_rows = _object_rows(conn, replay_tenant)
        assert replayed_rows == live_rows, (
            "rebuild != live for a rename: replaying the journal onto a fresh "
            f"tenant produced {replayed_rows}, live produced {live_rows}")
    finally:
        conn.close()
