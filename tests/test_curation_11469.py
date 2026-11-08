"""#11469 — the curation pass's invariants, pinned.

The migration moves the ONLY copy of a tenant's data (the record holds 0 rows for
it), so the properties below are the reason this pass is safe to run at all:

* a second run writes nothing (idempotent, both ledger-side and target-side);
* the write surface has no destructive verb (additive by type, not by discipline);
* the off-switch genuinely disarms;
* source == target is refused;
* a dry run touches nothing;
* a row is only moved out of "undecidable" by a rule the report can quote.
"""

from __future__ import annotations

import ast as _ast
import json
import pathlib as _pathlib

import pytest

from tortoise import curation as C


def row(node_id="n1", node_type="point", kind="statement", content="A claim.", content_hash=None, **props):
    return C.Row(node_id=node_id, node_type=node_type, kind=kind, content=content,
                 content_hash=content_hash, props=props)


class CountingWriter:
    """A target that records every write and folds by content hash, like ingest."""

    def __init__(self):
        self.calls: list[tuple[str, list[dict]]] = []
        self.stored: dict[str, dict] = {}

    def write_sections(self, target_graph, sections):
        keys = [p["migrationKey"] for items in sections.values() for p in items]
        self.calls.append((target_graph, keys))
        written = []
        for items in sections.values():
            for p in items:
                self.stored.setdefault(p["migrationKey"], p)
                written.append(p["migrationKey"])
        return {"written": written}


@pytest.fixture()
def ledger(tmp_path):
    return C.Ledger(tmp_path / "ledger.jsonl", target="new")


# ── idempotency ──────────────────────────────────────────────────────────


def test_second_run_writes_nothing(ledger):
    plan = C.build_plan(
        [row("a", content="claim one"), row("b", content="claim two")],
        source_graph="old", target_graph="new", packs={},
    )
    plan.decisions = [C.Decision(d.node_id, d.node_type, d.kind, C.CARRY, "test", d.pack, d.stable_key)
                      for d in plan.decisions]
    writer = CountingWriter()

    first = C.run_plan(plan, writer=writer, ledger=ledger, apply=True, graph_rows=ROWS)
    assert first.written == 2
    assert len(writer.calls) == 1

    second = C.run_plan(plan, writer=writer, ledger=ledger, apply=True, graph_rows=ROWS)
    assert second.written == 0
    assert second.skipped_known == 2
    assert len(writer.calls) == 1, "the second run must not reach the wire at all"


def test_digest_is_stable_across_runs(ledger):
    rows = [row("a", content="claim one"), row("b", content="claim two")]
    p1 = C.build_plan(rows, source_graph="old", target_graph="new", packs={})
    p2 = C.build_plan(rows, source_graph="old", target_graph="new", packs={})
    assert p1.digest() == p2.digest()


def test_duplicate_follows_its_keeper():
    """'the keeper carries it' must be TRUE, or the report contradicts the plan.

    Same content hash, different content (a stale hash): the keeper is not carried,
    so its duplicate must not be carried either — and must say so.

    Round-29 review finding 2 fixed a vacuous assertion here (`"a" in reason` passed
    on the letter inside `not-carried`). Round-30 review finding 2 then found that
    the fix asserted a FALSEHOOD in this very fixture: the reason named the empty
    row as "the row of record for this content" while the only copy of the text sat
    in the other row, and `plan --rows` cannot show that (Decision.as_dict carries
    no `content`). The promotion loop now lets the content-holding row BE the
    keeper, so the claim is true and the empty row inherits a truthful
    `undecidable` rather than a confident `not-carried`.
    """
    plan = C.build_plan(
        [row("empty-node", content="", content_hash="H"),
         row("content-node", content="A durable claim", content_hash="H")],
        source_graph="old", target_graph="new", packs={},
    )
    by_id = {d.node_id: d for d in plan.decisions}
    # The row that HOLDS the content is the row of record — not the empty one that
    # merely arrived first.
    assert by_id["content-node"].disposition == C.UNDECIDABLE, (
        "the row holding the only copy of the text is undecidable, never silently 'not-carried'")
    assert by_id["empty-node"].disposition == C.UNDECIDABLE
    reason = by_id["empty-node"].reason
    assert "content-node" in reason, f"the reason must name the keeper: {reason!r}"
    assert "empty-node" not in reason, f"the reason must not name the duplicate as keeper: {reason!r}"
    assert "row of record" not in reason or "content" in reason
    # Nothing is carried — the content has no ruling yet, which is the honest state.
    assert plan.carry_rows() == []


def test_a_content_holding_row_displaces_an_empty_keeper():
    """Round-30 review finding 2: the round-7 invariant only held when the
    content-bearing row's own rule CARRYed. When it did not, an empty keeper
    survived and the artifact claimed the content was accounted for.

    A row that holds content must displace an incumbent that holds none, whatever
    that row's own disposition is — and the disposition the empty row inherits must
    not assert the claim is safely left behind.
    """
    plan = C.build_plan(
        [row("empty-keeper", content="", content_hash="H"),
         row("real-keeper", content="Let me check the schema.", content_hash="H")],
        source_graph="old", target_graph="new", packs={},
    )
    by_id = {d.node_id: d for d in plan.decisions}
    # The row that HOLDS the content is the one of record, so it must never be
    # reported as somebody else's duplicate…
    assert "duplicate" not in by_id["real-keeper"].reason, (
        f"the content-holding row must not be labelled a duplicate: "
        f"{by_id['real-keeper'].reason!r}")
    # …and the row holding nothing must never be presented as the row of record.
    assert "row of record" not in by_id["empty-keeper"].reason, (
        f"an empty row cannot be the row of record for content it does not hold: "
        f"{by_id['empty-keeper'].reason!r}")
    assert by_id["empty-keeper"].disposition != C.CARRY
    assert by_id["real-keeper"].disposition in (C.UNDECIDABLE, C.NOT_CARRIED), (
        "its own rule decides its disposition — here the narrative marker does")


def test_the_promotion_loop_does_not_reorder_a_single_row_plan():
    """Guard: the keeper-promotion pass must not perturb an order it has no work for."""
    plan = C.build_plan([row("solo", kind="decision", content="a claim")],
                        source_graph="old", target_graph="new", packs={})
    assert [d.node_id for d in plan.decisions] == ["solo"]


def test_duplicate_of_an_undecidable_keeper_stays_undecidable():
    plan = C.build_plan(
        [row("a", content="same thing"), row("b", content="same thing")],
        source_graph="old", target_graph="new", packs={},
    )
    by_id = {d.node_id: d for d in plan.decisions}
    assert by_id["b"].disposition == C.UNDECIDABLE
    assert "no carry candidate" in by_id["b"].reason


# ── the switch and the guards ────────────────────────────────────────────


def test_dry_run_writes_nothing(ledger):
    plan = C.build_plan([row()], source_graph="old", target_graph="new", packs={})
    writer = CountingWriter()
    res = C.run_plan(plan, writer=writer, ledger=ledger, apply=False, graph_rows=ROWS)
    assert res.written == 0 and not res.applied
    assert writer.calls == []
    assert "dry run" in (res.refused or "")


def test_explicit_off_switch_writes_nothing(ledger):
    plan = C.build_plan([row()], source_graph="old", target_graph="new", packs={})
    writer = CountingWriter()
    res = C.run_plan(plan, writer=writer, ledger=ledger, apply=True, off=True, graph_rows=ROWS)
    assert res.off and not res.applied and writer.calls == []


def test_env_off_switch_writes_nothing(ledger, monkeypatch):
    monkeypatch.setenv(C.OFF_SWITCH_ENV, "1")
    plan = C.build_plan([row()], source_graph="old", target_graph="new", packs={})
    writer = CountingWriter()
    res = C.run_plan(plan, writer=writer, ledger=ledger, apply=True, graph_rows=ROWS)
    assert res.off and writer.calls == []


def test_source_equals_target_refused(ledger):
    plan = C.build_plan([row()], source_graph="live-v4", target_graph="live-v4", packs={})
    with pytest.raises(C.CurationRefused):
        C.run_plan(plan, writer=CountingWriter(), ledger=ledger, apply=True)


def test_source_equals_target_refused_across_spellings():
    # namespace string vs graph id — the same graph, two origins
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("org_abc_g_2b7e4c9d1a6f8035", "g_2b7e4c9d1a6f8035")


def gr(name: str) -> dict:
    """A graph row for an arbitrary name — the shape a surface exposing identities
    returns. The id is derived so every named graph is distinct."""
    gid = "g_" + __import__("hashlib").sha256(name.encode()).hexdigest()[:16]
    return {"graph_id": gid, "stable_id": gid, "name": name,
            "namespace": f"org_x_{gid}"}


ROWS = [gr(n) for n in ("old", "new", "A", "B")]


@pytest.fixture(autouse=True)
def _curation_off_switch_unset(monkeypatch):
    """The suite must not read an AMBIENT off switch.

    `TORTOISE_CURATE_OFF` is the operator's own variable and a plausible cell-wide
    export; inherited, it reddened 28 tests on correct code with messages that read as
    `run_plan` accounting defects. Every test that wants the switch sets it itself, and
    `test_the_suite_pins_the_ambient_off_switch_fixture` is the pin that keeps this
    fixture here."""
    monkeypatch.delenv(C.OFF_SWITCH_ENV, raising=False)

GRAPH_ROWS = [
    {"graph_id": "default", "stable_id": "g_0bcdb207ef7336c3", "name": "default",
     "namespace": "team_3326a01ea34ae595d84de5d8f9"},
    {"graph_id": "g_2b7e4c9d1a6f8035", "stable_id": "g_2b7e4c9d1a6f8035", "name": "live-v4",
     "namespace": "org_3326a01ea34ae595d84de5d8f9_g_2b7e4c9d1a6f8035"},
    {"graph_id": "g_deadbeefdeadbeef", "stable_id": "g_deadbeefdeadbeef", "name": "clean",
     "namespace": "org_3326a01ea34ae595d84de5d8f9_g_deadbeefdeadbeef"},
]


def test_the_graph_name_is_the_same_graph_as_its_namespace():
    """The operator types `live-v4`; the doc calls that the same graph as the
    namespace. A guard that only understands ids would let an in-place run through."""
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("live-v4", "org_3326a01ea34ae595d84de5d8f9_g_2b7e4c9d1a6f8035",
                                 graph_rows=GRAPH_ROWS)


def test_unresolvable_target_is_refused_fail_closed():
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("live-v4", "not-a-graph", graph_rows=GRAPH_ROWS)


def test_ambiguous_name_is_refused():
    rows = [*GRAPH_ROWS, {"graph_id": "g_ffffffffffffffff", "name": "clean",
                          "namespace": "org_x_g_ffffffffffffffff"}]
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("live-v4", "clean", graph_rows=rows)


def test_distinct_graphs_pass():
    C.assert_distinct_graphs("live-v4", "clean", graph_rows=GRAPH_ROWS)


NAMESPACE_ROWS = [{"namespace": "org_x_g_2b7e4c9d1a6f8035"},
                  {"namespace": "org_x_g_deadbeefdeadbeef"}]


def test_namespace_only_identity_is_enough_to_tell_graphs_apart():
    """This server exposes namespaces (which encode the id), not name rows — so two
    namespaces that differ ARE two graphs, and the guard must not over-block them."""
    C.assert_distinct_graphs("org_x_g_2b7e4c9d1a6f8035", "org_x_g_deadbeefdeadbeef",
                             graph_rows=NAMESPACE_ROWS)


def test_namespace_and_gid_spellings_of_one_graph_are_refused():
    """The surface returns namespaces; `g_<id>` is the other accepted spelling. They
    are the SAME graph and the guard must not let an in-place run through."""
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("org_x_g_2b7e4c9d1a6f8035", "g_2b7e4c9d1a6f8035",
                                 graph_rows=NAMESPACE_ROWS)


def test_org_and_team_spellings_of_the_tenant_graph_are_one_graph():
    """`org_<id>` (post-rename) and `team_<id>` (before it) are the SAME graph — the
    product says so. A guard that misses it approves an in-place run on the default."""
    tenant = [{"namespace": "org_3326a01ea34ae595d84de5d8f9"},
              {"namespace": "team_3326a01ea34ae595d84de5d8f9"}]
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("org_3326a01ea34ae595d84de5d8f9",
                                 "team_3326a01ea34ae595d84de5d8f9", graph_rows=tenant)


def test_a_dry_run_with_an_unproven_identity_plans_instead_of_refusing(tmp_path):
    """The guard is strict where a WRITE can happen; a plan-only run reports."""
    plan = C.build_plan([row("a", content="Let me look.")], source_graph="live-v4",
                        target_graph="new", packs={})
    res = C.run_plan(plan, writer=CountingWriter(), ledger=C.Ledger(tmp_path / "led.jsonl", target="new"),
                     apply=False, graph_rows=[{"namespace": "org_x_g_deadbeefdeadbeef"}])
    assert res.identity and "cannot resolve" in res.identity
    assert res.written == 0


