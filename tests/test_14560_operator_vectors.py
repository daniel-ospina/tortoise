"""#14560 — where an operator's vector actually lives, MEASURED on a real graph.

THE ISSUE, AND THE THREE CAUSES IT NAMED. On the production record, 17,899 rows of
`public.point` with `is_operator = true` carried NO `embedding_vec`, while 51,221
non-operator rows did. The issue offered three explanations: (a) the entity folds
skip operators, (b) a key mismatch between the record's operator rows and the
graph's `:Point` operator nodes, (c) `operator` is not addressable by the backfill's
label set.

WHAT IS TRUE, and each line below is executed rather than argued:

* **(a), in a sharper form than the issue states.** No fold ever gives a pure
  operator a vector IN EITHER STORE. `create_operator` writes
  `{id, is_operator, op_type, direction, …}` and no `embedding`; operators store no
  CONTENT (`#548`), so there is nothing to embed; and `_upsert_point_props` guards
  BOTH the restore arm and the compute arm on `not op`. The journal snapshot the
  write emits is the graph read-back, so it carries no VECTOR either — and the column
  is not absent: `sdk._emit_event` forces `embedding` present on every snapshot it
  journals (`#5004`, "presence is ownership"), so the record's INSERT for that row
  binds `embedding` to NULL. ⛔ THAT NULL IS THE MECHANISM, not a lost value: the
  vector was never produced, so there was nothing for the write to drop.
* **(b) is DISPROVED.** Both legs key the same `id`: `LABEL_SERVED["operator"]` is
  `("point", "id", …)` and the backfill's `:Point` population is `("point", "Point",
  "id")`. Nothing is mis-keyed; there is simply nothing on the graph side to pair.
* **(c) is TRUE BUT NOT THE CAUSE of the zero,** and it is not a fill gap either: an
  operator IS a `:Point` node in `public.point`, so the EXISTING `--label Point`
  population already copies every vector-bearing operator — measured here as the
  hybrid case. `--label operator` is not selectable, which is a REPORTING gap only.

WHAT THE ZERO ACTUALLY EXPOSED is the fourth thing: `operator`'s coverage gate was
UNSATISFIABLE. The record numerator applies the operator predicate (`#13630`) while
the graph denominator counted the whole `:Point` label, so 17,899 operator rows could
never be `>= 39,662` vector-bearing Points — no backfill could ever close it, and
`operator` gates `:Point`'s strip through `label_write_gate`. `#14560`'s fix is
`record_vector.GRAPH_KIND_FILTER`: one declaration, the Cypher twin of the SQL
predicate, applied by `projection._label_vector_count` — so the comparison is
like-for-like and the empty slice verifies as TRIVIAL with its reason naming why
there is nothing to copy. The denominator half is pinned there
(`tests/test_13610_graph_vector_copy.py`); THIS file pins the POPULATION half on a
real engine, because the denominator claim is only as good as the fact that a real
`create_operator` write carries no vector.

Run (docker lane — provides `TORTOISE_DB_URI`)::

    uv run python tools/test_lane.py run -- tests/test_14560_operator_vectors.py -v
"""
from __future__ import annotations

import uuid

import pytest

from tests._live_utils import _skip_unless_live_uri
from tortoise import record_projection, record_vector
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import reset_circuit_breakers

_DIM = 384
_OPERATOR_KIND = "(n.is_operator = true OR n.op_type IS NOT NULL)"


# ── plumbing ───────────────────────────────────────────────────────────────

@pytest.fixture()
def sdk():
    """A fresh docker graph per test (unique name → no cross-test bleed)."""
    _skip_unless_live_uri()
    reset_circuit_breakers()
    s = TortoiseSDK(graph_name=f"test_14560_operator_{uuid.uuid4().hex[:8]}")
    try:
        yield s
    finally:
        reset_circuit_breakers()
        s.close()


def _vector(hot: int = 3) -> list[float]:
    """A unit-basis vector: a value nobody could confuse for a computed one."""
    values = [0.0] * _DIM
    values[hot] = 1.0
    return values


