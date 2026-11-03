"""#9973 (+#9972) — a `DirectEdgeRepoint` descriptor is a MOVE, and the record
applies the WHOLE of it.

THE TWO DEFECTS, both measured on the plan and (optionally) on a real Postgres:

* **#9973 — the move was half-applied.** `RECORD_MUTATIONS["DirectEdgeRepoint"]`
  declared a plain `RelationUpsert`, so the record wrote the row at the
  descriptor's OWN `src` — the point the supersede had just retired — and nothing
  was ever removed. The graph does the opposite: `projection/__init__.py`'s
  pass-2b replays the transfer as `DELETE` at the old point then `MERGE` at
  `src_f = _final(src)`. A record-sourced rebuild therefore RE-ASSERTED an edge
  the live graph had deleted (and `cache_projection.relation_statement` MERGEs
  every `relation` row back into a live edge, so the stale row was not inert),
  and a chain (X→X'→X'') grew a row per link.

* **#9972 — the target was resolved by the wrong column.** A 2b descriptor names
  its target by the node's REPLAY key (`projection/edges.py::stub_key` — a NAME
  for aboutSubject/aboutObject/aboutEvent/aboutPoint, a `url` for the two
  `:Source` rels), while `_ENTITY_MATCH_COLUMNS` resolves `object`/`subject` by
  `id` (since #9881). A name can never equal an id minted by
  `ids.entity_name_id`, so every name-keyed repoint landed **zero** rows, in
  silence — the record's `relation` row that `EntityLinked` wrote was still
  there, so an edge COUNT still agreed with the graph while the identity did not.

WHY THE TWO LAND TOGETHER. The #9972 lane measured (on PGlite, recorded on this
issue) that a target-only fix is WORSE than the bug — it re-upserts the dead
point's row, drops the row's `confidence` (the descriptor carries no `attrs`) and
fans out where the fold refuses — and that a move-only fix is inert for the
four name-keyed labels, because "the successor's edge" never resolved. One
mutation, two halves.

WHAT IS PINNED HERE, and why each leg discriminates:

* the reference maps are the FOLD'S OWN (`STRUCTURAL_REL_LABELS`,
  `DERIVABLE_STRUCTURAL_RELS`, `stub_key`) compared over the whole map — a
  re-spelled label or a moved replay key reds here instead of silently
  mis-pointing a rebuilt edge;
* the move emits BOTH statements, in the graph's order, and the INSERT resolves
  the source to its TERMINAL LIVE SUCCESSOR (the recursive `_walk`) while the
  DELETE keys the point the descriptor names — reverse either and the test reds;
* the target resolves by NAME (or the Source's canonical triple) and NEVER by an
  id, with an ambiguity REFUSAL rather than a `LIMIT 1` pick (`#5509`);
* the `delete_only` leg is byte-unchanged — it is the ONE repoint shape that was
  already correct, and a fix that quietly rewrote it would be a regression
  wearing a fix's clothes;
* the 2a DIRECT family keeps `DO UPDATE` and chases BOTH endpoints (its
  descriptor already names the successor), so a chain stops stranding the
  transfer on an intermediate terminal point.

The DB-level legs run only when `TEST_RECORD_DB_URI` points at a Postgres
carrying the record migrations (the same arming surface `test_9078` /
`test_9567` use) — a plan is a claim, and the row set is the fact. They are the
legs that would have caught the two defects measured on this issue; both were
invisible to a plan-only reading because a 0-row `INSERT … SELECT` and a
`DELETE` that matches nothing are both *successful* statements.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tortoise import record_projection as rp
from tortoise.projection.edges import (
    DERIVABLE_STRUCTURAL_RELS,
    STRUCTURAL_REL_LABELS,
    stub_key,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The record's database — the arming surface `test_9078`/`test_9567` use.
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

OID = "obj-e0987478f6b24453433acfa783"
DOC = "https://example.test/doc"
SUBJ_ID = "s1"


def _ts(n: int) -> str:
    return f"2026-10-25T00:0{n}:00Z"


# ── the journal the PRODUCER writes ────────────────────────────────────────


def _point(pid: str, *, n: int = 1) -> dict:
    return {"type": "PointAdded", "ts": _ts(n), "id": pid,
            "point": {"id": pid, "content": f"content {pid}", "status": "live"}}


def _supersede(old: str, new: str, *, n: int = 2) -> dict:
    return {"type": "PointSuperseded", "ts": _ts(n), "id": old, "new_id": new,
            "valid_to": _ts(n)}


def _structural_repoint(src: str, rel: str, tgt: str, label: str) -> dict:
    """`sdk.supersede_point`'s 2b emission, verbatim (`sdk.py` 2b loop).

    The id base is the producer's: `f"{old_id}->{new_id}:{rel}:{target_key}"`.
    """
    return {"type": "DirectEdgeRepoint", "ts": _ts(2),
            "id": f"{src}->SUCCESSOR:{rel}:{tgt}", "src": src, "tgt": tgt,
            "target_label": label, "edge_type": rel}


def _world() -> list[dict]:
    return [
        {"type": "ObjectRegistered", "ts": _ts(0), "id": OID, "name": "Acme Corp"},
        {"type": "SubjectAdded", "ts": _ts(0), "id": SUBJ_ID, "name": "ParitySubj"},
        {"type": "SourceCreated", "ts": _ts(0), "id": DOC, "url": DOC,
         "sourceKind": "web"},
    ]


def _relation_statements(records: list[dict]) -> list[rp.Statement]:
    return [s for s in rp.replay_plan(records, org_id="o", graph_id="g")
            if "public.relation" in s.sql]


def _plan_one(record: dict) -> list[rp.Statement]:
    return rp.mutations_for(record, org_id="o", graph_id="g",
                            columns=rp.table_columns(),
                            refused=rp.refused_props_names())


# ════════════════════════════════════════════════════════════════════════════
# 1. The declaration — the record's maps ARE the fold's
# ════════════════════════════════════════════════════════════════════════════


def test_the_structural_repoint_label_map_is_the_folds_own():
    """The rel→label map must be the graph's, over the whole map.

    The record derives a structural descriptor's target LABEL from the rel
    (`projection/edges.py::resolve_structural_target` is called with
    `STRUCTURAL_REL_LABELS[etype]`, never with the payload's `target_label`), so
    a re-spelled or re-scoped entry here would file the edge against a different
    node than the graph did — the label-scoped-resolution defect the fold's own
    docstring names ("NEVER auto-detect labels").
    """
    assert rp._STRUCTURAL_REPOINT_LABELS == STRUCTURAL_REL_LABELS


def test_the_repoint_key_column_per_label_is_the_property_stub_key_reads():
    """`stub_key` decides WHICH property the descriptor's `tgt` holds; the
    record's column map must be that property, per label.

    Pinned by asking the producer's own resolver what it extracts rather than by
    restating a value table: a `name` for the four name-keyed about* rels, a
    `url` for the two `:Source` rels — and the record's column is the one that
    value lands in (`name`, and for `Source` the `node_key` column the url IS).

    A missing entry is the #9972 defect in its general form: the label then falls
    back to an entity-id predicate that the replay key can never equal.
    """
    assert set(rp._STRUCTURAL_REPOINT_KEY_COLUMNS) == set(
        STRUCTURAL_REL_LABELS.values())
    for rel, label in STRUCTURAL_REL_LABELS.items():
        keyed = stub_key(rel, {"name": "N", "url": "u"})
        assert keyed is not None, rel
        property_, key = "url" if keyed[1] == "u" else "name", keyed[1]
        assert keyed[0] == label, rel
        column = rp._STRUCTURAL_REPOINT_KEY_COLUMNS[label]
        if property_ == "url":
            # `:Source`'s `url` IS its record key (`chk_source_node_key_is_contract_key`)
            assert column == "node_key", (rel, column)
        else:
            assert column == "name" or column == "props->>'name'", (rel, column)
    # …and every derivable rel is routed (the two maps agree in both directions)
    assert set(rp._STRUCTURAL_REPOINT_LABELS) <= DERIVABLE_STRUCTURAL_RELS
    assert "aboutSource" not in rp._STRUCTURAL_REPOINT_LABELS, (
        "aboutSource is NOT a derivable structural rel — it must take the 2a "
        "DIRECT arm, not the replay-key arm")


def test_the_repoint_declares_a_move_and_not_an_upsert():
    """The registry must say MOVE — a reader must not need the dispatcher.

    `RelationUpsert` and `EdgeRepoint` are different mutations (the same rule
    that keeps `NodeSnapshot` and `NodePatch` apart), and the record declared
    the wrong one for this family until #9973.
    """
    effect = rp.RECORD_MUTATIONS["DirectEdgeRepoint"].effect
    assert isinstance(effect, rp.EdgeRepoint)
    assert not isinstance(effect, rp.RelationUpsert)


# ════════════════════════════════════════════════════════════════════════════
# 2. The structural 2b move — BOTH halves, resolved by REPLAY key (#9972)
# ════════════════════════════════════════════════════════════════════════════


def _structural_plan() -> tuple[list[rp.Statement], list[rp.Statement]]:
    """`(deletes, inserts)` for ONE aboutObject repoint over a supersede.

    Narrowed to the repoint's OWN statements by its replay key: the journal also
    writes the `EntityLinked` row, and a leg that read "the first relation
    INSERT" would be asserting over the row that was never in question — the
    shape of assertion this file exists to make impossible.
    """
    records = _world() + [
        _point("P1"), _point("P2"),
        {"type": "EntityLinked", "ts": _ts(1), "id": "P1",
         "source_label": "Point", "source_id": "P1",
         "target_label": "Object", "target_id": OID,
         "edge_type": "aboutObject"},
        _supersede("P1", "P2"),
        _structural_repoint("P1", "aboutObject", "Acme Corp", "Object"),
    ]
    rels = [s for s in _relation_statements(records)
            if "Acme Corp" in s.params]
    deletes = [s for s in rels if s.sql.startswith("DELETE FROM public.relation")]
    inserts = [s for s in rels if s.sql.startswith("INSERT")]
    return deletes, inserts


def test_a_structural_repoint_moves_its_edge_to_the_final_successor():
    """THE ISSUE'S OWN FAILING TEST: the move has TWO halves.

    Fails on `main` twice over — 0 relation `DELETE`s in the plan, and every
    repoint `INSERT` binding the descriptor's own `src` (`P1`), i.e. the point
    the supersede retired. The graph ends with the edge on `_final(P1) = P2` and
    nothing at `P1`; a plan that says otherwise is the record asserting a claim
    from a superseded point.
    """
    deletes, inserts = _structural_plan()
    assert len(deletes) == 1, [s.sql for s in deletes]
    assert len(inserts) == 1, [s.sql for s in inserts]

    # the delete half: keyed on the point the descriptor NAMES
    assert "'P1'" not in deletes[0].sql, (
        "the old point is RESOLVED, never interpolated")
    assert "P1" in deletes[0].params
    assert "aboutObject" in deletes[0].params

    # the create half: the source is chased to its terminal live successor
    insert = inserts[0]
    assert "WITH RECURSIVE _walk" in insert.sql, (
        "the successor is `_final(src)`, and the record has no graph to ask")
    assert "superseded_by" in insert.sql
    # …and the chase starts from the RESOLVED source row, so an absent endpoint
    # is the `absent` verdict rather than a minted key.
    assert "(SELECT node_key FROM public.point" in insert.sql


def test_a_structural_repoint_resolves_its_name_keyed_target():
    """#9972: the target is a NAME, so it must be MATCHED by name.

    `main` emitted `object.id = 'Acme Corp'` — a predicate no id minted by
    `ids.entity_name_id` can satisfy, so the statement succeeded and landed
    ZERO rows. The assertion is the producer's own key (`params`), never a
    restatement of the SQL.
    """
    _deletes, inserts = _structural_plan()
    sql, params = inserts[0].sql, inserts[0].params
    assert "Acme Corp" in params
    assert "name = %s" in sql, sql
    assert "FROM public.object WHERE org_id = %s AND graph_id = %s AND id = %s" \
        not in sql, "the target is name-keyed; `id = %s` is #9972"


def test_an_ambiguous_name_plans_no_row_rather_than_picking_one():
    """#5509's discipline, in SQL: EXACTLY ONE carrier or none.

    The graph REFUSES a name held by 2+ carriers (`_refuse_ambiguous_target`)
    instead of taking the backend's first row, so the record must land no row.
    The refusal is a `count(*) OVER ()` test because the plan cannot count rows:
    `GROUP BY node_key HAVING count(*) = 1` does NOT express it (`node_key` is the
    PK, so every group holds one row and every group passes) — measured on
    Postgres 16, where that form returned BOTH carriers and the DELETE's scalar
    subquery raised `cardinality_violation`.
    """
    _deletes, inserts = _structural_plan()
    sql = inserts[0].sql
    assert "count(*) OVER () AS _n" in sql, sql
    assert "_m._n = 1" in sql, sql
    assert "LIMIT 1" not in sql.split("FROM public.object")[1].split(") tgt")[0], (
        "a LIMIT 1 over a name is the arbitrary pick #5509 removes")


def test_the_source_target_is_resolved_the_way_the_moved_row_was_written():
    """`extractedFrom`/`aboutDocument` land on the `:Source` row the ORIGINAL
    edge used — the same canonical triple (or the same kind guard) as the write.

    A delete keyed on the descriptor's raw spelling while the create resolved the
    canonical one would remove 0 rows and leave the pre-move row exactly where
    this issue is about.
    """
    records = _world() + [
        _point("P1"), _point("P2"),
        _supersede("P1", "P2"),
        _structural_repoint("P1", "extractedFrom", DOC, "Source"),
        _structural_repoint("P1", "aboutDocument", DOC, "Source"),
    ]
    rels = _relation_statements(records)
    extracted = [s for s in rels
                 if "extractedFrom" in s.params and s.sql.startswith("INSERT")]
    document = [s for s in rels
                if "aboutDocument" in s.params and s.sql.startswith("INSERT")]
    assert extracted and document
    # extractedFrom: the canonical triple the row-key/edge resolver binds (#9956)
    assert "canonical_url" in extracted[0].sql
    assert "COLLATE \"C\"" in extracted[0].sql
    # aboutDocument: RESOLVE-ONLY and kind-guarded (D10 / adversarial class B1)
    assert "document_kind IS NOT NULL" in document[0].sql


def test_the_structural_create_keeps_the_successors_own_edge_state():
    """`DO NOTHING`, because the live transfer is `ON CREATE SET` (#9417).

    `supersede_point` is the RESTATEMENT path: the same subject is commonly bound
    by BOTH points, and the successor's own `confidence` wins. A structural
    descriptor carries no `attrs`, so an unconditional `DO UPDATE SET props =
    EXCLUDED.props` would overwrite that row with an empty map — destroying the
    successor's value, which is exactly the collision #9417 removed live.
    """
    _deletes, inserts = _structural_plan()
    assert inserts[0].sql.endswith("DO NOTHING"), inserts[0].sql[-80:]


def test_the_structural_move_refuses_a_self_edge_and_a_no_op_chase():
    """The graph's two skips, on the create half.

    * `_final(src) == src` ⇒ never re-create the edge at the old point (the
      graph's own defensive branch; also the cycle answer, where `_final` walks
      back onto its start);
    * a target that resolves to the successor itself ⇒ never mint a phantom
      self-edge (`E2E-11.6`'s no-self-edge contract).
    """
    _deletes, inserts = _structural_plan()
    sql = inserts[0].sql
    assert "WHERE src.node_key <> %s" in sql, sql
    assert "src.node_key <> tgt.node_key" in sql, sql


def test_the_target_label_comes_from_the_rel_never_from_the_payload():
    """The FOLD's rule, restated: `resolve_structural_target` is called with the
    rel's label. A descriptor whose `target_label` disagrees must not move the
    edge onto a same-named node of another type — the exact auto-detect hazard
    `projection/edges.py` writes out in place.
    """
    record = _structural_repoint("P1", "aboutObject", "Acme Corp", "Subject")
    statements = _plan_one(record)
    inserts = [s for s in statements if s.sql.startswith("INSERT")]
    assert inserts, [s.sql for s in statements]
    assert "FROM public.object" in inserts[0].sql, inserts[0].sql
    assert "FROM public.subject" not in inserts[0].sql


def test_the_descriptor_without_a_supersede_still_moves_nothing_at_the_old_point():
    """The move guard, pinned from the other side — and it is a NAMED BOUND.

    `sdk.supersede_point` emits `PointSuperseded` BEFORE the 2a/2b transfer
    loops, so a real journal always carries the successor link when a descriptor
    is applied and `_final(src)` resolves. A hand-written journal that ordered
    them the other way cannot: the chase is a no-op and the create is SKIPPED —
    the graph's own answer for a descriptor no surviving supersede fold covers
    ("never re-create the edge at old after the delete-leg"), and the direction
    `E2E-11.6` demands. This leg exists so that answer is a decision on record
    rather than a surprise on the disaster-recovery path.
    """
    records = _world() + [
        _point("P1"), _point("P2"),
        _structural_repoint("P1", "aboutObject", "Acme Corp", "Object"),
        _supersede("P1", "P2"),
    ]
    inserts = [s for s in _relation_statements(records) if s.sql.startswith("INSERT")]
    assert inserts[0].sql.endswith("DO NOTHING")
    assert "WHERE src.node_key <> %s" in inserts[0].sql


# ════════════════════════════════════════════════════════════════════════════
# 3. The shapes this fix must NOT touch
# ════════════════════════════════════════════════════════════════════════════


def test_the_delete_only_leg_is_unchanged():
    """The no-self-edge guard (#2489/#7969) is the ONE repoint shape that was
    already right: a DELETE, keyed on the descriptor's own endpoints.

    It is also the only place `idx` is constrained — a `delete_only` descriptor
    carries the direct successor's LOGICAL id, not a replay key, so it must not
    be routed through the structural resolution (which would resolve 0 rows and
    leave the phantom the guard exists to remove).
    """
    record = {"type": "DirectEdgeRepoint", "ts": _ts(2), "id": "p1->p2:IMPL",
              "src": "p1", "tgt": "p2", "edge_type": "IMPL",
              "delete_only": True}
    statements = _plan_one(record)
    assert len(statements) == 1
    sql, params = statements[0].sql, statements[0].params
    assert sql == (
        "DELETE FROM public.relation WHERE org_id = %s AND graph_id = %s "
        "AND rel_type = %s AND src_label = 'Point' AND src_key = %s "
        "AND tgt_label = 'Point' AND tgt_key = %s AND idx = %s"
    ), sql
    assert params == ("o", "g", "IMPL", "p1", "p2", 0)


def test_the_direct_2a_family_chases_both_endpoints_and_keeps_the_update():
    """A 2a descriptor already names the SUCCESSOR, so the move guard would plan
    no row at all — and its attrs are its state, so the conflict path must still
    UPDATE (the live 2a leg is a bare `SET r += $attrs`, last-writer-wins).

    Both endpoints are chased because a CHAIN moves on: descriptor 1 of
    `supersede(A→A1)`, `supersede(A1→A2)` is keyed `A1`, and the graph writes at
    `_final(A1) = A2`.
    """
    record = {"type": "DirectEdgeRepoint", "ts": _ts(2), "id": "A->A1:IMPL",
              "src": "A1", "tgt": "B", "edge_type": "IMPL"}
    statements = _plan_one(record)
    assert len(statements) == 1
    sql = statements[0].sql
    assert sql.count("WITH RECURSIVE _walk") == 2, (
        "BOTH endpoints of a direct transfer are `_final`-resolved")
    assert "WHERE src.node_key <> %s" not in sql, (
        "the move guard would plan no row for the direct family")
    assert "DO UPDATE SET" in sql
    assert "src.node_key <> tgt.node_key" in sql


def test_a_non_structural_rel_is_not_given_a_replay_key_resolution():
    """`ownedBy`/`dependsOn`/… are `DirectEdgeCreated`'s family and keep the
    label's own endpoint resolution; only the REPOINT family carries replay keys.

    Locally, `aboutSource` is the rel that looks structural and is NOT derivable
    — routing it through the replay-key arm would resolve a name that the
    producer never writes.
    """
    assert "aboutSource" not in rp._STRUCTURAL_REPOINT_LABELS
    with pytest.raises(rp.RecordProjectionError):
        rp._structural_repoint_target("o", "g", "aboutSource", "x")
    created = {"type": "DirectEdgeCreated", "ts": _ts(1), "id": "p1->s1:ownedBy",
               "src": "p1", "tgt": "s1", "edge_type": "ownedBy",
               "source_label": "Point", "target_label": "Subject"}
    sql = _plan_one(created)[0].sql
    assert "WITH RECURSIVE" not in sql
    assert "FROM public.subject WHERE org_id = %s AND graph_id = %s AND id = %s" \
        in sql


# ════════════════════════════════════════════════════════════════════════════
# 4. Executed against a Postgres — a plan is a claim, the row set is the fact
# ════════════════════════════════════════════════════════════════════════════


def _driver():
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — a plan alone cannot prove the "
                    "row set")
    psycopg2 = pytest.importorskip(
        "psycopg2", reason="psycopg2-binary is the record's driver")
    return dsn, psycopg2


@pytest.fixture
def db(request):
    """A migrated Postgres and a tenant THIS test alone has used, or a skip.

    FUNCTION-scoped, and the tenant is derived from the test's own name: the
    legs assert on the whole row set, so a module-scoped tenant would let the
    previous leg's rows answer this one's question (measured — the run's second
    and third DB legs passed for that reason before this was narrowed).
    `_seed_tenant` clears the layer tables for the pair it is given, and
    `journal_event` is append-only by trigger, so a dedicated `graph_id` is what
    keeps a re-replay's position rows from colliding across legs.

    The schema application and the tenant seed are IMPORTED from
    `tests/test_9078_record_written_by_production_path.py` — a second
    `_ensure_record_schema` drifts and passes on a stale schema.
    """
    dsn, psycopg2 = _driver()
    from tests.test_9078_record_written_by_production_path import (
        _ensure_record_schema,
        _seed_tenant,
    )
    slug = "".join(ch if ch.isalnum() else "-"
                   for ch in request.node.name.lower())[:48]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    _ensure_record_schema(conn)
    tenant = (f"org-9973-{slug}", f"graph-9973-{slug}")
    _seed_tenant(conn, tenant)
    yield conn, tenant
    conn.close()


def _replay(conn, tenant, records) -> None:
    plan = rp.replay_plan(records, org_id=tenant[0], graph_id=tenant[1])
    with conn.cursor() as cur:
        for statement in plan:
            cur.execute(statement.sql, statement.params)


def _rows(conn, tenant) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rel_type, src_key, tgt_key, confidence FROM public.relation "
            "WHERE org_id = %s AND graph_id = %s ORDER BY rel_type, src_key",
            tenant)
        return cur.fetchall()


def _structural_journal(old: str, new: str, *, extra_supersede: str | None = None):
    records = _world() + [
        _point("P1"), _point("P2"), _point("P3"),
        {"type": "EntityLinked", "ts": _ts(1), "id": "P1",
         "source_label": "Point", "source_id": "P1",
         "target_label": "Object", "target_id": OID,
         "edge_type": "aboutObject"},
    ]
    if extra_supersede is None:
        records += [_supersede(old, new),
                    _structural_repoint(old, "aboutObject", "Acme Corp", "Object")]
    else:
        records += [_supersede("P1", "P2"),
                    _structural_repoint("P1", "aboutObject", "Acme Corp", "Object"),
                    _supersede("P2", "P3", n=3),
                    _structural_repoint("P2", "aboutObject", "Acme Corp", "Object")]
    return records


def test_the_moved_edge_lands_on_the_successor_and_leaves_the_old_point(db):
    """THE ARTIFACT THIS ISSUE IS ABOUT, executed.

    Journal route (live / `rebuild_all`): edges incident to the old point ∅, and
    one `aboutObject` on the successor. The record route on `main` was the exact
    inverse. This leg reads the ROWS, which is the thing a 0-row `INSERT … SELECT`
    cannot lie about.
    """
    conn, tenant = db
    _replay(conn, tenant, _structural_journal("P1", "P2"))
    rows = _rows(conn, tenant)
    assert [(r[0], r[1], r[2]) for r in rows] == [("aboutObject", "P2", OID)], rows
    assert rows[0][3] is None, (
        "#10139's known gap, stated not hidden: a structural descriptor carries "
        "no `attrs`, so the moved row lands bare. Fixing the MOVE and carrying "
        "the attrs are two issues; this leg pins which one this is")


def test_the_record_chain_converges_on_one_row_at_the_final_successor(db):
    """`E2E-11.6` on the record route: a chain leaves ONE row, on the FINAL
    successor, and none on the intermediate terminal point.

    On `main` this journal left a row on `P1` and on `P2` (and the successor got
    nothing) — the record asserting a claim from a superseded point, with
    `cache_projection` ready to MERGE it back into a live edge.
    """
    conn, tenant = db
    _replay(conn, tenant, _structural_journal("P1", "P2", extra_supersede="P2->P3"))
    rows = _rows(conn, tenant)
    assert [r[1] for r in rows] == ["P3"], rows
    assert len(rows) == 1, f"one row per moved edge, not one per link: {rows}"


def test_replaying_the_same_journal_twice_is_idempotent(db):
    """A second replay converges — the delete then matches nothing and the
    create is an upsert keyed on the final successor."""
    conn, tenant = db
    journal = _structural_journal("P1", "P2")
    _replay(conn, tenant, journal)
    once = _rows(conn, tenant)
    _replay(conn, tenant, journal)
    assert _rows(conn, tenant) == once


def test_the_successors_own_edge_state_survives_the_move(db):
    """The `DO NOTHING` half, executed: the successor already holds this edge
    with its OWN confidence, and the transfer must not erase it (#9417).

    `supersede_point` is the restatement path, so both points commonly bind the
    same Subject/Object; the descriptor carries no `attrs`, so `DO UPDATE` would
    write NULL over the successor's value.
    """
    conn, tenant = db
    records = _world() + [
        _point("P1"), _point("P2"),
        {"type": "EntityLinked", "ts": _ts(1), "id": "P2",
         "source_label": "Point", "source_id": "P2",
         "target_label": "Object", "target_id": OID,
         "edge_type": "aboutObject", "confidence": 0.7},
        {"type": "EntityLinked", "ts": _ts(1), "id": "P1",
         "source_label": "Point", "source_id": "P1",
         "target_label": "Object", "target_id": OID,
         "edge_type": "aboutObject", "confidence": 0.9},
        _supersede("P1", "P2"),
        _structural_repoint("P1", "aboutObject", "Acme Corp", "Object"),
    ]
    _replay(conn, tenant, records)
    rows = _rows(conn, tenant)
    assert len(rows) == 1, rows
    assert rows[0][1] == "P2"
    assert float(rows[0][3]) == pytest.approx(0.7), (
        "the successor's own confidence must win the conflict (ON CREATE SET)")


def test_an_absent_successor_lands_no_row_and_does_not_abort_the_replay(db):
    """A `superseded_by` naming a point the record never materialised must be
    the `absent` verdict, not a dead run.

    `relation.src_key` is a composite FK into `node_identity`; without the chase's
    terminal join the INSERT would hand it a key no identity owns and the FK
    would ABORT the whole replay — turning "the graph's MATCH missed" into a
    broken disaster-recovery run.
    """
    conn, tenant = db
    records = _world() + [
        _point("P1"),
        {"type": "EntityLinked", "ts": _ts(1), "id": "P1",
         "source_label": "Point", "source_id": "P1",
         "target_label": "Object", "target_id": OID,
         "edge_type": "aboutObject"},
        _supersede("P1", "P2"),          # P2 has no PointAdded
        _structural_repoint("P1", "aboutObject", "Acme Corp", "Object"),
    ]
    _replay(conn, tenant, records)       # must not raise
    assert _rows(conn, tenant) == []


def test_the_record_view_re_mints_the_edge_at_the_successor_only(db):
    """THE other half of the defect, read through the PRODUCTION reader.

    `cache_projection.relation_statement` MERGEs EVERY `relation` row back into a
    live edge, so a row left on the superseded point is not inert — it becomes a
    live edge again on a record-sourced rebuild, indistinguishable from a
    legitimately projected one. On `main` `read_record_view` returned the edge
    keyed `P1` (the retired point) and the successor's edge had never landed; the
    projection therefore re-created the very edge the graph had deleted.

    Read through `_app_role_dsn` because that is the door production uses:
    `read_record_view` refuses a connection whose role can bypass RLS (#9600).
    """
    conn, tenant = db
    _replay(conn, tenant, _structural_journal("P1", "P2"))

    from urllib.parse import urlsplit

    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    from tortoise import cache_projection as cp

    dsn = os.environ[RECORD_DB_ENV]
    if not urlsplit(dsn).scheme:
        pytest.skip(f"{RECORD_DB_ENV} is not a URI, so the app-role DSN cannot "
                    "be derived (see the record runbook's connecting role)")
    app = None
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        app = psycopg2.connect(_app_role_dsn(dsn))
        view = cp.read_record_view(app, org_id=tenant[0], graph_id=tenant[1])
        projected = [(r.rel_type, r.src_label, r.src_key, r.tgt_label, r.tgt_key)
                     for r in view.relations]
        assert projected == [("aboutObject", "Point", "P2", "Object", OID)], (
            f"the record view must address the SUCCESSOR: {projected}")
        cypher = cp.relation_statement(view.relations[0]).cypher
        assert "MERGE (src)-[r:aboutObject" in cypher
    finally:
        if app is not None:
            app.rollback()
            app.close()