def test_a_ledger_for_another_target_is_refused(tmp_path):
    plan = C.build_plan([row("a", content="Let me look.")], source_graph="old",
                        target_graph="new", packs={})
    with pytest.raises(C.CurationRefused):
        C.run_plan(plan, writer=CountingWriter(), ledger=C.Ledger(tmp_path / "led.jsonl", target="A"),
                   apply=False, graph_rows=ROWS)


def test_no_rows_does_not_fail_open():
    """With no identity rows, cross-spelling must NOT pass on a string compare."""
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("live-v4", "g_2b7e4c9d1a6f8035")


def test_a_name_the_surface_cannot_resolve_is_refused():
    """`live-v4` cannot be resolved from namespace-only rows — refusing is the only
    safe answer, because the alternative is assuming it is not the archive."""
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("live-v4", "org_x_g_deadbeefdeadbeef", graph_rows=NAMESPACE_ROWS)


def test_no_target_refused():
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("old", "")


def test_writer_protocol_declares_no_destructive_verb():
    """Additivity is a property of the type: if someone adds a delete verb here,
    this test fails before the pass can destroy the only copy."""
    verbs = [m for m in dir(C.PlanWriter) if not m.startswith("_")]
    assert verbs == ["write_sections"], verbs


class RefusingWriter:
    def __init__(self):
        self.calls = 0

    def write_sections(self, target_graph, sections):
        self.calls += 1
        raise C.CurationRefused("target refused the batch: {'error': 'quota'}")


def test_a_refused_write_is_never_recorded(tmp_path):
    """A ledger that records ATTEMPTS is silent loss of the only copy."""
    led = C.Ledger(tmp_path / "l.jsonl", target="new")
    plan = C.build_plan([row("a", content="Let me look.")], source_graph="old",
                        target_graph="new", packs={})
    plan.decisions = [C.Decision(d.node_id, d.node_type, d.kind, C.CARRY, "test", d.pack,
                                 d.stable_key, d.content) for d in plan.decisions]
    res = C.run_plan(plan, writer=RefusingWriter(), ledger=led, apply=True, graph_rows=ROWS)
    assert res.written == 0
    assert res.refused and "refused" in res.refused
    assert len(led) == 0


def test_ledger_is_scoped_to_its_target(tmp_path):
    """Migrating into graph A does not mean graph B was migrated."""
    path = tmp_path / "l.jsonl"
    plan = C.build_plan([row("a", content="Let me look.")], source_graph="old",
                        target_graph="A", packs={})
    plan.decisions = [C.Decision(d.node_id, d.node_type, d.kind, C.CARRY, "test", d.pack,
                                 d.stable_key, d.content) for d in plan.decisions]
    w = CountingWriter()
    C.run_plan(plan, writer=w, ledger=C.Ledger(path, target="A"), apply=True, graph_rows=ROWS)

    plan_b = C.Plan(source_graph="old", target_graph="B", decisions=plan.decisions)
    res = C.run_plan(plan_b, writer=w, ledger=C.Ledger(path, target="B"), apply=True, graph_rows=ROWS)
    assert res.written == 1, "the second target must receive the row"
    assert [t for t, _ in w.calls] == ["A", "B"]


def test_off_switch_stops_a_run_between_batches():
    armed = {"v": True}
    calls = []

    def call_tool(tool, args):
        calls.append(args)
        return {"filed": 1}

    points = [{"migrationKey": f"k{i}", "content": "c"} for i in range(3)]
    # arm for the first batch, then flip the switch mid-run
    armed["v"] = True

    def armed_then_off():
        v = armed["v"]
        armed["v"] = False
        return v

    writer = C.IngestWriter(call_tool, batch_size=1, armed=armed_then_off)
    out = writer.write_sections("new", {"points": points})
    assert len(out["written"]) == 1
    assert out["stopped"] == "off-switch engaged"
    assert len(calls) == 1


class FakeCallToolResult:
    """The REAL shape `mcp_client.call_tool` returns: an object, not a dict."""

    def __init__(self, payload, is_error=False):
        self.structuredContent = None
        self.isError = is_error
        self.content = [type("T", (), {"text": json.dumps(payload)})()]


class RealCallToolResult:
    """fastmcp 3.x: snake_case fields, and a write result may carry TWO text parts
    (the payload, then #10084's graph-resolution disclosure)."""

    def __init__(self, payload, *, disclosure=None, is_error=False):
        texts = [type("T", (), {"text": json.dumps(payload)})()]
        if disclosure:
            texts.append(type("T", (), {"text": disclosure})())
        self.content = texts
        self.structured_content = None
        self.data = None
        self.is_error = is_error


def test_the_real_snake_case_result_shape_is_unwrapped():
    """Reading only camelCase made every branch dead on the live client, so the
    accept/refuse decision fell through to 'not a dict ⇒ accepted'."""
    def call_tool(tool, args):
        return RealCallToolResult({"created": 1}, disclosure="This write targeted graph 'x'.")

    out = C.IngestWriter(call_tool).write_sections(
        "new", {"points": [{"migrationKey": "k", "content": "c"}]})
    assert out["written"] == ["k"]


def test_a_refusal_in_the_real_shape_is_refused():
    def call_tool(tool, args):
        return RealCallToolResult({"error": "ERR_QUOTA"}, is_error=False)

    with pytest.raises(C.CurationRefused):
        C.IngestWriter(call_tool).write_sections(
            "new", {"points": [{"migrationKey": "k", "content": "c"}]})


def test_an_unparseable_result_is_refused_not_assumed_ok():
    class Weird:
        is_error = False
        structured_content = None
        data = None

        def __init__(self):
            self.content = [type("T", (), {"text": "not json at all"})()]

    with pytest.raises(C.CurationRefused):
        C.normalise_tool_result(Weird())


def test_a_calltoolresult_object_is_unwrapped_and_a_refusal_refuses():
    """`call_tool` hands back a CallToolResult, so a dict-only check never sees the
    refusal — and the batch gets ledgered as migrated."""
    def call_tool(tool, args):
        return FakeCallToolResult({"error": "ERR_QUOTA", "violations": [{"section": "points"}]})

    writer = C.IngestWriter(call_tool)
    with pytest.raises(C.CurationRefused):
        writer.write_sections("new", {"points": [{"migrationKey": "k", "content": "c"}]})


def test_a_refused_second_batch_reports_the_first_batch_and_records_it(tmp_path):
    """A partial run must not claim it wrote nothing, and must not lose the batch
    that DID land."""
    calls = {"n": 0}

    def call_tool(tool, args):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeCallToolResult({"created": 1})
        return FakeCallToolResult({"error": "ERR_QUOTA"})

    led = C.Ledger(tmp_path / "l.jsonl", target="new")
    plan = C.build_plan([row("a", content="Let me look."), row("b", content="Let me look again.")],
                        source_graph="old", target_graph="new", packs={})
    plan.decisions = [C.Decision(d.node_id, d.node_type, d.kind, C.CARRY, "test", d.pack,
                                 d.stable_key, d.content) for d in plan.decisions]
    res = C.run_plan(plan, writer=C.IngestWriter(call_tool, batch_size=1), ledger=led,
                     apply=True, graph_rows=ROWS)
    assert res.written == 1, "the accepted batch is reported"
    assert res.applied is True, "a partial run DID apply — the accepted batch landed"
    assert res.refused and "ERR_QUOTA" in res.refused
    assert len(led) == 1, "only the accepted batch is recorded"


def test_a_fully_refused_apply_is_not_reported_as_applied(ledger):
    """Round-33 review follow-up 1 — the exit code was right (3) and the JSON a
    driver actually reads was not: a fully-refused apply reported
    `"applied": true, "written": 0`, so a run that wrote NOTHING read as migrated.
    `applied` follows what the target ACCEPTED: no accepted key, no apply.
    """

    class RefuseFirstBatch:
        def write_sections(self, target_graph, sections):
            exc = C.CurationRefused("ingest refused the batch")
            exc.written_keys = []
            raise exc

    plan = C.build_plan([row("a", kind="decision", content="Use the graph.")],
                        source_graph="old", target_graph="new", packs={})
    # non-vacuity: there was something to write, so `applied: False` is a statement
    # about the refusal and not about an empty plan
    assert [d.disposition for d in plan.decisions] == [C.CARRY]
    res = C.run_plan(plan, writer=RefuseFirstBatch(), ledger=ledger, apply=True,
                     graph_rows=ROWS)
    assert res.written == 0
    assert res.refused and "refused the batch" in res.refused
    assert res.applied is False, "nothing was accepted, so the run did not apply"
    assert res.pending == 1, "the refused row is still owed to the target"
    assert len(ledger) == 0, "a refused batch is never recorded as migrated"


def test_an_empty_graph_id_tail_cannot_prove_two_graphs_distinct(tmp_path):
    """Round-33 follow-up 3's guarantee, made true and pinned.

    `_tail` yields "" for an id ending in `_g_`, and `_canonical_from_row` used to
    fall back to the RAW spelling — so `org_o1_g_x_g_` and `g_x_g_`, two spellings of
    ONE graph, canonicalised differently and the in-place guard PASSED. That is the
    one failure direction `_tail`'s truncation is documented never to have, so an
    empty tail must refuse instead of resolving by shape.
    """
    rows = [{"namespace": "org_o1_g_x_g_"}, {"namespace": "g_x_g_"}]
    assert C._canonical_from_row({"namespace": "org_o1_g_x_g_"}) is None
    assert C._canonical_from_row({"graph_id": "g_x_g_"}) is None
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("org_o1_g_x_g_", "g_x_g_", graph_rows=rows)
    # non-vacuity: a NON-empty tail still collapses both spellings, so the refusal
    # above is about the empty tail and not about the guard refusing everything
    assert C._canonical_from_row({"namespace": "org_o1_g_x_g_y"}) == "y"
    assert C._canonical_from_row({"graph_id": "g_x_g_y"}) == "y"


def test_a_stopped_apply_that_landed_nothing_is_not_reported_as_applied(ledger):
    """Round-33 follow-up 1, the arm OVER — `applied` is the same question here.

    The success return reported `applied=True` unconditionally, so a run STOPPED
    before its first batch accepted anything reported
    `"applied": true, "written": 0, "pending": 1` — the exact reading the refused
    arm was just fixed for, one arm over. The docstring's promise ("True only when
    the apply leg actually TOOK") was false here.
    """

    class StopBeforeFirstBatch:
        def write_sections(self, target_graph, sections):
            return {"written": [], "stopped": "off-switch engaged"}

    plan = C.build_plan([row("a", kind="decision", content="Use the graph.")],
                        source_graph="old", target_graph="new", packs={})
    # non-vacuity: there WAS something to write, so `applied: False` is a statement
    # about the stop and not about an empty plan
    assert [d.disposition for d in plan.decisions] == [C.CARRY]
    res = C.run_plan(plan, writer=StopBeforeFirstBatch(), ledger=ledger, apply=True,
                     graph_rows=ROWS)
    assert res.stopped == "off-switch engaged"
    assert res.written == 0
    assert res.applied is False, "nothing landed, so the run did not apply"
    assert res.pending == 1, "a stopped run still owes the target its rows"
    assert len(ledger) == 0


def test_a_transport_failure_mid_run_reports_what_landed(tmp_path):
    calls = {"n": 0}

    def call_tool(tool, args):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"created": 1}
        raise TimeoutError("connection dropped")

    led = C.Ledger(tmp_path / "l.jsonl", target="new")
    plan = C.build_plan([row("a", content="Let me look."), row("b", content="Let me look again.")],
                        source_graph="old", target_graph="new", packs={})
    plan.decisions = [C.Decision(d.node_id, d.node_type, d.kind, C.CARRY, "test", d.pack,
                                 d.stable_key, d.content) for d in plan.decisions]
    res = C.run_plan(plan, writer=C.IngestWriter(call_tool, batch_size=1), ledger=led,
                     apply=True, graph_rows=ROWS)
    assert res.written == 1 and "TimeoutError" in (res.refused or "")
    assert len(led) == 1


def test_ingest_refusal_envelope_is_not_a_success():
    def call_tool(tool, args):
        return {"error": "ERR_BUNDLE_INVALID", "violations": [{"section": "points"}]}

    writer = C.IngestWriter(call_tool)
    with pytest.raises(C.CurationRefused):
        writer.write_sections("new", {"points": [{"migrationKey": "k", "content": "c"}]})


# ── classification rules the inventory table cites ───────────────────────


def test_decision_is_carried():
    d = C.classify(row(kind="decision", content="We keep the old graph."), {})
    assert d.disposition == C.CARRY and "owner-level" in d.reason


def test_narrative_statement_is_not_carried():
    d = C.classify(row(content="Let me check the CI config first."), {})
    assert d.disposition == C.NOT_CARRIED and "narration" in d.reason


def test_bare_definite_object_is_not_carried():
    d = C.classify(row(node_type="object", kind="dev:code", content="the checkout"), {})
    assert d.disposition == C.NOT_CARRIED and "mention" in d.reason