class _Journal:
    """Capture what a write JOURNALS — the snapshot the record projection reads.

    The record has no other input: `record_projection.mutations_for` projects the
    journal record, so "does the record's row carry a vector?" is answered by the
    snapshot, not by the graph and not by the projection's code.

    ⛔ THE SNAPSHOT CAPTURED HERE IS THE CALLER'S INTENT, NOT THE JOURNALLED RECORD
    (#14572 review). This fixture builds an SDK with NO journal file, so the chokepoint
    (`live_write.write`) is never reached and the `point=` dict captured below is the
    one `_emit_event` RECEIVED. `_emit_event` then COPIES it and FORCES `embedding`
    present (`#5004`, "presence is ownership"), so the record the journal would hold
    carries `embedding: None` — the read-back's OMITTED key is not the record's shape.
    An arm that reads the INSERT off this dict therefore measures the wrong object; the
    arm below applies the forced key explicitly instead of asserting its absence.
    """

    def __init__(self, sdk):
        self.events: dict[str, tuple[dict, dict]] = {}
        self._sdk = sdk
        self._original = sdk._emit_event

    def __enter__(self):
        def hook(type_, payload=None, **kw):
            self.events.setdefault(type_, (payload or {}, kw))
            return self._original(type_, payload, **kw)

        self._sdk._emit_event = hook
        return self

    def __exit__(self, *_exc):
        self._sdk._emit_event = self._original
        return False

    def record(self, type_: str) -> dict:
        payload, kw = self.events[type_]
        record = {"type": type_, **payload}
        if kw.get("point"):
            record["point"] = kw["point"]
        return record


def _graph_vector_counts(sdk) -> tuple[int, int]:
    """``(whole :Point label, operator-kind subset)``, both vector-bearing.

    ⛔ ``ro_query``, NOT ``query``: these are READS, and the residue ratchet
    (`tools/sdk_privatise.py`, `#14548`) reds on a NEW untyped ``g.query``
    receiver. ``ro_query`` is the read-only verb on the handle and carries the
    same ``.result_set``, so the census stays flat without a pin.
    """
    g = sdk._get_proj().g
    whole = g.ro_query("MATCH (n:Point) WHERE n.embedding IS NOT NULL "
                       "RETURN count(n)").result_set[0][0]
    kinded = g.ro_query(f"MATCH (n:Point) WHERE n.embedding IS NOT NULL "
                        f"AND {_OPERATOR_KIND} RETURN count(n)").result_set[0][0]
    return int(whole), int(kinded)


def _point_insert_columns(record: dict) -> str:
    """The COLUMN LIST of the `public.point` INSERT a journal record produces.

    Read out of the statement rather than inferred from a row: the record is a fold,
    so the columns it writes are the whole of what the record can hold.
    """
    statements = record_projection.mutations_for(
        record, org_id="org_14560", graph_id="graph_14560")
    for statement in statements:
        sql = getattr(statement, "sql", None) or (
            statement[0] if isinstance(statement, (tuple, list)) else str(statement))
        if sql.startswith("INSERT INTO public.point "):
            return sql.split("(", 1)[1].split(")", 1)[0]
    raise AssertionError(
        "no `public.point` INSERT was produced — the record projection's shape has "
        f"moved, so this measurement would pass vacuously ({len(statements)} "
        "statement(s))")


# ── the measurement ────────────────────────────────────────────────────────

def test_the_point_table_CAN_hold_a_vector_so_the_absence_below_means_something(sdk):
    """The control the two cases below rest on: the column exists and is populated.

    Without this, "the operator's INSERT has no `embedding` column" would be
    indistinguishable from "no INSERT ever carries one" — and a reader would be free
    to conclude the record cannot store a Point vector at all.
    """
    columns = record_projection.table_columns()["point"]
    assert "embedding" in columns, sorted(columns)
    assert "embedding_vec" in columns, sorted(columns)

    with _Journal(sdk) as journal:
        point = sdk._create_point("statement", "a plain claim carries a vector",
                                  embedding=_vector())
    record = journal.record("PointAdded")
    assert "embedding" in (record.get("point") or {}), sorted(record.get("point") or {})
    assert "embedding" in _point_insert_columns(record).split(", "), (
        "a plain Point's journal vector did not reach the record's INSERT")
    assert point["id"]


