"""`#13629` — the graph-side entity-vector retirement: its census and its stripper.

Two instruments are under test, and each test below is a falsifier for ONE of their
load-bearing claims — the shape `tests/test_dense_leg_probe_13108.py` established, and
for the same reason: an instrument whose own refusals are unpinned is an instrument
that fails OPEN the day someone deletes a branch.

| test | claim it falsifies | it goes RED when … |
|---|---|---|
| `test_the_retired_population_is_the_four_labels_and_never_point` | the population is the issue's four labels | `:Point` enters the set a destructive tool iterates |
| `test_affected_entity_types_is_derived_and_source_serves_document` | the `entity_type` consequence is DERIVED | a second spelling of the label↔type map appears (the `#11405` two-of-five cell) |
| `test_the_census_prints_the_unmatched_count_including_zero` | "**including zero**" | a parity row omits `graph_only` when it is 0 |
| `test_a_graph_only_vector_blocks_the_label_and_names_it` | a graph-only vector is a REFUSAL | the label reads `PARITY` while the graph holds the only copy |
| `test_an_unkeyed_vector_bearing_node_is_counted_as_graph_only` | an unmatchable node is not "absent from the record" | it is skipped, so a strip would delete it |
| `test_a_short_page_read_refuses_at_the_graph_leg` | the `RESULTSET_SIZE` truncation guard | a silently short page set certifies a partial population |
| `test_the_statement_shapes_are_valid_on_the_real_engine` | the statements are valid Cypher | the fake answers a shape the engine rejects (a broken keyset walk, a `REMOVE` that widened past `IN $keys`) |
| `test_a_record_payload_with_no_readable_vector_is_not_the_copy` | `embedding_vec IS NOT NULL` is the test | `embedding IS NOT NULL` certifies a row no dense query can answer with |
| `test_digest_is_order_independent_and_set_exact` | the digest is a SET token | a reordered plan digests differently, or an added key does not change it |
| `test_preview_removes_nothing_and_records_no_write` | preview is the default | `build_plan` issues a write |
| `test_execute_refuses_an_unreadable_plan_file` / `test_the_two_token_refusals_are_raised_by_main_before_any_write` | the two token preconditions | `--execute` can run from a live census, or from a file alone |
| `test_execute_refuses_a_plan_edited_after_it_was_printed` | the plan's own digest is checked | a doctored plan passes because `--confirm` matched the file |
| `test_execute_refuses_a_stale_plan_naming_the_delta` | the exact-set precondition | a population that moved between plan and execute is stripped anyway |
| `test_execute_refuses_a_graph_only_node_and_removes_nothing` | the parity refusal is BEFORE the first write | the only copy of a fingerprint is removed, or removed before the refusal |
| `test_execute_refuses_while_the_write_path_still_emits_vectors` | the write gate | a strip runs into a live writer and is silently undone |
| `test_write_gate_is_fail_closed_when_the_symbol_is_absent` / `…raises` / `…cannot_be_imported` | fail-closed in every unknown **of the gate read** (`record_vector.mirror_required`); the unguarded reads below it — `tortoise.security`'s label map inside `write_gate`, `parity.affected_entity_types` inside `_fold_consults_gate` — RAISE, and only the first can fire (the fold's read repeats that same import); in a census run a broken label map aborts at the census's own `affected_entity_types` read and `main` refuses with exit 3, while the catch around `strip_readiness` is defensive depth | an unreadable gate reads as permission |
| `test_write_gate_refuses_an_entity_type_with_no_declared_graph_label` | the `entity_type`→label mapping is the last refusal that can fire without a label | INVERT it and the arm itself certifies the unmapped type; DELETE it and certification comes from the steps below once those are forced (unforced they refuse one step further down, so only the forced half pins the arm) |
| `test_label_write_gate_requires_every_entity_type_addressing_the_label` | `:Source` serves TWO types | a strip lands on the document half while `source` still writes |
| `test_execute_strips_the_planned_set_and_reports_the_count` | the batched `REMOVE` | a partial strip reports a complete one |
| `test_execute_refuses_an_empty_plan_unless_expect_empty` | "nothing to strip" is a refusal | an empty plan reports a finished retirement |
| `test_execute_refuses_target_drift` | a plan is an authority over ONE graph | it is applied to a different graph of the same shape |
| `test_the_refusal_set_is_closed` | the token set is closed and published | a refusal is raised with an unpublished token |
| `test_a_swallowed_exit_does_not_certify_the_arm` | an exit is a write-stop only if it actually LEAVES | a `raise` a `try` handler or a context manager can swallow certifies the writer as write-stopped |
| `test_the_collision_clause_of_at_parity_stands_alone` | each `at_parity` clause has its OWN falsifier | the `render_collision == 0` clause is deleted and only `graph_only` refuses |
| `test_the_stop_arm_must_not_store_the_vector` | the stop arm stops — it does not place the vector first | a stop arm that STORES (then exits, or pops-then-stores) certifies the writer as write-stopped |
| `test_the_stop_arm_of_a_POSITIVE_ask_must_not_store_either` | the `else` of `if gate(l):` is the STOP arm, and it is read | a store on that arm (`orelse` never examined) certifies the writer as write-stopped |
| `test_upsert_source_IS_a_Source_writer_through_the_PASSTHROUGH` | `_upsert_source` persists a caller `embedding` via `SET n += $extra` | the name is dropped from `:Source`'s population on a static-clause count, letting a strip run into a live writer |
| `test_a_tools_only_diff_selects_this_pin` | these falsifiers actually RUN in CI | `tools/` stays a flat non-python prefix and this file is never selected |
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tools import strip_graph_entity_vectors as strip

#: ⛔ THE SAME MODULE OBJECT THE STRIPPER USES. `strip_graph_entity_vectors` puts its
#: own directory on `sys.path` and imports `entity_vector_parity_census` as a TOP-LEVEL
#: module, which is a DIFFERENT object from `tools.entity_vector_parity_census`. A test
#: that monkeypatched the package-qualified one would patch a module the code under
#: test never calls — the patch would appear to work and assert nothing.
parity = strip.parity


# ── a hermetic stand-in for the two stores ──────────────────────────────────


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _Node:
    def __init__(self, nid, key, has_embedding):
        self.nid = nid
        self.key = key
        self.has_embedding = has_embedding


_LABEL_RE = re.compile(r"MATCH \(n:(\w+)\)")


class FakeGraph:
    """Answers exactly the three statement shapes the census/stripper issue.

    Deliberately NOT a Cypher engine: a fake that parsed Cypher would pass on the day
    the real statement breaks. It matches the statement SHAPES this repo owns, and it
    counts writes so `test_preview_removes_nothing_and_records_no_write` can assert the
    preview issued none.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, list[_Node]] = {
            label: [] for _t, label, _k in parity.RETIRED}
        self._next = 0
        self.writes: list[str] = []
        #: Simulate FalkorDB's silent RESULTSET_SIZE truncation: the reads stop after
        #: this many rows TOTAL while the aggregate count still reports the whole label.
        self.page_cap: int | None = None
        self._served = 0
        #: Every REMOVE that actually reached the store, with its params — so a test can
        #: assert WHICH keys were removed, not merely how many.
        self.removal_calls: list[tuple[str, dict]] = []
        #: How many times the key-set page read ran, per label. `execute` makes exactly
        #: TWO (the live parity re-check and the digest comparison); a THIRD is the
        #: removal computing its own set — the set nobody approved.
        self.keyset_reads: dict[str, int] = {}
        #: A hook the failure test uses to make a REMOVE raise.
        self.fail_removal = False

    # ── seeding ────────────────────────────────────────────────────────────
    def add(self, label, key, has_embedding=True):
        self._next += 1
        node = _Node(self._next, key, has_embedding)
        self.nodes[label].append(node)
        return node

    def vector_keys(self, label):
        return {n.key for n in self.nodes[label]
                if n.has_embedding and n.key}

    # ── the two verbs ──────────────────────────────────────────────────────
    def ro_query(self, cypher, params=None):
        return _Result(self._answer(cypher, params, write=False))

    def query(self, cypher, params=None):
        self.writes.append(cypher)
        return _Result(self._answer(cypher, params, write=True))

    def _answer(self, cypher, params, *, write):
        label = _LABEL_RE.search(cypher).group(1)
        if "REMOVE n.embedding" in cypher:
            assert write, "a REMOVE must never travel the read-only verb"
            if self.fail_removal:
                raise RuntimeError("simulated store failure")
            self.removal_calls.append((cypher, dict(params or {})))
            keys = set((params or {}).get("keys") or ())
            hit = 0
            for node in self.nodes[label]:
                if node.key in keys and node.has_embedding:
                    node.has_embedding = False
                    hit += 1
            return [[hit]]
        if "RETURN count(n)" in cypher:
            self._served = 0
            return [[sum(1 for n in self.nodes[label] if n.has_embedding)]]
        if "ORDER BY id(n)" in cypher:
            assert not write, "the census page read must be read-only"
            self.keyset_reads[label] = self.keyset_reads.get(label, 0) + 1
            cursor = int((params or {}).get("cursor") or 0)
            batch = int((params or {}).get("batch") or 0)
            live = sorted(
                (n for n in self.nodes[label] if n.has_embedding and n.nid >= cursor),
                key=lambda n: n.nid)
            page = live[:batch]
            if self.page_cap is not None:
                room = max(0, self.page_cap - self._served)
                page = page[:room]
                self._served += len(page)
            return [[n.nid, n.key] for n in page]
        raise AssertionError(f"unexpected statement shape: {cypher}")


class Record:
    """A stand-in for `public.<label>`: node_key -> (readable?, payload?)."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, tuple[bool, bool]]] = {
            table: {} for table, _l, _k in parity.RETIRED}

    def holds(self, table, key):
        self.rows[table][key] = (True, True)

    def holds_unreadable(self, table, key):
        """A row whose `embedding` payload is present but whose `embedding_vec` is NULL."""
        self.rows[table][key] = (False, True)

    def reader(self, record_dsn, org_id, graph_id, table, keys):
        """The census's record leg, narrowed to the graph's key set.

        ⛔ The NARROWING IS PART OF THE CONTRACT being faked: the real leg reads one
        aggregate row plus only those rows whose `node_key` is in ``keys``. A fake that
        returned the whole table would still pass while the real read had become an
        unbounded full-table fetch.
        """
        rows = self.rows[table]
        wanted = set(str(k) for k in keys)
        return {
            "readable": {k for k, (readable, _p) in rows.items()
                         if readable and k in wanted},
            "unreadable": sum(1 for k, (r, p) in rows.items()
                              if p and not r and k in wanted),
            "record_readable_total": sum(1 for r, _p in rows.values() if r),
            "record_unreadable_total": sum(1 for r, p in rows.values() if p and not r),
            "rows": len(wanted & set(rows)),
        }


@pytest.fixture()
def stores(monkeypatch):
    graph, record = FakeGraph(), Record()
    monkeypatch.setattr(parity, "read_record_vector_keys", record.reader)
    return graph, record


def _census(graph, labels=None):
    return parity.census(g=graph, record_dsn="postgresql://x", org_id="org",
                         graph_id="graph", labels=labels)


def _row(report, label):
    return next(r for r in report["labels"] if r["label"] == label)


def _ready(monkeypatch):
    """Close the write gate — the state #13617 + the event/source write-stop reach."""
    monkeypatch.setattr(strip, "write_gate", lambda entity_type: (False, "write-stopped"))


def _plan(graph, *, labels=None, batch=strip.DEFAULT_BATCH):
    return strip.build_plan(
        g=graph, record_dsn="postgresql://x", org_id="org", graph_id="graph",
        graph_name="g", graph_uri="docker://x/g", labels=labels, batch=batch)


def _execute(graph, plan, **over):
    kwargs = {
        "g": graph, "plan": plan, "record_dsn": "postgresql://x", "org_id": "org",
        "graph_id": "graph", "graph_name": "g",
        "graph_uri": strip.uri_fingerprint("docker://x/g"),
        "confirmed": plan["digest"],
        "batch": strip.DEFAULT_BATCH, "expect_empty": False,
    }
    kwargs.update(over)
    return strip.execute(**kwargs)


# ── the population ──────────────────────────────────────────────────────────


def test_the_retired_population_is_the_four_labels_and_never_point():
    """`#13629`'s population, and its scope boundary, in one place.

    `:Point` is owned by the armed cutover and `#13330`; the issue's brief forbids
    touching it. A destructive tool whose population grew by one label would strip the
    Point vectors `#5004` calls record truth, so the exclusion is asserted BY NAME
    rather than as a count that a future edit could preserve while widening the set.
    """
    assert [label for _t, label, _k in parity.RETIRED] == [
        "Object", "Subject", "Event", "Source"]
    assert "Point" not in {label for _t, label, _k in parity.RETIRED}
    assert parity.NOT_IN_SCOPE == ("Point",)


def test_affected_entity_types_is_derived_and_source_serves_document():
    """The `entity_type` consequence comes from the ONE label↔type map.

    `:Source` answers both `source` and `document` (`tortoise/security.py`), which is
    the fact `#11405` records as under-reported (two of five) in the epic's own table.
    Deriving it here is what keeps this instrument's population statement honest.
    """
    assert parity.affected_entity_types("Source") == ["document", "source"]
    assert parity.affected_entity_types("Object") == ["object"]
    assert parity.affected_entity_types("Subject") == ["subject"]
    assert parity.affected_entity_types("Event") == ["event"]


# ── the census: three numbers per label, zeros included ─────────────────────


def test_the_census_prints_the_unmatched_count_including_zero(stores):
    """`graph_only` is printed for EVERY label, including when it is 0.

    An absent column is how a gate reports a pass without having looked; `#11112`
    is this repo's recorded instance of exactly that (a census that CREATED the graph
    it then certified). So the zero must be present, and the verdict must be derived
    from it rather than from its absence.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    report = _census(graph, labels=["Object"])
    row = _row(report, "Object")
    assert row["graph_only"] == 0
    assert row["unkeyed_graph_nodes"] == 0
    assert row["record_vectors"] == 1
    assert row["graph_vectors"] == 1
    assert row["verdict"] == "PARITY"
    assert row["at_parity"] is True
    # …and the human render says so on the row rather than omitting it.
    text = parity.render({"labels": [row], "at": "now", "ready": True}, "g")
    assert "graph_only" in text
    # every column, zeros included: graph, record, graph_only, unkeyed, coll, rec_unread,
    # rec_only, rec_total, verdict
    assert re.search(r"Object\s+1\s+1\s+0\s+0\s+0\s+0\s+0\s+1\s+PARITY", text)


def test_a_graph_only_vector_blocks_the_label_and_names_it(stores):
    """A graph vector the record cannot answer with is a REFUSAL, per label."""
    graph, record = stores
    graph.add("Object", "obj-1")
    graph.add("Object", "obj-2")
    record.holds("object", "obj-1")
    report = _census(graph, labels=["Object"])
    row = _row(report, "Object")
    assert row["graph_only"] == 1
    assert row["graph_only_sample"] == ["obj-2"]
    assert row["verdict"] == "GRAPH-ONLY(1)"
    assert row["at_parity"] is False
    assert report["ready"] is False
    assert "backfill" in (row["blocked_reason"] or "").lower()


def test_an_unkeyed_vector_bearing_node_is_counted_as_graph_only(stores):
    """A vector-bearing node with no identity key is graph-only BY CONSTRUCTION.

    It cannot be named in a plan (there is no key to match it on), so a census that
    skipped it would let a strip delete the only copy of its fingerprint while
    reporting a clean parity table. Counted, separately AND into `graph_only`.
    """
    graph, record = stores
    graph.add("Object", None)          # carries a vector, holds no id
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    row = _row(_census(graph, labels=["Object"]), "Object")
    assert row["unkeyed_graph_nodes"] == 1
    assert row["graph_only"] == 1
    assert row["verdict"] == "GRAPH-ONLY(1)"
    assert row["at_parity"] is False


def test_a_record_payload_with_no_readable_vector_is_not_the_copy(stores):
    """`embedding IS NOT NULL` is NOT the parity test — `embedding_vec` is.

    `public.record_embedding_vector` NULLs anything that is not a 384-long all-numeric
    array, so a row can hold a payload no dense query can answer with. Treating that as
    "the record has it" would licence deleting the graph's only readable copy.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds_unreadable("object", "obj-1")
    row = _row(_census(graph, labels=["Object"]), "Object")
    assert row["record_unreadable_vector"] == 1
    assert row["record_vectors"] == 0
    assert row["graph_only"] == 1
    assert row["at_parity"] is False


def test_a_short_page_read_refuses_at_the_graph_leg(stores):
    """FalkorDB truncates SILENTLY at `RESULTSET_SIZE`; a short read is a REFUSAL.

    A silently short page set would report a graph-only node as absent FROM THE GRAPH
    and licence its strip, so the aggregate count is the load-bearing guard.
    """
    graph, _record = stores
    for index in range(5):
        graph.add("Object", f"obj-{index}")
    graph.page_cap = 2  # the engine answers 2 of 5
    with pytest.raises(parity.CensusError, match="SHORT read"):
        parity.read_graph_vector_keys(graph, "Object", "id")


def test_the_statement_shapes_are_valid_on_the_real_engine(tmp_path):
    """The three statements, executed against a REAL FalkorDB — not against the fake.

    A fake that answers by substring would pass on the day the statement breaks, so the
    shapes this instrument depends on are also run once for real: the aggregate count,
    the keyset page walk (with a page size of 1, so two pages), the `ro_query` verb
    (which the engine REJECTS for a write — that is the point of using it), and the
    batched `REMOVE`, twice, to prove the second application is a no-op.

    Hermetic: an embedded engine under `tmp_path`, no DSN, no network, no model call.
    """
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection(str(tmp_path / "g.db"), graph_name="smoke_census_13629",
                            skip_health_check=True, ensure_indexes=False)
    graph = proj.g
    graph.query("CREATE (o:Object {id:'obj-1', name:'a'}) SET o.embedding = vecf32([0.1,0.2])")
    graph.query("CREATE (o:Object {id:'obj-2', name:'b'}) SET o.embedding = vecf32([0.3,0.4])")
    graph.query("CREATE (o:Object {id:'obj-3', name:'c'})")  # no vector: not the population

    assert parity.read_graph_vector_keys(graph, "Object", "id") == {
        "count": 2, "keys": ["obj-1", "obj-2"], "text": ["obj-1", "obj-2"],
        "unkeyed": 0}

    original = parity.GRAPH_PAGE_SIZE
    parity.GRAPH_PAGE_SIZE = 1  # force the keyset walk across two pages
    try:
        assert sorted(parity.read_graph_vector_keys(
            graph, "Object", "id")["keys"]) == ["obj-1", "obj-2"]
    finally:
        parity.GRAPH_PAGE_SIZE = original

    statement = (
        "MATCH (n:Object) WHERE n.embedding IS NOT NULL AND n.id IN $keys "
        "REMOVE n.embedding RETURN count(n)")
    assert graph.query(statement, params={"keys": ["obj-1", "obj-2"]}).result_set == [[2]]
    assert graph.query(statement, params={"keys": ["obj-1"]}).result_set == [[0]]
    assert parity.read_graph_vector_keys(graph, "Object", "id")["count"] == 0
    # The untouched node keeps its (absent) vector state — the statement never widened
    # beyond its `IN $keys` set.
    assert graph.query("MATCH (n:Object {id:'obj-3'}) RETURN count(n)").result_set == [[1]]


def test_a_typed_identity_key_needs_the_VALUE_and_the_renderings_are_not_it(tmp_path):
    """⛔ WHY THE PLAN CARRIES THE RAW VALUE — MEASURED on the real engine, not assumed.

    Cypher `IN` is equality and equality is TYPE-SENSITIVE: an integer `:Object.id` is not
    selected by `n.id IN ["123"]`. A plan built from `str()` renderings therefore removes
    NOTHING on exactly the population that motivated accepting typed keys, and the run then
    reports a residual as a *concurrent writer* — a diagnosis that sends an operator to the
    wrong system and whose own remedy ("re-plan") reproduces the identical plan.

    ⛔ AND THE OBVIOUS REPAIR — compare `toString(n.<key>) IN $keys` — IS NOT ONE. The
    engine's `toString` is not Python's `str`: `toString(1.5)` is `1.500000` while `str(1.5)`
    is `1.5`. Only the value as the store holds it round-trips, which is why
    `read_graph_vector_keys` returns the typed key AND its rendering as two fields.

    Mutation: drop the raw value from `read_graph_vector_keys` (append `str(node_key)`
    instead of `node_key`) and the first removal assertion below reds.
    """
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection(str(tmp_path / "g.db"), graph_name="typed_key_13629",
                            skip_health_check=True, ensure_indexes=False)
    graph = proj.g
    graph.query("CREATE (o:Object {id:123, name:'int'}) "
                "SET o.embedding = vecf32([0.1,0.2])")
    graph.query("CREATE (o:Object {id:'456', name:'text'}) "
                "SET o.embedding = vecf32([0.3,0.4])")

    read = parity.read_graph_vector_keys(graph, "Object", "id")
    # ONE identity, TWO forms — both are carried, because each answers a different store.
    assert read["keys"] == [123, "456"]
    assert read["text"] == ["123", "456"]
    # …and an all-string label is unaffected: the same value in both fields.
    assert parity.read_graph_vector_keys(graph, "Subject", "id") == {
        "count": 0, "keys": [], "text": [], "unkeyed": 0}

    statement = (
        "MATCH (n:Object) WHERE n.embedding IS NOT NULL AND n.id IN $keys "
        "REMOVE n.embedding RETURN count(n)")
    # THE DEFECT, reproduced: the rendered key selects nothing on the integer node.
    assert graph.query(statement, params={"keys": ["123"]}).result_set == [[0]]
    assert parity.read_graph_vector_keys(graph, "Object", "id")["count"] == 2
    # THE FIX: the value as the store holds it selects it.
    assert graph.query(statement, params={"keys": [123]}).result_set == [[1]]
    assert parity.read_graph_vector_keys(graph, "Object", "id")["count"] == 1
    # …and `toString` would NOT have been a safe substitute (this is the trap, not a fix).
    assert graph.query("RETURN toString(1.5), toString(1e20)").result_set == [
        ["1.500000", "100000000000000000000.000000"]]
    assert [str(1.5), str(1e20)] == ["1.5", "1e+20"]


def test_a_graph_that_cannot_be_counted_refuses(stores):
    """A failed count is "unmeasurable", never an empty label."""
    graph, _record = stores

    def boom(*_a, **_k):
        raise RuntimeError("engine down")

    graph.ro_query = boom
    with pytest.raises(parity.CensusError, match="could not be read"):
        parity.read_graph_vector_keys(graph, "Object", "id")


# ── the digest is a TOKEN over an exact set ─────────────────────────────────


def test_digest_is_order_independent_and_set_exact():
    """Two properties, both load-bearing, and neither is a style choice.

    Sorted ROWS (not a JSON blob) so re-serialising the plan file cannot change what
    was approved; whole-set so ONE key moving changes the token.
    """
    a = strip.digest([("Object", "obj-1"), ("Subject", "sub-1")])
    b = strip.digest([("Subject", "sub-1"), ("Object", "obj-1")])
    assert a == b
    assert a.startswith("sha256:")
    assert strip.digest([("Object", "obj-1")]) != a
    assert strip.digest([("Object", "obj-2"), ("Subject", "sub-1")]) != a
    # A key that moved LABEL is a different node, not the same one.
    assert strip.digest([("Object", "sub-1")]) != strip.digest([("Subject", "sub-1")])


# ── the write gate: fail-closed in every unknown ────────────────────────────


def test_write_gate_is_fail_closed_when_the_symbol_is_absent(monkeypatch):
    """⛔ The polarity is INVERTED from the gate's own, deliberately.

    `mirror_required` answers `True` (= write) on an unknown because writing costs
    bytes while skipping costs the row. Here an unknown must answer "still emits",
    because *"I could not read the gate"* must never licence a delete.
    """
    monkeypatch.setattr(strip, "WRITE_GATE_SYMBOL", "_absent_symbol_13629")
    emits, reason = strip.write_gate("object")
    assert emits is True
    assert "_absent_symbol_13629" in reason
    assert "#13617" in reason