def test_object_with_referent_is_undecidable():
    d = C.classify(row(node_type="object", kind="dev:bug", content="Capture cap lockout"), {})
    assert d.disposition == C.UNDECIDABLE


def test_session_source_is_undecidable_not_noise():
    d = C.classify(row(node_type="source", kind="agentSession", content="session:abc"), {})
    assert d.disposition == C.UNDECIDABLE
    assert "0 rows" in d.reason  # the reason names WHY it cannot simply be dropped


def test_session_provenance_does_not_make_a_claim_noise():
    d = C.classify(row(content="The cap is held deliberately until the new extractor ships.",
                       sourceKind="agentSession"), {})
    assert d.disposition == C.UNDECIDABLE, "a session-derived claim is still a claim"


def test_operator_is_carried_as_reasoning():
    d = C.classify(row(node_type="operator", kind=None, content="claim A implies claim B"), {})
    assert d.disposition == C.CARRY and "reasoning" in d.reason


def test_an_empty_operator_row_is_not_carried():
    """The empty rule runs before the operator branch too, or an operator would be
    carried as an empty Point."""
    d = C.classify(row(node_type="operator", kind=None, content="  "), {})
    assert d.disposition == C.NOT_CARRIED


def test_empty_evidence_is_not_carried():
    """An empty evidence row would otherwise be carried as an empty point."""
    d = C.classify(row(kind="evidence", content="   "), {})
    assert d.disposition == C.NOT_CARRIED and "empty" in d.reason


def test_an_empty_signal_kind_is_not_carried():
    """The empty rule is checked BEFORE the kind rules, for every kind."""
    for kind in ("decision", "option", "finding", "useCase", "jobToBeDone"):
        d = C.classify(row(kind=kind, content="   "), {})
        assert d.disposition == C.NOT_CARRIED, kind


def test_a_carried_source_is_sent_as_a_source_not_a_point():
    """A document is a :Source (D10). Writing it as a Point re-types it and gives
    it a document-vocabulary pointKind."""
    plan = C.build_plan([row("s1", node_type="source", kind="article", content="https://x/y")],
                        source_graph="old", target_graph="new", packs={})
    bundle = plan.bundle()
    assert bundle["points"] == []
    assert bundle["sources"] and bundle["sources"][0]["url"] == "https://x/y"
    assert set(bundle["sources"][0]) == {"url", "sourceKind"}, (
        ":Source accepts only declared props (#3998 drops the rest silently)")


def test_a_carried_source_is_reached_by_the_writer(tmp_path):
    """The PLAN → writer handoff for a Source, end to end (a re-run is idempotent).

    Round-30: this docstring used to claim it pinned the round-5 defect. It does not —
    `CountingWriter` echoes whatever sections it is handed, so a writer that only sent
    `points` would still pass. That defect is pinned by
    `test_the_wire_carries_the_sources_section_not_only_points`; this test pins the
    handoff and the idempotency."""
    plan = C.build_plan([row("s1", node_type="source", kind="article", content="https://x/y")],
                        source_graph="old", target_graph="new", packs={})
    w = CountingWriter()
    led = C.Ledger(tmp_path / "src.jsonl", target="new")
    res = C.run_plan(plan, writer=w, ledger=led, apply=True, graph_rows=ROWS)
    assert res.written == 1
    assert w.calls and w.calls[0][1], "the source reached the writer"
    # …and a re-run writes nothing
    again = C.run_plan(plan, writer=w, ledger=led, apply=True, graph_rows=ROWS)
    assert again.written == 0 and len(w.calls) == 1


def test_namespaced_signal_kind_is_still_signal():
    d = C.classify(row(kind="core:decision", content="We keep the old graph."), {})
    assert d.disposition == C.CARRY


def test_pack_resolution_bare_and_namespaced():
    packs = {("pointKind", "bug"): "dev", ("objectKind", "code"): "dev"}
    assert C.pack_for("bug", "point", packs) == "dev"
    assert C.pack_for("dev:bug", "point", packs) == "dev"
    assert C.pack_for("something-else", "point", packs) is None


# ── the census is what gets posted on the issue ──────────────────────────


def test_census_counts_every_row_once():
    plan = C.build_plan(
        [row("a", kind="decision", content="d"), row("b", content="Let me look."),
         row("c", node_type="object", kind="dev:code", content="the rail")],
        source_graph="old", target_graph="new", packs={},
    )
    census = plan.census()
    assert census["total_rows"] == 3
    assert sum(census["totals"].values()) == 3
    assert census["totals"][C.CARRY] == 1
    assert census["totals"][C.NOT_CARRIED] == 2


def test_bundle_items_carry_content():
    """`ingest` refuses the WHOLE bundle when a point item has no content
    (sdk._check_item_shape) — a bundle without it writes zero rows."""
    plan = C.build_plan([row("a", kind="decision", content="We keep the old graph.")],
                        source_graph="old", target_graph="new", packs={})
    item = plan.bundle()["points"][0]
    assert item["content"] == "We keep the old graph."
    assert item["migrationKey"].startswith(C.MIGRATION_KEY_PREFIX)
    assert "ref" not in item, "the bundle mints no bundle-local refs at all"


def test_bundle_survives_the_ingest_canonicaliser():
    """`ref` and `migrationKey` must not be the same string.

    The canonicaliser expands any field VALUE equal to a bundle ref, so
    `ref == migrationKey` makes each item expand into itself and ingest refuses
    the whole bundle ("ref expansion exceeded depth 32"). Verified against
    tortoise.canonical, the module the real `ingest` runs.
    """
    from tortoise import canonical

    plan = C.build_plan([row("a", kind="decision", content="We keep the old graph."),
                         row("b", kind="finding", content="A finding.")],
                        source_graph="old", target_graph="new", packs={})
    bundle = {"points": plan.bundle()["points"]}
    resolved = canonical.resolve_refs(bundle)          # raises on a ref cycle
    assert len(resolved["points"]) == 2
    assert canonical.canonical_bundle(bundle)          # deterministic, no raise
    assert not any("ref" in p for p in bundle["points"]), (
        "a bundle-local ref is expanded wherever it appears as a field VALUE, so any "
        "ref we mint can collide with a row's own content/id — omit it instead")


def test_a_row_whose_content_looks_like_a_bundle_ref_cannot_cycle():
    """Adversarial content (`cur0`, a migration key, a node id) must not be able to
    make the bundle uncanonicalisable — the reason this bundle mints no refs."""
    from tortoise import canonical

    plan = C.build_plan([row("cur0", kind="decision", content="cur0"),
                         row("curate-11469:point:decision:x", kind="finding", content="cur1")],
                        source_graph="old", target_graph="new", packs={})
    bundle = {"points": plan.bundle()["points"]}
    resolved = canonical.resolve_refs(bundle)
    assert len(resolved["points"]) == 2
    assert canonical.canonical_bundle(bundle)


def test_bundle_carries_no_deletion():
    plan = C.build_plan([row("a", kind="decision", content="d")], source_graph="old", target_graph="new", packs={})
    body = json.dumps(plan.bundle())
    for verb in ("delete", "DELETE", "retract", "supersede"):
        assert verb not in body


# ── round-6 review findings ───────────────────────────────────────────────


def test_a_text_result_this_pass_cannot_interpret_is_a_refusal():
    """Round-6: a str/list result is NOT evidence of a write; `normalise_tool_result`
    passes it through untouched, so the refusal test must treat it as a refusal."""
    def call_tool(*a, **k):
        return '{"error": "ERR_QUOTA"}'  # refusal, but as text
    with pytest.raises(C.CurationRefused):
        C.IngestWriter(call_tool).write_sections(
            "new", {"points": [{"migrationKey": "k", "content": "c"}]})


def test_workflow_is_not_a_signal_kind():
    """`workflow` is an objectKind (pack_registry.CANONICAL_OBJECT_KINDS), so a
    Point typed with it would be a claim carrying an object kind."""
    assert "workflow" not in C.SIGNAL_KINDS
    assert "requirement" not in C.SIGNAL_KINDS


def test_a_source_with_an_empty_url_is_not_carried():
    d = C.classify(row("s", node_type="source", kind="article", content=""), {})
    assert d.disposition == C.NOT_CARRIED


def test_a_carried_event_is_undecidable_not_silently_dropped():
    """Round-6: an Event has no bundle section, so `carry` would report a row that
    nothing sends. It must be undecidable until the write path can represent it."""
    d = C.classify(row("e", node_type="event", kind="deployment", content="shipped"), {})
    assert d.disposition == C.UNDECIDABLE


def test_a_bundle_refuses_a_carried_row_with_no_section():
    plan = C.Plan(source_graph="old", target_graph="new", decisions=[
        C.Decision("x", "object", "service", C.CARRY, "manual", None, "k", "svc")])
    with pytest.raises(ValueError, match="no wire section"):
        plan.bundle()


def test_the_wire_never_carries_a_migration_key_on_a_source():
    """#3998 drops undeclared :Source props (with a denial log per row)."""
    seen = {}
    def call_tool(_name, args):
        seen.update(args["bundle"])
        return {"ok": True, "created": 1}
    C.IngestWriter(call_tool).write_sections(
        "new", {"sources": [{"url": "https://x/y", "sourceKind": "article",
                             "migrationKey": "k1"}]})
    assert seen["sources"] == [{"url": "https://x/y", "sourceKind": "article"}]


