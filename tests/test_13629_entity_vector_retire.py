"""`#13629` — the graph-side entity-vector retirement: its census and its stripper.

Two instruments are under test, and each test below is a falsifier for ONE of their
load-bearing claims — the shape `tests/test_dense_leg_probe_13108.py` established, and
for the same reason: an instrument whose own refusals are unpinned is an instrument
that fails OPEN the day someone deletes a branch.

| test | claim it falsifies | it goes RED when … |
|---|---|---|
| `test_the_retired_population_is_every_served_label_including_point` | the population is EVERY served label (five, `:Point` included since `#13971`) | a label leaves the set a destructive tool iterates |
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
| `test_the_fourth_legs_container_arm_asks_the_whole_family` | the fourth stop-arm leg asks the WHOLE `_CONTAINER_CALLS` family (pair and pair-WITH-EXTRAS), and the redundancy `_CONTAINER_CALLS_ANSWERED_UPSTREAM` records is a MEASUREMENT, never a licence to subtract | a local name tuple or a subtraction returns (silent: two family names answer, four skip), or **leg 3** stops answering a wrapper the record says it reads — leg 1 is a DUPLICATE at this seam (measured: 0 rows answer leg 1 and not leg 3), so no mutant that REMOVES or WEAKENS leg 1 can redden this test (strengthening it to answer a sole-decider row does, at `assert upstream is False`) and it is disclosed, not named as a guard |
| `test_the_stop_arm_of_a_POSITIVE_ask_must_not_store_either` | the `else` of `if gate(l):` is the STOP arm, and it is read | a store on that arm (`orelse` never examined) certifies the writer as write-stopped |
| `test_upsert_source_IS_a_Source_writer_through_the_PASSTHROUGH` | `_upsert_source` persists a caller `embedding` via `SET n += $extra` | the name is dropped from `:Source`'s population on a static-clause count, letting a strip run into a live writer |
| `test_the_gate_is_measured_from_a_fresh_process_when_the_population_is_supplied` | **#13796** — the gate is MEASURABLE from a fresh process | the stripper asks without its own denominator/tenant, so every label answers *unproved* while parity holds |
| `test_a_short_record_refuses_the_label_with_BOTH_counts_in_the_reason` | "proved short" is a DIFFERENT answer from "not proved" | a measured deficit is reported as an unmeasured one, sending the operator back to a census already run |
| `test_the_plan_is_STRIP_READY_from_a_fresh_process_when_the_record_covers` | coverage clears a plan in a fresh process, and the READY reason is the measurement | `strip_ready: True` without the two counts beside it — an assertion nobody can check |
| `test_the_plan_refuses_a_SHORT_record_for_the_MEASURED_reason` | **#13796** acceptance (b)+(c) — the refusal is the MEASUREMENT | a short record certifies a strip, or refuses with "not proved" when the counts were taken |
| `test_the_destructive_arm_hands_the_gate_the_runs_own_denominator` | **#13796**'s reachability on the `--execute` arm — the second of `main`'s two hops | the destructive path drops the population while the preview keeps it, so `execute` re-checks an unsatisfiable gate (measured: 107 passed under that mutant) |
| `test_the_census_write_gate_degrades_to_the_unmeasured_fallback_on_an_old_stripper` | `_write_gate`'s version-skew arm answers with the FAIL-CLOSED twin, never with a measurement it could not take | an old stripper is answered by a probe that reports `ready` with no counts behind it (measured: the mutant survived at 112 passed until the writers and the tenant door were both held open) |
| `test_execute_rechecks_the_gate_with_the_runs_own_measurement_context` | **#13796**'s THIRD hop — `execute`'s OWN re-check probe, built seconds before the first delete | the destructive re-check loses the denominator and refuses `REFUSE_WRITE_STILL_EMITS` on a covered graph (measured: 112 passed under that mutant before this test) |
| `test_an_uncountable_projection_yields_NO_supplier_rather_than_a_zero` | `graph_vector_population` answers `None`, never `0`, when the projection cannot count | a "supply a default" `0` VERIFIES coverage on a measurement never taken — the fail-OPEN direction (measured: 143 passed under that mutant) |
| `test_a_HALF_tenant_declaration_is_refused_not_downgraded_to_the_ambient_one` | a half declared tenant is REFUSED by `_declared_scope`, not collapsed to the ambient tenant | a `("org-A", "")` declaration is answered from the ambient tenant's record, certifying one tenant's strip from another's measurement |
| `test_a_tools_only_diff_selects_this_pin` | these falsifiers actually RUN in CI | `tools/` stays a flat non-python prefix and this file is never selected |
"""

from __future__ import annotations

import ast
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

#: The declared population, in the tool's own order — **derived, never re-spelled**. A
#: label entering `RETIRED` (`#13971` added `:Point`) must not mean hunting hard-coded
#: label lists through this file: an assertion that outlives its population is either
#: edited in a hurry or silently weakened, and both are worse than deriving it. The
#: assertions below say what they MEAN ("every declared label except the one planned")
#: and this list answers.
DECLARED_LABELS = [label for _t, label, _k in parity.RETIRED]
DECLARED_EXCEPT_OBJECT = sorted(lbl for lbl in DECLARED_LABELS if lbl != "Object")


def _census_strip():
    """The stripper module object the CENSUS imports — NOT the same object as `strip`.

    `tools.strip_graph_entity_vectors` and the top-level `strip_graph_entity_vectors` that
    `entity_vector_parity_census._write_gate` resolves are two module objects around one
    file (the tool puts its own directory on `sys.path`). A monkeypatch applied to `strip`
    is therefore INVISIBLE to the census's own call — measured: patching `strip`'s
    `_fold_consults_gate` left the census running the real writer check and refusing
    `:Object` on two ungated writers. A test that patched only the package-qualified
    module would read as green while asserting nothing about the census CLI.
    """
    import strip_graph_entity_vectors

    return strip_graph_entity_vectors


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
    monkeypatch.setattr(strip, "write_gate",
                        lambda entity_type, **_: (False, "write-stopped"))


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


def test_the_retired_population_is_every_served_label_including_point():
    """`#13971`'s population, and the reversal it records, in one place.

    `:Point` was OUT of this set under `#13629` (`NOT_IN_SCOPE == ("Point",)`), because
    the armed cutover and `#13330` owned it while its record coverage was unproven and
    every writer still mirrored it unconditionally. `#13971` removes the MEASUREMENT
    condition — coverage for `point`/`operator` is now taken from a fresh process — and
    the exclusion is therefore no longer asserted; what IS asserted is that the population
    is COMPLETE, so a future edit cannot silently drop a label from a destructive tool's
    set either. ⚠️ Joining the population is not the same as being strip-ready: what
    used to refuse `:Point` here was the WRITER arm, and `#14552` closed it — all four
    declared writers now ask the gate, each keeping its copy on a stated sole-copy branch
    (see `test_the_plan_ACCEPTS_POINT_now_that_all_four_writers_ask`). What can still
    refuse the label is the COVERAGE arm, which is a statement about the record's own
    counts rather than about scope.
    """
    assert [label for _t, label, _k in parity.RETIRED] == [
        "Object", "Subject", "Event", "Source", "Point"]
    # Every served type addresses a planned label — the population is the whole vocabulary.
    from tortoise.security import ENTITY_TYPE_LABELS

    assert set(ENTITY_TYPE_LABELS.values()) == {label for _t, label, _k in parity.RETIRED}
    # …and nothing is declared out of scope now, by NAME rather than by count.
    assert parity.NOT_IN_SCOPE == ()


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
    """⛔ THE MEASURED FORM IS THE ONE THE STRIPPER READS, so it is the one that must fail
    closed here (`#13796`). `mirror_verdict` is preferred when the tree carries it; the
    boolean-only fallback is pinned separately by
    `test_write_gate_falls_back_to_the_boolean_on_a_tree_without_the_measured_form`.
    """
    from tortoise import record_vector

    def boom(_entity_type, **_kwargs):
        raise RuntimeError("coverage cache unreadable")

    monkeypatch.setattr(record_vector, "mirror_verdict", boom, raising=False)
    emits, reason = strip.write_gate("object")
    assert emits is True
    assert "not a permission" in reason


def test_write_gate_falls_back_to_the_boolean_on_a_tree_without_the_measured_form(
        monkeypatch):
    """A tree holding `mirror_required` but not `mirror_verdict` is one this change has not
    reached, and the read must degrade to it rather than raise (`#13796`).

    ⛔ THE FALLBACK IS STILL FAIL-CLOSED: the answer is the same boolean, and the reason
    says coverage was the predicate's own claim — no counts, because none were taken.
    """
    from tortoise import record_vector

    monkeypatch.delattr(record_vector, "mirror_verdict", raising=False)
    monkeypatch.setattr(record_vector, "mirror_required", lambda entity_type: True,
                        raising=False)
    emits, reason = strip.write_gate("object")
    assert emits is True
    assert "no counts were taken" in reason, reason


def test_write_gate_falls_back_fail_closed_when_the_old_gate_raises(monkeypatch):
    """⛔ THE FALLBACK'S `except` ARM, which `ea10eb2d` left without a falsifier.

    Re-pointing `test_write_gate_is_fail_closed_when_the_gate_raises` at
    `mirror_verdict` (the measured form) took the only test that used to raise through
    the OLD symbol with it, and the sibling above returns `True` without raising — so
    deleting the fallback's `except Exception → return True, "… raised …"` left the suite
    green (measured: **110 passed**).

    Why it matters: a tree that carries `mirror_required` but not `mirror_verdict` is one
    this change has not reached, and a bare `getattr` miss there must answer fail-closed.
    A raise escaping instead is not a permission — but it is also not the refusal the tool
    documents, and the file's own standard is one falsifier per load-bearing claim.

    Mutation (measured): delete the `except` block in `write_gate`'s fallback arm (leaving
    `needed = bool(gate(entity_type))`) and this test reds on the escaping exception while
    the rest of the file stays green.
    """
    from tortoise import record_vector

    def _boom(entity_type):
        raise RuntimeError("the record leg is unreachable")

    monkeypatch.delattr(record_vector, "mirror_verdict", raising=False)
    monkeypatch.setattr(record_vector, "mirror_required", _boom, raising=False)

    emits, reason = strip.write_gate("object")
    assert emits is True, reason
    assert "raised" in reason and "RuntimeError" in reason, reason
    assert "an unreadable answer is not a permission" in reason, reason


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

    monkeypatch.setattr(
        record_vector, "mirror_verdict",
        lambda entity_type, **_: (False, "the record reports coverage"), raising=False)

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
    monkeypatch.setattr(
        record_vector, "mirror_verdict",
        lambda entity_type, **_: (False, "the record reports coverage"), raising=False)
    emits, reason = strip.write_gate("no_such_entity_type_13647")
    assert emits is True, f"an unmapped entity_type licensed a delete ({reason})"
    assert "no declared graph label" in reason


def test_write_gate_does_not_trust_the_record_predicate_alone(
        monkeypatch, tmp_path):
    """⛔ THE P1 FINDING, AS A FALSIFIER: coverage is a READ fact, not a WRITE fact.

    On the tree that introduces `mirror_required` (`#13610`'s write half), the predicate
    is answered from `COVERAGE_GATED_LABELS`, which is **every served label** — while the
    gate is consulted at exactly TWO folds (`_upsert_subject`, `_upsert_object`). So
    `mirror_required("event")` answers `False` while `_upsert_event` still writes
    `embedding` on every event. A tool that trusted the predicate alone would declare
    `:Event` write-stopped and strip it into a live writer.

    A mutation that drops the `_fold_consults_gate` call reds this test.

    ⛔ RE-DRIVEN AT `#13626`, AND THE FALSIFIER IS NOW A POPULATION RATHER THAN A LABEL.
    Every declared writer of every retired label asks the gate on this tree, so "the
    predicate says not-required while a writer still stores" is no longer reachable from
    the shipped source — asserting it against `object` would now pin the OPPOSITE of the
    tree. What the arm has to be shown is the invariant it enforces: with the predicate
    answering `False`, a population that contains ONE ungated writer still emits, and the
    refusal NAMES that writer. The population is supplied explicitly (the shape every
    census test here uses) so the assertion is about the arm and not about today's
    `entities.py`; `test_every_declared_writer_of_a_retired_label_asks_the_gate`
    pins the shipped population in the other direction.
    """
    from tortoise import record_vector

    monkeypatch.setattr(record_vector, "mirror_verdict",
                        lambda entity_type, **_: (
                            False, "the count measured 3 vector(s) in the record for object "
                                   "against 3 in the graph"), raising=False)
    # The recorded predicate says "not required" for every label; that is a READ fact and
    # the gate must still answer "emits" while a writer exists that nobody stopped.
    ungated = _fake_writer_module(tmp_path, monkeypatch, """
def _upsert_object(ev):
    return _write(ev)
""", name="fake_writer_13629_trusts_predicate")
    # The FOLD MAP is emptied on purpose: the refusal below must come from the REGISTERED
    # writer, i.e. from a population `FOLD_WRITERS` alone does not enumerate.
    monkeypatch.setattr(strip, "FOLD_WRITERS", {})
    monkeypatch.setattr(strip, "_writers_for", lambda label: (
        (f"{ungated}::_upsert_object",), None))
    emits, reason = strip.write_gate("object")
    assert emits is True
    assert "writes `embedding` without an `if`" in reason
    # ⛔ AND THE REFUSAL NAMES THE WRITER that still stores. This is the sentence an
    # operator reads when the gate shuts, so an accurate population is a diagnosis, not
    # a courtesy.
    assert "_upsert_object" in reason


def test_a_comment_mentioning_the_gate_is_not_a_gate(tmp_path, monkeypatch):
    """⛔ F3's real fold: the witness is a CALL, and `entities.py` is the tree under test.

    The gate tests above drive fake modules; this one pins the REAL module, because the only
    way to know the check can read `entities.py` at all is to point it there — and since
    `#13626` that direction is the CERTIFYING one: `_upsert_event` DOES ask, so the answer
    must be "gated". If the AST walk silently failed to find the fold, the "no `if`" refusal
    would come back and red this test.

    ⛔ AND IT MUST PASS THE LABEL'S `entity_types`. Without them `_writer_consults_gate`
    computes `wanted = ()` and `wanted and _asks_the_gate(...)` short-circuits, so the
    answer does not depend on the AST walk at all: a `_asks_the_gate -> True` mutant leaves
    an unfalsifiable assertion green (round 8's F2). With `("event",)` a mutant that stops
    matching THIS label's gate call reds it.
    """
    gated, why = strip._writer_consults_gate(
        "tortoise.projection.entities", "_upsert_event", ("event",))
    assert gated is True, why
    assert "tortoise.projection.entities::_upsert_event" in why

    # …and a prose mention in a writer that stores is still a refusal, on the same helper.
    prose = _fake_writer_module(tmp_path, monkeypatch, WRITER_PROSE_ONLY,
                               name="fake_writer_13629_prose_only")
    assert strip._writer_consults_gate(prose, "_upsert_object", ("object",))[0] is False


#: ⛔ THE EXEMPTION IS GONE (`#14552`). Until it, this map excused two `:Point` writers from the
#: census below, on the `#13971` review's finding that the graph copy they place is the SOLE
#: store on their lanes (`sdk::_write_capture_turns`'s batch/fallback writes, and
#: `projection::_rebuild_all_replay`'s pass-1b snapshot restore). That finding was CORRECT and
#: is untouched — what was missing was the ASK: neither writer could distinguish "the record
#: holds THIS row" from "the label's aggregate is covered". `#14552` gave them that answer
#: (the PASS-1B SNAPSHOT RESTORE reads the record per id; the CAPTURE asks the label predicate
#: per row on the lane where the record is expected to hold the turns and re-attaches the vector
#: for any turn the record's write actually REFUSED — it does not read the record per turn, and
#: does not need to), so both now ask the gate on the branch where the copy is a mirror, and the
#: census has NO exceptions left. The map is kept as an EMPTY declaration rather than deleted, so
#: re-adding an excuse is a visible edit to a named constant and the equality below reds.
SOLE_CARRIER_WRITERS: dict[str, frozenset[str]] = {}