def test_write_gate_is_fail_closed_when_the_gate_raises(monkeypatch):
    from tortoise import record_vector

    def boom(_entity_type):
        raise RuntimeError("coverage cache unreadable")

    monkeypatch.setattr(record_vector, "mirror_required", boom, raising=False)
    emits, reason = strip.write_gate("object")
    assert emits is True
    assert "not a permission" in reason


def test_write_gate_is_fail_closed_when_the_gate_module_cannot_be_imported(monkeypatch):
    """⛔ THE THIRD UNKNOWN OF THE GATE READ, and the one that had no falsifier before this
    test.

    ⛔ AND "EVERY UNKNOWN" MEANS EVERY UNKNOWN OF THE GATE READ, not of `write_gate`: the
    reads below it — `from tortoise.security import ENTITY_TYPE_LABELS` inside `write_gate`,
    and `parity.affected_entity_types(label)` inside `_fold_consults_gate` — are unguarded
    and RAISE rather than answering `(True, …)`. Measured: with the record predicate
    answering `False` (as it must be for those reads to be reached at all) the raise SITE
    is the former and never the latter — the fold's read is preceded by that same import,
    which `parity.affected_entity_types` repeats, so only one of the two can fire first.
    With the predicate answering `True` the gate refuses above both and nothing raises.

    Still no delete licence is ever minted, and the net is measured PER COMPONENT rather
    than per read:

    * in a CENSUS run a broken label map never reaches `write_gate` at all: the census
      performs the same read itself first (its own `affected_entity_types(label)` call,
      before any `strip_readiness`), so it escapes `census()` and `main()` REFUSES the run
      (exit 3, "A census that cannot be run is a refusal, not a pass");
    * the `except` around `strip_readiness` inside `census` (`ready, reason = False, …`, so
      that one label is blocked and the census completes) is therefore DEFENSIVE DEPTH —
      measured to fire for a raise `write_gate` does not currently produce.

    ⚠️ Line numbers are deliberately not cited here. The justification is measured rather
    than assumed: of the four citations this pair of tests ever carried, ONE pointed at a
    line that moved and had to be re-derived twice in this PR, and the decision is to stop
    writing claims that rot rather than to keep re-deriving them.

    A falsifier for those reads is a follow-up, not this test.

    `write_gate` imports the MODULE inside its own `try` (so a failed import becomes a
    fail-closed answer instead of an `ImportError`) and resolves the SYMBOL by name outside
    it (`getattr(record_vector, WRITE_GATE_SYMBOL, None)`), so an unlanded symbol answers
    "ABSENT" rather than raising — and the IMPORT ITSELF raising is a different unknown
    again, from `…raises` (which drives a gate that raises *after* a successful import).
    ⛔ THE FIGURE BELOW IS A PRE-FIX BASE MEASUREMENT, and it has to say so — it has been
    re-reported by two rounds because it read as a statement about this head: at the base
    this test was added to, inverting its polarity to `return False` left the file green
    (**measured at that base: 90 passed**). At THIS head the same mutant yields
    **`1 failed, 91 passed`** (measured at `975feae7`), failing on the VERDICT
    (`assert emits is True`) before either message assert. The polarity here is the
    deliberate inversion the docstring states: a module we cannot import proves nothing
    about any writer, so it must never licence a delete.
    """
    import sys

    import tortoise

    monkeypatch.delattr(tortoise, "record_vector", raising=False)
    monkeypatch.setitem(sys.modules, "tortoise.record_vector", None)

    emits, reason = strip.write_gate("object")
    assert emits is True, f"an unimportable gate licensed a delete ({reason})"
    assert "could not be imported" in reason
    assert "STILL EMITTING" in reason


def test_write_gate_refuses_an_entity_type_with_no_declared_graph_label(monkeypatch):
    """⛔ The `entity_type`→label mapping is the last refusal that can fire WITHOUT a label:
    everything below this arm — `_fold_consults_gate(label)` and its population check — is
    asked about a LABEL, and there is exactly one way to get one. ⛔ THE FIGURE HERE IS A
    PRE-FIX BASE MEASUREMENT: at the base this test was added to, inverting the arm to
    `return False` left the file green (**measured at that base: 90 passed**); at THIS head
    the same mutant yields **`1 failed, 91 passed`** (measured at `975feae7`), on the
    VERDICT at the `⛔ POLARITY FIRST` assert. The arm is
    REACHABLE — but only because this test drives the predicate that gates it, and that is
    worth stating rather than assuming: on this tree `record_vector.mirror_required` answers
    `True` for an unmapped `entity_type` **deterministically** (it returns `True` for
    anything outside `COVERAGE_GATED_LABELS`, and that cover is exactly the key set of
    `ENTITY_TYPE_LABELS`, so "unmapped" IS "not coverage-gated" — measured). Unpatched,
    `write_gate("no_such_entity_type_13647")` therefore refuses one step ABOVE this arm, at
    `if needed:`, with the `answers True` reason. Driving the predicate to `False` below is
    what makes the arm the deciding step — and ⛔ it is a state NO OPERATOR REACHES TODAY:
    `mirror_required` short-circuits `if entity_type not in COVERAGE_GATED_LABELS: return
    True` *before* arming and coverage are ever consulted, so with the armed-and-covered
    state forced true end-to-end an unmapped type still answers `True` (measured). The stub
    therefore exercises this arm as DEFENCE IN DEPTH against a future cover/type divergence,
    not as a refusal an operator gets.

    ⛔ WHAT THIS ARM IS *NOT*, MEASURED, because an earlier revision of this docstring
    claimed it and the claim was false. It is NOT the only thing standing between an
    unmapped type and a delete licence: with the arm **deleted** AND the record predicate
    driven to `False` as this test drives it, `write_gate("no_such…")`
    still answers `emits is True`, refused one step further down — by `_writers_for`'s
    empty-population branch (`_register_writers(None)` is `()`, because 0 of the 24 DECLARED
    `EMBED_WRITER_CLASSES` entries — 10 with a live label tuple, 14 declared `NO_LIVE_VECTOR`
    — carry `LABELS_AT_RUNTIME`), and, with a population
    forced, by `_fold_consults_gate`'s ungated branch (`affected_entity_types(None)` is
    `[]`, so every writer lands in `ungated`). So the arm is *defence in depth plus an
    accurate reason*, not the sole refusal. ⛔ The difference shows up in PRODUCT behaviour,
    not in this test: delete the arm and force nothing, and `write_gate` still answers
    `emits is True` — only the reason moves. That is exactly why this test forces the steps
    below it, so the DELETION reds on the verdict instead of on a string.

    What the FORCING block (the first half, `⛔ POLARITY FIRST`) therefore asserts is the
    POLARITY the arm owns: with everything below it forced to CERTIFY (`_fold_consults_gate`
    answering `(True, …)`), the arm is the only remaining refusal. Arm present → `emits is
    True` and this reason; arm deleted → `emits is False`, i.e. a delete licence minted for a
    label nothing serves (measured). The UNFORCED half that follows is a different claim —
    that with the predicate already driven to `False` the gate refuses at this arm with
    nothing below it forced — and it cannot pin the arm: with
    the arm deleted it still answers `emits is True` and only its message assertion reds. So
    ⛔ do not drop the forcing half as redundant: that is exactly the message-text-only kill
    this test exists to replace.

    ⛔ AND THE UNFORCED HALF IS NOT EVIDENCE ABOUT THE FOLD, which an earlier revision of
    this docstring claimed ("the FOLD needs no forcing — it already declines"). The arm
    returns at `if label is None:`, which is ABOVE `_fold_consults_gate`, so the fold is
    never consulted on that path at all: measured with a spy on
    `strip._fold_consults_gate` at `975feae7`, the second half makes **0 invocations**. The
    half therefore pins that the arm fires with NO forcing below it — a real claim about
    this tree — and says nothing about whether the fold would decline.
    """
    from tortoise import record_vector

    monkeypatch.setattr(record_vector, "mirror_required", lambda entity_type: False,
                        raising=False)

    # ⛔ POLARITY FIRST, so the DELETION of the arm reds on the VERDICT and not merely on the
    # message text: every step below the arm is forced to certify, leaving it the only refusal.
    monkeypatch.setattr(strip, "_fold_consults_gate",
                        lambda label: (True, f"every {label!r} writer calls the gate"))
    emits, reason = strip.write_gate("no_such_entity_type_13647")
    assert emits is True, (
        "with every step below it certifying, an unmapped entity_type was still let through, "
        f"so the strip would be licensed on a label nothing serves ({reason})")
    assert "no declared graph label" in reason

    # …and the UNFORCED configuration refuses at the SAME arm (the record predicate is still
    # driven to `False` — see the docstring on why it must be). ⛔ This half pins that the arm
    # fires with nothing below it forced; it is NOT evidence about the fold, which this path
    # never reaches (`if label is None:` returns above it — measured: 0 invocations).
    monkeypatch.undo()
    monkeypatch.setattr(record_vector, "mirror_required", lambda entity_type: False,
                        raising=False)
    emits, reason = strip.write_gate("no_such_entity_type_13647")
    assert emits is True, f"an unmapped entity_type licensed a delete ({reason})"
    assert "no declared graph label" in reason


def test_write_gate_does_not_trust_the_record_predicate_alone(monkeypatch):
    """⛔ THE P1 FINDING, AS A FALSIFIER: coverage is a READ fact, not a WRITE fact.

    On the tree that introduces `mirror_required` (`#13610`'s write half), the predicate
    is answered from `COVERAGE_GATED_LABELS`, which is **every served label** — while the
    gate is consulted at exactly TWO folds (`_upsert_subject`, `_upsert_object`). So
    `mirror_required("event")` answers `False` while `_upsert_event` still writes
    `embedding` on every event. A tool that trusted the predicate alone would declare
    `:Event` write-stopped and strip it into a live writer.

    A mutation that drops the `_fold_consults_gate` call reds this test.
    """
    from tortoise import record_vector

    monkeypatch.setattr(record_vector, "mirror_required",
                        lambda entity_type: False, raising=False)
    # The recorded predicate says "not required" for every label; that is a READ fact and
    # the gate must still answer "emits" while a writer exists that nobody stopped.
    emits, reason = strip.write_gate("object")
    assert emits is True
    assert "writes `embedding` without an `if`" in reason
    # ⛔ ASSERT ON THE WRITER THAT MUST STILL BE UNGATED, NOT ON THE FOLD.
    # `_upsert_object`/`_upsert_subject` are exactly what `#13617` write-stops, so pinning
    # the refusal to THEM makes this test red the moment #13617 lands — whichever PR merges
    # second inherits a failure caused by the other. `sdk::_update_entity` stores the
    # property through `SET n += $props` and is not part of #13617, so a set-based
    # assertion stays true before AND after it.
    # ⛔ ONE ASSERTION, AND IT CARRIES BOTH CLAIMS: the writer must be named at all, and it
    # must be one the FOLD MAP ALONE MISSED — `sdk::_update_entity` re-adds a
    # caller-supplied `embedding` through `SET n += $props`, so a gate that cleared the
    # `entities.py` folds alone would license a strip its own refusal claims to prevent.
    # The register-vs-fold-map population is pinned by
    # `test_the_writer_population_comes_from_the_REGISTER_not_only_the_fold_map`, which can
    # fail independently; an assertion repeated here could only fail when this one does.
    assert "_update_entity" in reason


def test_a_comment_mentioning_the_gate_is_not_a_gate(tmp_path, monkeypatch):
    """⛔ F3's real fold: the witness is a CALL, and `entities.py` is the tree under test.

    The two gate tests above drive fake modules; this one pins the REAL module, because the
    only way to know the check can read `entities.py` at all is to point it there. On this
    tree the folds do not consult the gate, so the answer must be "still emits" AND the
    reason must name `entities.py` — if the AST walk silently failed to find the fold, an
    "ABSENT" reason would pass a weaker assertion.

    ⛔ AND IT MUST PASS THE LABEL'S `entity_types`. Without them `_writer_consults_gate`
    computes `wanted = ()` and `wanted and _asks_the_gate(...)` short-circuits, so the
    refusal comes from the empty-`wanted` arm and says nothing about the AST walk: a
    `_asks_the_gate -> True` mutant leaves the assertion green (round 8's F2). With
    `("event",)` the mutant reds this test.
    """
    gated, why = strip._writer_consults_gate(
        "tortoise.projection.entities", "_upsert_event", ("event",))
    assert gated is False
    assert "tortoise.projection.entities::_upsert_event" in why
    assert "without an `if`" in why


def _fake_writer_module(tmp_path, monkeypatch, body, name="fake_writer_13629"):
    path = tmp_path / f"{name}.py"
    path.write_text(body)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    strip._writer_sources().pop(name, None)     # a fresh read per case
    return name


WRITER_PROSE_ONLY = """
def _upsert_object(ev):
    # TODO: consult mirror_required once the record covers this label
    return _write(ev)
"""

WRITER_DEAD_BRANCH = """
def _upsert_object(ev):
    if False:
        mirror_required("object")
    return _write(ev)
"""

#: ⛔ THE DEAD-*STORE* TWIN of `WRITER_DEAD_BRANCH`, and the fail-OPEN opposite direction:
#: the guard walk already dropped a dead GUARD, but the STORE walk did not, so a store that
#: never runs certified the guarded arm while the vector was written unconditionally below
#: it. A dead store is not evidence of a store.
WRITER_DEAD_STORE = """
def _upsert_object(props):
    if _derived_mirror_required("object"):
        if False:
            embedding = _encode(props)
    props["embedding"] = vec
    return _write(props)
"""

WRITER_UNCALLED = """
def _upsert_object(ev):
    mirror_required("object")
    return _write(ev)
"""


#: A dict writer's write-stop: remove the property from the props before `SET n += $props`.
#: ⛔ AND IT MUST BE NEGATED. `mirror_required` asks "must the GRAPH still store?", so the
#: removal runs when the answer is NO — `if not gate("object"): props.pop("embedding")`,
#: which is the repo's own idiom. Popping on the POSITIVE answer deletes the property
#: exactly when the graph is supposed to keep it: not a write-stop, so it is refused below.
WRITER_POPS_THE_KEY = """
def _upsert_object(props):
    if not _derived_mirror_required("object"):
        props.pop("embedding", None)
    return _write({"SET n += $props": props})
"""

WRITER_SUBSCRIPT_STORE = """
def _upsert_object(props):
    if _derived_mirror_required("object"):
        props["embedding"] = _encode(props)
    return _write(props)
"""

#: …and the same removal under the POSITIVE ask is not a write-stop.
WRITER_POPS_ON_THE_MIRROR_ARM = """
def _upsert_object(props):
    if _derived_mirror_required("object"):
        props.pop("embedding", None)
    return _write({"SET n += $props": props})
"""

WRITER_POPS_ANOTHER_KEY = """
def _upsert_object(props):
    embedding = _encode(props)
    if _derived_mirror_required("object"):
        props.pop("other", None)
    return _write(props, embedding)
"""

#: ⛔ THE SHAPE `#13617` ACTUALLY IMPLEMENTS — an ENCODE GUARD with no `return`. The
#: accepted form is "an `if` that asks about THIS label and whose body touches the store",
#: and requiring an early exit instead (an earlier version did) refused the only write-stop
#: that exists, which made the gate unable to open on the fold that asks.
WRITER_ENCODE_GUARD = """
def _upsert_object(ev):
    embedding = None
    if _derived_mirror_required("object"):
        embedding = _encode(ev)
    return _write(ev, embedding)
"""

WRITER_GATED = """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        return None
    return _write(ev)
"""

#: Shapes that CALL the gate and write anyway. Each is refused: the call must ask about
#: THIS label, must not be short-circuited away, and its branch must EXIT.
WRITER_CALL_BUT_UNGATED = (
    # asks about a DIFFERENT label (a five-label writer's partial write-stop)
    """
def _upsert_object(ev):
    if mirror_required("source"):
        return None
    return _write(ev)
""",
    # a computed argument the tool cannot attribute to this label
    """
def _upsert_object(ev):
    kind = "object"
    if mirror_required(kind):
        return None
    return _write(ev)
""",
    # short-circuited: the branch is taken either way
    """
def _upsert_object(ev):
    if mirror_required("object") or True:
        return _write(ev)
    return None
""",
    # a call that decides nothing: the write runs regardless
    """
def _upsert_object(ev):
    if mirror_required("object"):
        _bump_counter()
    return _write(ev)
""",
)

#: A gate call that is RETURNED while the write has already happened — the "dying
#: declaration" one line further along. None of these may count as gated.
WRITER_RETURNED_NOT_DECIDING = (
    """
def _upsert_object(ev):
    self._write(ev)
    return mirror_required("object")
""",
    """
def _upsert_object(ev):
    return (self._write(ev), mirror_required("object"))
""",
    """
def _upsert_object(ev):
    return self._write(ev) or mirror_required("object")
""",
)

#: ⛔ A `RETURN` THAT CARRIES A WRITE IS A WRITE, not an exit — `if not gate(l): return
#: self._write(ev)` stores on exactly the arm the gate said stop. Judged by the same
#: polarity rule as an assignment (this shape was ACCEPTED by an earlier revision).
WRITER_RETURNS_A_WRITE_WHEN_STOPPING = """
def _upsert_object(ev):
    if not mirror_required("object"):
        return self._write(ev)
    return None
"""

#: …while these DO decide, and refusing them would make the gate unsatisfiable. The first is
#: the CORRECT polarity of `#13617`'s shape spelled the other way round (write on the mirror
#: arm); the second is the negated early exit. Both must stay accepted, or the gate is an
#: inert enforcer that no correct implementation could satisfy.
WRITER_RETURNED_AND_DECIDING = (
    """
def _upsert_object(ev):
    if not mirror_required("object"):
        return None
    return self._write(ev)
""",
    """
def _upsert_object(ev):
    if ev and not mirror_required("object"):
        return None
    return self._write(ev)
""",
)


def test_the_gate_check_asks_whether_the_writer_CALLS_it_into_control_flow(
        tmp_path, monkeypatch):
    """⛔ THE P1 FALSIFIER: a MENTION is not a gate, and neither is a dying call.

    The first version of this check was a substring search over the function's source
    lines. Three shapes cleared it while the write went ungated, and all three are
    realistic:

    * a COMMENT saying `# TODO: consult mirror_required` — precisely what a WIP write-stop
      leaves behind, in the fold that never asks;
    * a DEAD `if False:` branch;
    * a bare `mirror_required(...)` expression statement whose answer nothing reads.

    Mutation: revert `_asks_the_gate` to `any(name in body)` and the first three cases
    below go green-to-red (i.e. this test fails), while the fourth must stay gated.
    """
    for source in (WRITER_PROSE_ONLY, WRITER_DEAD_BRANCH, WRITER_UNCALLED,
                   WRITER_DEAD_STORE):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"{source!r} was accepted as a gate"
        assert "without an `if`" in why

    for source in WRITER_CALL_BUT_UNGATED:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"an un-attributed / short-circuited gate was accepted: {source!r}"

    for source in WRITER_RETURNED_NOT_DECIDING:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a RETURNED-but-dying gate was accepted: {source!r}"

    module = _fake_writer_module(tmp_path, monkeypatch, WRITER_RETURNS_A_WRITE_WHEN_STOPPING)
    gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
    assert gated is False, "a `return <write>` under a NEGATED ask opened the gate"

    # ⛔ AND AN EXIT IS NOT POLARITY-BLIND EITHER. Closing "a `return` that carries a write"
    # left the BARE exit accepted on BOTH arms, so the review's leading example —
    # `if mirror_required("object"): return None`, which exits on the runs the vector must
    # be written and stores on every run a strip can happen — was still certified gated
    # (`measured at df345c22: GATED`). A bare exit is a write-stop only on the negated arm.
    for source in ("""
def _upsert_object(ev):
    if mirror_required("object"):
        return None
    return _write(ev)
""", """
def _upsert_object(ev):
    if ev and mirror_required("object"):
        return None
    return _write(ev)
""", """
def _upsert_object(ev):
    if mirror_required("object"):
        raise RuntimeError("the graph must still store this")
    return _write(ev)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"an INVERTED early exit opened the gate: {source!r}"

    for source in WRITER_RETURNED_AND_DECIDING:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        assert strip._writer_consults_gate(
            module, "_upsert_object", ("object",))[0] is True, source

    # ⛔ AND THE ENCODE-GUARD FORM — the shape `#13617` implements, and the one that decides
    # whether the gate can EVER open. Asserted here on purpose: an earlier revision DEFINED
    # this shape and never used it, so round 5's P1 fix was unpinned and reverting it left
    # every test green.
    module = _fake_writer_module(tmp_path, monkeypatch, WRITER_ENCODE_GUARD)
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is True

    # …and the guard must STORE, not MENTION: "prose is not evidence" applies one level
    # below the mention check too. Mutation: accept any embedding-named argument or string
    # inside the guarded branch and both cases below go green.
    for mention in ('_log("object embedding still mirrored")', "print(embedding)"):
        module = _fake_writer_module(tmp_path, monkeypatch, f"""
def _upsert_object(ev):
    embedding = _encode(ev)
    if _derived_mirror_required("object"):
        {mention}
    return _write(ev, embedding)
""")
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a {mention!r} MENTION opened the gate"

    # ⛔ MASKING THE ANSWER IS NOT ASKING IT. A two-token blacklist (`not`, `or`) left every
    # other way of inverting or wrapping the call accepted — and `is False` is not exotic:
    # this repo already spells it that way in its write paths. The gate call must BE the
    # operand of the branch test.
    for masked in ('_derived_mirror_required("object") is False',
                   '_derived_mirror_required("object") == False',
                   '_derived_mirror_required("object") != True',
                   'bool(_derived_mirror_required("object")) is False',
                   '{True: False, False: True}[_derived_mirror_required("object")]',
                   'all((_derived_mirror_required("object"), True))'):
        module = _fake_writer_module(tmp_path, monkeypatch, f"""
def _upsert_object(ev):
    if {masked}:
        embedding = _encode(ev)
    return _write(ev, embedding)
""")
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a MASKED ask opened the gate: {masked}"

    # …and the `and` chain stays accepted: every operand must hold, so a non-gate conjunct
    # can only make the branch LESS likely, never take it against the gate.
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    if ev and _derived_mirror_required("object"):
        embedding = _encode(ev)
    return _write(ev, embedding)
""")
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is True, "an `and` chain was refused"

    # ⛔ AND A STORE INSIDE A NESTED SCOPE IS NOT THIS FUNCTION'S STORE — `ast.walk`
    # descends into it, so a helper that never runs used to "prove" the write-stop.
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        def helper():
            embedding = _encode(ev)
        _ = helper
    return _write(ev, _encode(ev))
""")
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is False, "a nested-scope store opened the gate"

    # ⛔ AND THE REMOVAL MUST NAME *THIS* PROPERTY. This repo pops the identity triple
    # (`embedding_model`/`embedding_text_hash`) while KEEPING the vector, so a substring
    # match would license the strip on a writer that still stores.
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(props):
    if not _derived_mirror_required("object"):
        props.pop("embedding_model", None)
    return _write({"SET n += $props": props})
""")
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is False, "an `embedding_model` pop opened the gate"

    # …and a `while True:` body is REACHABLE — only a falsy constant test is dead.
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    while True:
        if _derived_mirror_required("object"):
            embedding = _encode(ev)
        break
    return _write(ev, embedding)