def test_the_inventory_report_names_the_source_filter():
    """Round-6: the source listing is a SUBSET (`documentKind IS NULL`), so a
    consumer of the JSON must be told — the gap cannot live only in a docstring."""
    import importlib.util
    import sys as _sys
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "tools" / "curate_graph.py"
    spec = importlib.util.spec_from_file_location("curate_graph_11469", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["curate_graph_11469"] = mod
    spec.loader.exec_module(mod)
    notes = mod.NOT_ENUMERABLE
    assert "source" in notes and "documentKind IS NULL" in notes["source"]


# ── round-7 review findings ───────────────────────────────────────────────


def test_two_sources_sharing_a_url_but_not_a_kind_both_reach_the_wire(tmp_path):
    """Round-7: a url-only ledger key collapsed two Source rows onto one entry and
    reported `skipped_known` on a FIRST run with an EMPTY ledger."""
    plan = C.build_plan(
        [row("s1", node_type="source", kind="article", content="https://x/y"),
         row("s2", node_type="source", kind="document", content="https://x/y")],
        source_graph="old", target_graph="new", packs={})
    w = CountingWriter()
    res = C.run_plan(plan, writer=w, ledger=C.Ledger(tmp_path / "l.jsonl", target="new"),
                     apply=True, graph_rows=ROWS)
    assert res.skipped_known == 0, "nothing was written yet, so nothing is 'already known'"
    assert res.written == 2 and len(w.calls[0][1]) == 2


def test_a_kept_point_still_carries_its_migration_key():
    """Round-7: the key was stripped from EVERY section, so a migrated point was no
    longer identifiable in the target — the module's stated purpose."""
    seen = {}
    def call_tool(_name, args):
        seen.update(args["bundle"])
        return {"ok": True, "created": 1}
    C.IngestWriter(call_tool).write_sections(
        "new", {"points": [{"migrationKey": "k1", "kind": "statement", "content": "c"}]})
    assert seen["points"][0]["migrationKey"] == "k1"


def test_a_real_content_row_is_not_dropped_for_an_empty_keeper():
    """Round-7: an empty row sharing the content hash made the real row 'follow a
    keeper that carries nothing' — the content would have been carried nowhere."""
    a = row("a", kind="decision", content="   ", content_hash="h1")   # empty keeper candidate
    b = row("b", kind="decision", content="ship it", content_hash="h1")  # real content, same hash
    plan = C.build_plan([a, b], source_graph="old", target_graph="new", packs={})
    carried = [d.node_id for d in plan.carry_rows()]
    assert carried == ["b"], carried


# ── round-8 review findings ───────────────────────────────────────────────


def _tool_module():
    import importlib.util
    import sys as _sys
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "tools" / "curate_graph.py"
    spec = importlib.util.spec_from_file_location("curate_graph_11469_r8", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["curate_graph_11469_r8"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_a_source_ledger_key_cannot_collide_on_the_delimiter(tmp_path):
    """Round-8 blocking: the key joined (kind, url) with ':', so a kind containing
    ':' collided with a different (kind, url) pair and one row was reported
    `skipped_known` on a FIRST run with an EMPTY ledger."""
    plan = C.build_plan(
        [row("s1", node_type="source", kind="article", content="https://x/y:z"),
         row("s2", node_type="source", kind="article:https", content="//x/y:z")],
        source_graph="old", target_graph="new", packs={})
    w = CountingWriter()
    res = C.run_plan(plan, writer=w, ledger=C.Ledger(tmp_path / "l.jsonl", target="new"),
                     apply=True, graph_rows=ROWS)
    assert res.skipped_known == 0 and res.written == 2


def test_an_error_envelope_is_never_reported_as_zero_rows():
    """Round-8: no test reached the reader at all, so reverting its fail-closed
    unwrap reproduced `sources_read: 0` + exit 0 with nothing failing."""
    mod = _tool_module()
    reader = mod.MCPReader(lambda name, args: {"error": "ERR_QUOTA", "code": "QUOTA"}, None)
    with pytest.raises(RuntimeError, match="refused"):
        reader.read_sources()


def test_a_transport_failure_does_not_fall_back_to_another_graph():
    """Round-8: ANY exception re-read a DIFFERENT graph under the requested graph's
    identity — a 429 must raise, not silently switch targets."""
    mod = _tool_module()
    calls = []

    def call(_name, args):
        calls.append(args)
        raise TimeoutError("upstream 429")
    reader = mod.MCPReader(call, "org_x_g_aaa")
    with pytest.raises(TimeoutError):
        reader.read_sources()
    assert len(calls) == 1, "a transport error must not trigger a second, unbound read"


def test_a_graph_refusal_does_fall_back_and_says_so_once_per_section():
    mod = _tool_module()

    def call(_name, args):
        if "graph" in args:
            return {"error": "unknown graph 'org_x_g_aaa'", "code": "GRAPH_WRITE_REFUSED"}
        return {"result": [{"url": "https://x/y", "sourceKind": "article"}]}
    reader = mod.MCPReader(call, "org_x_g_aaa")
    rows, _ = reader.read_sources()
    assert [r.content for r in rows] == ["https://x/y"]
    assert reader.note and "credential" in reader.note
    reader.read_sources()  # the same section twice is ONE note (deduped)
    reader._section("status")  # a second falling section must APPEND, not overwrite
    assert reader.note.count("credential") == 2, reader.note


def test_an_iserror_result_without_an_error_key_is_a_refusal():
    """The dangerous sub-case: `isError` says refused, the payload has no `error`
    key — counting it as a write is how a ledger claims rows that do not exist."""
    class Result:
        """SNAKE_CASE ONLY, like the live client (#4292): a fixture that sets both
        spellings cannot detect a regression to camelCase-only reads."""

        def __init__(self):
            self.is_error = True           # snake_case only (live client, #4292)
            self.content = [{"type": "text", "text": '{"created": 3}'}]
            self.structured_content = {"result": {"created": 3}}
    with pytest.raises(C.CurationRefused):
        C.normalise_tool_result(Result())


def test_a_quota_envelope_does_not_re_read_a_different_graph():
    """Round-9 blocking: the ENVELOPE arm of `_section` had no refusal gate, so a
    quota error made the census come from the bound graph while the report named
    the requested one."""
    mod = _tool_module()
    calls = []

    def call(_name, args):
        calls.append(args)
        return {"error": "ERR_QUOTA: budget exceeded", "code": "QUOTA"}
    reader = mod.MCPReader(call, "org_x_g_aaa")
    with pytest.raises(RuntimeError, match="QUOTA"):
        reader.read_sources()
    assert len(calls) == 1, "a quota error must not trigger an unbound read"


def test_the_entry_point_reader_refuses_a_zero_or_short_point_read():
    """No test reached `read_points`, so reverting either guard left 66 green."""
    mod = _tool_module()

    def zero(_name, args):
        return {"result": [], "total": 19595}
    with pytest.raises(RuntimeError, match="0 points"):
        mod.MCPReader(zero, None).read_points()

    def short(_name, args):
        one = {"id": "p1", "kind": "decision", "content": "c"}
        return {"result": [one], "total": 19595}
    with pytest.raises(RuntimeError, match="incomplete"):
        mod.MCPReader(short, None).read_points()


def test_an_unqualified_read_is_never_labelled_default(monkeypatch, tmp_path):
    """Round-9: an omitted `graph` reads the CREDENTIAL's own graph (#10084), so a
    report saying "default" states the opposite — and the inventory and the plan
    must use the SAME label, or one artifact contradicts itself."""
    mod = _tool_module()

    def call(name, args):  # a graph-bound surface: everything answers, nothing writes
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        if name == "tortoise_graph_overview" and args.get("section") == "sources":
            return {"result": [{"url": "https://x/y", "sourceKind": "article"}]}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["inventory", "--out", str(tmp_path / "inv.json"), "--no-verify-read"])
    assert rc == 0
    report = json.loads((tmp_path / "inv.json").read_text())
    # the LABEL itself, not just "it ran"
    assert report["graph"] == "(credential-bound)"
    assert report["census"]["source_graph"] == "(credential-bound)"


def test_the_unqualified_label_is_credential_bound_everywhere():
    import inspect
    mod = _tool_module()
    src = inspect.getsource(mod)
    assert "args.graph or \"(credential-bound)\"" in src
    assert 'args.graph or "default"' not in src


# ── round-10 review findings ──────────────────────────────────────────────


def test_the_fallback_vocabulary_matches_the_real_refusal_producers():
    """Round-10: the markers were invented, so the documented credential-bound
    fallback was unreachable while a bogus permission string passed the gate."""
    from tortoise.mcp_auth import _retarget_refusal
    mod = _tool_module()
    real = str(_retarget_refusal("org_x_g_aaa"))
    assert mod._looks_like_graph_refusal(real), real
    assert mod._looks_like_graph_refusal("No graph 'foo' is reachable from this workspace")
    assert not mod._looks_like_graph_refusal("permission denied: scope graph read not allowed")
    assert not mod._looks_like_graph_refusal("ERR_QUOTA: budget exceeded")


def test_a_duplicate_source_row_is_not_reported_as_already_migrated(tmp_path):
    """Round-10/17: a plan carrying the same Source twice is two carry decisions but
    ONE write; counting the second as `skipped_known` claims a migration that never
    happened. NOTE this shape cannot come from `read_sources` — `sdk.list_sources`
    groups by (url, sourceKind) — it comes from a hand-built plan or another reader;
    the counter exists so the arithmetic reconciles either way."""
    plan = C.build_plan(
        [row("https://x/y", node_type="source", kind="article", content="https://x/y"),
         row("https://x/y", node_type="source", kind="article", content="https://x/y")],
        source_graph="old", target_graph="new", packs={})
    res = C.run_plan(plan, writer=CountingWriter(),
                     ledger=C.Ledger(tmp_path / "l.jsonl", target="new"),
                     apply=True, graph_rows=ROWS)
    assert res.skipped_known == 0, "nothing was in the ledger"
    assert res.duplicate_rows == 1
    assert res.written + res.skipped_known + res.duplicate_rows == len(plan.carry_rows())


def test_a_dry_run_reports_what_the_ledger_already_holds(tmp_path):
    """Round-10: the dry run returned skipped_known=0 unconditionally, so the only
    mode runnable today could not say how much had already migrated."""
    plan = C.build_plan([row("p1", kind="decision", content="ship it")],
                        source_graph="old", target_graph="new", packs={})
    led = C.Ledger(tmp_path / "l.jsonl", target="new")
    C.run_plan(plan, writer=CountingWriter(), ledger=led, apply=True, graph_rows=ROWS)
    dry = C.run_plan(plan, writer=CountingWriter(), ledger=led, apply=False, graph_rows=ROWS)
    assert dry.skipped_known == 1 and dry.written == 0


# ── round-11 review findings ──────────────────────────────────────────────

#: The row shape the real surface returns: NAMESPACES only (`tortoise_list_graphs()
#: -> list[str]`), so an id-shaped spelling a row does not name is UNRESOLVED.
NAMESPACE_ONLY_ROWS = [
    {"namespace": "team_3326a01ea34ae595d84de5d8f9", "graph_id": "default"},
    {"namespace": "org_3326a01ea34ae595d84de5d8f9_g_2b7e4c9d1a6f8035"},
]


def test_the_tenant_graphs_id_spelling_is_not_treated_as_a_distinct_graph():
    """Round-11 BLOCKING: with namespace-only rows, `team_<org>` canonicalised to the
    org id while `g_<tenant-gid>` fell back to its shape — so the guard PASSED, and
    an `--apply` wrote to the SOURCE graph in place (#11469's central guarantee)."""
    with pytest.raises(C.CurationRefused, match=r"cannot resolve|same graph"):
        C.assert_distinct_graphs("g_0bcdb207ef7336c3",
                                 "team_3326a01ea34ae595d84de5d8f9",
                                 graph_rows=NAMESPACE_ONLY_ROWS)


def test_an_id_no_row_names_is_unresolved_when_rows_are_available():
    assert C.canonical_graph_id("g_deadbeefdeadbeef", NAMESPACE_ONLY_ROWS) is None
    # …while the shapes the rows DO carry still resolve.
    assert C.canonical_graph_id("team_3326a01ea34ae595d84de5d8f9", NAMESPACE_ONLY_ROWS)
    assert C.canonical_graph_id("org_3326a01ea34ae595d84de5d8f9_g_2b7e4c9d1a6f8035",
                                NAMESPACE_ONLY_ROWS)


def test_the_refusal_gate_does_not_trip_on_a_db_error_that_mentions_graph():
    """Round-11: "FalkorDB: graph not found" re-read a DIFFERENT graph under the
    requested graph's name; only a message quoting the graph argument may fall back."""
    mod = _tool_module()
    assert not mod._looks_like_graph_refusal("FalkorDB: graph not found")
    assert not mod._looks_like_graph_refusal("InternalError: unknown error while reading the graph")
    assert mod._looks_like_graph_refusal("unknown graph 'live-v4'")


# ── round-12 review findings ──────────────────────────────────────────────


def test_the_default_sentinel_is_not_an_identity():
    """Round-12: `{"graph_id": "default", "namespace": "team_x"}` canonicalised to
    "default" while a plain `{"namespace": "team_x"}` row gave "x", so two rows
    describing ONE graph let `--graph default --target team_x` write in place."""
    rows = [{"namespace": "team_3326a01ea34ae595d84de5d8f9"},
            {"graph_id": "default", "namespace": "team_3326a01ea34ae595d84de5d8f9",
             "name": "default"}]
    # the sentinel resolves to the ROW's real identity, not to the string "default"
    assert C.canonical_graph_id("default", rows) == "3326a01ea34ae595d84de5d8f9"
    assert C.canonical_graph_id("team_3326a01ea34ae595d84de5d8f9", rows) == \
        C.canonical_graph_id("default", rows)
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("default", "team_3326a01ea34ae595d84de5d8f9",
                                 graph_rows=rows)


def test_rows_that_disagree_about_one_ref_are_refused():
    """Two rows for one ref with different identities must never pick one."""
    rows = [{"namespace": "team_x"}, {"graph_id": "g_other", "namespace": "team_x"}]
    assert C.canonical_graph_id("team_x", rows) is None
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("g_other", "team_x", graph_rows=rows)


def test_a_total_pack_catalog_failure_is_visible_in_the_census(monkeypatch):
    """Round-29 review finding 3: `pack_kind_map` promised that an empty map was
    "visible in the census", but `census()` had no pack dimension at all — a run
    where EVERY manifest failed to load built a fully pack-less plan, stamped
    `pack: null` on every carried row, and reported a census byte-identical to a
    healthy run's. The destination is the pack-FILED graph, so unobservable pack
    loss is the failure this pins.

    Round-30 review finding 1 fixed two ways the first version of this test did not
    actually pin it. (a) `rows_without_pack` counted EVERY decision, so its value was
    set by the population left behind rather than by pack loss — measured with the
    real catalog, only the strategy kinds resolve, so a healthy run over the
    documented carry set read 125 unfiled of 127. It now counts CARRIED rows only.
    (b) the healthy arm used `core`, a namespace this same file asserts does not
    exist (see `test_an_unknown_kind_prefix_is_not_pack_ownership`), so it never
    proved a healthy arm at all. It now uses a REAL pack for a REAL carried kind.
    **The two arms must differ in the VALUE, not merely in key presence.**
    """
    # `jobToBeDone` is a carried strategy kind, and the installed catalog files it
    # under `product-strategy` — so this is a healthy arm in the true sense.
    healthy_mapping, healthy_note = C.load_pack_kind_map()
    assert healthy_note is None, f"this test needs a loadable catalog, got: {healthy_note!r}"
    assert C.pack_for("jobToBeDone", "point", healthy_mapping) == "product-strategy"

    def plan_row():
        # The fixture MUST carry non-carried rows that also have no pack, or
        # "carried rows only" and "every decision" compute the SAME number and the
        # test cannot tell them apart (measured: the single-row version passed with
        # the buggy `every decision` form). The narrative statements below are
        # not-carried and unfiled — they are exactly the population that made the
        # old field read ~the same in both catalog states.
        return C.build_plan(
            [row("p1", kind="jobToBeDone", content="a durable job"),
             row("n1", content="Let me look."),
             row("n2", content="Let me check that.")],
            source_graph="old", target_graph="new",
        )

    healthy = plan_row()
    assert len(healthy.carry_rows()) == 1, (
        f"the fixture must carry exactly the filed row; got "
        f"{[d.node_id for d in healthy.carry_rows()]}")
    healthy_census = healthy.census()
    assert healthy.pack_catalog_note is None
    assert healthy_census["carried_rows"] == 1
    assert healthy_census["rows_without_pack"] == 0, (
        "a healthy catalog files every CARRIED row; non-carried rows must not be counted, "
        f"or the field tracks the population left behind: {healthy_census}")

    # ── now make the catalog unreadable, and require the reported state to CHANGE ──
    import tortoise.pack_registry as reg

    def boom(*_a, **_kw):
        raise OSError("manifest directory unreadable")

    monkeypatch.setattr(reg, "default_packs_dir", boom)

    mapping, note = C.load_pack_kind_map()
    assert mapping == {}, "the catalog still degrades rather than blocking the inventory"
    assert note and "pack catalog unavailable" in note
    assert "OSError" in note, "the note names the cause, not just the symptom"

    broken = plan_row()
    broken_census = broken.census()
    assert broken.pack_catalog_note == note
    assert broken_census["pack_catalog_note"] == note
    assert broken_census["carried_rows"] == healthy_census["carried_rows"], (
        "the same plan: only the pack dimension changed")
    assert broken_census["rows_without_pack"] == broken_census["carried_rows"]
    # THE ASSERTION THAT MATTERS: the state is distinguishable by value.
    assert broken_census["rows_without_pack"] != healthy_census["rows_without_pack"], (
        "a total pack loss must MOVE this field — otherwise it reports nothing")


def test_an_unknown_kind_prefix_is_not_pack_ownership():
    """Round-12: `core:decision` returned pack "core", but no `core` pack exists —
    a migrated node would assert ownership of a pack that is not installed."""
    packs = C.pack_kind_map()
    assert "core" not in set(packs.values())
    assert C.pack_for("core:decision", "point", packs) != "core"
    # a REAL namespace still resolves
    ns = sorted(set(packs.values()))[0]
    assert C.pack_for(f"{ns}:decision", "point", packs) == ns


def test_a_points_refusal_names_its_real_cause():
    """Round-12: a `_safe` quota envelope reads as "0 rows" (is_error=False), and the
    refusal discarded the cause, sending the operator to the wrong remedy."""
    mod = _tool_module()

    def call(_name, args):
        return {"error": "ERR_QUOTA: budget exceeded", "code": "QUOTA"}
    with pytest.raises(RuntimeError, match="QUOTA"):
        mod.MCPReader(call, None).read_points(verify=False)


# ── round-13 review findings ──────────────────────────────────────────────


def test_the_counters_span_the_plan_in_every_mode(tmp_path):
    """Round-13: the pending set was discarded, so a DRY run (the only runnable mode
    today) reported 0/0/0 over a census of carry rows and a driver could read that
    as "nothing to migrate".

    Round-29 review finding 1: the name said "every mode" and the design doc named
    `partial refusal` and `mid-run stop` by hand, but this test covered only dry /
    apply / re-run — the two modes a 429 on the capped endpoint actually produces
    had no reconciliation test at all. Both arms are below.
    """
    rows = [row(f"p{i}", kind="decision", content=f"claim {i}") for i in range(3)] + [
        row("https://x/y", node_type="source", kind="article", content="https://x/y")]
    plan = C.build_plan(rows, source_graph="old", target_graph="new", packs={})
    carry = len(plan.carry_rows())
    led = C.Ledger(tmp_path / "l.jsonl", target="new")

    # `pending` = rows NOT already in the target (attempted, or would be in a dry
    # run), so THREE counters SPAN the plan: skipped + duplicates + pending.
    # `written` is a SUBSET of `pending`, never a fourth addend — see the design
    # doc's `dry by default` row and round-29 review finding 1.
    dry = C.run_plan(plan, writer=CountingWriter(), ledger=led, apply=False, graph_rows=ROWS)
    assert dry.skipped_known + dry.duplicate_rows + dry.pending == carry
    assert dry.pending == carry, "a dry run must say how much there is to migrate"

    first = C.run_plan(plan, writer=CountingWriter(), ledger=led, apply=True, graph_rows=ROWS)
    assert first.skipped_known + first.duplicate_rows + first.pending == carry
    assert first.written == first.pending
    again = C.run_plan(plan, writer=CountingWriter(), ledger=led, apply=True, graph_rows=ROWS)
    assert again.skipped_known + again.duplicate_rows + again.pending == carry
    assert again.written == 0 and again.pending == 0 and again.skipped_known == carry

    # ── partial refusal (the 429 arm): the target accepts, then refuses ──────
    # The refusal comes from INSIDE one `write_sections` call, carrying the keys a
    # batch had already had accepted — that is the shape `IngestWriter` produces
    # when its second batch is refused, and `run_plan` calls the writer once.
    class _AcceptsThenRefuses:
        def write_sections(self, target, sections):
            first_key = sections["points"][0]["migrationKey"]
            raise C.CurationRefused("ERR_QUOTA", written_keys=[first_key])

    led2 = C.Ledger(tmp_path / "l2.jsonl", target="new")
    part = C.run_plan(plan, writer=_AcceptsThenRefuses(), ledger=led2, apply=True,
                      graph_rows=ROWS)
    assert part.refused and "ERR_QUOTA" in part.refused
    assert part.written == 1, "the accepted batch is reported"
    # The three counters still SPAN the census, and `written` stays inside `pending`.
    assert part.skipped_known + part.duplicate_rows + part.pending == carry
    assert 0 < part.written <= part.pending, (
        f"written ({part.written}) is a subset of pending ({part.pending}), never a fourth "
        f"addend of the census ({carry})")

    # ── mid-run stop (the off-switch arm): disarms between batches ───────────
    armed = {"v": True}

    def armed_then_off():
        v = armed["v"]
        armed["v"] = False
        return v

    def accept_one(_tool, _args):
        return {"created": 1}

    led3 = C.Ledger(tmp_path / "l3.jsonl", target="new")
    stop = C.run_plan(
        plan,
        writer=C.IngestWriter(accept_one, batch_size=1, armed=armed_then_off),
        ledger=led3,
        apply=True,
        graph_rows=ROWS,
    )
    assert stop.skipped_known + stop.duplicate_rows + stop.pending == carry
    assert 0 < stop.written <= stop.pending, (
        f"a mid-run stop reports what landed, inside pending — got written={stop.written}, "
        f"pending={stop.pending}, census={carry}")


class _ShiftingSurface:
    """A population that loses a row mid-walk: the offset walk repeats a row."""

    def __init__(self):
        self.pages = 0
        self.population = [{"id": f"p{i}", "kind": "decision", "content": f"c{i}"}
                           for i in range(6)]

    def __call__(self, _name, args):
        self.pages += 1
        if self.pages == 2:            # p1 is retracted between page 1 and page 2
            self.population.pop(1)
        off = int(args.get("offset") or 0)
        lim = int(args.get("limit") or 2)
        return {"result": self.population[off:off + lim], "total": len(self.population)}


def test_a_shifted_pagination_is_refused_not_silently_partial():
    """Round-13: a retraction mid-walk shifted later rows up, so one live row was
    skipped and a tombstone re-read — with `points_read == total`, so no guard fired."""
    mod = _tool_module()
    reader = mod.MCPReader(_ShiftingSurface(), None)
    with pytest.raises(RuntimeError, match=r"shifted|twice"):
        reader.read_points(page=2)


def test_a_refused_run_still_reports_what_the_plan_owes(tmp_path):
    """Round-13 follow-up: the pre-write refusal paths returned pending=0, the same
    "reads as nothing to migrate" shape the dry run had."""
    plan = C.build_plan([row("p1", kind="decision", content="a"),
                         row("p2", kind="decision", content="b")],
                        source_graph="old", target_graph="new", packs={})
    off = C.run_plan(plan, writer=CountingWriter(),
                     ledger=C.Ledger(tmp_path / "off.jsonl", target="new"),
                     apply=True, off=True, graph_rows=ROWS)
    assert off.pending == 2 and off.written == 0
    refused = C.run_plan(plan, writer=CountingWriter(),
                         ledger=C.Ledger(tmp_path / "ref.jsonl", target="new"),
                         apply=True, graph_rows=[{"namespace": "org_9_g_aaa"}])
    assert refused.pending == 2 and refused.refused


class _ConstantTotalDuplicateSurface:
    """A walk that re-serves a row while the reported total stays the same — the
    case the total-change guard cannot see."""

    def __init__(self):
        self.pages = 0

    def __call__(self, _name, args):
        self.pages += 1
        if self.pages == 1:
            return {"result": [{"id": "p0", "kind": "decision", "content": "a"}], "total": 3}
        # the walk re-serves p0 from a LATER page instead of moving on
        return {"result": [{"id": "p0", "kind": "decision", "content": "a"},
                           {"id": "p1", "kind": "decision", "content": "b"}], "total": 3}


def test_a_row_served_twice_is_refused_even_when_the_total_is_stable():
    mod = _tool_module()
    with pytest.raises(RuntimeError, match=r"earlier page"):
        mod.MCPReader(_ConstantTotalDuplicateSurface(), None).read_points(page=1, verify=False)


def test_two_nodes_sharing_an_id_in_one_page_are_not_a_pagination_shift():
    """Round-14: `create_point(id=X)` is not unique, so an id fan-out is a legal
    graph shape — it must not be reported as a concurrency problem."""
    mod = _tool_module()

    def call(_name, args):
        return {"result": [{"id": "rv-a", "kind": "decision", "content": "one"},
                           {"id": "rv-a", "kind": "decision", "content": "two"}],
                "total": 2}
    rows, total = mod.MCPReader(call, None).read_points(page=500, verify=False)
    assert [r.content for r in rows] == ["one", "two"] and total == 2


def test_a_truncated_last_page_whose_total_moved_is_refused():
    """Round-14: the empty-batch `break` ran BEFORE the totals check, so a final page
    that returned nothing with a changed total fell through as a complete census."""
    mod = _tool_module()

    def call(_name, args):
        off = int(args.get("offset") or 0)
        if off == 0:
            return {"result": [{"id": f"p{i}", "kind": "decision", "content": str(i)}
                               for i in range(4)], "total": 6}
        # the population collapsed below the offset; total moved from 6 to 4
        return {"result": [], "total": 4}
    with pytest.raises(RuntimeError, match=r"shifted|incomplete|refused"):
        mod.MCPReader(call, None).read_points(page=4)


def test_a_refused_run_over_a_landed_plan_owes_nothing(tmp_path):
    """Round-14: the pre-write refusal paths hard-coded 0/0 and a raw carry total, so
    a run over a plan that had already landed still said "N pending"."""
    plan = C.build_plan([row("p1", kind="decision", content="a"),
                         row("p2", kind="decision", content="b")],
                        source_graph="old", target_graph="new", packs={})
    led = C.Ledger(tmp_path / "l.jsonl", target="new")
    C.run_plan(plan, writer=CountingWriter(), ledger=led, apply=True, graph_rows=ROWS)
    off = C.run_plan(plan, writer=CountingWriter(), ledger=led, apply=True, off=True,
                     graph_rows=ROWS)
    assert (off.pending, off.skipped_known) == (0, 2), (off.pending, off.skipped_known)


def test_the_plan_can_emit_row_level_reasons(monkeypatch, tmp_path):
    """Round-14: the doc claimed every row is reported with its reason, but no payload
    carried one — the census is aggregate by design."""
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "we ship"},
                               {"id": "p2", "kind": "statement", "content": "narration"}],
                    "total": 2}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    rows_out = tmp_path / "rows.jsonl"
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--out", str(tmp_path / "plan.json"), "--rows", str(rows_out),
                   "--ledger", str(tmp_path / "l.jsonl"),   # never ~/.tortoise/…
                   "--no-verify-read"])
    assert rc == 0
    lines = [json.loads(x) for x in rows_out.read_text().splitlines() if x.strip()]
    assert {d["node_id"] for d in lines} == {"p1", "p2"}
    assert all(d["reason"] and d["disposition"] for d in lines)