def test_a_pure_operator_carries_no_vector_in_either_store(sdk):
    """THE MEASURED FACT the diagnosis rests on: nothing is lost, because nothing exists.

    A `create_operator` Point lands on the graph with NO `embedding` (operators store
    no content, `#548`), its read-back carries none, and the vector the journal
    snapshot carries is `None` — `embedding` present on the key (`#5004`'s presence
    contract) and bound to NULL, never a vector the write dropped. That is the whole
    of the "17,899 operator rows with 0 vectors": there is no vector in either store
    to lose, and `mirror_required('operator')` therefore cannot be protecting one.
    """
    a = sdk._create_point("statement", "anchor one", embedding=_vector(1))
    b = sdk._create_point("statement", "anchor two", embedding=_vector(2))
    with _Journal(sdk) as journal:
        operator = sdk._create_operator("IMPL", a["id"], [b["id"]], label="supports")

    whole, kinded = _graph_vector_counts(sdk)
    assert (whole, kinded) == (2, 0), (
        "the measurement moved: expected the two plain anchors to be the only "
        f"vector-bearing Points and NO operator among them, got {whole}/{kinded}")

    readback = sdk._get_point(operator["id"])
    assert "embedding" not in readback, sorted(readback)
    assert readback.get("is_operator") is True, readback

    record = journal.record("OperatorAdded")
    snapshot = record.get("point") or {}
    # ⛔ NO VECTOR IS PRODUCED FOR THIS SHAPE: the caller's snapshot carries no
    # `embedding` key at all (`_get_point` omits a NULL property), so there is nothing
    # for a write to drop (operators store no content, `#548`).
    assert "embedding" not in snapshot, sorted(snapshot)
    # ⛔ AND THE RECORD ROW IS NOT COLUMN-LESS (#14572 review). `#5004` forces the key
    # present on every snapshot the chokepoint journals — `embedding: None` — so the
    # INSERT HAS the column and binds it to NULL. The arm asserts THAT shape: the column
    # is present, the value is None. The earlier cut asserted the column was ABSENT,
    # which is false about the production row and graded a dict the journal never holds.
    journalled = {**snapshot, "embedding": None}
    columns = _point_insert_columns({"type": "OperatorAdded", "point": journalled}).split(", ")
    assert "embedding" in columns, (
        "the operator's record row carries no `embedding` column even with the `#5004` "
        f"key forced present, so this arm is not measuring the production row: {columns}")
    assert journalled["embedding"] is None, journalled["embedding"]


def test_a_hybrid_operator_carries_its_vector_in_BOTH_stores(sdk):
    """The one operator shape WITH a vector, and it never needs a copy.

    `create_point(op_type=…)` writes `is_operator = false` WITH an `op_type` — the
    hybrid the canonical identity (`is_operator = true OR op_type IS NOT NULL`)
    counts as an operator. It has content, so it is embedded, and its `PointAdded`
    snapshot carries the vector — so the record's row is filled by the SAME write.

    ⛔ WHY THIS KILLS THE "extend the backfill with an `operator` slice" REPAIR: a
    per-kind copy would have exactly this population to copy, and it is never
    short — the write already lands it on both sides. The measured `fillable` for
    such a slice is zero by construction, not by luck.
    """
    with _Journal(sdk) as journal:
        hybrid = sdk._create_point("statement", "a hybrid operator claim",
                                   op_type="IMPL", embedding=_vector())
    whole, kinded = _graph_vector_counts(sdk)
    assert (whole, kinded) == (1, 1), (whole, kinded)

    record = journal.record("PointAdded")
    assert "embedding" in (record.get("point") or {}), sorted(record.get("point") or {})
    assert "embedding" in _point_insert_columns(record).split(", "), (
        "the hybrid operator's vector did not reach the record's INSERT")
    assert hybrid.get("is_operator") is False and hybrid.get("op_type") == "IMPL", hybrid


