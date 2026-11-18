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
| `test_write_gate_is_fail_closed_when_the_symbol_is_absent` / `…raises` | fail-closed in every unknown | an unreadable gate reads as permission |
| `test_label_write_gate_requires_every_entity_type_addressing_the_label` | `:Source` serves TWO types | a strip lands on the document half while `source` still writes |
| `test_execute_strips_the_planned_set_and_reports_the_count` | the batched `REMOVE` | a partial strip reports a complete one |
| `test_execute_refuses_an_empty_plan_unless_expect_empty` | "nothing to strip" is a refusal | an empty plan reports a finished retirement |
| `test_execute_refuses_target_drift` | a plan is an authority over ONE graph | it is applied to a different graph of the same shape |
| `test_the_refusal_set_is_closed` | the token set is closed and published | a refusal is raised with an unpublished token |
| `test_a_tools_only_diff_selects_this_pin` | these falsifiers actually RUN in CI | `tools/` stays a flat non-python prefix and this file is never selected |
"""

from __future__ import annotations

import json
import os
import re
import sys
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

    def reader(self, record_dsn, org_id, graph_id, table):
        rows = self.rows[table]
        return {
            "keys": {k for k, (readable, _p) in rows.items() if readable},
            "unreadable": sum(1 for r, p in rows.values() if p and not r),
            "rows": len(rows),
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
        graph_name="g", graph_uri="docker://x", labels=labels, batch=batch)


def _execute(graph, plan, **over):
    kwargs = {
        "g": graph, "plan": plan, "record_dsn": "postgresql://x", "org_id": "org",
        "graph_id": "graph", "graph_name": "g", "confirmed": plan["digest"],
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
    assert re.search(r"Object\s+1\s+1\s+0\s+0\s+0\s+0\s+PARITY", text)


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
        "count": 2, "keys": ["obj-1", "obj-2"], "unkeyed": 0}

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


def test_write_gate_reports_a_stopped_writer(monkeypatch):
    from tortoise import record_vector

    monkeypatch.setattr(record_vector, "mirror_required",
                        lambda entity_type: False, raising=False)
    emits, reason = strip.write_gate("object")
    assert emits is False
    assert "no longer writes" in reason


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


def test_execute_refuses_a_plan_edited_after_it_was_printed(tmp_path):
    """The plan's OWN digest is checked, not only the caller's claim."""
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


# ── the refusals are a closed, published set ────────────────────────────────


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