def test_two_nodes_sharing_an_id_across_pages_are_not_a_pagination_shift():
    """Round-15: an id fan-out whose twins sort onto DIFFERENT pages (created at
    different times) is the more likely half of a legal shape — the guard must key on
    the node's content, not its id."""
    mod = _tool_module()
    pop = [{"id": "dup", "kind": "decision", "content": "first"},          # 0
           {"id": "a", "kind": "decision", "content": "a"},                # 1
           {"id": "b", "kind": "decision", "content": "b"},                # 2
           {"id": "c", "kind": "decision", "content": "c"},                # 3
           {"id": "dup", "kind": "decision", "content": "second"},         # 4
           {"id": "d", "kind": "decision", "content": "d"}]                # 5

    def call(_name, args):
        off = int(args.get("offset") or 0)
        lim = int(args.get("limit") or 2)
        return {"result": pop[off:off + lim], "total": len(pop)}
    rows, total = mod.MCPReader(call, None).read_points(page=2, verify=False)
    assert len(rows) == 6 and total == 6


def test_the_bundle_passes_ingests_phase1_validator():
    """Round-16: the doc claimed "the bundle is ingest-valid" while every cited test
    exercised `tortoise.canonical` only — adding an illegal point prop would be a
    whole-batch Phase-1 refusal at run time with all tests green."""
    from tortoise.sdk import TortoiseSDK
    plan = C.build_plan(
        [row("p1", kind="decision", content="we ship"),
         row("p2", node_type="operator", kind=None, content="claim A implies claim B"),
         row("https://x/y", node_type="source", kind="article", content="https://x/y")],
        source_graph="old", target_graph="new", packs={})
    # the lane's URI is what `TortoiseSDK()` picks up by default
    sdk = TortoiseSDK()
    assert sdk._validate_bundle(plan.bundle(), promotion_policy="auto") == []