""")
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is True, "`while True:` was pruned as dead"

    # ⛔ POLARITY: `mirror_required` reads "must the GRAPH still store this?", so a `not`
    # around it stores on exactly the runs where the gate said STOP — i.e. every run a
    # strip can happen on. It is refused for the STORE arm, and only there: for an early
    # exit, `if not gate(...): return` is the shape we want.
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        embedding = _encode(ev)
    return _write(ev, embedding)
""")
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is False, "a POLARITY-INVERTED guard opened the gate"

    # ⛔ AND THE STORE MUST PLACE SOMETHING. `embedding = None` is the placeholder the real
    # write-stop uses as its pre-guard reset; hoisted inside the guard it proves nothing,
    # and `if gate(...): embedding = None` / `else: embedding = _encode(...)` stores on the
    # branch the gate said stop.
    for source in ("""
def _upsert_object(ev):
    embedding = _encode(ev)
    if _derived_mirror_required("object"):
        embedding = None
    return _write(ev, embedding)
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        embedding = None
    else:
        embedding = _encode(ev)
    return _write(ev, embedding)
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object") or self.force_vectors:
        embedding = _encode(ev)
    return _write(ev, embedding)
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        touched = True
    return _write(ev, embedding)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a VACUOUS store opened the gate: {source!r}"

    # ⛔ A `return <call>` IS NEITHER AN EXIT NOR A STORE. Counting any call as a write
    # accepted `if gate(l): return len(vec)` followed by an unconditional store — the vector
    # was still written on the stop arm, and the gate certified "write-stopped".
    for source in ("""
def _upsert_object(props, vec):
    if _derived_mirror_required("object"):
        return len(vec)
    props["embedding"] = vec
    return _write(props)
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        return logger.info(ev)
    return _write(ev)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a `return <call>` opened the gate: {source!r}"

    # …while the forms that really do place the vector are accepted: the mapping spellings
    # of a dict store, and a stop inside a `match` case (an `ast.match_case` is not an
    # `ast.stmt`, so the guard walk used to drop every case body).
    for source in ("""
def _upsert_object(props):
    if _derived_mirror_required("object"):
        props.update({"embedding": _encode(props)})
    return _write(props)
""", """
def _upsert_object(props):
    if _derived_mirror_required("object"):
        props |= {"embedding": _encode(props)}
    return _write(props)
""", """
def _upsert_object(props, kind):
    match kind:
        case "object":
            if not _derived_mirror_required("object"):
                props.pop("embedding", None)
    return _write(props)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        assert strip._writer_consults_gate(
            module, "_upsert_object", ("object",))[0] is True, f"a real store was refused: {source!r}"

    # …and `if not True:` is DEAD like `if False:`, so a guard inside it is not a stop.
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    if not True:
        if mirror_required("object"):
            embedding = _encode(ev)
    return _write(ev)
""")
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is False, "`if not True:` was treated as live"

    # ⛔ THE PROPERTY NAME MUST BE EXACT ON THE STORE SIDE TOO. A substring match accepted
    # `props["embedding_model"] = current_model()` — the identity triple this repo stores
    # while KEEPING the vector — so a writer that set its identity inside the guard cleared
    # the gate while still storing `props["embedding"]` elsewhere. And a clause BUILDER
    # (`set_embedding_clause`) writes nothing: it assembles a string.
    for source in ("""
def _upsert_object(props):
    if _derived_mirror_required("object"):
        props["embedding_model"] = current_model()
    props["embedding"] = _encode(props)
    return _write(props)
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        embedding_text = ev.text
    embedding = _encode(ev)
    return _write(ev, embedding)
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        clause = set_embedding_clause("ev")
    return _write(ev, clause)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"an identity/clause-builder store opened the gate: {source!r}"

    # …and the decided removals that DO name the property: `del props["embedding"]`, and an
    # explicit inversion spelled `is False` (a Compare, which the whitelist would otherwise
    # refuse) — this repo writes `is False` in its own write paths.
    for source in ("""
def _upsert_object(props):
    if _derived_mirror_required("object") is False:
        props.pop("embedding", None)
    return _write({"SET n += $props": props})
""", """
def _upsert_object(props):
    if not _derived_mirror_required("object"):
        del props["embedding"]
    return _write({"SET n += $props": props})
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        assert strip._writer_consults_gate(
            module, "_upsert_object", ("object",))[0] is True, f"a decided removal was refused: {source!r}"

    # ⛔ A BARE EXIT UNDER A POSITIVE ASK IS NOT A STOP. `if gate(l): return None` skips the
    # write when the graph NEEDS the vector, and an earlier revision accepted it — which is
    # a fail-open when the body writes on the other arm. The stop is the NEGATED form.
    for source in ("""
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        return None
    props["embedding"] = _encode(ev)
    return _write(ev)
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        return None
    return _write(ev)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a bare exit under a POSITIVE ask opened the gate: {source!r}"

    # ⛔ A FIVE-LABEL WRITER STORES THROUGH A DICT (`SET n += $props`), so its write-stop
    # REMOVES THE KEY rather than skipping an encode — `props.pop("embedding")`. Counting
    # only assignments would refuse the one form that writer can take, i.e. leave the gate
    # permanently shut on `sdk::_update_entity`. A dict-subscript store counts too; a `pop`
    # of some OTHER key does not.
    for source, wanted in ((WRITER_POPS_THE_KEY, True),
                           (WRITER_SUBSCRIPT_STORE, True),
                           (WRITER_POPS_ANOTHER_KEY, False),
                           (WRITER_POPS_ON_THE_MIRROR_ARM, False)):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        assert strip._writer_consults_gate(
            module, "_upsert_object", ("object",))[0] is wanted, source

    # ⛔ `clear_embedding` is what the STRIP does, not evidence that a writer stopped
    # storing; and a store arm that accepted ANY assignment would be satisfied by
    # `touched = something()`, which places no vector. Both refused.
    for source in ("""
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        clear_embedding(graph, "n", "n", {}, vec)
    return _write(ev, embedding)
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        touched = _encode(ev)
    return _write(ev, embedding)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a non-store opened the gate: {source!r}"

    # …and the accepted non-early-exit forms that really do store, so the narrowing above
    # is not just "refuse everything": the encode guard, a Cypher clause, and a
    # self-naming vector writer.
    for source in (WRITER_ENCODE_GUARD, """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        clause = "SET n.embedding = $vec"
    return _write(ev, clause)
""", """
def _upsert_object(props):
    if _derived_mirror_required("object"):
        props.update(embedding=_encode(props))
    return _write(props)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        assert strip._writer_consults_gate(
            module, "_upsert_object", ("object",))[0] is True, f"a real store was refused: {source!r}"

    # …and a guard in dead code, or in a nested scope, is not THIS function's guard.
    for source in ("""
def _upsert_object(ev):
    if False:
        if mirror_required("object"):
            embedding = _encode(ev)
    return _write(ev)
""", """
def _upsert_object(ev):
    def inner():
        if mirror_required("object"):
            embedding = _encode(ev)
    return _write(ev)
""", """
def _upsert_object(ev):
    while False:
        if mirror_required("object"):
            embedding = _encode(ev)
    return _write(ev)
""", """
def _upsert_object(ev):
    class C:
        if mirror_required("object"):
            embedding = _encode(ev)
    return _write(ev)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a guard in dead/nested code opened the gate: {source!r}"

    # ⚠️ THE `else` OF A CONSTANT-TESTED `if` IS REACHABLE, so pruning the dead arm must not
    # drop the live sibling — a pruner that pushes only `body` for a constant test loses it.
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    if False:
        pass
    else:
        if mirror_required("object"):
            embedding = _encode(ev)
    return _write(ev, embedding)
""")
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is True, "the reachable `else` was pruned"

    module = _fake_writer_module(tmp_path, monkeypatch, WRITER_GATED)
    assert strip._writer_consults_gate(module, "_upsert_object", ("object",))[0] is True
    # The positive direction must survive reformatting that a substring check would miss.
    module = _fake_writer_module(
        tmp_path, monkeypatch, """
def _upsert_object(ev):
    gate = mirror_required
    if not gate("object"):
        return _write(ev)
""")
    # ⛔ A LOCAL ALIAS IS *NOT* ACCEPTED, and that is deliberate: `gate = mirror_required`
    # then `gate(...)` cannot be resolved without data flow, and accepting any call would
    # accept `f(...)`. Failing closed costs a refactor; failing open costs the data.
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is False


def test_a_cypher_clause_is_a_store_only_where_a_statement_runner_gets_it(
        tmp_path, monkeypatch):
    """⛔ `.embedding =` IN A STRING IS TEXT UNTIL SOMETHING RUNS IT.

    The clause spelling has to stay accepted — a writer may assemble its statement — but
    accepting the ASSIGNMENT alone admitted round 6's "a mention is not a store" through
    `ast.Constant`, one layer below the mention fix: measured at `6ee74268`, a guard whose
    only store was `_clause = "SET n.embedding = $e"` (nothing runs it) certified a writer
    that stored the vector unconditionally below, i.e. dead text lifted
    `REFUSE_WRITE_STILL_EMITS`.

    So the clause must REACH A STATEMENT RUNNER: passed straight to one
    (`session.run("… SET n.embedding = $e")`) or bound to a name that one is given. A
    LOGGER is not a runner — `_log(clause)` is the mention case again — which is why the
    runner test is an include list of callee verbs rather than a list of loggers to skip.
    """
    # REFUSED: the clause is assigned and never run, or handed to a logger only.
    for source in ("""
def _upsert_object(props):
    if _derived_mirror_required("object"):
        _clause = "SET n.embedding = $e"
    props["embedding"] = _encode(props["name"])
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        clause = "SET n.embedding = $e"
        _log(clause)
    props["embedding"] = _encode(ev)
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"clause TEXT nothing runs opened the gate: {source!r}"

    # ACCEPTED: both real spellings — built then run, and passed straight to the runner.
    for source in ("""
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        clause = "SET n.embedding = $vec"
    return _write(ev, clause)
""", """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        _run("MATCH (n:Object {id: $i}) SET n.embedding = $vec", ev)
    return None
"""):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is True, f"a clause that DOES run was refused: {source!r} ({why})"


def test_the_gate_check_is_fail_closed_on_an_unreadable_or_renamed_writer(
        tmp_path, monkeypatch):
    """An unparseable module, a missing function and a redefinition all refuse."""
    module = _fake_writer_module(tmp_path, monkeypatch, "def (:\n", name="broken_13629")
    gated, why = strip._writer_consults_gate(module, "_upsert_object")
    assert gated is False
    assert "could not be parsed" in why and "STILL EMITTING" in why

    module = _fake_writer_module(tmp_path, monkeypatch, "def something_else():\n    return 1\n")
    gated, why = strip._writer_consults_gate(module, "_upsert_object")
    assert gated is False
    assert "ABSENT" in why

    # ⛔ A SHADOWED DEFINITION IS A WRITER TOO. `ast` sees BOTH, and `setdefault` kept only
    # the FIRST — so a GATED first definition hid an UNGATED second one that still stores,
    # which is the fail-open direction. Mutation: keep only the first body and this test
    # reds on `gated is True`.
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    if not mirror_required("object"):
        return None
    return _write(ev)


def _upsert_object(ev):
    return _write(ev)
""")
    gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
    assert gated is False
    # The UNGATED definition is named by its own line, not hidden behind the gated one.
    assert "line(s) 8" in why

    # ⛔ …AND WHEN *BOTH* DEFINITIONS ARE UNGATED, BOTH LINES ARE NAMED. The assertion has to
    # track the SET of definitions walked: `"line(s) 2"` is a substring of both the shipped
    # `line(s) 2, 6` and a `setdefault` mutant's `line(s) 2`, so a prefix assertion proves
    # nothing (round 8's F2).
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    return _write(ev)


def _upsert_object(ev):
    return _write(ev)
""")
    gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
    assert gated is False
    assert "line(s) 2, 6" in why, why


def test_the_writer_population_comes_from_the_REGISTER_not_only_the_fold_map(
        monkeypatch):
    """⛔ THE COMPLETENESS WITNESS. `FOLD_WRITERS` covers `entities.py` and nothing else.

    `tortoise.sdk::_update_entity` keeps a caller-supplied `embedding` through
    `SET n += $props` on Subject/Object/Source/Event, `hosted_backup::restore_graph`
    writes all five labels, and five `sdk`/`session_indexer` writers store `:Event`. Each
    re-adds a vector on the next call exactly as a fold does — so a gate that cleared the
    folds alone would license the strip its own refusal claims to prevent.

    Mutation: drop `_register_writers` from `_writers_for` and this test reds.
    """
    writers, refusal = strip._writers_for("Event")
    assert refusal is None
    assert "tortoise.sdk::_update_entity" in writers
    assert "tortoise.hosted_backup::restore_graph" in writers
    assert "tortoise.projection.entities::_upsert_event" in writers

    gated, why = strip._fold_consults_gate("Event")
    assert gated is False
    assert "do not ask the gate" in why


def test_the_record_still_requiring_the_copy_keeps_the_gate_SHUT(monkeypatch, tmp_path):
    """⛔ THE OTHER HALF OF THE POLARITY, and it was the one with no falsifier at all.

    `mirror_required(entity_type) is True` means the record has NOT proved it holds this
    label's vectors, so the graph copy is still the answering leg and the fold keeps writing
    it. Every other test in this file drives that predicate to `False` (or makes it
    missing/raising), so rewriting `if needed: return True` to `if False:` left the suite
    **green — measured, 88 passed** — and the strip would then be licensed (ready) while the
    record predicate said the copy is still required: a retirement silently undone by the
    next gated write and reported as complete.

    The writer population here ASKS the gate (the shape `_fold_consults_gate` accepts), so a
    refusal cannot come from the un-gated-writer arm: the predicate's `True` is the only
    remaining reason, which is exactly what is being pinned.
    """
    from tortoise import record_vector

    monkeypatch.setattr(record_vector, "mirror_required",
                        lambda entity_type: True, raising=False)
    gated = _fake_writer_module(tmp_path, monkeypatch, """
def _only_writer(ev):
    if not _derived_mirror_required("object"):
        return None
    return _write(ev)
""")
    monkeypatch.setattr(strip, "_writers_for", lambda label: (
        (f"{gated}::_only_writer",), None))

    emits, reason = strip.write_gate("object")
    assert emits is True, (
        "a writer population that asks the gate is NECESSARY and not sufficient: "
        f"the record predicate still says the copy is required, and the gate opened ({reason})")
    assert "answers True" in reason
    assert "record has not proved" in reason


def test_a_writer_whose_labels_are_only_known_at_runtime_stays_in_the_population(monkeypatch):
    """`LABELS_AT_RUNTIME` is precisely a writer the gate cannot prove stopped.

    `_register_writers` therefore keeps it in the population unconditionally — and that arm
    had no falsifier: dropping `or record.stores == register.LABELS_AT_RUNTIME` left the suite
    **green (measured, 88 passed)**, because no live declaration carries that disposition any
    more (`#11641` bounded the last two). A future declaration that reverts to the
    runtime-labels shape would then leave the population SILENTLY, and `_fold_consults_gate`
    could clear a label whose writer still stores the vector — the exact fail-open the
    comment beside the arm says must never happen.
    """
    from tortoise import embed_writer_register as register

    monkeypatch.setattr(register, "EMBED_WRITER_CLASSES", {
        "tortoise.sdk::_runtime_only": register.WriterClass(
            cls="runtime", reason="a synthetic declaration for this pin",
            owner_issue="#13647", stores=register.LABELS_AT_RUNTIME),
        "tortoise.sdk::_tuple_declared": register.WriterClass(
            cls="tuple", reason="a synthetic declaration for this pin",
            owner_issue="#13647", stores=("Object",)),
    })

    writers = strip._register_writers("Object")
    assert "tortoise.sdk::_runtime_only" in writers, (
        "a runtime-labels writer left the population, so the gate could clear a label whose "
        "writer still stores the vector")
    assert "tortoise.sdk::_tuple_declared" in writers


def test_the_gate_OPENS_when_every_declared_writer_asks(monkeypatch, tmp_path):
    """⛔ THE ANTI-INERT-ENFORCER TEST, and it needed a real verdict to be possible.

    The gate refuses every label today, and once the writer population became the UNION of
    the fold map and `embed_writer_register`, a reviewer reasonably asked whether its firing
    condition can hold at ALL — an enforcer that can never open is a defect, not a guard.
    It can: one label, a population whose every writer asks the gate, and the coverage
    predicate answering False. This drives `write_gate` itself (not a stub: every execute
    test replaces it), so the False verdict is observed rather than assumed.
    """
    from tortoise import record_vector

    monkeypatch.setattr(record_vector, "mirror_required",
                        lambda entity_type: False, raising=False)
    gated = _fake_writer_module(tmp_path, monkeypatch, """
def _only_writer(ev):
    if not _derived_mirror_required("object"):
        return None
    return _write(ev)
""")
    monkeypatch.setattr(strip, "_writers_for", lambda label: (
        (f"{gated}::_only_writer",), None))

    emits, reason = strip.write_gate("object")
    assert emits is False, reason
    assert "calls the gate" in reason
    # …and a single un-gated writer closes it again, so the positive arm is not a bypass.
    monkeypatch.setattr(strip, "_writers_for", lambda label: (
        (f"{gated}::_only_writer", f"{gated}::_missing"), None))
    emits, reason = strip.write_gate("object")
    assert emits is True
    assert "_missing" in reason


def test_the_preview_refuses_an_unparseable_target_without_echoing_it(tmp_path, capsys):
    """⛔ THE REFUSAL MUST NOT RELAY THE RESOLVER'S MESSAGE, because that message QUOTES the
    offending token.

    `resolve_db_endpoint` casts the authority's port through `urllib`, and for a password
    containing `/` the first `/` leaves the password's HEAD as the authority — so the
    `ValueError` text is `Port could not be cast to integer value as 'pa'` (measured),
    i.e. a fragment of the credential, on stderr and into CI logs. The fingerprint above
    refuses this same shape one layer up; a refusal that prints the token hands the
    secret back. The type is reported, the text is withheld.
    """
    code = strip.main([
        "--uri", "docker://:pa/ss@falkordb.internal:6379/tortoise",
        "--graph-name", "tortoise", "--org-id", "o", "--graph-id", "g",
        "--record-dsn", "postgresql://x/y"])
    err = capsys.readouterr().err
    assert code == 3, err
    assert "REFUSED" in err
    assert "Port could not be cast" not in err, err
    assert "pa/ss" not in err and "'pa'" not in err, err
    assert "WITHHELD" in err, err


def test_the_execute_path_refuses_an_unreadable_gate_instead_of_tracebacking(
        tmp_path, monkeypatch, capsys):
    """⛔ THE EXECUTE ARM CAUGHT ONLY `CensusError`, so any other raise escaped as a
    traceback and was read as exit 1 ("usage") — over a destructive path.

    The tool's contract is "a census that cannot be run is a refusal, not a pass", and the
    PREVIEW arm already caught everything; the arm that REMOVES nodes did not. Measured
    pre-fix: a gate read raising `ImportError` inside `parity.census` propagated out of
    `strip.main` instead of refusing, and nothing about the invocation was wrong.
    """
    plan = {
        "tool": strip.PLAN_TOOL, "version": strip.PLAN_VERSION,
        "graph": "g", "org_id": "o", "graph_id": "g",
        "graph_uri": strip.uri_fingerprint("docker://:pw@h:6379/g"),
        "batch": 7,
        "labels": [{"label": "Object", "keys": ["obj-1"], "graph_vectors": 1,
                    "record_vectors": 1, "missing": 0, "graph_only": 0,
                    "unkeyed_nodes_blocking": 0, "strip_ready": True,
                    "blocked_reason": None}],
        "total_nodes": 1,
    }
    plan["digest"] = strip.digest(strip.plan_entries(plan))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    import tortoise.projection as projection

    class _Proj:
        g = "GRAPH"

    monkeypatch.setattr(projection, "FalkorProjection",
                        type("P", (), {"from_uri": staticmethod(lambda *a, **k: _Proj()),
                                       "__init__": lambda self, *a, **k: None}))
    monkeypatch.setattr(strip.census_mod, "resolve_graph_name", lambda *a, **k: "g")
    monkeypatch.setattr(strip.census_mod, "_refuse_unless_graph_present",
                        lambda *a, **k: None)
    monkeypatch.setattr(strip.census_mod, "empty_graph_target_flag", lambda *a, **k: None)

    def _boom(**kwargs):
        raise ImportError("import of tortoise.security halted")

    monkeypatch.setattr(strip, "execute", _boom)

    code = strip.main([
        "--uri", "docker://:pw@h:6379/g", "--graph-name", "g",
        "--org-id", "o", "--graph-id", "g", "--record-dsn", "postgresql://x/y",
        "--execute", "--plan", str(plan_path),
        "--confirm-plan-digest", plan["digest"]])
    err = capsys.readouterr().err
    assert code == 3, err
    assert "REFUSED" in err
    assert "ImportError" in err
    # ⛔ THE MESSAGE IS RELAYED HERE — this arm wraps no target resolution, and the URI it
    # was handed is already a credential-free fingerprint, so withholding the text would
    # only make a real bug undiagnosable (measured: a `TypeError` on this arm would be
    # indistinguishable from a gate failure if the message were dropped).
    assert "tortoise.security" in err, "the cause must be diagnosable on this arm"


def test_a_plan_that_lists_a_label_twice_is_refused(tmp_path):
    """A digest is a SET, so a duplicated label row verifies — and would strip twice."""
    plan = {
        "tool": strip.PLAN_TOOL, "version": strip.PLAN_VERSION,
        "labels": [{"label": "Object", "keys": ["obj-1"]},
                   {"label": "Object", "keys": ["obj-2"]}],
        "total_nodes": 2,
    }
    plan["digest"] = strip.digest(strip.plan_entries(plan))
    path = tmp_path / "duplicated.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(strip.Refusal) as excinfo:
        strip._load_plan(str(path), plan["digest"])
    assert excinfo.value.token == strip.REFUSE_PLAN_UNREADABLE
    assert "more than once" in str(excinfo.value)


def test_main_honours_the_plans_recorded_batch_and_the_flag_overrides_it(
        tmp_path, monkeypatch):
    """A recorded value must not be silently ignored at the moment it is used.

    ⛔ THIS DRIVES `main`, which is where the resolution lives. The earlier version of this
    test asserted `int(plan["batch"]) == 7` — a Python expression about a dict it had just
    built, which stayed green when the fix was reverted, because `main` is the only caller
    that resolves the value and every other `execute` test passes `batch=` explicitly.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://x")
    plan = {
        "tool": strip.PLAN_TOOL, "version": strip.PLAN_VERSION,
        "graph": "g", "graph_uri": "docker://h:6379/g", "org_id": "o", "graph_id": "g",
        "batch": 7,
        "labels": [{"label": "Object", "keys": ["obj-1"], "nodes": 1,
                    "unkeyed_nodes_blocking": 0, "strip_ready": True,
                    "blocked_reason": None}],
        "total_nodes": 1,
    }
    plan["digest"] = strip.digest(strip.plan_entries(plan))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    import tortoise.projection as projection

    seen: dict = {}

    class _Proj:
        g = "GRAPH"

    monkeypatch.setattr(projection, "FalkorProjection",
                        type("P", (), {"from_uri": staticmethod(lambda *a, **k: _Proj()),
                                       "__init__": lambda self, *a, **k: None}))
    monkeypatch.setattr(strip.census_mod, "resolve_graph_name", lambda *a, **k: "g")
    monkeypatch.setattr(strip.census_mod, "_refuse_unless_graph_present",
                        lambda *a, **k: None)
    monkeypatch.setattr(strip.census_mod, "empty_graph_target_flag", lambda *a, **k: None)
    monkeypatch.setattr(strip, "execute",
                        lambda **kwargs: seen.update(kwargs) or {"removed": 0})
    monkeypatch.setattr(strip, "result_exit_code", lambda result: 0)

    base = ["--uri", "docker://:pw@h:6379/g", "--graph-name", "g",
            "--org-id", "o", "--graph-id", "g",
            "--execute", "--plan", str(plan_path),
            "--confirm-plan-digest", plan["digest"]]

    assert strip.main(base) == 0
    assert seen["batch"] == 7, seen["batch"]          # the plan's recorded value

    assert strip.main([*base, "--batch", "2"]) == 0
    assert seen["batch"] == 2                          # an explicit flag wins


def test_an_unreadable_or_empty_writer_population_is_not_evidence(monkeypatch):
    """A register that cannot be read, or declares nothing, refuses — `all([])` is True."""
    monkeypatch.setattr(strip, "_register_writers",
                        lambda label: (_ for _ in ()).throw(RuntimeError("boom")))
    gated, why = strip._fold_consults_gate("Object")
    assert gated is False
    assert "could not be read" in why

    monkeypatch.setattr(strip, "_register_writers", lambda label: ())
    monkeypatch.setattr(strip, "FOLD_WRITERS", {})
    gated, why = strip._fold_consults_gate("Object")
    assert gated is False
    assert "empty population is not" in why


def test_a_label_no_entity_type_addresses_is_not_write_stopped(monkeypatch):
    """`all([])` is True — so the vacuous label is refused, not declared stopped."""
    monkeypatch.setattr(strip.parity, "affected_entity_types", lambda label: [])
    still, why = strip.label_write_gate("Object")
    assert still is True
    assert "zero evidence" in why


def test_label_write_gate_requires_every_entity_type_addressing_the_label(monkeypatch):
    """`:Source` serves `source` AND `document`; one stopped writer is not enough."""
    monkeypatch.setattr(
        strip, "write_gate",
        lambda entity_type: (entity_type == "document", f"{entity_type} emits"))
    emits, reason = strip.label_write_gate("Source")
    assert emits is True
    assert "document" in reason
    # …and when BOTH stop, the label stops.
    monkeypatch.setattr(strip, "write_gate", lambda entity_type: (False, "stopped"))
    assert strip.label_write_gate("Source")[0] is False


# ── preview is the default and writes nothing ───────────────────────────────


def test_preview_removes_nothing_and_records_no_write(stores, monkeypatch):
    """`build_plan` reads both stores and issues ZERO writes."""
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph)
    assert graph.writes == []
    assert plan["total_nodes"] == 1
    assert plan["digest"].startswith("sha256:")
    assert plan["ready"] is True


def test_preview_names_the_write_gate_as_the_block_where_it_is_closed_now(stores, monkeypatch):
    """On a tree without the write-stop, the plan's refusal is the WRITE GATE.

    This is the LIVE state `#13629` documents in its own PR body: the record's half has
    landed, the graph's write half has not, so a plan is producible and an execution is
    refused. `monkeypatch` is used only to make the message deterministic.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    monkeypatch.setattr(strip, "WRITE_GATE_SYMBOL", "_absent_symbol_13629")
    plan = _plan(graph)
    assert plan["ready"] is False
    assert plan["labels"][0]["strip_ready"] is False
    assert "_absent_symbol_13629" in plan["labels"][0]["blocked_reason"]


# ── execution: every precondition, each its own token ───────────────────────


def test_execute_refuses_an_unreadable_plan_file(tmp_path):
    """A plan that cannot be read is a refusal, never "then use the live census"."""
    with pytest.raises(strip.Refusal) as excinfo:
        strip._load_plan(str(tmp_path / "absent.json"), "sha256:a")
    assert excinfo.value.token == strip.REFUSE_PLAN_UNREADABLE


def test_the_two_token_refusals_are_raised_by_main_before_any_write(monkeypatch, capsys):
    """`--execute` with no `--plan`, and `--plan` with no digest, both REFUSE."""
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://x")
    for argv, token in (
        (["--db-path", "/tmp/x", "--graph-name", "g", "--org-id", "org",
          "--graph-id", "graph", "--execute"], strip.REFUSE_NO_PLAN),
        (["--db-path", "/tmp/x", "--graph-name", "g", "--org-id", "org",
          "--graph-id", "graph", "--execute", "--plan", "/tmp/plan.json"],
         strip.REFUSE_NO_DIGEST),
    ):
        assert strip.main(argv) == 2
        assert token in capsys.readouterr().err


def test_build_plan_refuses_an_ambiguous_target_before_it_prints_anything(stores):
    """⛔ THE WIRING FALSIFIER for `REFUSE_TARGET_UNPARSEABLE`: `build_plan` fingerprints
    the target it is about to put in the plan, so the refusal has to arrive as a
    `Refusal` carrying its token — not as a traceback out of `uri_fingerprint`, and not
    as a plan whose `graph_uri` is a truncated fragment of the real target.

    Both halves are asserted: the plan is NOT produced, and the credential appears
    nowhere in the message.
    """
    graph, _record = stores
    graph.add("Object", "obj-1")
    with pytest.raises(strip.Refusal) as excinfo:
        strip.build_plan(
            g=graph, record_dsn="postgresql://x", org_id="org", graph_id="graph",
            graph_name="g", graph_uri="docker://:pa/ss@prod:6379/g",
            labels=None, batch=strip.DEFAULT_BATCH)
    assert excinfo.value.token == strip.REFUSE_TARGET_UNPARSEABLE
    assert "pa/ss" not in str(excinfo.value)


def test_main_turns_a_build_plan_refusal_into_exit_2_with_its_token(monkeypatch, capsys):
    """⛔ THE WIRING FALSIFIER for the arm, not just for the raise.

    `build_plan` raising a `Refusal` used to escape `main` as a traceback and be read as
    exit 1 ("usage") when nothing about the invocation was wrong. Deleting the arm must
    redden THIS test — which is what the direct-call test above cannot do, because it
    never enters `main`. Measured: with the arm deleted the suite stayed green, so the
    arm was correct but unprotected.
    """
    import tortoise.projection as projection

    class _Proj:
        g = "GRAPH"

    monkeypatch.setattr(projection, "FalkorProjection",
                        type("P", (), {"from_uri": staticmethod(lambda *a, **k: _Proj()),
                                       "__init__": lambda self, *a, **k: None}))
    monkeypatch.setattr(strip.census_mod, "resolve_graph_name", lambda *a, **k: "g")
    monkeypatch.setattr(strip.census_mod, "_refuse_unless_graph_present",
                        lambda *a, **k: None)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://x")

    def _refuse(**kwargs):
        raise strip.Refusal(strip.REFUSE_TARGET_UNPARSEABLE,
                            "the target cannot be fingerprinted (test double)")

    monkeypatch.setattr(strip, "build_plan", _refuse)

    argv = ["--uri", "docker://:pa/ss@prod:6379/g", "--graph-name", "g",
            "--org-id", "o", "--graph-id", "g"]
    assert strip.main(argv) == 2
    err = capsys.readouterr().err
    assert strip.REFUSE_TARGET_UNPARSEABLE in err
    # The token, not a usage traceback, and nothing was written.
    assert "Traceback" not in err


def test_the_unresolvable_target_error_does_not_print_the_credential(monkeypatch, capsys):
    """⛔ `urlparse` names the userinfo tail in its own ValueError, so interpolating the
    exception would print a fragment of the graph password on an error line (#720's
    premise). Pinned by driving the real `main` with the shape that produces it.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://x")

    def _raise(*a, **k):
        raise ValueError(
            "Port could not be cast to integer value as 's3cret'")

    monkeypatch.setattr(strip.census_mod, "resolve_graph_name", _raise)

    argv = ["--uri", "docker://:s3cret#x@host:6379/tortoise",
            "--org-id", "o", "--graph-id", "g"]
    assert strip.main(argv) == 3
    err = capsys.readouterr().err
    assert "s3cret" not in err, err
    assert "ValueError" in err                      # the type name is safe and kept


def test_uri_fingerprint_drops_the_credential_and_keeps_the_target():
    """The plan approves a TARGET, and it must not circulate the password to name one.

    ⛔ Both halves are load-bearing. Dropping the credential is why the plan (which a
    human pastes and approves) is safe to share; KEEPING host/port/name is why a clone
    of the same graph on another box still reads as a different target.
    """
    assert strip.uri_fingerprint(
        "docker://:s3cret@falkordb.internal:6379/tortoise") == \
        "docker://falkordb.internal:6379/tortoise"
    assert strip.uri_fingerprint("docker://:s3cret@host:6379/tortoise") != \
        strip.uri_fingerprint("docker://:s3cret@other-host:6379/tortoise")
    assert "s3cret" not in strip.uri_fingerprint("docker://:s3cret@host:6379/g")
    # ⛔ A PASSWORD CONTAINING `/` IS THE CASE THAT LEAKED. Partitioning on the first
    # `/` before the userinfo→host boundary cut the password in half, left no `@` to
    # strip, and returned the credential VERBATIM — and `tortoise/__main__.py` builds
    # `docker://:{password}@host:port/tortoise` from the RAW password, so this shape is
    # reachable from an ordinary `FALKORDB_PASSWORD`.
    #
    # ⛔ AND IT IS REFUSED, not silently cut at the last `@`. Cutting there hides the
    # credential but DROPS the host, and a fingerprint is a COMPARISON KEY: the two
    # targets below would fold onto one value, which is exactly what stops
    # `REFUSE_TARGET_DRIFT` seeing a production→staging swap. Both properties matter, and
    # only a refusal holds both — so the falsifier pins BOTH halves: no leak AND no
    # false equality.
    with pytest.raises(strip.Refusal) as ambiguous:
        strip.uri_fingerprint("docker://:pa/ss@falkordb.internal:6379/tortoise")
    assert ambiguous.value.token == strip.REFUSE_TARGET_UNPARSEABLE
    assert "pa/ss" not in str(ambiguous.value)
    # The same ambiguity with an `@` in the NAME is the shape that used to fold two
    # different hosts onto `docker://b`: prod and staging must never compare equal.
    for target in ("docker://prod:6379/a@b", "docker://staging:6379/a@b"):
        with pytest.raises(strip.Refusal):
            strip.uri_fingerprint(target)
    # ⛔ A PATH-LESS target has no `/`, so the ambiguity guard above cannot fire and the
    # query used to survive INSIDE the host — returned verbatim into the plan, `--json`
    # and the drift message. Measured regression against the previous commit.
    assert strip.uri_fingerprint("docker://prod:6379?api_key=s3cret") == \
        "docker://prod:6379"
    assert strip.uri_fingerprint("docker://:pw@host:6379?api_key=s3cret") == \
        "docker://host:6379"
    assert strip.uri_fingerprint("docker://prod:6379#frag") == "docker://prod:6379"
    assert strip.uri_fingerprint("docker://prod:6379?api_key=s3cret") != \
        strip.uri_fingerprint("docker://staging:6379?api_key=s3cret")
    # ⛔ …and the REFUSAL must still name the remedy. This assertion exists because the
    # remedy sentence was unpinned: deleting it left the whole suite green.
    with pytest.raises(strip.Refusal) as refused:
        strip.uri_fingerprint("docker://:pa/ss@host:6379/g")
    assert "ercent-encode" in str(refused.value)
    # A query/fragment is not target identity and may carry a credential — it is dropped
    # rather than re-emitted in the path.
    assert strip.uri_fingerprint("docker://prod:6379/g?api_key=s3cret") == \
        "docker://prod:6379/g"
    # ⛔ A `?`/`#` INSIDE THE PASSWORD sits between the scheme and the `@`, so it belongs to
    # the userinfo and is removed WITH it. Cutting the query first — the tidier-looking
    # order — truncated the credential at that character and emitted the remainder as the
    # HOST (`docker://:s3cret`), re-creating the exact #2983 leak this function cites.
    #
    # ⛔ AND THESE REFUSE, because `urlsplit` terminates the authority at the `#`/`?`: the
    # netloc is `:s3cret`, whose port cast fails, so neither address is one this tool could
    # ever dial. A fingerprint naming `host:6379` there would name a deployment the resolver
    # never connects to — the same rule that refuses `docker://?x@prod:6379`.
    for undialable in ("docker://:s3cret#frag@host:6379/tortoise",
                       "docker://:s3cret?q@host:6379/tortoise",
                       # a `?` BEFORE the host leaves the netloc as `:rot`, whose port
                       # cast fails — the same undialable class, not a "genuine query".
                       "docker://:rot?ated@prod:6379/g",
                       "docker://:rot?ated@staging:6379/g"):
        with pytest.raises(strip.Refusal):
            strip.uri_fingerprint(undialable)
    # A `?` AFTER a real host is a genuine query: the authority is `host:6379`, the query is
    # dropped, and the userinfo is gone. (Asserted above for the `:pw@host:6379?…` shape.)
    assert strip.uri_fingerprint("docker://prod:6379?x@y/g") == "docker://prod:6379/g"
    # …and an `@` inside the QUERY is the ambiguous shape, refused rather than folded.
    with pytest.raises(strip.Refusal):
        strip.uri_fingerprint("docker://prod:6379/g?api_key=x@y")
    # …and the ordinary host-distinguishing property survives, so the refusal did not buy
    # safety by collapsing credentialed URIs to a constant.
    assert strip.uri_fingerprint("docker://:pa%2Fss@prod:6379/tortoise") == \
        "docker://prod:6379/tortoise"
    # ⛔ A `?`/`#` INSIDE THE PASSWORD IS USERINFO, NOT A QUERY. Cutting the query first
    # dropped the `@` before the ambiguity guard could see it, so the credential came back
    # AND the host went missing: measured on the previous revision,
    # `docker://:s3cret?xx@prod:6379/g` rendered `docker://:s3cret`, and prod/staging
    # folded onto `docker://:pa`. The authority window is therefore resolved BEFORE the
    # query is cut.
    #
    # ⛔ BUT WHEN THE MARKER TERMINATES AN AUTHORITY THAT IS NOT A HOST, THE REFUSAL WINS.
    # `urlsplit("docker://:s3cret?xx@prod:6379/g").netloc` is `":s3cret"` — the port cast
    # fails, so this run could never dial that address, and returning `prod:6379/g` would
    # name a deployment the tool never connects to. The same reason the empty-authority
    # shapes (`docker://?x@prod:6379`) refuse: a fingerprint may not name a target the
    # resolver would not dial.
    for unaddressable in (
        "docker://:s3cret?xx@prod:6379/g",
        "docker://:pa?ss@prod:6379/g",
        "docker://:pw#x@prod:6379/g",
        "docker://?x@prod:6379",
        "docker://#x@prod:6379",
        "docker://:6379?x@prod:6379",
    ):
        with pytest.raises(strip.Refusal) as unaddr:
            strip.uri_fingerprint(unaddressable)
        assert unaddr.value.token == strip.REFUSE_TARGET_UNPARSEABLE
        assert "s3cret" not in str(unaddr.value) and "xx" not in str(unaddr.value)
    # …and a query BEFORE a path (`host:6379?x@y/g`) resolves to its real host rather than
    # letting the query tail become one.
    # ⛔ BUT A QUERY TAIL THAT ITSELF LOOKS LIKE `host:port` IS NOT RESOLVED EITHER WAY —
    # `prod:6379?x@y:123/g` reads BOTH as "a genuine query after host prod" AND as "a
    # password containing `?` whose host is `y:123`", and nothing in the URI prefers one.
    # Taking the query reading can print the userinfo into the plan and fold two
    # deployments onto one fingerprint (`docker://admin:1234?x@prod:6379/g` did exactly
    # that: prod/staging both rendered `docker://admin:1234/g`); taking the userinfo
    # reading folds the other pair. So the class is REFUSED. Fail-closed and provable,
    # instead of picking a reading and hoping the input was the kind we guessed.
    for ambiguous in (
        "docker://prod:6379?x@y:123/g",
        "docker://staging:6379?x@y:123/g",
        "docker://admin:1234?x@prod:6379/g",
        "docker://admin:1234?x@staging:6379/g",
        "docker://svc:1?pass@prod:6379/g",
        "docker://user?pw@prod:6379/g",
        "docker://prod:6379?x@other:6379",
        # ⛔ THE `#` ARM NEEDS ITS OWN MEMBER. Every other member above uses `?`, so
        # deleting `#` from the marker set left the whole suite green while
        # `docker://prod:6379#x@other:6379` started rendering `docker://other:6379` — a
        # host the tool never dials (`urlsplit` puts the whole `#…` in the fragment).
        "docker://prod:6379#x@other:6379",
        "docker://admin:1234#x@prod:6379/g",
        "docker://prod:6379?ssl=true&addr=user@master:6379",
    ):
        with pytest.raises(strip.Refusal) as amb:
            strip.uri_fingerprint(ambiguous)
        assert amb.value.token == strip.REFUSE_TARGET_UNPARSEABLE
        # the refusal must not echo the credential material it could not classify
        assert "1234" not in str(amb.value) and "pass@" not in str(amb.value)
    # A bare-word tail is NOT a plausible second host, so it still resolves.
    assert strip.uri_fingerprint("docker://host:6379?x@y/g") == "docker://host:6379/g"
    # ⛔ AND THE QUERY IS DROPPED FROM THE AUTHORITY TOO, not only from the path: with no
    # path segment it used to be re-emitted WHOLE into the plan (`?api_key=SECRET…`) —
    # measured, and reachable end to end because the resolver accepts that URI.
    assert strip.uri_fingerprint("docker://:pw@prod:6379?api_key=SECRETVALUE") == \
        "docker://prod:6379"
    assert "SECRETVALUE" not in strip.uri_fingerprint(
        "docker://:pw@prod:6379?api_key=SECRETVALUE")
    # ⛔ WHEN THE FIRST `?` TERMINATES THE AUTHORITY TO NOTHING DIALABLE, THE REFUSAL WINS —
    # even if a later `?` looks like a real query. `urlsplit` puts this netloc at `:pa`,
    # whose port cast fails, so the tool could never connect to `prod:6379` and a
    # fingerprint naming it would name a deployment this run does not dial.
    for undialable in ("docker://:pa?ss@prod:6379?api_key=SECRET",
                       "docker://?x@prod:6379",
                       "docker://:6379?x@prod:6379"):
        with pytest.raises(strip.Refusal):
            strip.uri_fingerprint(undialable)
    assert strip.uri_fingerprint("docker://prod:6379#frag") == "docker://prod:6379"
    assert strip.uri_fingerprint("docker://h:6379?") == "docker://h:6379"
    # Case is not identity (RFC 3986 §6.2.2.1): a re-run in another case must not be
    # refused as drift.
    assert strip.uri_fingerprint("DOCKER://H:6379/g") == \
        strip.uri_fingerprint("docker://h:6379/g") == "docker://h:6379/g"
    # ⛔ A `--uri` WITHOUT `://` IS PASSED THROUGH AS A DB PATH — but not when it carries
    # userinfo: measured, `docker::pw@host:6379/g` came back VERBATIM, password included.
    with pytest.raises(strip.Refusal):
        strip.uri_fingerprint("docker::pw@host:6379/g")
    assert strip.uri_fingerprint("/tmp/a@b.db") == "/tmp/a@b.db"
    # ⛔ THE NO-SCHEME REFUSAL MUST NOT ECHO THE TOKEN IT DETECTED. Its first cut
    # interpolated everything before the `@` — i.e. the userinfo it had just fired on — so
    # the password came back VERBATIM in the reason (measured).
    with pytest.raises(strip.Refusal) as no_scheme:
        strip.uri_fingerprint("docker::hunter2TOPSECRET@host:6379/g")
    assert "hunter2TOPSECRET" not in str(no_scheme.value)
    # …and a COLON IN A LOCAL PATH is not userinfo: the heuristic applies only when the
    # text before the `:` is scheme-shaped (no `/`).
    assert strip.uri_fingerprint("/tmp/a:b@c.db") == "/tmp/a:b@c.db"
    # ⛔ #2987'S SHAPE: a CREDENTIAL WITH NO `@` (a copy-paste that dropped the `@host`
    # tail). The canonical masker answers `<uri-redacted-unrecognised-shape>` for these; a
    # COMPARISON KEY may not do less. Measured pre-fix: each came back VERBATIM as the
    # host, and an empty authority folded every such target onto `docker://`.
    for credential_shaped in ("docker://:hunter2", "docker://user:hunter2",
                              "docker://:hunter2:6379", "docker://", "docker://?x"):
        with pytest.raises(strip.Refusal) as shaped:
            strip.uri_fingerprint(credential_shaped)
        assert shaped.value.token == strip.REFUSE_TARGET_UNPARSEABLE
        assert "hunter2" not in str(shaped.value)
    # the recognised shapes still fingerprint, so the arm is not a blanket refusal
    assert strip.uri_fingerprint("docker://h:6379") == "docker://h:6379"
    assert strip.uri_fingerprint("docker://localhost") == "docker://localhost"
    assert strip.uri_fingerprint("docker://:pa%2Fss@prod:6379/tortoise") != \
        strip.uri_fingerprint("docker://:pa%2Fss@staging:6379/tortoise")
    # A bare db-path carries no credential and is passed through unchanged, so the
    # embedded lane's fingerprint is its path and the plan stays readable.
    assert strip.uri_fingerprint("/tmp/x.db") == "/tmp/x.db"


def test_main_gives_execute_the_FINGERPRINT_of_the_live_target(tmp_path, monkeypatch,
                                                             stores):
    """⛔ THE WIRING FALSIFIER: `execute` compares the plan's fingerprint against what
    `main` hands it, so a live RAW uri would drift on every credentialed deployment —
    and an OMITTED `graph_uri` would not refuse at all, it would raise `TypeError` out
    of the destructive path with no token and no "nothing was removed" message.

    Both arms are pinned here because the refusal tests above return *before* `execute`
    is reached, so the one call that writes was never executed by a test at all.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://x")
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = strip.build_plan(
        g=graph, record_dsn="postgresql://x", org_id="org", graph_id="graph",
        graph_name="g", graph_uri="docker://:s3cret@host:6379/g",
        labels=None, batch=strip.DEFAULT_BATCH)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    import tortoise.projection as projection

    seen: dict = {}

    class _Proj:
        g = "GRAPH"

    class _FakeProjection:
        def __init__(self, *a, **k):
            pass

        @staticmethod
        def from_uri(*a, **k):
            return _Proj()

    monkeypatch.setattr(projection, "FalkorProjection", _FakeProjection)
    monkeypatch.setattr(strip.census_mod, "resolve_graph_name",
                        lambda *a, **k: "g")
    monkeypatch.setattr(strip.census_mod, "_refuse_unless_graph_present",
                        lambda *a, **k: None)
    monkeypatch.setattr(strip.census_mod, "empty_graph_target_flag",
                        lambda *a, **k: None)
    monkeypatch.setattr(strip, "_load_plan", lambda *a, **k: plan)

    def _execute(**kwargs):
        seen.update(kwargs)
        return {"removed": 0}

    monkeypatch.setattr(strip, "execute", _execute)

    argv = ["--uri", "docker://:s3cret@host:6379/g", "--graph-name", "g",
            "--org-id", "org", "--graph-id", "graph",
            "--execute", "--plan", str(plan_path),
            "--confirm-plan-digest", plan["digest"]]
    assert strip.main(argv) == 0
    # The credential never reaches the comparison, and the target is still named.
    assert seen["graph_uri"] == "docker://host:6379/g"
    assert "s3cret" not in seen["graph_uri"]
    # …and the live value MATCHES the plan's, i.e. this run cannot drift on its own URI
    # even though the plan was built from a CREDENTIALED uri and this run passes one too.
    assert seen["graph_uri"] == plan["graph_uri"] == "docker://host:6379/g"


def test_execute_refuses_a_plan_edited_after_it_was_printed(tmp_path):
    """The plan's OWN digest is checked, not only the caller's claim."""
    #: The RECOMPUTATION arm: the `digest` FIELD can match while the key LISTS do not,
    #: which is the edit that would otherwise smuggle an unapproved (or empty) set past
    #: `--confirm-plan-digest`. Falsified by removing the `plan_entries` re-derivation.
    doctored = {
        "tool": strip.PLAN_TOOL, "version": strip.PLAN_VERSION,
        "labels": [{"label": "Object", "keys": ["obj-1"]}],
        "total_nodes": 1,
    }
    doctored["digest"] = strip.digest(strip.plan_entries(doctored))
    empty = json.loads(json.dumps(doctored))
    empty["labels"] = [{"label": "Object", "keys": []}]   # field left untouched
    path = tmp_path / "doctored.json"
    path.write_text(json.dumps(empty))
    with pytest.raises(strip.Refusal) as excinfo:
        strip._load_plan(str(path), doctored["digest"])
    assert excinfo.value.token == strip.REFUSE_DIGEST_MISMATCH
    # The clean plan loads, so the refusal above is about the edit and not about parsing.
    path.write_text(json.dumps(doctored))
    assert strip._load_plan(str(path), doctored["digest"])["total_nodes"] == 1
    plan = {"tool": strip.PLAN_TOOL, "version": strip.PLAN_VERSION, "digest": "sha256:a"}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(strip.Refusal) as excinfo:
        strip._load_plan(str(path), "sha256:b")
    assert excinfo.value.token == strip.REFUSE_DIGEST_MISMATCH


def test_execute_refuses_a_plan_from_another_tool(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"tool": "purge_legacy_turns", "version": 1}))
    with pytest.raises(strip.Refusal) as excinfo:
        strip._load_plan(str(path), "sha256:a")
    assert excinfo.value.token == strip.REFUSE_PLAN_UNREADABLE


def test_execute_refuses_a_stale_plan_naming_the_delta(stores, monkeypatch):
    """The exact-set precondition: one node more OR less refuses."""
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph)
    graph.add("Object", "obj-2")          # the population moves after approval
    record.holds("object", "obj-2")
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan)
    assert excinfo.value.token == strip.REFUSE_PLAN_STALE
    assert "obj-2" not in str(excinfo.value)   # counts, never raw keys, in the message
    assert "Object=2" in str(excinfo.value)
    assert graph.vector_keys("Object") == {"obj-1", "obj-2"}


