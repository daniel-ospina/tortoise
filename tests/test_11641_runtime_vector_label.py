"""#11641 — the two writers whose vector label was decided at RUNTIME now BOUND it.

`#11492` gave `#5090` DoD row 2 a declared `stores` per writer, and exactly two writers could not
answer WHERE: they store a vector for real, but the label came from outside the function — the
dump's own labels for `tortoise.hosted_backup::restore_graph`, the caller's statement for
`tortoise.sdk::_event_raw_write_door`. Declared `LABELS_AT_RUNTIME`, they held the row at
`PARTIAL`, because a runtime label cannot be compared to `run_vector_query`'s served set.

This suite pins the ANSWER, and it pins it in BOTH directions, because each half fails silently on
its own:

* the **bound** — a vector store onto a label the read path does not serve is REFUSED at the seam
  (`restore_graph` pre-mutation, so a refusal cannot leave a half-restored graph; the door before
  the journal append, so a refusal cannot leave a record with no node);
* the **legitimate path** — a served-label vector still restores, still lands as `vecf32`, and is
  still reachable through `run_vector_query`; a real `:Event` write still goes through the door.

RED proof (each assertion fails on the pre-fix tree):

* `test_restore_graph_refuses_an_unserved_label` — before the fix the restore silently wrote a
  vector onto `:Batch` (a label nothing queries) and returned counts as if all was well;
* `test_the_door_refuses_a_statement_outside_its_label` — before the fix the door executed any
  caller statement, and the register's own reason says the label was "not the door's to name";
* `test_the_declared_restore_labels_are_the_served_vocabulary` — before the fix `restore_graph`
  declared `LABELS_AT_RUNTIME`, so row 2 had no label to compare and read `PARTIAL`.
"""
from __future__ import annotations

import os
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.embed_writer_register import (
    EMBED_WRITER_CLASSES,
    LABELS_AT_RUNTIME,
)
from tortoise.hosted_backup import (
    _VECTOR_RESTORE_LABEL_SET,
    _VECTOR_RESTORE_LABELS,
    DUMP_FORMAT,
    restore_graph,
)
from tortoise.projection import FalkorProjection
from tortoise.sdk import _RAW_WRITE_DOOR_LABELS, TortoiseSDK
from tortoise.vector_write import (
    clear_then_set_prefix,
    served_store_labels,
    set_embedding_clause,
    statement_interpolates_label,
    statement_node_labels,
)

RESTORE_WRITER = "tortoise.hosted_backup::restore_graph"
DOOR_WRITER = "tortoise.sdk::_event_raw_write_door"

#: A vector of the width the store's index is created with, small enough to inline in a dump.
VEC = [0.01 * i for i in range(384)]


# ── the declarations, in BOTH directions ────────────────────────────────────────────────────────

def test_the_declared_restore_labels_are_the_served_vocabulary() -> None:
    """The restore's bound and the query path's vocabulary must be the SAME set — either way.

    ⛔ Both directions, because they fail differently and neither is loud:
    * a label ADDED to `tortoise.security.VALID_ENTITY_TYPES` makes the query path serve a label
      this bound still refuses — the restore would refuse a dump it should accept, so the
      declaration is now narrower than the product;
    * a label DROPPED from it leaves this tuple naming a label no read reaches — the register's
      declaration would then be the only thing in the product still asserting a query path that
      does not exist.
    """
    served = served_store_labels()
    assert set(_VECTOR_RESTORE_LABEL_SET) == served, (
        "`tortoise.hosted_backup::_VECTOR_RESTORE_LABELS` and the query path's served labels have "
        f"diverged: restore={sorted(_VECTOR_RESTORE_LABEL_SET)} served={sorted(served)}. The "
        "restore's bound is a MIRROR of the read path's vocabulary and must move with it"
    )
    assert {label for label, _prop in _VECTOR_RESTORE_LABELS} == served