def test_a_write_flag_on_inventory_is_refused_not_ignored(monkeypatch, capsys, tmp_path):
    """Round-16: `inventory --apply --ledger --rows` exited 0 having silently ignored
    every one of them."""
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool", lambda *a, **k: {"result": []})
    assert mod.main(["inventory", "--apply"]) == 2
    assert "apply only to `plan`" in capsys.readouterr().err
    # EVERY write flag, not just the one that happened to be checked first
    for flag in (["--ledger", str(tmp_path / "x.jsonl")], ["--target", "org_x_g_new"]):
        assert mod.main(["inventory", *flag]) == 2
        assert "apply only to `plan`" in capsys.readouterr().err


def test_inventory_writes_the_row_artifact_it_advertises(monkeypatch, tmp_path):
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "we ship"}],
                    "total": 1}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    rows_out = tmp_path / "inv.jsonl"
    assert mod.main(["inventory", "--out", str(tmp_path / "inv.json"),
                     "--rows", str(rows_out), "--no-verify-read"]) == 0
    assert len(rows_out.read_text().splitlines()) == 1


def test_the_page_flag_is_documented_as_a_page_size():
    mod = _tool_module()
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
        mod.main(["--help"])
    assert "page SIZE" in buf.getvalue()


def test_a_row_set_whose_only_spelling_is_the_sentinel_has_no_identity():
    """Round-17: the sentinel fell through to a literal "default" identity, so a
    tenant row exposing no namespace still distinguished `default` from `team_x`."""
    rows = [{"graph_id": "default"}, {"namespace": "team_org1"}]
    assert C.canonical_graph_id("default", rows) is None
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("default", "team_org1", graph_rows=rows)


def test_an_illegal_page_size_is_a_usage_error():
    """Round-17: `--page-size 0` reached the surface and came back as
    "refusing to report an empty graph" with a traceback, not as a usage error."""
    mod = _tool_module()
    with pytest.raises(SystemExit) as exc:
        mod.main(["inventory", "--page-size", "0"])
    assert exc.value.code == 2


def test_one_graph_split_across_two_rows_with_different_fields_is_refused():
    """Round-18 BLOCKING: the tenant graph listed as `{graph_id: default, stable_id:
    g_<tenant>, name: default}` and as `{namespace: team_<org>}` gave the two refs
    DIFFERENT canonicals (each row can only use the fields it has), so
    `--graph g_<tenant> --target team_<org> --apply` wrote IN PLACE. Identity is
    unprovable when the matched rows expose different fields, so it must refuse."""
    rows = [{"graph_id": "default", "stable_id": "g_0bcdb207ef7336c3", "name": "default"},
            {"namespace": "team_3326a01ea34ae595d84de5d8f9"}]
    with pytest.raises(C.CurationRefused, match=r"cannot prove"):
        C.assert_distinct_graphs("g_0bcdb207ef7336c3", "team_3326a01ea34ae595d84de5d8f9",
                                 graph_rows=rows)


class _EqualSizeChurnSurface:
    """Retract one row and append another between pages: the total never moves and no
    row is served twice, so neither the total guard nor the repeat guard can see it."""

    def __init__(self):
        self.population = [{"id": f"p{i}", "kind": "decision", "content": f"c{i}"}
                           for i in range(6)]
        self.pages = 0

    def __call__(self, _name, args):
        self.pages += 1
        if self.pages == 2:            # p1 retracted, p6 appended — same size
            self.population.pop(1)
            self.population.append({"id": "p6", "kind": "decision", "content": "c6"})
        off = int(args.get("offset") or 0)
        lim = int(args.get("limit") or 2)
        return {"result": self.population[off:off + lim], "total": len(self.population)}


def test_a_same_size_churn_mid_walk_is_refused():
    """Round-19: a retraction plus an addition keeps the total stable and repeats no
    row, so the census held a tombstone and omitted a live row while looking complete."""
    mod = _tool_module()
    with pytest.raises(RuntimeError, match=r"changed during the walk"):
        mod.MCPReader(_EqualSizeChurnSurface(), None).read_points(page=2)


def test_the_iserror_fixture_sets_only_the_live_clients_spelling():
    """Round-19: the fixture set BOTH `is_error` and `isError`, so a regression to
    camelCase-only reads (the measured #4292 defect) left the suite green."""
    import inspect
    src = inspect.getsource(test_an_iserror_result_without_an_error_key_is_a_refusal)
    # no camelCase ATTRIBUTE ASSIGNMENT anywhere in the fixture (the docstring may say
    # the word: it is describing the property, not setting it)
    assert not [ln for ln in src.splitlines()
                if ln.strip().startswith(("self.isError", "isError"))]


def test_an_item_without_a_migration_key_is_accounted_not_crashed():
    """Round-20: the accepted-key report read the key back off the item AFTER the
    target accepted the write, so a source item with no `migrationKey` — exactly what
    `plan.bundle()` emits — raised KeyError after a REAL success and escaped unrecorded."""
    seen = {}
    def call_tool(_name, args):
        seen.update(args["bundle"])
        return {"ok": True, "created": 1}
    out = C.IngestWriter(call_tool).write_sections(
        "new", {"sources": [{"url": "https://x/y", "sourceKind": "article"}]})
    assert seen["sources"] == [{"url": "https://x/y", "sourceKind": "article"}]
    assert len(out["written"]) == 1 and out["written"][0]


def test_a_churn_midway_through_the_walk_is_caught_by_the_page_recheck():
    """Round-20 BLOCKING: a churn that lands after the first page is invisible to a
    snapshot of the last page TAKEN BEFORE it.

    Round-28: this test used to be named "…caught by the second pass" and matched three
    mechanisms at once, so it stayed green with the second walk disabled AND green with
    the page re-check disabled — it discriminated neither. It now pins exactly one, by
    its own message; the second full walk is pinned by
    `test_a_content_change_only_the_second_pass_can_see_is_refused`."""
    mod = _tool_module()
    pop = [{"id": f"r{i}", "kind": "decision", "content": f"c{i}"} for i in range(6)]
    state = {"pages": 0}

    def call(_name, args):
        state["pages"] += 1
        # after the walk has passed the first page, retract an ALREADY-READ row and
        # append a new one: the total never moves and nothing is served twice
        if state["pages"] == 4:
            pop.pop(2)
            pop.append({"id": "R9b", "kind": "decision", "content": "c9b"})
        off = int(args.get("offset") or 0)
        lim = int(args.get("limit") or 2)
        return {"result": pop[off:off + lim], "total": len(pop)}

    with pytest.raises(RuntimeError, match=r"last page changed during the walk"):
        mod.MCPReader(call, None).read_points(page=2)


def test_a_source_is_written_once_because_the_ledger_key_is_the_same_one(tmp_path):
    """Round-21 BLOCKING: the writer derived its own key for a source (the plan's key
    is stripped from the wire), and the two derivations disagreed — so a SECOND apply
    re-sent a source the ledger already held."""
    plan = C.build_plan([row("https://x/y", node_type="source", kind="article",
                             content="https://x/y")],
                        source_graph="old", target_graph="new", packs={})
    sent = []

    def call_tool(_name, args):
        sent.append(args["bundle"])
        return {"ok": True, "created": 1}
    led = C.Ledger(tmp_path / "l.jsonl", target="new")
    first = C.run_plan(plan, writer=C.IngestWriter(call_tool), ledger=led, apply=True,
                       graph_rows=ROWS)
    assert first.written == 1 and first.skipped_known == 0
    second = C.run_plan(plan, writer=C.IngestWriter(call_tool), ledger=led, apply=True,
                        graph_rows=ROWS)
    assert (second.written, second.skipped_known, second.pending) == (0, 1, 0), \
        (second.written, second.skipped_known, second.pending)
    assert len(sent) == 1, "the source must be sent exactly once across two applies"


def test_a_stable_walk_with_verification_on_succeeds():
    """Round-21 BLOCKING: the verification pass read `C.Row` objects with `.get`,
    so with the DEFAULT flags every walk raised AttributeError — and no test walked a
    stable surface with verification on."""
    mod = _tool_module()
    pop = [{"id": f"r{i}", "kind": "decision", "content": f"c{i}"} for i in range(6)]

    def call(_name, args):
        off = int(args.get("offset") or 0)
        lim = int(args.get("limit") or 2)
        return {"result": pop[off:off + lim], "total": len(pop)}
    rows, total = mod.MCPReader(call, None).read_points(page=2)   # verify=True default
    assert len(rows) == 6 and total == 6


def test_a_content_change_only_the_second_pass_can_see_is_refused():
    """A row UPDATED in place (same ids, same total, nothing re-served, first and last
    pages intact) is visible only to the full second pass."""
    mod = _tool_module()
    pop = [{"id": f"r{i}", "kind": "decision", "content": f"c{i}"} for i in range(6)]
    state = {"calls": 0}

    def call(_name, args):
        state["calls"] += 1
        if state["calls"] == 7:      # during the SECOND pass, rewrite a middle row
            pop[2]["content"] = "rewritten"
        off = int(args.get("offset") or 0)
        lim = int(args.get("limit") or 2)
        return {"result": pop[off:off + lim], "total": len(pop)}

    with pytest.raises(RuntimeError, match=r"DIFFERENT row sequence|second read"):
        mod.MCPReader(call, None).read_points(page=2)


# ── round-22 review findings ──────────────────────────────────────────────


def test_a_torn_ledger_tail_does_not_swallow_the_next_key(tmp_path):
    """Round-22: appending onto a torn line (no trailing newline) CONCATENATES the two
    records, destroying both keys — so an accepted row was silently forgotten."""
    path = tmp_path / "l.jsonl"
    path.write_text('{"target": "new", "migrationKey": "k-tor')   # torn write
    with pytest.raises(C.CurationRefused, match=r"damaged"):
        C.Ledger(path, target="new")                               # the tear is not silently skipped
    # repair by truncation, but leave the last RECORD without a trailing newline (the
    # torn-tail shape the append path must survive) — a file ending in "\n" would let a
    # regression in `record()` pass unnoticed
    path.write_text('{"target": "new", "migrationKey": "k1"}')
    led = C.Ledger(path, target="new")
    led.record(["k2"])
    reloaded = C.Ledger(path, target="new")
    assert "k1" in reloaded and "k2" in reloaded, "a torn tail must not swallow either key"


def test_a_disarmed_run_is_not_reported_as_a_failure(monkeypatch, tmp_path, capsys):
    """Round-22: `plan --apply --off` exited 3 ("REFUSED"), so a driver paged on a run
    that did exactly what it was told."""
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--apply", "--off", "--no-verify-read",
                   "--ledger", str(tmp_path / "l.jsonl"), "--out", str(tmp_path / "p.json")])
    assert rc == 0
    assert "disarmed" in capsys.readouterr().err


def test_a_disarmed_run_with_an_unresolvable_target_is_still_disarmed(tmp_path):
    """Round-23: the identity guard ran BEFORE the off switch, so a run the operator
    explicitly disarmed reported `REFUSED: cannot resolve the target graph` (exit 3) —
    the round-22 defect one arm over."""
    plan = C.build_plan([row("p1", kind="decision", content="c")],
                        source_graph="old", target_graph="g_unreachable")
    res = C.run_plan(plan, writer=CountingWriter(),
                     ledger=C.Ledger(tmp_path / "l.jsonl", target="g_unreachable"),
                     apply=True, off=True, graph_rows=ROWS)
    assert res.off is True and res.refused == "off-switch active"


def test_a_ledger_line_that_is_json_but_not_an_object_is_reported_as_damage(tmp_path):
    """Round-23: `null` / `[1,2]` raised TypeError out of `Ledger.__init__` and escaped
    `cmd_plan`'s CurationRefused handler as a traceback."""
    for bad in ("null", "[1, 2]", '"x"'):
        path = tmp_path / f"{bad[0]}.jsonl"
        path.write_text(bad + "\n")
        with pytest.raises(C.CurationRefused, match=r"damaged"):
            C.Ledger(path, target="new")