def test_execute_refuses_a_graph_only_node_and_removes_nothing(stores, monkeypatch):
    """⛔ THE FALSIFIER THE ISSUE ASKS FOR, in its destructive direction.

    A node whose record row holds no `embedding_vec` has the graph as the ONLY copy of
    its fingerprint. Execution must refuse BEFORE the first write and must leave BOTH
    nodes untouched — refusing after a partial strip is the failure this gate exists to
    prevent, so the assertion is on the store, not on the message.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    graph.add("Object", "obj-2")
    record.holds("object", "obj-1")       # obj-2 has NO record vector
    plan = _plan(graph)
    assert plan["ready"] is False
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan)
    assert excinfo.value.token == strip.REFUSE_GRAPH_ONLY
    assert "backfill" in str(excinfo.value)
    assert graph.vector_keys("Object") == {"obj-1", "obj-2"}
    assert graph.writes == []


def test_execute_refuses_while_the_write_path_still_emits_vectors(stores, monkeypatch):
    """⛔ THE OTHER FALSIFIER: the write gate is not ceremony.

    `_upsert_object`/`_upsert_subject` write `embedding` on `ON MATCH` too, so a strip
    that runs while the fold still encodes is undone by the next mention — and an
    operator could not tell a completed retirement from a racing writer. So the gate is
    a REFUSAL, and the graph is untouched.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    monkeypatch.setattr(strip, "WRITE_GATE_SYMBOL", "_absent_symbol_13629")
    plan = _plan(graph)
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan)
    assert excinfo.value.token == strip.REFUSE_WRITE_STILL_EMITS
    assert "_absent_symbol_13629" in str(excinfo.value)
    assert graph.vector_keys("Object") == {"obj-1"}
    assert graph.writes == []