def test_the_register_declares_what_both_seams_enforce() -> None:
    """The register is a DECLARATION; these two tuples are what make it true.

    Three things must agree or the declaration is the fabrication `LABELS_AT_RUNTIME` was invented
    to refuse: the register's `stores`, the constant each writer enforces, and the served set.
    """
    assert EMBED_WRITER_CLASSES[RESTORE_WRITER].stores != LABELS_AT_RUNTIME, (
        "restore_graph is back to a runtime label — the restore must bound what it stores on, or "
        "row 2 cannot compare it to the served set"
    )
    assert set(EMBED_WRITER_CLASSES[RESTORE_WRITER].stores) == set(_VECTOR_RESTORE_LABEL_SET)
    assert EMBED_WRITER_CLASSES[DOOR_WRITER].stores == ("Event",)
    assert tuple(label for label, _prop in _RAW_WRITE_DOOR_LABELS) == (
        EMBED_WRITER_CLASSES[DOOR_WRITER].stores)
    # And every label EITHER writer declares must be one the read path serves: a declaration is
    # only green because it names served labels, so a served-set shrink must red here too.
    declared = (set(EMBED_WRITER_CLASSES[RESTORE_WRITER].stores)
                | set(EMBED_WRITER_CLASSES[DOOR_WRITER].stores))
    assert declared <= served_store_labels(), (
        f"{sorted(declared - served_store_labels())} is declared but no read path serves it"
    )


def test_row_2_reads_met_with_no_runtime_label_left() -> None:
    """The issue's target, measured through the instrument rather than asserted about the register.

    Reads the probe's own row-2 verdict, so this cannot pass while the row it is about still
    holds — the separation `#11492`'s reviews kept getting wrong in the other direction.
    """
    from tools.dod_5090_probe import scored_labels, served_labels

    scored = scored_labels()
    assert not scored["unresolved"], (
        "row 2's HOLD set is non-empty — a writer's label is still not a label: "
        f"{sorted(scored['unresolved'])}"
    )
    assert scored["labels"] <= served_labels(), (
        f"row 2 would print an orphan: {sorted(scored['labels'] - served_labels())}"
    )
    assert LABELS_AT_RUNTIME not in {
        entry.stores for entry in EMBED_WRITER_CLASSES.values()}, (
        "a writer is declared labels-at-runtime again — name the bound instead"
    )


# ── the door: refused shapes, and the real shape that must keep working ────────────────────────

class _NoJournal:
    """A door receiver whose journal must NEVER be reached by a refused statement.

    The refusal has to come BEFORE the append: a door that journals first and refuses after would
    leave a record for a node it never wrote — the exact live/replay divergence the door's own
    docstring forbids. So the fake RAISES if the append is touched at all.
    """

    def _journal_event_raw_write(self, *args, **kwargs):  # pragma: no cover - asserted not called
        raise AssertionError(
            "the journal was reached for a REFUSED statement — the door's bound must run first"
        )


DOOR = TortoiseSDK._event_raw_write_door


@pytest.mark.parametrize("statement", [
    "MATCH (b:Batch {id:$eid}) SET b += $props",
    "CREATE (b:Batch {id:$eid}) SET b += $props",
    "MATCH (e:Event {eventId:$eid}), (b:Batch {id:$eid}) SET b += $props",
    "MATCH (e {eventId:$eid}) SET e += $props",
])
def test_the_door_refuses_a_statement_outside_its_label(statement: str) -> None:
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")
    assert "_event_raw_write_door" in str(refused.value), (
        "the refusal must name the writer a reader can go look at"
    )
    assert ("no read path" in str(refused.value)
            or "door stores on" in str(refused.value)
            or "NO node label" in str(refused.value)), str(refused.value)


@pytest.mark.parametrize("statement", [
    # The four literal statements `tortoise/sdk.py::ingest_corpus` passes, built with the real
    # helpers so the shape is the product's, not a paraphrase of it.
    "MATCH (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
    + "SET e += $props, " + set_embedding_clause("e"),
    "MERGE (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
    + "SET e += $props, " + set_embedding_clause("e"),
    "CREATE (e:Event {eventId:$eid}) SET e += $props",
    "MATCH (e:Event {eventId:$eid}) SET e += $props",
    # …and the two `tortoise/session_indexer.py::main` builds.
    "MATCH (e:Event {eventId: $eid}) " + clear_then_set_prefix("e")
    + "SET e += $props, " + set_embedding_clause("e"),
    "CREATE (e:Event {eventId: $eid}) SET e += $props, "
    + set_embedding_clause("e", preserve_on_null=False),
])
def test_every_real_door_shape_is_inside_the_bound(statement: str) -> None:
    """The accept direction: the bound must not refuse the product's own six statements."""
    assert statement_node_labels(statement) == {"Event"}, statement
    # …and the bound itself accepts them (past the raise, onto the journal refusal — which is what
    # proves the guard let them through).
    with pytest.raises(AssertionError, match="journal was reached"):
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")