def test_a_keyless_point_row_reaches_the_wire_under_the_writers_own_key(tmp_path):
    """Round-25: a `points` item with no `migrationKey` was keyed with the SOURCE
    derivation, which hashes only (sourceKind, url) — both absent for a point, so it
    was a CONSTANT. Two keyless rows collapsed onto one key (the second silently never
    reached the writer) and the writer recorded a per-row key the planner could never
    re-derive, so a re-run re-sent it."""
    a = {"kind": "decision", "content": "first"}
    b = {"kind": "decision", "content": "second"}
    sections, skipped, dups = C._pending_sections({"points": [a, b]},
                                                  C.Ledger(tmp_path / "l.jsonl", target="new"))
    assert skipped == 0 and dups == 0
    assert C._fallback_key("points", a) != C._fallback_key("points", b)
    sent = [{k: v for k, v in row.items() if k != "migrationKey"} for row in sections["points"]]
    assert sent == [a, b], "a carried row was dropped as a duplicate"


# ── round-26 review findings ──────────────────────────────────────────────


def test_a_falsy_but_present_migration_key_uses_the_writers_derivation(tmp_path):
    """Round-26: `_pending_sections` tested `is None`, the writer `or`. With `""` the
    two sides derived DIFFERENT keys, so two rows collapsed onto one (the second never
    reached the wire) and every re-run re-sent them."""
    a = {"kind": "decision", "content": "first", "migrationKey": ""}
    b = {"kind": "decision", "content": "second", "migrationKey": ""}
    sections, skipped, dups = C._pending_sections({"points": [a, b], "sources": [
        {"url": "https://x.test/1", "sourceKind": "web", "migrationKey": 0}]},
        C.Ledger(tmp_path / "l.jsonl", target="new"))
    assert skipped == 0 and dups == 0
    keys = [p["migrationKey"] for p in sections["points"]]
    assert keys == [C._fallback_key("points", a), C._fallback_key("points", b)]
    assert sections["sources"][0]["migrationKey"] == C._fallback_key(
        "sources", {"url": "https://x.test/1", "sourceKind": "web"})


def test_the_ledger_records_what_the_target_accepted_not_what_was_attempted(tmp_path):
    """Round-26: a writer that reports a SUBSET without raising (legal under
    `write_sections`' contract) must not have every attempted row ledgered."""
    class SubsetWriter:
        def write_sections(self, target_graph, sections):
            return {"written": [sections["points"][0]["migrationKey"]]}

    plan = C.build_plan([row("p1", kind="decision", content="a"),
                         row("p2", kind="decision", content="b")],
                        source_graph="old", target_graph="new", packs={})
    led = C.Ledger(tmp_path / "l.jsonl", target="new")
    res = C.run_plan(plan, writer=SubsetWriter(), ledger=led, apply=True, graph_rows=ROWS)
    assert res.written == 1 and len(led) == 1


@pytest.mark.parametrize("value", ["0", "no", "off", "", "false"])
def test_only_a_truthy_vocabulary_disarms_the_run(ledger, monkeypatch, value):
    """Round-26: the docstring promises anything else leaves the run ARMED; only "1"
    was ever exercised, so widening the vocabulary would have passed."""
    monkeypatch.setenv(C.OFF_SWITCH_ENV, value)
    assert C.off_switch_active(False) is False


def test_a_ledger_path_that_cannot_be_read_is_a_refusal_not_a_traceback(tmp_path):
    """Round-26: `--ledger <a directory>` escaped as IsADirectoryError/OSError."""
    with pytest.raises(C.CurationRefused, match=r"cannot be read"):
        C.Ledger(tmp_path, target="new")          # a directory, not a file


def test_the_cli_exit_codes_pin_every_failure_arm(monkeypatch, tmp_path, capsys):
    """Round-26: returning 0 from any of the three failure arms left the suite green."""
    mod = _tool_module()
    state = {"graphs": [{"graph_id": "g_a", "stable_id": "g_a", "namespace": "org_x_g_a"}]}

    def call(name, args):
        if name == "tortoise_graph_overview" and args.get("section") == "graphs":
            return {"result": state["graphs"]}       # the identity rows the guard reads
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    # the refs must be spellings the ROWS name, or every arm below would be the
    # identity refusal and the other two arms would go untested
    base = ["plan", "--graph", ROWS[0]["namespace"], "--target", ROWS[1]["namespace"],
            "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
            "--out", str(tmp_path / "p.json")]

    # 1. the identity guard refuses: the rows are the AUTHORITY, and none of them
    #    names the refs, so an applying run is refused rather than resolved by shape
    state["graphs"] = [{"graph_id": "g_other", "stable_id": "g_other",
                        "namespace": "org_y_g_other"}]
    assert mod.main([*base, "--apply"]) == 3

    class StopWriter:
        def write_sections(self, target_graph, sections):
            return {"written": [p["migrationKey"] for p in sections["points"]][:1],
                    "stopped": "off-switch flipped mid-run"}

    class StopBeforeFirstBatchWriter:
        def write_sections(self, target_graph, sections):
            return {"written": [], "stopped": "off-switch engaged before any batch"}

    class NoOpWriter:
        """Reports success without accepting a key and without stopping.

        `IngestWriter` never does this, so only a custom PlanWriter can — but the
        honest-`applied` change makes this state report `applied: False`, and a
        fall-through to exit 0 would read as a finished migration.
        """

        def write_sections(self, target_graph, sections):
            return {"written": []}

    class RefuseWriter:
        def write_sections(self, target_graph, sections):
            exc = C.CurationRefused("ingest refused the batch")
            exc.written_keys = []
            raise exc

    # 2. a MID-RUN stop, 2b. a stop that landed NOTHING, 2c. a no-op success, and
    #    3. a MID-RUN refusal are all failures. 2b is the regression an honest
    #    `applied` introduces (the stop arm must be keyed on `stopped`); 2c is the
    #    state an honest `applied` makes reachable but no arm owned.
    for i, (writer, needle) in enumerate(((StopWriter(), "STOPPED"),
                                         (StopBeforeFirstBatchWriter(), "STOPPED"),
                                         (NoOpWriter(), "NO-OP"),
                                         (RefuseWriter(), "REFUSED"))):
        state["graphs"] = ROWS          # resolvable identity, so the run reaches the write
        monkeypatch.setattr(mod.C, "IngestWriter", lambda *a, _w=writer, **k: _w)
        # a FRESH ledger per arm: reusing one makes the second arm a no-op
        # (skipped_known=1, pending=0) and the writer is never reached
        args = [*base[:-4], "--ledger", str(tmp_path / f"l{i}.jsonl"),
                "--out", str(tmp_path / f"p{i}.json"), "--apply"]
        capsys.readouterr()   # drain the previous arm's stderr, so the check is per-arm
        assert mod.main(args) == 3, f"{needle} must be a failure exit"
        # WHICH arm fired, not just that some arm returned 3. Without this the stop arm
        # is unpinned: with the NO-OP arm present, re-keying it back to
        # `applied and stopped` still exits 3 (the NO-OP arm catches it) and only the
        # DIAGNOSTIC is wrong — a stop reported as "nothing accepted", which sends an
        # operator to the wrong cause. Measured: that mutant survived until this line.
        err = capsys.readouterr().err
        assert needle in err, f"{needle} must be the arm that fired, got: {err!r}"
        if needle == "REFUSED":
            # Round-33 follow-up 1: the refusal above accepted NO key, so the run must
            # not be announced as a mid-run refusal (that message means "some of it
            # landed") — a driver reading stderr would be told a partial migration
            # happened when nothing did. Pre-fix this arm printed "REFUSED mid-run".
            assert "REFUSED:" in err and "mid-run" not in err


def test_apply_without_graph_is_refused_before_any_read(monkeypatch, capsys):
    """Round-26: the guard was unpinned, and two comments claimed it was covered.
    Removing it turns a usage error (2) into a full read of the capped source."""
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool",
                        lambda *a, **k: pytest.fail("must not read (or write) a graph"))
    assert mod.main(["plan", "--target", "org_x_g_new", "--apply"]) == 2
    assert "--graph" in capsys.readouterr().err


def test_a_self_contradictory_row_set_cannot_prove_two_graphs_distinct():
    """Round-27: two rows that spell ONE graph two ways in the SAME field (both
    `graph_id: default`, `stable_id` g_a / g_b, names na / nb) let each ref match
    exactly one row, so no `hits > 1` refusal fires and the shapes are identical —
    the guard passed and the write went to the tenant graph in place."""
    rows = [
        {"graph_id": "default", "stable_id": "g_a", "name": "na", "namespace": "team_o1"},
        {"graph_id": "default", "stable_id": "g_b", "name": "nb", "namespace": "team_o1"},
    ]
    with pytest.raises(C.CurationRefused, match=r"cannot prove"):
        C.assert_distinct_graphs("na", "nb", graph_rows=rows)
    # …and a row set that does NOT share an identity value is still allowed
    ok = [
        {"graph_id": "g_a", "stable_id": "g_a", "name": "na", "namespace": "org_x_g_a"},
        {"graph_id": "g_b", "stable_id": "g_b", "name": "nb", "namespace": "org_x_g_b"},
    ]
    C.assert_distinct_graphs("na", "nb", graph_rows=ok)


def test_the_wire_carries_the_sources_section_not_only_points(tmp_path):
    """Round-27: the round-5 test could not fail for its stated reason — its writer
    echoed every key back and ignored `sections`. This one drives the REAL
    `IngestWriter` and inspects the bundles that reached the wire."""
    plan = C.build_plan([row("p1", kind="decision", content="a decision"),
                         row("s1", node_type="source", kind="article", content="https://x/y")],
                        source_graph="old", target_graph="new", packs={})
    sent: list[dict] = []

    def call(name, args):
        sent.append(dict(args.get("bundle") or {}))
        return {"ok": True, "created": 1}
    res = C.run_plan(plan, writer=C.IngestWriter(call),
                     ledger=C.Ledger(tmp_path / "l.jsonl", target="new"),
                     apply=True, graph_rows=ROWS)
    assert res.written == 2
    sections = [k for bundle in sent for k in bundle]
    assert "points" in sections and "sources" in sections, sections


def test_the_suite_pins_the_ambient_off_switch_fixture():
    """Round-27: the round-25 test was vacuous — the autouse fixture had already
    cleared the variable, so it could not fail for the reason it named. This one is
    red if the fixture is removed while `TORTOISE_CURATE_OFF` is exported."""
    import os
    assert C.OFF_SWITCH_ENV not in os.environ, (
        "an inherited TORTOISE_CURATE_OFF reached a test: the autouse "
        "_curation_off_switch_unset fixture is what keeps the suite hermetic")


# ── the wire the CLI is actually written against (round-28) ──────────────
#
# Every surface this pass talks to is INJECTED, so each existing test answers with
# its own fake — and a fake matches whatever name it is given. That made the suite
# green over a wire nothing had checked: renaming a tool (or asking for a section
# the server does not advertise) would red nothing here and die in production at
# the first read of a run that is the only copy of the data. These two pins read
# the call sites out of the SOURCE and require the names to exist on the real
# surface, so the next rename is caught here instead of at the endpoint.

REPO = _pathlib.Path(__file__).resolve().parent.parent
CLI_SRC = REPO / "tools" / "curate_graph.py"
CURATION_SRC = REPO / "tortoise" / "curation.py"
MCP_SERVER_SRC = REPO / "tortoise" / "mcp_server.py"


def _first_arg_strings(func_names, *paths):
    """String literals passed as the FIRST argument to any of `func_names(...)`."""
    found = set()
    for path in paths:
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call) or not node.args:
                continue
            func = node.func
            name = (func.attr if isinstance(func, _ast.Attribute) else getattr(func, "id", None))
            if name in func_names:
                arg = node.args[0]
                if isinstance(arg, _ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
    return found


def _declared_overview_sections():
    """`mcp_server._OVERVIEW_SECTIONS`, read from source — importing that module
    needs an ambient-free env (`TORTOISE_API_KEY` exported makes it raise), which
    is exactly the ambient-state trap this suite already pins against."""
    tree = _ast.parse(MCP_SERVER_SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, _ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], _ast.Name)
                and node.targets[0].id == "_OVERVIEW_SECTIONS"):
            return tuple(_ast.literal_eval(node.value))
    raise AssertionError("mcp_server defines no _OVERVIEW_SECTIONS")


def test_every_tool_the_curation_code_names_is_on_the_registry():
    """A tool name in this pass is a production dependency, not a label."""
    from tortoise.tool_registry import TOOL_REGISTRY

    used = _first_arg_strings({"call_tool"}, CLI_SRC, CURATION_SRC)
    used |= _first_arg_strings({"_call"}, CLI_SRC)
    # `IngestWriter.TOOL` is a class attribute, not a call argument.
    used.add(C.IngestWriter.TOOL)
    # NON-VACUITY: the extraction must have found the real call sites, or this
    # test would pass over an empty set the moment a helper is renamed.
    assert {"tortoise_query", "tortoise_graph_overview", "tortoise_ingest"} <= used, used
    served = {t.name for t in TOOL_REGISTRY}
    assert used <= served, f"not on the served surface: {sorted(used - served)}"