def test_the_operator_denominator_counts_the_SLICE_not_the_whole_label(sdk, monkeypatch):
    """The denominator fix, executed against a real engine and a real population.

    Two plain Points plus one hybrid operator: the OLD denominator would have read 3
    for `operator` (the whole `:Point` label) and no numerator could ever reach it.
    The kind-aware one reads 1, the slice the record's predicate actually counts — so
    the gate is satisfiable, and a record that holds the slice verifies
    SUBSTANTIVELY while a record that holds none stays UNVERIFIED with the repair
    named.
    """
    sdk._create_point("statement", "plain one", embedding=_vector(1))
    sdk._create_point("statement", "plain two", embedding=_vector(2))
    sdk._create_point("statement", "hybrid", op_type="IMPL", embedding=_vector(3))
    proj = sdk._get_proj()

    assert proj._label_vector_count("point") == 3
    assert proj._label_vector_count("operator") == 1, (
        "the denominator is still the whole `:Point` label — `operator` is "
        "unsatisfiable by arithmetic again")

    # The record side is STUBBED here (`_record_vector_count` below), so the tenant is
    # a literal: what this test measures is the GRAPH denominator, not the door.
    scope = record_vector._Scope("org_14560", "graph_14560")
    record_vector.reset_coverage_cache()
    try:
        for numerator, verified in ((1, True), (0, False)):
            monkeypatch.setattr(record_vector, "_record_vector_count",
                                lambda *a, _n=numerator, **k: _n, raising=True)
            record_vector.reset_coverage_cache()
            verdict = record_vector.coverage(
                "operator", scope, graph_population=proj._label_vector_count,
                record_dsn=None, now=0.0)
            assert verdict.verified is verified, verdict.as_dict()
            assert verdict.graph_vectors == 1, verdict.as_dict()
            assert verdict.record_vectors == numerator, verdict.as_dict()
    finally:
        record_vector.reset_coverage_cache()


# ── the consequence for a DESTRUCTIVE consumer (no database needed) ────────

def test_a_TRIVIAL_verdict_cannot_license_the_restore_drop(monkeypatch):
    """⛔ THE SIDE EFFECT THIS CHANGE HAD TO CLOSE, pinned without a database.

    `operator` was never the only thing standing between a DR restore and a dropped
    `:Point` vector — it just LOOKED that way, because its denominator could never
    verify. Making the denominator kind-aware makes the empty operator slice verify
    (``0 >= 0``, marked ``trivial``), and `hosted_backup._restore_retires_vector`
    drops a restored vector when every gated label of the node answers stop. A
    dropped restored vector has no other copy and `#5004` forbids re-encoding it, so
    a count that compared two zeroes must not license the drop: that door now asks
    with ``require_substantive=True``.

    Both directions are driven — a NON-trivial verified verdict still answers stop (so
    this is not an always-keep stub; asserted on `:Object`, whose drop is NOT held),
    a trivial one keeps the copy — and the plain `mirror_required` answer is asserted
    to be ``False`` in both, which is the issue's own acceptance condition and the
    reason the keyword could not simply be the default.
    """
    from tortoise.hosted_backup import _restore_retires_vector
    from tortoise.record_writer import use_record_tenant

    scope = ("org_14560", "graph_14560")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record_14560")

    with use_record_tenant(*scope):
        def _seat(record: int, graph: int) -> None:
            monkeypatch.setattr(record_vector, "_record_vector_count",
                                lambda *a, **k: record, raising=True)
            record_vector.reset_coverage_cache()
            for entity_type in ("point", "operator", "object"):
                verdict = record_vector.coverage(
                    entity_type, record_vector._Scope(*scope), graph_vectors=graph)
                assert verdict.verified is True, verdict.as_dict()

        try:
            # (1) TRIVIAL: the operator slice is empty in BOTH stores.
            _seat(0, 0)
            assert record_vector.mirror_required("operator") is False, (
                "the issue's acceptance condition: an empty operator slice must not "
                "keep a graph copy that does not exist")
            assert record_vector.mirror_required(
                "operator", require_substantive=True) is True, (
                "a TRIVIAL verdict licensed a DELETION — `0 >= 0` proves nothing about "
                "the row a restore would drop")
            assert _restore_retires_vector(["Point"]) is False, (
                "the DR restore would drop a `:Point` vector on a vacuous count; that "
                "vector has no other copy and `#5004` forbids re-encoding it")
            assert _restore_retires_vector(["Object"]) is False, (
                "the `require_substantive` rule is `:Point`-specific above only in "
                "WHERE it is hit; a vacuous `:Object` count may not license a drop "
                "either")

            # (2) SUBSTANTIVE: the same answer minus the vacuity stops the writer for
            # the labels the door may still act on — which is what makes (1) a rule
            # rather than an always-keep stub.
            _seat(2, 2)
            assert record_vector.mirror_required("operator") is False
            assert record_vector.mirror_required(
                "operator", require_substantive=True) is False
            assert _restore_retires_vector(["Object"]) is True, (
                "a substantively verified pair no longer retires the restored vector "
                "— the restore door was closed, not taught to ask")
            assert _restore_retires_vector(["Point"]) is False, (
                "`#14560` review F5: `:Point`'s drop is HELD until `#13971`'s per-ID "
                "ask lands — an aggregate count is not evidence about the row a "
                "restore would delete")
        finally:
            record_vector.reset_coverage_cache()