def test_execute_refuses_target_drift(stores, monkeypatch):
    """A plan is an authority over ONE graph, by name."""
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph)
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan, graph_name="other-graph")
    assert excinfo.value.token == strip.REFUSE_TARGET_DRIFT
    assert graph.vector_keys("Object") == {"obj-1"}


def test_execute_refuses_an_empty_plan_unless_expect_empty(stores, monkeypatch):
    """An empty plan executed to a vacuous "success" reports a retirement it never did."""
    _ready(monkeypatch)
    graph, _record = stores
    plan = _plan(graph)
    assert plan["total_nodes"] == 0
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan)
    assert excinfo.value.token == strip.REFUSE_NOTHING_TO_STRIP
    result = _execute(graph, plan, expect_empty=True)
    assert result["empty"] is True
    assert graph.writes == []


def test_expect_empty_does_not_acknowledge_a_NARROWED_scope(stores, monkeypatch):
    """⛔ `--expect-empty` answers "an empty population is intended".

    It does NOT answer the OTHER question a narrowed plan raises — "the labels outside this
    plan are deliberately untouched". Reading the first as the second published exit 0 on a
    `--label Object` sweep of an empty graph while `Event`/`Source`/`Subject` still held
    their vectors, because `execute` returned `{"empty": True}` BEFORE computing
    `unmeasured_by_label` and `result_exit_code` short circuited on `empty` in front of its
    own narrowed clause. `--expect-narrowed` is the flag that acknowledges the scope.
    """
    _ready(monkeypatch)
    graph, _record = stores
    narrowed = _plan(graph, labels=["Object"])
    assert narrowed["total_nodes"] == 0
    result = _execute(graph, narrowed, expect_empty=True)
    assert result["empty"] is True
    assert result["unmeasured_by_label"] == ["Event", "Source", "Subject"]
    assert strip.result_exit_code(result) == 5, (
        "an empty NARROWED plan published exit 0 over three unstripped labels")

    # …and an operator who says the partial sweep was intended still gets 0, on the record.
    acknowledged = _execute(graph, _plan(graph, labels=["Object"]),
                            expect_empty=True, expect_narrowed=True)
    assert acknowledged["expect_narrowed"] is True
    assert strip.result_exit_code(acknowledged) == 0

    # A plan over the WHOLE population is unaffected: nothing was left unmeasured.
    whole = _execute(graph, _plan(graph), expect_empty=True)
    assert whole["unmeasured_by_label"] == []
    assert strip.result_exit_code(whole) == 0


def test_execute_strips_the_planned_set_and_reports_the_count(stores, monkeypatch):
    """The batched removal: a complete set, a truthful count, and a clean residual.

    `--batch 1` forces five statements so a batch boundary cannot hide a shortfall, and
    the removal count is asserted against the store rather than against itself.
    """
    _ready(monkeypatch)
    graph, record = stores
    for index in range(5):
        graph.add("Object", f"obj-{index}")
        record.holds("object", f"obj-{index}")
    plan = _plan(graph, batch=1)
    result = _execute(graph, plan, batch=1)
    assert result["removed"] == 5
    assert result["expected"] == 5
    assert result["removed_by_label"]["Object"] == 5
    assert result["residual_by_label"]["Object"] == 0
    assert graph.vector_keys("Object") == set()
    assert len([w for w in graph.writes if "REMOVE n.embedding" in w]) == 5


def test_the_removal_statement_is_idempotent_on_an_already_stripped_node(stores,
                                                                      monkeypatch):
    """`REMOVE` of an absent property is a no-op — the property of the design that makes
    a re-run harmless even when the plan's own preconditions refuse it."""
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    statement = (
        "MATCH (n:Object) WHERE n.embedding IS NOT NULL AND n.id IN $keys "
        "REMOVE n.embedding RETURN count(n)")
    assert graph.query(statement, {"keys": ["obj-1"]}).result_set[0][0] == 1
    assert graph.query(statement, {"keys": ["obj-1"]}).result_set[0][0] == 0
    assert graph.vector_keys("Object") == set()


def test_a_typed_identity_key_is_stripped_and_the_refusal_is_not_blamed_on_a_writer(
        stores, monkeypatch):
    """⛔ F1 (PR #13647) — a plan accepted a typed key and the removal could not match it.

    `read_graph_vector_keys` made a non-string `:Object.id` keyable, `build_plan` carried
    its `str()` rendering, and `execute` compared that rendering against the TYPED property
    with a type-sensitive `IN`. The removal selected nothing, so the node kept its vector
    and the run exited 5 — whose message names a **concurrent writer** or an open write gate,
    neither of which is true, and whose remedy ("re-plan") reproduces the identical plan.
    The retirement would be wedged for that label with no path the message names.

    The repair is that the plan carries the VALUE as the graph holds it, so the plan and the
    removal compare the same thing.

    Mutation: append `str(node_key)` instead of `node_key` in `read_graph_vector_keys` (the
    pre-fix behaviour) and this test fails at `result["removed"] == 0`.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", 123)            # an integer `:Object.id`
    record.holds("object", "123")        # the record's `text` column renders it
    plan = _plan(graph)
    # The plan carries the GRAPH's value, not a rendering of it.
    assert plan["labels"][0]["keys"] == [123]
    assert plan["total_nodes"] == 1

    result = _execute(graph, plan)
    assert result["removed"] == 1
    assert result["removed_by_label"]["Object"] == 1
    assert result["shortfall_by_label"]["Object"] == 0
    assert result["residual_by_label"]["Object"] == 0
    assert graph.vector_keys("Object") == set()
    # …so the post-write verdict is a COMPLETED retirement, not exit 5 blaming a writer.
    assert strip.result_exit_code(result) == 0


def test_a_plan_that_RENDERED_a_typed_key_is_refused_as_stale(stores, monkeypatch):
    """A plan minted before typed keys were first-class is STALE, never a silent no-op.

    Such a plan is self-consistent (its own digest matches its lists), so only the LIVE
    comparison can catch it — which is exactly what the fail-closed direction needs: the
    operator is told to re-plan rather than shown a residual blamed on a concurrent writer.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", 123)
    record.holds("object", "123")
    plan = _plan(graph)
    plan["labels"][0]["keys"] = ["123"]          # the pre-fix rendering, self-consistently
    plan["digest"] = strip.digest(strip.plan_entries(plan))
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan)
    assert excinfo.value.token == strip.REFUSE_PLAN_STALE
    assert graph.vector_keys("Object") == {123}   # nothing was removed


def test_a_second_execute_with_the_same_plan_refuses_as_stale(stores, monkeypatch):
    """A drained graph is a STALE plan, not a vacuous success — re-plan instead."""
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph)
    _execute(graph, plan)
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan)
    assert excinfo.value.token == strip.REFUSE_PLAN_STALE


# ── the record leg: the ONE comparison that certifies "the record has it" ───


class _FakeCursor:
    """Answers the two record statements by SHAPE, and records what was executed."""

    def __init__(self, aggregate, rows):
        self._aggregate = aggregate
        self._rows = rows
        self.description: list[tuple[str]] = []
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params):
        self.executed.append((sql, params))
        if "FILTER (WHERE embedding_vec IS NOT NULL)" in sql:
            self.description = [("record_readable",), ("record_unreadable",)]
            self._pending = [self._aggregate]
        else:
            self.description = [("node_key",), ("readable",), ("has_payload",)]
            self._pending = list(self._rows)

    def fetchall(self):
        return self._pending


def _install_record(monkeypatch, aggregate, rows):
    import tortoise.record_projection as rp
    import tortoise.record_serve as rs

    cursor = _FakeCursor(aggregate, rows)

    class _Conn:
        closed = False

        def close(self):
            _Conn.closed = True

    monkeypatch.setitem(sys.modules, "psycopg2", types.ModuleType("psycopg2"))
    monkeypatch.setattr(rs, "open_record", lambda dsn: _Conn())
    monkeypatch.setattr(rp, "scope_connection", lambda conn, org_id: cursor)
    return cursor, _Conn


def test_the_record_leg_is_bounded_to_the_graph_keys_and_never_counts_a_NULL_vec(
        monkeypatch):
    """⛔ THE RECORD COMPARISON, PINNED — the one fact that licenses deleting anything.

    Two properties, and both were previously unpinned because every other test replaced
    this reader wholesale:

    1. **BOUNDED.** The rows that cross the wire are narrowed by `node_key = ANY(...)` to
       the graph's own key set — never an unbounded full-table fetch. A fake that
       returned the whole table would have kept every other test green while the real
       read scanned `public.event` end to end.
    2. **`embedding_vec` IS THE TEST, not `embedding`.** A row whose payload is present
       but whose GENERATED `embedding_vec` is NULL answers no dense query, so it is NOT
       the copy. Mutating `_RECORD_KEYS_SQL` to select `(embedding IS NOT NULL) AS
       readable` — the exact plausible edit — reds this test and nothing else.
    """
    cursor, conn = _install_record(
        monkeypatch,
        aggregate=(7, 2),                                    # readable, unreadable
        rows=[("obj-1", True, True),                         # the copy exists
              ("obj-2", False, True),                        # payload, NULL vec: NOT it
              ("obj-3", False, False)])                      # bare row
    out = parity.read_record_vector_keys(
        "postgresql://x", "org", "graph", "object", {"obj-1", "obj-2", "obj-3"})

    assert out["readable"] == {"obj-1"}
    assert out["unreadable"] == 1
    assert out["record_readable_total"] == 7
    assert conn.closed is True

    keys_sql, keys_params = cursor.executed[-1]
    assert "node_key = ANY(%s)" in keys_sql
    assert keys_params == ("org", "graph", ["obj-1", "obj-2", "obj-3"])
    # …and no statement fetches the table without that narrowing.
    for sql, _params in cursor.executed:
        assert "node_key = ANY(%s)" in sql or "FILTER (WHERE embedding_vec IS NOT NULL)" in sql
    # The aggregate is ONE row, so RESULTSET_SIZE cannot truncate it.
    assert "count(*)" in cursor.executed[0][0]


def test_the_record_aggregate_read_NAMES_its_own_columns():
    """⛔ F1, AS A SHAPE PIN THAT CANNOT AGREE WITH A FAKE.

    PostgreSQL names an unaliased aggregate ``count``, so a statement written without
    ``AS`` returns TWO columns called ``count`` — and `_RECORD_AGGREGATE_COLUMNS` then
    never matches, making EVERY real run exit 3 with "the record schema this census
    depends on has moved", which points an operator at a schema that did not move.

    The fake cursor answers with whatever names it is given, so it cannot catch this: the
    only witness is the SQL TEXT. Mutation: remove either ``AS`` and this test reds while
    every other record-leg test stays green.
    """
    for table in ("object", "subject", "event", "source"):
        sql = parity._RECORD_AGGREGATE_SQL.format(table=table)
        for column in parity._RECORD_AGGREGATE_COLUMNS:
            assert f"AS {column}" in sql, f"{table}: {column} is not named in the aggregate"
        # …and no bare aggregate survives as an unnamed projection.
        assert sql.count("AS record_readable") == 1
        assert sql.count("AS record_unreadable") == 1


def test_the_record_leg_refuses_a_schema_that_moved(monkeypatch):
    """A missing column is a REFUSAL, never an empty comparison that certifies."""
    cursor, _conn = _install_record(monkeypatch, aggregate=(0, 0), rows=[])
    cursor.description = [("something_else",)]
    import tortoise.record_projection as rp
    import tortoise.record_serve as rs

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(rs, "open_record", lambda dsn: _Conn())

    class _Cursor(_FakeCursor):
        def execute(self, sql, params):
            self.executed.append((sql, params))
            self.description = [("something_else",)]
            self._pending = [(0, 0)]

    monkeypatch.setattr(rp, "scope_connection", lambda conn, org_id: _Cursor((0, 0), []))
    with pytest.raises(parity.CensusError, match="did not return"):
        parity.read_record_vector_keys(
            "postgresql://x", "org", "graph", "object", {"obj-1"})


def test_the_record_leg_refuses_an_unvetted_table_name(monkeypatch):
    """The table name is interpolated into SQL, so it is allowlisted, not escaped."""
    with pytest.raises(parity.CensusError, match="not one of this census's record tables"):
        parity._quote_record_table("object; DROP TABLE point")


# ── the census's population statement is the MEASURED one ──────────────────


def test_a_narrowed_census_certifies_only_the_labels_it_measured(stores):
    """⛔ `--label Object` must not read as "all four are at parity".

    `population` is the MEASURED set and the render says so, because a report that named
    all four while querying one is the "gate reports a pass without having looked" class
    this module exists to refuse. A mutation back to `[r[1] for r in RETIRED]` reds this.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    report = _census(graph, labels=["Object"])
    assert report["population"] == ["Object"]
    assert report["declared_population"] == ["Object", "Subject", "Event", "Source"]
    assert report["narrowed"] is True
    text = parity.render(report, "g")
    assert "every label MEASURED (Object)" in text
    assert "NARROWED" in text
    # …and the un-measured labels are NAMED as unmeasured, never silently omitted.
    narrowed_line = text.split("NARROWED", 1)[1]
    for absent in ("Event", "Source", "Subject"):
        assert absent in narrowed_line


def test_parity_and_ready_are_two_different_answers(stores, monkeypatch):
    """Parity is a pure store fact; `ready` also carries the write gate.

    A census reporting only one would either hide the write-gate refusal (a reader
    ticketing "parity holds, so strip") or let the gate's absence look like a parity
    failure (a reader backfilling a record that is already complete).
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    report = _census(graph, labels=["Object"])
    assert report["parity"] is True
    assert report["ready"] is False          # no write-stop on this tree
    assert "WRITE GATE" in parity.render(report, "g")
    monkeypatch.setattr(strip, "write_gate", lambda entity_type: (False, "stopped"))
    report = strip.parity.census(
        g=graph, record_dsn="postgresql://x", org_id="org", graph_id="graph",
        labels=["Object"], strip_readiness=strip.strip_readiness)
    assert report["ready"] is True


# ── the plan is the authority over an exact set ────────────────────────────


def test_the_plan_carries_its_keys_and_the_removal_uses_them(stores, monkeypatch):
    """The approved set, not a set re-read after the check.

    The digest already proves live == plan; stripping the LIVE set instead would let a
    vector that appeared in the check-to-write window be deleted without ever being in
    the approved plan.
    """
    _ready(monkeypatch)
    graph, record = stores
    for key in ("obj-1", "obj-2"):
        graph.add("Object", key)
        record.holds("object", key)
    plan = _plan(graph, labels=["Object"])
    assert plan["labels"][0]["keys"] == ["obj-1", "obj-2"]
    assert sorted(strip.plan_entries(plan)) == [("Object", "obj-1"), ("Object", "obj-2")]
    _execute(graph, plan, batch=1)
    stripped = [params["keys"] for _cypher, params in graph.removal_calls]
    removed_keys = [key for batch in stripped for key in batch]
    assert sorted(removed_keys) == ["obj-1", "obj-2"]


def test_an_edited_plan_cannot_smuggle_an_empty_set_through_the_count(stores,
                                                                    monkeypatch):
    """`total_nodes` is a field a hand can edit; the ENTRY list is the authority.

    A plan whose `total_nodes` says 5 while its per-label key lists are empty is the
    vacuous "finished retirement" `REFUSE_NOTHING_TO_STRIP` exists for. Derived from the
    entries, the refusal fires.
    """
    _ready(monkeypatch)
    graph, _record = stores
    plan = _plan(graph, labels=["Object"])
    plan["labels"] = [{"label": "Object", "keys": [], "nodes": 0,
                       "unkeyed_nodes_blocking": 0, "strip_ready": True,
                       "blocked_reason": None}]
    plan["total_nodes"] = 5
    plan["digest"] = strip.digest([])
    assert strip.plan_entries(plan) == []
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan)
    assert excinfo.value.token == strip.REFUSE_NOTHING_TO_STRIP


def test_execute_refuses_tenant_drift_not_just_a_graph_name_drift(stores, monkeypatch):
    """A plan is an authority over ONE target — and the target is a TRIPLE.

    `--org-id`/`--graph-id` default from the environment and the graph name is often the
    `tortoise` default, so checking only the name lets a plan previewed on one tenant be
    executed against another tenant's record.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph, labels=["Object"])
    for kwarg in ({"org_id": "other-org"}, {"graph_id": "other-graph"},
                  {"graph_name": "other-graph"}):
        with pytest.raises(strip.Refusal) as excinfo:
            _execute(graph, plan, **kwarg)
        assert excinfo.value.token == strip.REFUSE_TARGET_DRIFT
    assert graph.vector_keys("Object") == {"obj-1"}


def test_the_digest_cannot_collide_on_a_key_containing_its_separator():
    """A node key is CALLER-SUPPLIED, so framing is a correctness property.

    Without length framing, the single node `(Object, "a\nSubject\tb")` and the pair
    `(Object, "a") + (Subject, "b")` digest identically — and this digest IS the authority
    to delete.
    """
    single = strip.digest([("Object", "a\nSubject\tb")])
    pair = strip.digest([("Object", "a"), ("Subject", "b")])
    assert single != pair
    # Length framing also keeps the tab-separated reading of a NORMAL set unchanged —
    # pinned against a LITERAL, because comparing the function with itself (the previous
    # version of this line) is green under the pre-fix `label\tkey` join too.
    assert strip.digest([("Object", "obj-1")]) == \
        "sha256:" + hashlib.sha256(b"6:Object5:obj-1").hexdigest()


def test_the_digest_distinguishes_a_typed_key_from_its_rendering():
    """⛔ `str(123)` and `"123"` are DIFFERENT nodes, so they must not share a digest.

    The digest is the authority to delete: if an integer key and a same-text string key
    framed identically, a plan could certify a set and the removal act on the other one. A
    typed key is framed with a leading `~`, which a string frame can never start with (its
    first character is always the length's digit).

    ⛔ AND THE STRING FRAME IS UNCHANGED, byte for byte — a plan minted against an all-string
    population must still re-derive to the digest it recorded (asserted by the literal
    above), so this is an ADDITION to the framing, not a replacement of it.
    """
    assert strip.digest([("Object", 123)]) != strip.digest([("Object", "123")])
    assert strip.digest([("Object", 123)]) == \
        "sha256:" + hashlib.sha256(b"6:Object~int:3:123").hexdigest()
    # A mixed-type label is still a well-defined set (the sort is type-safe, not a crash).
    assert strip.digest([("Object", 123), ("Object", "456")]) != \
        strip.digest([("Object", 123)])
    # …and one key moving between the two families is one key moving.
    assert strip.digest([("Object", 123)]) != strip.digest([("Object", 124)])


# ── the post-write verdict is fail-closed ──────────────────────────────────


def test_an_unmeasurable_residual_is_not_exit_zero():
    """Exit 5 means "may remain"; a verdict that could not be taken must not sum to 0."""
    assert strip.result_exit_code({"residual_by_label": {"Object": 0}, "empty": False}) == 0
    assert strip.result_exit_code({"residual_by_label": {"Object": 3}, "empty": False}) == 5
    assert strip.result_exit_code(
        {"residual_by_label": {"Object": "UNMEASURED(boom)"}, "empty": False}) == 5
    assert strip.result_exit_code({"residual_by_label": {}, "empty": True}) == 0


# ── the plan file is a JSON document, so its LISTS must match its DIGEST ────


def test_a_plan_whose_key_lists_were_edited_is_refused_even_with_the_digest_field(
        stores, monkeypatch):
    """⛔ THE RECOMPUTATION, which is not redundant with the `digest` FIELD check.

    `execute` strips `plan["labels"][*]["keys"]`. The `digest` field is a separate string
    in the same JSON file, so editing a key list leaves it untouched — and a field-only
    check would then approve and strip a set the human never saw, while `plan_entries`
    calls itself "the approved node set". Re-deriving the digest FROM the lists is what
    makes that claim true.

    Mutation: drop the `digest(plan_entries(plan))` recomputation in `_load_plan` and this
    test reds (the run would strip `obj-2` and report success).
    """
    _ready(monkeypatch)
    graph, record = stores
    for key in ("obj-1", "obj-2"):
        graph.add("Object", key)
        record.holds("object", key)
    plan = _plan(graph, labels=["Object"])
    plan["labels"][0]["keys"] = ["obj-2"]          # the set a human did NOT approve
    plan["total_nodes"] = 1
    # …and the recorded digest is left exactly as printed.
    assert plan["digest"] == _plan(graph, labels=["Object"])["digest"]

    # The FIELD check alone would have passed: `_load_plan` is what refuses.
    with pytest.raises(strip.Refusal) as excinfo:
        strip._load_plan(_write_plan_file(plan), plan["digest"])
    assert excinfo.value.token == strip.REFUSE_DIGEST_MISMATCH
    assert graph.vector_keys("Object") == {"obj-1", "obj-2"}


def test_the_target_drift_check_covers_the_URI_not_just_its_name(stores, monkeypatch):
    """⛔ THE FOURTH AXIS. A clone of the same graph has the same NAME, the same tenant
    and the same node set — only the host says which deployment it is.

    Mutation: drop the `graph_uri` row from `execute`'s drift list and this test reds
    while every other drift test stays green.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph, labels=["Object"])
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan, graph_uri=strip.uri_fingerprint("docker://staging:6379/g"))
    assert excinfo.value.token == strip.REFUSE_TARGET_DRIFT
    assert "graph_uri" in str(excinfo.value)
    assert graph.vector_keys("Object") == {"obj-1"}


def test_the_plan_never_circulates_the_graph_password():
    """The plan is what a human approves and pastes — it must not carry a credential."""
    assert strip.uri_fingerprint("docker://:s3cr3t@prod:6379/tortoise") == \
        "docker://prod:6379/tortoise"
    # A URI with no graph path still strips the credential, and a bare path is untouched.
    assert strip.uri_fingerprint("redis://user:pw@h:1") == "redis://h:1"
    assert strip.uri_fingerprint("/tmp/embedded.db") == "/tmp/embedded.db"