def test_the_door_refuses_a_statement_that_INTERPOLATES_a_label() -> None:
    """The refused shape a reader of LITERAL labels cannot see (#11641).

    `statement_node_labels` returns only the labels a statement NAMES, so a caller can satisfy the
    bound with one literal `(e:Event …)` pattern and store the vector on a SECOND pattern whose
    label it renders at runtime: `MATCH (e:Event {eventId:$eid}), (n:{kind}) SET n.embedding = …`.
    The bound would read `{'Event'}`, pass, and the vector would land wherever `kind` points — the
    label inherited from the caller again, which is the whole defect `#11641` closes.

    ⛔ NON-VACUITY: the STATEMENT is the proof of this assertion's reach — the reader below really
    does see nothing wrong with it (`{'Event'}`, no stray label), so on a bound that only reads
    named labels the door reaches the JOURNAL (the fake raises), which is exactly what the pre-fix
    tree does for every statement.
    """
    statement = (
        "MATCH (e:Event {eventId:$eid}), (n:{kind}) "
        + clear_then_set_prefix("n") + "SET n += $props, " + set_embedding_clause("n")
    )
    # The reader's blind spot, asserted rather than assumed: the literal label is all it sees.
    assert statement_node_labels(statement) == {"Event"}, statement_node_labels(statement)
    assert statement_interpolates_label(statement) is True
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")
    assert "INTERPOLATION" in str(refused.value), str(refused.value)
    assert "_event_raw_write_door" in str(refused.value), (
        "the refusal must name the writer a reader can go look at"
    )


def test_no_real_door_shape_is_mistaken_for_an_interpolation() -> None:
    """The other direction: the product's own statements must not trip the new refusal.

    A guard that refused a real shape would break ingestion, and a nested map literal
    (`{meta: {a: 1}}`) is the ONE text shape a loose `:{` pattern would misread — so both sides of
    the boundary are pinned here rather than reasoned about.
    """
    for statement in (
        "MATCH (e:Event {eventId:$eid}) SET e += $props",
        "MERGE (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
        + "SET e += $props, " + set_embedding_clause("e"),
        # A nested map literal is NOT a label interpolation — the pattern is anchored to the node
        # pattern's `(alias:` prefix for exactly this reason.
        "MATCH (e:Event {eventId:$eid}) SET e.meta = {a: {b: 1}}",
    ):
        assert statement_interpolates_label(statement) is False, statement


def test_a_real_event_write_still_lands_on_event(tmp_path) -> None:
    """End to end: the door's bound does not break the writer it guards (live, not simulated)."""
    sdk = TortoiseSDK(str(tmp_path / f"{uuid4().hex}.db"))
    try:
        proj = sdk._get_proj()
        written = sdk._event_raw_write_door(
            proj,
            "MERGE (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
            + "SET e += $props, " + set_embedding_clause("e"),
            event_id="evt-11641",
            props={"eventId": "evt-11641", "eventKind": "DocumentCreated"},
            event_kind="DocumentCreated",
            embedding=VEC,
            lane="test-11641",
        )
        assert written is True
        rows = proj.g.query(
            "MATCH (e:Event {eventId:'evt-11641'}) RETURN typeof(e.embedding)"
        ).result_set
        assert rows and rows[0][0] == "Vectorf32", rows
    finally:
        sdk.close()


# ── restore_graph: refused artifact, and the legitimate one it must still restore ─────────────

def _dump(nodes: list[dict]) -> dict:
    return {"format": DUMP_FORMAT, "nodes": nodes, "edges": []}


def _proj(tmp_path) -> FalkorProjection:
    return FalkorProjection(str(tmp_path / f"{uuid4().hex}.db"))