def test_the_Point_restore_drop_is_HELD_until_the_per_id_ask_lands(monkeypatch):
    """⛔ F5: the door `require_substantive` does NOT close, and why it is held shut.

    `require_substantive` refuses a VACUOUS aggregate. The aggregate that is merely
    SUFFICIENT is still an aggregate, and before `#14560` it could never reach `:Point`
    (the `operator` conjunct counted a different population, so it was never stop).
    Now it can — so `:Point`, the label whose ~39.6k vectors are the reason this gate
    exists, would begin retiring restored vectors on evidence that says nothing about
    the ROW being deleted, while `#5004` forbids re-encoding what is dropped and the
    per-ID ask that WOULD be evidence is `#13971`'s outstanding work.

    Pinned in both directions on the ONE declaration that says so: the hold is in
    force, AND `:Point` is still a gated label (so `#13971`'s gating and the strip's
    writer census are untouched). Mutating `_RESTORE_DROP_HELD` in either direction
    reds an arm here.
    """
    from tortoise import hosted_backup

    assert "Point" in hosted_backup._RESTORE_DROP_HELD, (
        "the hold was lifted without `#13971`'s per-ID ask — a restored `:Point` vector "
        "would now be dropped on an AGGREGATE count, and `#5004` forbids re-encoding it")
    assert "Point" in hosted_backup._GATED_RESTORE_LABELS, (
        "lifting the hold must not un-gate the label: the strip's writer census asks "
        "that set, so a `:Point` removed from it is an ungated writer again")

    # The hold is not a no-op on the decision: a node whose ONLY gated label is `:Point`
    # keeps its vector even when the label asks stop, while a non-held gated label in the
    # same position retires — so the arm is a hold, not a blanket refusal.
    monkeypatch.setattr(hosted_backup, "_GATED_RESTORE_LABELS", ("Point",))
    assert hosted_backup._restore_retires_vector(["Point"]) is False

    monkeypatch.setattr(hosted_backup, "_GATED_RESTORE_LABELS", ("Object",))
    monkeypatch.setattr(
        "tortoise.projection.entities._derived_mirror_required",
        lambda entity_type, **kw: False, raising=True)
    assert hosted_backup._restore_retires_vector(["Object"]) is True, (
        "the same shape with a NON-held label must still retire — otherwise the hold "
        "above is indistinguishable from a broken decision")