def test_a_narrowed_execution_records_the_labels_it_did_not_touch(stores, monkeypatch):
    """⛔ `residual_by_label` is over the plan's labels, so a narrowed strip's zeros are
    about THOSE labels only. Without `unmeasured_by_label` the result reads as a clean
    retirement of a population three quarters of which nobody looked at.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph, labels=["Object"])
    result = _execute(graph, plan)
    assert result["unmeasured_by_label"] == ["Event", "Source", "Subject"]
    assert result["removed"] == 1


def test_a_narrowed_execution_is_not_exit_0(stores, monkeypatch):
    """⛔ EXIT 0 IS THE CONTRACT "stripped AND verified" — for a narrowed plan that is a lie.

    `residual_by_label` is over the plan's labels, so a `--label Object` run whose residuals
    are all zero used to publish 0 while `Event`/`Source`/`Subject` still held their
    vectors: a script gating on the exit code read a partial sweep as a completed
    retirement. The stderr warning is for a human; the exit code is for the caller.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    # ⛔ TWO approvals for two executions: a plan is an EXACT key set, so re-running the
    # same approval after it stripped (rightly) refuses on the population digest.
    plan = _plan(graph, labels=["Object"])
    deliberate = _plan(graph, labels=["Object"])
    result = _execute(graph, plan)
    assert result["removed"] == 1 and result["residual_by_label"] == {"Object": 0}
    assert strip.result_exit_code(result) == 5, "a partial sweep published a clean exit"

    # …and an operator who says so deliberately gets 0, on the record. A fresh node keeps
    # this plan's key set exact (a plan names keys, not labels).
    graph.add("Object", "obj-2")
    record.holds("object", "obj-2")
    plan = _plan(graph, labels=["Object"])
    deliberate = _plan(graph, labels=["Object"])
    acknowledged = _execute(graph, deliberate, expect_narrowed=True)
    assert acknowledged["expect_narrowed"] is True and acknowledged["removed"] == 1
    assert strip.result_exit_code(acknowledged) == 0

    # A plan that covers EVERY declared label is unaffected: nothing was left unmeasured.
    for label, key in (("Event", "ev-1"), ("Source", "src-1"), ("Subject", "subj-1")):
        graph.add(label, key)
        record.holds(label.lower(), key)
    whole = _plan(graph)
    assert sorted(whole["declared_population"]) == ["Event", "Object", "Source", "Subject"]
    assert strip.result_exit_code(_execute(graph, whole)) == 0


def test_a_plan_without_a_labels_key_refuses_instead_of_raising(stores, monkeypatch):
    """A hand-edited plan must get a published token, not a `KeyError` traceback.

    `_load_plan` tolerates a missing `labels` key (`plan_entries` uses `.get`), so `execute`
    reading `plan["labels"]` crashed at the one moment an operator most needs a verdict —
    and exit 1 is argparse's usage code, not any of the eleven refusal tokens.
    """
    _ready(monkeypatch)
    graph, _record = stores
    graph.add("Object", "obj-1")
    plan = _plan(graph, labels=["Object"])
    del plan["labels"]
    plan["digest"] = strip.digest(strip.plan_entries(plan))
    # Whatever the refusal, it is a PUBLISHED one on the destructive path — never a
    # `KeyError` traceback, which exits 1 ("usage") and names nothing.
    try:
        _execute(graph, plan)
    except strip.Refusal as exc:
        assert exc.token in strip.REFUSALS, exc.token
        assert "KeyError" not in str(exc)
    except strip.CensusError as exc:
        assert "NO label" in str(exc)
    else:                                    # pragma: no cover - must refuse
        pytest.fail("a plan with no `labels` key executed")
    assert graph.vector_keys("Object"), "the refusal must not have removed anything"


def test_an_edited_plan_cannot_acknowledge_its_own_narrowing(stores, monkeypatch):
    """⛔ THE DIGEST COVERS THE KEY LISTS, NOT `declared_population`, so that field is a hand-
    editable declaration — and deriving "what was left unmeasured" from it let an edited
    plan publish exit 0 while the other labels still held their vectors. The live census is
    a measurement; the plan is an authority over ONE key set.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph, labels=["Object"])
    plan["declared_population"] = ["Object"]          # the edit the digest cannot see
    plan["digest"] = strip.digest(strip.plan_entries(plan))
    result = _execute(graph, plan)
    assert result["unmeasured_by_label"], "a plan field acknowledged its own narrowing"
    assert strip.result_exit_code(result) == 5


def test_every_type_a_label_serves_must_be_asked_about_BY_SOME_WRITER(monkeypatch, tmp_path):
    """⛔ `:Source` is addressed as BOTH `document` and `source`, and its writers are SPLIT.

    A single document-only stop leaves the `source` half writing, so coverage is required per
    TYPE — but as a UNION across writers, not a cross-product: `_upsert_document` writes only
    the document's vector and `_upsert_source` only the provenance source's, so demanding that
    EACH ask about BOTH would only be satisfied by a document fold keyed on the SOURCE
    predicate (stopping document writes as soon as source coverage verifies — a vector in
    neither store, #11395). Both directions are asserted below.
    """
    document_only = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_document(ev):
    if not _derived_mirror_required("document"):
        props.pop("embedding", None)
    return _write(props)
""")
    monkeypatch.setattr(strip, "_writers_for",
                        lambda label: ((f"{document_only}::_upsert_document",), None))
    gated, why = strip._fold_consults_gate("Source")
    assert gated is False, "a document-only stop cleared the `source` half of :Source"
    assert "source" in why, "the refusal must name the type nobody asks about"

    # …and the NATURAL split population opens the gate: one writer per type.
    split = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_document(ev):
    if not _derived_mirror_required("document"):
        props.pop("embedding", None)
    return _write(props)


def _upsert_source(ev):
    if not _derived_mirror_required("source"):
        props.pop("embedding", None)
    return _write(props)
""", name="fake_writer_13629_split")
    monkeypatch.setattr(strip, "_writers_for", lambda label: (
        (f"{split}::_upsert_document", f"{split}::_upsert_source"), None))
    gated, why = strip._fold_consults_gate("Source")
    assert gated is True, f"the natural split population was refused: {why}"


def test_upsert_source_IS_a_Source_writer_through_the_PASSTHROUGH(tmp_path, monkeypatch):
    """⛔ `_upsert_source` STORES A VECTOR — DROPPING IT FROM `FOLD_WRITERS` WOULD FAIL OPEN.

    A concurrent review at `96f36c3d` proposed exactly that drop: it measured
    `grep -cE 'set_embedding_clause|clear_embedding'` over
    `tortoise/projection/entities.py:7117-7546` → **0** and concluded the fold "stores no
    vector", so `:Source` could never open on it. The measurement is true and the
    conclusion is not: it reads the STATIC clause text only, which is the measurement the
    writer register itself already corrected at `#12182` cycle 9 (P1). `_upsert_source`
    ends by funnelling extra props through
    `_persist_extra_props(..., allow_keys=_allow)` with `_allow = self._SOURCE_EXTRA_PROPS`,
    whose CLOSED surface declares `embedding`, and that helper emits `SET n += $extra`.
    A caller-supplied `embedding` therefore lands on the node (measured live in `#12182`:
    `create_source(..., props={'embedding': [...]})` then `RETURN s.embedding` answers),
    which is `#12180`'s open subject — a writer that re-adds the vector on its next call.

    Removing the name would let `_fold_consults_gate("Source")` clear a population that
    still writes: the exact fail-open `FOLD_WRITERS` exists to prevent, and the reason it
    is a REFUSAL rather than a design review. The three facts the refusal rests on are
    pinned here so the drop cannot be re-derived from the grep.

    Mutation: delete `"embedding"` from `_SOURCE_EXTRA_PROPS`, or route the allowlist past
    `_persist_extra_props`, or drop the name from `FOLD_WRITERS["Source"]` — each reds
    this test.
    """
    import inspect

    from tortoise.projection import entities as entities_module

    assert "embedding" in entities_module._EntityHandlers._SOURCE_EXTRA_PROPS, (
        "the :Source caller allowlist stopped declaring `embedding` — re-measure "
        "whether `_upsert_source` can still persist a caller vector before trusting "
        "the static-clause count")

    body = inspect.getsource(entities_module._EntityHandlers._upsert_source)
    assert "_allow = self._SOURCE_EXTRA_PROPS" in body, (
        "the fold no longer hands the caller allowlist to the passthrough")
    assert "_persist_extra_props" in body and "allow_keys=_allow" in body, (
        "the fold no longer persists caller props through the closed passthrough")

    passthrough = inspect.getsource(entities_module._EntityHandlers._persist_extra_props)
    assert "SET n += $extra" in passthrough, (
        "the passthrough stopped emitting the map SET — re-measure the store")

    assert "_upsert_source" in strip.FOLD_WRITERS["Source"], (
        "`_upsert_source` was dropped from :Source's declared writers, so a strip would "
        "be undone by the next caller-supplied embedding")

    # …and the operator-facing refusal reason says so, because it is the sentence an
    # operator reads when the gate shuts: an inaccurate writer list is a diagnosis bug.
    gated, why = strip._fold_consults_gate("Source")
    assert gated is False
    assert "tortoise.projection.entities::_upsert_source" in why


def test_a_census_over_no_label_refuses_instead_of_certifying_nothing(stores):
    """`all([])` is True: an empty measured population would report parity on zero evidence."""
    graph, _record = stores
    with pytest.raises(parity.CensusError, match="measured NO label"):
        _census(graph, labels=[])


def test_the_census_reports_the_tenant_wide_total_separately(stores):
    """`record_vectors` is narrowed to the graph's keys; the tenant total is its own field.

    Two numbers, because they answer different questions and the docstring's table names
    both: `record_vectors` is "can the record answer for THESE nodes",
    `record_readable_total` is "how many readable rows does this tenant hold at all".
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    record.holds("object", "obj-not-in-this-graph")
    row = _row(_census(graph, labels=["Object"]), "Object")
    assert row["record_vectors"] == 1
    assert row["record_readable_total"] == 2
    assert row["record_only"] == 1


def test_an_unimportable_stripper_is_not_read_as_a_caller_opt_out(monkeypatch):
    """A broken import and an explicit `None` are different states with different fixes.

    Both fold to "not ready", but a census that reported the opt-out message for a broken
    import would send an operator to fix their own call instead of the module.
    """
    import builtins

    real_import = builtins.__import__

    def _explode(name, *args, **kwargs):
        if name == "strip_graph_entity_vectors":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _explode)
    gate = parity._write_gate()
    ready, reason = gate("Object")
    assert ready is False
    assert "not importable" in reason and "ImportError" in reason


def test_the_removal_never_re_reads_a_key_set_it_is_about_to_strip(stores, monkeypatch):
    """⛔ F4.1 — a READ-ORDER witness, because a set comparison cannot discriminate.

    In a fixture where live == plan, a fresh read returns the identical keys, so
    `removal_calls` is byte-identical whether the removal uses the plan's keys or re-reads
    the table — that is why the previous assertion could not fail.

    The order is what differs. `execute` reads the key set twice per label, BOTH before any
    removal: the live parity re-check and the digest comparison. So a page read for label X
    that happens AFTER another label has been stripped, and BEFORE X's own `REMOVE`, exists
    only if the removal is computing its own set. Post-removal residual reads are for
    labels already stripped, so they cannot trigger this.

    Mutation: revert `keys = sorted(row.get("keys") or ())` to a `read_graph_vector_keys`
    call and the SECOND label trips this witness.
    """
    _ready(monkeypatch)
    graph, record = stores
    for key in ("obj-1", "obj-2"):
        graph.add("Object", key)
        record.holds("object", key)
    for key in ("sub-1", "sub-2"):
        graph.add("Subject", key)
        record.holds("subject", key)

    removed: set[str] = set()
    reads_after_a_removal: list[tuple[str, str]] = []
    original = graph._answer

    def _watched(cypher, params, *, write):
        label = _LABEL_RE.search(cypher).group(1)
        if not write and "ORDER BY id(n)" in cypher and removed and label not in removed:
            reads_after_a_removal.append((sorted(removed)[0], label))
        if write and "REMOVE n.embedding" in cypher:
            removed.add(label)
        return original(cypher, params, write=write)

    graph._answer = _watched          # type: ignore[method-assign]
    plan = _plan(graph, labels=["Object", "Subject"], batch=1)
    result = _execute(graph, plan, batch=1)

    assert reads_after_a_removal == [], reads_after_a_removal
    assert result["removed"] == 4
    # …and both labels really were stripped, so the witness above ran over a real removal.
    assert graph.vector_keys("Object") == set()
    assert graph.vector_keys("Subject") == set()


def test_a_removal_that_raises_splits_on_whether_anything_was_stripped(stores,
                                                                     monkeypatch):
    """⛔ THE *ATTEMPTED* COUNT DECIDES THE TOKEN, never the returned row counts.

    Exit 2 promises "refused, nothing written, safe to retry"; exit 6 promises "part of the
    graph is already stripped, re-plan". A FIRST-statement failure — what a store wedge, a
    timeout or an oversized `--batch` actually produces — has no earlier batch that could
    have stripped anything, so it must be exit 2. Reporting "part-stripped" there teaches
    an operator to distrust exit 6 on the one occasion it matters.

    ⛔ AND `sum(removed.values())` CANNOT ANSWER IT (`#13647` round-3 finding 6). `removed`
    accumulates `result.result_set[0][0]`, which is 0 when a batch lands but the driver
    returns no row — so a count-based token asserted "NO node was stripped" over a
    part-stripped graph. ARM 3 drives exactly that shape.

    Three arms are driven, because a test that only covers one of them pins a message the
    others contradict.
    """
    _ready(monkeypatch)
    graph, record = stores
    for key in ("obj-1", "obj-2", "obj-3"):
        graph.add("Object", key)
        record.holds("object", key)
    plan = _plan(graph, labels=["Object"], batch=1)

    # ── ARM 1: the FIRST batch fails ⇒ nothing could have been removed ⇒ exit 2 ────
    graph.fail_removal = True
    with pytest.raises(strip.Refusal) as first:
        _execute(graph, plan, batch=1)
    assert first.value.token == strip.REFUSE_WRITE_ERROR
    assert "FIRST removal statement" in str(first.value)
    assert graph.vector_keys("Object") == {"obj-1", "obj-2", "obj-3"}
    assert strip.refusal_exit_code(strip.REFUSE_WRITE_ERROR) == 2

    # ── ARM 2: the failure lands AFTER a batch ⇒ part-stripped ⇒ exit 6 ──────
    graph.fail_removal = False
    calls = {"n": 0}
    original = graph._answer

    def _fail_second(cypher, params, *, write):
        if write and "REMOVE n.embedding" in cypher:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated store failure on batch 2")
        return original(cypher, params, write=write)

    graph._answer = _fail_second            # type: ignore[method-assign]
    with pytest.raises(strip.Refusal) as second:
        _execute(graph, plan, batch=1)
    assert second.value.token == strip.REFUSE_WRITE_FAILED
    assert "ALREADY COMPLETED" in str(second.value)
    # One node really is gone — that is what makes exit 6 honest here.
    assert graph.vector_keys("Object") == {"obj-2", "obj-3"}
    assert strip.refusal_exit_code(strip.REFUSE_WRITE_FAILED) == 6

    # ── ARM 3: a batch LANDS but the driver reports NO ROW ⇒ still exit 6 ─────
    # The pre-fix rule summed `removed`, saw 0, and told the operator "no node was
    # stripped" over a graph it had just half-stripped.
    reset, _record = FakeGraph(), Record()
    for key in ("obj-1", "obj-2", "obj-3"):
        reset.add("Object", key)
        _record.holds("object", key)
    plan3 = strip.build_plan(
        g=reset, record_dsn="postgresql://x", org_id="org", graph_id="graph",
        graph_name="g", graph_uri="docker://x/g", labels=["Object"], batch=1)
    stats = {"n": 0}
    original3 = reset._answer

    def _strip_then_report_nothing(cypher, params, *, write):
        if write and "REMOVE n.embedding" in cypher:
            stats["n"] += 1
            if stats["n"] == 1:
                original3(cypher, params, write=write)   # the batch really lands…
                return []                                # …and reports no row
            raise RuntimeError("simulated store failure on batch 2")
        return original3(cypher, params, write=write)

    reset._answer = _strip_then_report_nothing    # type: ignore[method-assign]
    with pytest.raises(strip.Refusal) as third:
        _execute(reset, plan3, batch=1)
    assert third.value.token == strip.REFUSE_WRITE_FAILED, (
        f"a LANDED batch that reported no row was read as 'nothing was stripped': {third.value}")
    assert "part-stripped" in str(third.value)
    # …and the graph proves the batch landed, so exit 2's promise would have been false.
    assert reset.vector_keys("Object") == {"obj-2", "obj-3"}

    # The rest of the mapping is unchanged: everything else is a pre-write refusal.
    assert strip.refusal_exit_code(strip.REFUSE_GRAPH_ONLY) == 2
    assert strip.refusal_exit_code(strip.REFUSE_PLAN_STALE) == 2
    assert strip.refusal_exit_code(strip.REFUSE_NOTHING_TO_STRIP) == 2


def test_narrowing_flags_are_REFUSED_on_execute_rather_than_silently_dropped(capsys):
    """⛔ `--label` narrows the PLAN; at execute time the digest is the authority.

    Accepting `--label` on `--execute` and ignoring it strips the WHOLE approved plan
    while the operator believes one label was targeted — an argparse-validated parameter
    that the run never reads.
    """
    base = ["--uri", "docker://h/g", "--record-dsn", "postgresql://x",
            "--org-id", "o", "--graph-id", "g", "--execute",
            "--plan", "/tmp/p.json", "--confirm-plan-digest", "sha256:x"]
    for extra in (["--label", "Object"], ["--plan-out", "/tmp/out.json"]):
        with pytest.raises(SystemExit) as excinfo:
            strip.main([*base, *extra])
        assert excinfo.value.code == 2          # argparse usage error, before any store
        err = capsys.readouterr().err
        assert extra[0] in err


def _stub_census_main(monkeypatch, report):
    """Point `parity.main` at a canned report and a graph that never opens."""
    monkeypatch.setattr(parity, "census", lambda **kwargs: dict(report))
    monkeypatch.setattr(parity, "render", lambda *a, **k: "")
    monkeypatch.setattr(parity, "_write_gate", lambda: None)

    class _Proj:
        g = None

    # `FalkorProjection` is imported INSIDE `main`, so the seam is the projection module.
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "FalkorProjection",
                        type("P", (), {"from_uri": staticmethod(lambda *a, **k: _Proj()),
                                       "__init__": lambda self, *a, **k: None}))
    # …and the graph-target helpers come from `turn_parity_census` (`census_mod`).
    monkeypatch.setattr(parity.census_mod, "resolve_graph_name", lambda *a, **k: "g")
    monkeypatch.setattr(parity.census_mod, "_refuse_unless_graph_present",
                        lambda *a, **k: None)
    monkeypatch.setattr(parity.census_mod, "empty_graph_target_flag", lambda *a, **k: None)


def test_the_census_json_never_carries_the_graph_password(monkeypatch, tmp_path, capfd):
    """⛔ THE URI IS A CREDENTIAL, and `--json` writes it to a FILE.

    The documented invocation is `--uri 'docker://:pw@host:6379/<graph>'`, and the report is
    the artefact an operator pastes into an issue — so `report["graph_uri"] = args.uri` put
    the graph password on disk. The stripper half of this pair already closes exactly this
    with `uri_fingerprint` (and `test_the_plan_never_circulates_the_graph_password`); a
    census that spells it its own way breaks the rule the pair establishes.
    """
    report = {
        "at": "now", "labels": [], "keys_by_label": {},
        "parity": True, "ready": True, "blocked_reason": "",
        "population": ["Object", "Subject", "Event", "Source"],
        "declared_population": ["Object", "Subject", "Event", "Source"],
        "narrowed": False, "graph_page_size": 10,
    }
    _stub_census_main(monkeypatch, report)
    out = tmp_path / "census.json"
    rc = parity.main(["--uri", "docker://:s3cret@h:6379/g",
                      "--record-dsn", "postgresql://x",
                      "--org-id", "o", "--graph-id", "g",
                      "--json", str(out)])
    assert rc == 0
    written = out.read_text()
    assert "s3cret" not in written, "the census artifact carries the graph password"
    import json as _json

    assert _json.loads(written)["graph_uri"] == "docker://h:6379/g"
    # …the helper is the STRIPPER's own fingerprint, not a second spelling of it.
    assert parity._report_uri("docker://:pw@h:6379/g") == strip.uri_fingerprint(
        "docker://:pw@h:6379/g")