def test_every_overview_section_the_reader_asks_for_is_advertised():
    """`graph_overview` answers an unknown section with an error ENVELOPE, which
    the reader fails closed on — so a bad section name is a dead run, not a
    silently empty census. Pin the names against the server's own tuple."""
    used = _first_arg_strings({"_section"}, CLI_SRC)
    # NON-VACUITY: all five sections the two verbs read must be present.
    assert {"status", "structure", "sources", "graphs", "audit"} <= used, used
    declared = _declared_overview_sections()
    assert {"audit", "status", "structure", "sources", "graphs"} <= set(declared), declared
    assert used <= set(declared), f"not an overview section: {sorted(used - set(declared))}"

# ── round-29 review findings ──────────────────────────────────────────────


def test_a_row_set_that_puts_a_bare_name_in_namespace_cannot_prove_distinctness():
    """Round-29: `MCPReader.graphs()` wraps every string the surface returns as
    `{"namespace": s}`, so a NAME arrives in the namespace field and a pair that is ONE
    graph becomes two shape-identical rows with disjoint values — nothing shared, shapes
    equal, both guard clauses silent, and `--apply` wrote to the source in place."""
    rows = [{"namespace": "org_o1_g_old"}, {"namespace": "live-v4"}]
    with pytest.raises(C.CurationRefused, match=r"not a namespace"):
        C.assert_distinct_graphs("live-v4", "org_o1_g_old", graph_rows=rows)
    # the real surface's shape (namespaces only) is unaffected
    ok = [{"namespace": "org_o1_g_old"}, {"namespace": "org_o1_g_new"}]
    C.assert_distinct_graphs("org_o1_g_old", "org_o1_g_new", graph_rows=ok)


def test_a_non_string_migration_key_is_stringified_on_both_sides(tmp_path):
    """Round-29: the planner kept the raw `5` while the writer recorded `"5"`, so the
    row stayed pending forever and every re-run re-sent it."""
    sections, _skipped, _dups = C._pending_sections({"points": [
        {"kind": "decision", "content": "a", "migrationKey": 5}]},
        C.Ledger(tmp_path / "l.jsonl", target="new"))
    key = sections["points"][0]["migrationKey"]
    assert key == "5" and isinstance(key, str)
    led = C.Ledger(tmp_path / "l.jsonl", target="new")
    led.record([key])
    again, skipped2, _ = C._pending_sections({"points": [
        {"kind": "decision", "content": "a", "migrationKey": 5}]},
        C.Ledger(tmp_path / "l.jsonl", target="new"))
    assert again["points"] == [] and skipped2 == 1, "a re-run must recognise the key"


def test_the_graph_name_resolves_to_the_same_canonical_as_its_namespace():
    """Round-29: the cited test could not fail for NAME resolution — with name matching
    deleted the pair is simply unresolvable and the guard refuses anyway, so it stayed
    green. This one asserts the resolution itself."""
    rows = [{"graph_id": "g_2b7e4c9d1a6f8035", "name": "live-v4",
             "namespace": "org_3326_g_2b7e4c9d1a6f8035"}]
    by_name = C.canonical_graph_id("live-v4", rows)
    by_ns = C.canonical_graph_id("org_3326_g_2b7e4c9d1a6f8035", rows)
    assert by_name is not None and by_name == by_ns
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("live-v4", "org_3326_g_2b7e4c9d1a6f8035", graph_rows=rows)


# ── round-30 review findings ──────────────────────────────────────────────


def test_the_tenants_two_rows_cannot_hide_one_graph_behind_the_name_field():
    """Round-30 BLOCKING: `_matched_row` matched `name` first-row-wins, so it returned
    the SAME row for both refs and `ra is not rb` short-circuited the only clause that
    could see the contradiction — `--graph g_t… --target default --apply` exited 0 and
    wrote in place on the tenant graph."""
    rows = [
        {"stable_id": "g_t0000000000000", "name": "default"},   # the tenant graph
        {"graph_id": "default", "namespace": "team_o1"},        # the SAME graph
        {"namespace": "org_o1_g_deadbeefdeadbeef"},             # a real distinct graph
    ]
    with pytest.raises(C.CurationRefused, match=r"cannot prove|same graph"):
        C.assert_distinct_graphs("g_t0000000000000", "default", graph_rows=rows)
    # …and the resolution the clause now keys on is the one the canonical came from
    assert C._matched_row("g_t0000000000000", rows) is rows[0]
    assert C._matched_row("default", rows) is rows[1]


def test_a_totals_move_on_a_SHRINKING_population_is_refused_without_the_verification_pass():
    """Round-30: the round-14 fix (parse `total` even on an EMPTY final page) had no
    discriminator — on the fixture named for it the short-read guard fires too, so
    reverting the ordering left the suite green. Here the walk sees MORE rows than the
    reported total (short-read cannot fire), so only the totals guard can refuse."""
    mod = _tool_module()

    def call(_name, args):
        off = int(args.get("offset") or 0)
        if off == 0:
            return {"result": [{"id": f"p{i}", "kind": "decision", "content": str(i)}
                               for i in range(4)], "total": 10}
        # the population shrank to 2 BEFORE this page was read, so the page is EMPTY and
        # its total is 2. The message is what discriminates: with the totals parse gated
        # on a non-empty batch (the pre-round-14 ordering) this same fixture is refused
        # by the SHORT-READ guard instead, so asserting the CAUSE is the only way the
        # test can tell the two mechanisms apart.
        return {"result": [], "total": 2}
    with pytest.raises(RuntimeError, match=r"reported total changed"):
        mod.MCPReader(call, None).read_points(page=4, verify=False)
    with pytest.raises(RuntimeError, match=r"reported total changed"):
        mod.MCPReader(call, None).read_points(page=4)      # verification pass on


def test_an_undecodable_ledger_is_a_refusal_not_a_traceback(tmp_path):
    """Round-30: `UnicodeDecodeError` comes from ITERATING the file, so it escaped the
    per-line handler and `main()` died with a traceback instead of the documented
    refusal."""
    path = tmp_path / "l.jsonl"
    path.write_bytes(b'{"target": "new", "migrationKey": "\xff\xfe"}\n')
    with pytest.raises(C.CurationRefused, match=r"cannot be read"):
        C.Ledger(path, target="new")


def test_a_case_varied_ref_cannot_be_one_rows_id_and_anothers_name():
    """Round-31 BLOCKING: identity fields were casefolded, while `mcp_auth` matches ids
    with `==` and casefolds ONLY the name — so `--graph Default` casefolded onto the
    tenant row's `graph_id: default` in the guard, while the write path read it as the
    NAME of the `g_deadbeef…` row. The guard's two canonicals differed for ONE graph and
    the run wrote in place."""
    rows = [
        {"graph_id": "default", "stable_id": "g_t0000000000000",
         "namespace": "team_o1", "name": "house"},                       # the tenant graph
        {"graph_id": "g_deadbeefdeadbeef", "stable_id": "g_deadbeefdeadbeef",
         "namespace": "org_o1_g_deadbeefdeadbeef", "name": "default"},   # a graph NAMED "default"
    ]
    # the identity match is exact: `Default` is NOT the tenant row's graph id
    assert C._matched_row("Default", rows) is rows[1]
    assert C.canonical_graph_id("Default", rows) == C.canonical_graph_id(
        "org_o1_g_deadbeefdeadbeef", rows)
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("Default", "org_o1_g_deadbeefdeadbeef", graph_rows=rows)
    # …and a NAME is still case-insensitive, as the surface documents
    assert C.canonical_graph_id("HOUSE", rows) == C.canonical_graph_id("g_t0000000000000", rows)


# ── round-34 review findings ──────────────────────────────────────────────


class _RefusalOnALaterPage:
    """A walk whose SECOND page is a refusal envelope, on a surface with no `total`.

    Neither existing guard can see this: the shortfall check has no total to compare
    against, and both the page re-check and the verification pass re-read the last
    page that SUCCEEDED (a refusal returns no rows, so the loop breaks before the
    fingerprint is updated), so they compare like with like and agree.
    """

    def __init__(self):
        self.calls = 0

    def __call__(self, _name, args):
        self.calls += 1
        if int(args.get("offset") or 0) == 0:
            return {"result": [{"id": "p1", "kind": "decision", "content": "one"},
                               {"id": "p2", "kind": "decision", "content": "two"}]}
        return {"error": "429 Too Many Requests — quota exceeded", "code": 429}


def test_a_refusal_on_a_later_page_is_never_a_complete_census():
    """Round-34 finding 1: `refusal` was captured for the remedy message and then only
    consulted in the `not rows` arm, so a mid-walk refusal left a SHORT walk behind
    that the caller returned as a complete census (`total` fell back to `len(rows)`)."""
    mod = _tool_module()
    with pytest.raises(RuntimeError, match=r"429|refused"):
        mod.MCPReader(_RefusalOnALaterPage(), None).read_points(page=2, verify=False)
    # …and the DEFAULT (verification-pass) path is not a second chance: the second walk
    # sees the same refusal and agrees with the first.
    with pytest.raises(RuntimeError, match=r"429|refused"):
        mod.MCPReader(_RefusalOnALaterPage(), None).read_points(page=2)


def test_a_stale_hash_never_claims_a_row_of_record_for_another_text():
    """Round-34 finding 2: rows are folded by content hash alone, so two rows sharing a
    hash whose CONTENT differs made the second text an "exact content duplicate" of the
    first, with the reason asserting the keeper was "the row of record for this
    content" — a false claim over a unique text the carry set then left behind.

    The hash agreeing while the text disagrees is exactly the stale-hash shape this
    file already builds (`test_duplicate_follows_its_keeper`), one content over.
    """
    plan = C.build_plan(
        [row("narr", content="Let me check the CI config first.", content_hash="H"),
         row("unique", content="The venue refuses writes at its node cap.", content_hash="H")],
        source_graph="old", target_graph="new", packs={},
    )
    by_id = {d.node_id: d for d in plan.decisions}
    assert by_id["unique"].disposition == C.UNDECIDABLE, (
        "a hash that disagrees with the text proves no duplicate, so the row is a "
        f"per-row decision: {by_id['unique']!r}")
    assert "is the row of record" not in by_id["unique"].reason, (
        f"the keeper must not be presented as this text's row of record: "
        f"{by_id['unique'].reason!r}")
    # Non-vacuity: the honest pair (SAME text, same hash) is still folded, so this is a
    # stale-hash guard and not a blanket refusal to detect duplicates.
    same = C.build_plan(
        [row("k1", content="A durable claim", content_hash="H"),
         row("k2", content="A durable claim", content_hash="H")],
        source_graph="old", target_graph="new", packs={},
    )
    folded = {d.node_id: d for d in same.decisions}
    assert folded["k2"].disposition != C.CARRY and "duplicate" in folded["k2"].reason


def test_two_rows_sharing_an_id_and_a_hash_cannot_hide_the_second_text(tmp_path):
    """Round-35 finding 1: the keeper map was keyed by `row.node_id`, and point ids are
    NOT unique — the reader admits same-id nodes with different content as a legal shape
    (`test_two_nodes_sharing_an_id_in_one_page_are_not_a_pagination_shift`). So a row that
    merely shared the keeper's id matched `keepers[key] == row.node_id`, was appended the
    KEEPER's decision a second time, and its own text reached the artifact NOWHERE — no
    decision, no reason, no `--rows` line. `_pending_sections` then counted the pair as a
    plan-level `duplicate_rows` repeat, so the report said "this plan repeats a row" about
    a unique text that had simply been left behind.
    """
    plan = C.build_plan(
        [row("dup", content="text one", content_hash="H"),
         row("dup", content="text two", content_hash="H")],
        source_graph="old", target_graph="new", packs={},
    )
    assert len(plan.decisions) == 2, [d.node_id for d in plan.decisions]
    first, second = plan.decisions
    assert first.content == "text one"
    assert second.content == "text two", (
        "the second row must keep its OWN content, not be replaced by the keeper's: "
        f"{second.content!r}")
    assert second.disposition == C.UNDECIDABLE, (
        "a hash that disagrees with the text proves no duplicate, so the second text is "
        f"a per-row decision: {second!r}")
    # The pair is NOT a plan-level repeat: only a row whose content the plan duplicates
    # may be reported that way, and these two texts differ.
    _sections, _skipped, duplicates = C._pending_sections(
        plan.bundle(), C.Ledger(tmp_path / "l.jsonl", target="new"))
    assert duplicates == 0, (
        f"distinct texts are not a duplicate repeat (duplicate_rows={duplicates})")

    # Non-vacuity: the same id with the SAME text is a genuine duplicate and must still
    # fold, so this is an identity guard and not a blanket refusal to detect duplicates.
    same = C.build_plan(
        [row("dup", content="A durable claim", content_hash="H"),
         row("dup", content="A durable claim", content_hash="H")],
        source_graph="old", target_graph="new", packs={},
    )
    assert len(same.decisions) == 2, [d.content for d in same.decisions]
    assert same.decisions[1].disposition != C.CARRY and "duplicate" in same.decisions[1].reason, (
        f"a real duplicate must still be detected: {same.decisions[1]!r}")