def test_every_declared_writer_of_a_retired_label_asks_the_gate():
    """⛔ `#13626`'s DELIVERABLE, AS A CENSUS OVER THE REAL TREES.

    The strip's own refusal counts writers that do not ask; this test counts the same thing
    from the other side, for EVERY declared writer of every label the tool may strip, and
    names each one that fails. Without it `strip_ready` would be the only witness — and it
    is answered from a process whose gate may be unarmed, which says nothing about the
    source.

    Mutation: un-wire any single writer (drop its `if _derived_mirror_required(...)`, or its
    `props.pop("embedding")` stop arm) and the label it serves reds here with its name.
    ⛔ THERE IS NO EXCEPTION LEFT (`#14552`): `SOLE_CARRIER_WRITERS` is empty, so the two
    `:Point` writers that used to be excused are held to the same test as every other writer.
    """
    ungated: dict[str, list[str]] = {}
    for _table, label, _key in parity.RETIRED:
        writers, refusal = strip._writers_for(label)
        assert refusal is None, f":{label} population unreadable: {refusal}"
        types = tuple(parity.affected_entity_types(label))
        excused = SOLE_CARRIER_WRITERS.get(label, frozenset())
        # every excused name must still be a DECLARED writer of this label: an exemption for a
        # writer that no longer stores it would silently outlive the code it was written for.
        assert excused <= set(writers), (
            f"SOLE_CARRIER_WRITERS names a writer that is not a declared writer of :{label}: "
            f"{sorted(excused - set(writers))}")
        for key in writers:
            if key in excused:
                continue
            module_name, _, symbol = key.partition("::")
            if not any(strip._writer_consults_gate(module_name, symbol, (t,))[0]
                       for t in types):
                ungated.setdefault(label, []).append(key)
    assert ungated == {}, (
        "a declared writer of a retired label still sets `embedding` without asking the "
        f"gate, so a strip would be undone by its next call: {ungated}")
    # ⛔ AND NO EXCEPTION IS LEFT. `#14552` closed the last two (`:Point`'s capture batch and
    # the pass-1b snapshot restore) by giving them the per-row answer they were missing.
    assert SOLE_CARRIER_WRITERS == {}, (
        "a label still excuses a writer from the gate: the exemption is how a redundant "
        f"write stays invisible to this census: {SOLE_CARRIER_WRITERS}")


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

    ⛔ AND SINCE `#13626` ALL OF THEM ASK, so the assertion moved from "the refusal names
    them" to "the population CONTAINS them and the gate CLEARS them" — the same population
    fact, read in the direction the tree now answers. The identity of the population is what
    this test exists for: a label cleared on the folds alone is the strip-licensing defect
    the register was added to prevent, and `_fold_consults_gate` clearing is only meaningful
    because the population it cleared is the register's.
    """
    writers, refusal = strip._writers_for("Event")
    assert refusal is None
    assert "tortoise.sdk::_update_entity" in writers
    assert "tortoise.hosted_backup::restore_graph" in writers
    assert "tortoise.projection.entities::_upsert_event" in writers
    # The five `:Event` writers that are NOT folds — the population the fold map alone misses.
    # ⛔ THE SDK NAMES HERE ARE THE REGISTER'S SEAMS, NOT THE RETIRED PUBLIC METHODS.
    # `#13899` privatised `ingest_corpus` to `sdk.py::_ingest_corpus` and moved the register
    # entry with it (`tortoise/embed_writer_register.py`, the `RECOGNISED_PRODUCER` row whose
    # reason quotes `self._session_embedding(...)`); this loop kept the public spelling and so
    # asserted a name that no longer exists — measured red on `main` at `ef1d6f7561`, where
    # `_writers_for("Event")` contains `tortoise.sdk::_ingest_corpus` and not this id. Name the
    # seam the register names: `ingest_corpus` is retired (#12901), so a pin on it cannot be
    # satisfied by any repair, only by resurrecting a method the retirement removed.
    for outside in ("tortoise.sdk::_session_event_write",
                    "tortoise.sdk::_embedding_repair_attempt",
                    "tortoise.sdk::_event_raw_write_door",
                    "tortoise.sdk::_ingest_corpus",
                    "tortoise.session_indexer::main"):
        assert outside in writers, outside

    gated, why = strip._fold_consults_gate("Event")
    assert gated is True, why
    assert "every :Event writer (9) calls the gate" in why, why


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

    monkeypatch.setattr(record_vector, "mirror_verdict",
                        lambda entity_type, **_: (
                            True, "the record has not proved it holds this label's "
                                  "vectors in this process"), raising=False)
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

    monkeypatch.setattr(record_vector, "mirror_verdict",
                        lambda entity_type, **_: (
                            False, "the count measured 2 vector(s) in the record for object "
                                   "against 2 in the graph"), raising=False)
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
    # ⛔ AND THE CERTIFIED REASON NAMES THE MEASUREMENT (`#13796`): a `strip_ready: True`
    # rests on it, so the counts the verdict compared must survive into the sentence the
    # operator reads.
    assert "2 vector(s)" in reason and "against 2 in the graph" in reason
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
        lambda entity_type, **_: (entity_type == "document", f"{entity_type} emits"))
    emits, reason = strip.label_write_gate("Source")
    assert emits is True
    assert "document" in reason
    # …and when BOTH stop, the label stops — and its reason carries the coverage account of
    # every type it cleared (`#13796`: a `strip_ready: True` must rest on a checkable
    # sentence, not on the bare word "stopped").
    monkeypatch.setattr(strip, "write_gate",
                        lambda entity_type, **_: (False, f"record covers {entity_type}"))
    stopped, why = strip.label_write_gate("Source")
    assert stopped is False
    assert "record covers source" in why and "record covers document" in why


# ── #13796: the gate is MEASURABLE from a fresh operator process ────────────


class _Projection:
    """The projection's own graph-side vector count, as the real one answers it.

    `tortoise.projection._label_vector_count` is the ONE aggregate `#13610` added for
    this denominator, and it is a full-label count of nodes carrying an `embedding`.
    Reproducing it against `FakeGraph` (rather than returning a constant) keeps the
    test on the statement shape the supplier really issues, so a supplier that asked
    the wrong question would red here instead of passing on a stub.
    """

    def __init__(self, graph) -> None:
        self._graph = graph

    def _label_vector_count(self, entity_type, *, timeout_ms=None):
        from tortoise.security import entity_label

        rows = self._graph.ro_query(
            f"MATCH (n:{entity_label(entity_type)}) WHERE n.embedding IS NOT NULL "
            "RETURN count(n)").result_set
        return int(rows[0][0])


#: The tenant the LAST `_record_vector_count` call was asked about, so the ORDER of the
#: declared pair is observable (`#13796` review rounds 5-7: the half-tenant guard, then its
#: mirrored form, then the pair's ordering — each a one-token edit on one line).
ASKED_SCOPE: dict = {}


def _counts_sentence(record_vectors: int, graph_vectors: int, *,
                     label: str = "object") -> str:
    """The count sentence `Coverage.reason` carries, built in ONE place (#13872).

    ⛔ WHY THIS IS A HELPER AND NOT EIGHT LITERALS. `8d8355bde4` (#13812) reworded the
    reason from a present-tense `"the record holds N … the graph serves M"` to the
    measurement-dated `"the count measured N … against M"` — and left EIGHT assertions in
    this file pinning the old sentence, so `test (c)` has been RED on `main` since (#13872).
    The pins are still literal and still assert the WHOLE sentence rather than a bare digit
    (a `"12" in reason` passes on a fold's line numbers, which is what this pin exists to
    stop), but they now name the sentence once: the next reword edits this function, not
    eight call sites that a shard then has to find.

    The wording is NOT derived from `tortoise/record_vector.py` — an expectation computed by
    the code under test cannot notice that the code changed, which is the whole point of a
    pin. This is the SAME literal, recorded once.
    """
    return (f"the count measured {record_vectors} vector(s) in the record for "
            f"{label} against {graph_vectors} in the graph")


def _fresh_record_leg(monkeypatch, *, record_vectors: int,
                      graph_vectors: int | None = None):
    """An EMPTY coverage cache, an armed leg, and the record's aggregate stubbed.

    ⛔ THE EMPTY CACHE IS THE POINT. `#13796`'s defect needs no fault: a process that has
    served no gated dense read holds no verdict, and before this change that was the
    permanent state of every operator run. `reset_coverage_cache()` is what makes this
    test the fresh process the issue describes.

    The returned supplier is only for the `write_gate` tests; a test that measures through
    `_Projection` passes `graph_vectors` nothing and lets the GRAPH answer the denominator,
    because a constant there would hide a supplier that asked the wrong question.
    """
    from tortoise import record_vector

    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()
    # ⛔ THE COUNT STUB CAPTURES THE SCOPE IT WAS ASKED ABOUT (review round 7). Every
    # `_record_vector_count` stub in this file used `lambda *a, **k:` and threw the scope
    # away, so `scope=(graph_id, org_id)` — the two halves interchanged on the line this
    # series edits — measured a DIFFERENT tenant and left **148 passed** across both suites.
    # A swapped pair is accepted silently by `_declared_scope` (both halves are non-empty
    # strings), so the order has to be asserted, not assumed.
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda _label, scope=None, **k: (
                            ASKED_SCOPE.update(scope=scope), record_vectors)[1],
                        raising=True)
    return lambda _entity_type: graph_vectors


def test_the_gate_is_measured_from_a_fresh_process_when_the_population_is_supplied(
        monkeypatch):
    """⛔ #13796, THE DEFECT ITSELF: the gate was unsatisfiable from this process.

    Two halves, and the FIRST is the falsifier: asked with no supplier and no declared
    tenant — the state every operator run starts in — the gate reports NO MEASUREMENT.
    ⛔ The observed run could not even say WHICH precondition was missing: `write_gate`
    used to print one fixed sentence ("the record has not proved it holds this label's
    vectors") whatever the predicate's real arm was, so a missing tenant and a missing
    denominator were indistinguishable in the artifact an operator pastes into the issue.
    Now each arm names itself, and this half pins that neither claims a count.

    The second half supplies what a caller holding the graph handle always could — the
    denominator — plus the tenant this run declared on its command line, and the answer
    becomes the MEASUREMENT: the coverage half clears and the gate reaches `answers False`.
    ⚠️ What refuses after that is a DIFFERENT question, `#13647`'s writer check, and on
    this tree it refuses every label because the writers are not gated (`#13806`). This
    test pins the coverage half being measured and its counts reported; it does not claim
    the label is strippable.
    """
    supply = _fresh_record_leg(monkeypatch, record_vectors=9200, graph_vectors=9200)

    emits, unmeasured = strip.write_gate("object")
    assert emits is True
    assert "9200" not in unmeasured, (
        "a fresh process with no denominator reported a MEASUREMENT it never took: "
        f"{unmeasured}")
    assert ("has not proved" in unmeasured) or ("no tenant" in unmeasured), unmeasured

    emits, reason = strip.write_gate(
        "object", graph_population=supply, org_id="org", graph_id="graph",
        record_dsn="postgresql://localhost/record")
    assert "answers False" in reason, reason
    assert "has not proved" not in reason, reason
    assert _counts_sentence(9200, 9200) in reason, (
        f"the measured arm did not report the two counts it compared: {reason}")
    # ⛔ AND THE TENANT WAS ASKED FOR IN THE DECLARED ORDER (review round 7). A swapped
    # `scope=(graph_id, org_id)` measures a DIFFERENT tenant — both halves are non-empty
    # strings, so `_declared_scope` accepts it silently and the gate answers from the wrong
    # record. Measured: the swap left both suites at 148 passed before this assertion.
    asked = ASKED_SCOPE.get("scope")
    assert asked is not None, (
        "the record count was taken with no scope at all — the declared tenant never "
        f"reached the query: {ASKED_SCOPE!r}")
    assert (asked.org_id, asked.graph_id) == ("org", "graph"), (
        "the tenant's two halves reached the record query INTERCHANGED, so the count came "
        f"from a different tenant: {asked!r}")


def test_a_short_record_refuses_the_label_with_BOTH_counts_in_the_reason(monkeypatch):
    """The other direction: short coverage is a refusal that NAMES THE DEFICIT.

    ⛔ "NOT PROVED" AND "PROVED SHORT" ARE DIFFERENT ANSWERS, and only the second tells
    an operator what to do (backfill 9188 rows). A stripper that reported the first for a
    measured deficit would send them to re-run the census they had already run.
    """
    supply = _fresh_record_leg(monkeypatch, record_vectors=12, graph_vectors=9200)
    emits, reason = strip.write_gate(
        "object", graph_population=supply, org_id="org", graph_id="graph",
        record_dsn="postgresql://localhost/record")
    assert emits is True
    assert "answers True" in reason, reason
    # ⛔ THE SENTENCE, NOT THE DIGITS. `"12" in reason` is satisfied by any two digits
    # anywhere in a reason that also embeds the fold's line numbers, so a deficit reported
    # with the wrong counts would still pass it (found in review of this change).
    assert _counts_sentence(12, 9200) in reason, (
        f"a short record did not report the deficit it measured: {reason}")


def test_the_plan_is_STRIP_READY_from_a_fresh_process_when_the_record_covers(
        stores, monkeypatch):
    """`strip_ready: True` and a reason an operator can check — acceptance #13796 (a).

    ⛔ IT IS MET WITH THE WRITER HALF HELD CLOSED, AND END-TO-END ON ZERO LABELS ON THIS
    TREE. The two halves ask different questions: coverage asks *"does the record hold this
    label's vectors?"* (`#13796`, fixed here) and the writer check asks *"will a writer
    re-add them?"* (`#13647`). On this tree the second still refuses every label, because
    `hosted_backup::restore_graph` and `sdk::_update_entity` (and, for `:Event`/`:Source`,
    several more) store an `embedding` without asking the gate — measured, and owned by
    `#13806`, which needs a write-semantics decision rather than a test change.

    So the writer check is DRIVEN CLOSED here on purpose, and what this test pins is the
    half this issue is about: with writers certified, a covering record makes the plan ready
    IN A FRESH PROCESS and the reason beside `strip_ready` is the measurement, not the bare
    word "ready".
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    _fresh_record_leg(monkeypatch, record_vectors=1, graph_vectors=1)
    monkeypatch.setattr(strip, "_fold_consults_gate",
                        lambda label: (True, f"every :{label} writer calls the gate"))

    plan = strip.build_plan(
        g=graph, record_dsn="postgresql://localhost/record", org_id="org",
        graph_id="graph", graph_name="g", graph_uri="docker://x/g",
        labels=["Object"], batch=strip.DEFAULT_BATCH,
        graph_population=strip.graph_vector_population(_Projection(graph)))

    row = plan["labels"][0]
    assert row["strip_ready"] is True, row["blocked_reason"]
    assert plan["ready"] is True
    # ⛔ THE REASON NAMES BOTH COUNTS, on the READY arm — that is what makes
    # `strip_ready: True` checkable instead of asserted.
    assert _counts_sentence(1, 1) in row["readiness_reason"], row["readiness_reason"]
    # …and the operator reading the PLAN sees them, not the bare word "ready".
    rendered = strip.render_plan(plan)
    assert _counts_sentence(1, 1) in rendered


def test_the_plan_refuses_a_SHORT_record_for_the_MEASURED_reason(stores, monkeypatch):
    """⛔ ACCEPTANCE #13796 (b) AND (c) IN ONE FALSIFIER: the block is the MEASUREMENT.

    "The plan for a short-record label must refuse, and must refuse for the *measured*
    reason (not "not proved" when the counts were never taken)." Both halves are
    asserted, because they fail in opposite directions: a stripper that refused with
    "not proved" would be unable to distinguish an unbackfilled deployment from a
    tested one, and a stripper that did not refuse would strip a label whose only copy is
    the graph's.

    ⛔ AND THE REFUSAL MUST BE THE COVERAGE ARM'S, so the OTHER two arms are held open:
    every graph key is READABLE IN THE RECORD (parity passes, otherwise the parity block
    would be the reason and a coverage gate that wrongly certified would still look
    refused) and every writer is driven closed. The record's own aggregate stays short, so
    the coverage comparison is the ONLY thing left that can refuse — which is what makes
    this a falsifier rather than a test that passes for an unrelated reason (both halves
    caught in review of this change; the earlier cut proved nothing).
    """
    graph, record = stores
    for key in ("obj-1", "obj-2", "obj-3"):
        graph.add("Object", key)
        # parity holds: the record can answer with EVERY graph vector it measured.
        record.holds("object", key)
    # …but the record's own aggregate holds ONE vector, against a graph that serves three.
    # The two legs are separate reads (row-level for parity, one aggregate for coverage),
    # and only the second is short: that difference is the whole point.
    _fresh_record_leg(monkeypatch, record_vectors=1)
    monkeypatch.setattr(strip, "_fold_consults_gate",
                        lambda label: (True, f"every :{label} writer calls the gate"))

    plan = strip.build_plan(
        g=graph, record_dsn="postgresql://localhost/record", org_id="org",
        graph_id="graph", graph_name="g", graph_uri="docker://x/g",
        labels=["Object"], batch=strip.DEFAULT_BATCH,
        graph_population=strip.graph_vector_population(_Projection(graph)))

    row = plan["labels"][0]
    assert row["strip_ready"] is False, (
        "a short record certified a strip — the graph holds vectors the record cannot "
        f"answer with ({row})")
    blocked = row["blocked_reason"]
    assert _counts_sentence(1, 3) in blocked, blocked
    assert "has not proved" not in blocked, blocked


def test_the_plan_ACCEPTS_POINT_now_that_all_four_writers_ask(stores, monkeypatch):
    """`#14552`: `:Point` is MEASURED from a fresh process and — for the first time —
    `strip_ready: True`, because the writer census is now EMPTY.

    ⛔ NO `_fold_consults_gate` MONKEYPATCH — the REAL census runs here. `#13971` (review
    P1) recorded that two writers could not ask, because the graph copy they place is the
    SOLE store on their lanes:

      * `tortoise.sdk::_write_capture_turns` — its graph batch runs exactly where the record
        cannot hold the turn (no journal, no tenant, an open connect breaker, or a record
        write that REFUSED it);
      * `tortoise.projection::_rebuild_all_replay` — pass-1b restores a snapshot vector only
        for ids the journal wrote NO vector for, i.e. only where the snapshot is the sole
        carrier of a graph-only id's vector.

    The finding stands and those branches are untouched: what `#14552` closed is the ASK.
    The PASS-1B SNAPSHOT RESTORE asks the RECORD about the ids it is about to write
    (`record_read.point_vector_keys` — does it SERVE a vector for THIS id) and asks
    `mirror_required("point")` — a LABEL predicate — only about an id the record ANSWERS;
    the CAPTURE asks the LABEL predicate per row on the lane where
    `record_holds_turns(...)` says the record is expected to hold the turns, and re-attaches
    the vector per rescued turn — so on both writers the branch that keeps the copy is now
    the DOCUMENTED sole-copy branch rather than a place no gate reaches. Gating either one on
    the label aggregate ALONE is still forbidden, and the two tests that pin it
    (`test_rebuild_graph_only_derived_parity.py::
    test_the_pass_1b_snapshot_restore_is_NEVER_gated_by_the_label_verdict`,
    `test_10000_turns_record_only.py::test_the_graph_store_lane_keeps_the_turn_VECTOR`)
    still pass — they force the gate to STOP and the vectors still land.

    ⛔ The MEASUREMENT half is asserted too, and it must be: the coverage verdict for
    `point` is taken in THIS process and names both counts, so `strip_ready: True` rests on
    a measurement rather than on an absent refusal. ⚠️ WHICH HALF THIS PIN CAN SPEAK FOR
    (review P2): `strip_ready` is `blocked_reason is None`, and the readiness probe asks
    EVERY served type of the label — `point` **and** `operator`
    (`parity.affected_entity_types`). The record-leg double below answers one count for
    every entity type, so this test proves the `point` half and CANNOT represent the
    `operator` half (whose graph denominator is the whole `:Point` label, which is why the
    tool's own docstring calls it unreachable while a plain point carries an embedding).
    On a deployment where `operator` is the only blocker the plan refuses and NAMES it;
    that is the tool's verdict to return, not this pin's to fake.
    """
    graph, record = stores
    for key in ("pt-1", "pt-2"):
        graph.add("Point", key)
        record.holds("point", key)
    _fresh_record_leg(monkeypatch, record_vectors=2)

    plan = strip.build_plan(
        g=graph, record_dsn="postgresql://localhost/record", org_id="org",
        graph_id="graph", graph_name="g", graph_uri="docker://x/g",
        labels=["Point"], batch=strip.DEFAULT_BATCH,
        graph_population=strip.graph_vector_population(_Projection(graph)))

    row = plan["labels"][0]
    assert row["strip_ready"] is True, row["blocked_reason"]
    assert plan["ready"] is True
    # …and the READY reason is the measured coverage verdict, naming both counts.
    assert _counts_sentence(2, 2, label="point") in row["readiness_reason"], (
        row["readiness_reason"])
    # ⛔ AND THE WRITER CENSUS IS EMPTY FOR EVERY ONE OF THE FOUR DECLARED WRITERS — the
    # whole point of this change. A regression in any one of them re-refuses the label here.
    ready, why = strip._fold_consults_gate("Point")
    assert ready is True, why
    assert "writer(s) that do not ask" not in why, why
    for writer in ("_write_capture_turns", "_rebuild_all_replay",
                   "_update_entity", "restore_graph"):
        assert f"{writer}" not in why, (writer, why)


def test_event_and_subject_are_REFUSED_only_by_the_COUNT_not_by_the_gate(
        stores, monkeypatch):
    """`#14552` ACCEPTANCE, THE HONEST HALF: `:Subject` and `:Event` are already
    writer-clean, so their refusal is the RECORD'S COUNT — a backfill, not a write gate.

    The issue asked for `strip_ready: True` on all three labels. For `:Subject`/`:Event`
    the production refusal is the coverage/parity arm, and no change in this lane can move
    a count: the graph holds vectors the record cannot answer with. This test pins BOTH
    halves so the claim is checkable rather than a shrug:

      * with every graph id readable in the record and a record aggregate that covers them,
        the SAME plan reports `strip_ready: True` — the writers are not the blocker;
      * with one graph-only id, it refuses and names the GRAPH-ONLY population (the
        backfill reason), never the writer census.
    """
    graph, record = stores
    for label, table, keys in (("Subject", "subject", ("sub-1", "sub-2")),
                               ("Event", "event", ("ev-1", "ev-2"))):
        assert strip._fold_consults_gate(label)[0] is True, (
            f":{label} has a writer that does not ask — the census is the blocker, not the "
            "count")
        for key in keys:
            graph.add(label, key)
            record.holds(table, key)
    _fresh_record_leg(monkeypatch, record_vectors=2, graph_vectors=2)

    plan = strip.build_plan(
        g=graph, record_dsn="postgresql://localhost/record", org_id="org",
        graph_id="graph", graph_name="g", graph_uri="docker://x/g",
        labels=["Subject", "Event"], batch=strip.DEFAULT_BATCH,
        graph_population=strip.graph_vector_population(_Projection(graph)))
    for row in plan["labels"]:
        assert row["strip_ready"] is True, row["blocked_reason"]

    # …and with ONE graph-only id the refusal is the BACKFILL arm, naming the population.
    graph.add("Event", "ev-unrecorded")
    plan = strip.build_plan(
        g=graph, record_dsn="postgresql://localhost/record", org_id="org",
        graph_id="graph", graph_name="g", graph_uri="docker://x/g",
        labels=["Event"], batch=strip.DEFAULT_BATCH,
        graph_population=strip.graph_vector_population(_Projection(graph)))
    row = plan["labels"][0]
    assert row["strip_ready"] is False, row
    blocked = row["blocked_reason"]
    assert "graph node(s) carry a vector the record cannot answer with" in blocked, blocked
    assert "writer(s) that do not ask" not in blocked, blocked


def test_the_plan_refuses_a_SHORT_record_for_POINT_and_names_both_counts(stores, monkeypatch):
    """`#13971` FALSIFIER: with the record short of the graph for `point`, the plan refuses
    and names both counts.

    Parity is held OPEN on purpose — every graph key is readable in the record — so the
    only arm left that can refuse is the COVERAGE comparison. A stripper that refused with
    "has not proved" would be unable to tell an unbackfilled deployment from a tested one,
    which is the `#13796` defect; one that did not refuse would delete the only copy.
    """
    graph, record = stores
    for key in ("pt-1", "pt-2", "pt-3"):
        graph.add("Point", key)
        record.holds("point", key)
    # …but the record's own aggregate holds ONE vector against a graph that serves three.
    _fresh_record_leg(monkeypatch, record_vectors=1)

    plan = strip.build_plan(
        g=graph, record_dsn="postgresql://localhost/record", org_id="org",
        graph_id="graph", graph_name="g", graph_uri="docker://x/g",
        labels=["Point"], batch=strip.DEFAULT_BATCH,
        graph_population=strip.graph_vector_population(_Projection(graph)))

    row = plan["labels"][0]
    assert row["strip_ready"] is False, (
        "a short record certified a `:Point` strip — the graph holds vectors the record "
        f"cannot answer with ({row})")
    blocked = row["blocked_reason"]
    assert _counts_sentence(1, 3, label="point") in blocked, blocked
    assert "has not proved" not in blocked, blocked
    assert plan["ready"] is False


# ── #13796: the CLIs must hand the gate THEIR OWN measurement context ───────


def _cli_harness(monkeypatch, graph):
    """Point both CLIs at ``graph`` through a projection DOUBLE that can also count.

    ⛔ THE DOUBLE EXPOSES `_label_vector_count`, WHICH IS THE POINT. Every other
    `main`-level test's `_Proj` carries only `g`, so `graph_vector_population(proj)`
    answers `None` there and a reverted CLI wiring would look identical — which is how
    the central hop of this fix came to have no falsifier at all (found in review of
    this change, by two independent reviewers, measured: deleting
    `graph_population=population` from `main` left the suite at 105 passed).
    """
    class _Proj(_Projection):
        def __init__(self, target):
            super().__init__(target)
            self.g = target

    import tortoise.projection as projection

    monkeypatch.setattr(projection, "FalkorProjection",
                        type("P", (), {"from_uri": staticmethod(lambda *a, **k: _Proj(graph)),
                                       "__init__": lambda self, *a, **k: None}))
    monkeypatch.setattr(parity.census_mod, "resolve_graph_name", lambda *a, **k: "g")
    monkeypatch.setattr(parity.census_mod, "_refuse_unless_graph_present",
                        lambda *a, **k: None)
    monkeypatch.setattr(parity.census_mod, "empty_graph_target_flag", lambda *a, **k: None)
    monkeypatch.setattr(strip, "_fold_consults_gate",
                        lambda label: (True, f"every :{label} writer calls the gate"))


def test_the_stripper_cli_hands_the_gate_its_own_projection_and_tenant(
        stores, monkeypatch, capsys):
    """⛔ #13796's CENTRAL HOP, pinned from the CLI: a reverted `main` must RED.

    The plan tests pass `graph_population=` themselves, so they pin `build_plan` and not
    the hop that supplies it. Here `main` is driven end-to-end with a covering record and
    a projection double that CAN count: if `main` stops handing its own population and
    tenant to the gate, the row falls back to "this process has taken no verdict" and the
    measured sentence disappears from the printed plan.

    Mutation (measured): delete `graph_population=population` from `main`'s `build_plan`
    call and this test reds on the absent sentence while the rest of the file stays green.

    ⛔ TWO vector-bearing nodes on purpose: with one, a constant `lambda _label: 1` produced
    the same sentence and this assertion passed on an ASSERTED denominator (#13796's
    forbidden shape — measured, 2 passed). The graph's own count must be the only thing
    that can print it.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    graph.add("Object", "obj-2")
    record.holds("object", "obj-1")
    record.holds("object", "obj-2")
    _fresh_record_leg(monkeypatch, record_vectors=2)
    _cli_harness(monkeypatch, graph)

    assert strip.main(["--uri", "docker://:pw@h:6379/g", "--graph-name", "g",
                       "--record-dsn", "postgresql://localhost/record",
                       "--org-id", "o", "--graph-id", "g",
                       "--label", "Object"]) == 0
    out = capsys.readouterr().out
    assert _counts_sentence(2, 2) in out, (
        "the preview plan did not carry the measurement — `main` stopped handing the gate "
        f"its own projection/tenant, and #13796 is back ({out})")
    assert "has not proved" not in out, out


def test_the_census_cli_hands_the_gate_its_own_projection_and_tenant(
        stores, monkeypatch):
    """The same hop on the CENSUS tool, and it is a separate call site.

    ⛔ `_write_gate` IS A FUNCTION OF THE RUN'S PROJECTION, TENANT AND DSN SINCE #13796, and
    a census main that calls it with no arguments restores the defect the pair exists to
    fix — for the report an operator reads FIRST. Measured: reverting
    `_write_gate(proj, org_id=…, graph_id=…, record_dsn=…)` to `_write_gate()` left
    `test_13629 + test_13685` at 144 passed, 2 skipped. The probe `census()` receives is
    captured here and asked directly, so the argument expression is what is pinned.

    ⛔ `ready` IS STILL FALSE HERE, AND THAT IS NOT A REGRESSION: the census's gate is the
    stripper's, so it runs BOTH halves — the coverage half clears (below) and `#13647`'s
    writer check refuses `:Object` because its writers are ungated on this tree (`#13806`).
    The discriminator is therefore the MEASUREMENT, not the verdict: an unwired gate answers
    "not proved" and carries no counts; a wired one carries them. (The two module objects
    are also why this test cannot simply force the writer half closed the way the sibling
    does — the census resolves the stripper by its top-level name.)
    """
    graph, record = stores
    # ⛔ TWO nodes, for the same reason as the preview sibling: one node lets a CONSTANT
    # denominator print the same sentence, which is the asserted gate #13796 forbids.
    graph.add("Object", "obj-1")
    graph.add("Object", "obj-2")
    record.holds("object", "obj-1")
    record.holds("object", "obj-2")
    _fresh_record_leg(monkeypatch, record_vectors=2)
    _cli_harness(monkeypatch, graph)

    captured: dict = {}
    real_census = parity.census

    def _spy(**kwargs):
        captured["strip_readiness"] = kwargs["strip_readiness"]
        return real_census(**kwargs)

    monkeypatch.setattr(parity, "census", _spy)

    assert parity.main(["--uri", "docker://:pw@h:6379/g", "--graph-name", "g",
                        "--record-dsn", "postgresql://localhost/record",
                        "--org-id", "o", "--graph-id", "g",
                        "--label", "Object"]) == 0
    probe = captured["strip_readiness"]
    _ready, reason = probe("Object")
    assert _counts_sentence(2, 2) in reason, (
        "the census's gate was not given the run's projection/tenant, so it answers "
        f"\"not proved\" for a covered label (#13796's symptom): {reason}")
    assert "has not proved" not in reason, reason


def test_the_census_gate_takes_its_record_leg_from_the_flag_not_the_ambient_env(
        stores, monkeypatch):
    """⛔ THE CENSUS SIDE OF THE `record_dsn` HOP — its own call site, its own falsifier.

    `entity_vector_parity_census._write_gate` builds the probe at `:661` and hands it
    `record_dsn=record_dsn`. Deleting that one keyword leaves the projection, tenant and
    population wired, compiles and runs, and — because `_fresh_record_leg` SETS
    `TORTOISE_RECORD_DSN` (`:1837`) — leaves every census test green on the ambient value
    (measured: **110 passed**). The report an operator reads FIRST then carries
    "not proved" for a label the record covers, with no counts anywhere.

    Deleting the ambient is what makes the argument load-bearing; the `--record-dsn` flag
    alone is an invocation `main` supports (`:904`).

    Mutation (measured): drop `record_dsn=record_dsn` from `_write_gate`'s `readiness_probe`
    call and this test reds on the absent counts while the rest of the file stays green.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    _fresh_record_leg(monkeypatch, record_vectors=1)

    from tortoise import record_vector

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    record_vector.reset_coverage_cache()
    _cli_harness(monkeypatch, graph)

    captured: dict = {}
    real_census = parity.census

    def _spy(**kwargs):
        captured["strip_readiness"] = kwargs["strip_readiness"]
        return real_census(**kwargs)

    monkeypatch.setattr(parity, "census", _spy)

    assert parity.main(["--uri", "docker://:pw@h:6379/g", "--graph-name", "g",
                        "--record-dsn", "postgresql://localhost/record",
                        "--org-id", "o", "--graph-id", "g",
                        "--label", "Object"]) == 0
    _ready, reason = captured["strip_readiness"]("Object")
    assert _counts_sentence(1, 1) in reason, (
        "the census's gate did not receive this run's --record-dsn, so the record leg "
        f"came from an absent ambient and nothing was measured ({reason})")
    assert "not proved" not in reason, reason


# ── #13796: the two hops the CLI tests above do NOT pin ─────────────────────


def test_the_plan_probe_takes_its_record_leg_from_the_flag_not_the_ambient_env(
        stores, monkeypatch, capsys):
    """⛔ `build_plan`'s `readiness_probe(record_dsn=…)` HOP, the one harness masks.

    `main` declares `--record-dsn` on its command line, so an operator run need not export
    `TORTOISE_RECORD_DSN`. `build_plan` must therefore hand the probe THIS run's DSN — but
    `readiness_probe`'s `record_dsn` defaults to ``None``, so deleting
    `record_dsn=record_dsn` there (leaving `graph_population`/`org_id`/`graph_id` intact)
    compiles, runs, and leaves the whole file at **109 passed, measured**. The plan then
    resolves the record leg from an ABSENT ambient instead, and the row reaches the
    operator with `#13796`'s undiagnosable sentence — a ready-looking plan with no counts
    behind it.

    ⛔ WHY EVERY OTHER TEST MISSES IT: `_fresh_record_leg` SETS
    `TORTOISE_RECORD_DSN` (`:1837`), so the ambient value stands in for the argument and a
    dropped parameter is indistinguishable. Deleting the ambient is what makes the
    argument load-bearing, and it is the only difference between this test and its
    sibling above.

    Mutation (measured): drop `record_dsn=record_dsn` from `build_plan`'s
    `readiness_probe(...)` call and this test reds on the absent sentence while the rest
    of the file stays green.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    _fresh_record_leg(monkeypatch, record_vectors=1)

    from tortoise import record_vector

    # ⛔ NO AMBIENT RECORD DSN — the `--record-dsn`-only invocation `main` supports.
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    record_vector.reset_coverage_cache()
    _cli_harness(monkeypatch, graph)

    assert strip.main(["--uri", "docker://:pw@h:6379/g", "--graph-name", "g",
                       "--record-dsn", "postgresql://localhost/record",
                       "--org-id", "o", "--graph-id", "g",
                       "--label", "Object"]) == 0
    out = capsys.readouterr().out
    assert _counts_sentence(1, 1) in out, (
        "the plan did not carry the measurement — `build_plan` stopped handing the probe "
        f"this run's --record-dsn, so the record leg came from an absent ambient ({out})")
    assert "has not proved" not in out, out


def test_the_destructive_arm_hands_the_gate_the_runs_own_denominator(
        stores, tmp_path, monkeypatch):
    """⛔ `main`'s SECOND hop, and the one still unpinned after the preview fix.

    `main` builds the population once and threads it into BOTH arms — `build_plan` on the
    preview and `execute` on the destructive path. The sibling CLI test above drives the
    preview, so it pins the first hop; dropping the argument from the `execute` call alone
    leaves this file at **107 passed, measured** — the same class of gap the round-2
    finding filed, one arm over. `execute` re-checks the gate before it removes anything,
    so without the denominator the destructive run re-asks a question no fresh process can
    answer and refuses with #13796's own sentence.

    ⚠️ THE ASSERTION IS A REAL MEASUREMENT, NOT AN IDENTITY. The supplier is the
    projection's own count, so it is asked here for `object` and must answer the number of
    nodes THIS graph carries an `embedding` on. ⛔ THAT NUMBER IS **TWO**, deliberately: with
    a single vector-bearing node a constant `lambda _label: 1` satisfied every assertion in
    this file — measured, 2 passed — and a constant on a larger graph is precisely the
    asserted gate #13796 forbids. Two nodes make the count distinguishable from a constant,
    and the supplier is exactly what `record_vector.coverage` binds, so this is the value
    the gate compares rather than a proxy for it.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    # …a SECOND vector-bearing node. `execute` is stubbed below, so the plan still covers
    # `obj-1` alone; the point of `obj-2` is only that the denominator is not 1.
    graph.add("Object", "obj-2")
    record.holds("object", "obj-1")
    _fresh_record_leg(monkeypatch, record_vectors=1)
    # ⛔ THE AMBIENT LEG IS DELETED, so the `--record-dsn` flag is the ONLY source the
    # destructive hop can draw from (`_fresh_record_leg` otherwise sets it to the same
    # string the plan declares, and a dropped argument then looks identical).
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    _cli_harness(monkeypatch, graph)

    plan = {
        "tool": strip.PLAN_TOOL, "version": strip.PLAN_VERSION,
        "graph": "g", "graph_uri": "docker://h:6379/g", "org_id": "o", "graph_id": "g",
        "labels": [{"label": "Object", "keys": ["obj-1"], "nodes": 1,
                    "unkeyed_nodes_blocking": 0, "strip_ready": True,
                    "blocked_reason": None}],
        "total_nodes": 1,
    }
    plan["digest"] = strip.digest(strip.plan_entries(plan))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    executed: dict = {}
    monkeypatch.setattr(strip, "execute",
                        lambda **kwargs: executed.update(kwargs) or {"removed": 0})
    monkeypatch.setattr(strip, "result_exit_code", lambda result: 0)

    assert strip.main(["--uri", "docker://:pw@h:6379/g", "--graph-name", "g",
                       "--record-dsn", "postgresql://localhost/record",
                       "--org-id", "o", "--graph-id", "g",
                       "--execute", "--plan", str(plan_path),
                       "--confirm-plan-digest", plan["digest"]]) == 0

    # ⛔ THE DECLARED RECORD LEG AND TENANT, on the DESTRUCTIVE hop (review round 8).
    # Dropping `record_dsn` here substitutes the AMBIENT store: `record_serve.open_record`
    # resolves `TORTOISE_RECORD_DSN` when the value is `None`, and `execute` takes its
    # parity AND coverage verdict from the live census it runs with it — so a covering
    # ambient store would certify a strip whose copy lives elsewhere. That is the
    # false-PASS direction, and the `record_dsn` drift check does not compare this field.
    # The ambient variable is REMOVED first, because `_fresh_record_leg` sets it to the
    # same string the plan declares and would otherwise stand in for the argument.
    assert executed.get("record_dsn") == "postgresql://localhost/record", (
        "the destructive arm lost the run's declared record leg — its live pre-write "
        f"census then reads the AMBIENT store: {executed.get('record_dsn')!r}")
    assert (executed.get("org_id"), executed.get("graph_id")) == ("o", "g"), (
        "the destructive arm carried the tenant's halves interchanged: "
        f"{executed.get('org_id')!r}/{executed.get('graph_id')!r}")

    supply = executed.get("graph_population")
    assert callable(supply), (
        "the destructive arm ran without the denominator this run holds, so `execute` "
        f"re-checks the gate #13796 made unsatisfiable: {supply!r}")
    assert supply("object") == 2, (
        "the destructive arm carried a supplier that is not this graph's own count — the "
        f"gate would compare against the wrong population: {supply('object')!r}")


def test_the_census_write_gate_degrades_to_the_unmeasured_fallback_on_an_old_stripper(
        stores, monkeypatch):
    """⛔ THE VERSION-SKEW ARM: a stripper predating `readiness_probe` must still ANSWER.

    `_write_gate` prefers `readiness_probe(… measurement context …)` and falls back to the
    bare `strip_readiness` name. The fallback is a same-signature FAIL-CLOSED twin that
    looks exactly like the real answer, which is why the arm needs a falsifier: a caller
    that reached for it on a modern tree, or a `_write_gate` that answered with a probe it
    could not build, would report `ready` with no counts behind it — the unmeasured gate
    #13796 is about, restored in the second tool.

    ⛔ THE ORDER IS FORCED BY THE DELEGATION, NOT BY POLARITY (corrected after review).
    `strip_readiness` is `return readiness_probe()(label)`, so once `readiness_probe` is
    removed the fallback RAISES `NameError` rather than refusing — measured. The polarity
    is therefore measured FIRST, while the name still resolves; asserting only the
    identity would pin which function came back without pinning what it says.

    ⛔ AND THE REFUSAL MUST BE THE COVERAGE ARM'S, or this asserts nothing about
    measurement: on this tree `#13647`'s writer check refuses `:Object` first (#13806), so
    an unpatched run answers `ready is False` for the WRITER's reason and a
    `strip_readiness` that invented a denominator still passes (measured: **112 passed**
    under `readiness_probe(graph_population=lambda _: 0)` before this correction). Two
    gates are held open for that: the writers are certified, and an AMBIENT TENANT is
    installed — without a tenant the verdict refuses at the tenant door and the invented
    denominator is never consulted either.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    _fresh_record_leg(monkeypatch, record_vectors=1)
    census_strip = _census_strip()
    monkeypatch.setattr(census_strip, "_fold_consults_gate",
                        lambda label: (True, f"every :{label} writer calls the gate"))
    from tortoise.record_writer import use_record_tenant

    # ── the fallback REFUSES FOR THE MEASUREMENT: no context, so nothing is cleared.
    with use_record_tenant("org", "graph"):
        ready, reason = census_strip.strip_readiness("Object")
    assert ready is False, (
        f"the unmeasured fallback certified a strip it never measured: {reason}")
    assert "has not proved" in reason, (
        "the refusal was not the coverage arm's — so this test cannot say the fallback is "
        f"unmeasured: {reason}")

    # ── and on a tree without `readiness_probe`, that name is what `_write_gate` returns.
    monkeypatch.delattr(census_strip, "readiness_probe", raising=False)
    fallback = parity._write_gate(
        _Projection(graph), org_id="o", graph_id="g",
        record_dsn="postgresql://localhost/record")
    assert fallback is census_strip.strip_readiness, (
        "a stripper without `readiness_probe` was answered by something other than the "
        f"fail-closed twin: {fallback!r}")


def test_execute_rechecks_the_gate_with_the_runs_own_measurement_context(
        stores, monkeypatch):
    """⛔ `execute`'s OWN probe — the third hop, and the one round 2 named but nobody pinned.

    `execute` loads a plan from disk, so it never calls `build_plan` and its coverage cache
    starts empty. Immediately before the first delete it builds its OWN readiness probe for
    the live parity re-check, with the same measurement context. Measured on this head:
    dropping `graph_population=graph_population` from THAT call left the file at **112
    passed** — round 2's finding named both mutants and round 3 pinned only `main`'s call,
    one hop up. With the context gone, `mirror_verdict` takes the cache-only arm, every
    label reports *"has not proved"*, and the destructive run refuses
    `REFUSE_WRITE_STILL_EMITS` on a fully backfilled graph — #13796's exact symptom, on the
    path that deletes.

    ⛔ THE ASSERTION IS BEHAVIOURAL, NOT STRUCTURAL: the real `execute` runs to completion
    (no `Refusal`, exit 0) with the writers certified and a covering record, which is the
    outcome the denominator buys — and it is what settles the reviewer's UNVERIFIED row
    (`does the mutant actually raise REFUSE_WRITE_STILL_EMITS?`). The captured probe kwargs
    are checked too, so a mutant that keeps the argument but empties it still reds.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    _fresh_record_leg(monkeypatch, record_vectors=1)
    monkeypatch.setattr(strip, "_fold_consults_gate",
                        lambda label: (True, f"every :{label} writer calls the gate"))
    plan = _plan(graph, labels=["Object"])

    captured: dict = {}
    real_probe = strip.readiness_probe

    def _spy(**kwargs):
        captured.update(kwargs)
        # ⛔ ASKED AT BUILD TIME, not after the strip: the supplier is a live count of the
        # graph's vector-bearing nodes, so a reading taken afterwards answers 0 because the
        # removal already happened — that would measure this test's own ordering, not the
        # wiring.
        supplier = kwargs.get("graph_population")
        captured["population_at_build"] = supplier("object") if callable(supplier) else None
        return real_probe(**kwargs)

    monkeypatch.setattr(strip, "readiness_probe", _spy)

    result = _execute(graph, plan, expect_narrowed=True,
                      graph_population=strip.graph_vector_population(
                          _Projection(graph)))

    assert captured, "`execute` never built a readiness probe — the re-check was skipped"
    assert callable(captured.get("graph_population")), (
        "the destructive re-check was built without this run's denominator, so every "
        f"label answers 'has not proved' (#13796) seconds before a delete: {captured!r}")
    assert captured["population_at_build"] == 1, (
        "the destructive re-check carried a supplier that is not this graph's own count: "
        f"{captured['population_at_build']!r}")
    # ⛔ AND THE DECLARED RECORD LEG on this hop too (round 2's F2; its siblings cover the
    # other two sites by deleting the ambient variable, which is also the right test here
    # — `_execute` passes `record_dsn="postgresql://x"` while the helper sets the ambient
    # to a different store, so the explicit value is the only one that can arrive).
    assert captured.get("record_dsn") == "postgresql://x", (
        f"the destructive re-check lost the run's declared record leg: {captured!r}")
    assert strip.result_exit_code(result) == 0, result
    assert graph.vector_keys("Object") == set(), "the strip did not run"


def test_an_uncountable_projection_yields_NO_supplier_rather_than_a_zero(stores,
                                                                       monkeypatch):
    """⛔ `None` IS UNVERIFIED, NEVER `0` — and this is the FAIL-OPEN direction.

    `graph_vector_population` returns ``None`` when the projection cannot count. The guard
    that makes that ``None`` mean UNVERIFIED is a pair in `record_vector`:
    `coverage` never calls an absent supplier, and `_safe_population` answers ``None`` for a
    raise, a ``None`` value and a non-integer alike. An absence read as ``0`` would VERIFY
    coverage on a failed measurement. Measured on the
    head before this test: swapping the ``else None`` for the ordinary "supply a default"
    idiom ``else (lambda _label: 0)`` left both suites at **143 passed**, and the gate then
    answered `still_emits = False` — *"the record holds 5 vector(s) for object, the graph
    serves 0"* — on a projection that holds the handle but cannot count. That is a strip
    licence manufactured from a measurement that never happened, which is exactly what
    #13796 forbids (*"measured, not asserted"*).

    Not reachable from either CLI today (`FalkorProjection` always carries
    `_label_vector_count`), so this is defence in depth — but it is the direction every
    other gap in this file was NOT, and it had no falsifier at all.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    _fresh_record_leg(monkeypatch, record_vectors=5)

    class _Uncountable:
        """A projection that holds the HANDLE but exposes no count method."""

        g = graph

    assert strip.graph_vector_population(_Uncountable()) is None, (
        "an uncountable projection produced a supplier — and a default of 0 there VERIFIES "
        "coverage on a measurement that was never taken")
    # …and the refusal that produces is the fail-closed one, not a licence.
    monkeypatch.setattr(strip, "_fold_consults_gate",
                        lambda label: (True, f"every :{label} writer calls the gate"))
    emits, reason = strip.write_gate(
        "object", graph_population=strip.graph_vector_population(_Uncountable()),
        org_id="org", graph_id="graph", record_dsn="postgresql://localhost/record")
    assert emits is True, f"an unmeasurable denominator licensed a delete: {reason}"
    assert "has not proved" in reason or "cannot be asked" in reason, reason


@pytest.mark.parametrize("org_id,graph_id", [
    ("org-A", ""),      # a declared org with an empty graph
    ("", "graph-A"),    # the MIRROR — pinned because `if org_id` survives the first row
    (None, ""),         # neither half usable, and one of them explicitly absent
    ("", None),         # …and its mirror: `if (org_id or graph_id)` lets both through
])
def test_a_HALF_tenant_declaration_is_refused_not_downgraded_to_the_ambient_one(
        stores, monkeypatch, org_id, graph_id):
    """⛔ A HALF DECLARED TENANT MUST NOT FALL BACK TO THE AMBIENT ONE (fail-open).

    `write_gate` used to build `scope=(org_id, graph_id) if (org_id and graph_id) else None`.
    That looks like the same guard and is not: `("org-A", "")` collapses to `None`, which
    `_declared_scope` reads as *nobody declared one* and answers with the AMBIENT tenant —
    so a mis-declared run measured a DIFFERENT tenant's record and certified a strip from
    it (measured: with an ambient `("org-B", "graph-B")` installed, the gate answered
    `still_emits = False` for declared `org-A`/`""`). `_declared_scope` is the ONE resolver
    both doors use precisely because it REFUSES a half pair, and pre-coercing the tuple
    here took that refusal away. Neither CLI can reach it (both refuse a partial tenant at
    exit 3), so this is the same defence-in-depth class as the `None`/`0` arm above.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    _fresh_record_leg(monkeypatch, record_vectors=1)
    monkeypatch.setattr(strip, "_fold_consults_gate",
                        lambda label: (True, f"every :{label} writer calls the gate"))
    from tortoise.record_writer import use_record_tenant

    # An AMBIENT tenant that is NOT the one this run declared, so a downgrade is visible.
    with use_record_tenant("org-B", "graph-B"):
        emits, reason = strip.write_gate(
            "object",
            graph_population=strip.graph_vector_population(_Projection(graph)),
            org_id=org_id, graph_id=graph_id,
            record_dsn="postgresql://localhost/record")

    assert emits is True, (
        "a half tenant declaration was answered from the AMBIENT tenant's record — one "
        f"tenant's strip certified from another tenant's measurement: {reason}")
    # ⛔ AND THE ACCOUNT IS THE DECLARATION'S, not the no-declaration sentence: a caller
    # that DID declare a tenant must not be told that "none was declared" (review round 6
    # — the refusal was right and its reason was false in both clauses).
    assert "DECLARED is not a whole" in reason, (
        f"a refused DECLARATION was reported as no declaration at all: {reason}")


def test_the_tenant_pair_reaches_the_record_query_in_the_DECLARED_ORDER_on_every_hop(
        stores, monkeypatch):
    """⛔ THE PAIR'S ORDER, AT THE POINT WHERE IT BECOMES A QUERY (review round 8).

    `_declared_scope` accepts a TRANSPOSED pair silently — both halves are non-empty
    strings — so `(graph_id, org_id)` measures a different tenant and the gate answers from
    the wrong record. Round 7 pinned that at the ONE hop it edited (`write_gate` →
    `mirror_verdict`); the same pair is transposed by every intermediate hop, and a matrix
    of the whole measurement context measured **118 passed** at each of the other seven.
    Their consequence is fail-closed under RLS (a swapped org matches no rows → unverified
    → the mirror is kept), which is why they ranked P3 — but a swap is invisible, and
    "invisible but currently harmless" is one schema change away from a wrong-tenant
    verdict.

    ⛔ ASSERTING AT THE QUERY COVERS THE WHOLE CHAIN TRANSITIVELY. The two internal
    pass-throughs (`label_write_gate` → `write_gate`, `readiness_probe` →
    `label_write_gate`) are exercised by every path below, so a transposition there moves
    the scope the count stub OBSERVES. The three entry hops are driven with `org` and
    `graph` DISTINCT strings, because equal halves cannot reveal a transposition.

    The recorder is CLEARED per hop on purpose: `ASKED_SCOPE` is module-level state, and a
    stale value from an earlier hop would make the assertion vacuous.
    """
    graph, record = stores
    graph.add("Object", "obj-1")
    record.holds("object", "obj-1")
    _fresh_record_leg(monkeypatch, record_vectors=1)
    monkeypatch.setattr(strip, "_fold_consults_gate",
                        lambda label: (True, f"every :{label} writer calls the gate"))
    census_strip = _census_strip()
    monkeypatch.setattr(census_strip, "_fold_consults_gate",
                        lambda label: (True, f"every :{label} writer calls the gate"))
    # ⛔ DISTINCT halves: `("same", "same")` is invariant under transposition.
    ORG, GRAPH = "org-A", "graph-A"

    from tortoise import record_vector

    def _fresh_hop() -> None:
        """Clear the observed scope AND the coverage cache.

        ⛔ BOTH, or the second hop onward is a CACHE HIT: the verdict for
        `(object, ("org-A","graph-A"), dsn)` is memoised by the first hop, and a cached
        verdict never calls `_record_vector_count` — so the recorder keeps the previous
        hop's scope and the assertion goes vacuous.
        """
        ASKED_SCOPE.clear()
        record_vector.reset_coverage_cache()

    def _assert_query_order(hop: str) -> None:
        asked = ASKED_SCOPE.get("scope")
        assert asked is not None, (
            f"{hop}: the record count was taken with NO scope, so the declared tenant "
            f"never reached the query ({ASKED_SCOPE!r})")
        assert (asked.org_id, asked.graph_id) == (ORG, GRAPH), (
            f"{hop}: the tenant reached the record query INTERCHANGED, so the count came "
            f"from a different tenant: {asked!r}")

    # ── hop 1: `build_plan` → `readiness_probe` → … → the query.
    _fresh_hop()
    strip.build_plan(
        g=graph, record_dsn="postgresql://localhost/record", org_id=ORG, graph_id=GRAPH,
        graph_name="g", graph_uri="docker://x/g", labels=["Object"],
        batch=strip.DEFAULT_BATCH,
        graph_population=strip.graph_vector_population(_Projection(graph)))
    _assert_query_order("preview (`build_plan`)")

    # ── hop 2: `execute`'s own live re-check probe.
    _fresh_hop()
    plan = _plan(graph, labels=["Object"])
    plan["org_id"], plan["graph_id"] = ORG, GRAPH
    _execute(graph, plan, org_id=ORG, graph_id=GRAPH, expect_narrowed=True,
             graph_population=strip.graph_vector_population(_Projection(graph)))
    _assert_query_order("destructive (`execute`)")

    # ── hop 3: the census CLI's `_write_gate` (the TOP-LEVEL module it resolves).
    _fresh_hop()
    probe = parity._write_gate(_Projection(graph), org_id=ORG, graph_id=GRAPH,
                              record_dsn="postgresql://localhost/record")
    probe("Object")
    _assert_query_order("census (`_write_gate`)")


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
    assert result["unmeasured_by_label"] == DECLARED_EXCEPT_OBJECT
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
    assert report["declared_population"] == DECLARED_LABELS
    assert report["narrowed"] is True
    text = parity.render(report, "g")
    assert "every label MEASURED (Object)" in text
    assert "NARROWED" in text
    # …and the un-measured labels are NAMED as unmeasured, never silently omitted.
    narrowed_line = text.split("NARROWED", 1)[1]
    for absent in DECLARED_EXCEPT_OBJECT:
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
    monkeypatch.setattr(strip, "write_gate",
                        lambda entity_type, **_: (False, "stopped"))
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
    assert result["unmeasured_by_label"] == DECLARED_EXCEPT_OBJECT
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
    for label, key in (("Event", "ev-1"), ("Source", "src-1"), ("Subject", "subj-1"),
                       ("Point", "pt-1")):
        graph.add(label, key)
        record.holds(label.lower(), key)
    whole = _plan(graph)
    assert sorted(whole["declared_population"]) == sorted(DECLARED_LABELS)
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

    # …and the fold ASKS THE GATE ABOUT THAT STORE (`#13626`), which is what lets the
    # label clear at all — the population fact above and this write-stop are two halves of
    # one guarantee: named while it could store, gated so the store can be declined.
    assert strip._writer_consults_gate(
        "tortoise.projection.entities", "_upsert_source", ("source",))[0] is True
    gated, why = strip._fold_consults_gate("Source")
    assert gated is True, why


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
    monkeypatch.setattr(parity, "_write_gate", lambda *a, **k: None)

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
    for label in DECLARED_LABELS:
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
    assert refused, "the refused fixture tuple is EMPTY — the loop below asserts nothing"
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
    assert accepted, "the accepted fixture tuple is EMPTY — the loop below asserts nothing"
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
    assert refused, "the refused fixture tuple is EMPTY — the loop below asserts nothing"
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
    assert accepted, "the accepted fixture tuple is EMPTY — the loop below asserts nothing"
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

    ⛔ AND ONE CONSTRUCTOR SPELLING OVER (`#13788`, F6): `fromkeys` builds the same mapping from
    `(KEYS, VALUE)` instead of a dict operand, so the include list that named
    `dict`/`defaultdict`/`Counter`/`ChainMap` did not cover it — `props |=
    dict.fromkeys(["embedding"], vec)` CERTIFIED a stop arm while `props |=
    {"embedding": vec}` was refused. The arm reads the keys literal as an ELEMENT (a bare
    string is not the key) and requires a non-constant value; these rows add the mutator, the
    writer-handed and the SET-literal positions to the ones it ships.

    ⛔ AND ONE EXPRESSION FORM OVER (`98eec2ca`). Beyond those mutators, three ways a
    mapping is REBUILT — `props = {**props, "embedding": …}`, `props = props |
    {"embedding": …}`, and the writer being HANDED either — plus the keyword mutator
    spelling (`setdefault(key="embedding", default=…)`) and a subscript keyed by a LOCAL
    bound to the property string all certified the stop arm. Each is refused now, stop-arm
    only (`_rebuild_stores_the_vector`, `_property_alias_names`), and the mention-shaped
    controls at the end keep the rule from swallowing a read: a plain literal for reporting
    is not a merge, and a constant value places nothing.

    ⛔ AND ONE WRAPPER *FAMILY* OVER (`#13788`, measured at `105b047b`). The rules answered an
    `if`-expression but not the two other spellings of the SAME conditional selection: `x or y`
    IS `x if x else y`, and `(p := x)` evaluates to `x` with no name to resolve. So
    `props |= [] or [("embedding", vec)]`, `props.update([("x", 1)] and [("embedding", vec)])`,
    `props |= {} or {"embedding": vec}`, `props.update(**({} or {"embedding": vec}))`,
    `_set(props, **({} or {"key": "embedding", "value": vec}))` and
    `props |= (p := [("embedding", vec)])` all CERTIFIED a stop arm that stores. `BoolOp` and
    `NamedExpr` now answer in every rule that answers `IfExp` — the two operand rules, the
    ``**`` value scan, and the container-candidate resolver — because a wrapper typed in one
    rule and not its twin is this series' recurring defect. Mutation: delete either `ast.BoolOp`
    arm and its rows red; delete either `ast.NamedExpr` arm and its rows red.

    ⛔ AND THE *CONTAINER* OF A CONSTANT INDEX IS A CONDITIONAL TOO: `([{"embedding": vec}] if f
    else [{}])[0]` CERTIFIED because the index arm demanded one bare literal while the
    `if`-expression arm answered a bare operand — the two met only when the literal was bare.
    `_const_index_candidates` answers EVERY branch (a store in any of them is a store), and the
    controlling narrowings are pinned in the accepted tuple: a container whose branches place
    nothing, an `or` whose operands place nothing, and a walrus over a non-store all certify.
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
        # ⛔ THE MERGE-SOURCE INCLUDE LIST MISSED TWO STDLIB SPELLINGS (`#13788` F6, measured at
        # `78cbe4da`). `types.MappingProxyType` is a mapping-like merge source (PEP 584's
        # "mapping-like objects") around a literal this check CAN read, and `dict.fromkeys`
        # returns a real dict — both merged the vector on a stop arm while being CERTIFIED,
        # beside the bare literal refused. `MappingProxyType` joins `_MAPPING_CONSTRUCTORS`;
        # `fromkeys` gets its own arm because its operands are a KEY SEQUENCE and one value.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= types.MappingProxyType({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(**types.MappingProxyType({"embedding": _encode(ev)}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = types.MappingProxyType({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(["embedding"], _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = dict.fromkeys(["embedding"], _encode(ev))
        return None
    _write(props)
""",
        # ⛔ AND THE PLAIN-ASSIGN `if…else` MAPPING HAS ITS OWN ROW (`#13788` F7): the arm in
        # `_mapping_literal_stores_the_vector` was the ONLY route for this spelling and no
        # fixture reddened it — reverting it left the suite green while re-certifying a stop
        # arm that stores.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {"embedding": _encode(ev)} if _flag(ev) else {}
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
        # ⛔ AND THE WRAPPER FAMILY HAS A SECOND SPELLING (`#13788`, measured at
        # `716991951e`). `copy.copy(x)` and `copy.deepcopy(x)` return the same new mapping as
        # `x.copy()`, so naming the METHOD instead of the SHAPE left every position open: at
        # that head all four rows below CERTIFIED while their `.copy()` twins were refused
        # (each runtime-checked to place a top-level `embedding`). Attribution is one arm:
        # `_transparent_copy_operand` is the single place that answers the family, and a
        # mutant that drops its functional branch reddens exactly these four rows.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= copy.copy({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(copy.copy({"embedding": _encode(ev)}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(**copy.deepcopy({"embedding": _encode(ev)}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = copy.deepcopy({"embedding": _encode(ev)})
        return None
    _write(props)
""",
        # ⛔ THE ``**`` KEYWORD OPERAND IS AN OPERAND (`#13788` F2, measured at `45b5f88a`).
        # Both ``**`` arms narrowed the operand to `isinstance(value, ast.Dict)`, so the one
        # transparent wrapper this branch's OWN pairs rule already follows re-opened the hole
        # the bare spelling had just closed. All three rows below CERTIFIED a stop arm that
        # places the vector while `update(**{"embedding": vec})` was refused. Attribution
        # measured one arm at a time: reverting only the mapping-query arm certifies the
        # `update(**…)` row, reverting only the helper arm certifies the `_set(**…)` row, and
        # `__setitem__(**…)` is answered by both (it certifies only when BOTH are reverted) —
        # so the first two are single-arm pins and the third pins the pair.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(**{"embedding": _encode(ev)}.copy())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(**{"embedding": _encode(ev)}.copy())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **{"embedding": _encode(ev)}.copy())
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
        # ⛔ THE OTHER OPERAND POSITIONS AND LITERAL FORMS OF THE `fromkeys` MERGE (`#13788`,
        # measured at `c54b8d4b`): the rows above pin the `|=` and rebuild spellings landed by
        # F6; these pin the mutator position, the writer-handed position, and the SET literal,
        # which build the same mapping. A keyword `value=` operand is deliberately absent —
        # `dict.fromkeys` is positional-only (`dict.fromkeys(iterable, value=None, /)`), so that
        # spelling raises and places nothing, and a row for it would assert nothing.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(dict.fromkeys(("embedding",), _encode(ev)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _write(dict.fromkeys(["embedding"], _encode(ev)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys({"embedding"}, _encode(ev))
        return None
    _write(props)
""",
        # ⛔ …AND THE `zip` HALF OF THE STATED RESIDUAL IS THE SAME STORE WHEN ITS COLUMNS ARE
        # LITERALS (`#13788`). `dict(zip(keys, values))` was disclosed as CERTIFIED because
        # "answering it needs the call's RESULT — a zip of names"; that is true of NAMES and
        # false of two literal columns, which `zip` pairs positionally. `dict(zip(("embedding",),
        # (_encode(ev),)))` is the literal mapping one call spelling over, and it CERTIFIED a
        # stop arm that stores in every operand position while the literal twin was refused —
        # 48 runtime-storing spellings measured across the wrapper/position matrix. The three
        # routes below are the three call sites (`_zip_stores_the_vector`): the `|=` arm, the
        # call's own operands, and the mapping-constructor operands — plus the indexed and the
        # `strict=` spellings, which are one constant form over.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= zip(("embedding",), (_encode(ev),))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(zip(("embedding",), (_encode(ev),)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = dict(zip(["a", "embedding"], (1, _encode(ev))))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict(zip(("embedding",), (_encode(ev),)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **dict(zip(("embedding",), (_encode(ev),))))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(zip(("embedding",), (_encode(ev),), strict=True))
        return None
    _write(props)
""",
        # …and PAIRED BY INDEX, not by column length: `zip` stops at the shorter column, so a
        # property key at an index the values column DOES reach is a store even when the two
        # literals differ in length (`zip(("embedding", "x"), (_m(),))` pairs `embedding` with
        # a live value). An equal-length requirement answered this with a CERTIFIED store.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(dict(zip(("embedding", "x"), (_m(),))))
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
        # ⛔ AND THE `**` OPERAND OF A DICT DISPLAY IS AN OPERAND TOO (`#13788` F3, measured at
        # `45b5f88a`). `_dict_stores_the_vector`'s merge recursion narrowed the `**` value to
        # `ast.Dict | ast.DictComp`, so the same one-line wrapper this branch follows in every
        # other position re-opened it one display over: all four rows below CERTIFIED while
        # their unwrapped spellings were refused (measured, and the fix re-measured against
        # all four — reverting the arm certifies every one of them).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({**{"embedding": _encode(ev)}.copy()})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {**{"embedding": _encode(ev)}.copy()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {**{"embedding": _encode(ev)}.copy()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= _collections.OrderedDict({**{"embedding": _encode(ev)}.copy()})
        return None
    _write(props)
""",
        # ⛔ …and the two halves the constructor arm did not reach: its operand may be a
        # MAPPING LITERAL rather than a pairs sequence, and the constructor family is not
        # "suffix `Dict`" — `defaultdict`/`ChainMap`/`Counter` are mappings whose names say
        # nothing, and the mapping operand need be neither first nor alone. Each of these was
        # CERTIFIED while the bare literal was refused (measured by the review of this branch,
        # which also runtime-checked that they merge).
        # ⛔ …and the constructor spellings this branch's reviews measured: the operand may be
        # neither a pairs sequence nor first nor alone (`defaultdict(list, pairs)`,
        # `OrderedDict(pairs, extra=1)`), and the constructor family is not "suffix `Dict`" —
        # `defaultdict` is a mapping whose name says nothing. Each was CERTIFIED at the tip
        # its review measured, and each was runtime-checked to merge. Two rows that first
        # landed in this block were DUPLICATES of rows already above it in this same tuple:
        # removed, because a duplicate row adds no discriminating power while it makes the
        # block's claim ("each was certified") untrue of the tree it lands on.
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
        # `elt`-shaped tuple instead of its own branch raises rather than refuses. It is
        # refused through the GENERATOR's `iter` — where the pairs literal actually is — and
        # that term is pinned below by the `**` row, which only that term can answer.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: v for k, v in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        # ⛔ …AND THE COMPREHENSION WHOSE **KEY** IS THE CONSTANT — the property need not be
        # inside a pair: `{"embedding": _encode(ev) for _x in _keys()}` merges a real
        # top-level vector and was CERTIFIED while the literal was refused (measured at
        # `45b5f88a`, and runtime-checked to leave `{"embedding": VEC}` in `props`). The same
        # form was open through `update(…)`, `dict(**…)`, a REBUILD and a UNION, so all five
        # rows are here; the controls that keep them from over-refusing live in `accepted`.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"embedding": _encode(ev) for _x in _keys()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({"embedding": _encode(ev) for _x in _keys()})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(**{"embedding": _encode(ev) for _x in _keys()})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {"embedding": _encode(ev) for _x in _keys()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = props | {"embedding": _encode(ev) for _x in _keys()}
        return None
    _write(props)
""",
        # ⛔ …AND THE UNION IS TWO-SIDED, so BOTH sides are pinned: `{…for …} | props` merges the
        # same mapping and was CERTIFIED while the right-operand spelling was refused. A
        # right-only implementation passed the suite without this row (measured by the review of
        # this branch: the mutant survived).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {"embedding": _encode(ev) for _x in _keys()} | props
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {**{"embedding": _encode(ev) for _x in _keys()}}
        return None
    _write(props)
""",
        # ⛔ …AND THE KEY NEED NOT BE THE LITERAL AT ALL. The property can be COMPUTED from
        # the pair (`{f"{k}": v …}`, `{str(k): v …}`, `{(k2 := k): v …}`, `{k + "": v …}`,
        # `{v[0]: v[1] for v in [(…)]}`) and each of those placed the top-level vector while
        # being CERTIFIED (measured, and runtime-checked). A key this check cannot read now
        # fails CLOSED whenever the source is a pairs literal it CAN read — an over-refusal of
        # a key that transforms the name away, which is cheap beside certifying a stop arm
        # that stores.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {f"{k}": v for k, v in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {str(k): v for k, v in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {v[0]: v[1] for v in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        # ⛔ …AND THE UNPACK NEED NOT BE EXACTLY TWO: `{k: v for k, v, *rest in [(…)]}` reads the
        # same pair as `(key, value, *rest)` and places the vector; a three-element source
        # (`[("embedding", vec, 1)]`) is read the same way and is NOT a "pair" to the pairs
        # rule, which mirrors what `dict([...])` accepts, so it has its own term.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: v for k, v, *rest in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: v for k, v, *rest in [("embedding", _encode(ev), 1)]}
        return None
    _write(props)
""",
        # ⛔ …AND THE PAIR-WITH-EXTRAS TERM REACHES ONLY THE `Name`-KEY PATH, so the two
        # sub-classes do not compose without a shared source walk: with a COMPUTED key the
        # fail-closed arm asked the pairs rule alone, which rejects a three-element element by
        # design — `{f"{k}": v for k, v, *rest in [("embedding", vec, 1)]}` was CERTIFIED and
        # stores (measured, runtime-checked). The source walk now asks both shapes THROUGH the
        # wrappers, so `tuple(...)`, `.copy()`, a full slice, an `if`-expression, `+ []`, a
        # comprehension over the literal and a `Starred` splice are all refused too — each of
        # those re-opened the same store one wrapper in (measured).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {f"{k}": v for k, v, *rest in [("embedding", _encode(ev), 1)]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {f"{k}": v for k, v, *rest in tuple([("embedding", _encode(ev), 1)])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {f"{k}": v for k, v, *rest in [("embedding", _encode(ev), 1)][:]}
        return None
    _write(props)
""",
        # ⛔ …AND TWO WRAPPERS THE SHARED SOURCE RESOLVER DOES NOT READ, measured CERTIFIED on
        # the incoming head: a LIST'S OWN `.copy()` and an `if`-expression. The resolver reads
        # the sources it recognises; these are sources it does not, so the wrapper walk is an
        # ADDITIVE fourth leg beside it rather than a replacement (each draft that swapped one
        # for the other narrowed acceptance somewhere else).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {f"{k}": v for k, v, *rest in [("embedding", _encode(ev), 1)].copy()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {f"{k}": v for k, v, *rest in ([("embedding", _encode(ev), 1)] if _flag(ev) else [])}
        return None
    _write(props)
""",
        # ⛔ …AND A SET LITERAL OF PAIRS IS THE SAME ITERABLE (`dict.__ior__`/`update()` take
        # any iterable of pairs), which the pairs rule excluded while `fromkeys` two rules up
        # already accepted `List | Tuple | Set` as a key sequence — measured CERTIFIED.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {("embedding", _encode(ev))}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({("embedding", _encode(ev))})
        return None
    _write(props)
""",
        # ⛔ …AND THE COMPREHENSION TWIN OF `props[alias] = …`, which the subscript arm has
        # refused since `98eec2ca`: `alias = "embedding"` then `{alias: v for k, v in [("x",
        # vec)]}` places the property, and neither the pair's constant (`"x"`) nor the target
        # names the property — the binding lives in the scope (measured CERTIFIED).
        """
def _upsert_object(props, ev):
    alias = "embedding"
    if not _derived_mirror_required("object"):
        props |= {alias: v for k, v in [("x", _encode(ev))]}
        return None
    _write(props)
""",
        # ⛔ …AND THE SAME COMPREHENSION BEHIND ONE `**` — `_dict_stores_the_vector`'s merge
        # arm accepted only an `ast.Dict` operand, so `{**<dictcomp>}` was CERTIFIED while the
        # literal behind the same `**` was refused (measured, and runtime-checked to place the
        # key). One wrapper, one hole.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {**{k: v for k, v in [("embedding", _encode(ev))]}}
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
        # ⛔ THE WRITER HANDED A REBUILT MAPPING IS THE SAME STORE (`#13788` F4, measured at
        # `45b5f88a`). `_dict_stores_the_vector`'s `{**{…}}` recursion is gated on `stop_arm`,
        # and `_rebuild_stores_the_vector` — the route that answers `_write({**props, …})` —
        # asked it with the default OFF while being reached only under `anywhere`. The
        # recursion was therefore INERT on this route: all five rows below CERTIFIED while
        # `_write({**props, "embedding": vec})` directly above was refused.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _write({**{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _write({**{"embedding": _encode(ev)}.copy()})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _write({**props, **{"embedding": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = props | {**{"embedding": _encode(ev)}}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _write(props | {**{"embedding": _encode(ev)}})
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
        # ⛔ THE HELPER'S `key`/`value` PAIR IS THE SAME PAIR THROUGH A WRAPPER (`#13788` F5,
        # measured at `45b5f88a`). The `**` operand's VALUE scan read only a top-level
        # `ast.Dict` literal, so the row above (refused) certified in every one-wrapper
        # spelling of itself — the same wrapper vocabulary the store test already follows.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **{"key": "embedding", "value": _encode(ev)}.copy())
        return None
    _write(props)
""",
        # ⛔ AND THE WRAPPER'S FUNCTIONAL SPELLING IS THE SAME PAIR (`#13788`, measured at
        # `716991951e`): `copy.copy(…)` / `copy.deepcopy(…)` return the same new mapping as
        # `.copy()`, so the scan that named only the METHOD left them reading as an
        # unreadable operand and both rows below CERTIFIED beside the refused `.copy()` row
        # above. `_transparent_copy_operand` is the one arm that answers the family; the
        # `_read(…)` control beside it keeps a wrapped READ refused-free.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **copy.copy({"key": "embedding", "value": _encode(ev)}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **copy.deepcopy({"key": "embedding", "value": _encode(ev)}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **{"key": "embedding", "value": _encode(ev)} if _flag(ev) else {})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **{**{"key": "embedding", "value": _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **dict(key="embedding", value=_encode(ev)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **({"key": "embedding", "value": _encode(ev)} | {}))
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
        # ⛔ THE UNBOUND `dict.setdefault(props, …)` ROW IS PINNED ABOVE, ONCE (`#13788`, the
        # review's dead-weight note). It appeared TWICE, byte-identical, so one of the two
        # asserted nothing the other did not — a duplicate row is dead weight in a tuple whose
        # whole purpose is to be read as the vocabulary.
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
        # ⛔ …AND THE MAPPING OPERAND'S **`if…else`** WRAPPER, which the rows above (the
        # `.copy()` family, added with `#13788` F1) do not cover, and which the pairs form
        # ALREADY refuses (`||= [("embedding", vec)] if f else []`) — so the mapping form
        # was refused for `.copy()` and still CERTIFIED for `if…else` (measured at the review
        # of this branch). An `if`-expression picks one of two MAPPINGS, so it places exactly
        # what the bare literal places, in both the merge (`|=`) and the mutator (`update`)
        # positions. Rows for the operand forms already present are NOT repeated here: a
        # duplicate row adds no discriminating power (`#13788` merge).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = _collections.OrderedDict({"embedding": _encode(ev)}.copy())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"embedding": _encode(ev)} if _flag(ev) else {}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({"embedding": _encode(ev)} if _flag(ev) else {})
        return None
    _write(props)
""",
        # ⛔ …AND A UNION WHOSE MERGE OPERAND IS WRAPPED IS THE SAME STORE (`#13788` F4).
        # The bare `_rebuild_stores_the_vector` display merge is now read in STOP mode (the
        # `stop_arm=True` token, pinned by its own five rows above), and the union arm itself
        # still asked two TYPE TESTS for its operands — so a `.copy()` or an `if…else` around
        # the mapping inside a union CERTIFIED beside the bare operand refused:
        #     props = ({"embedding": vec}.copy()) | {}          CERTIFIED
        #     props = {} | ({"embedding": vec}.copy())          CERTIFIED
        #     props = ({"embedding": vec} if f else {}) | {}     CERTIFIED
        # Measured: each is CERTIFIED with the union arm left as two type tests and refused
        # once its operands are answered by `_mapping_operand_stores_the_vector` — the operand
        # rule, which carries the stop-mode Dict recursion, the comprehension, the container
        # arms and BOTH wrappers. The union rows for the unwrapped `**` merge stay here too:
        # they were the measured CERTIFIED spellings at the head this was found at, and they
        # are refused by the same one-rule answer.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = ({"embedding": _encode(ev)}.copy()) | {}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {} | ({"embedding": _encode(ev)}.copy())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = ({"embedding": _encode(ev)} if _flag(ev) else {}) | {}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {**{"embedding": _encode(ev)}} | {}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {**{"embedding": _encode(ev)}} | {}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {**{"embedding": _encode(ev)}}.copy() | {}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({**{"embedding": _encode(ev)}} | {})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {**{"embedding": _encode(ev) for _x in _keys()}} | {}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {**{"embedding": _encode(ev)}} | {"embedding_model": _m()}
        return None
    _write(props)
""",
        # ⛔ …AND A CONSTANT INDEX INTO A CONTAINER LITERAL IS THE ELEMENT ITSELF (`#13788` F4).
        # The slice arm followed `[pairs][:]` but not `[pairs][0]`, so every `[<mapping>][<n>]`
        # spelling CERTIFIED a stop arm that stores — seven measured rows here, each
        # runtime-checked to leave `{"embedding": VEC}` in `props`, including the ASSIGN arm
        # (`props = [{"embedding": vec}][0]`), the `.copy()`-wrapped element, the pairs-shaped
        # element, and the NEGATIVE index (`[-1]` is `UnaryOp(USub, Constant(1))`, not
        # `Constant(-1)` — the "one constant spelling over" shape that had to be read through
        # `_constant_index` to be caught at all). The index must be CONSTANT and the container
        # a List/Tuple LITERAL; an index that is a name or an expression stays the stated
        # data-flow residual, and the control for it is in the accepted block below.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [{"embedding": _encode(ev)}][0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([{"embedding": _encode(ev)}][0])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = [{"embedding": _encode(ev)}][0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = [{"embedding": _encode(ev)}.copy()][0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([[("embedding", _encode(ev))]][0])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict([{"embedding": _encode(ev)}][0])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([{"embedding": _encode(ev)}][-1])
        return None
    _write(props)
""",
        # ⛔ …AND THE CONTAINER REACHED THROUGH A WRAPPER — the container arms and the index
        # arm landed in the same round and only met when the literal was BARE (`#13788`,
        # measured at `40db94f1`). `[<mapping>][0:][0]` (a FULL slice spelled with a lower
        # bound, `Slice(lower=Constant(0))` — not the bare `[:]` the first fix answered),
        # `list([<mapping>])[0]`, and the `.items()` view (or its comprehension) of a mapping
        # literal all place the vector and all CERTIFIED while the bare spellings were
        # refused. One helper (`_container_literal`) answers the container wrappers for BOTH
        # operand rules, so the two cannot answer the same spelling differently.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([{"embedding": _encode(ev)}][0:][0])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(list([{"embedding": _encode(ev)}])[0])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({k: v for k, v in {"embedding": _encode(ev)}.items()})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({"embedding": _encode(ev)}.items())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= tuple([{"embedding": _encode(ev)}][::1])[0]
        return None
    _write(props)
""",
        # ⛔ THE VECTOR CAN BE THE COMPREHENSION'S **PRODUCED VALUE**, not the pair's second
        # element (`#13788`, measured by the review of this branch). Every arm hunted the
        # vector inside the SOURCE's pair, so a shape with no pair at all — and the spellings
        # that put the property in the pair and the VECTOR in the value — were CERTIFIED while
        # the literal spelling was refused. Each was runtime-checked to leave
        # `{'embedding': VEC}` in `props`:
        #
        #     props |= {k: _encode(ev) for k in ["embedding"]}
        #     props |= {k: _encode(ev) for k, _i in [("embedding", 1)]}
        #     props |= {v: _encode(ev) for _i, v in [(1, "embedding")]}
        #     props |= {f"{k}": _encode(ev) for k, _i in [("embedding", 1)]}
        #
        # ⛔ THE F-STRING ROW IS WHY THE TERM ASKS *MENTIONS*, NOT *IS*: a `Name`-only version
        # of `_comprehension_key_mentions_the_bound_property` refused the first three and
        # ACCEPTED this one (measured).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ["embedding"]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _i in [("embedding", 1)]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {v: _encode(ev) for _i, v in [(1, "embedding")]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {f"{k}": _encode(ev) for k, _i in [("embedding", 1)]}
        return None
    _write(props)
""",
        # ⛔ AND A PAIR WITH EXTRAS IS NOT NAMED-KEY ONLY (`#13788`, measured by the review of
        # this branch). The extras term was added for the extras shape by the same commit that
        # shipped this branch's key fixes, and wired only into the `Name` branch — so a
        # non-`Name` key read the SAME extras pair differently:
        #
        #     props |= {v[0]: v[1] for v in [("embedding", _encode(ev), 1)]}          <- CERTIFIED
        #     props |= {v[0]: v[1] for v in [("embedding", _encode(ev))]}             <- refused
        #
        # Measured CERTIFIED in the `|=`, `update(…)` and plain-assign positions, each
        # runtime-checked to place the vector. The term now lives in the ONE resolver the two
        # comprehension questions share (`_comprehension_source_pairs_the_property_with_a_value`).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {v[0]: v[1] for v in [("embedding", _encode(ev), 1)]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({v[0]: v[1] for v in [("embedding", _encode(ev), 1)]})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {v[0]: v[1] for v in [("embedding", _encode(ev), 1)]}
        return None
    _write(props)
""",
        # ⛔ THE VALUE-SIDE TERM NEEDS THE WRAPPER VOCABULARY TOO (`#13788`, measured by the
        # review of this branch). The bare literal was answered while one TRANSPARENT wrapper
        # — the exact shape this branch's `.copy()` history records — re-opened the hole:
        #
        #     props |= {k: _encode(ev) for k in ["embedding"]}      <- refused
        #     props |= {k: _encode(ev) for k in ["embedding"][:]}   <- ACCEPTED
        #     props |= {k: _encode(ev) for k in [*["embedding"]]}   <- ACCEPTED
        #
        # A SET and a TUPLE source are refused too, so the row is not list-only.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ["embedding"][:]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in [*["embedding"]]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ("embedding",)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"embedding"}}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in list(["embedding"])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in tuple(["embedding"])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ["embedding"] + []}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ["embedding"] * 1}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in set(["embedding"])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"embedding": 1}}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"embedding": 1}.keys()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict.fromkeys(["embedding"])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _i in {"embedding": 1}.items()}
        return None
    _write(props)
""",
        # ⛔ AND THE `.items()` RECEIVER'S OWN WRAPPERS ARE THE SAME VIEW (`#13788` review
        # round 6, P1 — a measured in-surface false PASS). The arm read a BARE mapping
        # literal, so each ONE-WRAPPER spelling of the row above CERTIFIED while the bare
        # twin was refused, and the vector is placed by the comprehension's own value
        # expression, so no other rule reads it:
        #
        #     {k: _encode(ev) for k, _v in {'embedding': 1}.copy().items()}          CERTIFIED
        #     … in ({'embedding': 1} if _f() else {}).items()                        CERTIFIED
        #     … in {**{'embedding': 1}}.items()                                      CERTIFIED
        #
        # `_mapping_items_pairs` is the one resolver for that receiver; the wrappers here are
        # the ones `_transparent_copy_operand`, `_conditional_branches` and the `**` merge
        # already own. The controls for it are in the `accepted` tuple below (a non-property
        # key and a constant value must still certify), which is what keeps the arm from
        # becoming an inert enforcer in the other direction.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {"embedding": 1}.copy().items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        import copy
        props |= {k: _encode(ev) for k, _v in copy.copy({"embedding": 1}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in ({"embedding": 1} if _f() else {}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {**({"embedding": 1})}.items()}
        return None
    _write(props)
""",
        # ⛔ AND A MAPPING CONSTRUCTOR OVER A MAPPING IS THE SAME `.items()` VIEW (`#13788`,
        # measured at `bb16c4b2de` — the round-6 fix's own residue, one spelling family over).
        # `dict(mapping)` is a shallow copy, so its `.items()` is `mapping.items()`, and the
        # module ALREADY reads a single-positional `_is_mapping_constructor(...)` call as a
        # keys source at the twin seam (`_comprehension_source_yields`), so the bare iteration
        # was REFUSED while the `.items()` view CERTIFIED:
        #
        #     {k: _encode(ev) for k in     OrderedDict({"embedding": 1})}              refused
        #     {k: _encode(ev) for k, _v in OrderedDict({"embedding": 1}).items()}      CERTIFIED
        #
        # The include list is `_is_mapping_constructor` (the one the operand side owns), and
        # the KEYWORD form is answered beside it for the twin seam's own reason:
        # `dict(embedding=1).items()` yields `("embedding", 1)` exactly as `dict(embedding=1)`
        # iterates `"embedding"`. The controls are in the `accepted` tuple below.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict({"embedding": 1}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in OrderedDict({"embedding": 1}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in MappingProxyType({"embedding": 1}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict(embedding=1).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict(OrderedDict({"embedding": 1})).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in [x for x in ["embedding"]]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _i in [("embedding", 1)] + []}
        return None
    _write(props)
""",
        # ⛔ AND THIS ROW'S REFUSAL COMES FROM THE LEGS **ABOVE**, NOT FROM THE FOURTH ONE. The
        # attribution that stood here ("`#13788`, measured by dropping the leg and replaying
        # every row: exactly this row moved") was STALE, and a stale "this row pins that leg"
        # note is how the next lane re-widens what the leg was protecting (`#13788` round 12;
        # re-measured for the review of PR #13928, then again by the test beside this tuple). A
        # non-`Name` key over a `set(...)` WRAPPER is a source the PAIRS RULE DOES read —
        # `set(...)` is a Call, and `_pairs_operand_stores_the_vector`'s Call arm unwraps it
        # through `_container_call_operand` — and leg 3 answers it too, one leg before
        # `_source_holds_the_store_anywhere` (the fourth leg) is ever asked:
        #
        #     {v[0]: v[1] for v in set([("embedding", _encode(ev))])}    <- refused by leg 1/3
        #
        # runtime-checked to leave the top-level vector in `props`.
        # Measured, not reasoned: deleting the FOURTH leg moves NO row of this tuple (the test
        # still passes), while deleting legs 1+3 makes exactly this row certify. So the row pins
        # that the `set(...)` wrapper is read at all — worth having — but it does not pin the
        # fourth leg, and nothing in this tuple does.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {v[0]: v[1] for v in set([("embedding", _encode(ev))])}
        return None
    _write(props)
""",
        # ⛔ THE PAIR-WITH-EXTRAS BEHIND A FILTERED COMPREHENSION (PR #13928 review, P1). This is
        # the class the fourth leg's own docstring says it exists for — `(…, 1)` — and the class
        # its exclusion sample never asked: the wrapper re-emits the 3-tuple, the comprehension's
        # `if` clause makes the shared source resolver abstain on the BARE literal, and `v[0]`/
        # `v[1]` then place the top-level vector.
        # ⛔ BUT THESE THREE ROWS SPELL THE FILTER DIRECTLY IN THE CLAUSE, and THIS FORM is refused
        # by another term of the `|=` chain with or without the fourth arm (measured end to end at
        # `085c815866`: all three refused while the arm was subtracting four names). So they pin
        # the CLASS, not the arm — the arm's own witness is the two NESTED spellings below, which
        # is where the first repair round's "the disposition moves 0 of 60 rows" came from and why
        # that reading was a sample artefact: the sample held no nested wrapper.
        # Non-vacuity measured, not assumed: with the non-`Name`-key chain of
        # `_pairs_operand_stores_the_vector` disabled, ALL 60 rows of this sample CERTIFY through
        # `_writer_consults_gate` (so none of these rows is vacuous), and with the arm's four-name
        # subtraction restored the two NESTED spellings below certify — the false PASS this tuple
        # pins. A licence whose sample omits the shape it names is how the exclusion looked safe.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {v[0]: v[1] for v in sorted([("embedding", _encode(ev), 1)]) if v}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {v[0]: v[1] for v in set([("embedding", _encode(ev), 1)]) if v}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _i in iter([("embedding", 1, 1)]) if k}
        return None
    _write(props)
""",
        # ⛔ AND THE **NESTED** SPELLINGS — THE ROWS THAT PIN THE FOURTH ARM ITSELF (PR #13928
        #    review round 3, measured at `085c815866` vs the arm restored). A comprehension over a
        #    comprehension: the INNER one carries the `if` / the transforming element, so the
        #    shared SOURCE resolver sees a `ListComp` it answers with `None` (identity + unfiltered
        #    only), and the pairs rule sees a 3-tuple (exactly two). Nothing upstream answers it,
        #    so the fourth arm is the SOLE decider — and while it subtracted the four family names
        #    these two writers were CERTIFIED (gated True) and the vector reached `props`:
        #
        #      leg1 False / leg3 False / leg4 False  <- false PASS, `085c815866`
        #      leg1 False / leg3 False / leg4 True   <- refused, here
        #
        #    Measured, both heads, same fixture bodies, only the arm's disposition moving. These
        #    two rows plus `test_the_fourth_legs_container_arm_asks_the_whole_family` are the
        #    non-vacuity proof: re-add
        #    `_callee_name(node) not in _CONTAINER_CALLS_ANSWERED_UPSTREAM` and both CERTIFY.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {v[0]: v[1] for v in [p for p in sorted([("embedding", _encode(ev), 1)]) if p]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {v[0]: v[1] for v in [p[0:2] for p in reversed([("embedding", _encode(ev), 1)])]}
        return None
    _write(props)
""",
        # ⛔ …AND THE OTHER TWO SPELLINGS OF THE SAME CONDITIONAL WRAPPER (`#13788`, measured
        # at `105b047b`). `x or y` is `x if x else y` — the wrapper the rules already follow —
        # and `(p := x)` IS `x`. Both CERTIFIED on every operand form (pairs, mapping, the
        # ``**`` keyword pair, the rebuild assignment) while the `if`-expression twin beside
        # them was refused. `BoolOp` and `NamedExpr` are therefore answered in every rule that
        # answers `IfExp`; these rows pin the pairs and mapping rules, the ``**`` value scan
        # and the assignment arm.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [] or [("embedding", _encode(ev))]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([("x", 1)] and [("embedding", _encode(ev))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {} or {"embedding": _encode(ev)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {} or {"embedding": _encode(ev)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(**({} or {"embedding": _encode(ev)}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **({} or {"key": "embedding", "value": _encode(ev)}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= (p := [("embedding", _encode(ev))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= (p := {"embedding": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = (p := {"embedding": _encode(ev)})
        return None
    _write(props)
""",
        # ⛔ …AND A CONSTANT INDEX INTO A *CONDITIONAL* CONTAINER (`#13788`, measured at
        # `105b047b`): the const-index arm and the `if`-expression arm were both implemented and
        # only met when the literal was BARE. Either branch may be the one selected, so a store
        # in any of them refuses — and the `or` spelling of the same container follows.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= ([{"embedding": _encode(ev)}] if _f() else [{}])[0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= ([{"embedding": _encode(ev)}] or [])[0]
        return None
    _write(props)
""",

        # ⛔ AND THE RESOLVER'S OWN FOUR ARMS (`#13788`, measured by a fix lane at `b3f06c38`).
        # The resolver carries the transparent-container vocabulary the operand rules use, and
        # yet answered neither the TRANSPARENT COPY nor the `if…else` of its own source, nor a
        # MAPPING reached through a builder / a union / a constant index. Every row below was
        # CERTIFIED before its arm existed, each runtime-checked to leave `{"embedding": VEC}`
        # in `props`:
        #
        #     {k: _encode(ev) for k in ["embedding"].copy()}                      <- CERTIFIED
        #     {k: _encode(ev) for k in copy.copy(["embedding"])}                  <- CERTIFIED
        #     {k: _encode(ev) for k in (["embedding"] if _flag(ev) else [])}      <- CERTIFIED
        #     {k: _encode(ev) for k in ChainMap({"embedding": 1})}                <- CERTIFIED
        #     {k: _encode(ev) for k in dict({"embedding": 1})}                    <- CERTIFIED
        #     {k: _encode(ev) for k in ({"embedding": 1} | {})}                   <- CERTIFIED
        #     {k: _encode(ev) for k in [{"embedding": 1}][0]}                     <- CERTIFIED
        #     {k: _encode(ev) for k in dict(embedding=1)}                          <- CERTIFIED
        #
        # ⛔ THE `dict({…})` ROW IS THE ONE THE RESOLVER'S OWN NOTE CALLED REDUNDANT. It is not:
        # the inner literal's VALUE is a CONSTANT (`{"embedding": 1}`), so no store rule reads it,
        # and the store is made by the comprehension's own value expression. The constructor rule
        # answers a DIFFERENT question (does the constructor's operand STORE the vector), so the
        # claim is now a fixture instead of a sentence.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ["embedding"].copy()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in copy.copy(["embedding"])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in (["embedding"] if _flag(ev) else [])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ChainMap({"embedding": 1})}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict({"embedding": 1})}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ({"embedding": 1} | {})}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in [{"embedding": 1}][0]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict(embedding=1)}
        return None
    _write(props)
""",
        # …and TWO OTHER POSITIONS: the arms are in the resolver, which every position asks, so
        # a row in one position alone would not show that.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({k: _encode(ev) for k in ChainMap({"embedding": 1})})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {k: _encode(ev) for k in ["embedding"].copy()}
        return None
    _write(props)
""",
        # ⛔ AND THE WRAPPER *ORDER* CANNOT MATTER (`#13788`, measured at `6a164ac9`). The
        # container resolver delegated its terminal arm to `_container_literal`, so a
        # conditional OUTSIDE a slice was answered while a wrapper INSIDE the conditional was
        # not — the same fix, one nesting level over, and the doubly-indexed row is the same
        # gap twice. Every wrapper is now resolved in one place (`_conditional_branches`), so
        # the nesting order stops mattering; each row below placed a real top-level vector.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= ([{"embedding": _encode(ev)}] if _f() else [{}])[0:][0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= list([{"embedding": _encode(ev)}] if _f() else [{}])[0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(([{"embedding": _encode(ev)}] if _f() else [{}])[0:][0])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= ([([{"embedding": _encode(ev)}] if _f() else [{}])] if _g() else [[]])[0][0]
        return None
    _write(props)
""",
        # ⛔ AND THE SAME WRAPPER IN THE THREE OTHER RESOLVERS (`#13788`, measured at
        # `6a164ac9`). Each carried its own copy of the vocabulary — the comprehension-source
        # resolver (`{k: vec for k in …}`), the zip arm and the `Starred` splice — and each had
        # answered only its BARE spelling, so one conditional around it CERTIFIED while the bare
        # twin was refused. `_conditional_branches` is now the one definition all of them ask,
        # which is why these rows exist beside the container ones: three copies had three holes.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in (["embedding"] if _f() else ["x"])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ([] or ["embedding"])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= (zip(("embedding",), (_encode(ev),)) if _f() else zip((), ()))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= (None or zip(("embedding",), (_encode(ev),)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *(["embedding", _encode(ev)] if _f() else []))
        return None
    _write(props)
""",
        # ⛔ AND THE SPLICE IS NOT ONE LEVEL DEEP (`#13788`, measured at `794eb162`). It answered
        # a bare literal and then the conditional spellings, while every OTHER wrapper the pairs
        # rule already resolves still CERTIFIED beside its refused bare twin — `list()`/`tuple()`,
        # `.copy()`, `+`, a full slice, a `Starred` element, and the conditional of a `Starred`.
        # The resolver (`_sequence_options`) is what the splice path (`_argument_expansions`) asks,
        # so a splice and the operand rule cannot answer one spelling differently.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *list(["embedding", _encode(ev)]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *tuple(["embedding", _encode(ev)]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *(["embedding", _encode(ev)].copy()))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *(["embedding", _encode(ev)] + []))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *(["embedding", _encode(ev)][:]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *[*["embedding", _encode(ev)]])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *([*["embedding", _encode(ev)]] if _f() else []))
        return None
    _write(props)
""",
        # ⛔ AND THE COMPREHENSION SOURCE RESOLVER HAD TWO POLICIES FOR ONE WRAPPER (`#13788`,
        # measured at `794eb162`): its `BoolOp` arm answered `None` when ANY branch was
        # unreadable while its `if…else` arm unions the readable branches — so the SAME
        # conditional got two answers (`["embedding"] or _maybe` CERTIFIED, `["embedding"] if f
        # else _maybe` refused). `None` now means EVERY branch is unreadable.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in (["embedding"] or _maybe)}
        return None
    _write(props)
""",
        # ⛔ AND ITS CONST-INDEX ARM ASKED `_container_literal`, THE ONE RESOLVER WITH NO
        # CONDITIONAL VOCABULARY (`#13788`, measured at `794eb162`) — the arm's own note claimed
        # those were "the ONE answer the operand rules already give this spelling", which is
        # measurably false. It asks `_const_index_candidates` now.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ([{"embedding": 1}] if _f() else [{"x": 1}])[0]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ([{"embedding": 1}] or [{"x": 1}])[0]}
        return None
    _write(props)
""",
        # ⛔ AND THE VOCABULARY WAS STILL FOUR COPIES, MEASURED AS A MATRIX (`#13788`, measured at
        # `6504b28f`). A 1853-legal-row sweep with a RUNTIME ORACLE found 451 stop-arm false
        # PASSes (27.2 %) and 0 wrong refusals, all of it one root: `_container_literal`,
        # `_const_index_candidates` and the zip arm's type test each
        # knew a different subset (and the splice flattener, `_spliced_sequence_elements`, was a
        # further copy — since DELETED, because `_argument_expansions` spliced without it). They
        # are one resolver now (`_sequence_options`), and these rows
        # are the five classes that sweep named — every one refused beside its honest control.
        #
        # F1: the zip arm TYPE-TESTED each column instead of resolving it (320 of the 451).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(zip(list(("embedding",)), (_encode(ev),)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(zip(("embedding",), (_encode(ev),)[:]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= zip(("embedding",) if _f() else ("x",), (_encode(ev),))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= zip((_p := ("embedding",)), (_encode(ev),))
        return None
    _write(props)
""",
        # F2: the container-candidate resolver ignored `.copy()`, `+` and `*` (108 rows).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= ([{"embedding": _encode(ev)}]).copy()[0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= ([{"embedding": _encode(ev)}] + [])[0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= (2 * [{"embedding": _encode(ev)}])[0]
        return None
    _write(props)
""",
        # F3: the splice missed a CONSTANT INDEX — one spelling over from the round before (15).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *[["embedding", _encode(ev)]][0])
        return None
    _write(props)
""",
        # F4: a `{**X}` comprehension source was given up on as unreadable (2).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {**{"embedding": 1}}}
        return None
    _write(props)
""",
        # F5: the pairs rule named `list`/`tuple` but not `set(...)` (6 rows) — while its own
        # `_pairs_store_the_vector` docstring says a SET literal is an iterable of pairs, and
        # three sibling arms already name `set`.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= set([("embedding", _encode(ev))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(set([("embedding", _encode(ev))]))
        return None
    _write(props)
""",
        # ⛔ AND THE PROPERTY'S OWN STRING BOUND TO A NAME IS THE SAME KEY, IN EVERY SPELLING
        # (`#13788` F11, measured at `6504b28f9e`). `_keyed_by_alias` (subscript) and
        # `_dict_comp_keyed_by_alias` (comprehension) resolved it since `98eec2ca`, but every
        # rule that reads a dict LITERAL took only `ast.Constant` — so with `key = "embedding"`
        # bound inside the writer, `props[key] = vec` was REFUSED while `props.update({key:
        # vec})`, `|=`, `=`, `{**{…}}`, `__setitem__`, `setdefault`, the pairs form and a
        # constructor operand were all CERTIFIED on a stop arm that stores (each runtime-checked
        # to leave `props["embedding"]`). One key rule (`_names_property_key`), threaded through
        # the mapping/pairs/operand/constructor rules, answers them all.
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props.update({key: _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props |= {key: _encode(ev)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props = {key: _encode(ev)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props.update({**{key: _encode(ev)}})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props.__setitem__(key, _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props.setdefault(key, _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props.update([(key, _encode(ev))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props.update(OrderedDict({key: _encode(ev)}))
        return None
    _write(props)
""",
        # ⛔ AND A `zip` OF TWO LITERAL COLUMNS IS THE SAME PAIR ONE CONSTANT INDEX IN
        # (`#13788` F12, measured at `6504b28f9e`): `_zip_stores_the_vector` answered a
        # conditional but not the container-index vocabulary the two operand rules already
        # resolve, so `[…][0]` / `list([…])[0]` CERTIFIED beside the bare operand refused.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([zip(("embedding",), (_encode(ev),))][0])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [zip(("embedding",), (_encode(ev),))][0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(list([zip(("embedding",), (_encode(ev),))])[0])
        return None
    _write(props)
""",
        # ⛔ THE SPLICE PATH'S OWN USE OF THE REPETITION ARM (`#13788` F13). ⚠️ THE ARM WAS
        # ALREADY PINNED, and this comment used to say otherwise: deleting `_sequence_options`'
        # `ast.Mult` arm reddens the PRE-EXISTING row `props |= (2 * [{"embedding": …}])[0]`
        # (measured by the review of `86d6c74a5`), so "reached by no row / the suite stayed
        # green" was false. What these two rows add is the SPLICE path: the splice flattener
        # (`_argument_expansions`) asks `_sequence_options`, and a `Starred` around a repetition
        # reaches the arm through that projection rather than through the const-index route the
        # pre-existing row uses. ⚠️ That flattener is `_argument_expansions`, NOT the deleted
        # `_spliced_sequence_elements` this comment named before — measured by the review of
        # `9b19001eb`: emptying that helper's body flips 0 of 351 refused rows. ⛔ AND THE ROW MUST BE THE **SPLIT** FORM (`*["embedding", v]` splices to TWO
        # arguments): the pair-tuple form `*[("embedding", v)]` is refused by the pairs rule
        # whether the splice flattens it or not (measured), so it would pin nothing here.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *(["embedding", _encode(ev)] * 1))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *(1 * ["embedding", _encode(ev)]))
        return None
    _write(props)
""",
        # ⛔ EVERY EXPANSION, NOT THE FIRST ONE (`#13788` F14, measured at `9b19001eb`).
        # `_third_argument_stores_the_vector` narrowed the argument list to `expansions[0]`
        # before its positional-keyword leg, so the verdict turned on BRANCH ORDER: with the
        # STORING branch second, the leg read `[]` and the `value=` keyword carried the vector
        # into a call that was CERTIFIED (runtime-checked: `_f()` falsy leaves exactly
        # `_set(props, "embedding", value=VEC)`). The row below is that spelling; its twin one
        # branch over was already refused, which is what made the hole invisible.
        # ⇒ the mutant it kills: `named_positionally = any(… for argument in expansions[0])`.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, *([] if _f() else ["embedding"]), value=_encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(props, *([] if _f() else ["embedding"]), value=_encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.setdefault(*([] if _f() else ["embedding"]), default=_encode(ev))
        return None
    _write(props)
""",
        # ⛔ AND THE KEY RULE MUST REACH EVERY **CHANNEL** THE PROPERTY TRAVELS, not only the
        # positional and literal ones (`#13788` F11 seam, measured by the review of
        # `86d6c74a5`). The keyword legs and the pair-with-extras legs kept the inline
        # `ast.Constant` test while the positional legs moved to `_names_property_key`, so
        # `_set(key=key, value=vec)` and `{k: v for k, v, _z in [(key, vec, 1)]}` CERTIFIED
        # beside their literal twins refused — the same key, one channel over. Each of these
        # was runtime-checked to place `props["embedding"]`.
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props._set(key=key, value=_encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props._set(x, **{"key": key, "value": _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props.setdefault(key=key, default=_encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props |= {k: v for k, v, _z in [(key, _encode(ev), 1)]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props |= {k: v for k, v, _z in [(key, _encode(ev), 1)].copy()}
        return None
    _write(props)
""",
        # ⛔ AND THE ALIAS SET IS CLOSED OVER ITS OWN NAMES (`#13788` F19, measured at
        # `10796bf7f`): collecting only `x = <Constant>` answered about the SECOND hop — with
        # `key = "embedding"` and `also = key` bound in the writer, every channel below
        # CERTIFIED while the same spellings through `key` were refused (each runtime-checked
        # to leave `props["embedding"]`). The binding is still a static local of THIS scope.
        """
def _upsert_object(props, ev):
    key = "embedding"
    also = key
    if not _derived_mirror_required("object"):
        props.update({also: _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    also = key
    if not _derived_mirror_required("object"):
        props[also] = _encode(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    a = b = key
    if not _derived_mirror_required("object"):
        props |= {a: v for k, v in [("x", _encode(ev))]}
        return None
    _write(props)
""",
        # ⛔ AND A TUPLE-UNPACK TARGET TAKES THE VALUE AT ITS OWN INDEX (`#13788` F23, measured
        # by the review of `9ddcaa4f`): `x, _z = key, 1` binds the property to `x`, and it bound
        # NOTHING while the closure filtered a tuple target out — 8/8 channels CERTIFIED a stop
        # arm that stores `props["embedding"]`. ⚠️ AND THE MIRROR IMAGE IS NOT AN ALIAS:
        # `x = (key, 1)` binds the TUPLE, places the key `("embedding", 1)` and stores no
        # `props["embedding"]`, so it must keep certifying (see the accepted rows).
        """
def _upsert_object(props, ev):
    key = "embedding"
    x, _z = key, 1
    if not _derived_mirror_required("object"):
        props[x] = _encode(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    x, _z = key, 1
    if not _derived_mirror_required("object"):
        props.update({x: _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    x, _z = key, 1
    if not _derived_mirror_required("object"):
        props |= {x: _encode(ev)}
        return None
    _write(props)
""",
        # ⛔ AND A `Starred` TARGET IS PAIRED BY PYTHON'S OWN RULE (`#13788`, review of
        # `4748044f7d`). Dropping pairing for EVERY starred target — the fix for
        # `*_r, x = 1, key, 2`, which binds `x = 2` and must CERTIFY — also dropped THREE
        # CORRECT refusals the previous commit had, because each of these binds the property to
        # a simple target. The rule used instead: elements BEFORE the star take the LEADING
        # values, elements AFTER it take the TRAILING ones. The `*_r, x = 1, 2, key` row is the
        # shape the earlier fix lost; the two MIS-PAIR CONTROLS live in the ACCEPTED tuple —
        # `*_r, x = 1, key, 2` (binds `x = 2`) and `x, *_r = 1, key` (binds `x = 1`) — so an
        # over-refusal regression in this new code cannot ship green.
        """
def _upsert_object(props, ev):
    key = "embedding"
    x, *_r = key, 1
    if not _derived_mirror_required("object"):
        props[x] = _encode(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    x, *_r = key, 1, 2
    if not _derived_mirror_required("object"):
        props.update({x: _encode(ev)})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    *_r, x = 1, 2, key
    if not _derived_mirror_required("object"):
        props |= {x: _encode(ev)}
        return None
    _write(props)
""",
        # ⛔ AND EACH `ast.Tuple | ast.List` UNION IN THAT PAIRING NEEDS BOTH HALVES PINNED
        # (`#13788`, measured by this lane at `079a2e69`). The union is ONE `isinstance` guard in
        # `_property_alias_names`, and nothing exercised its LIST half: narrowing the TARGET guard
        # to `ast.Tuple` red **0 of the 474 fixture rows** and CERTIFIED `[*_r, x] = 1, key`;
        # narrowing the VALUE guard the same way red **0** and CERTIFIED `x, *_r = [key, 1]`.
        # Both bind `x = key` and store `props["embedding"]`, so an inert mutation there is a
        # false PASS — the same unpinned-direction shape as the two MIS-PAIR controls above, one
        # spelling over. One row per half, each reddened by exactly its OWN mutant and by neither
        # of the others.
        """
def _upsert_object(props, ev):
    key = "embedding"
    [*_r, x] = 1, key
    if not _derived_mirror_required("object"):
        props[x] = _encode(ev)
        return None
    _write(props)
""",
        # ⛔ AND THE SAME RULE ON THE **VALUE** SIDE, WHERE IT IS A RESIDUAL AND MUST BE PINNED
        # IN BOTH DIRECTIONS (`#13788`, measured at `bcb710a5`). A `Starred` in the VALUE
        # (`*rest`) spreads an UNKNOWN number of elements, so the flattened index of everything
        # after it depends on a binding the walk does not follow. The TAIL pairing is still
        # right-aligned against the value's trailing elements, which is why this row — the tail
        # target binds the property — is REFUSED and must stay refused: it is attributable to the
        # `if tail:` pairing (dropping that arm certifies this writer).
        """
def _upsert_object(props, ev):
    key = "embedding"
    rest = ["a", "key"]
    *_r, x = *rest, key
    if not _derived_mirror_required("object"):
        props[x] = _encode(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    x, *_r = [key, 1]
    if not _derived_mirror_required("object"):
        props.update({x: _encode(ev)})
        return None
    _write(props)
""",
        # ⚠️ A KEY EXPRESSION THAT FOLDS TO THE PROPERTY (`("embedding" if _f() else "y")`,
        # `("embedding" or "y")`, `(w := "embedding")`) is a MEASURED, DISCLOSED false PASS —
        # residual #5 of `_mutator_stores_the_vector` — not a row here. Resolving it in the key
        # rule flipped NINE mirror-arm spellings REFUSED -> GATED (VGATE, round 3): the rule is
        # asked from both arms and the direct dict test carries no polarity. A row for it would
        # be a row only the stop arm refuses, which is what made the widening invisible.

        # ⛔ AND THE SPLICE MUST KEEP THE ARGUMENT POSITION (`#13788`, measured at `fbcbf152`).
        # `_spliced_sequence_elements` (since DELETED) unioned every option, which is right for "is there a store
        # in here" and WRONG for the position-sensitive three-argument test: the union put the
        # non-storing branch first, so `"embedding"` landed at index 2 and the store CERTIFIED
        # while the bare twin was refused. Each expansion is now tested on its own.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(props, *({"x": 1} and (["embedding", _encode(ev)])))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(props, *({"x": 1} and [["embedding", _encode(ev)]][0]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(props, *([] if _f() else ["embedding", _encode(ev)]))
        return None
    _write(props)
""",
        # ⛔ AND A `*`-SPLICED ZIPCALL IS THE SAME CALL (`#13788`, measured at `fbcbf152`): the
        # arm required exactly two positional arguments, and a single `Starred` is ONE, so
        # `zip(*[("embedding",), (vec,)])` CERTIFIED one spelling from its refused twin.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= zip(*[("embedding",), (_encode(ev),)])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(zip(*[("embedding",), (_encode(ev),)]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= zip(*(("embedding",), (_encode(ev),)))
        return None
    _write(props)
""",

        # ⛔ AND THE PROPERTY'S ALIAS IS THE PROPERTY IN THE COMPREHENSION SOURCE TOO (`#13788`,
        # measured at `32f836c1`). Every other channel resolves `key = "embedding"` through
        # `_names_property_key` (that is what F11 landed), but the two key-value terms of this
        # resolver still demanded `ast.Constant` — so with the alias bound INSIDE the writer these
        # CERTIFIED while their literal twins were refused, in every position. That is the
        # asymmetry F11's own docstring claims to have closed, in the one path it did not reach.
        """
def _upsert_object(ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in (key,)}
        return None
    props["embedding"] = _encode(ev)
""",
        """
def _upsert_object(ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props.update({k: _encode(ev) for k in (key,)})
        return None
    props["embedding"] = _encode(ev)
""",
        """
def _upsert_object(ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props = {k: _encode(ev) for k in (key,)}
        return None
    props["embedding"] = _encode(ev)
""",
        """
def _upsert_object(ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props |= {v: _encode(ev) for _i, v in [("x", key)]}
        return None
    props["embedding"] = _encode(ev)
""",

        # ⛔ AND THE ALIAS KEY THAT THE CLAUSE DOES *NOT* BIND (`#13788`, measured at `e21a2fcb`).
        # `_dict_comp_keyed_by_alias` existed for the ASSIGNMENT position only, so a
        # comprehension keyed by a scope alias CERTIFIED through every operand position —
        # 113 in-surface rows in one matrix — while `props[key] = …` and every literal-key
        # spelling were refused. The operand-rule `DictComp` branch asks it now.
        """
def _upsert_object(ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props.update({key: _encode(ev) for _i, v in [("x", 1)]})
        return None
    props["embedding"] = _encode(ev)
""",
        """
def _upsert_object(ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props |= ({key: _encode(ev) for _i, v in [("x", 1)]}).copy()
        return None
    props["embedding"] = _encode(ev)
""",
        """
def _upsert_object(ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props |= [{key: _encode(ev) for _i, v in [("x", 1)]}][0]
        return None
    props["embedding"] = _encode(ev)
""",
        """
def _upsert_object(ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props |= ({key: _encode(ev) for _i, v in [("x", 1)]} if _f() else {})
        return None
    props["embedding"] = _encode(ev)
""",

        # ⛔ AND THE `fromkeys` KEY SEQUENCE IS RESOLVED, NOT TYPE-TESTED (`#13788`, measured at
        # `dde62cb2`): the same defect the zip COLUMNS carried one round earlier, one operand
        # over. `isinstance(keys, ast.List | ast.Tuple | ast.Set)` answered only the BARE literal,
        # so 168 matrix rows CERTIFIED across every declared position while
        # `dict.fromkeys(["embedding"], vec)` was refused.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(list(["embedding"]), _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(["embedding"].copy(), _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(["embedding"] if _f() else ["x"], _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(list([key]), _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys([["embedding"]][0], _encode(ev))
        return None
    _write(props)
""",

        # ⛔ AND A WRAPPER AROUND A PAIR ELEMENT IS THE SAME PAIR (`#13788`, measured at
        # `a17a1550`): `_pairs_store_the_vector` type-tested `ast.Tuple | ast.List`, so ONE
        # transparent wrapper around the pair flipped a REFUSED store to CERTIFIED — the same
        # root as the zip columns and the `fromkeys` key sequence in the two rounds before, and
        # 53 of the affected rows produced an argument list byte-identical to a refused twin.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(props, *[copy.copy(("embedding", _encode(ev)))])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(props, *[("embedding", _encode(ev)).copy()])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(props, *[copy.copy(["embedding", _encode(ev)])])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(props, *( [("embedding", _encode(ev))] if _f() else [] ))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [copy.copy({"embedding": _encode(ev)})][0]
        return None
    _write(props)
""",
        # ⛔ AND THE `.items()` RECEIVER'S *CONSTRUCTOR* IS THE SAME MAPPING (`#13788` review
        # round 7, P1, measured). The receiver resolver learned the copy/conditional/merge
        # wrappers and still read a BARE mapping spelling only, so every builder the OPERAND
        # side already refuses (`_is_mapping_constructor`, `fromkeys`) re-opened the hole one
        # view over — and the KEYS source in the same function already reads them, one line
        # apart from the twin:
        #
        #     {k: _encode(ev) for k in dict({"embedding": 1})}              refused
        #     {k: _encode(ev) for k, _v in dict({"embedding": 1}).items()}  refused (this block)
        #
        # `dict([("embedding", 1)])` and `dict(**{…})` are the same mapping through the other
        # two constructor forms (`_sequence_options` already reads the first, and a `**`
        # keyword is the merge arm), and a dict COMPREHENSION is a mapping whose one pair is
        # its own key/value expression. The controls below keep the arm from refusing an
        # honest non-property key.
        #
        # ⛔ (`#13788` review round 8, P1) AND EVERY OPERAND FORM COMPOSES, on BOTH seams: a
        # MIXED constructor (`dict({'embedding': 1}, x=2)` — the documented
        # `dict(mapping, **kw)` shape), a pairs iterable whose ELEMENT is the mapping itself
        # (`dict(*[{'embedding': 1}])`, a `Starred` this branch resolves everywhere else), the
        # `|` UNION spelling of the merge (`({'embedding': 1} | d)`), and the KEYS twins of the
        # pairs-iterable and comprehension forms. Each CERTIFIED while its sibling seam refused
        # the same object — the exact asymmetry the commit above this one exists to close.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict({"embedding": 1}, x=2).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict({"embedding": 1}, x=2)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict(*[{"embedding": 1}]).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in ({"embedding": 1} | d).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {**(({"embedding": 1}) | d)}.items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict([("embedding", 1)])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"embedding": 1 for _x in _k()}}
        return None
    _write(props)
""",
        # ⛔ (#13788 review round 9, P1/P2) AND THE PAIRS ELEMENT IS A SEQUENCE, THE MIXED
        # CONSTRUCTOR READS ITS KEYWORD, AND A COMPREHENSION'S KEY MAY BE BOUND BY ITS OWN CLAUSE.
        # Each CERTIFIED at the parent while its sibling seam refused the same object: an element
        # wrapper `_sequence_options` already resolves one level up (const index, full slice,
        # `.copy()`, `Starred`); a mixed `dict(d, embedding=1)` discarded the literal keyword
        # because a DIFFERENT operand was unreadable; and `{kk: 1 for kk in ["embedding"]}` emitted
        # a key expression the key question can only read as a constant. The single-element row
        # pins the pair ARITY: a 1-tuple reached the keys twin's unpack and raised ValueError.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict([[("embedding", 1)][0]]).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict([[*("embedding", 1)]]).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict([[("embedding", 1)][0]])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict([["embedding"]])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict(d, embedding=1).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict(d, **{"embedding": 1})}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {kk: 1 for kk in ["embedding"]}.items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {kk: 1 for kk in ["embedding"]}}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict([("embedding", 1)]).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict(**{"embedding": 1}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict.fromkeys(["embedding"]).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {"embedding": 1 for _x in _keys()}.items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({k: _encode(ev) for k, _v in dict({"embedding": 1}).items()})
        return None
    _write(props)
""",
        # ⛔ AND A CONSTANT-INDEX / FULL-SLICE RECEIVER IS THE SAME MAPPING (`#13788`, measured
        # at `84d108e8e2`). The receiver resolver's wrapper chain stopped at the copy and
        # conditional families while `_sequence_options` — the module's ONE sequence
        # resolver, whose vocabulary is "a CONSTANT index into an option, a FULL slice" —
        # already answered the container spellings at every other seam, and the sibling KEYS
        # source in this same function refused the indexed container. So each of these
        # CERTIFIED a stop arm that stores the vector, one subscript over the hole the block
        # above closes. The `[1:2]` PARTIAL slice stays the stated residual (a partial slice
        # selects a different element, so answering it would answer about the wrong one).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in [{"embedding": 1}][0].items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in [{"embedding": 1}][-1].items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in [{"embedding": 1}][0:].items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in ([{"embedding": 1}] + [])[0].items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in [{"embedding": 1}].copy()[0].items()}
        return None
    _write(props)
""",
        # ⛔ AND ONE UNREADABLE MERGE OPERAND MUST NOT ABORT THE WALK (`#13788` review round 7,
        # P1, measured). A merge's key set is a UNION, so `{**{"embedding": 1}, **d}` holds
        # `"embedding"` for EVERY possible `d` and the readable operand alone proves the store:
        # the arm returned `None` on the unreadable one instead, and `None` is ABSTENTION at the
        # `.items()` arm, i.e. CERTIFIED. Its own comment claimed the opposite ("both callers
        # REFUSE on what this returns"), which is what made the defect invisible. The sibling
        # CONDITIONAL arm already unions its readable branches; the keys-source twin ten lines
        # down had the identical defect and is fixed in the same rule.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {**{"embedding": 1}, **d}.items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {**d, **{"embedding": 1}}.items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {**{"embedding": 1}, **d}}
        return None
    _write(props)
""",
        # ⛔ A MAPPING'S VIEW IS A PAIRS SOURCE IN EVERY POSITION, INCLUDING INSIDE A
        # CONSTRUCTOR (`#13788` review round 10, P1, measured). `.items()` was answered at three
        # seams and NOT at the pairs resolver, so a constructor fed a view abstained and the whole
        # family CERTIFIED beside its refused bare twin. Every row below stores `embedding` and
        # every one of them answered `gated=True` before the view arms landed.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict({"embedding": 1}.items())}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict({"embedding": 1}.items()).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict.fromkeys({"embedding": 1}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict.fromkeys({"embedding": 1})}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {"a": ("embedding", 1)}.values()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in list({"embedding": 1}.items())}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in tuple({"embedding": 1}.items())}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict(OrderedDict({"embedding": 1}).items())}
        return None
    _write(props)
""",
        # ⛔ THE VIEW ARMS NEED A ROW EACH, OR REVERTING ONE IS UNPUNISHED (`#13788` review
        #    round 10, measured by the sweep that found BOTH of these legs surviving their own
        #    mutation). Each row below is the ONLY fixture row that reds when the named arm is
        #    removed:
        #
        #      - the `.values()` arm of `_mapping_items_pairs`, reached from a CONSTRUCTOR
        #        (the comprehension position is covered by the row above, which pins the OTHER
        #        resolver's `.values()` arm — same spelling, different seam, so each needs its
        #        own witness);
        #      - the view arm of `_sequence_options`, reached only when the view is wrapped by a
        #        container call, because the wrapper is resolved there BEFORE any caller sees it.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict({"a": ("embedding", 1)}.values())}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict(list({"embedding": 1}.items()))}
        return None
    _write(props)
""",
        # ⛔ THE VIEW SEAMS, ROUND BY ROUND (`#13788` review round 11 + round 12). Each row
        #    below was measured CERTIFIED *and* runtime-stored ONE POSITION SIDEWAYS from a
        #    row that was already refused, because `dict.__ior__`/`dict.update` UNPACK every
        #    ELEMENT of what they are given and a mapping's VIEW is an iterable of those
        #    elements. Which view, and which wrapper, is the whole difference:
        #
        #      props.update({"embedding": _encode(ev)}.items())                  refused
        #      props.update({"a": ("embedding", _encode(ev))}.values())          CERTIFIED
        #      props |= dict({"a": ("embedding", _encode(ev))}.values())         CERTIFIED
        #      props |= {"a": ("embedding", _encode(ev))}.values()               CERTIFIED
        #      props |= list({"a": ("embedding", _encode(ev))}.values())         CERTIFIED
        #      props |= tuple({"a": ("embedding", _encode(ev))}.values())        CERTIFIED
        #      props |= {("embedding", _encode(ev)): 1}.keys()                    CERTIFIED
        #      props |= list({("embedding", _encode(ev)): 1}.keys())              CERTIFIED
        #      props.update({("embedding", _encode(ev)): 1}.keys())               CERTIFIED
        #      props.update(dict.items({"embedding": _encode(ev)}))              CERTIFIED
        #      props.update(dict.values({"a": ("embedding", _encode(ev))}))      CERTIFIED
        #      props.update(dict.keys({("embedding", _encode(ev)): 1}))          CERTIFIED
        #
        #    …plus the `fromkeys` twin, which takes its argument's KEYS and was read through
        #    `_sequence_options` alone while its pairs twin had already been taught to read a
        #    mapping:
        #
        #      props |= dict.fromkeys(["embedding"], _encode(ev))                refused
        #      props |= dict.fromkeys({"embedding": 1}, _encode(ev))             CERTIFIED
        #      props |= dict.fromkeys({"embedding": 1}.keys(), _encode(ev))      CERTIFIED
        #
        #    Every row was runtime-checked (executed, not asserted) to leave
        #    `props["embedding"]` at the vector. They are ONE rule at three seams, not a new
        #    vocabulary: `_mapping_items_pairs` already reads a view, `_comprehension_source_yields`
        #    already answers a mapping's keys, and `_sequence_options` already resolves the
        #    wrappers — each new spelling asks the resolver that exists.
        #
        #    ⛔ A ROW PER LEG, because a leg without a witness is not a closed hole (the sweep
        #    that found two of round 10's own legs surviving said so): the `values()` rows
        #    witness the pairs-operand view arm; the `keys()` rows witness the `.keys()` arm in
        #    `_mapping_items_pairs`; the wrapped/constructor rows witness `_sequence_options`'
        #    attribute list; the `fromkeys` rows witness the constructor rule's mapping
        #    fallback; and the two `dict.items/dict.values/dict.keys` rows witness the UNBOUND
        #    arms — the last of which was DEAD CODE nested inside a `not args` guard until a
        #    row reached it (measured by that sweep).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({"a": ("embedding", _encode(ev))}.values())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict({"a": ("embedding", _encode(ev))}.values())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"a": ("embedding", _encode(ev))}.values()
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= list({"a": ("embedding", _encode(ev))}.values())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= tuple({"a": ("embedding", _encode(ev))}.values())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {("embedding", _encode(ev)): 1}.keys()
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= list({("embedding", _encode(ev)): 1}.keys())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({("embedding", _encode(ev)): 1}.keys())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys({"embedding": 1}, _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys({"embedding": 1}.keys(), _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(dict.items({"embedding": _encode(ev)}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(dict.values({"a": ("embedding", _encode(ev))}))
        return None
    _write(props)
""",
        # ⛔ AND THE UNBOUND KEYS VIEW, which is the third attribute of that same family.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(dict.keys({("embedding", _encode(ev)): 1}))
        return None
    _write(props)
""",
        # ⛔ AND THE UNBOUND VIEW INSIDE A CONSTRUCTOR, which is the ONLY position that reaches
        #    `_mapping_items_pairs`' unbound arm directly (`#13788` review round 11, measured by
        #    the mutation sweep: reverting that arm moved NO row until this one existed — the
        #    operand seam rebuilds the BOUND call and never asks it). A leg with no witness is
        #    not a closed hole.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict(dict.items({"embedding": 1}))}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict(list({("embedding", 1): 1}.keys()))}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict(dict.keys({("embedding", 1): 1}))}
        return None
    _write(props)
""",
        # ⛔ THE VALUES VIEW AS A *KEY* SOURCE IS NOT AN HONEST WRITE-STOP (`#13788` review
        #    round 11, P1, measured). The `.values()` arm's own comment claimed it was —
        #    "where the view is the KEY source and nothing is stored" — and a key-source
        #    clause binds the VALUE, so a mapping whose values ARE the property name places
        #    the vector exactly as the refused `.keys()` / bare-iteration twins do. This is
        #    the ONLY row that reds when the arm's scalar reading is removed, so it is that
        #    leg's witness; the pairs reading beside it is pinned by the row above.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"a": "embedding"}.values()}
        return None
    _write(props)
""",
        # ⛔ A CONSTANT *KEY* INTO A MAPPING IS A MAPPING LOOKUP (`#13788` review round 11, P1,
        #    measured). The const-index twin was closed in round 9 for a SEQUENCE
        #    (`[{"embedding": 1}][0]`, refused above); the DICT half of the same seam was
        #    left open and `d4f3de3ab0`'s message called that residue closed. Each row below
        #    is a witness for one leg — the first three and the `dict(...)` one for the
        #    `_mapping_items_pairs` Subscript arm, the bare-iteration and `.keys()` pair for
        #    `_comprehension_source_yields`' twin. Revert either leg and its rows go CERTIFIED.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {"a": {"embedding": 1}}["a"].items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {"a": {"b": ("embedding", 1)}}["a"].values()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict({"a": {"embedding": 1}}["a"]).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"a": {"embedding": 1}}["a"]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"a": {"embedding": 1}}["a"].keys()}
        return None
    _write(props)
""",
        # ⛔ THE PASS-THROUGH CALL FAMILY IS ONE INCLUDE LIST (`#13788` round 12, measured):
        #    `sorted(...)`, `reversed(...)` and `iter(...)` RETURN THE SAME ELEMENTS they are
        #    given, so a view behind any of them places the same pair / the same key. The
        #    family was spelled out at FOUR seams with FOUR different member sets and none of
        #    these names appeared in any of them, so 21 of 100 generated rows CERTIFIED while
        #    the writer, executed, landed the vector:
        #
        #      props.update(sorted({"embedding": _encode(ev)}.items()))                   CERTIFIED
        #      props |= dict(list(reversed({"a": ("embedding", _encode(ev))}.values())))  CERTIFIED
        #      props.update(iter({"embedding": _encode(ev)}.items()))                     CERTIFIED
        #
        #    `_CONTAINER_CALLS` is the ONE list all four seams ask — the fourth asks it through
        #    `_container_call_operand` with **no subtraction** (PR #13928 review, P1: the arm
        #    answered a subtracted part of the family on a sample that never asked the
        #    pair-with-extras, so the subtraction is gone and only the measurement is recorded,
        #    in `_CONTAINER_CALLS_ANSWERED_UPSTREAM`); `sorted`'s `key=`/`reverse=` re-order
        #    without re-selecting, hence the keyword row.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(sorted({"embedding": _encode(ev)}.items()))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(list(reversed({"a": ("embedding", _encode(ev))}.values())))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in reversed({("embedding", _encode(ev)): 1}.keys())}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(sorted({"embedding": _encode(ev)}.items(), reverse=True))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(sorted([("embedding", _encode(ev))]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(sorted({"embedding": 1}), _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(iter({"embedding": _encode(ev)}.items()))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in iter([("embedding", _encode(ev))])}
        return None
    _write(props)
""",
        # …and the KEY-VIEW read as a keys source places nothing (`{k: … for k in
        # sorted(keys_view)}` keys the new mapping BY the pair), so it is an honest write-stop.
    )
    # ⛔ AN EMPTIED TUPLE IS A GREEN TEST ASSERTING NOTHING (`#13788`, measured by the review
    # of this branch). These tuples run to hundreds of lines and a careless trim — or a bad
    # merge, which this branch has already survived twice — would leave the loop below
    # iterating nothing and the test passing while the whole rule went unpinned.
    assert refused, "the refused fixture tuple is EMPTY — the loop below asserts nothing"
    for source in refused:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, f"a STOP ARM THAT STORES certified the writer: {source!r} ({why})"

    # ⛔ AND THE HONEST STOP ARMS MUST STILL CERTIFY, or the rule is an inert enforcer: a
    # removal, a bare exit, and a non-store statement before the exit are all write-stops.
    accepted = (
        # ⛔ THE TWO NEW ARMS MUST NOT SWALLOW A NON-STORE (`#13788` review round 11). Each
        #    control below is the same SHAPE as a row the fix refuses, with the property absent
        #    from the one position the rule reads — so a rule that refused on the spelling
        #    rather than on the store would pass every refused row above and still be wrong.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"a": "embedding_model"}.values()}
        return None
    _write(props)
""",

        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"a": {"embedding_model": 1}}["a"]}
        return None
    _write(props)
""",

        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {"a": {"embedding_model": 1}}["a"].items()}
        return None
    _write(props)
""",
        # ⛔ THE ONE-SEQUENCE-RESOLVER MUST NOT SWALLOW A NON-STORE (`#13788`, measured at
        # `6504b28f`): a zip whose value column is empty or names no property, a `set(...)` of
        # non-stores, a `+` of non-stores and a `{**X}` source naming no property are all honest
        # write-stops. Without these rows a resolver that answered "yes" to any of those would
        # pass every refused row — and the sweep above measured 0 wrong refusals, which is the
        # property these controls exist to keep.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= zip(list(("embedding_model",)), (_m(),))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= set([("embedding_model", _m())])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= ([{"embedding_model": _m()}] + [])[0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {**{"embedding_model": 1}}}
        return None
    _write(props)
""",
        # ⛔ THE SPLICE FLATTENER MUST NOT SWALLOW A NON-STORE (`#13788`, measured at
        # `794eb162`): a splice of a sequence that names no property, a repetition by zero,
        # and a conditional whose branches place nothing are honest write-stops. Without these
        # rows a flattener that answered "any Starred is a store" would pass every refused row.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *list(["embedding_model", _m()]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *(["embedding", _encode(ev)] * 0))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props._set(x, *(["embedding_model", _m()] if _f() else []))
        return None
    _write(props)
""",
        # ⛔ THE CONDITIONAL RESOLVERS MUST NOT SWALLOW A NON-STORE (`#13788`, measured at
        # `6a164ac9`): a branch that yields nothing, and a wrapper around a NON-STORING
        # spelling, are honest write-stops. Without these rows a resolver that answered "any
        # conditional is a store" would pass every refused row above.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= ([{}] if _f() else [{}])[0:][0]
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in (["embedding_model"] if _f() else [])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= (zip(("embedding",), ()) if _f() else zip((), ()))
        return None
    _write(props)
""",
        # ⛔ THE NARROWINGS OF THE SOURCE RESOLVER, one row per narrowing, or closing the
        # wrapper family above becomes an over-refusal (`#13788`, each measured CERTIFIED on
        # the merged head AND on this head). `[...] * 0` iterates NOTHING, so it places no key;
        # a mapping source is a store only when it yields the PROPERTY (iterating `{"other": 1}`
        # yields `"other"`); a TRANSFORMING comprehension is the resolver's stated residual, and
        # pinning it as ACCEPTED means widening it later has to argue against a fixture instead
        # of quietly reading one more source; and the constant-VALUE comprehension keeps the
        # same non-constant-value narrowing every other arm has.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ["embedding"] * 0}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"other": 1}}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in [x.upper() for x in ["embedding"]]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"embedding": None for k in {"embedding": 1}}
        return None
    _write(props)
""",
        # ⛔ THE BOOLEAN AND WALRUS ARMS MUST NOT SWALLOW A NON-STORE (`#13788`, measured at
        # `105b047b`): refusing every `or` would make the rule an over-block
        # (`#13788`'s whole class, inverted). An `or` whose operands place nothing, a walrus
        # over a non-store, and a conditional container with no storing branch are all honest
        # write-stops and must stay CERTIFIED — without these rows, a `BoolOp` arm written as
        # `return True` would pass the entire refused tuple above.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= [] or []
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([] or [("embedding_model", _m())])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= (p := [("embedding_model", _m())])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= ([{"embedding": None}] if _f() else [{}])[0]
        return None
    _write(props)
""",
        # ⛔ THE TWO NARROWINGS OF THE CONTAINER WRAPPER, or the fix becomes an over-refusal.
        # A PARTIAL slice changes WHICH element the outer index selects (`[1:][0]` is the
        # SECOND element), so resolving it would answer about a different element — the same
        # reason `_is_full_slice` takes only `[:]` / `[0:]` / `[::1]`. And a values view of
        # ⛔ SCALAR values is not a pairs view — but a values view of PAIRS is one, at the
        # unpacking and operand seams alike (`#13788` review round 11; the earlier wording here
        # said "`.values()` is not the pairs view", which the round-10/11 rules falsify and which
        # a future lane would have cited to leave the operand-seam hole open).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([{"embedding": _encode(ev)}, {}][1:][0])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({k: v for k, v in {"embedding_model": _m()}.items()})
        return None
    _write(props)
""",
        # ⛔ THE TWO NARROWINGS OF THE `.items()` RECEIVER'S WRAPPERS, or the round-6 fix
        # becomes an over-refusal (`#13788`). They are the SAME spellings as the four `refused`
        # rows and must still CERTIFY, because neither places the property as a key: one keys
        # the produced mapping by a name that is not the property, the other produces a
        # CONSTANT value, which is not the vector. Without these rows a resolver that answered
        # "yes" to every wrapped mapping would pass every refused row above.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {"embedding_model": 1}.copy().items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: 1 for k, _v in ({"embedding": 1} if _f() else {}).items()}
        return None
    _write(props)
""",
        # ⛔ THE TWO NARROWINGS OF THE CONSTRUCTOR RECEIVER, or that fix becomes an
        # over-refusal (`#13788`). Same spellings as the five `refused` rows above and they
        # must still CERTIFY: one keys the produced mapping by a name that is not the
        # property, the other produces a CONSTANT value, which is not the vector. Without
        # them a resolver that answered "yes" to every constructor would pass every refused
        # row above. ⚠️ `defaultdict(int, {...})` and `dict(**{...})` are deliberately NOT
        # rows: both CERTIFY on the twin seam too, so pinning them here would assert a
        # behaviour this change does not have (recorded, not silently dropped).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in OrderedDict({"embedding_model": 1}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: 1 for k, _v in dict({"embedding": 1}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.pop("embedding", None)
        return None
    _write(props)
""",
        # ⛔ THE THREE CONTROLS FOR THE DICT-COMPREHENSION RULES. A comprehension's `key` and
        # `value` are the produced mapping's KEY and VALUE expressions, so a pairs literal in
        # either becomes a container key or a LIST value and never the vector at a top-level
        # `embedding` key — scanning them refused honest writers (measured, the mutant
        # surviving until these rows existed). The constant-key rule has the same two
        # narrowings every other arm has: the key must be the property EXACTLY, and the value
        # must be NON-CONSTANT (`{"embedding": None for …}` is the pre-guard reset).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {x: [("embedding", _encode(ev))] for x in ("a", "b")}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"a": _encode(ev) for _x in ("m",)}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"embedding": None for _x in ("m",)}
        return None
    _write(props)
""",
        # ⛔ …and the EXACT-NAME narrowing of that key, on the key side: `embedding_model` is the
        # identity triple this repo writes BESIDE the vector, so a comprehension keyed by it sets
        # nothing this gate is about. A substring match on the key passed the suite without this
        # row (measured by the review of this branch).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"embedding_model": _m() for _x in ("m",)}
        return None
    _write(props)
""",
        # ⛔ …and TWO comprehensions whose SOURCE is a pairs literal but whose pair is NOT
        # unpacked into the key it produces: `{x: 1 for x in [(…)]}` keys the mapping by the
        # PAIR, and `{v: k for k, v in [(…)]}` by the vector, so neither places an `embedding`
        # key. Refusing them refused honest writers (measured at this head, each runtime-checked
        # to leave no such key); the source is now read only when the clause unpacks its pair
        # into the key it produces.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {x: 1 for x in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        # ⛔ …and a comprehension KEYED BY A NAME THE CLAUSE DOES NOT BIND: it keys the mapping
        # by that name and places nothing (`{"other": vec}`), so refusing it was an
        # over-refusal — the same reason the arm that did so was removed. The ALIASED twin is
        # the one that stores, and it has its own refused row.
        # ⛔ …and a MENTION: a comprehension handed to an UNKNOWN callee writes nothing
        # (`_audit` reports, it does not store). Recognising it in the REBUILD walk refused
        # this honest writer (measured), which is why the rebuild recognition is keyed to the
        # assigned expression rather than to any nested comprehension.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = _audit({"embedding": _encode(ev) for _x in ("m",)})
        return None
    _write(props)
""",
        # ⛔ THE CONTROLS FOR THE DICT-COMPREHENSION BRANCH: its `key`/`value` expressions are
        # the produced mapping's KEY and VALUE, so a pairs literal there becomes a container
        # key or a list-of-pairs value and NEVER a top-level `embedding` key. Scanning them
        # refused both of these honest writers (measured with a runtime oracle: the merge
        # places no top-level key), so only the generators are scanned and these rows keep the
        # branch from widening back over them.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {tuple([("embedding", _encode(ev))]): 1 for _x in _keys()}
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
        # ⛔ AND A `fromkeys` THAT PLACES NOTHING STAYS CERTIFIED (`#13788`, measured at
        # `c54b8d4b`): the value operand is what places the vector, so its DEFAULT (`None`,
        # the pre-guard reset), a `None` operand and a keys literal that names another
        # property all stay honest write-stops. Without these the new arm is an
        # inert-by-over-refusal rule that costs a writer its gate.
        # ⛔ BUT THE `_keys(ev)` ROW BELOW IS A RESIDUAL, NOT AN HONEST WRITER (review of
        # `4748044f7d`): a keys operand this check cannot read is DATA FLOW it does not do, and
        # `_keys(ev)` returning `("embedding",)` PLACES the vector — so that row pins "we do not
        # over-refuse a shape we cannot resolve", exactly like the `zip` residual further down.
        # Calling it an honest write-stop was a false justification on a right row.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(dict.fromkeys(["embedding"]))
        return None
    _write(props)
""",
        # ⛔ THE HONEST-STOP CONTROLS FOR THE NEW MERGE SOURCES (`#13788` F6): a proxy around the
        # pre-guard reset places nothing, `fromkeys` with no value fills `None`, and the
        # identity triple is exactly what this repo writes while KEEPING the vector — all three
        # must stay CERTIFIED or the widened include list costs an honest writer its gate.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= types.MappingProxyType({"embedding": None})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(["embedding"], None)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(["embedding_model"], _m())
        return None
    _write(props)
""",
        # ⛔ THE VALUE-SIDE `Starred` BOUNDARY (`#13788`, measured at `bcb710a5`). A `Starred`
        # in the unpack VALUE spreads an UNKNOWN number of elements, so the walk does not
        # flatten it. ⛔ BOTH OF ITS ROWS MOVED TO `over_refused` UNDER `#14001`, and one of
        # them was this file's last measured FALSE PASS:
        #   `x, *r = *rest, 1` with `rest = ("embedding",)` binds `x = "embedding"` and PLACES
        #   the vector (runtime-checked) — and it CERTIFIED here, pinned as a residual.
        # The inverted polarity refuses an unbound name in a key position, so the false PASS is
        # closed and its honest twin (`rest = ("a", "b")`, `x = "a"`) is refused too, as the
        # permitted over-refusal. Resolving either needs the value's own binding model.
        # ⛔ THE `zip` RESIDUAL THAT STAYS RESIDUAL (`#13788`): a zip whose COLUMNS ARE NAMES is
        # data flow this module does not do, and the two narrowings of the literal form must
        # each keep an honest writer certified — a keys literal naming ANOTHER property, a
        # property key past the end of the values column (`zip` pairs nothing there, so
        # `dict(zip(("embedding",), ()))` is `{}`), and a CONSTANT value (the pre-guard reset
        # `embedding = None`, which places nothing). Without these the new arm costs an honest
        # writer its gate.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(zip(("embedding_model",), (_m(),)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(zip(("embedding",), ()))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(zip(("embedding",), (None,)))
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
        # ⛔ …AND THE WRAPPER ARMS' TWO NARROWINGS HOLD ONE FORM OVER, or the new refusals
        # become an inert enforcer that costs a writer its gate: `.copy()` on the IDENTITY
        # triple (this repo stores `embedding_model` beside the vector) places no vector, an
        # `if…else` whose mapping holds the PLACEHOLDER places nothing, and a `**` merge of
        # the placeholder is the pre-guard reset. All of them were CERTIFIED before the fix
        # and must stay CERTIFIED after it.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({"embedding_model": _m()}.copy())
        return None
    _write(props)
""",
        # ⛔ THE HONEST-STOP CONTROLS FOR THE FUNCTIONAL-COPY ARM (`#13788`, measured at
        # `716991951e`): the widened wrapper arm must keep the two narrowings, so the identity
        # triple and the pre-guard reset behind `copy.copy(…)` stay CERTIFIED. Without these
        # rows an over-broad `return True` in `_transparent_copy_operand`'s callers would cost
        # an honest writer its gate — the exact failure the pairs rule's controls pin.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(copy.copy({"embedding_model": _m()}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(**copy.deepcopy({"embedding": None}))
        return None
    _write(props)
""",
        # ⛔ THE HONEST-STOP CONTROLS FOR THE ``**`` WRAPPER ARM (`#13788` F2): the new refusal
        # is still narrowed to an EXACT property name and a NON-CONSTANT value, so the
        # pre-guard reset (`None`) and the identity triple behind the same wrapper must stay
        # CERTIFIED — otherwise the widened arm is an over-broad `return True` that costs an
        # honest writer its gate.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(**{"embedding_model": _m()}.copy())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(**{"embedding": None}.copy())
        return None
    _write(props)
""",
        # ⛔ THE HONEST-STOP CONTROLS FOR THE STOP-MODE REBUILD ROUTE (`#13788` F4): the
        # strengthened recursion must not swallow a MENTION or an identity-triple rebuild —
        # `_write({**props, **extra})` merges an unknown and names no key, and
        # `_write({**props, "embedding_model": …})` merges only the identity triple.
        # ⛔ THE HONEST-STOP CONTROLS FOR THE `**` VALUE WALK (`#13788` F5): a READ whose
        # default is a literal must still CERTIFY, and the identity triple must stay certified
        # behind the same wrapper — otherwise the walk costs an honest writer its gate.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _read(props, **{"key": "embedding", "default": None}.copy())
        return None
    _write(props)
""",
        # ⛔ AND THE FUNCTIONAL SPELLING OF THAT SAME CONTROL (`#13788`, measured at
        # `716991951e`): the widened walk now reaches inside `copy.copy(…)`, so the pair that
        # must stay a READ — the property as a constant beside a LITERAL default — has to keep
        # certifying behind the functional wrapper too. Without this row, widening the walk
        # could silently cost an honest reader its gate.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _read(props, **copy.copy({"key": "embedding", "default": None}))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _set(props, **{"key": "embedding_model", "value": _m()}.copy())
        return None
    _write(props)
""",
        # ⛔ THE HONEST-STOP CONTROLS FOR THE `**`-IN-A-DISPLAY WRAPPER ARM (`#13788` F3): the
        # operand rule keeps the two narrowings (EXACT property, NON-CONSTANT value) behind the
        # wrapper, so the pre-guard reset and the identity triple must still CERTIFY.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({**{"embedding": None}.copy()})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {**{"embedding_model": _m()}.copy()}
        return None
    _write(props)
""",
        # ⛔ …AND THE SAME NARROWING IN THE *OPERAND* POSITION, not only the rebuild one
        # (`#13788` F3). The `**`-merge arm's identity control above is the REBUILD spelling
        # (`props = {**{…}.copy()}`); the operand spelling of the identical merge reaches the
        # arm through `update(…)` instead, and a control that only pins one position leaves
        # the other free to become an over-broad `return True`. `{**{"embedding_model": …}}`
        # merges the identity triple into a mapping that keeps the vector, so it places no
        # vector and must stay CERTIFIED — the strip must not refuse a writer for naming the
        # property's SIBLINGS.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({**{"embedding_model": _m()}.copy()})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"embedding": None} if _flag(ev) else {}
        return None
    _write(props)
""",
        # ⛔ THE HONEST-STOP CONTROLS FOR THE CONSTRUCTOR `**` MERGE (`#13788` F1): the new
        # refusals are NARROWED to an EXACT property name and a NON-CONSTANT value, so the
        # pre-guard reset (`None`) and the identity triple must still CERTIFY — otherwise the
        # added refusal costs an honest writer its gate. The last rows pin the same narrowing
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
        # ⛔ THE HONEST-STOP CONTROLS FOR THE UNION (`#13788` F4): routing a union's operands
        # through the operand rule must not swallow a `|` that places nothing. The pre-guard
        # reset behind a `**` inside a union, and the identity triple in the same position,
        # both still CERTIFY — the same two narrowings (EXACT property, NON-CONSTANT value)
        # that keep every other arm from refusing an honest writer.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {**{"embedding": None}} | {}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {**{"embedding_model": _m()}} | {}
        return None
    _write(props)
""",
        # ⛔ THE CONTROLS FOR THE CONSTANT-INDEX ARM (`#13788` F4), so the new recognition is
        # not an over-broad `return True`: the pre-guard reset and the identity triple behind
        # the same index still CERTIFY, and so does an index that is a NAME — `_merged[0]` is
        # the STATED residual (following a name is data flow this module does not do), and a
        # row for it keeps a future widening from swallowing it silently.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update([{"embedding": None}][0])
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = [{"embedding_model": _m()}][0]
        return None
    _write(props)
""",
        # ⛔ AND THE CONTROLS FOR THE UNION ARM: the wrapper recognition must not swallow a
        # union that places nothing — the identity triple behind `.copy()` in the same
        # position still CERTIFIES, and so does a union of two empty displays.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = ({"embedding_model": _m()}.copy()) | {}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props = {} | {}
        return None
    _write(props)
""",
        # ⛔ THE OVER-REFUSALS ARE PINNED, NOT MERELY WRITTEN DOWN (`#13788`, measured by the
        # review of this branch). The doctrine ACCEPTS refusing a key that transforms the
        # property name away, and until now that cost lived only in prose — a reader holding a
        # "must not over-refuse" argument had no artifact to read and no way to tell a
        # deliberate narrowing from an accident. `over_refused` below makes the cost VISIBLE: it
        # asserts the refusal, so an implementation that gets cleverer at reading the key will
        # red there and be made to argue the doctrine rather than quietly widen acceptance.
        # ⛔ THE HONEST CONTROLS FOR THE NEW VALUE-SIDE TERM follow — the value is a CONSTANT,
        # the source is a name this check cannot read (the disclosed residual), or the pair is
        # consumed WHOLE — so none of them places a top-level vector and all must certify.
        # …the value is a CONSTANT, so no vector is placed.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: 1 for k in ["embedding"]}
        return None
    _write(props)
""",
        # …and the pair is consumed WHOLE, so the mapping is keyed by the pair and no
        # top-level `embedding` key is placed.
        # ⛔ THE "KEY BOUND TO THE PROPERTY, SOURCE IS A NAME" control that used to sit here is
        # GONE — `#14001` refuses it and its row is pinned in `over_refused` below (verified by
        # the round-2 review: the comment had been left orphaned above an unrelated row).
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {x: 1 for x in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        # ⛔ AND THE FOUR NEW ARMS ARE NARROWED, one control each (`#13788`): a copy, an
        # `if…else` branch, a builder's keys, a union, a constant index and the keyword form are
        # only a STORE when what they yield is the PROPERTY. Without them an arm could answer
        # True and the suite would not notice — the rule would be inert instead of narrow.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ["embedding_model"].copy()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in (["embedding_model"] if _flag(ev) else [])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ChainMap({"embedding_model": 1})}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        _write({**props, "embedding_model": _m()})
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ({"embedding_model": 1} | {})}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in [{"embedding_model": 1}][0]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict(embedding_model=1)}
        return None
    _write(props)
""",
        # ⛔ THE MIS-PAIR CONTROLS for the `Starred` pairing in `_property_alias_names`
        # (`#13788`, delta reviews of `bcb710a547` and `079a2e6919`): Python binds `x = 2` in the
        # first and `x = 1` in the second, and puts the PROPERTY in the starred name — so both
        # are honest writers, i.e. the direction that pairing exists to protect.
        # `*_r, x = 1, key, 2` was refused by the branch's earlier index pairing and by no other
        # rule; `x, *_r = 1, key` was refused by NOTHING (the index pairing binds `x = 1`
        # correctly and CERTIFIED it). Both rows are kept because each pins the OVER-REFUSAL
        # half of one LEG, which no `refused` row can: before they existed (459 rows) the
        # historical index pairing reddened ONE row (`refused[323]`, a LOST refusal) and no
        # accepted row at all; with them present that same mutant additionally reds
        # `accepted[108]`, and an off-by-shift on the HEAD leg reds `accepted[109]`.
        """
def _upsert_object(props, ev):
    key = "embedding"
    *_r, x = 1, key, 2
    if not _derived_mirror_required("object"):
        props[x] = _encode(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    key = "embedding"
    x, *_r = 1, key
    if not _derived_mirror_required("object"):
        props[x] = _encode(ev)
        return None
    _write(props)
""",
        # ⛔ THE CONSTRUCTOR AND MERGE ARMS MUST NOT SWALLOW A NON-STORE (`#13788` review round
        # 7). A constructor whose key names no property, a comprehension whose value is a
        # constant, a `fromkeys` over a non-property key and a merge whose only READABLE
        # operand holds no property are all honest write-stops; without these rows an arm that
        # refused everything the new vocabulary can see would pass every refused row above.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict({"embedding_model": 1}).items()}
        return None
    _write(props)
""",
        # ⛔ THE SUBSCRIPT RECEIVER'S CONSTANT-VALUE CONTROL (`#13788`, measured at
        # `84d108e8e2`): the same indexed receiver as the refused rows above, with a value
        # that places nothing — the arm must not refuse it. The byte-identical duplicate of
        # the `dict({…}).items()` control that used to sit beside it was removed by
        # `bb3a64dae9` (the file's own standard at `:6120`); this row ADDS coverage rather
        # than replacing it.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: 1 for k, _v in [{"embedding": 1}][0].items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict.fromkeys(["embedding_model"]).items()}
        return None
    _write(props)
""",
        # ⛔ THE NEW OPERAND FORMS MUST NOT SWALLOW A NON-STORE EITHER (`#13788` review round 8):
        # a MIXED constructor and a `|` UNION whose only property-looking key is absent are
        # honest write-stops, so the collection loop and the union arm are pinned against the
        # inert-enforcer direction too.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict({"embedding_model": 1}, x=2).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {kk: 1 for kk in ["embedding_model"]}.items()}
        return None
    _write(props)
""",
        # ⛔ THE ARITY PIN IS THE ROW BELOW, NOT THE ROW ABOVE (`#13788` review round 10, P2,
        #    measured): reverting the arity leg (`pairs.append((element,))`) reds
        #    `dict([d])` and leaves `dict([d]).items()` CERTIFIED, because the ITEMS seam never
        #    unpacks the pair list — it hands the pairs back to callers that guard with
        #    `len(item)` — while the KEYS seam unpacks (`for key, _value in pairs`). The commit
        #    that added both says "both seams"; that is the claim this comment corrects, because
        #    a record that overstates its own coverage is how the next lane measures the wrong
        #    row. `dict([d]).items()` earns its place as an honest-write-stop control, not as an
        #    arity witness.
        # ⛔ AND THE TWO `dict([d])` ROWS ARE CERTIFIED BECAUSE `d` IS A NAME, NOT BECAUSE THE
        #    STORE IS HARMLESS (`#13788` review round 10, P2, measured): bound to a real pair or
        #    a mapping, BOTH rows place the vector at runtime. They sit in the tuple whose block
        #    comment calls its members honest write-stops, so they are named here as the STATED
        #    NAME-BOUND RESIDUAL (`_sequence_options`: "a container bound to a NAME stays the
        #    stated data-flow residual") — a future fix that closes the NAME residual will red
        #    them, and that red is correct, not a regression.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict({"embedding_model": 1}.items())}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {"a": ("embedding_model", 1)}.values()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict.fromkeys({"embedding_model": 1}).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"a": 1, "b": 2}.values()}
        return None
    _write(props)
""",
        # ⛔ THE VIEW ARMS MUST NOT SWALLOW THE KEY-SOURCE SPELLING (`#13788` review round 10):
        #    `for k in m.values()` reads the view as a KEY source, not as pairs, so a blanket
        #    "a values view is a pairs sequence" rule would refuse an honest writer. Pinned as
        #    an accepted control because that is the cheaper error to police.
        # ⛔ THE OPERAND-SEAM VIEW ARMS MUST NOT SWALLOW A NON-STORE (`#13788` review rounds
        #    11/12). A values view of SCALAR values is not a pairs view at all, a pairs view
        #    whose key names a different property places nothing, a CONSTANT value places the
        #    placeholder the file already refuses to count, and plain scalar KEYS are not pairs
        #    either — every one is an honest write-stop and every one is the shape a blanket
        #    "a view is a pairs source" rule would refuse. Pinned as accepted rows because a
        #    false refusal is the cheaper error to police.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({"a": 1, "b": 2}.values())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update({"a": ("embedding_model", _encode(ev))}.values())
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"a": ("embedding", None)}.values()
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {("a", "b"): 1}.keys()
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {("embedding_model", _m()): 1}.keys()
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {"embedding": 1}.keys()
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys({"embedding_model": 1}, _encode(ev))
        return None
    _write(props)
""",
        # ⛔ AND THE RE-ORDERING FAMILY MUST NOT SWALLOW A NON-STORE (`#13788` round 12).
        #    Certifying these is what keeps the include list from becoming "anything sorted is
        #    a store": a scalar value is not a pair, a pair keyed by `embedding_model` places
        #    nothing, an explicit `None` value is a constant, and a keys view read as a KEY
        #    source binds the pair as the key.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(sorted({"a": 1, "b": 2}.values()))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(sorted([("embedding_model", _encode(ev))]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(sorted([("embedding", None)]))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in sorted({("embedding", 1): 1}.keys())}
        return None
    _write(props)
""",
    )
    assert accepted, "the accepted fixture tuple is EMPTY — the loop below asserts nothing"
    # ⛔ A DELIBERATE OVER-REFUSAL MUST STAY PINNED AS ONE. `k.upper()` can never produce
    # `"embedding"` from `"embedding"`, so refusing it loses an honest writer — and it is
    # refused anyway, because failing closed on a stop arm is the cheaper error. The point of
    # this tuple is that these rows are NOT stores: widening acceptance here needs the doctrine
    # argued first, not a quiet key-reading improvement.
    over_refused = (
        # ⛔ `_write({**props, **extra})` USED TO LIVE HERE AS AN "HONEST CONTROL" AND IT IS
        # NOT ONE (`#14001`, measured by the review of this branch): `extra` is CALLER-SUPPLIED,
        # so the arm hands the write channel the property whenever that mapping carries it —
        # measured, `extra={"embedding": VECTOR}` hands the vector to `_write`, and `props |
        # extra` does too. The shipped gate still CERTIFIES it (that channel is not proved), so
        # it is neither an honest control nor an over-refusal: it is a KNOWN FAIL-OPEN, pinned
        # with its caller argument in
        # `tests/test_14001_strip_gate_fail_closed.py::REBUILD_RESIDUAL_ROWS`
        # (`rebuilt-mapping-extra-unpack` / `-bitor`), where closing it reds instead of
        # silently widening this tuple.
        # ⛔ THESE ROWS ARE REFUSED **BECAUSE OF THE POLARITY INVERSION** (`#14001`).
        # Every one of them was in `accepted` while the stop arm's key source was a shape
        # the gate ABSTAINED on — a call (`_keys()`, `_pair(ev)`, `zip(_keys(ev), …)`), a
        # mapping reached through a NAME, a `**kwargs` splat, or a value-side `Starred` —
        # and abstention was read as "no store". That reading is the DEFECT: `props |=
        # {k: vec for k in _keys()}` PLACES the vector the moment `_keys()` answers
        # `["embedding"]`, and the old gate certified it. Under the inverted polarity an
        # unprovable key source REFUSES, which costs these honest writers their gate and
        # is the cheaper error by the doctrine above. They stay PINNED as refusals: making
        # any of them certify again needs the doctrine argued first, exactly as before.
        # (SEVERAL of these rows really store once their key source answers `embedding` —
        # `props |= {k: vec for k in _keys()}` does, and `x, *_r = *rest, 1` with
        # `rest = ("embedding",)` is statically certain to — so they are fail-OPENs this
        # polarity closes, not only over-refusals.)
        #
        # ⛔ AND ONE OF THEM IS THE CANONICAL ROW OF THE WHOLE ISSUE, kept here because a
        # review of an earlier cut of this branch found it had been DELETED and not re-pinned
        # (the pin is what `assert over_refused` exists to protect):
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in _keys()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {v: k for k, v in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {other: v for k, v in [("embedding", _encode(ev))]}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {x: [("embedding", _encode(ev))] for x in _keys()}
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
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.__setitem__(*_pair(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(_keys(ev), _encode(ev))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    rest = ("a", "b")
    x, y, *r = *rest, 1
    if not _derived_mirror_required("object"):
        props[x] = _encode(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    rest = ("embedding",)
    x, *_r = *rest, 1
    if not _derived_mirror_required("object"):
        props[x] = _encode(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= dict(zip(_keys(ev), (_encode(ev),)))
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props.update(_merged[0])
        return None
    _write(props)
""",
        # ⛔ THE SAME CLASS ONE OPERATOR OVER (`#14001`): a `{|}` / `{**}` merge whose OTHER
        # operand is a NAME (`d`) — the keys are `d`'s, which this walk cannot read, so
        # `d = {"embedding": vec}` would place the vector.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {**{"embedding_model": 1}, **d}}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in ({"embedding_model": 1} | d).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict(d).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in dict([d]).items()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict([d])}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in d.values()}
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in d.items()}
        return None
    _write(props)
""",
        # ⛔ THE VALUE-SIDE `Starred` IS THE THIRD PERMITTED OVER-REFUSAL, and it is the price of
        # not following a value's binding (`#13788`, measured at `bcb710a5`). `*rest` spreads an
        # UNKNOWN number of elements, so a HEAD target positioned after it may or may not land
        # where the positional pairing puts it; the arm therefore FAILS CLOSED. In this fixture
        # `rest = ["a", "b"]` binds `y = "b"`, so nothing is stored and the refusal costs an
        # honest writer its gate — refused anyway, because the alternative is pairing a position
        # the walk cannot resolve. Closing it needs the value's own binding model (residual #6),
        # not a wider pairing here: dropping the head pairing would CERTIFY this writer and also
        # silently lose the refusals the same arm exists for. Pinned here so widening it has to
        # argue the doctrine first.
        """
def _upsert_object(props, ev):
    key = "embedding"
    rest = ["a", "b"]
    x, y, *r = *rest, key
    if not _derived_mirror_required("object"):
        props[y] = _encode(ev)
        return None
    _write(props)
""",
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k.upper(): _encode(ev) for k, _i in [("embedding", 1)]}
        return None
    _write(props)
""",
        # ⛔ THE OTHER PERMITTED OVER-REFUSAL, and it is the price of ONE resolver serving both
        # comprehension questions (`#13788`, measured by dropping the resolver's pair leg from
        # the unpacking term and replaying every row: exactly this one moved). The row stores
        # `{"embedding": 1}`, NOT the vector — the produced VALUE is the constant — so the
        # value-side term correctly stays silent. The pair term asks the SOURCE question
        # ("does an iteration yield `(property, non-constant)`"), which is the answer that
        # keeps the wrapper family closed, and the cost is refusing this spelling. Pinned here
        # rather than argued in prose, so widening it has to argue the doctrine first.
        """
def _upsert_object(props, ev):
    if not _derived_mirror_required("object"):
        props |= {k: 1 for k, _i in set([("embedding", _encode(ev))])}
        return None
    _write(props)
""",
    )
    assert over_refused, "the over_refused fixture tuple is EMPTY — the loop below asserts nothing"
    for source in over_refused:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is False, (
            f"a DELIBERATE over-refusal stopped being refused — this is not a bug to fix in a "
            f"hurry: the doctrine (fail closed on a stop arm) is what makes it acceptable, and "
            f"widening acceptance needs that argument made first: {source!r} ({why})"
        )
    for source in accepted:
        module = _fake_writer_module(tmp_path, monkeypatch, source)
        gated, why = strip._writer_consults_gate(module, "_upsert_object", ("object",))
        assert gated is True, f"an honest write-stop was refused: {source!r} ({why})"


def _the_comprehension_source_over(source: str) -> tuple:
    """`props |= {v[0]: v[1] for v in <source>}` -> (source node, dict comprehension)."""
    module = ast.parse(f"def _u(props, ev):\n    props |= {{v[0]: v[1] for v in {source}}}\n")
    comprehension = module.body[0].body[0].value
    return comprehension.generators[0].iter, comprehension


def test_the_fourth_legs_container_arm_asks_the_whole_family():
    """⛔ THE FOURTH LEG SHEDS NO FAMILY NAME, AND ITS REDUNDANCY IS A MEASUREMENT WITH A SHAPE
    (`#13788` round 12; re-measured for the review of PR #13928, which is why the subtraction
    is GONE).

    `_source_holds_the_store_anywhere` asks the ONE include list (`_CONTAINER_CALLS`, through
    `_container_call_operand`) and answers ALL of it. At `085c815866` it answered a measured
    PART of it — subtracting `_CONTAINER_CALLS_ANSWERED_UPSTREAM` — on a sample of five PLAIN
    two-element inner shapes, which is the direction this file forbids (a subtraction can only
    REDUCE refusals) on a licence that never asked the pair-WITH-EXTRAS shape the arm's own
    docstring says it exists for.

    ⛔ AND THE COVER IS NOT REDUNDANT THERE — measured END TO END through `_writer_consults_gate`,
    six names x ten inner shapes = 60 rows, same fixture bodies at `085c815866` and at this head,
    only the arm's disposition moving: **20 CERTIFIED at `085c815866`, 12 here**. The 8 rows that
    move are `set`/`sorted`/`reversed`/`iter` x the two NESTED extras spellings, each a real store:

        props |= {v[0]: v[1] for v in [p for p in sorted([("embedding", _encode(ev), 1)]) if p]}
        props |= {v[0]: v[1] for v in [p[0:2] for p in reversed([("embedding", _encode(ev), 1)])]}

    — CERTIFIED at `085c815866`, REFUSED here. A nested comprehension carrying an `if` (or
    transforming its element) makes leg 3 abstain, and a 3-tuple makes the pairs rule abstain, so
    the fourth leg is the SOLE decider for that class, not a redundant one. (The first repair
    round's end-to-end rows spell the filter DIRECTLY in the clause — `sorted([(…, 1)]) if v` —
    which some other term of the `|=` chain refuses with or without this arm; its 60-row sample
    read "0 verdicts moved" because it never asked a nested wrapper. Those rows are kept — they
    pin the class — and the nested spellings are added beside them.)

    So this test guards two things that must move together:

    * the arm still ANSWERS every family name over BOTH pair shapes, INCLUDING the nested extras
      spellings where nothing upstream answers — a local name tuple, or a subtraction, coming back
      is the defect `_CONTAINER_CALLS` exists to prevent, and it would be silent: the family
      would answer here for two names and skip the other four with nothing reddening;
    * the redundancy the record claims still HOLDS WHERE IT WAS MEASURED, and is asserted NOT to
      hold for the sole-decider class — so a later lane cannot cite the record as cover for those
      rows. Thin leg 3 and the first half reddens instead of the record quietly describing a set
      of legs that no longer read those wrappers. ⛔ **Leg 1 cannot redden THIS test by REMOVAL or
      WEAKENING** (measured at `f2e5e3a98f`: over this 60-row sample `leg1 AND NOT leg3` is 0 rows —
      leg 1 is a subset of leg 3 here — so deleting leg 1 outright moves `upstream` nowhere and
      this test stays green). ⛔⛔ **THAT IS A FACT ABOUT THIS TEST, NOT ABOUT THE SUITE — do not
      widen it.** Leg 1 is a disjunct of the `upstream = (… or …)` expression below, so a mutant
      that STRENGTHENS leg 1 to answer a sole-decider row reddens its `assert upstream is False`;
      and removing leg 1 is caught by a DIFFERENT test in this same file — measured at this head,
      leg 1 disabled on `tools.strip_graph_entity_vectors` reddens
      `test_the_stop_arm_must_not_store_the_vector` with `a STOP ARM THAT STORES certified the
      writer`, on the plain-pair stop arm `props |= [("embedding", _encode(ev))]` that leg 1
      alone refuses. So leg 1 is a duplicate AT THE FAMILY SEAM, not an unwitnessed leg: scope
      this paragraph to the family test or it becomes the "protection nothing can redden" claim
      this whole file exists to remove.
      ⛔ Both cites in this paragraph are by CONSTRUCT, not by line number, on purpose: an earlier
      revision pinned `:9990` / `:9994` and both were wrong within one commit (a string fragment
      and a blank line) — see `tests/test_12184_shifted_cites.py`, written for exactly that
      false-correction shape. Do not re-pin them.
      The assertions below ask `leg1 OR leg3` on purpose — the record is about the redundancy, not
      about a leg — and the record says so rather than naming leg 1 as a guard it cannot be.
      Repairing the asymmetry would mean deleting leg 1, which is the one direction this file
      forbids.

    ⛔ THE BASELINE IT IS MEASURED AGAINST, stated correctly (the review of `085c815866` caught
    the earlier sentence being false): at the merge base `e3f894b31b` the arm ALREADY asked the
    shared helper over all six names — the literal `("list", "tuple")` spelling is pre-`#13788`,
    two commits back. This test still cannot pass at either head, for two different reasons:
    at `085c815866` because the arm subtracts four of the six (the assertions below redden), and
    at `e3f894b31b` because the record constant these assertions read does not exist there.
    """
    inner_shapes = (
        # the inner spellings a wrapper can hand this arm
        '[("embedding", _encode(ev))]',
        '[("embedding", _encode(ev))].copy()',
        '([("embedding", _encode(ev))] if _gate() else [])',
        '[*[("embedding", _encode(ev))]]',
        '{("embedding", _encode(ev))}',
        # ⛔ …AND THE PAIR-WITH-EXTRAS, the shape this arm's own docstring says it exists for and
        # the class the review's counterexample was made of. Leaving it out of the sample is what
        # made `0 of 30` look like a licence.
        '[("embedding", _encode(ev), 1)]',
        # …and two extras spellings NO leg answers at source level — one whose KEY is the property
        # but whose VALUE is a bare constant (`[("embedding", 1, 1)]`), one whose produced key is
        # not the property (`[(1, "embedding", _encode(ev))]`). ⛔ Do not reduce both to one reason:
        # the key is not what rules the first out (measured at this head, `[("embedding",
        # _encode(ev), 1)]` and `[("embedding", 1, 1)]` carry the same key and leg 3 answers only
        # the live-valued one) — its constant `v[1]` is. Kept in the sample so the redundancy
        # claim is measured over them too rather than silently skipped. Neither is a store —
        # measured: `[("embedding", 1, 1)]` yields `{'embedding': 1}` (the property IS the key;
        # its VALUE is a constant), and `[(1, "embedding", _encode(ev))]` yields `{1: 'embedding'}`
        # (key `1`, value not the vector). ⛔ THEY HAVE NO END-TO-END ROW EITHER: an earlier revision
        # of this comment pointed at `test_the_stop_arm_must_not_store_the_vector` as the home of
        # their end-to-end rows, which is false for both — measured at this head, that test spells
        # `[(1, "embedding", _encode(ev))]` NOWHERE, and its one `("embedding", 1, 1)` sits in a
        # `for k, _i in iter(...)` destructuring, not the `{v[0]: v[1]}` pair this sample asks
        # about. The pointer is DELETED rather than corrected: a pointer a reader cannot follow is
        # the defect this block exists to remove.
        '[("embedding", 1, 1)]',
        '[(1, "embedding", _encode(ev))]',
    )
    # ⛔ THE SOLE-DECIDER CLASS (`085c815866` review, P1): the extras pair BEHIND a nested
    #    comprehension, whose `if` (or transforming element) makes leg 3 abstain while the 3-tuple
    #    makes the pairs rule abstain. Nothing upstream answers these, so the fourth leg is the
    #    whole gate — these are the rows that CERTIFIED while the subtraction was in place.
    sole_decider_shapes = (
        '[p for p in [("embedding", _encode(ev), 1)] if p]',
        '[p[0:2] for p in [("embedding", _encode(ev), 1)]]',
    )
    record = tuple(strip._CONTAINER_CALLS_ANSWERED_UPSTREAM)
    assert record, (
        "the record of which family names an upstream leg answers is EMPTY — a red-free test "
        "that asserts nothing. Restore it (it is the measurement, not a disposition)."
    )
    for name in record:
        # the record is spoken over the FAMILY: a name that left `_CONTAINER_CALLS` must leave
        # this tuple too, or the record describes a set that no longer exists
        assert name in strip._CONTAINER_CALLS, (
            f"{name!r} is recorded as answered upstream but is not in `_CONTAINER_CALLS` — the "
            f"record has outlived the family it measures"
        )

    for name in strip._CONTAINER_CALLS:
        for inner in ('[("embedding", _encode(ev))]',
                      '[("embedding", _encode(ev), 1)]',
                      *sole_decider_shapes):
            source, _comprehension = _the_comprehension_source_over(f"{name}({inner})")
            assert strip._source_holds_the_store_anywhere(source) is True, (
                f"the fourth seam's container arm stopped answering `{name}({inner})` — it asks "
                f"the ONE include list, so every family name must reach it. A local name tuple "
                f"or a subtraction has come back: that reduces refusals, the one direction this "
                f"file forbids, and it happens silently. For the nested extras spellings this "
                f"arm is the SOLE decider (`085c815866` CERTIFIED them end to end)."
            )

    for name in strip._CONTAINER_CALLS:
        for inner in (*inner_shapes, *sole_decider_shapes):
            source, _comprehension = _the_comprehension_source_over(f"{name}({inner})")
            upstream = (strip._pairs_operand_stores_the_vector(source)
                        or strip._comprehension_source_pairs_the_property_with_a_value(source))
            if inner in sole_decider_shapes:
                assert upstream is False, (
                    f"`{name}({inner})` is now answered UPSTREAM as well, so the fourth leg is "
                    f"no longer its only decider. That is good news — but say it here, in the "
                    f"record, before anything leans on it: this row was the counterexample that "
                    f"killed the four-name subtraction, and a stronger upstream leg is a "
                    f"measurement, never a licence to subtract again."
                )
                continue
            if strip._source_holds_the_store_anywhere(source) is not True:
                continue
            assert upstream is True, (
                f"`{name}({inner})` is answered by the FOURTH leg and by nothing upstream — the "
                f"redundancy `_CONTAINER_CALLS_ANSWERED_UPSTREAM` records has stopped holding. "
                f"⛔ THIS RED IS NOT DIAGNOSTIC: do not read it as blaming one leg, and do not "
                f"treat what follows as a closed list. It fires whenever `upstream` came back "
                f"False while the arm answers, and that has more than one cause — leg 3 can stop "
                f"answering a wrapper it read; a recogniser BOTH upstream legs consult can stop "
                f"naming the key (measured at this head: `_names_property_key` forced off "
                f"reddens HERE, on a PLAIN row, with the arm untouched); or the FOURTH leg can "
                f"WIDEN to answer a row nothing upstream answers (measured: an arm-only widening "
                f"reddens HERE with no upstream leg touched). The one thing this red DOES "
                f"establish is that leg 1 is not the cause — it is a DUPLICATE at this seam, "
                f"measured 0 rows where leg 1 answers and leg 3 does not. A real store is still "
                f"REFUSED, so nothing certifies: this is the record's guard, not a fail-open."
            )


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
    assert refused, "the refused fixture tuple is EMPTY — the loop below asserts nothing"
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
    assert accepted, "the accepted fixture tuple is EMPTY — the loop below asserts nothing"
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
    assert result["unmeasured_by_label"] == DECLARED_EXCEPT_OBJECT
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