@pytest.mark.parametrize("labels", [
    ["Batch"],            # a label nothing queries
    ["Session"],          # the other unserved label the register knows about
    [],                   # unlabeled: reachable by no entity_type at all
])
def test_restore_graph_refuses_an_unserved_label(tmp_path, labels) -> None:
    """A dump carrying a vector on an unqueryable label is REFUSED, before any mutation.

    Before this bound the restore wrote it silently and returned node/edge counts: a vector paid
    for and unreadable, restored through the one operation that could have noticed it.
    """
    proj = _proj(tmp_path)
    artifact = _dump([{"dump_id": 1, "labels": labels,
                       "props": {"id": "x", "embedding": VEC}}])
    with pytest.raises(ValueError) as refused:
        restore_graph(proj.g, artifact)
    assert "no read path" in str(refused.value) or "run_vector_query" in str(refused.value), (
        str(refused.value)
    )
    # PRE-MUTATION: the refusal must leave the target holding nothing but its own export-skip
    # bookkeeping (`FalkorProjection` creates a `:Meta{key:'point_fts_v2'}` marker at construction,
    # which `restore_graph`'s own emptiness check tolerates) — the #3895 discipline, extended to the
    # vector half.
    from tortoise.hosted_api import _is_export_skip_node
    left = [
        list(row[0] or []) for row in proj.g.query(
            "MATCH (n) RETURN labels(n), properties(n)").result_set
        if not _is_export_skip_node(list(row[0] or []), dict(row[1] or {}))
    ]
    assert left == [], (
        f"the refusal left {left} behind — the vector check must run before any node is created, "
        "or a rejected artifact leaves a partially restored graph"
    )


def test_restore_graph_still_restores_a_served_label(tmp_path) -> None:
    """The legitimate direction, for every served label AND a multi-label node.

    The multi-label case is not decoration: `MATCH (n:Point)` matches a node that also carries
    `:Foo`, so such a vector IS reachable and must restore — grouped under its SERVED label.
    """
    proj = _proj(tmp_path)
    nodes = [
        {"dump_id": 1, "labels": ["Point"],
         "props": {"id": "p1", "content": "a point", "pointKind": "claim", "embedding": VEC}},
        {"dump_id": 2, "labels": ["Event"],
         "props": {"eventId": "e1", "eventKind": "DocumentCreated", "embedding": VEC}},
        {"dump_id": 3, "labels": ["Point", "TestMarker"],
         "props": {"id": "p3", "content": "multi-label", "pointKind": "claim",
                   "embedding": VEC}},
        {"dump_id": 4, "labels": ["Subject"], "props": {"id": "s1", "name": "no vector"}},
    ]
    counts = restore_graph(proj.g, _dump(nodes))
    assert counts["nodes"] == 4, counts

    for label, key, value in (
        ("Point", "id", "p1"),
        ("Event", "eventId", "e1"),
        ("Point", "id", "p3"),
    ):
        rows = proj.g.query(
            f"MATCH (n:{label} {{{key}:$v}}) RETURN typeof(n.embedding)",
            params={"v": value},
        ).result_set
        assert rows and rows[0][0] == "Vectorf32", (label, value, rows)
    # …and the label-scoped write did not smear: the node that carried NO vector still carries none.
    assert proj.g.query(
        "MATCH (n:Subject {id:'s1'}) RETURN n.embedding"
    ).result_set[0][0] is None


def test_an_unserved_vector_restores_when_it_carries_no_embedding(tmp_path) -> None:
    """The bound is on the VECTOR, not on the label: an unserved label with no vector is fine.

    Without this the guard would be a new restriction on legitimate dumps — `:Batch` markers,
    `:Session` nodes and every bookkeeping label restores exactly as before.
    """
    proj = _proj(tmp_path)
    counts = restore_graph(proj.g, _dump([
        {"dump_id": 1, "labels": ["Batch"], "props": {"id": "b1", "createdAt": "2026-01-01"}},
    ]))
    assert counts["nodes"] == 1, counts
    assert proj.g.query(
        "MATCH (b:Batch {id:'b1'}) RETURN b.embedding"
    ).result_set[0][0] is None