def test_census_main_withholds_the_resolver_message_that_quotes_the_credential(
        monkeypatch, capsys):
    """⛔ The census's generic catch interpolated `{exc}`, and `resolve_graph_name` casts
    the authority's port through `urllib`, whose `ValueError` QUOTES the offending token —
    for `docker://:pa/ss@host:6379/g` that token is `pa`, a fragment of the graph password.
    Measured through `parity.main` before the fix: stderr carried "…as 'pa'". The
    stripper sibling already withheld it (`_unexpected_failure`); this arm, in the same
    PR three files over, did not.
    """
    def _raise(*a, **k):
        raise ValueError("Port could not be cast to integer value as 'pa'")

    monkeypatch.setattr(parity.census_mod, "resolve_graph_name", _raise)
    rc = parity.main(["--uri", "docker://:pa/ss@host:6379/g",
                      "--record-dsn", "postgresql://x",
                      "--org-id", "o", "--graph-id", "g"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "'pa'" not in err and "pa/ss" not in err, err
    assert "ValueError" in err          # the TYPE is safe and is kept


def test_census_main_separates_parity_from_the_strip_precondition(monkeypatch,
                                                                 capfd):
    """⛔ F4.3 — `--require-parity` and `--require-ready` are two questions.

    The previous code answered `--require-parity` from `report["ready"]`, so a run whose
    PARITY held but whose write gate was open exited 4 under a flag named "parity" —
    sending an operator to hunt a missing record copy that is present. Both flags and the
    NARROWED arm are pinned here because no test called `parity.main` at all.
    """
    report = {
        "at": "now", "labels": [], "keys_by_label": {},
        "parity": True, "ready": False, "blocked_reason": "write gate open",
        "population": ["Object", "Subject", "Event", "Source"],
        "declared_population": ["Object", "Subject", "Event", "Source"],
        "narrowed": False, "graph_page_size": 10,
    }
    _stub_census_main(monkeypatch, report)

    base = ["--uri", "docker://h/g", "--record-dsn", "postgresql://x",
            "--org-id", "o", "--graph-id", "g"]
    # Parity holds ⇒ --require-parity is satisfied even though the gate is open.
    assert parity.main([*base, "--require-parity"]) == 0
    # …and --require-ready is not.
    assert parity.main([*base, "--require-ready"]) == 4
    # A NARROWED run cannot satisfy either flag, and says why.
    narrowed = dict(report, population=["Object"], narrowed=True)
    monkeypatch.setattr(parity, "census", lambda **kwargs: dict(narrowed))
    assert parity.main([*base, "--require-parity"]) == 4
    err = capfd.readouterr().err
    assert "NARROWED" in err and "Subject" in err


# ── the refusals are a closed, published set ────────────────────────────────


def _write_plan_file(plan: dict) -> str:
    """A plan on disk whose FORM is whatever the test built — `_load_plan` polices it."""
    import json as _json
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        _json.dump(plan, handle)
        return handle.name


def test_the_refusal_set_is_closed():
    """A refusal outside `REFUSALS` is a bug, and `Refusal` says so at construction."""
    assert len(set(strip.REFUSALS)) == len(strip.REFUSALS)
    with pytest.raises(ValueError, match="not in REFUSALS"):
        strip.Refusal("REFUSE_MADE_UP", "…")
    for token in strip.REFUSALS:
        assert token.startswith("REFUSE_")


def test_the_preview_renders_every_label_and_the_digest(stores):
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph)
    text = strip.render_plan(plan)
    for label in ("Object", "Subject", "Event", "Source"):
        assert label in text
    assert plan["digest"] in text
    assert "PREVIEW — nothing was removed" in text


# ── the pins actually run in CI ─────────────────────────────────────────────


def test_a_tools_only_diff_selects_this_pin():
    """Registration is CLASSIFICATION — it does not make a diff RUN this file.

    `tools/` is a flat `NON_PYTHON_PREFIXES` entry, so a change confined to these two
    instruments would take the docs-only early return and run tier-1 smoke, where this
    file is not registered. Both tools are therefore `CORE_ALSO` entries.
    """
    from tools import ci_selection as cs

    manifest = cs.load_manifest()
    for path in ("tools/entity_vector_parity_census.py",
                 "tools/strip_graph_entity_vectors.py"):
        selected = cs.select([path], "pull_request", manifest)
        assert "test_13629_entity_vector_retire.py" in selected["test_files"], (
            f"a {path}-only diff does not select this file — its falsifiers never "
            f"run: surfaces={selected['surfaces']} full={selected['full']}"
        )


def test_the_interpreter_guard_ships_in_both_entry_points():
    """`#5128`: the guard must be FIRST, and attributable, in both files."""
    for name in ("entity_vector_parity_census.py", "strip_graph_entity_vectors.py"):
        source = (Path(__file__).resolve().parent.parent / "tools" / name).read_text()
        guard = source.index("sys.version_info < (3, 12)")
        for later in ("import argparse", "import hashlib", "import json"):
            if later in source:
                assert guard < source.index(later), (
                    f"{name}: the interpreter guard sits BELOW `{later}`")


# ── #13647 round-3 P1 roots: a rendering is not an identity, and a condition is not a proof ──


def test_a_render_collision_is_not_parity(stores, monkeypatch):
    """⛔ TWO TYPED IDENTITIES THAT RENDER ALIKE ARE NOT ONE NODE (`#13647` round-3 P1).

    The record leg's only language is `text` (its identity column is TEXT), so
    `:Object {id:123}` and `:Object {id:"123"}` both arrive as `"123"`. A `set(text)` walk
    collapses them, and a set-difference against ONE readable record row is empty — so the
    census used to print PARITY over two graph vectors the record holds one copy of, and
    `at_parity`/`parity`/`ready` all read True.

    Falsified three ways: the bucket, the verdict, and the destructive consumer.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", 123)          # typed identity, legal (sdk `SET n += $props`, dumps)
    graph.add("Object", "123")        # …and its rendering: a DIFFERENT node
    record.holds("object", "123")     # ONE readable row, which both would join to
    row = _row(_census(graph, labels=["Object"]), "Object")
    assert row["graph_vectors"] == 2
    assert row["record_vectors"] == 1
    assert row["render_collision"] == 1
    assert row["graph_only"] == 1, "the collision excess is unmatchable, so it is graph-only"
    assert row["at_parity"] is False
    assert row["verdict"] == "RENDER-COLLISION(1)"
    assert "TEXT" in (row["blocked_reason"] or ""), row["blocked_reason"]

    # …and the strip is refused by the LIVE parity re-check, not merely in the printout.
    plan = _plan(graph, labels=["Object"])
    assert plan["ready"] is False
    with pytest.raises(strip.Refusal) as excinfo:
        _execute(graph, plan)
    assert excinfo.value.token == strip.REFUSE_GRAPH_ONLY
    assert graph.vector_keys("Object") == {123, "123"}, "a collided vector was deleted"


def test_parity_requires_a_READABLE_ROW_PER_GRAPH_VECTOR():
    """The COUNT clause of `at_parity`, asserted directly so it cannot be deleted quietly.

    A set-difference answers "is every distinct RENDERING answered". `graph_vectors <=
    record_vectors` is the statement that every NODE is. Both are kept: the collision
    bucket names the cause, this clause is the invariant.
    """
    row = parity.LabelCensus(label="Object", table="object", entity_types=["object"],
                             graph_vectors=2, record_vectors=1)
    assert row.graph_only == 0 and row.render_collision == 0
    assert row.at_parity is False
    row.record_vectors = 2
    assert row.at_parity is True


def test_the_collision_clause_of_at_parity_stands_alone():
    """The COLLISION clause of `at_parity`, asserted directly so it cannot be deleted quietly.

    `#13647`'s instrument is a false-PASS gate, so it states the render-collision invariant
    more than once on purpose: `census()` folds the collision excess into `graph_only`
    (which is what the OTHER consumers, including `execute`'s live parity re-check, read),
    and `at_parity` states it again on the typed count. That redundancy is the point — the
    two statements are deleted independently — but it also means the verdict-level test
    (`test_a_render_collision_is_not_parity`) does NOT redden when this clause alone is
    removed, because `graph_only > 0` still refuses. MEASURED at `426568fe`: dropping
    `and self.render_collision == 0` left the whole file green (83 passed), i.e. an
    unreddened guard.

    So this asserts the clause on the one shape only it covers: a row whose `graph_only`
    and `unkeyed` are clean and whose counts agree, leaving the collision bucket as the
    sole evidence. The control at the end keeps it from being an always-False assertion.

    Adopted from the predecessor lane's rescued tree
    (`refs/wip/issue-13629-retire-entity-vectors`, `2fc26874fc`): its measurement reproduced
    here before the test was taken, and the test reddens when the clause is dropped.
    """
    row = parity.LabelCensus(label="Object", table="object", entity_types=["object"],
                             graph_vectors=2, record_vectors=2, render_collision=1)
    assert row.graph_only == 0 and row.unkeyed_graph_nodes == 0
    assert row.graph_vectors <= row.record_vectors
    assert row.at_parity is False, "a render collision read as parity"
    assert row.verdict == "RENDER-COLLISION(1)"
    row.render_collision = 0
    assert row.at_parity is True, "the control: without the collision this row IS at parity"


def test_a_store_or_exit_behind_a_LIVE_condition_does_not_certify_the_arm(tmp_path,
                                                                       monkeypatch):
    """⛔ A CONDITIONAL STOP IS NOT A STOP (`#13647` round-3 P1, and its 2b spellings).

    `_own_walk` prunes nested SCOPES and statically dead branches, so a stop under a LIVE
    nested condition (`if DEBUG: raise`) still certified the writer as write-stopped while
    the store below ran unconditionally — and the exit arm's answer is the sole fact that
    authorises deleting the only readable copy of a fingerprint. The store/remove arms had
    the mirror looseness.

    The `with`/`try` controls at the end are the other half: an arm's action that IS
    unconditionally reached must still certify, or the fix would be an inert enforcer.
    """
    refused = (
        # the exit, behind a live condition
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        if DEBUG:
            raise
    props["embedding"] = _encode(ev)
    return _write(ev)
""",
        # the same root spelled for the STORE arm (positive ask)
        """
def _upsert_object(props):
    if _derived_mirror_required("object"):
        if flag:
            props["embedding"] = _encode(props)
    return _write({"SET n += $props": props})
""",
        # …and for the REMOVAL arm (negated ask)
        """
def _upsert_object(props):
    if not _derived_mirror_required("object"):
        if flag:
            props.pop("embedding", None)
    return _write({"SET n += $props": props})
""",
        # 2b: a TRUTHY constant walks both arms, so the store in the never-run `else`
        # certified the guard while the vector was written unconditionally below
        """
def _upsert_object(ev):
    if _derived_mirror_required("object"):
        if True:
            pass
        else:
            props["embedding"] = _encode(ev)
    return _write(ev)
""",
        # 2b: `if []:` is dead but is not an `ast.Constant`, so the pruner kept its body
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        if []:
            raise
    props["embedding"] = _encode(ev)
    return _write(ev)
""",
    )
    for source in refused:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a CONDITIONAL stop certified the arm: {source!r} ({why})"

    accepted = (
        # an exit inside a `with` body IS unconditionally reached
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        with TRACE:
            return None
    return _write(ev)
""",
        # …and so is a store inside a `try` body
        """
def _upsert_object(ev):
    embedding = None
    if _derived_mirror_required("object"):
        try:
            embedding = _encode(ev)
        finally:
            _note(ev)
    return _write(ev, embedding)
""",
    )
    for source in accepted:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        assert strip._writer_consults_gate(
            module, "_upsert_object", ("object",))[0] is True, (
            f"an UNCONDITIONAL arm was refused: {source!r}")


#: ⛔ A SWALLOWED `raise` IS NOT AN EXIT (`#13647` round-9 F1, `_unconditional_exits`).
#: `_is_write_stop_exit` answers True for any `ast.Raise`, so the walk — not the predicate
#: — has to withhold the raises it cannot prove leave the function. Both shapes below were
#: certified GATED at `1cc61230` while the store on the next line ran unconditionally, i.e.
#: `REFUSE_WRITE_STILL_EMITS` was lifted for a writer that still emits.
WRITER_SWALLOWED_EXIT = """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        try:
            raise _Skip()
        except Exception:
            pass
    props["embedding"] = _encode(ev)
    return _write(ev)
"""

WRITER_SWALLOWED_EXIT_IN_A_WITH = """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        with suppress(Exception):
            raise _Skip()
    props["embedding"] = _encode(ev)
    return _write(ev)
"""


def test_a_swallowed_exit_does_not_certify_the_arm(tmp_path, monkeypatch):
    """⛔ `raise` IS ONLY A WRITE-STOP IF NOTHING CATCHES IT (`#13647` round-9 F1).

    `_unconditional_walk` descends into a `try` BODY because the body always runs up to a
    raise — which is right for a STORE and wrong for an EXIT, since a raise there is caught
    by that same `try`'s handlers (`try: raise` / `except Exception: pass` falls straight
    through to the store below). The mirror shape is a context manager: `__exit__` may
    return True and swallow the raise (`with suppress(Exception): raise`). Both were
    reported GATED at `1cc61230` — a false PASS on the boolean that authorises deleting the
    only readable copy of a fingerprint.

    Mutation: put the exit arm back on `_unconditional_walk` (i.e. delete the swallowable
    filter) and the first two cases below go `True`, reddening this test; the accepted
    cases at the end are what keeps the fix from being an inert enforcer.

    ⛔ AND THE SWALLOWING CONSTRUCT NEED NOT BE *INSIDE* THE GUARD. The exit arm starts its
    descent at a statement of the guard's own body, so a `try`/`with` that ENCLOSES the
    guard was invisible to it — the same fail-OPEN one level up, measured GATED at
    `97e1b9e2` and closed by `_swallowed_from_outside`. Mutation: delete the
    `swallowable=_swallowed_from_outside(...)` seed and the two ancestor cases below go
    `True`.
    """
    refused = (
        WRITER_SWALLOWED_EXIT,
        WRITER_SWALLOWED_EXIT_IN_A_WITH,
        # the same root through `except*`, which parses as `TryStar` and must set the
        # same flag — the `Try` arm's `getattr(ast, "TryStar", ast.Try)` sibling
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        try:
            raise _Skip()
        except* Exception:
            pass
    props["embedding"] = _encode(ev)
    return _write(ev)
""",
        # …and a manager nested inside a `try/finally`: the flag is inherited, not reset by
        # the arm that CANNOT catch
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        try:
            with suppress(Exception):
                raise _Skip()
        finally:
            _note(ev)
    props["embedding"] = _encode(ev)
    return _write(ev)
""",
        # ⛔ AND THE SWALLOW CAN ENCLOSE THE GUARD — a `try` above it (the shape a
        # function-level wrapper takes, e.g. `hosted_backup::restore_graph`) or a `with`.
        # The exit arm never sees these: it starts inside the guard body.
        """
def _upsert_object(ev):
    try:
        if not _derived_mirror_required("object"):
            raise _Skip()
    except Exception:
        _log(ev)
    props["embedding"] = _encode(ev)
    return _write(ev)
""",
        """
def _upsert_object(ev):
    with suppress(Exception):
        if not _derived_mirror_required("object"):
            raise _Skip()
    props["embedding"] = _encode(ev)
    return _write(ev)
""",
    )
    for source in refused:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a SWALLOWED exit certified the arm: {source!r} ({why})"

    # ⛔ AND A `return` IS NEVER SWALLOWED, nor is a raise nothing can catch: `try/finally`
    # has no handlers, and its `finally` cannot cancel the propagation — while an enclosing
    # `try` cannot catch a raise placed in its OWN handler. Those must still certify, or the
    # gate would be permanently shut.
    accepted = (
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        try:
            raise _Skip()
        finally:
            _note(ev)
    return _write(ev)
""",
        # ⛔ AN ENCLOSING `try/finally` CANNOT CATCH, and an enclosing `try`'s HANDLER is
        # the one position in that `try` its own handlers never see — both are genuine
        # exits, so refusing them would be the inert-enforcer direction of the same fix.
        """
def _upsert_object(ev):
    try:
        if not _derived_mirror_required("object"):
            raise _Skip()
    finally:
        _note(ev)
    return _write(ev)
""",
        """
def _upsert_object(ev):
    try:
        _prep(ev)
    except Exception:
        if not _derived_mirror_required("object"):
            raise _Skip()
    props["embedding"] = _encode(ev)
    return _write(ev)
""",
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        with TRACE:
            return None
    return _write(ev)
""",
    )
    for source in accepted:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        assert strip._writer_consults_gate(
            module, "_upsert_object", ("object",))[0] is True, (
            f"an exit nothing can swallow was refused: {source!r}")

    # ⛔ AND THE FLAG IS AN *EXIT* FLAG ONLY. A store inside a `try` body that DOES declare a
    # handler still places the vector before any raise can be caught, so it still certifies —
    # widening the pruning to stores would refuse the honest shape.
    module = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    embedding = None
    if _derived_mirror_required("object"):
        try:
            embedding = _encode(ev)
        except Exception:
            pass
    return _write(ev, embedding)
""")
    assert strip._writer_consults_gate(
        module, "_upsert_object", ("object",))[0] is True, (
        "a store inside a `try` body was refused as a swallowed exit")


def test_the_stop_arm_must_not_store_the_vector(tmp_path, monkeypatch):
    """⛔ A STOP ARM THAT STORES IS NOT A WRITE-STOP (`#13647`, measured at `622e9935`).

    The exit arm ran BEFORE the polarity arms and asked only "is there a bare
    `return`/`raise` here?" — never "does this same arm PLACE the vector?". So every shape
    below certified the writer as write-stopped while the arm the gate said *stop* stored
    the embedding, lifting `REFUSE_WRITE_STILL_EMITS` on the boolean that authorises
    deleting the only readable copy of a fingerprint:

        if not _derived_mirror_required("object"):
            props["embedding"] = _encode(ev)     # the vector it was told to stop writing
            return None

    Measured at `622e9935` through `_writer_consults_gate`: all six refused shapes below
    answered `True`. The same held for the `raise` spelling, for a dict writer's
    `update(embedding=…)`, for a `pop("embedding")` followed by a store (the arm both
    removes and re-places it), for a store in the arm's own `finally`, and for a store
    behind a live condition — which is why the rule is "NO store ANYWHERE in the stop arm"
    (`_own_walk`) rather than "no *unconditional* store" (`_unconditional_walk`, still the
    right walk on the mirror arm, where an unconditional store is the evidence FOR).

    Mutation: delete the `anywhere=True` guard in `_asks_the_gate` and every refused shape
    here goes `True` again; delete only the mutator-vocabulary line (`if anywhere and
    _mutator_stores_the_vector(sub)`) and the five METHOD-spelling rows red while the rest
    stay refused. The accepted controls below stay `True` either way, which is what keeps
    the rule from becoming an inert enforcer (a writer with no stop arm at all is refused
    by the polarity arms, not by this one).

    ⛔ AND THE STORE'S *METHOD* SPELLING IS THE SAME STORE (`add2e7b89`). The vocabulary
    recognised the operator form `props |= {"embedding": vec}` and the `update(…)` keyword
    but not the methods those survive as in real code, so `props.__setitem__("embedding",
    vec)`, `props.__ior__({…})`, `props.setdefault("embedding", vec)`,
    `setattr(props, "embedding", vec)` and the unbound `dict.__setitem__(props, …)` placed
    the vector on the stop arm and still certified the writer — the same defect as the six
    shapes above, one verb over. The recognition is stop-arm-only on purpose: on the mirror
    arm it would WIDEN what the destructive gate accepts, and a fix may add refusals, never
    acceptances (`_mutator_stores_the_vector`).

    ⛔ AND THE *FUNCTIONAL* AND *CONSTRUCTOR* SPELLINGS ARE THE SAME STORES (`8637df0f`).
    `operator.setitem(props, "embedding", vec)` is literally what `props["embedding"] =
    vec` compiles to, `operator.ior(props, {…})` is `props |= {…}`, and
    `props.update(dict(embedding=vec))` / `props |= dict(embedding=vec)` / `update(**{…})`
    place exactly the mapping the literal forms already refuse. Measured fail-OPEN at
    `8637df0f`: all of them certified the writer. Same rule, one spelling over.

    ⛔ AND ONE EXPRESSION FORM OVER (`98eec2ca`). Beyond those mutators, three ways a
    mapping is REBUILT — `props = {**props, "embedding": …}`, `props = props |
    {"embedding": …}`, and the writer being HANDED either — plus the keyword mutator
    spelling (`setdefault(key="embedding", default=…)`) and a subscript keyed by a LOCAL
    bound to the property string all certified the stop arm. Each is refused now, stop-arm
    only (`_rebuild_stores_the_vector`, `_property_alias_names`), and the mention-shaped
    controls at the end keep the rule from swallowing a read: a plain literal for reporting
    is not a merge, and a constant value places nothing.
    """
    refused = (
        # the arm places the vector, THEN leaves
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        props["embedding"] = _encode(ev)
        return None
    props["embedding"] = _encode(ev)
    return _write(ev)
""",
        # …the local-name spelling of the same store, with a `raise` exit
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        embedding = _encode(ev)
        raise _Skip()
    props["embedding"] = embedding
""",
        # …a dict writer's keyword store, which the mirror arm already accepts as a store
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(embedding=_encode(ev))
        return None
    _write(props)
""",
        # …removes the property AND re-places it: the removal arm alone would certify
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.pop("embedding", None)
        props["embedding"] = _encode(ev)
        return None
    _write(props)
""",
        # …places it in the arm's own `finally`, which the exit walk descends into
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        try:
            return None
        finally:
            props["embedding"] = _encode(ev)
    props["embedding"] = _encode(ev)
""",
        # …places it behind a condition, so no *unconditional* store is visible in the arm
        """
def _upsert_object(ev, flag):
    if not _derived_mirror_required("object"):
        if flag:
            props["embedding"] = _encode(ev)
        return None
    props["embedding"] = _encode(ev)
""",
        # …places it through a dict MUTATOR rather than an assignment: the operator form
        # (`props |= {…}`) was already a store, so its methods must be too. Measured
        # fail-OPEN at `add2e7b89` — each of these five certified the writer.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__("embedding", _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.setdefault("embedding", _encode(ev))
        return None
    _write(props)
""",
        # the unbound spelling: one positional argument more than the bound form
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        dict.__setitem__(props, "embedding", _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__ior__({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        setattr(props, "embedding", _encode(ev))
        return None
    _write(props)
""",
        # ⛔ the FUNCTIONAL `operator` spellings of the same stores, measured fail-OPEN at
        # `8637df0f`: `setitem` is what `props["embedding"] = vec` compiles to, `ior` what
        # `props |= {…}` does. The bare `setitem` is the `from operator import setitem` form.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        operator.setitem(props, "embedding", _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        setitem(props, "embedding", _encode(ev))
        return None
    _write(props)
""",
        # ⛔ …and the three ways a mapping is REBUILT rather than mutated. Measured
        # fail-OPEN at `98eec2ca`: each certified a stop arm that placed the vector.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {**props, "embedding": _encode(ev)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        operator.ior(props, {"embedding": _encode(ev)})
        return None
    _write(props)
""",
        # ⛔ …and the CONSTRUCTOR spelling of the mapping the literal forms already refuse,
        # in its bare and its `*`-spread form — the spread one is what pins the constructor
        # rule for an operand nested inside a container (`_rebuild_stores_the_vector` is the
        # single recogniser: `dict(...)` there delegates to
        # `_mapping_argument_stores_the_vector`).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(dict(embedding=_encode(ev)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(*[dict(embedding=_encode(ev))])
        return None
    _write(props)
""",
        # ⛔ …and `operator.or_`, the NON-IN-PLACE functional `|`: the same store as
        # `props = props | {"embedding": vec}`, through the one synonym the include list did
        # not name — measured CERTIFIED GATED by the first review of this commit. This row is
        # the DISCRIMINATING one (delete `"or_"` and it alone reddens); the `dict(…)` operand
        # beside it is refused by the constructor arm either way, so it is a spelling control,
        # not a second pin — stated because the earlier comment claimed two pins and only one
        # was, measured by the review of this branch.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = operator.or_(props, {"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = operator.or_(props, dict(embedding=_encode(ev)))
        return None
    _write(props)
""",
        # ⛔ …and the ITERABLE-OF-PAIRS spelling of the `|=` merge, which `dict.__ior__`
        # accepts exactly as it accepts the literal: `props |= [("embedding", vec)]`. The
        # AugAssign arm narrowed to `ast.Dict` and walked past it — measured CERTIFIED by the
        # review of this branch while the CALL form `props.update([("embedding", vec)])` was
        # already refused. Both narrowings of the pairs rule still hold, which is what the two
        # `accepted` controls below assert.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [("embedding", _encode(ev))]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= (("embedding", _encode(ev)),)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [("embedding_model", _m()), ("embedding", _encode(ev))]
        return None
    _write(props)
""",
        # ⛔ …and one TRANSPARENT WRAPPER in, which is where the pairs rule was leaking:
        # `.copy()`, `list(…)`, `tuple(…)`, a whole-list slice and an `if`-expression around
        # the same literal all place the vector and all were CERTIFIED (measured by the review
        # of this branch) — one `.copy()` re-opened the hole the plain spelling had closed.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [("embedding", _encode(ev))].copy()
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= list([("embedding", _encode(ev))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= tuple([("embedding", _encode(ev))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [("embedding", _encode(ev))][:]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [("embedding", _encode(ev))] if _flag(ev) else []
        return None
    _write(props)
""",
        # ⛔ …and the same sequence BUILT OR WRAPPED, each of which was CERTIFIED while the
        # bare literal was refused: concatenation and repetition (`+ []`, `* 1`), a `Starred`
        # element splicing it in, a comprehension over it, and a MAPPING CONSTRUCTOR
        # (`OrderedDict` is a dict subclass, so `|=` merges it exactly as it merges a dict).
        # Also the pairs rule ONE ARM OVER: `update(...)` shares
        # `_mapping_operand_stores_the_vector`, which now ends in the same unwrapper — without
        # that, `update([...].copy())` was CERTIFIED while `update([...])` was refused.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [("embedding", _encode(ev))] + []
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [("embedding", _encode(ev))] * 1
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= (*[("embedding", _encode(ev))],)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [x for x in [("embedding", _encode(ev))]]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict([("embedding", _encode(ev))])
        return None
    _write(props)
""",
        # ⛔ …AND THE CONSTRUCTOR'S **MAPPING** OPERAND IS THE SAME MERGE (PEP 584: `d |= x`
        # "behaves like `dict.update()`", taking "mapping-like objects … or iterables of
        # key-value pairs"). The constructor arm asked only the PAIRS rule — which has no
        # `ast.Dict` arm by design — so every mapping spelling below was CERTIFIED while
        # `[(…)]` was refused (measured at the review of this branch): the halves of one
        # rule, one operand form apart. `Counter`/`ChainMap`/`defaultdict` are named because
        # they are merge sources too (measured: `|=` and `update()` merge all of them), and a
        # name-suffix test for `*Dict` alone cannot reach them.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(_collections.OrderedDict({"embedding": _encode(ev)}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.Counter({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.defaultdict(dict, {"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.ChainMap({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        # ⛔ …AND THE SAME CONSTRUCTOR ONE SPELLING OVER IS THE SAME STORE: REBUILT rather
        # than merged. `_rebuild_stores_the_vector` named `dict` and only `dict`, so
        # `props = OrderedDict({"embedding": vec})` was CERTIFIED while
        # `props = dict({"embedding": vec})` was refused — one name list, two spellings of
        # it (measured). All four are the same rebuild the writer is then handed.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = _collections.OrderedDict({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = _collections.Counter({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = _collections.defaultdict(dict, {"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = _collections.OrderedDict([("embedding", _encode(ev))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([("embedding", _encode(ev))].copy())
        return None
    _write(props)
""",
        # ⛔ …AND THE MAPPING OPERAND BEHIND THE SAME WRAPPER (`#13788` F1, measured at
        # `10184e6d`). The unwrapper above was written in the PAIRS rule only, so the one-line
        # wrapper `.copy()` — whose pairs spelling the row above refuses — CERTIFIED the
        # mapping spelling of the identical store:
        # `props.update({"embedding": vec}.copy())` placed the vector on the stop arm while
        # `props.update([("embedding", vec)].copy())` was refused. Both forms are operands of
        # the same merge (PEP 584), so a transparent wrapper must not be able to answer one and
        # not the other; every wrapper arm now asks `_operand_stores_the_vector`. The last row
        # is the `**` keyword, whose operand was answered by the mapping-only rule and so
        # missed the wrapper entirely.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({"embedding": _encode(ev)}.copy())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"embedding": _encode(ev)}.copy()
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict({"embedding": _encode(ev)}.copy())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict({"embedding": _encode(ev)}.copy())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(**{"embedding": _encode(ev)}.copy())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([("embedding", _encode(ev))] if _flag(ev) else [])
        return None
    _write(props)
""",
        # ⛔ THE CONSTRUCTOR OPERAND'S `**` MERGE IS THE SAME STORE (`#13788` F1, measured at
        # `10184e6d`). `_constructor_stores_the_vector` asked
        # `_mapping_argument_stores_the_vector` per argument, and THAT helper's dict-literal
        # arm omits `stop_arm` — so the `{**{…}}` recursion which already refuses
        # `props |= {**{"embedding": vec}}` was INERT inside a constructor operand. Every row
        # below CERTIFIED a stop arm that stores, while the identical bare merge was refused.
        # The operand rule (`_mapping_operand_stores_the_vector`) also carries the pairs tail,
        # so it answers both operand forms and the ARGUMENT rule is not asked for an operand
        # at all.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict({**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = _collections.OrderedDict({**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(_collections.OrderedDict({**{"embedding": _encode(ev)}}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.defaultdict(dict, {**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.Counter({**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.ChainMap({**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        # …and ONE NESTING IN is the same operand: a constructor handed a constructor.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict(_collections.OrderedDict({**{"embedding": _encode(ev)}}))
        return None
    _write(props)
""",
        # …and the REBUILD position of the `.copy()` wrapper the rows above cover as an
        # operand: `isinstance(value, ast.Dict)` saw only the bare literal there, so
        # `props = {"embedding": vec}` was refused beside its `.copy()` CERTIFIED.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {"embedding": _encode(ev)}.copy()
        return None
    _write(props)
""",
        # ⛔ …and the two halves the constructor arm did not reach: its operand may be a
        # MAPPING LITERAL rather than a pairs sequence, and the constructor family is not
        # "suffix `Dict`" — `defaultdict`/`ChainMap`/`Counter` are mappings whose names say
        # nothing, and the mapping operand need be neither first nor alone. Each of these was
        # CERTIFIED while the bare literal was refused (measured by the review of this branch,
        # which also runtime-checked that they merge).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.defaultdict(list, [("embedding", _encode(ev))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict([("embedding", _encode(ev))], extra=1)
        return None
    _write(props)
""",
        # (the `OrderedDict({…})` and `ChainMap({…})` rows this block used to repeat are the
        # ones above it — the same spelling twice pinned nothing the first row did not, and a
        # duplicate reads as a second distinct case. Dropped, not reordered.)
        # ⛔ …and a ``**`` MAPPING KEYWORD on a constructor, which merges exactly as
        # `dict(**{"embedding": vec})` does. This row is answered by the MUTATOR arm on the
        # nested `dict(...)` call, not by the constructor scan (whose keyword half was
        # therefore removed as inert — the mutant survived until this row was checked against
        # the other arm); it stays because the spelling must keep its refusal.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(**{"embedding": _encode(ev)})
        return None
    _write(props)
""",
        # ⛔ …and the DICT COMPREHENSION, which is a comprehension whose fields are
        # `key`/`value` rather than `elt` — omitting it left
        # `{k: v for k, v in [("embedding", vec)]}` CERTIFIED, and adding the type to the
        # `elt`-shaped tuple instead of its own branch raises rather than refuses.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: v for k, v in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        # ⛔ …and the DUNDER ALIAS of the same function (`operator.__or__ is operator.or_`),
        # in all three spellings the review of this branch measured CERTIFIED: the functional,
        # the METHOD form on the mapping, and the unbound `dict.__or__(props, …)`. A tuple that
        # names `__ior__` beside `ior` and only `or_` for the non-in-place operator is half a
        # vocabulary, and half a vocabulary is a false PASS.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = operator.__or__(props, {"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = props.__or__({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = dict.__or__(props, {"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = operator.__or__(props, {**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = props | {"embedding": _encode(ev)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(embedding=_encode(ev))
        return None
    _write(props)
""",
        # ⛔ the SPLATTED constructor: the keyword mapping is not positionally visible.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(**{"embedding": _encode(ev)})
        return None
    _write(props)
""",
        # …the writer is handed the rebuilt mapping instead of assigning it
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _write({**props, "embedding": _encode(ev)})
        return None
    _write(props)
""",
        # …the KEYWORD spelling of the same mutator
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.setdefault(key="embedding", default=_encode(ev))
        return None
    _write(props)
""",
        # …and a subscript keyed by a LOCAL bound to the property string
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        key = "embedding"
        props[key] = _encode(ev)
        return None
    _write(props)
""",
        # ⛔ #13647 round 12 — the store vocabulary this line did not carry:
        # an unpacked/chained/Starred/mapping TARGET, a mapping mutator's
        # functional + constructor spellings, and the `for`/`with`/walrus
        # binding arms. Every row below certified a stop arm that placed
        # the vector (or, in `accepted`, was falsely refused by an earlier
        # version of the same fix).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props["embedding"], props["embedding_model"] = _encode(ev), _m()
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        embedding, _identity = _encode(ev), _m()
        return None
    props["embedding"] = embedding
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        x = props["embedding"] = _encode(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        [props["embedding"], _m] = [_encode(ev), 1]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        for props["embedding"] in _encode(ev):
            _log("placed")
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict([("embedding", _encode(ev))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {**{"embedding": _encode(ev)}}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(**{"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        (props["embedding"], _m), _h = (_encode(ev), 1), 2
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        *props["embedding"], = _vecs(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props["embedding"], = (_encode(ev),)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {"embedding": _encode(ev)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, "embedding", _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, "embedding", value=_encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, key="embedding", value=_encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, embedding=_encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(**{**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(**{**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__ior__({**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        if (embedding := _encode(ev)):
            _log(embedding)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _xs = [_m() for props["embedding"] in _vecs(ev)]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        with _ctx() as props["embedding"]:
            _log("bound")
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        with _ctx() as (props["embedding"], _m):
            _log("bound")
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        with _ctx() as (*props["embedding"], _m):
            _log("bound")
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        with _ctx() as ((props["embedding"], _q), _m):
            _log("bound")
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        with _ctx() as [props["embedding"], _m]:
            _log("bound")
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        with _ctx() as [(props["embedding"], _q), _m]:
            _log("bound")
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(*("embedding", _encode(ev)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(*("embedding", _encode(ev), "extra"))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, *["embedding", _encode(ev)])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, *[{"embedding": _encode(ev)}])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__("embedding", *[])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **{"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        dict.setdefault(props, "embedding", _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(*[{"embedding": _encode(ev)}])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **{"key": "embedding", "value": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, "embedding", **{"value": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(key="embedding", value=_encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(*["embedding", _encode(ev)])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.setdefault(*["embedding", _encode(ev)])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        dict.setdefault(props, "embedding", _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **{**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
async def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        async for props["embedding"] in _vecs(ev):
            pass
        return None
    _write(props)
""",
        """
async def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        async with _ctx() as props["embedding"]:
            _log("bound")
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update((("embedding", _encode(ev)),))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict([["embedding", _encode(ev)]])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props["embedding"], _x = (None,)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props["embedding"], props["embedding_model"] = _encode(ev), _m()
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        x = props["embedding"] = _encode(ev)
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props.update(**{"embedding": _encode(ev)})
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props |= dict(embedding=_encode(ev))
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props.__setitem__("embedding", _encode(ev))
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props |= {**{"embedding": _encode(ev)}}
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props.update({**{"embedding": _encode(ev)}})
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        for props["embedding"] in _encode(ev):
            _log("x")
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props = {"embedding": _encode(ev)}
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        _set(props, "embedding", _encode(ev))
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        if (embedding := _encode(ev)):
            _log(embedding)
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        _xs = [_m() for props["embedding"] in _vecs(ev)]
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        with _ctx() as props["embedding"]:
            _log("bound")
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        with _ctx() as (props["embedding"], _m):
            _log("bound")
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        _set(props, **{"embedding": _encode(ev)})
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        operator.ior(props, {"embedding": _encode(ev)})
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props |= {"embedding_model": _m()}
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props.update({"embedding_model": _m()})
    _write(props)
""",
    )
    for source in refused:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a STOP ARM THAT STORES certified the writer: {source!r} ({why})"

    # ⛔ AND THE HONEST STOP ARMS MUST STILL CERTIFY, or the rule is an inert enforcer: a
    # removal, a bare exit, and a non-store statement before the exit are all write-stops.
    accepted = (
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.pop("embedding", None)
        return None
    _write(props)
""",
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        return None
    props["embedding"] = _encode(ev)
""",
        """
def _upsert_object(ev):
    if not _derived_mirror_required("object"):
        _log("skipping the graph copy", ev)
        return None
    props["embedding"] = _encode(ev)
""",
        # the real `#13617` shape: a POSITIVE ask whose arm stores (the mirror arm)
        """
def _upsert_object(ev):
    embedding = None
    if _derived_mirror_required("object"):
        embedding = _encode(ev)
    props["embedding"] = embedding
""",
        # ⛔ A READ IS NOT A STORE, and the mutator vocabulary must not swallow one: an
        # arm that only reads the property and then removes it is an honest write-stop.
        """
def _upsert_object(props):
    if not _derived_mirror_required("object"):
        _audit(props.get("embedding"), props.setdefault("embedding", None))
        props.pop("embedding", None)
        return None
    _write(props)
""",
        # ⛔ AND THE REBUILD VOCABULARY MUST NOT SWALLOW A MENTION EITHER: a plain literal
        # carrying the property for REPORTING is not a merge, and a constant is not a store.
        """
def _upsert_object(props):
    if not _derived_mirror_required("object"):
        _audit({"embedding": props.get("embedding")})
        props.pop("embedding", None)
        return None
    _write(props)
""",
        """
def _upsert_object(props):
    if not _derived_mirror_required("object"):
        props.setdefault(key="embedding", default=None)
        return None
    _write(props)
""",
        # ⛔ #13647 round 12 — the store vocabulary this line did not carry:
        # an unpacked/chained/Starred/mapping TARGET, a mapping mutator's
        # functional + constructor spellings, and the `for`/`with`/walrus
        # binding arms. Every row below certified a stop arm that placed
        # the vector (or, in `accepted`, was falsely refused by an earlier
        # version of the same fix).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props["embedding"], props["embedding_model"] = None, None
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _read(props, "embedding", None)
        props.pop("embedding", None)
        return None
    _write(props)
""",
        # ⛔ AND A REMOVAL WITH A SENTINEL DEFAULT IS STILL THE WRITE-STOP, not a store: the
        # third-argument rule (written for `_set(props, "embedding", vec)`) reads
        # `pop("embedding", _MISSING)` as `(receiver, PROPERTY, non-constant)` and refused it —
        # measured by the first review of this commit, which found the parent CERTIFIED it.
        # `pop` is separated in `_mutator_stores_the_vector` for exactly this shape.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.pop("embedding", _MISSING)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        dict.pop(props, "embedding", _MISSING)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _read(props, key="embedding", default=None)
        props.pop("embedding", None)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        with _ctx() as cur:
            _log(cur)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _xs = [_m() for _i in _vecs(ev)]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _a, _b = 1, 2
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict([("embedding_model", _m())])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict([("embedding", None)])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [("embedding", None)]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [("embedding_model", _m())]
        return None
    _write(props)
""",
        # ⛔ AND A CONTAINER OF MAPPINGS IS NOT AN ITERABLE OF PAIRS: `dict.__ior__` rejects
        # `[{"embedding": vec}]` with a `ValueError` (element 0 has length 1, 2 required), so
        # this is not a store — and the `update` arm's recursive container rule, borrowed here,
        # refused it. That is a refusal the branch never had, on a statement Python rejects
        # outright; it stays CERTIFIED so the vocabulary says what the language says.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [{"embedding": _encode(ev)}]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev, **kwargs):
    if not _derived_mirror_required("object"):
        props.update(**kwargs)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev, **kwargs):
    if not _derived_mirror_required("object"):
        _set(props, **kwargs)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props["embedding"]: str
        props.pop("embedding", None)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict([("embedding",)])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"meta": {"embedding": _encode(ev)}}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(embedding=None)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__("embedding_model", _m())
        props.pop("embedding", None)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        dict.setdefault(props, "embedding_model", _m())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(embedding=None)
        props.pop("embedding", None)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(*_pair(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, *_pair(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props["embedding"], = (None,)
        return None
    _write(props)
""",
        # ⛔ AND A CONSTRUCTOR OPERAND THAT PLACES NOTHING IS STILL A MENTION: the mapping
        # rule's two narrowings (EXACT property, NON-CONSTANT value) travel with it into the
        # constructor arm, so `OrderedDict({"embedding": None})` — the pre-guard reset — and
        # the identity triple stay honest write-stops. Without these the widened arm would be
        # an inert-by-over-refusal rule that costs a writer its gate.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict({"embedding": None})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.Counter({"embedding_model": _m()})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.defaultdict(dict, {"embedding": None})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = _collections.OrderedDict({"embedding_model": _m()})
        return None
    _write(props)
""",
        # ⛔ THE IDENTITY CONTROL FOR THE WRAPPER ARM (`#13788` F1): a `.copy()` operand that
        # {"embedding_model": …} sets only the IDENTITY triple, so it must still CERTIFY —
        # otherwise the new refusal costs an honest writer its gate.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({"embedding_model": _m()}.copy())
        return None
    _write(props)
""",
        # ⛔ THE HONEST-STOP CONTROLS FOR THE CONSTRUCTOR `**` MERGE (`#13788` F1): the new
        # refusals are NARROWED to an EXACT property name and a NON-CONSTANT value, so the
        # pre-guard reset (`None`) and the identity triple must still CERTIFY — otherwise the
        # added refusal costs an honest writer its gate. The last row pins the same narrowing
        # one nesting in, so the recursion arm is not an over-broad `return True`.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict({**{"embedding": None}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = _collections.ChainMap({**{"embedding_model": _m()}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict(_collections.OrderedDict({**{"embedding_model": _m()}}))
        return None
    _write(props)
""",
    )
    for source in accepted:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is True, f"an honest write-stop was refused: {source!r} ({why})"


def test_the_stop_arm_of_a_POSITIVE_ask_must_not_store_either(tmp_path, monkeypatch):
    """⛔ ON A POSITIVE ASK THE *ELSE* IS THE STOP ARM (`#13647`, measured at `f9d0549e80`).

    `mirror_required(l)` answers True = "the graph must still store this", so under
    `if gate(l): …` the BODY is the mirror arm and `orelse` is the arm that runs when the
    graph must STOP. Every polarity arm read `sub.body` only — the mirror arm demanded a
    store there, and the negated arms guarded their own body — so a store on the positive
    ask's ELSE was never examined by anything:

        if _derived_mirror_required("object"):
            props["embedding"] = _encode(ev)
        else:
            props["embedding"] = _encode(ev)     # stores exactly when told to stop
            return None

    Measured fail-OPEN at `f9d0549e80` through `_writer_consults_gate`: **GATED** — a writer
    that stores precisely on the stop arm lifted `REFUSE_WRITE_STILL_EMITS`, the same defect
    `622e9935` closed for the `if not gate(l):` spelling one arm over. The walk reads
    `orelse` with `anywhere=True` for the same reason the negated stop arm does: an
    unplaceable store on a stop arm still places the vector, so refusing is the fail-closed
    answer (a `try`'s `orelse` is unrelated — `_reachable_ifs` yields `If` nodes only).

    Mutation: delete the `sub.orelse` arm in `_asks_the_gate` and every refused shape below
    answers `True` again; the accepted controls stay `True`, which keeps the rule from
    becoming an inert enforcer that refuses the honest `else` (a removal, an audit line).
    """
    refused = (
        # the else places the vector, THEN exits
        """
def _upsert_object(ev):
    props = {}
    if _derived_mirror_required("object"):
        props["embedding"] = _encode(ev)
    else:
        props["embedding"] = _encode(ev)
        return None
    return _write(props)
""",
        # …the keyword-mutator spelling of the same store
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props["embedding"] = _encode(ev)
    else:
        props.update(embedding=_encode(ev))
        return None
    _write(props)
""",
        # …behind a live condition inside the else (anywhere=True, not unconditional)
        """
def _upsert_object(ev):
    props = {}
    if _derived_mirror_required("object"):
        props["embedding"] = _encode(ev)
    else:
        if ev.get("force"):
            props["embedding"] = _encode(ev)
        return None
    return _write(props)
""",
        # …in the else arm's own `finally`, which the exit walk descends into
        """
def _upsert_object(ev):
    props = {}
    if _derived_mirror_required("object"):
        props["embedding"] = _encode(ev)
    else:
        try:
            _log("stopping", ev)
        finally:
            props["embedding"] = _encode(ev)
    return _write(props)
""",
        # …removes then RE-PLACES it: the removal alone would look like an honest stop
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props["embedding"] = _encode(ev)
    else:
        props.pop("embedding", None)
        props["embedding"] = _encode(ev)
    _write(props)
""",
    )
    for source in refused:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, (
            f"the STOP ARM of a POSITIVE ask stored and still certified: {source!r} ({why})")

    # ⛔ AND THE HONEST POSITIVE FORMS MUST STILL CERTIFY, or the new refusal is an inert
    # enforcer that shuts the gate on the very shape `#13617` ships.
    accepted = (
        # the real `#13617` shape: an encode guard with no else at all
        """
def _upsert_object(ev):
    embedding = None
    if _derived_mirror_required("object"):
        embedding = _encode(ev)
    props["embedding"] = embedding
""",
        # an else that REMOVES is exactly the write-stop
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props["embedding"] = _encode(ev)
    else:
        props.pop("embedding", None)
    _write(props)
""",
        # …and an else that only READS/LOGS places nothing, so it is not a store
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props["embedding"] = _encode(ev)
    else:
        _audit(props.get("embedding"))
        _log("skipping the graph copy", ev)
    _write(props)
""",
    )
    for source in accepted:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is True, f"an honest positive-ask write-stop was refused: {source!r} ({why})"


def test_the_mutator_vocabulary_does_not_widen_the_mirror_arm(tmp_path, monkeypatch):
    """⛔ A FIX MAY ADD REFUSALS, NEVER ACCEPTANCES (`#13647`, at `8637df0f`).

    The functional/constructor store recognition added for the STOP arm is consulted only
    with `anywhere=True`. If it leaked into the mirror arm (`if gate(l): …`), the destructive
    gate would ACCEPT writers it refused before — the opposite of what a repair may do: a
    false refusal costs a refactor, a false acceptance deletes the only readable copy of a
    fingerprint. So the LITERAL forms must keep certifying the mirror arm…
    """
    literal = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props |= {"embedding": _encode(ev)}
    props["embedding"] = _encode(ev)
""")
    gated, why = strip._writer_consults_gate(literal, "_upsert_object", ("object",))
    assert gated is True, f"the literal merge arm stopped certifying: {why}"

    # …while the CONSTRUCTOR spelling of the same mapping must NOT, and neither may the
    # constructor form of `update` — the widening the stop-arm fix would cause if it were
    # consulted on both arms.
    for source in (
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props |= dict(embedding=_encode(ev))
    props["embedding"] = _encode(ev)
""",
        """
def _upsert_object(props, ev):
    if _derived_mirror_required("object"):
        props.update(dict(embedding=_encode(ev)))
    props["embedding"] = _encode(ev)
""",
    ):
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, (
            f"a CONSTRUCTOR store widened what the mirror arm accepts: {source!r} ({why})")


def test_the_digest_does_not_dedupe_two_typed_identities_by_VALUE():
    """⛔ `set(entries)` COMPARED `(Object, 1)` AND `(Object, 1.0)` EQUAL (`#13647` 3).

    `1 == 1.0` and they hash alike, so one of two genuinely different identities was
    silently dropped from the digest — and WHICH survived depended on insertion order, so
    the same set could digest two ways and a stale-plan check could pass over drift. The
    dedupe is now on the TYPE-TAGGED frame.
    """
    one = strip.digest([("Object", 1)])
    both = strip.digest([("Object", 1), ("Object", 1.0)])
    assert one != both, "an int and a float identity collided into one digest row"
    assert both == strip.digest([("Object", 1.0), ("Object", 1)]), (
        "the digest depends on insertion order for typed keys")


def test_an_all_string_plan_still_re_derives_to_the_same_bytes():
    """The typed-key frame is ADDITIVE: a string-only set must order and frame as before.

    The sort key is still the raw value (`identity_sort_key`), not the frame — sorting by
    `"2:aa" < "1:b"` would re-digest every plan minted against an all-string population,
    which is the compatibility promise `_digest_key` makes.
    """
    body = "\n".join(["6:Object2:aa", "6:Object1:b"])
    expected = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
    assert strip.digest([("Object", "b"), ("Object", "aa")]) == expected


def test_a_plan_that_names_the_same_node_twice_is_refused(tmp_path):
    """⛔ THE DUPLICATE-LABEL DEFECT ONE LEVEL DOWN (`#13647` 4).

    The digest describes a distinct SET while `expected` counts ENTRIES, so a plan listing
    the same identity twice verified and then claimed more nodes than the set it was
    approved for — and the removal would strip the same key twice.
    """
    plan = {
        "tool": strip.PLAN_TOOL, "version": strip.PLAN_VERSION,
        "labels": [{"label": "Object", "keys": ["obj-1", "obj-1"]}],
        "total_nodes": 2,
    }
    plan["digest"] = strip.digest(strip.plan_entries(plan))
    path = tmp_path / "duplicated-node.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(strip.Refusal) as excinfo:
        strip._load_plan(str(path), plan["digest"])
    assert excinfo.value.token == strip.REFUSE_PLAN_UNREADABLE
    assert "same node more than once" in str(excinfo.value)


def test_a_live_census_without_its_declaration_cannot_be_narrowed_by_the_PLAN(
        stores, monkeypatch):
    """⛔ THE `declared_population` FALLBACK WAS A HAND-EDITABLE ACKNOWLEDGEMENT (5).

    `served = set(live[...]) or set(plan[...])` re-admitted the field the comment beside it
    forbids: on a live-census shape WITHOUT the key, an edited plan declared the narrow
    scope it wanted and the run published exit 0 over three untouched labels. The fallback
    is now the module's own `RETIRED` constant, which no plan can edit.
    """
    _ready(monkeypatch)
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    plan = _plan(graph, labels=["Object"])
    plan["declared_population"] = ["Object"]      # the edit no digest covers
    real_census = parity.census

    def _census_without_the_declaration(*args, **kwargs):
        report = real_census(*args, **kwargs)
        report.pop("declared_population", None)
        return report

    monkeypatch.setattr(strip.parity, "census", _census_without_the_declaration)
    result = _execute(graph, plan)
    assert result["unmeasured_by_label"] == ["Event", "Source", "Subject"]
    assert strip.result_exit_code(result) == 5, (
        "a narrowed sweep published exit 0 from the plan's own declaration")


def test_the_compatibility_break_is_disclosed_where_the_operator_reads_it():
    """A plan OUTLIVES the build that printed it, so the break is stated in the tool (7).

    The PR body is not an artefact an operator has: this module's docstring is.
    """
    text = strip.__doc__ or ""
    assert "COMPATIBILITY" in text
    for phrase in ("REFUSE_DIGEST_MISMATCH", "--expect-narrowed", "``6``"):
        assert phrase in text, f"the compatibility note omits {phrase!r}"
