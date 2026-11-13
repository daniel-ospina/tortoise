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


def test_the_promotion_loop_identifies_the_keeper_by_position_not_by_node_id():
    """Round-36 review finding — the PROMOTION pass had the round-35 bug the third pass lost.

    Point ids are not unique (`test_two_nodes_sharing_an_id_in_one_page_are_not_a_pagination
    _shift` pins that as a legal read shape), so a `node_id`-keyed promotion skipped a row
    that HOLDS content merely because it shared the empty keeper's id: the empty row stayed
    the keeper and the content row's decision read "the keeper dup is the row of record for
    this content" over a row holding no content, while the only copy was `not-carried` and
    `carry_rows()` came back EMPTY. Deleting this identity test (restoring the id comparison)
    leaves the rest of the suite green, so this is the pin that witnesses it.

    Asserted by POSITION, never through a `{d.node_id: d}` map: the two rows share an id, so
    a map silently keeps only one of them and cannot see the swap at all.
    """
    plan = C.build_plan(
        [row("dup", kind="decision", content="", content_hash="H"),
         row("dup", kind="decision", content="the venue refuses writes at its node cap",
             content_hash="H")],
        source_graph="old", target_graph="new", packs={},
    )
    assert len(plan.decisions) == 2
    empty, holder = plan.decisions
    # The row that HOLDS the content is the row of record, whatever its id is.
    assert holder.disposition == C.CARRY, (
        f"the row holding the only copy of the text must carry: {holder.reason!r}")
    assert "duplicate" not in holder.reason, (
        f"the content-holding row must not be labelled a duplicate: {holder.reason!r}")
    # …and the empty row may never be presented as the row of record for that content.
    assert empty.disposition != C.CARRY
    assert "row of record" not in empty.reason, (
        f"an empty row cannot be the row of record for content it does not hold: "
        f"{empty.reason!r}")
    # The only copy of the text reaches the wire — the whole point of the promotion pass.
    assert [d.node_id for d in plan.carry_rows()] == ["dup"]


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


@pytest.mark.parametrize("key, payload", [
    # One key PER CASE: an envelope carrying two of them cannot tell which one the guard
    # consulted, which is how dropping "violations" (or "detail") from `_ingest_refused`
    # left this suite green while a violations-only refusal would have been ledgered as
    # migrated (round-36 review finding).
    ("error", "ERR_QUOTA"),
    ("violations", [{"section": "points", "reason": "missing content"}]),
    ("detail", "the target rejected the bundle"),
])
def test_each_refusal_envelope_key_refuses_on_its_own(key, payload):
    """Every key `_ingest_refused` consults must refuse BY ITSELF, naming the payload."""
    def call_tool(tool, args):
        return {key: payload}

    writer = C.IngestWriter(call_tool)
    with pytest.raises(C.CurationRefused) as excinfo:
        writer.write_sections("new", {"points": [{"migrationKey": "k", "content": "c"}]})
    assert str(payload) in str(excinfo.value), (
        f"the refusal must carry the {key!r} payload it acted on: {excinfo.value!r}")


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


def test_the_reasoning_layer_is_not_carried_without_its_endpoints():
    """Was `test_operator_is_carried_as_reasoning` — which asserted CARRY on a rule
    that could never run, because the harness declared the whole layer unlistable.

    The layer IS readable: `tortoise_search(entity_type="operator", limit=5000)`
    answers 3,727 rows in one call on live-v4, an exact match with
    `structure.operators` (measured 2026-11-13, read-only). What that read serves is
    the operator's LABEL as content and NO endpoints — so a carried operator arrives
    in the clean graph as a floating node, which is the one shape this issue forbids
    ("Every retained fact is connected ... not a floating node").
    """
    d = C.classify(row(node_type="operator", kind="IMPL", content="claim A implies claim B"), {})
    assert d.disposition == C.UNDECIDABLE, d
    assert "reasoning" in d.reason and "endpoint" in d.reason, d.reason


def test_an_operator_with_an_empty_label_is_undecidable_not_not_carried():
    """Was `test_an_empty_operator_row_is_not_carried`.

    An operator's ``content`` is its LABEL. An empty label does not mean the row has
    "nothing to carry" — the relationship is still a relationship, and its endpoints
    are what would be carried — so `not-carried` would report a decision nobody took
    (it reads as "deliberately left behind"). The reason names the real blocker.
    """
    d = C.classify(row(node_type="operator", kind="IMPL", content="  "), {})
    assert d.disposition == C.UNDECIDABLE, d
    assert "endpoint" in d.reason, d.reason


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
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
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
        if args.get("section") == "status":
            # The real status section REPORTS counts; a fixture answering it with an
            # empty `result` models a surface that says nothing about the source
            # population — the state the unreconciled-zero refusal exists for.
            return {"counts": {"Source": 0}}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    rows_out = tmp_path / "rows.jsonl"
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--out", str(tmp_path / "plan.json"), "--rows", str(rows_out),
                   "--ledger", str(tmp_path / "l.jsonl"),   # never ~/.tortoise/…
                   "--no-verify-read"])
    assert rc == 0
    lines = [json.loads(x) for x in rows_out.read_text().splitlines() if x.strip()]
    assert {d["node_id"] for d in lines} == {"p1", "p2"}
    assert all(d["reason"] and d["disposition"] for d in lines)
    # The TEXT the reason is about travels with it, so the report alone re-derives
    # the decision (round-36 review, P3-4: `as_dict` carried neither content nor props).
    assert {d["content"] for d in lines} == {"we ship", "narration"}


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
    for flag in (["--ledger", str(tmp_path / "x.jsonl")], ["--target", "org_x_g_new"],
                 ["--off"]):
        assert mod.main(["inventory", *flag]) == 2
        assert "apply only to `plan`" in capsys.readouterr().err


def test_inventory_writes_the_row_artifact_it_advertises(monkeypatch, tmp_path):
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "we ship"}],
                    "total": 1}
        if args.get("section") == "status":
            return {"counts": {"Source": 0}}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
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
        if args.get("section") == "status":
            return {"counts": {"Source": 0}}
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
    """Round-26: returning 0 from ANY failure arm left the suite green.

    The loop below now covers five arms (two stops, the no-op, and the fully- and
    partially-refused writes), and each one also asserts its OWN stderr needle — the
    exit code alone cannot tell the arms apart once a later arm can also return 3.
    """
    mod = _tool_module()
    state = {"graphs": [{"graph_id": "g_a", "stable_id": "g_a", "namespace": "org_x_g_a"}]}

    def call(name, args):
        if name == "tortoise_graph_overview" and args.get("section") == "graphs":
            return {"result": state["graphs"]}       # the identity rows the guard reads
        if name == "tortoise_graph_overview" and args.get("section") == "status":
            return {"counts": {"Source": 0}}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
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

    class PartialRefuseWriter:
        """Accepts the FIRST batch, then refuses a later one.

        This is the arm `IngestWriter` really reaches on a two-batch run whose second
        call is refused (it raises with the accumulated `written`), and the documented
        carry set is 127 rows against a default `batch_size=100`, so points DO span two
        batches on a first real apply. It is also the worst row in the table: a
        HALF-migrated target. Deleting the `applied and refused` arm left the suite
        green and flipped this run's rc 3 -> 0 (measured), so it needs its own witness.
        """

        def write_sections(self, target_graph, sections):
            exc = C.CurationRefused("target refused the batch: 429")
            exc.written_keys = [p["migrationKey"] for p in sections["points"]][:1]
            raise exc

    # 2. a MID-RUN stop, 2b. a stop that landed NOTHING, 2c. a no-op success,
    #    3. a fully-refused write, and 3b. a PARTIAL refusal (some of it landed) are
    #    all failures. 2b is the regression an honest `applied` introduces (the stop
    #    arm must be keyed on `stopped`); 2c is the state an honest `applied` makes
    #    reachable but no arm owned; 3b is the arm whose absence changes the EXIT CODE.
    for i, (writer, needle) in enumerate(((StopWriter(), "STOPPED"),
                                         (StopBeforeFirstBatchWriter(), "STOPPED"),
                                         (NoOpWriter(), "NO-OP"),
                                         (RefuseWriter(), "REFUSED"),
                                         (PartialRefuseWriter(), "REFUSED mid-run"))):
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


def test_a_matching_row_with_no_establishable_canonical_is_a_refusal_not_a_skip():
    """A ref that matches a row whose canonical cannot be established used to be
    SKIPPED (`if canon and canon not in hits`), so a *second* row resolved the same
    ref through its own spelling and the guard's canonical for that ref could diverge
    from the row `mcp_auth` resolves it to. Measured on this exact row set: the guard
    passed with canonicals `b` != `a` while the write path resolved BOTH refs to the
    MIDDLE row — an in-place write behind a guard that said the graphs were distinct.

    Reverified here end to end rather than asserted: the middle row matches
    `stable_id == "default"` and canonicalises through a namespace with an EMPTY tail
    (`org_o1_g_`), which is not a spelling of anything."""
    rows = [
        {"graph_id": "b", "name": "default"},
        {"stable_id": "default", "namespace": "org_o1_g_", "name": "a"},
        {"stable_id": "g_x_g_a", "namespace": "org_o1_g_"},
    ]
    # Precondition the defect rests on: the middle row MATCHES the ref…
    assert any((r.get("stable_id") or "").strip() == "default" for r in rows)
    # …and cannot be identified, so it must refuse instead of being skipped.
    assert C._canonical_from_row(rows[1]) is None
    assert C.canonical_graph_id("default", rows) is None
    with pytest.raises(C.CurationRefused, match=r"cannot resolve|cannot prove"):
        C.assert_distinct_graphs("default", "org_o1_g_", graph_rows=rows)


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

    In SHIPPED code BOTH entry points raise at the SAME `page_refusal` gate inside
    `read_points`, before any verification walk starts — so this test witnesses that one
    gate on the `verify=False` and `verify=True` paths, not two independent guards.

    `_fingerprint_walk` is a SECOND, independent witness that only becomes REACHABLE once
    the gate is gone: with the gate disabled, `verify=False` returns the short walk as a
    complete census (the silent partial census R7 forbids) while `verify=True` still
    raises from the verification pass — and `--no-verify-read` removes even that fallback.
    Both halves measured. Two earlier revisions of this docstring were wrong in opposite
    directions: the first said the verification pass re-read the last SUCCEEDED page and
    "agreed" (it does not — it walks from offset 0), and the second called the two paths
    "distinct witnesses" in shipped code (they are one gate; the fallback is only
    reachable without it).
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
    # ⛔ `match` names the GATE's message, not just "a refusal". `r"429|refused"` also
    # matches `_fingerprint_walk`'s `verification pass was refused: …`, so it could not
    # tell the two guards apart: deferring the gate behind the verification pass
    # (`if page_refusal and not verify:`) left this test GREEN while the default path was
    # protected by something else (measured; the class docstring above claims this test
    # witnesses the shared gate, and this is what makes that claim true).
    with pytest.raises(RuntimeError, match=r"read refused at offset 2"):
        mod.MCPReader(_RefusalOnALaterPage(), None).read_points(page=2, verify=False)
    # …and the DEFAULT (verification-pass) path reaches the SAME page gate and raises
    # there — it is not a second chance that "agrees with the first": both arms fail at
    # the gate, before any verification walk is started.
    with pytest.raises(RuntimeError, match=r"read refused at offset 2"):
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
    # NON-VACUITY, and it has to be the SPECIFIC claim (the sibling test's form, for the
    # same measured reason): a blanket "withhold every duplicate" mutant also satisfies
    # `!= CARRY and "duplicate" in reason`, because the content-mismatch reason contains
    # the word "duplicate" too. Pinning the exact phrase makes that mutant red THIS row.
    assert folded["k2"].disposition != C.CARRY
    assert "exact content duplicate" in folded["k2"].reason, (
        f"the honest same-text pair must still fold as a duplicate, not be withheld: "
        f"{folded['k2'].reason!r}")


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
        # `kind="decision"` is LOAD-BEARING, not decoration: with the default kind both
        # rows classify UNDECIDABLE, the bundle carries nothing, and the `duplicates == 0`
        # assertion below then holds under EVERY mutant — a dead assertion (round-36
        # review, mutation rule 3: measured 0 both pre- and post-fix). A carried pair is
        # what makes `duplicate_rows` able to move: measured 1 on the pre-fix code.
        [row("dup", kind="decision", content="text one", content_hash="H"),
         row("dup", kind="decision", content="text two", content_hash="H")],
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
    # ⛔ These rows pass `kind="decision"` on purpose: the default kind (`"statement"`)
    # classifies UNDECIDABLE, so `duplicate_of` is UNDECIDABLE and the fold takes the
    # undecidable branch instead. `kind="decision"` is what reaches the CARRIED branch
    # (`duplicate_of == CARRY` → not-carried), and BOTH pins below are load-bearing in a
    # different way: the DISPOSITION pin kills a fold-to-the-wrong-branch mutant
    # (measured: with only `!= C.CARRY` here, folding the CARRIED arm to UNDECIDABLE
    # leaves this arm green), and the PHRASE pin kills a blanket-withhold mutant
    # (measured: `duplicate_of is not None` → NOT_CARRIED for every duplicate leaves this
    # arm green without it).
    same = C.build_plan(
        [row("dup", kind="decision", content="A durable claim", content_hash="H"),
         row("dup", kind="decision", content="A durable claim", content_hash="H")],
        source_graph="old", target_graph="new", packs={},
    )
    assert len(same.decisions) == 2, [d.content for d in same.decisions]
    assert same.decisions[1].disposition == C.NOT_CARRIED, (
        "a duplicate of a CARRIED row folds to not-carried — the CARRIED and UNDECIDABLE "
        f"arms are separate `if`s and this pin is what tells them apart: {same.decisions[1]!r}")
    assert "exact content duplicate" in same.decisions[1].reason, (
        f"a real duplicate must still be detected: {same.decisions[1]!r}")


# ── round-36 (merge-gate review) findings ─────────────────────────────────
#
# Five P3s from the review at 919de3c428: no wrong outcome in shipped code, but each
# was a property the PR advertised that nothing asserted. Fixed rather than
# re-recorded, and each is pinned here against the mutant it now kills.


def test_the_shipped_off_switch_watch_disarms_a_live_run(monkeypatch):
    """P3-1 — R4's mid-run leg through the SHIPPED `armed` default.

    Every other mid-run disarm in this suite injects its own `armed=` callable, and
    `run_plan` consults the switch BEFORE the writer is built — so replacing
    `IngestWriter`'s default with a constant (or resolving it once in `__init__`)
    left all 143 tests green while `TORTOISE_CURATE_OFF` stopped disarming a live
    write. This arm passes NO `armed`, flips the env from inside the first target
    call, and requires the second batch to be refused.
    """
    monkeypatch.delenv(C.OFF_SWITCH_ENV, raising=False)
    calls: list[dict] = []

    def call_tool(tool, args):
        calls.append(args)
        if len(calls) == 1:
            monkeypatch.setenv(C.OFF_SWITCH_ENV, "1")   # the operator flips it mid-run
        return {"created": len(args["bundle"]["points"])}

    points = [{"migrationKey": f"k{i}", "content": "c"} for i in range(3)]
    writer = C.IngestWriter(call_tool, batch_size=1)          # ⛔ NO `armed=`
    out = writer.write_sections("new", {"points": points})
    assert len(calls) == 1, f"the second batch reached the wire after the switch: {calls}"
    assert out["written"] == ["k0"], out
    assert out["stopped"] == "off-switch engaged", out


def test_an_unreadable_source_listing_is_a_refusal_not_an_empty_census():
    """P3-2 — R7's other half: the source section.

    `_section` fails closed on an error ENVELOPE, but a refusal arriving as an
    unrecognised DATA shape carries no `error` key and unwrapped to `[]`. On this
    tenant that silently reported a graph with no sources — 1,578 of 17,420 rows
    gone from the census with nothing failing.
    """
    mod = _tool_module()
    reader = mod.MCPReader(lambda name, args: {"unexpected": "shape"}, None)
    with pytest.raises(RuntimeError, match="unrecognised shape"):
        reader.read_sources()


def test_a_zero_source_census_with_nothing_to_reconcile_it_is_refused(monkeypatch, capsys,
                                                                     tmp_path):
    """P3-2 — the caller half: 0 sources AND no `counts.Source` is unmeasurable.

    An empty listing is only distinguishable from an unread one by the status
    section's own count. With neither, `source_count_gap` was null and the run
    exited 0 over a census that had dropped the whole source population.
    """
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        if args.get("section") == "sources":
            return {"result": []}                     # an empty, perfectly readable listing
        if args.get("section") == "status":
            return {"unexpected": "shape"}            # ...with nothing to reconcile it
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["inventory", "--out", str(tmp_path / "inv.json"), "--no-verify-read"])
    assert rc == 3, capsys.readouterr()
    assert "REFUSED" in capsys.readouterr().err


def test_a_genuinely_empty_source_population_still_passes(monkeypatch, tmp_path):
    """P3-2's counter-arm — the refusal must not block a graph that really has none.

    `counts.Source == 0` reconciles the empty listing (gap 0, not None), so the census
    is measurable and the run succeeds.
    """
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        if args.get("section") == "status":
            return {"counts": {"Source": 0}}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    assert mod.main(["inventory", "--out", str(tmp_path / "inv.json"),
                     "--no-verify-read"]) == 0
    report = json.loads((tmp_path / "inv.json").read_text())
    assert report["sources_read"] == 0 and report["source_count_gap"] == 0


def test_a_re_run_under_another_spelling_of_the_target_still_skips(tmp_path):
    """P3-5 — R3's "skips a migrated key BEFORE it reaches the wire".

    The ledger was keyed by the literal `--target` string, so the SAME graph spelled
    `org_x_g_new` on day one and `g_new` on day two read as two targets: every row was
    re-sent, and idempotency rested entirely on target-side dedup (which
    `tortoise_ingest` does not promise). The scope is now the canonical identity.
    """
    rows = [{"graph_id": "g_old", "stable_id": "g_old", "namespace": "org_x_g_old"},
            {"graph_id": "g_new", "stable_id": "g_new", "namespace": "org_x_g_new"}]
    led = C.Ledger(tmp_path / "l.jsonl", target="org_x_g_new", graph_rows=rows)
    w = CountingWriter()

    first = C.build_plan([row("p1", kind="decision", content="a durable claim")],
                         source_graph="org_x_g_old", target_graph="org_x_g_new")
    got = C.run_plan(first, writer=w, ledger=led, apply=True, graph_rows=rows)
    assert got.written == 1, got

    # the same graph, spelled the way a shorter ref resolves on the next day
    again = C.build_plan([row("p1", kind="decision", content="a durable claim")],
                         source_graph="org_x_g_old", target_graph="g_new")
    res = C.run_plan(again, writer=w, ledger=led, apply=True, graph_rows=rows)
    assert (res.written, res.skipped_known) == (0, 1), (res.written, res.skipped_known)
    assert len(w.calls) == 1, w.calls


def test_a_ledger_scoped_to_another_graph_is_still_refused(tmp_path):
    """P3-5's counter-arm — canonicalisation must not turn the guard into a pass.

    Two DIFFERENT graphs whose refs both resolve share nothing, so the refusal
    survives: only spellings of the SAME canonical identity intersect.
    """
    rows = [{"graph_id": "g_a", "stable_id": "g_a", "namespace": "org_x_g_a"},
            {"graph_id": "g_b", "stable_id": "g_b", "namespace": "org_x_g_b"}]
    led = C.Ledger(tmp_path / "l.jsonl", target="org_x_g_a", graph_rows=rows)
    plan = C.build_plan([row("p1", kind="decision", content="c")],
                        source_graph="org_x_g_b", target_graph="org_x_g_b")
    with pytest.raises(C.CurationRefused, match=r"scoped to"):
        C.run_plan(plan, writer=CountingWriter(), ledger=led, apply=True, graph_rows=rows)


def test_a_count_only_source_listing_with_no_rows_is_refused():
    """Round-37 P3-3 — `{"total": 1578}` with no rows is a listing that did not arrive.

    The count-only arm accepted ANY integer, so this payload passed the shape gate,
    read 0 rows and exited 0 with only `source_count_gap: 1578` to notice — the
    drifted-listing shape the gate exists for.
    """
    mod = _tool_module()
    reader = mod.MCPReader(lambda name, args: {"total": 1578}, None)
    with pytest.raises(RuntimeError, match="unrecognised shape"):
        reader.read_sources()


def test_a_count_only_listing_that_says_zero_is_a_readable_empty_listing():
    """The same arm's honest case: a count of 0 IS consistent with no rows."""
    mod = _tool_module()
    reader = mod.MCPReader(lambda name, args: {"total": 0}, None)
    rows, total = reader.read_sources()
    assert rows == [] and total == 0


def test_a_disarmed_run_over_an_unmeasurable_census_still_exits_zero(monkeypatch, tmp_path,
                                                                     capsys):
    """Round-37 P3-2 — precedence between the off switch and the census refusal.

    A disarmed run is a SUCCESS (exit 0) — the round-22 contract — so the zero-source
    refusal must not fire ahead of it. The unmeasurable census is not swallowed: it
    travels on the artifact as a read note.
    """
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        if args.get("section") == "sources":
            return {"result": []}
        if args.get("section") == "status":
            return {"unexpected": "shape"}          # nothing to reconcile the empty listing
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--apply", "--off", "--no-verify-read",
                   "--ledger", str(tmp_path / "l.jsonl"), "--out", str(tmp_path / "p.json")])
    assert rc == 0, capsys.readouterr()
    assert "disarmed" in capsys.readouterr().err
    note = json.loads((tmp_path / "p.json").read_text())["read_note"]
    assert note and "cannot be told from an unread one" in note, note


def test_the_cli_hands_the_graph_rows_to_the_ledger_scope(monkeypatch, tmp_path):
    """Round-37 P3-4 — P3-5's pin sits one layer BELOW the shipped path.

    Both unit arms build `C.Ledger(..., graph_rows=…)` themselves, so deleting the
    `graph_rows=graphs` wiring in `tools/curate_graph.py` left them green while the
    shipped CLI recorded under the literal spelling and re-sent every row on the next
    run under another spelling. This drives `mod.main` twice over ONE ledger.
    """
    mod = _tool_module()
    rows = [{"graph_id": "g_old", "stable_id": "g_old", "namespace": "org_x_g_old"},
            {"graph_id": "g_new", "stable_id": "g_new", "namespace": "org_x_g_new"}]
    wire: list[str] = []

    def call(name, args):
        if name == "tortoise_graph_overview" and args.get("section") == "graphs":
            return {"result": rows}
        if name == "tortoise_graph_overview" and args.get("section") == "status":
            return {"counts": {"Source": 0}}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision",
                                "content": "a durable claim"}], "total": 1}
        if name == "tortoise_ingest":
            wire.append(args["graph"])
            return {"created": len(args["bundle"]["points"])}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    base = ["plan", "--graph", "org_x_g_old", "--ledger", str(tmp_path / "l.jsonl"),
            "--out", str(tmp_path / "p.json"), "--no-verify-read", "--apply"]
    assert mod.main([*base, "--target", "org_x_g_new"]) == 0
    assert wire == ["org_x_g_new"], wire

    # the same graph, spelled the way a shorter ref resolves the next day
    assert mod.main([*base, "--target", "g_new"]) == 0
    assert wire == ["org_x_g_new"], f"the re-run re-sent the row: {wire}"
    run = json.loads((tmp_path / "p.json").read_text())["run"]
    assert (run["written"], run["skipped_known"]) == (0, 1), run


def test_the_ledger_never_matches_a_different_graph_by_a_bare_tail():
    """Round-37 P2 — an id-space value in the match set is a fail-OPEN.

    `mcp_auth` resolves a bare token by graph NAME, so recording graph A under its
    `_tail` identity `x` made a later `--target x` — which resolves to a DIFFERENT
    graph NAMED `x` — share A's scope and skip every row into a target that owed all
    of them (`applied: True, written: 0`, exit 0).
    """
    rows = [{"graph_id": "g_x", "stable_id": "g_x", "namespace": "org_o1_g_x",
             "name": "alpha"},
            {"graph_id": "g_b", "stable_id": "g_b", "namespace": "org_o1_g_b",
             "name": "x"}]
    # graph A records under its own id field, never under the derived tail
    assert C.ledger_scope("org_o1_g_x", rows) == "g_x"
    # and its scope set is REFS of its own row — a different row shares none of them
    assert C.ledger_scopes("org_o1_g_x", rows).isdisjoint(C.ledger_scopes("x", rows)), (
        C.ledger_scopes("org_o1_g_x", rows), C.ledger_scopes("x", rows))


def test_a_name_that_equals_another_graphs_id_is_not_a_match():
    """Round-37 P2, second instance — a name is only unique AS a name.

    `mcp_auth.resolve_graph_target` is field-major (id → stable_id → namespace →
    name), so a name that equals ANOTHER graph's id resolves to that graph. Putting the
    bare field value in the ledger's match set therefore let graph BETA (whose `name`
    is graph ALPHA's `graph_id`) share ALPHA's scope. `_refs_unique_to` excludes every
    value two rows carry, so the sets are disjoint and the guard still refuses.
    """
    rows = [{"graph_id": "g_alpha", "stable_id": "g_alpha", "namespace": "org_o1_g_alpha",
             "name": "alpha"},
            {"graph_id": "g_beta", "stable_id": "g_beta", "namespace": "org_o1_g_beta",
             "name": "g_alpha"}]
    alpha = C.ledger_scopes("g_alpha", rows)
    beta = C.ledger_scopes("org_o1_g_beta", rows)
    assert alpha.isdisjoint(beta), (alpha, beta)
    # and the shared token really is the hazard: BETA names itself with ALPHA's id
    assert "g_alpha" in rows[1].values()


def test_the_ledger_never_records_the_default_sentinel():
    """`default` names the tenant graph without identifying it (`_canonical_from_row`)
    and is workspace-universal, while the ledger path is per-user — recording it would
    scope two tenants' graphs together. The row's own `stable_id` is used instead."""
    rows = [{"graph_id": "default", "stable_id": "g_tenant1", "namespace": "org_o1"}]
    assert C.ledger_scope("org_o1", rows) == "g_tenant1"


def test_a_disarmed_run_over_an_unmeasurable_points_read_exits_zero(monkeypatch, tmp_path,
                                                                    capsys):
    """Round-37 P3 — the switch precedence must cover EVERY census read, not one.

    A disarmed run over a failed points read raised straight out of `cmd_plan` (exit 1,
    no artifact) — the round-22 defect one arm over, and exactly backwards for the flag
    an operator flips DURING an incident. The artifact says `census_measured: false`, so
    the absent census cannot be read as an empty one.
    """
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [], "total": 0}       # an unmeasurable points read
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--apply", "--off", "--no-verify-read",
                   "--ledger", str(tmp_path / "l.jsonl"), "--out", str(tmp_path / "p.json")])
    assert rc == 0, capsys.readouterr()
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["census"] is None and payload["census_measured"] is False, payload
    assert "read returned 0 points" in payload["read_failure"], payload["read_failure"]
    assert payload["run"]["off"] is True and payload["run"]["applied"] is False
    # ⛔ `run.refused` names the CAUSE here too, not the helper's `"off-switch active"`
    # default: the switch is not what refused this run, and this arm must say the same
    # thing the sibling `CurationRefused` arm and the damaged-ledger arm say (the latter
    # is pinned on its own: `"damaged" in run["refused"]`). One state, one spelling.
    assert "read returned 0 points" in payload["run"]["refused"], payload["run"]


def test_an_armed_run_over_an_unmeasurable_points_read_still_fails(monkeypatch, tmp_path):
    """The counter-arm: the precedence is ONLY the switch. Armed, a failed read is as
    loud as it was — it must not become a quiet artifact."""
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [], "total": 0}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    with pytest.raises(RuntimeError, match="read returned 0 points"):
        mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                  "--apply", "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl")])


def test_the_cli_refuses_a_target_spelling_the_graphs_surface_cannot_resolve(
        monkeypatch, tmp_path):
    """Round-37 P3-2 — the SHIPPED shape of the `graphs` section.

    `graphs()` wraps each string from `tortoise_list_graphs()` as `{"namespace": s}`
    (`sdk.list_graphs() -> list[str]`), so the surface exposes exactly the strings it
    lists. A `--target` spelled differently from every exposed string is therefore
    REFUSED by the identity guard — never silently re-sent, and never skipped: that is
    the honest contract on today's surface, and this arms it.
    """
    mod = _tool_module()
    wire: list[str] = []

    def call(name, args):
        if name == "tortoise_graph_overview" and args.get("section") == "graphs":
            return {"result": ["org_x_g_old", "org_x_g_new"]}   # the REAL payload shape
        if name == "tortoise_graph_overview" and args.get("section") == "status":
            return {"counts": {"Source": 0}}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision",
                                "content": "a durable claim"}], "total": 1}
        if name == "tortoise_ingest":
            wire.append(args["graph"])
            return {"created": len(args["bundle"]["points"])}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    base = ["plan", "--graph", "org_x_g_old", "--ledger", str(tmp_path / "l.jsonl"),
            "--out", str(tmp_path / "p.json"), "--no-verify-read", "--apply"]
    assert mod.main([*base, "--target", "org_x_g_new"]) == 0
    assert wire == ["org_x_g_new"], wire

    # a spelling this surface does not expose: a REFUSAL, not a re-send and not a skip
    assert mod.main([*base, "--target", "g_new"]) == 3
    assert wire == ["org_x_g_new"], f"an unresolvable spelling reached the wire: {wire}"


def test_a_name_never_scopes_a_ledger_even_when_the_id_is_not_exposed():
    """Round-38 P2 — the shipped `graphs` shape LOSES `graph_id`/`stable_id`.

    `MCPReader.graphs()` wraps a `list[str]` as `{"namespace": s}`, so in production a
    row usually exposes only its namespace. A name that equals ANOTHER graph's id is
    therefore not carried by any row in an exact field, and the previous "only unique
    values" filter judged it unique: ALPHA's `name: g_beta` put `g_beta` in ALPHA's
    scope, so a run into BETA skipped every row (`applied: True, written: 0`, exit 0)
    and a run spelled by BETA's id recorded its keys under ALPHA's scope.

    The ledger now resolves through the EXACT id fields only (`_ledger_row`), never
    through `_matched_row`'s name pass, so an id the exact fields do not name scopes to
    itself and the guard refuses instead of inheriting another graph's keys.
    """
    rows = [{"graph_id": "g_old", "namespace": "org_o1_g_old", "name": "old"},
            {"namespace": "org_o1_g_beta"},                      # BETA's id is NOT exposed
            {"graph_id": "g_alpha", "namespace": "org_o1_g_alpha", "name": "g_beta"}]
    alpha = C.ledger_scopes("org_o1_g_alpha", rows)
    beta = C.ledger_scopes("org_o1_g_beta", rows)
    by_beta_id = C.ledger_scopes("g_beta", rows)
    assert alpha.isdisjoint(beta), (alpha, beta)
    assert alpha.isdisjoint(by_beta_id), (alpha, by_beta_id)
    assert "g_beta" not in alpha, alpha
    assert C.ledger_scope("g_beta", rows) != "g_alpha"
    # and the unification the feature exists for still holds where the id IS exposed
    assert C.ledger_scope("org_o1_g_alpha", rows) == "g_alpha"


def test_the_default_sentinel_cannot_scope_a_ledger(tmp_path):
    """Round-38 P3 — excluding the sentinel from the RECORDED key was half the fix.

    It still entered the MATCH set, and the fallback still recorded it, so workspace A's
    `{"target": "default"}` line made workspace B's run report `written: 0`. `default`
    is workspace-universal while the ledger file is per-user, so it is not a scope at
    all: a target identified only by the sentinel is refused, like any unproven
    identity.
    """
    with pytest.raises(C.CurationRefused, match=r"`default` sentinel"):
        C.Ledger(tmp_path / "l.jsonl", target="default")
    # a row that carries the sentinel but also a real id is fine — the id is the scope
    rows = [{"graph_id": "default", "stable_id": "g_tenant1", "namespace": "org_o1"}]
    assert C.ledger_scope("org_o1", rows) == "g_tenant1"
    assert "default" not in C.ledger_scopes("org_o1", rows)


def test_an_applying_run_with_no_identity_rows_is_refused(monkeypatch, capsys, tmp_path):
    """Round-38 P3 — an EMPTY `graphs` section silently disabled the rows authority.

    With no identity rows `assert_distinct_graphs` falls back to shape, which accepts
    any id-shaped ref, so `--target g_new` was WRITTEN while the ledger filed the key
    under a second scope for the same graph — the re-send the doc said could not
    happen. An apply with no identity rows is an unproven target: refused.
    """
    mod = _tool_module()
    wire: list[str] = []

    def call(name, args):
        if name == "tortoise_graph_overview" and args.get("section") == "graphs":
            return {"result": []}                                # empty, non-erroring
        if name == "tortoise_graph_overview" and args.get("section") == "status":
            return {"counts": {"Source": 0}}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        if name == "tortoise_ingest":
            wire.append(args["graph"])
            return {"created": 1}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new", "--apply",
                   "--ledger", str(tmp_path / "l.jsonl"), "--no-verify-read"])
    assert rc == 3, capsys.readouterr()
    assert "no identity rows" in capsys.readouterr().err
    assert wire == [], f"an unproven target reached the wire: {wire}"


def test_a_disarmed_run_over_a_damaged_ledger_still_exits_zero(monkeypatch, tmp_path,
                                                               capsys):
    """Round-38 P3 — the disarmed contract must be TOTAL, not per-arm.

    A disarmed run whose census read failed exited 0, while a disarmed run over a
    damaged ledger exited 3 — two runs in one incident with opposite contracts. The
    switch wins for both, and because the census WAS measured in the ledger case it
    travels on the artifact (`census_measured: true`), so the two losses are not
    reported as one fact.
    """
    mod = _tool_module()
    led = tmp_path / "l.jsonl"
    led.write_text("null\n")          # a damaged ledger line: unreadable keys

    def call(name, args):
        if name == "tortoise_graph_overview" and args.get("section") == "status":
            return {"counts": {"Source": 0}}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new", "--apply",
                   "--off", "--ledger", str(led), "--no-verify-read",
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0, capsys.readouterr()
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["census_measured"] is True and payload["census"], payload
    assert "damaged" in payload["disarmed_reason"], payload["disarmed_reason"]
    assert payload["read_failure"] is None


def test_the_ledger_resolves_field_major_like_the_write_path():
    """Round-39 P3 — `_ledger_row` walked ROW-major while the resolver walks FIELD-major.

    A ROW-major walk returns a row whose LATER field equals the ref before one whose
    EARLIER field does, so rows `[{graph_id: g_alpha, namespace: LIVE},
    {graph_id: LIVE, namespace: org_o1_g_beta}]` resolved `LIVE` to BETA on the write
    path and to ALPHA here: the run wrote into BETA and recorded its keys under ALPHA,
    and a later run into ALPHA then reported `written: 0` over rows it never received.
    """
    rows = [{"graph_id": "g_alpha", "namespace": "LIVE"},
            {"graph_id": "LIVE", "namespace": "org_o1_g_beta"}]
    # FIELD-major: `graph_id` on row 2 beats `namespace` on row 1 — the resolver's order
    scope = C.ledger_scope("LIVE", rows)
    assert scope != "g_alpha", scope
    assert scope == "org_o1_g_beta", scope
    assert C.ledger_scopes("LIVE", rows).isdisjoint(C.ledger_scopes("g_alpha", rows))


def test_every_plan_payload_says_whether_the_census_was_measured(monkeypatch, tmp_path):
    """Round-39 P3 — `census_measured` was on the two FAILURE arms only.

    The field exists so a driver answers "was it measured?" uniformly; the most common
    disarmed run (healthy `--off`) is exactly the one that lacked it, and a driver doing
    `payload["census_measured"]` KeyError-ed there while the failure arms were explicit.
    Both `inventory` and a healthy `plan` (disarmed and armed) now carry it.
    """
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_graph_overview" and args.get("section") == "status":
            return {"counts": {"Source": 0}}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    assert mod.main(["inventory", "--out", str(tmp_path / "inv.json"),
                     "--no-verify-read"]) == 0
    assert json.loads((tmp_path / "inv.json").read_text())["census_measured"] is True

    for extra, out in ((["--off"], "off.json"), ([], "on.json")):
        assert mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                         *extra, "--no-verify-read", "--out", str(tmp_path / out),
                         "--ledger", str(tmp_path / "l.jsonl")]) == 0
        assert json.loads((tmp_path / out).read_text())["census_measured"] is True


def test_the_disarmed_unreconciled_arm_says_the_sources_were_not_measured(monkeypatch,
                                                                          tmp_path):
    """Round-40 P3 — `census_measured` was unconditionally True on the plan payload.

    The disarmed arm carries an UNRECONCILABLE source listing through to the artifact
    (the armed one returns 3 above), so on that arm the source population was not
    measured. The field exists so a driver reads that from the field alone, with the
    reason in `read_note` — a blanket True made the two arms indistinguishable.
    """
    mod = _tool_module()

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        if args.get("section") == "sources":
            return {"result": []}
        if args.get("section") == "status":
            return {"unexpected": "shape"}           # nothing to reconcile the empty listing
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    assert mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                     "--apply", "--off", "--no-verify-read",
                     "--out", str(tmp_path / "p.json"),
                     "--ledger", str(tmp_path / "l.jsonl")]) == 0
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["census_measured"] is False, payload["census_measured"]
    assert payload["census"], "the MEASURED half of the census still travels"
    assert "cannot be told from an unread one" in (payload["read_note"] or "")


def test_a_disarmed_run_over_a_damaged_ledger_reports_what_it_still_owes(monkeypatch,
                                                                         tmp_path, capsys):
    """Round-38 review P2 — a measured census beside `pending: 0` reads as "done".

    The disarmed-over-a-damaged-ledger arm carries a MEASURED census, so the ledger —
    the only thing that could say which of those rows already landed — is exactly what
    could not be read. Reporting the hardcoded `pending: 0` therefore said "nothing is
    owed" about a run that wrote nothing, and a driver's `pending == 0 ⇒ done` check
    reads a refused migration as finished. `pending` is now the carry count (the
    upper bound the unreadable ledger leaves), so the documented three-counter
    invariant still SPANS the carry set on this arm.
    """
    mod = _tool_module()
    led = tmp_path / "l.jsonl"
    led.write_text("null\n")          # a damaged ledger line: unreadable keys

    def call(name, args):
        if name == "tortoise_graph_overview" and args.get("section") == "status":
            return {"counts": {"Source": 0}}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "a"},
                               {"id": "p2", "kind": "decision", "content": "b"}],
                    "total": 2}
        if name == "tortoise_ingest":
            raise AssertionError("a disarmed run reached the wire")
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new", "--apply",
                   "--off", "--ledger", str(led), "--no-verify-read",
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0, capsys.readouterr()
    payload = json.loads((tmp_path / "p.json").read_text())
    carried = payload["census"]["carried_rows"]
    run = payload["run"]
    assert carried == 2, payload["census"]
    assert run["pending"] == carried, run
    assert run["skipped_known"] + run["duplicate_rows"] + run["pending"] == carried, run
    # and the `refused` field names the LEDGER, not the switch the operator set
    assert "damaged" in run["refused"], run["refused"]
    # ⛔ The reasoning layer's fields ride this artifact too. It hands over a FULL census
    # stopped by the ledger, so it is the one disarmed arm where the layer WAS read — and
    # without these keys a driver doing the switch found `census_measured: true` with the
    # only contrary fact in prose (round-2 review). `None` would be wrong here: the read
    # completed, so the value is a real boolean.
    assert payload["operators_read"] == 0, payload
    assert payload["operators_total_reported"] == 0, payload
    assert payload["operators_reconciled"] is True, payload


def test_a_name_spelled_target_scopes_literally_and_never_skips(tmp_path):
    """Round-38 review P2 — record what a NAME spelling actually does.

    A graph row that exposes ids makes its NAME a guard-accepted target spelling, but
    a name is not an identity and must not enter the ledger match set (round-38 P2: a
    name equal to another graph's id skipped every row into it). What the ledger does
    instead is scope the spelling LITERALLY, and this pin states the consequence
    honestly: the two scopes stay disjoint (so a name can never SKIP rows), and a
    ledger built for one spelling driven against a plan for the other REFUSES loudly
    rather than quietly re-sending into a target it cannot account for.

    ⛔ The mutant this pin kills is `d29e07cf56ef`, NOT the commit that introduced
    `_ledger_row` (`b9793ae2b358`): there the ledger already refused the name pass, so
    this test PASSES at it. Measured on `d29e07cf56ef`: the scope set
    came back `{g_<hash>, 'new', org_x_g_<hash>}` — the row's refs leaking into a name
    spelling, which is the round-38 skip.
    """
    assert C.ledger_scopes("new", ROWS) == frozenset({"new"}), C.ledger_scopes("new", ROWS)
    by_ref = C.ledger_scopes(ROWS[1]["namespace"], ROWS)
    assert by_ref.isdisjoint({"new"}), by_ref

    # the unit seam: a ledger scoped by the ref, driven against a plan for the name
    led = C.Ledger(tmp_path / "l.jsonl", target=ROWS[1]["namespace"], graph_rows=ROWS)
    led.record(["k1"])
    plan = C.build_plan([row("a", content="c")], source_graph="old", target_graph="new",
                        packs={})
    with pytest.raises(C.CurationRefused, match="answering for another graph"):
        C.run_plan(plan, writer=CountingWriter(), ledger=led, apply=True, graph_rows=ROWS)

def test_a_disarmed_run_over_a_damaged_ledger_keeps_the_unmeasured_source_fact(
        monkeypatch, tmp_path):
    """Round-41 P3 — the round-40 fix landed on the main payload, not this sibling arm.

    A disarmed run can be BOTH unreconciled (sources unmeasurable) AND ledger-refused.
    `_disarmed_artifact` derived `census_measured` from `census is not None`, so that
    combination reported `True` over an unmeasured source population and dropped the
    reason entirely (no `read_note`). The caller now supplies the fact, and the note
    travels.
    """
    mod = _tool_module()
    led = tmp_path / "l.jsonl"
    led.write_text("null\n")                       # a damaged ledger line

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        if args.get("section") == "sources":
            return {"result": []}
        if args.get("section") == "status":
            return {"unexpected": "shape"}          # nothing to reconcile the empty listing
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", _with_operator_count(call))
    assert mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                     "--apply", "--off", "--no-verify-read", "--ledger", str(led),
                     "--out", str(tmp_path / "p.json")]) == 0
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["census"], "the MEASURED half still travels"
    assert payload["census_measured"] is False, payload["census_measured"]
    assert "cannot be told from an unread one" in (payload["read_note"] or ""), payload
    assert "damaged" in payload["disarmed_reason"], payload["disarmed_reason"]


def test_the_disarmed_operator_line_agrees_with_the_artifact_it_accompanies(monkeypatch,
                                                                            tmp_path,
                                                                            capsys):
    """Round-42 P3 — the stderr label derived its claim from `census is not None`.

    On the arm this file's round-41 pin covers (disarmed + damaged ledger + an
    unreconcilable source listing) the artifact honestly said `census_measured: false`
    while the operator-facing line said "census measured". The incident log and the
    JSON told two stories about one run, and the label was the last place in the helper
    still ignoring the caller-supplied fact.
    """
    mod = _tool_module()
    led = tmp_path / "l.jsonl"
    led.write_text("null\n")

    def call(name, args):
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        if args.get("section") == "sources":
            return {"result": []}
        if args.get("section") == "status":
            return {"unexpected": "shape"}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    assert mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                     "--apply", "--off", "--no-verify-read", "--ledger", str(led),
                     "--out", str(tmp_path / "p.json")]) == 0
    payload = json.loads((tmp_path / "p.json").read_text())
    err = capsys.readouterr().err
    assert payload["census_measured"] is False, payload["census_measured"]
    assert "census NOT measured" in err, err
    assert "census measured:" not in err, err


# ── the operator layer is READABLE, and the census must not omit it ───────
#
# Measured on live-v4, 2026-11-13, read-only: `graph_overview(section="structure")`
# reports `operators: 3,727`, and `tortoise_search(entity_type="operator",
# limit=5000)` returns **3,727 rows in ONE call** — an exact match with the reported
# count — with `match_source="structural"` and no vector leg. The harness described
# that layer as unlistable (`NOT_ENUMERABLE["operator"]`: "no listing surface ... ask
# for an operator kind") and never read it: `tortoise_query(kind="operator")` and
# `tortoise_query(kind="IMPL")` both answer `total: 0` on that graph, so the named
# escape hatch returns zero rather than rows.
#
# The consequence was a plan that reports `total_rows: 17,420` over a readable
# population of 21,147, and a document table claiming `operator | carry` beside a
# `carried_rows: 127`. The issue's own Target 2 — "every dropped row is named in the
# report with its reason; nothing is dropped silently" — is what these cases pin.

#: The real `tortoise_search(entity_type="operator")` row shape: `point_kind` (snake),
#: `content` carrying the operator's LABEL (sdk.py's projection: "label is searchable
#: text"), and no endpoints at all (`relationships: []`).
OPERATOR_ROWS = [
    {"id": "1a1a9ad8b1c-4d77ae46f01f", "content": "", "point_kind": "IMPL"},
    {"id": "1a1a9ad8b1d-000000000002", "content": "", "point_kind": "IMPL"},
    {"id": "1a1a9ad8b1e-000000000003", "content": "supports the claim", "point_kind": "NAND"},
]


_UNSET = object()


def _with_operator_count(call, operators=(), events=()):
    """Adapt a PRE-OPERATOR fake surface: it now has to answer MORE reads.

    Every fake written before 2026-11-13 answers an unknown section with an empty
    listing, which the harness cannot tell from an UNREAD operator layer — so the
    designed refusal fires and a test about the ledger/identity/off-switch arms would
    fail for an unrelated reason. The wrapper makes the fake's graph explicit: these
    rows answer `tortoise_search`, and this count answers `structure.operators`.
    A test that exercises the operator read itself must NOT be wrapped.

    The EVENT layer (#12378) needs the same adaptation for the same reason, and without
    it the whole pre-existing suite would fail on a refusal that has nothing to do with
    what each test is about: `events` answers `tortoise_get_entity(type="events")` and
    its length answers `counts.Event` in the status section. The default `events=()` is a
    graph with NO Event layer, which is a legitimate census — and because the count is
    merged as well, it reconciles to 0 rather than hitting the silent-zero refusal.
    A test that exercises the EVENT read itself must NOT be wrapped either.
    """
    def wrapped(name, args):
        args = args or {}
        if name == "tortoise_search":
            return {"result": list(operators)}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            return {"result": list(events)}
        res = call(name, args)
        if args.get("section") == "structure" and isinstance(res, dict) and "error" not in res:
            merged = {k: v for k, v in res.items() if k not in ("result", "results", "points")}
            merged.setdefault("operators", len(operators))
            return merged
        if args.get("section") == "status" and isinstance(res, dict) and "error" not in res:
            merged = dict(res)
            counts = merged.get("counts")
            if isinstance(counts, dict):
                counts = dict(counts)
                counts.setdefault("Event", len(events))
                merged["counts"] = counts
            else:
                merged.setdefault("Event", len(events))
            return merged
        return res
    return wrapped


def _operator_surface(operators=None, *, reported=_UNSET, points=None, events=()):
    """A fake surface whose operator layer answers `tortoise_search`, like the real one.

    ``reported=None`` means the `structure` section carries NO `operators` key at all
    (the unreported arm) — distinct from the default, which is the layer's own length.
    ``events`` answers the Event read (#12378) and for the same reason: every surface in
    this file now has to, or the census refuses a layer the fake simply never mentioned.
    """
    operators = OPERATOR_ROWS if operators is None else operators
    points = ([{"id": "p1", "kind": "decision", "content": "a durable claim"}]
              if points is None else points)
    reported = len(operators) if reported is _UNSET else reported

    def call(name, args):
        args = args or {}
        if name == "tortoise_search":
            if args.get("entity_type") == "operator":
                return {"result": list(operators)}
            return {"result": []}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            return {"result": list(events)}
        if name == "tortoise_query":
            return {"result": list(points), "total": len(points)}
        if args.get("section") == "structure":
            structure = {"total": len(points)}
            if reported is not None:
                structure["operators"] = reported
            return structure
        if args.get("section") == "status":
            return {"counts": {"Point": len(points), "Source": 0,
                               "Event": len(events)}}
        if args.get("section") == "sources":
            return {"result": []}
        if args.get("section") == "graphs":
            return {"result": [{"namespace": "org_x_g_old"}, {"namespace": "org_x_g_new"}]}
        return {"result": []}
    return call


def test_the_operator_layer_reaches_the_census(monkeypatch, tmp_path):
    """The layer the harness declared unlistable is 3,727 readable rows in one call.

    Dropping it made `total_rows` a claim about a population the pass had not read:
    the operator rows are as much a part of "what is in the old graph" as a Point,
    and a census that silently omits them cannot answer "what will be carried".
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool", _operator_surface())
    assert mod.main(["inventory", "--out", str(tmp_path / "inv.json"),
                     "--no-verify-read"]) == 0
    report = json.loads((tmp_path / "inv.json").read_text())
    census = report["census"]
    assert census["total_rows"] == 4, census
    assert census["by_class"]["operator:IMPL"]["undecidable"] == 2, census["by_class"]
    assert census["by_class"]["operator:NAND"]["undecidable"] == 1, census["by_class"]
    assert report["operators_read"] == 3, report.get("operators_read")
    assert report["operators_total_reported"] == 3


def test_the_operator_layer_is_not_claimed_unlistable():
    """The surface table must not describe a readable layer as having no listing.

    `NOT_ENUMERABLE["operator"]` told every consumer (and the architecture doc) to
    count the layer instead of reading it, and named an escape hatch
    (`ask for an operator kind`) that answers `total: 0` on the live graph.
    """
    mod = _tool_module()
    assert "operator" not in mod.NOT_ENUMERABLE, mod.NOT_ENUMERABLE
    # ...and the classes this pass does NOT read stay named, each with its own reason.
    # ⛔ `event` is NOT in this tuple any more: #12378 made the Event layer a READ layer,
    # so an entry describing it as unenumerated would be the same defect one layer over.
    # (`test_the_event_layer_is_no_longer_claimed_unreadable` pins that side.)
    for still_named in ("object", "subject"):
        assert still_named in mod.NOT_ENUMERABLE, still_named


def test_an_operator_is_never_carried_as_a_floating_node():
    """Its meaning is its ENDPOINTS, which the read surface does not serve.

    Carrying an operator as a point writes a node with no source and no targets —
    the exact shape the issue forbids ("Every retained fact is connected ... not a
    floating node"). The disposition is therefore undecidable, and the reason has to
    say why: a reader who sees `carry` on 3,727 operator rows will migrate them.
    """
    labelled = C.classify(C.Row(node_id="o1", node_type="operator", kind="IMPL",
                                content="supports the claim"), {})
    assert labelled.disposition == C.UNDECIDABLE, labelled
    assert "endpoint" in labelled.reason, labelled.reason
    # An empty LABEL is not "nothing to carry": the row is a relationship, and its
    # content being empty says nothing about whether the relationship matters.
    unlabelled = C.classify(C.Row(node_id="o2", node_type="operator", kind="IMPL",
                                  content=""), {})
    assert unlabelled.disposition == C.UNDECIDABLE, unlabelled
    assert "endpoint" in unlabelled.reason, unlabelled.reason


def test_two_operators_with_the_same_empty_label_are_two_relationships():
    """Content-addressing an operator folds DISTINCT relationships onto one keeper.

    Their labels are empty, so a content hash makes 3,727 relationships one row plus
    3,726 "exact duplicates" — the reasoning layer reported as a single claim (or, on
    a two-row plan, one claim plus one row whose reason says it is a duplicate of it).

    ⛔ The assertion is on the KEY and on the duplicate CLAIM, not on the row count:
    `build_plan` appends a Decision for EVERY row, so `len(decisions)`,
    `total_rows` and `totals` are identical under both keys and a row-count test passes
    on the reverted code (measured by the round-1 reviewer).
    """
    plan = C.build_plan(
        [row("o1", node_type="operator", kind="IMPL", content=""),
         row("o2", node_type="operator", kind="IMPL", content="")],
        source_graph="old", target_graph="new", packs={})
    keys = [d.stable_key for d in plan.decisions]
    assert len(set(keys)) == 2, keys
    assert not any("duplicate" in d.reason for d in plan.decisions), [
        d.reason for d in plan.decisions]
    assert plan.census()["totals"]["undecidable"] == 2


def test_an_operator_read_short_of_the_reported_count_is_refused(monkeypatch, capsys,
                                                                tmp_path):
    """The single-call operator read must reconcile, like the source listing does.

    `tortoise_search` has no `offset`, so the layer is read in ONE bounded call. A
    surface that answers fewer rows than `structure.operators` reports (a clamp, a
    partial response) would otherwise enter the census as the whole layer — the
    silent-partial-census class, one node type over.
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool",
                        _operator_surface(operators=OPERATOR_ROWS, reported=9))
    rc = mod.main(["inventory", "--out", str(tmp_path / "inv.json"), "--no-verify-read"])
    assert rc == 3, (rc, capsys.readouterr())
    assert "operator" in capsys.readouterr().err.lower()


def test_an_operator_layer_beyond_one_call_is_refused_not_truncated(monkeypatch, capsys,
                                                                   tmp_path):
    """A population the single-call read cannot cover is a refusal, never a page.

    There is no offset walk on `tortoise_search`, so a layer larger than the declared
    ceiling cannot be read completely — and a truncated read is worse than a refusal,
    because it enters the census looking complete.

    ⛔ The discriminating assertion is that NO READ WAS ISSUED, not a message substring.
    Round-7 review measured that deleting this guard still exits 3 (the reconciliation
    guard fires one line later, on the same input) and the only thing separating the two
    was the word "offset" in stderr — so rewording either message flipped the verdict
    with no behaviour change. The ceiling's real job is to refuse BEFORE spending the
    call: that is what a layer above the surface's maximum needs, because the read it
    would spend returns a partial one.
    """
    mod = _tool_module()
    ceiling = mod.OPERATOR_READ_CEILING
    calls: list[str] = []
    surface = _operator_surface(operators=OPERATOR_ROWS, reported=ceiling + 1)

    def call(name, args):
        calls.append(name)
        return surface(name, args)

    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["inventory", "--out", str(tmp_path / "inv.json"), "--no-verify-read"])
    assert rc == 3, (rc, capsys.readouterr())
    assert "offset" in capsys.readouterr().err.lower()
    assert "tortoise_search" not in calls, (
        "a layer above the single-call ceiling must be refused BEFORE the read is spent"
    )


def test_an_operator_read_that_fills_its_limit_is_refused_unless_the_count_vouches(
        monkeypatch, capsys, tmp_path):
    """A response that fills the requested limit EXACTLY may be truncated.

    `tortoise_search` has a `limit` and no `offset`, so there is no page to walk to:
    "a full page" and "a truncated layer" are the same bytes unless a reported count
    says that is the whole population. Round-1 review found this arm unpinned.

    ⛔ BOTH arms are pinned here. Refusing is the safe failure, but always refusing is
    the OVER-BROAD one: a layer of exactly `OPERATOR_READ_CEILING` rows that
    `structure.operators` vouches for as complete is a legitimate full read, and round-7
    review measured that flipping the guard to `if len(raw) == OPERATOR_READ_CEILING:`
    (refuse the vouched-for case too) left every test green while blocking the whole
    census — the carry set included, since the refusal has no override.
    """
    mod = _tool_module()
    at_limit = [{"id": f"o{i}", "content": "", "point_kind": "IMPL"}
                for i in range(mod.OPERATOR_READ_CEILING)]
    monkeypatch.setattr(mod, "_live_call_tool",
                        _operator_surface(operators=at_limit, reported=None))
    rc = mod.main(["inventory", "--out", str(tmp_path / "inv.json"), "--no-verify-read"])
    assert rc == 3, (rc, capsys.readouterr())
    assert "truncat" in capsys.readouterr().err.lower()

    # …and the ACCEPT arm: the same 10,000 rows WITH a count that vouches for them is a
    # complete layer, not a truncation.
    monkeypatch.setattr(mod, "_live_call_tool",
                        _operator_surface(operators=at_limit,
                                          reported=mod.OPERATOR_READ_CEILING))
    out = tmp_path / "inv_accepted.json"
    rc = mod.main(["inventory", "--out", str(out), "--no-verify-read"])
    assert rc == 0, (rc, capsys.readouterr())
    payload = json.loads(out.read_text())
    assert payload["operators_read"] == mod.OPERATOR_READ_CEILING, payload["operators_read"]
    assert payload["operators_total_reported"] == mod.OPERATOR_READ_CEILING
    assert payload["operators_reconciled"] is True


def test_an_operator_row_without_an_id_is_refused(monkeypatch, capsys, tmp_path):
    """The class's identity key IS its id — its text is a label, routinely empty.

    Two id-less operator rows are indistinguishable and would fold onto one keeper, so
    the layer is refused rather than half-identified. Round-1 review found this arm
    unpinned.
    """
    mod = _tool_module()
    rows = [{"id": "o1", "content": "", "point_kind": "IMPL"},
            {"content": "", "point_kind": "IMPL"}]
    monkeypatch.setattr(mod, "_live_call_tool", _operator_surface(operators=rows))
    rc = mod.main(["inventory", "--out", str(tmp_path / "inv.json"), "--no-verify-read"])
    assert rc == 3, (rc, capsys.readouterr())
    assert "no `id`" in capsys.readouterr().err


def test_a_zero_operator_read_with_no_count_is_refused(monkeypatch, capsys, tmp_path):
    """An empty layer and an UNREAD one are the same bytes without a count.

    The silent-zero class `read_points` and `_unreconciled_sources` already fail closed
    on, one node type over. Round-1 review found this arm fail-OPEN: a 0-row read with
    no `structure.operators` to reconcile it reported `census_measured: True` over an
    operator population nobody had measured.
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool",
                        _operator_surface(operators=[], reported=None))
    rc = mod.main(["inventory", "--out", str(tmp_path / "inv.json"), "--no-verify-read"])
    assert rc == 3, (rc, capsys.readouterr())
    assert "empty" in capsys.readouterr().err.lower()


def test_the_operator_read_is_routed_to_the_graph_and_falls_back_on_a_binding_refusal(
        monkeypatch, tmp_path):
    """`graph` is the credential's per-call routing argument — the operator read needs it.

    Omitting it read the operator layer from the DEFAULT graph while every other section
    came from `--graph`: a census mixing two graphs, and a hard refusal on a correct run
    whenever the two graphs' operator counts differed. Round-1 review found it, and found
    that no fake surface exercised the argument.
    """
    mod = _tool_module()
    seen: list[dict] = []

    def call(name, args):
        args = args or {}
        if name == "tortoise_search":
            seen.append(dict(args))
            # A graph-BOUND credential refuses ANY graph argument, and the layer is
            # then read from the credential's own graph — recorded, never silent.
            if "graph" in args:
                return {"error": "This credential is bound to one graph and cannot be "
                                 "re-targeted by a `graph` argument (requested x)"}
            return {"result": list(OPERATOR_ROWS)}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            # This surface's Event layer is empty but RECONCILED: the census reads it
            # now (#12378), and an unmentioned layer would refuse the run for a reason
            # this test is not about.
            return {"result": []}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision", "content": "c"}], "total": 1}
        if args.get("section") == "structure":
            return {"total": 1, "operators": len(OPERATOR_ROWS)}
        if args.get("section") == "status":
            return {"counts": {"Point": 1, "Source": 0, "Event": 0}}
        if args.get("section") == "sources":
            return {"result": []}
        return {"result": []}
    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["inventory", "--graph", "org_x_g_old",
                   "--out", str(tmp_path / "inv.json"), "--no-verify-read"])
    assert rc == 0
    assert seen and seen[0].get("graph") == "org_x_g_old", seen
    # ⛔ The REQUEST SHAPE, not only the routing: `limit` is what makes the at-limit
    # truncation guard reachable at all (it compares `len(raw)` to the CEILING), and
    # `entity_type` is what selects the operator layer instead of a points search.
    # Round-7 review measured that dropping `limit` from the request left every test
    # green — the fake ignores unknown args, so nothing observed them.
    for sent in seen:
        assert sent.get("entity_type") == "operator", sent
        assert sent.get("limit") == mod.OPERATOR_READ_CEILING, sent
    assert any("graph" not in a for a in seen), "the fallback read must omit the argument"
    report = json.loads((tmp_path / "inv.json").read_text())
    assert report["operators_read"] == len(OPERATOR_ROWS)
    assert "credential's own graph" in (report["read_note"] or ""), report["read_note"]


def test_a_disarmed_plan_over_a_refused_operator_layer_still_exits_zero(
        monkeypatch, capsys, tmp_path):
    """The off switch is TOTAL: a DESIGNED refusal of the layer is not a failure there.

    Round-1 review found this arm unpinned — and it is the only place a
    `CurationRefused` is deliberately converted into a success (nothing was going to be
    written, so the round-22 contract applies), so it needs its own pin.
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool",
                        _operator_surface(operators=OPERATOR_ROWS, reported=9))
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--off", "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0, (rc, capsys.readouterr())
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["census"] is None, payload
    assert payload["census_measured"] is False
    assert "structure.operators" in (payload["disarmed_reason"] or "")
    # ⛔ A `CurationRefused` IS a read that failed, and the sibling arm sets this field for
    # exactly that — a driver switching on `read_failure` to answer "did the read fail?"
    # must not get null on one read-failure arm and a message on the other (round-3).
    assert "structure.operators" in (payload["read_failure"] or ""), payload
    # ⛔ And the field that says WHO refused names the cause here, like every other
    # disarmed failure arm (the ledger arm's own test asserts `"damaged" in
    # run["refused"]`) — this is the arm's UNIQUE behaviour, and round-7 review measured
    # it asserted nowhere: dropping `refused=str(exc)` left 193 tests green, as did
    # deleting the whole arm (the generic `except Exception` below produces the same
    # rc/census/read_failure). The generic arm prefixes the exception TYPE, so the
    # `disarmed_reason` shape below is what tells the two arms apart.
    assert "structure.operators" in (payload["run"]["refused"] or ""), payload["run"]
    assert not payload["disarmed_reason"].startswith("CurationRefused:"), payload


def test_a_quota_envelope_on_the_operator_read_does_not_re_read_another_graph():
    """Only a refusal ABOUT the graph argument may fall back — `_section`'s rule.

    Round-2 review measured this unpinned in the FAIL-OPEN direction: making both
    fallbacks unconditional (`if reason:`) left the whole suite green, so a quota or DB
    error would silently re-read the operator layer from a DIFFERENT graph and report it
    under `--graph`'s identity. The sibling guards for the sections are pinned by
    `test_a_quota_envelope_does_not_re_read_a_different_graph`.
    """
    mod = _tool_module()
    calls: list[dict] = []

    def call(name, args):
        calls.append(dict(args or {}))
        return {"error": "429 Too Many Requests — retry_after 10"}

    reader = mod.MCPReader(call, "org_x_g_old")
    with pytest.raises(RuntimeError, match="refused"):
        reader.read_operators({"operators": 3})
    assert len(calls) == 1, calls           # ONE call: no fallback, no second graph
    assert calls[0].get("graph") == "org_x_g_old", calls


def test_a_transport_error_on_the_operator_read_does_not_re_read_another_graph():
    """A raised transport failure says nothing about the graph binding — it raises.

    The round-2 counterpart of `test_a_transport_failure_does_not_fall_back_to_another_graph`:
    deleting the operator read's `try/except` discrimination left the suite green.
    """
    mod = _tool_module()
    calls: list[dict] = []

    def call(name, args):
        calls.append(dict(args or {}))
        raise TimeoutError("read timed out after 10s")

    reader = mod.MCPReader(call, "org_x_g_old")
    with pytest.raises(TimeoutError):
        reader.read_operators({"operators": 3})
    assert len(calls) == 1 and calls[0].get("graph") == "org_x_g_old", calls


def test_an_armed_plan_over_a_refused_operator_layer_exits_three(monkeypatch, capsys,
                                                                tmp_path):
    """An ARMED plan reports the designed refusal as rc 3, never as a traceback.

    The doc's exit-code table defines 3 as a designed failure and 1 as "not part of the
    designed contract"; without the `except C.CurationRefused` arm in `cmd_plan` the
    refusal escapes as an unhandled `RuntimeError`. Round-2 review measured the whole
    arm removable with the suite green — the disarmed counter-arm was covered, this one
    was not.
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool",
                        _operator_surface(operators=OPERATOR_ROWS, reported=9))
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                   "--out", str(tmp_path / "p.json")])
    assert rc == 3, (rc, capsys.readouterr())
    assert "REFUSED" in capsys.readouterr().err


def test_the_plan_payload_carries_the_operator_census(monkeypatch, tmp_path):
    """`plan` is the artifact a driver reads before `--apply` — it needs the layer too.

    The `inventory` copy is pinned by `test_the_operator_layer_reaches_the_census`;
    round-2 review measured `cmd_plan`'s `build_plan(points + sources)` reverting to
    leave the suite green, so an operator omission on the `plan` verb would have been
    invisible while `inventory` reported 21,147 rows.
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool", _operator_surface())
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0, rc
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["operators_read"] == 3, payload.get("operators_read")
    assert payload["operators_total_reported"] == 3
    assert payload["census"]["total_rows"] == 4, payload["census"]
    assert payload["census"]["by_class"]["operator:IMPL"]["undecidable"] == 2


def test_a_graph_refusal_raised_as_an_exception_retries_without_the_argument():
    """The OTHER fallback arm: a graph-bound credential that RAISES instead of answering.

    Round-2 review measured this arm deletable with the suite green. Both shapes are
    real — an envelope (`_error_of`) and a raised error — and each has its own guard, so
    each needs its own pin: this one asserts the retry happens WITHOUT `graph` and with a
    note, which is the behaviour a graph-bound venue depends on.
    """
    mod = _tool_module()
    calls: list[dict] = []

    def call(name, args):
        args = dict(args or {})
        calls.append(args)
        if "graph" in args:
            raise RuntimeError(
                "graph 'org_x_g_old' does not exist or is not reachable by this credential")
        return {"result": list(OPERATOR_ROWS)}

    reader = mod.MCPReader(call, "org_x_g_old")
    rows, reported = reader.read_operators({"operators": 3})
    assert len(rows) == 3 and reported == 3, (rows, reported)
    assert len(calls) == 2, calls
    assert "graph" in calls[0] and "graph" not in calls[1], calls
    assert reader.note is not None and "refused" in reader.note, reader.note


def test_an_unreported_count_over_a_non_empty_operator_read_is_accepted_and_noted(
        monkeypatch, tmp_path):
    """The DOWNGRADE arm: rows without a count are still the read's rows, and it says so.

    Round-3 review measured this arm unpinned in both halves — deleting the note left
    every test green, and flipping the zero-row guard to `if reported is None:` (always
    refuse) failed only a message-grep in the at-limit case. The claim is in
    `read_operators`' docstring and in the doc, so it needs a positive pin: a non-empty
    read with no `structure.operators` succeeds, reports `None` for the count, and names
    the lost cross-check in `read_note`.
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool",
                        _operator_surface(operators=OPERATOR_ROWS, reported=None))
    rc = mod.main(["inventory", "--graph", "org_x_g_old", "--no-verify-read",
                   "--out", str(tmp_path / "i.json")])
    assert rc == 0, rc
    payload = json.loads((tmp_path / "i.json").read_text())
    assert payload["operators_read"] == len(OPERATOR_ROWS), payload["operators_read"]
    assert payload["operators_total_reported"] is None, payload
    # ⛔ The MACHINE-READABLE half of the downgrade: `census_measured` speaks for the
    # point/source census and cannot answer "was this layer read whole?" — so with no
    # count to reconcile it, the artifact must say so in a field rather than only in
    # `read_note` prose (round-7 review).
    assert payload["operators_reconciled"] is False, payload
    assert payload["census"]["total_rows"] > len(OPERATOR_ROWS)
    assert "no `operators` count" in (payload["read_note"] or ""), payload["read_note"]
    assert "unreconciled" in (payload["read_note"] or "")


def test_an_operator_row_that_is_not_an_object_is_refused_not_a_traceback(
        monkeypatch, capsys, tmp_path):
    """Shape drift at the ROW level gets the designed refusal, not an AttributeError.

    `_is_row_listing` accepts any list, so a junk element would reach `.get` and raise
    (`rc 1` traceback) instead of `rc 3` — the guard `graphs()` and `read_sources` already
    apply to their own rows (round-3 review).
    """
    mod = _tool_module()
    rows = [*OPERATOR_ROWS, "junk"]
    monkeypatch.setattr(mod, "_live_call_tool",
                        _operator_surface(operators=rows, reported=len(rows)))
    rc = mod.main(["inventory", "--graph", "org_x_g_old", "--no-verify-read",
                   "--out", str(tmp_path / "i.json")])
    assert rc == 3, (rc, capsys.readouterr())
    assert "not an object" in capsys.readouterr().err


def test_inventory_and_plan_answer_the_same_exit_code_for_a_surface_fault(
        monkeypatch, capsys, tmp_path):
    """One failure, one code — for BOTH verbs.

    Round-3 measured `plan` returning 3 for a 429 on the first read while `inventory`
    raised (rc 1) for the identical failure, because only `plan` wrapped every read.
    `CurationRefused` also covers a tool payload that answered with an error, so both
    verbs must report it as the designed refusal (rc 3).
    """
    mod = _tool_module()

    def call(name, args):
        # An MCP ERROR RESULT (the surface's own "this call failed" shape) — the case
        # `normalise_tool_result` turns into `CurationRefused`. A plain `{"error": ...}`
        # dict is a different path (the reader raises `RuntimeError`), and it is not the
        # divergence under test.
        if (args or {}).get("section") == "status":
            return RealCallToolResult({"error": "429 Too Many Requests"}, is_error=True)
        return {"result": []}

    monkeypatch.setattr(mod, "_live_call_tool", call)
    inv = mod.main(["inventory", "--graph", "org_x_g_old", "--no-verify-read",
                    "--out", str(tmp_path / "i.json")])
    assert inv == 3, (inv, capsys.readouterr())
    assert "REFUSED" in capsys.readouterr().err
    plan = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                     "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                     "--out", str(tmp_path / "p.json")])
    assert plan == inv, (plan, inv)


@pytest.mark.parametrize("failing", ["status", "structure", "sources", "graphs", "audit",
                                      "points"])
def test_every_census_read_answers_the_designed_refusal(
        monkeypatch, capsys, tmp_path, failing):
    """EVERY census read, one section at a time — not just the ones a review noticed.

    Round-4 review measured the `audit` read sitting outside `cmd_inventory`'s designed
    refusal handler: an `is_error` payload there raised as rc 1 while `status`,
    `structure`, `sources` and `graphs` answered rc 3. The fix put it inside — but
    round-7 review measured that only `status`, `audit` and the operator read are
    actually PINNED, so moving `structure`, `graphs`, `points` or `sources` back out of
    the `try` left every test green. The handler's whole claim is "one code for one
    failure across every census read", so each read gets its own arm here.

    The failure shape is an MCP ERROR RESULT (`RealCallToolResult(..., is_error=True)`),
    which `normalise_tool_result` turns into `CurationRefused` — the surface's own "this
    call failed" delivery, and the case the two-vocabularies defect was about. A plain
    `{"error": …}` dict is a different path (the reader raises `RuntimeError`, then
    runtime error), and it is not what this test is about.
    """
    mod = _tool_module()
    boom = RealCallToolResult({"error": "429 Too Many Requests"}, is_error=True)

    def call(name, args):
        args = args or {}
        section = args.get("section")
        if failing == "points":
            if name == "tortoise_query":
                return boom
        elif section == failing:
            return boom
        if section == "structure":
            return {"total": 1, "operators": 0}
        if section == "status":
            return {"counts": {"Point": 1, "Source": 0}}
        if section == "sources":
            return {"result": [], "total": 0}
        if section == "graphs":
            return {"result": [{"namespace": "org_x_g_old"}]}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision",
                                "content": "a durable claim"}], "total": 1}
        return {"result": []}

    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["inventory", "--graph", "org_x_g_old", "--no-verify-read",
                   "--out", str(tmp_path / "i.json")])
    assert rc == 3, (failing, rc, capsys.readouterr())
    assert "REFUSED" in capsys.readouterr().err


def test_two_operator_rows_sharing_an_id_do_not_get_a_content_duplicate_reason():
    """An operator is never folded on content, so its reason must not claim a hash fold.

    Round-5 review measured both duplicate limbs asserting a content-hash relation for
    operator rows whose only shared thing was a node ID — `read_operators` refuses the
    id-LESS row but not the id-DUPLICATED one, and `create_point` ids are already
    documented as non-unique here, so the shape is reachable. The plan's row artifact is
    the review surface (`plan --rows`), and a false relation in it is the defect class
    four rounds have been removing.
    """
    row = C.Row(node_id="o9", node_type="operator", kind="IMPL", content="",
                content_hash=None, props={"id": "o9"})
    other = C.Row(node_id="o9", node_type="operator", kind="IMPL", content="b",
                  content_hash="h", props={"id": "o9"})

    mismatch = C.classify_point(row, {}, duplicate_of="o9", keeper_id="o9",
                                content_mismatch=True)
    assert mismatch.disposition == C.UNDECIDABLE
    assert "content hash" not in mismatch.reason, mismatch.reason
    assert "endpoint" in mismatch.reason, mismatch.reason

    # ⛔ EVERY limb here is SYNTHETIC: each duplicate arm carries `row.node_type !=
    # "operator"`, so no operator row reaches one through `build_plan` at all — the
    # operator branch decides it. The calls below are what keeps those clauses honest,
    # one arm each; the `build_plan` case after them is the REAL path (`build_plan` is
    # the only caller that passes `duplicate_of`, and an operator keeper is always
    # `UNDECIDABLE`). Round-6 called the `UNDECIDABLE` arm "the one that actually fires"
    # and round-7 pinned only that; both were wrong in the same direction — the arm that
    # fires is the operator branch, and every duplicate arm here is defence in depth
    # (round-2 review).
    same = C.classify_point(other, {}, duplicate_of=C.UNDECIDABLE, keeper_id="o9")
    assert same.disposition == C.UNDECIDABLE, same
    assert "duplicate" not in same.reason.lower(), same.reason
    assert "content hash" not in same.reason, same.reason

    # …and the `CARRY` arm, which nothing pinned: without its `!= "operator"` clause an
    # operator would inherit a NOT_CARRIED "exact content duplicate of a carried row"
    # reason — a content fold asserted over a LABEL, on a class that is never carried.
    carried = C.classify_point(other, {}, duplicate_of=C.CARRY, keeper_id="o9")
    assert carried.disposition == C.UNDECIDABLE, carried
    assert "duplicate" not in carried.reason.lower(), carried.reason

    plan = C.build_plan([
        C.Row(node_id="o1", node_type="operator", kind="IMPL", content="",
              content_hash=None, props={"id": "o1"}),
        C.Row(node_id="o1", node_type="operator", kind="IMPL", content="",
              content_hash=None, props={"id": "o1"}),
    ], source_graph="g_old", target_graph="g_new")
    assert len(plan.decisions) == 2, plan.decisions
    for d in plan.decisions:
        assert d.disposition == C.UNDECIDABLE, d
        assert "duplicate" not in d.reason.lower(), d.reason
        assert "content hash" not in d.reason, d.reason


def test_an_operator_payload_that_is_not_a_row_listing_is_refused(monkeypatch, capsys,
                                                                 tmp_path):
    """The shape guard is the only thing between a wrong payload and a reported zero.

    Round-6 review mutated `if not _is_row_listing(res):` to `if False:` and all 192
    tests stayed green: with `structure.operators == 0` and a payload of
    `{"unexpected": "shape"}`, removing the guard reports `operators_read: 0`,
    `operators_total_reported: 0` and exit 0 — a census asserting an empty reasoning
    layer over a payload nobody read, which is the silent-zero class this whole change
    exists to close.
    """
    mod = _tool_module()

    def call(name, args):
        args = args or {}
        if name == "tortoise_search":
            return {"unexpected": "shape"}
        if args.get("section") == "structure":
            return {"total": 1, "operators": 0}
        if args.get("section") == "status":
            return {"counts": {"Point": 1, "Source": 0}}
        if args.get("section") == "sources":
            return {"result": [], "total": 0}
        if args.get("section") == "graphs":
            return {"result": [{"namespace": "org_x_g_old"}]}
        if name == "tortoise_query":
            return {"result": [{"id": "p1", "kind": "decision",
                                "content": "a durable claim"}], "total": 1}
        return {"result": []}

    monkeypatch.setattr(mod, "_live_call_tool", call)
    # A `RuntimeError`, deliberately: this reader's shape guard is the same class as
    # `read_sources`', and the doc's rc-1 row is where "the payload was not a listing"
    # belongs (it is the read being unusable, not a designed census refusal).
    with pytest.raises(RuntimeError, match="unrecognised shape"):
        mod.main(["inventory", "--graph", "org_x_g_old", "--no-verify-read",
                  "--out", str(tmp_path / "i.json")])


@pytest.mark.parametrize("failure", ["refused", "raised", "after_read_refused",
                                      "after_read_raised", "refused_inside",
                                      "after_read_unreported"])
def test_a_disarmed_census_failure_carries_the_sections_fallback_note(
        monkeypatch, capsys, tmp_path, failure):
    """BOTH disarmed census-failure arms carry `read_note` — one state, one spelling.

    A section whose explicit `graph` argument is refused falls back to the credential's
    own graph, and `_section` records that on `reader.note`. Dropping the note when a
    LATER read ends the run loses the only record that part of the census came from a
    different graph than `--graph` names — and round-2 review measured that deleting
    `read_note=reader.note` from the `CurationRefused` arm left all 198 tests green, so
    the disclosure was one refactor away from vanishing silently.

    `refused` drives the DESIGNED arm (`CurationRefused`, from an `is_error` payload on
    the source listing); `raised` drives the generic one (`RuntimeError` from an
    unmeasurable points read). Both must answer the same way on this field.

    ⛔ The two `after_read_*` arms are the state that makes `None` mean what it says:
    `graphs()` is the LAST census read, so a failure there leaves the OPERATOR LAYER
    already read and reconciled — and an arm reporting `operators_read: null` on it
    would assert the opposite of what happened. Round-3 review measured exactly that
    (a plain `{"error": …}` on `graphs` reached the generic arm with the layer read, and
    both disarmed arms said "never read"). Both arms are driven separately: the
    `is_error` payload takes the DESIGNED arm and a plain dict the GENERIC one, and they
    are distinct code paths — pinning one left the other green (measured).
    """
    mod = _tool_module()

    def call(name, args):
        args = args or {}
        section = args.get("section")
        if section == "status":
            if "graph" in args:
                return {"error": "unknown graph 'org_x_g_old' is not reachable from this "
                                 "workspace"}
            return {"counts": {"Point": 1, "Source": 0}}
        if section == "structure":
            # `refused_inside` makes the LAYER READ ITSELF refuse: two rows served
            # against three counted, so `read_operators` raises after seeing rows.
            # `after_read_unreported` omits the count entirely, so the completed read
            # cannot be reconciled — the `False` half of the tri-state.
            if failure == "after_read_unreported":
                return {"total": 1}
            return {"total": 1, "operators": len(OPERATOR_ROWS) + (failure == "refused_inside")}
        if section == "sources":
            if failure == "refused":
                return RealCallToolResult({"error": "429 Too Many Requests"}, is_error=True)
            return {"result": [], "total": 0}
        if section == "graphs":
            if failure == "after_read_refused":
                return RealCallToolResult({"error": "429 Too Many Requests"}, is_error=True)
            if failure in ("after_read_raised", "after_read_unreported"):
                return {"error": "500 Internal Server Error"}
            return {"result": [{"namespace": "org_x_g_old"},
                               {"namespace": "org_x_g_new"}]}
        if name == "tortoise_query":
            if failure == "raised":
                return {"result": [], "total": 0}      # the unmeasurable points read
            return {"result": [{"id": "p1", "kind": "decision", "content": "a"}], "total": 1}
        if name == "tortoise_search":
            return {"result": list(OPERATOR_ROWS)}
        return {"result": []}

    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--off", "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0, (rc, capsys.readouterr())
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["census"] is None, payload
    assert "credential's own graph" in (payload["read_note"] or ""), payload["read_note"]
    if failure == "refused_inside":
        # ⛔ The layer read DID see rows and then refused, so no census was produced. The
        # three fields are `null` — the definition is "no completed layer read", NOT
        # "rows were never seen" — and the diagnosis travels in `read_failure`, which is
        # the only place a driver can learn what the refusing read had already seen
        # (round-4 review: the artifact said "never read" while `read_failure` said
        # "read N rows but structure.operators reports N+1").
        assert payload["operators_read"] is None, payload
        assert payload["operators_total_reported"] is None, payload
        assert payload["operators_reconciled"] is None, payload
        # The converse must NOT be read into these fields: `census` is `null` here too,
        # but the pair below (`after_read_*`) reaches the SAME `census: null` with a
        # COMPLETED read. Pinning both halves keeps a later "census is null, so pass
        # `operators=None`" tidy-up from reinstating the round-3 defect (round-5 review).
        assert payload["census"] is None, payload
        assert f"read {len(OPERATOR_ROWS)} rows" in (payload["read_failure"] or ""), (
            payload["read_failure"])
    elif failure == "after_read_unreported":
        # ⛔ The THIRD state, and the one this artifact could still lie about: the layer
        # read COMPLETED (`operators_read` is known) but nothing vouched for the count, so
        # the field is `False` — NOT `null` (that means "no completed read") and NOT
        # `True` (that means the count reconciled). Unpinned, a mutation collapsing it to
        # `null` kept the whole suite green while restoring, one sub-state over, the
        # round-3/5 defect the `after_read_*` arms exist to prevent (round-7 review).
        assert payload["operators_read"] == len(OPERATOR_ROWS), payload
        assert payload["operators_total_reported"] is None, payload
        assert payload["operators_reconciled"] is False, payload
    elif failure.startswith("after_read"):
        # The layer WAS read (and reconciled) before the failing read: the artifact must
        # report the real pair, not `null`.
        assert payload["operators_read"] == len(OPERATOR_ROWS), payload
        assert payload["operators_total_reported"] == len(OPERATOR_ROWS), payload
        assert payload["operators_reconciled"] is True, payload
    else:
        # The census was never read on these arms, so the layer's three fields say so —
        # `None`, never `False`: "not read" and "read but unreconciled" are different.
        assert payload["operators_read"] is None, payload
        assert payload["operators_total_reported"] is None, payload
        assert payload["operators_reconciled"] is None, payload


def test_both_verbs_take_the_count_and_the_layer_in_one_window(monkeypatch, tmp_path):
    """`structure` is read IMMEDIATELY before the layer, in BOTH verbs.

    The refusal's fourth cause is a concurrent create between the count and the read, and its
    message states the window is "two requests". `read_points` alone is 34+ calls on a real
    graph, so reading the count at the top of the verb makes that message false: the window
    would be the whole point walk. `plan` was narrowed and `inventory` was left reading the
    count at the top — one arm fixed, the shared message claiming both (review of
    `0918c1366a`). Pinned on the ORDER of the requests, because that is the property; a pin on
    the code's shape would not survive a refactor that moved the read again.
    """
    mod = _tool_module()
    seen: list[tuple[str, str | None]] = []
    surface = _operator_surface(operators=OPERATOR_ROWS, reported=len(OPERATOR_ROWS))

    def recording(name, args=None):
        seen.append((name, (args or {}).get("section")))
        return surface(name, args)

    for verb in ("inventory", "plan"):
        seen.clear()
        monkeypatch.setattr(mod, "_live_call_tool", recording)
        argv = [verb, "--graph", "org_x_g_old", "--no-verify-read",
                "--out", str(tmp_path / f"{verb}.json")]
        if verb == "plan":
            argv += ["--target", "org_x_g_new", "--ledger", str(tmp_path / "plan.jsonl")]
        assert mod.main(argv) == 0, verb
        count_at = seen.index(("tortoise_graph_overview", "structure"))
        # the layer read is the ONLY `tortoise_search` this run makes
        layer_at = next(i for i, (name, _) in enumerate(seen) if name == "tortoise_search")
        assert layer_at == count_at + 1, (verb, seen)


def test_the_plan_payload_carries_the_layers_three_fields(monkeypatch, tmp_path):
    """The `plan` payload is the artifact a driver reads — its three fields are pinned.

    The `inventory` report and all three disarmed arms are pinned in both directions;
    the `plan` success payload's fields were asserted nowhere, so forcing
    `operators_reconciled` to a constant left the suite green (review of `e1371f7b92`).
    """
    mod = _tool_module()

    def run(reported, out):
        monkeypatch.setattr(mod, "_live_call_tool",
                            _operator_surface(operators=OPERATOR_ROWS, reported=reported))
        rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                       "--no-verify-read", "--ledger", str(tmp_path / f"{out}.jsonl"),
                       "--out", str(tmp_path / f"{out}.json")])
        assert rc == 0, rc
        return json.loads((tmp_path / f"{out}.json").read_text())

    unreported = run(None, "p_unreported")
    assert unreported["operators_read"] == len(OPERATOR_ROWS)
    assert unreported["operators_total_reported"] is None
    assert unreported["operators_reconciled"] is False, unreported["operators_reconciled"]

    reconciled = run(len(OPERATOR_ROWS), "p_reconciled")
    assert reconciled["operators_read"] == len(OPERATOR_ROWS)
    assert reconciled["operators_total_reported"] == len(OPERATOR_ROWS)
    assert reconciled["operators_reconciled"] is True


@pytest.mark.parametrize("verb", ["inventory", "plan"])
def test_a_structure_refusal_is_the_designed_refusal_on_BOTH_verbs(monkeypatch, capsys,
                                                                  tmp_path, verb):
    """`plan` reads `structure` too, so it needs the same arm `inventory` has.

    `test_every_census_read_answers_the_designed_refusal` is parametrized over the
    sections but only ever runs `inventory`, and the window commit added `plan`'s
    `structure` read outside that test's reach — moving it outside the `try` made an
    armed `plan` exit 1 (traceback) instead of the documented 3, with the suite green.
    """
    mod = _tool_module()

    def call(name, args):
        args = args or {}
        if args.get("section") == "structure":
            return RealCallToolResult({"error": "429 Too Many Requests"}, is_error=True)
        return _operator_surface()(name, args)

    monkeypatch.setattr(mod, "_live_call_tool", call)
    if verb == "inventory":
        rc = mod.main(["inventory", "--graph", "org_x_g_old", "--no-verify-read",
                       "--out", str(tmp_path / "i.json")])
    else:
        rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                       "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                       "--out", str(tmp_path / "p.json")])
    assert rc == 3, (verb, rc, capsys.readouterr())
    assert "REFUSED" in capsys.readouterr().err


# ── the Event layer is READABLE, and the census must not omit it (#12378) ──
#
# Measured on live-v4, 2026-11-13, read-only: `tortoise_get_entity(None,
# type="events", limit=5000)` returns **4,670 rows in ONE call**, exactly `counts.Event`
# — the ADVERTISED path (the older `tortoise_get_events` name is retired at the server,
# #3883). The harness recorded the layer as "READABLE but NOT read by this pass" and
# never enumerated it, so the census carried no per-kind resolution for the episodic
# tier and #11469's Target 2 ("every dropped row is named with its reason") could not be
# satisfied for rows it never read.
#
# What the layer does NOT do is move a row: `classify_event` answers `undecidable` on
# both of its arms (an agent-session container, whose home is #10000's open D1 question;
# a domain Event, whose write path has no `events` section). The read buys per-kind
# census resolution, and these cases pin it plus the guards that keep a truncated layer
# out of the census.

#: The real `get_events` row shape: `properties(e)` of a `:Event`, whose text is `name`
#: (persisted because it is absent from `_EVENT_HANDLED`, `projection/entities.py`).
#: A meeting persists `title` instead — that is the declared fallback, and `ev3` pins it.
#: Each row exercises a DIFFERENT text carrier, because the reader declares three and a
#: test that only ever sees two of them pins two (round-2 review measured that deleting
#: `subject` from `_event_text` left the named-for-three test green):
#:   ev-1 `name` (the canonical carrier, persisted because it is absent from
#:        `_EVENT_HANDLED`) · ev-2 `title` ONLY, with no `name` — the real meeting shape,
#:        which is what makes `title` a FALLBACK rather than a coincidence · ev-3
#:        `subject` ONLY, the last declared carrier.
EVENT_ROWS = [
    {"id": "ev-1", "eventKind": "AgentSession", "name": "session 2026-11-13"},
    {"id": "ev-2", "eventKind": "meeting", "title": "kickoff"},
    {"id": "ev-3", "eventKind": "deployment", "subject": "deploy 4f2a"},
]


def _event_surface(events=None, *, reported=_UNSET, points=None, operators=None):
    """A fake surface whose Event layer answers `tortoise_get_entity(type="events")`.

    ``reported=None`` means the STATUS section carries no `Event` count at all (the
    unreported arm) — distinct from the default, which is the layer's own length.
    """
    events = EVENT_ROWS if events is None else events
    reported = len(events) if reported is _UNSET else reported

    def call(name, args):
        args = args or {}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            return {"result": list(events)}
        if name == "tortoise_search":
            return {"result": list(operators or [])}
        if name == "tortoise_query":
            pts = ([{"id": "p1", "kind": "decision", "content": "a durable claim"}]
                   if points is None else points)
            return {"result": list(pts), "total": len(pts)}
        if args.get("section") == "structure":
            return {"total": 1, "operators": len(operators or [])}
        if args.get("section") == "status":
            counts = {"Point": 1, "Source": 0}
            if reported is not None:
                counts["Event"] = reported
            return {"counts": counts}
        return {"result": []}
    return call


def test_the_event_layer_reaches_the_census(monkeypatch, tmp_path, capsys):
    """The episodic layer is 4,670 readable rows in one call, and the census must carry it.

    Omitting it made `total_rows` a claim about a population the pass never read, and
    left the episodic tier as one undifferentiated number in `by_class` — where the
    operator layer was before PR #12380.
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool", _event_surface())
    assert mod.main(["inventory", "--out", str(tmp_path / "inv.json"),
                     "--no-verify-read"]) == 0
    report = json.loads((tmp_path / "inv.json").read_text())
    census = report["census"]
    assert census["total_rows"] == 4, census            # 1 point + 3 events
    assert census["by_class"]["event:AgentSession"]["undecidable"] == 1, census["by_class"]
    assert census["by_class"]["event:meeting"]["undecidable"] == 1, census["by_class"]
    assert census["by_class"]["event:deployment"]["undecidable"] == 1, census["by_class"]
    assert report["events_read"] == 3, report.get("events_read")
    assert report["events_total_reported"] == 3, report.get("events_total_reported")
    assert report["events_reconciled"] is True, report.get("events_reconciled")
    # ⛔ The STDOUT SUMMARY, which nothing pinned (PR review round 1): it named points,
    # sources and operators and omitted the 4,670 Events the run had just classified, so
    # the one line an operator reads without opening the JSON stated 21,147 over a census
    # of 25,817. Reading the JSON in the other tests did not make that line safe — it can
    # drift freely while every artifact assertion stays green.
    assert "3 events" in capsys.readouterr().out, "the summary line must name the layer"


def test_the_event_layer_reaches_the_PLAN_census_too(monkeypatch, tmp_path):
    """The `plan` verb's census population, which nothing pinned (review round 2).

    `cmd_plan` builds its OWN census (`C.build_plan(points + sources + operators +
    events)`), so the two call sites are independent: measured, dropping `+ events`
    there left the whole file green, because every `event:*` assertion above drives
    `inventory`. The payload would then carry `events_read: 3` /
    `events_reconciled: True` beside a census of 1 row with NO `event:*` key — the run
    claiming it read and reconciled a layer its own plan artifact does not contain.

    Both halves are asserted for that reason: the counting half (`total_rows`) and the
    KEY-SHAPE half (`event:<kind>`), because a key regressed to something else still
    leaves `total_rows` right. The doc-text pins guard the PROSE that describes the
    request; this guards the ARTIFACT the plan verb actually emits.
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool", _event_surface())
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new", "--off",
                   "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0
    payload = json.loads((tmp_path / "p.json").read_text())
    census = payload["census"]
    assert census["total_rows"] == 4, census            # 1 point + 3 events
    assert census["by_class"]["event:AgentSession"]["undecidable"] == 1, census["by_class"]
    assert census["by_class"]["event:meeting"]["undecidable"] == 1, census["by_class"]
    assert census["by_class"]["event:deployment"]["undecidable"] == 1, census["by_class"]


def test_an_event_row_that_is_not_an_object_is_refused_not_a_traceback(
        monkeypatch, capsys, tmp_path):
    """Shape drift at the ROW level gets the designed refusal, not an AttributeError.

    `_is_row_listing` accepts any list, so a junk element reaches `.get` and raises (rc 1
    traceback) instead of rc 3 — the guard `read_operators` (and `read_sources`) already
    carry, and the one arm of this change that had NO revert proof: measured, deleting the
    `isinstance` block left the whole suite at 230 passed (PR review round 1).
    """
    mod = _tool_module()
    rows = [*EVENT_ROWS, "junk"]
    monkeypatch.setattr(mod, "_live_call_tool",
                        _event_surface(rows, reported=len(rows)))
    rc = mod.main(["inventory", "--graph", "org_x_g_old", "--no-verify-read",
                   "--out", str(tmp_path / "i.json")])
    assert rc == 3, (rc, capsys.readouterr())
    assert "not an object" in capsys.readouterr().err


def test_the_event_layer_is_no_longer_claimed_unreadable():
    """The surface table must not describe a read layer as one this pass does not read.

    `NOT_ENUMERABLE["event"]` said "READABLE but NOT read by this pass" — a scope
    decision written in the table whose job is to name what *cannot* be reached, and the
    report then presented itself as complete over 4,670 unread rows.
    """
    mod = _tool_module()
    assert "event" not in mod.NOT_ENUMERABLE, mod.NOT_ENUMERABLE
    # ...and the classes this pass still does NOT read stay named.
    for still_named in ("object", "subject"):
        assert still_named in mod.NOT_ENUMERABLE, still_named


def test_an_events_text_is_its_name_with_title_and_subject_fallbacks():
    """An Event's text is `name`; a meeting persists `title`. Content-address on either.

    `Row.stable_key` is content-addressed for this class, so rows sharing an empty text do
    share one keeper. ⚠️ Do not describe that as the operator layer's trap: there the
    defect was a false duplicate CLAIM (one row reported as the other's duplicate, which
    the report then contradicted). Event duplicates are withheld outright — `classify()`
    passes `duplicate_of` only to `classify_point` — so on this class the shared keeper is
    invisible in the report and is not a false claim. What matters here is only that the
    text is `name`-derived rather than empty, which is what the assertions below pin.
    """
    mod = _tool_module()
    reader = mod.MCPReader(_event_surface(), None)
    rows, reported = reader.read_events({"counts": {"Event": 3}})
    assert reported == 3
    # name · title-as-fallback (ev-2 has NO name) · subject-as-fallback (ev-3 has neither).
    assert [r.content for r in rows] == ["session 2026-11-13", "kickoff", "deploy 4f2a"]
    assert [r.kind for r in rows] == ["AgentSession", "meeting", "deployment"]
    assert len({r.stable_key for r in rows}) == 3, [r.stable_key for r in rows]


def test_an_event_row_carrying_eventId_is_read():
    """`_upsert_event` writes `id` == the MERGE key `eventId`, but legacy/by-url rows
    carry the `eventId` spelling — refusing those would drop a readable layer."""
    mod = _tool_module()
    reader = mod.MCPReader(
        _event_surface([{"eventId": "ev-9", "eventKind": "meeting", "name": "x"}]), None)
    rows, _ = reader.read_events({"counts": {"Event": 1}})
    assert [r.node_id for r in rows] == ["ev-9"]


def test_an_event_row_with_no_identity_is_refused(monkeypatch, tmp_path, capsys):
    """Two id-less Events are indistinguishable and would fold onto one keeper."""
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool",
                        _event_surface([{"eventKind": "meeting", "name": "x"}]))
    rc = mod.main(["inventory", "--out", str(tmp_path / "i.json"),
                   "--no-verify-read"])
    assert rc == 3, capsys.readouterr()
    err = capsys.readouterr().err
    assert "eventId" in err and "Event row" in err, err


def test_an_event_layer_above_the_ceiling_is_refused(monkeypatch, tmp_path, capsys):
    """There is no offset walk, so a population at the ceiling has no whole read.

    ⛔ AT, not above: FalkorDB silently caps a non-aggregate read at its server-global
    `RESULTSET_SIZE`, which DEFAULTS to 10 000 — the same number as `EVENT_READ_CEILING`.
    A layer AT the bound is therefore indistinguishable from one the server capped there,
    so accepting it would file a possibly-cut layer as complete (PR-review cycle 5).
    """
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool",
                        _event_surface(events=[], reported=mod.EVENT_READ_CEILING))
    rc = mod.main(["inventory", "--out", str(tmp_path / "i.json"), "--no-verify-read"])
    assert rc == 3, capsys.readouterr()
    err = capsys.readouterr().err
    # ⛔ The BOUND's own words, not just "Event layer": the disagreement arm also says
    # "Event layer" (0 rows vs a reported 10,001), so an assertion on the subject only
    # stayed green with this arm deleted — measured, and it is why the ceiling text is
    # named here.
    assert "RESPONSE BOUND" in err, err


def test_a_truncation_shaped_event_read_is_refused(monkeypatch, tmp_path, capsys):
    """A response that FILLS the limit, with nothing vouching for it, may be truncated.

    `sdk.get_events` is `MATCH (e:Event) … LIMIT $lim` with no `SKIP`, so "a full page"
    and "a truncated layer" are the same bytes. Deleting this arm reds this test.
    """
    mod = _tool_module()
    # ⛔ The fill must reach the REQUEST, and the request IS `EVENT_READ_CEILING` (a
    # response below the bound is provably whole — see read_events). This fixture also
    # models the ENGINE: a server whose `RESULTSET_SIZE` equals the bound returns exactly
    # this many rows for a larger layer, so this is the shape a real truncation has.
    at_limit = [{"id": f"ev-{i}", "eventKind": "meeting", "name": f"m{i}"}
                for i in range(mod.EVENT_READ_CEILING)]
    monkeypatch.setattr(mod, "_live_call_tool",
                        _event_surface(at_limit, reported=None))
    rc = mod.main(["inventory", "--out", str(tmp_path / "i.json"), "--no-verify-read"])
    assert rc == 3, capsys.readouterr()
    assert "truncated" in capsys.readouterr().err


def test_a_zero_row_event_read_with_no_count_refuses(monkeypatch, tmp_path, capsys):
    """"No Events in this graph" and "the layer was not read" are the same bytes."""
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool", _event_surface([], reported=None))
    rc = mod.main(["inventory", "--out", str(tmp_path / "i.json"), "--no-verify-read"])
    assert rc == 3, capsys.readouterr()
    assert "Event" in capsys.readouterr().err


def test_an_event_read_disagreeing_with_the_count_is_refused(monkeypatch, tmp_path,
                                                             capsys):
    """Two surfaces describing one population must agree, or one did not read it."""
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool",
                        _event_surface(EVENT_ROWS, reported=9))
    rc = mod.main(["inventory", "--out", str(tmp_path / "i.json"), "--no-verify-read"])
    assert rc == 3, capsys.readouterr()
    assert "counts.Event" in capsys.readouterr().err


def test_an_unreported_event_count_is_disclosed_not_refused(monkeypatch, tmp_path):
    """A lost cross-check over a NON-EMPTY read is a note, not a lost population."""
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool", _event_surface(EVENT_ROWS, reported=None))
    assert mod.main(["inventory", "--out", str(tmp_path / "i.json"),
                     "--no-verify-read"]) == 0
    report = json.loads((tmp_path / "i.json").read_text())
    assert report["events_read"] == 3
    assert report["events_total_reported"] is None
    assert report["events_reconciled"] is False
    assert "Event" in (report["read_note"] or ""), report.get("read_note")


# ⛔ THE FIELD ON THE OTHER TWO ARTIFACTS, which had no revert proof (PR-review cycle 7).
# `docs/architecture/CURATION-11469.md:196` promises `events_reconciled: false` on EVERY
# artifact and enumerates the tests, but the only unreported-count test above drives
# `inventory`: hardcoding `True` at `curate_graph.py`'s plan payload or at
# `_disarmed_artifact` left the whole file at 238 passed (measured, both). That is the
# unproven-guard class this PR has already been charged for twice — and this field is the
# machine-readable form of "no cross-check existed for this layer", the exact prose->field
# conversion two review cycles produced, so it is the one a lane would "simplify" to True.
def test_the_plan_payload_carries_the_unreconciled_event_field(monkeypatch, tmp_path):
    """The ARMED plan artifact states the lost cross-check, not just the inventory one."""
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool", _event_surface(EVENT_ROWS, reported=None))
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["events_read"] == 3, payload
    assert payload["events_total_reported"] is None, payload
    assert payload["events_reconciled"] is False, payload


def test_the_disarmed_artifact_carries_the_unreconciled_event_field(monkeypatch, tmp_path):
    """...and so does the THIRD call site: a completed read with no count to reconcile.

    ⚠️ `--off` alone does NOT reach `_disarmed_artifact` — a disarmed run that finishes
    normally writes the ARMED plan payload with `run.off: true` (measured: a first revision
    of this test asserted the field on that payload, so it pinned the plan's field a second
    time and stayed GREEN with `_disarmed_artifact` hardcoded to `True`). The helper is
    reached on a REFUSAL or an exception AFTER the Event read, so this drives a `graphs()`
    failure — the same shape `test_a_completed_event_read_survives_a_graphs_failure_on_both
    _disarmed_arms` uses for the `True` half of the tri-state.
    """
    mod = _tool_module()

    def call(name, args):
        args = args or {}
        if args.get("section") == "graphs":
            return RealCallToolResult({"error": "429 Too Many Requests"}, is_error=True)
        return _event_surface(EVENT_ROWS, reported=None)(name, args)

    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new", "--off",
                   "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0
    payload = json.loads((tmp_path / "p.json").read_text())
    # The read COMPLETED (3 rows) and reported NO count, so the tri-state is (3, None) and
    # the field must say the cross-check was lost — not `True`, and not `None` (which would
    # mean "no completed read", the round-2/3 defect this PR exists to close).
    assert payload["events_read"] == 3, payload
    assert payload["events_total_reported"] is None, payload
    assert payload["events_reconciled"] is False, payload


def test_the_event_read_is_routed_to_the_graph():
    """Omitting `graph` reads the DEFAULT graph while every other section came from
    `--graph` — a census mixing two graphs, which is the operator read's own defect."""
    mod = _tool_module()
    calls: list[dict] = []

    def call(name, args):
        calls.append({"name": name, **(args or {})})
        return {"result": list(EVENT_ROWS)}

    reader = mod.MCPReader(call, "org_x_g_old")
    reader.read_events({"counts": {"Event": 3}})
    assert calls and calls[0]["name"] == "tortoise_get_entity", calls
    assert calls[0].get("type") == "events", calls
    assert calls[0].get("graph") == "org_x_g_old", calls
    # ⛔ The REQUEST SHAPE, and specifically `limit`: every fake ignored it, so dropping
    # the argument outright left the suite green — and the live effect is that
    # `tortoise_get_entity` falls back to its own default (`limit=20`), so a 4,670-row
    # layer would return 20 rows and the census would refuse on EVERY run.
    # ⛔ The request is EXACTLY `EVENT_READ_CEILING` — deliberately NOT `reported + 1` and
    # NOT the bound plus one. Sizing it to the count gave one row of headroom, so a single
    # grown Event FILLED it and the read became truncation-shaped (PR-review cycle 4);
    # sizing it to the bound PLUS ONE was worse, because FalkorDB caps a non-aggregate read
    # at `RESULTSET_SIZE` (default 10 000, i.e. this bound), so `bound + 1` can never be
    # filled and the fill refusal became inert (cycle 5).
    assert calls[0].get("limit") == mod.EVENT_READ_CEILING, calls


def test_a_bound_credential_falls_back_and_discloses_on_the_event_read():
    """A graph-BOUND credential cannot be re-targeted: re-read without the argument and
    SAY SO — but only for a refusal about the graph itself."""
    mod = _tool_module()

    def call(name, args):
        if (args or {}).get("graph"):
            return {"error": "This credential is bound to one graph and cannot be "
                             "re-targeted by a `graph` argument"}
        return {"result": list(EVENT_ROWS)}

    reader = mod.MCPReader(call, "org_x_g_old")
    rows, reported = reader.read_events({"counts": {"Event": 3}})
    assert len(rows) == 3 and reported == 3
    assert "Event read" in (reader.note or ""), reader.note


def test_a_non_graph_refusal_on_the_event_read_does_NOT_fall_back():
    """Only a refusal ABOUT the graph may be re-read without it.

    Re-reading a different graph under the requested graph's identity is how a census
    mixes two graphs — the doctrine this module states for `_section`. Measured: deleting
    either `_looks_like_graph_refusal` guard (the RAISED arm and the ENVELOPE arm) left the
    whole suite green, so a 429 or a timeout on the requested graph was silently re-read
    against the credential's own graph and only caught when the counts happened to
    disagree (final review, F3).
    """
    mod = _tool_module()

    # Both arms, both shapes: the raised arm needs something that RAISES, the envelope arm
    # an error payload — a fixture that only exercises one leaves the other unpinned.
    for shape in ("envelope-429", "envelope-deadline", "raises-timeout"):
        def call(name, args, _shape=shape):
            if (args or {}).get("graph"):
                if _shape == "raises-timeout":
                    raise TimeoutError("read timed out after 10s")
                # ⛔ A plain DICT envelope for the envelope arm: a `RealCallToolResult`
                # with `is_error=True` is refused EARLIER (inside `normalise_tool_result`,
                # i.e. on the RAISED arm), so it never reaches the envelope arm and leaves
                # that arm's `_looks_like_graph_refusal` guard unpinned — measured, with a
                # `RealCallToolResult` the guard could be deleted and this test stayed
                # green.
                err = ("429 Too Many Requests" if _shape == "envelope-429"
                       else "Deadline Exceeded")
                return {"error": err}
            return {"result": list(EVENT_ROWS)}

        reader = mod.MCPReader(call, "org_x_g_old")
        try:
            reader.read_events({"counts": {"Event": 3}})
        except Exception as exc:  # the refusal shape is what is under test
            assert "429" in str(exc) or "timed out" in str(exc) or "Deadline" in str(exc), exc
        else:
            raise AssertionError(
                f"{shape}: a non-graph refusal was re-read without the graph argument, so "
                "the Event layer came from a DIFFERENT graph than the one requested")
        assert not reader.note or "read from the credential" not in reader.note, reader.note


def test_an_event_payload_that_is_not_a_row_listing_is_refused():
    """A count-only or renamed-key payload is not "an empty layer" (final review, F4).

    `_is_row_listing` is the arm that separates an unreadable payload from an empty one.
    Measured: deleting it left the suite green, so a `{"total": 3}` answer (a renamed
    `result` key, or a resolution block arriving as the first parseable part) would have
    read as zero Events.
    """
    mod = _tool_module()
    base = _event_surface(reported=3)

    def call(name, args):
        # ⛔ The raw payload, NOT through `_event_surface` — that helper wraps whatever it
        # is given in `{"result": [...]}`, which is exactly the shape this test must not
        # produce (a dict inside `result` is a ROW-list of one non-row, and the failure it
        # then reaches is the row-shape arm, not this one).
        if name == "tortoise_get_entity" and (args or {}).get("type") == "events":
            return {"total": 3}
        return base(name, args)

    # ⚠️ Asserted at the READER, not through `main`: an unrecognisable payload RAISES
    # (a RuntimeError), so it never reaches the census as rows — which is the property
    # this arm exists for. A `main`-level assertion would just be observing the traceback.
    reader = mod.MCPReader(call, None)
    try:
        reader.read_events({"counts": {"Event": 3}})
    except RuntimeError as exc:
        # ⛔ The ARM's own words. `CurationRefused` subclasses RuntimeError, so an assertion
        # on the exception TYPE alone is satisfied by the DISAGREEMENT arm when this guard
        # is deleted (measured green under exactly that mutation) — a false pin.
        assert "unrecognised shape" in str(exc), exc
        assert "counts.Event reports" not in str(exc), exc
    else:
        raise AssertionError(
            "a count-only payload was accepted as the Event layer — an unreadable payload "
            "became an empty or partial layer")


def test_an_error_envelope_on_the_event_read_is_named_as_a_refusal_not_a_shape():
    """The refusal must be ATTRIBUTABLE: an envelope is an error, not a weird payload.

    Measured: deleting the `_error_of` check left the suite green; the 429 the surface
    returned then surfaced as "answered in an unrecognised shape", which sends an operator
    to the wrong place entirely (final review, F5).
    """
    mod = _tool_module()
    base = _event_surface(reported=3)

    def call(name, args):
        if name == "tortoise_get_entity" and (args or {}).get("type") == "events":
            # A plain dict envelope — the shape `_error_of` reads. A `RealCallToolResult`
            # with `is_error=True` is refused EARLIER (by `normalise_tool_result`), so it
            # never reaches this arm and would pin nothing.
            return {"error": "429 Too Many Requests"}
        return base(name, args)

    # Asserted at the READER: the payload-shape arm raises here rather than returning rows.
    reader = mod.MCPReader(call, None)
    try:
        reader.read_events({"counts": {"Event": 3}})
    except RuntimeError as exc:
        # The refusal must be ATTRIBUTABLE. Measured: with the `_error_of` check deleted,
        # the 429 is still refused (fail-closed) but arrives as "answered in an
        # unrecognised shape", which sends an operator to the wrong place entirely.
        assert "was refused" in str(exc) and "429" in str(exc), exc
        assert "unrecognised shape" not in str(exc), exc
    else:
        raise AssertionError("an error envelope was accepted as the Event layer")


def test_a_disarmed_event_read_failure_reports_the_layer_as_unread(monkeypatch, tmp_path):
    """`None` means NO COMPLETED READ — not "this graph has no Events".

    The round-2/3 defect class on the operator layer, one layer over: a disarmed payload
    that DROPS the layer's fields leaves its state unknowable exactly when the run has
    been reduced to reporting it.
    """
    mod = _tool_module()

    def call(name, args):
        args = args or {}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            return RealCallToolResult({"error": "429 Too Many Requests"}, is_error=True)
        return _with_operator_count(_event_surface())(name, args)

    monkeypatch.setattr(mod, "_live_call_tool", call)
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new", "--off",
                   "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["events_read"] is None, payload
    assert payload["events_total_reported"] is None, payload
    assert payload["events_reconciled"] is None, payload
    # The READ that refused is identified by the tri-state (`None`, not 0), and the
    # envelope's own text rides `read_failure` — the same shape the operator layer's
    # refusal takes, because `normalise_tool_result` raises before the layer-specific
    # message is reachable. One state, one spelling, both layers.
    assert "429" in (payload["read_failure"] or ""), payload


def test_a_disarmed_run_after_a_completed_event_read_reports_the_pair(monkeypatch,
                                                                     tmp_path):
    """The OTHER arm of the same tri-state: a completed read travels with its count."""
    mod = _tool_module()
    monkeypatch.setattr(mod, "_live_call_tool", _event_surface())
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new", "--off",
                   "--no-verify-read", "--ledger", str(tmp_path / "l.jsonl"),
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["run"]["off"] is True, payload["run"]
    assert payload["events_read"] == 3, payload
    assert payload["events_total_reported"] == 3, payload
    assert payload["events_reconciled"] is True, payload


def test_an_agent_session_event_gets_the_session_reason_not_the_domain_one():
    """`AgentSession` is the real spelling; the classifier used to test lower-case only.

    `sdk.index_sessions` writes `"eventKind": "AgentSession"` and `_upsert_event` stores
    it verbatim, while `classify_event` compared against `agentSession` case-SENSITIVELY.
    That was unreachable while nothing read the layer; once `read_events` enumerated it,
    every agent-session row fell through to the domain branch and was reported as
    "a domain Event … no `events` write section" — the disposition stayed `undecidable`
    (nothing was carried by mistake), but the per-row REASON, which is the one thing
    reading the layer buys, was false for the whole 4,670-row layer.
    """
    _tool_module()          # the module must import; the classifier is the subject
    packs: dict[tuple[str, str], str] = {}
    for kind in ("AgentSession", "agentSession", "session", ""):
        row = C.Row(node_id="ev-x", node_type="event", kind=kind, content="a session")
        decision = C.classify_event(row, packs)
        assert decision.disposition == C.UNDECIDABLE, (kind, decision)
        assert "agent-session Event" in decision.reason, (kind, decision.reason)
    # ...and a real domain Event keeps the domain reason.
    domain = C.classify_event(
        C.Row(node_id="ev-y", node_type="event", kind="deployment", content="deploy 4f2a"),
        packs)
    assert domain.disposition == C.UNDECIDABLE, domain
    assert "no `events` section" in domain.reason, domain.reason
    # ⛔ A PACK-NAMESPACED event kind is NOT the same decision as a bare one, which is
    # exactly what `curation.Plan.bundle`'s spelling note says now (PR review round 1):
    # the fix case-FOLDS the kind, and case-folding strips no namespace — so the
    # namespaced spelling still falls through to the domain branch. This arm reds if a
    # later reader "corrects" that note by bare-folding the comparison instead.
    for namespaced in ("core:agentSession", "core:AgentSession"):
        ns = C.classify_event(
            C.Row(node_id="ev-z", node_type="event", kind=namespaced, content="a session"),
            packs)
        assert ns.disposition == C.UNDECIDABLE, (namespaced, ns)
        assert "no `events` section" in ns.reason, (namespaced, ns.reason)


def test_the_ledger_disarmed_arm_carries_the_completed_event_layer(monkeypatch, tmp_path):
    """The THIRD disarmed stop point — and the one a two-arm tri-state test would miss.

    `cmd_plan` reaches `_disarmed_artifact` from three call sites (a refused census read,
    an unexpected exception, and a refused/damaged LEDGER). The operator precedent passes
    its layer to all three; a test that only exercises the first two stays green with the
    ledger arm silently dropping `events_*`, which is exactly the round-2/3 defect class
    on the operator layer one layer over.
    """
    mod = _tool_module()
    led = tmp_path / "l.jsonl"
    led.write_text("null\n")          # a damaged ledger line: unreadable keys
    monkeypatch.setattr(mod, "_live_call_tool", _event_surface())
    rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                   "--apply", "--off", "--ledger", str(led), "--no-verify-read",
                   "--out", str(tmp_path / "p.json")])
    assert rc == 0
    payload = json.loads((tmp_path / "p.json").read_text())
    assert payload["census"] is not None and payload["census_measured"] is True, payload
    # The audit of this arm is "the census WAS read; the LEDGER is what failed".
    assert payload["events_read"] == 3, payload
    assert payload["events_total_reported"] == 3, payload
    assert payload["events_reconciled"] is True, payload


def test_the_architecture_doc_no_longer_claims_the_event_layer_is_unread():
    """The doc is the other half of the artifact: a stale row makes the read look optional.

    `docs/architecture/CURATION-11469.md` stated the Event layer as "counted here, NOT
    enumerated … this pass deliberately does not read it (class table; #12378)" and, in
    the class table, "this pass does NOT read it". Once `read_events` lands those rows are
    false — and a false doc is how the next lane re-derives the omission as intended.
    """
    mod = _tool_module()          # noqa: F841  (the module must import; the doc is the subject)
    doc = (_pathlib.Path(__file__).resolve().parent.parent
           / "docs" / "architecture" / "CURATION-11469.md").read_text(encoding="utf-8")
    assert "this pass does NOT read it" not in doc, (
        "the class table still says the Event layer is not read")
    assert "deliberately does not read it" not in doc, (
        "the node-count table still says the omission is deliberate")
    assert "counted here, NOT enumerated" not in doc, (
        "the node-count table still describes the Event layer as unenumerated")
    # ⛔ The THIRD sentence, one paragraph above the row this test was written for: the
    # first version of this pin asserted three phrasings and stayed green over
    # "Events are readable in one call too and are deliberately NOT read here", which is
    # the same false claim in different words (round-2 review). Pin the CLAIM, not a
    # phrase: no sentence in this doc may say the Event layer is not read.
    assert "NOT read here" not in doc, (
        "a paragraph still says the Event layer is not read")
    assert "deliberately NOT read" not in doc, (
        "a paragraph still says the omission is deliberate")
    # ...and the spelling-invariance claim must match the classifier (case-folded since
    # #12378): "compare the EXACT `row.kind`" is now true for `classify_source` only.
    assert "compare the EXACT" not in doc or "classify_source` compares the" in doc, (
        "the doc must attribute the EXACT comparison to classify_source only")
    # ⛔ The POPULATION ARITHMETIC, which the phrase pin could not see (round-3 review):
    # the doc's stated enumerable population must equal the sum of the class table, and
    # reading the Event layer moved it by 4,670. Assert the SUM, not a number copied by
    # hand, so a future layer that is read without updating this line reds here.
    # ⛔ PARSE the class table's row for each class and check the doc's stated population
    # against their sum. A dict of literals with substring checks stayed GREEN when the
    # Event ROW was changed to 4,671 while the population/dispositions lines still said
    # 4,670/25,817 — i.e. it could not see the self-contradiction it was written for
    # (PR-review cycle 2), because every literal it looked for also occurs elsewhere.
    import re as _re

    # ⛔ Parse the POPULATION LINE as a sum of its class addends and check the arithmetic.
    # Adding up the class-table ROWS instead double-counts: the Point row states 19,595,
    # which already includes the 3,727 operators (the row says so). And a dict of literals
    # with substring checks stayed GREEN when the Event ROW was changed to 4,671 while the
    # population line still said 4,670 — it could not see the self-contradiction it was
    # written for, because every literal it looked for also occurs elsewhere.
    flat = _re.sub(r"\s+", " ", doc)
    m = _re.search(
        r"\*\*([\d,]+) points \+ ([\d,]+) sources \+ ([\d,]+) operators \+ ([\d,]+) "
        r"events = ([\d,]+)\*\*", flat)
    assert m, "the doc no longer states the enumerable population as a sum of its classes"
    pts, srcs, ops, evts, total = (int(g.replace(",", "")) for g in m.groups())
    assert pts + srcs + ops + evts == total, (pts, srcs, ops, evts, total)
    # The BY-NODE-TYPE Event row must agree with the population's events addend — the
    # exact check that catches the round-3 defect (a row updated without the population
    # line). ⚠️ This is doc:49, NOT the class table: its `**Event**` bold-count shape is
    # unique to the node-type table.
    row = _re.search(r"^\| \*\*Event\*\* \| \*\*([\d,]+)\*\*", doc, _re.M)
    assert row, "the by-node-type table has no Event row"
    assert int(row.group(1).replace(",", "")) == evts, (
        f"the Event row states {row.group(1)} but the population line counts {evts:,}")
    # ⛔ ...and the CLASS table's own Event row, which the assertion above does NOT reach
    # (review cycle 6 measured it: the regex above matches the by-node-type row, so
    # setting this one to 4,671 left the file at 236 passed). Its shape is different —
    # a backticked kind and an unbolded count — so it needs its own parse. Without this,
    # the class table can contradict its own population and dispositions lines silently,
    # which is precisely the self-contradiction the check exists to catch.
    cls = _re.search(r"^\| `event` \(session container\) \| ([\d,]+) \(", doc, _re.M)
    assert cls, "the class table has no `event` (session container) row"
    assert int(cls.group(1).replace(",", "")) == evts, (
        f"the class-table Event row states {cls.group(1)} but the population line counts "
        f"{evts:,}")
    # ...and the dispositions line must add up to the same population.
    assert f"127 carry · 5 not-carried · {total - 132:,} undecidable" in flat, (
        "the dispositions line does not add up to the population")
    # ⛔ The PLAN's wiring row must not prescribe the clamp the code was fixed to remove:
    # it said `limit=EVENT_READ_CEILING` while the code then sent `reported + 1`, i.e. the
    # plan documented the fail-open (PR-review cycle 2), and nothing pinned it.
    plan = (_pathlib.Path(__file__).resolve().parent.parent / "docs" / "plans"
            / "2026-11-13-issue-12378-event-layer-read.md").read_text(encoding="utf-8")
    assert "limit=EVENT_READ_CEILING + 1" not in plan, (
        "the plan still prescribes the request one row ABOVE the bound, which can never be "
        "filled and makes the fill refusal inert (PR-review cycle 5)")
    # ⛔ Pin the WIRING ROW, not the whole document: the plan legitimately RECORDS the two
    # superseded sizings as history (a reader needs to know why the request is no longer
    # sized to the count), so a whole-document "absent" assertion reds on the very
    # paragraph that documents the fix. What must never come back is the row a lane would
    # re-implement from.
    wiring = [ln for ln in plan.splitlines() if ln.startswith("| `tools/curate_graph.py::"
                                                             "MCPReader._get_entities`")]
    assert wiring, "the plan has no MCPReader._get_entities wiring row"
    assert "limit` = `EVENT_READ_CEILING`" in wiring[0], wiring[0]
    assert "reported + 1" not in wiring[0] and "min(" not in wiring[0], wiring[0]
    assert "EVENT_READ_CEILING + 1" not in wiring[0], wiring[0]
    # The plan must not describe the re-count as growth-only either: since the symmetric
    # fix, a SHRINK with an agreeing fresh count is accepted, and the growth-only wording
    # states the opposite of the code for the mirror direction (PR-review cycle 3).
    assert "growth direction is re-counted" not in plan, (
        "the plan still describes the re-count as growth-only")
    # The plan must describe the request the code actually sends, and must not resurrect
    # EITHER superseded sizing: the one-row headroom (cycle 4) or the bound-plus-one that
    # the engine's own `RESULTSET_SIZE` cap turns into an inert guard (cycle 5).
    assert "**sized to exactly `EVENT_READ_CEILING`**" in plan, (
        "the plan does not describe the request the code sends")
    assert "RESULTSET_SIZE" in plan, (
        "the plan does not name the engine cap that makes `bound + 1` unfillable")
    # ⚠️ PRESENT TENSE, not the broad substring (PR-review cycle 7): the plan legitimately
    # RECORDS the superseded sizing ("the first draft WAS sized to `counts.Event + 1`"), and
    # a bare `sized to ...` test false-reds on exactly that note.
    #
    # ⛔ BUT THE "`is sized to`" FORM WAS A NO-OP GATE, AND THAT IS WORSE THAN BRITTLE
    # (review cycle 8, measured). The withdrawn sentence this pin exists for reached the
    # doc EMHPHASIS-WRAPPED and mid-sentence — "The read is therefore\n**sized to
    # `counts.Event + 1`**, which is…" — so neither `is sized to` nor any `is `-prefixed
    # form can match it, and re-inserting it VERBATIM from
    # `git show 3770e1e59a:docs/plans/…:118-123` left this test GREEN (`1 passed`).
    # A test that cannot fail on the exact defect it was written for is not a pin.
    #
    # The check is therefore per-SENTENCE on emphasis-normalised text, EXCLUDING sentences
    # that explicitly mark the sizing as history — so recorded history stays writable and a
    # present-tense prescription reds in any emphasis or word order.
    plan_norm = _re.sub(r"\*+", "", _re.sub(r"\s+", " ", plan))
    # ⛔ THE MARKER MUST BE TIED TO THE SIZING, NOT TO THE SENTENCE'S SUBJECT (review
    # cycle 10, measured). Leaving the loose markers in (`cycle N`, `original`, `first
    # draft`, `earlier`) exempted a clause that PRESCRIBES as soon as it mentioned one:
    # "Cycle 3 confirmed the read is sized to `counts.Event + 1` today." stayed GREEN,
    # because the clause itself carried `Cycle 3` — no comma to split on, so the
    # per-clause rule could not separate them. Only a marker attached to the SIZING makes
    # a statement historical rather than prescriptive; a bare `cycle N` or `original`
    # says where the number came from, not that it was withdrawn.
    _HISTORY = _re.compile(
        r"\b(?:was|were|had been)\s+sized\b|withdrawn|superseded|no longer|used to", _re.I)

    def _prescribes(text: str) -> list[str]:
        """Clauses that state the withdrawn sizing as if it were current.

        ⚠️ THE UNIT IS A CLAUSE, NOT A SENTENCE (PR-review cycle 9, measured). Excluding
        the whole SENTENCE when any history word appears anywhere in it let a live
        prescription ride along in the same sentence: "Earlier revisions aside, the read is
        no longer sized to the bound, it is **sized to `counts.Event + 1`** today" stayed
        GREEN — a single "no longer" excused a sentence that prescribes the defect. Commas
        split too, so history and a live claim cannot share a unit.
        """
        return [
            c.strip() for c in _re.split(r"(?<=[.;:,])\s+", text)
            if "sized to `counts.Event + 1`" in c and not _HISTORY.search(c)
        ]

    # ⛔ THE DETECTOR IS SELF-TESTED, because a pin that cannot fail on its own defect is
    # the class this block has produced twice already. Each case below is a MEASURED
    # mutation of the plan doc: the first is the verbatim withdrawn sentence (emphasis-
    # wrapped, mid-sentence — the form the previous `is sized to` pin could not see), the
    # second is the clause-splitting bypass, the last two must stay writable as history.
    assert _prescribes(
        "The read is therefore sized to `counts.Event + 1`, which is the smallest "
        "request that PROVES non-truncation."), (
        "the plan-doc detector cannot see the withdrawn sentence it exists for")
    assert _prescribes(
        "Earlier revisions aside, the read is no longer sized to the bound, it is sized "
        "to `counts.Event + 1` today."), (
        "a history word in the same sentence still exempts a live prescription")
    assert not _prescribes(
        "A first draft said the request was sized to `counts.Event + 1`."), (
        "recorded history must stay writable")
    assert not _prescribes(
        "The superseded row sized the request to `counts.Event + 1`."), (
        "recorded history must stay writable")
    # ⛔ ...and the other direction of the same trap: a history WORD is not a history
    # STATEMENT. `Cycle 3` says where the number came from; it does not withdraw it.
    # Measured GREEN before this case was added (review cycle 10).
    assert _prescribes(
        "Cycle 3 confirmed the read is sized to `counts.Event + 1` today."), (
        "a bare history word in the same clause still exempts a live prescription")
    assert _prescribes(
        "As earlier, the read is sized to `counts.Event + 1`."), (
        "a bare history word in the same clause still exempts a live prescription")
    prescribing = _prescribes(plan_norm)
    assert not prescribing, (
        "the plan prescribes sizing the request to the count (one row of headroom), which "
        f"the code does not send; offending clause(s): {prescribing[:2]}")
    # ⛔ ...and the CODE's own text, which nothing pinned (PR-review cycle 5). The plan pin
    # above guards the plan, but `tools/curate_graph.py` carried the SAME superseded
    # prescription in TWO places — the `read_events` docstring bullet and the
    # `EVENT_READ_CEILING` constant's comment — while the code then sent
    # `EVENT_READ_CEILING + 1` (`curate_graph.py:limit_sent`, cycle 3; it sends the bound
    # itself since cycle 5). It survived four review
    # cycles and one peer fix for exactly that reason: the phrase was pinned in the plan
    # only. A maintainer reconciling code to that text deletes the bound-sized request and
    # restores the one-row-headroom truncation window, so the wrong direction is the
    # DANGEROUS one and the absence is asserted on the prescribing phrasing, not on the
    # digits (the corrective comments legitimately quote the old sizing as history).
    #
    # ⚠️ The `#:` markers are STRIPPED before matching, and that is load-bearing: the
    # constant's comment wraps, so the raw text carries `sizes each request to\n#:
    # `counts.Event + 1`` and a phrase assertion on the un-stripped text matches NOTHING —
    # a first revision of this pin passed over the exact defect it was written for
    # (measured: reverted the comment, the pin stayed green).
    source_text = (_pathlib.Path(__file__).resolve().parent.parent
                   / "tools" / "curate_graph.py").read_text(encoding="utf-8")
    source_flat = _re.sub(r"\s*#:\s*", " ", _re.sub(r"\s+", " ", source_text))
    # ⚠️ Each assertion below is SCOPED, for three measured reasons (PR-review cycle 6):
    # a bare whole-file occurrence count cannot attribute anything — `limit_sent =
    # EVENT_READ_CEILING + 1` supplies one for free, so `count(...) >= 2` stayed GREEN with
    # the constant's comment reverted; and a whole-file absence test FALSE-REDS on a
    # legitimate historical sentence ("the request WAS sized to `counts.Event + 1` before
    # cycle 4" is exactly the kind of note this file keeps and must keep).
    #
    # The two ABSENCE assertions are therefore pinned to the PRESENT-TENSE prescription —
    # `is sized to` / `sizes each request to`, which a past-tense note cannot contain — and
    # they are checked against text with `#:` markers stripped, because the constant's
    # comment WRAPS and the phrase would otherwise straddle a marker and match nothing
    # (measured: an earlier revision of this pin passed over the very defect it was written
    # for). ⚠️ Plain-`#` comments are deliberately NOT stripped: the corrective comments at
    # `read_events`'s call site legitimately quote the old sizing to explain why it was
    # removed, and stripping them would red on that history.
    #
    # The two PRESENCE assertions are the ones that catch a revert ATTRIBUTABLY — each names
    # its own site's sentence, so no historical mention can satisfy them and neither can the
    # code line.
    assert "is sized to `counts.Event + 1`" not in source_flat, (
        "curate_graph.py still prescribes sizing the Event request to the count; the code "
        "sizes it to exactly EVENT_READ_CEILING")
    assert "sizes each request to `counts.Event + 1`" not in source_flat, (
        "the EVENT_READ_CEILING comment still prescribes sizing the request to the count")
    # ⛔ THE CODE'S OWN VALUE, which nothing pinned until cycle 7 — the assertions above are
    # all about PROSE. A meaning-preserving rewording of either sentence redded the previous
    # pair (measured: "So the read must size its request TO the bound, not above it" →
    # "The read therefore requests exactly this bound and never more" left 1 failed), which
    # is the cost that gets a pin deleted rather than honoured. The value the code assigns is
    # mechanical — no rewording can move it — and it is the thing every sentence above is
    # trying to describe, so it is asserted directly, in BOTH directions: the shipped
    # assignment present, and the superseded bound-plus-one absent.
    # ⚠️ The presence side pins the ASSIGNMENT, not the line's termination (PR-review cycle
    # 8): `"limit_sent = EVENT_READ_CEILING\n"` redded a MEANING-PRESERVING edit — appending
    # `# the declared response bound` to that line — while its own message accused the code
    # of not sizing the request to the bound (measured: 1 failed). That is the same
    # brittleness this block removed one assertion earlier, and it is the shape that gets a
    # pin deleted rather than honoured. The regex allows a trailing comment and nothing else.
    assert _re.search(r"^\s*limit_sent = EVENT_READ_CEILING\s*(?:#.*)?$", source_text,
                      _re.M), (
        "curate_graph.py does not size the Event request to the bound")
    assert "limit_sent = EVENT_READ_CEILING + 1" not in source_text, (
        "curate_graph.py still sizes the Event request one ABOVE the bound, which the "
        "engine's RESULTSET_SIZE cap makes unfillable and therefore leaves the fill "
        "refusal inert")
    # ...and both must say WHY, because the reason is what a future lane edits away: the
    # engine caps a non-aggregate read at `RESULTSET_SIZE`, DEFAULT 10 000, i.e. AT this
    # bound, so `bound + 1` can never be filled and the fill refusal becomes inert.
    assert "RESULTSET_SIZE" in source_flat, (
        "curate_graph.py never names the engine cap that sizes this read's request")

    # ...and the row records the change rather than dropping the subject.
    assert "enumerated** since #12378" in doc or "**enumerated** — agent-session" in doc, (
        "the Event row must say it IS enumerated now, and cite #12378")
    # ⛔ The GUARANTEE TABLE, which none of the phrase pins could see (PR review round 1):
    # "What the harness guarantees (and how each is proven)" carried a row for the
    # reasoning layer and NONE for the episodic one, so it presented itself as the
    # complete guarantee set over a census that had just grown by 4,670 rows. The row is
    # pinned by its own name plus the test the mutation proof leans on, so a revert of
    # either reds here rather than drifting back to a guarantees section that is silent
    # about a layer the census reads.
    assert "the episodic layer is read, not assumed" in doc, (
        "the guarantee table has no row for the Event layer the census now reads")
    assert "test_an_event_row_that_is_not_an_object_is_refused_not_a_traceback" in doc, (
        "the Event guarantee row must name the row-shape guard's test")
    assert "EVENT_READ_CEILING" in doc, (
        "the Event guarantee row must name the bound it refuses above")
    # ⛔ The DOC INDEX (PR review cycle 2): `docs/00_index.md` is the routing entry
    # `AGENTS.md` sends every lane to, and it carried the PRE-Event number set
    # (21,147 rows / 21,015 undecidable) while this change moved the census to 25,817 /
    # 25,685 — the exact under-statement #12378 exists to stop, in the index rather than
    # in the doc the phrase pins cover. The operator layer's own PR updated this line
    # (`a6948020c6`), so the precedent makes the omission drift.
    # Pin the CURRENT claim, not the absence of the digits: the index may keep a withdrawn
    # number as history (it does for 17,420), so a bare `"21,147" not in index` would
    # forbid a correct footnote while still passing if the current figure regressed.
    index = (_pathlib.Path(__file__).resolve().parent.parent
             / "docs" / "00_index.md").read_text(encoding="utf-8")
    assert "over **21,147** enumerable rows" not in index, (
        "docs/00_index.md still states the pre-Event-layer population as CURRENT")
    assert "and 21,015 stay undecidable" not in index, (
        "docs/00_index.md still states the pre-Event-layer undecidable count as CURRENT")
    assert "over **25,817** enumerable rows" in index, (
        "docs/00_index.md must state the population the Event read moved the census to")
    assert "25,685 stay undecidable" in index, (
        "docs/00_index.md must state the undecidable count the Event read moved")


def test_an_event_with_no_text_is_counted_on_the_report(monkeypatch, tmp_path):
    """An Event with no name/title/subject has an empty text, and the report SAYS so.

    The pass keys Events by content, so those rows share one `stable_key`. What does NOT
    happen is a duplicate CLAIM: `classify_event` takes no `duplicate_of`/`keeper_id` and
    every Event arm is `undecidable`, so the row count and the per-row reasons are
    unaffected — an earlier draft of the `read_note` asserted a fold outcome that never
    reaches an Event decision (round-2 review), which is why the counter is pinned here
    and the wording says only what is true.
    """
    mod = _tool_module()
    surface = _event_surface([
        {"id": "ev-1", "eventKind": "deployment", "subject": "has text"},
        {"id": "ev-2", "eventKind": "deployment", "subject": ""},
    ])
    monkeypatch.setattr(mod, "_live_call_tool", surface)
    assert mod.main(["inventory", "--out", str(tmp_path / "i.json"),
                     "--no-verify-read"]) == 0
    report = json.loads((tmp_path / "i.json").read_text())
    note = report["read_note"] or ""
    assert "1 of 2 Event rows carry no name/title/subject" in note, note
    # The disclosure must not claim a duplicate fold, and the count must be intact.
    assert "content duplicates" not in note, note
    assert report["census"]["total_rows"] == 3, report["census"]      # 1 point + 2 events
    assert report["census"]["by_class"]["event:deployment"]["undecidable"] == 2
    assert report["events_read"] == 2, report


def test_a_layer_that_grew_past_the_bound_is_not_accepted_as_complete():
    """A layer that grew past the bound must not be vouched for by the count.

    `limit_sent` used to be `min(reported + 1, EVENT_READ_CEILING)`, which collapses to
    `reported` exactly when `counts.Event == EVENT_READ_CEILING`: a layer that had grown to
    10,050 then came back as 10,000 rows == the count, passed the reconciliation, and was
    filed `events_reconciled: True` with 50 rows unread. Measured by the PR reviewer.
    `reported + 1` was the next wrong answer (ONE row of headroom: a single Event created
    between the count and the read fills the request), and `EVENT_READ_CEILING + 1` was the
    third — the engine caps a non-aggregate read at `RESULTSET_SIZE` (default 10,000, i.e.
    the bound), so a request one ABOVE the bound can never be filled and this very layer
    came back as 10,000 rows == the count and was accepted with 50 unread. The shipped
    request is exactly `EVENT_READ_CEILING`: the fake below models real LIMIT semantics, so
    10,000 rows fill the request and refuse.
    """
    mod = _tool_module()
    ceiling = mod.EVENT_READ_CEILING
    # The count sits BELOW the bound (so the bound refusal does not pre-empt the read) and
    # the true layer sits ABOVE it: exactly the growth the cap makes invisible.
    true_layer = [{"id": f"ev-{i}", "eventKind": "meeting", "name": f"m{i}"}
                  for i in range(ceiling + 50)]
    stale = ceiling - 1
    sent: list[int] = []

    def call(name, args):
        args = args or {}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            sent.append(args["limit"])
            # LIMIT semantics AND the server's cap, which equals the request here: the
            # response tops out at `limit` however large the layer really is.
            return {"result": true_layer[:args["limit"]]}
        if args.get("section") == "status":
            return {"counts": {"Event": stale}}            # the stale count, both reads
        return {"result": []}

    reader = mod.MCPReader(call, None)
    with pytest.raises(C.CurationRefused, match=r"counts\.Event"):
        reader.read_events({"counts": {"Event": stale}})
    # The request is the bound itself: a capped response EQUALS it and refuses, and a
    # response below it was not capped. A request of `bound + 1` could never be filled.
    assert sent == [ceiling], sent


def test_a_layer_that_grew_with_an_agreeing_recount_is_accepted_not_refused():
    """The GROWTH direction is a correct read, and this tenant grows while sessions index.

    `counts.Event` is fed by `index_sessions`, so the two-request window can close on a
    layer one row larger than the count. The read re-counts ONCE and accepts when the fresh
    count agrees with the rows; a stale count must not present as a broken layer.
    """
    mod = _tool_module()
    rows = [{"id": "ev-1", "eventKind": "meeting", "name": "a"},
            {"id": "ev-2", "eventKind": "meeting", "name": "b"},
            {"id": "ev-3", "eventKind": "meeting", "name": "c"},
            {"id": "ev-4", "eventKind": "meeting", "name": "d"}]
    counts_seen: list[int] = []

    def call(name, args):
        args = args or {}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            return {"result": rows[:args["limit"]]}
        if args.get("section") == "status":
            counts_seen.append(1)
            # The caller holds the STALE count (3); this re-read sees the newer 4.
            return {"counts": {"Event": 4}}
        return {"result": []}

    reader = mod.MCPReader(call, None)
    out, reported = reader.read_events({"counts": {"Event": 3}})
    assert len(out) == 4 and reported == 4, (len(out), reported)
    assert "CHANGED" in (reader.note or ""), reader.note


def test_a_completed_event_read_survives_a_graphs_failure_on_both_disarmed_arms(
        monkeypatch, tmp_path):
    """Two of the three disarmed call sites were unpinned, and both can drop a READ layer.

    `graphs()` is read AFTER the Event read, so a failure there leaves the layer read — and
    an arm that dropped `events=` on the way out reported `events_read: None` ("no completed
    read") over a read that HAD completed. Measured: deleting `events=episodic` from either
    the `CurationRefused` arm or the `Exception` arm left the whole suite green.
    """
    mod = _tool_module()

    # ⛔ Two arms, two SHAPES: the `CurationRefused` arm takes an error ENVELOPE, and the
    # `Exception` arm needs something that RAISES — a plain dict here does not reach it
    # (`graphs()` ignores unrecognised rows), so the test would pass while never entering
    # the arm it names. Measured: with a dict, dropping `events=` from the `Exception` arm
    # still left the test green.
    for shape in ("envelope", "raises"):
        def call(name, args, _shape=shape):
            args = args or {}
            if args.get("section") == "graphs":
                if _shape == "envelope":
                    return RealCallToolResult({"error": "429 Too Many Requests"},
                                              is_error=True)
                raise TimeoutError("graphs read timed out after 10s")
            return _event_surface()(name, args)

        monkeypatch.setattr(mod, "_live_call_tool", call)
        out = tmp_path / f"p-{shape}.json"
        rc = mod.main(["plan", "--graph", "org_x_g_old", "--target", "org_x_g_new",
                       "--off", "--no-verify-read",
                       "--ledger", str(tmp_path / f"l-{shape}.jsonl"),
                       "--out", str(out)])
        assert rc == 0, (shape, rc)
        payload = json.loads(out.read_text())
        assert payload["run"]["off"] is True, (shape, payload["run"])
        # The layer read COMPLETED before `graphs()` failed, so it travels as (n, m):
        # `None` here would be the round-2/3 defect this PR exists to close.
        assert payload["events_read"] == 3, (shape, payload.get("events_read"))
        assert payload["events_total_reported"] == 3, (shape, payload)
        assert payload["events_reconciled"] is True, (shape, payload)


def test_a_layer_that_shrank_with_an_agreeing_recount_is_accepted_not_refused():
    """The MIRROR of the growth case: a delete between count and read is a correct read.

    The re-count was applied only when `read > reported`, so a `:Event` deleted by the
    hosted entity-delete path in the two-request window made a COMPLETE read refuse —
    the same false block in the other direction (PR-review cycle 2). The request is
    exactly `EVENT_READ_CEILING`, so a response STRICTLY BELOW it was not capped and is the
    exact layer in either direction, and a fresh count that equals the rows confirms the
    disagreement was a stale count — while a response that FILLS the request refuses even
    when the fresh count agrees (see the reported-arm fill arm), because `bound + 1` did
    not survive cycle 5: a request one above the bound can never be filled at the engine's
    cap, which made that refusal inert.
    """
    mod = _tool_module()
    rows = [{"id": "ev-1", "eventKind": "meeting", "name": "a"},
            {"id": "ev-2", "eventKind": "meeting", "name": "b"}]

    def call(name, args):
        args = args or {}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            return {"result": rows[:args["limit"]]}
        if args.get("section") == "status":
            return {"counts": {"Event": 2}}       # the newer, lower count
        return {"result": []}

    reader = mod.MCPReader(call, None)
    out, reported = reader.read_events({"counts": {"Event": 5}})   # stale, higher count
    assert len(out) == 2 and reported == 2, (len(out), reported)
    assert "CHANGED" in (reader.note or ""), reader.note


def test_a_response_that_FILLED_its_request_refuses_even_when_a_recount_agrees():
    """An agreeing re-count does not prove the rows are whole when the response was FULL.

    The dangerous shape is a COMPOUND race: the layer grew past the request (so the
    response was TRUNCATED), then shrank back to exactly the requested size before the
    re-read — the equality holds, and accepting files `events_reconciled: True` over rows
    that are partly dead and partly missing. Measured by the PR reviewer (4 live rows
    unread, 4 deleted rows reported, accepted). A response that fills its request is the
    only truncation-capable accept, so it must refuse instead of trusting the re-count.
    """
    mod = _tool_module()
    def call(name, args):
        args = args or {}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            # Fill the REQUEST exactly: this is the truncation-capable shape.
            return {"result": [{"id": f"ev-{i}", "eventKind": "meeting", "name": f"e{i}"}
                               for i in range(args["limit"])]}
        if args.get("section") == "status":
            # The re-count AGREES with the rows (the grow-then-shrink-back race), so the
            # disagreement arm resolves in favour of the read and only the FILL arm is
            # left to refuse it.
            return {"counts": {"Event": mod.EVENT_READ_CEILING}}
        return {"result": []}

    reader = mod.MCPReader(call, None)
    try:
        reader.read_events({"counts": {"Event": 3}})     # stale count: the re-count decides
    except mod.C.CurationRefused as exc:
        msg = str(exc)
        assert "filling the" in msg, msg
        assert "may also have been CUT" in msg, msg
    else:
        raise AssertionError(
            "a full request + an agreeing re-count was ACCEPTED: the compound race "
            "(grow past the request, shrink back before the re-read) files "
            "events_reconciled=True over unread rows")


def test_a_server_capped_response_disagreeing_with_the_count_refuses():
    """The ENGINE'S cap, modelled: a non-aggregate read returns at most `RESULTSET_SIZE`.

    FalkorDB truncates any non-aggregate read at its server-global `RESULTSET_SIZE`
    (**default 10 000** — measured on docker 4.20.4, #4515/#4233/#5062) with no error and
    no marker, so `MATCH (e:Event) … LIMIT $lim` cannot return more than the cap however
    large the request is. The fake below enforces exactly that, which the earlier fakes did
    not: they returned `args["limit"]` rows, an input the real server cannot produce, so
    the "reverting the guard reds this test" property held only against impossible data.

    The shape under test is the one the cap makes possible and the request geometry cannot
    see: a layer LARGER than the cap comes back as exactly `cap` rows. Here the count says
    9,999 and the truncated response says 10,000 — a disagreement the re-count resolves
    against the read, so the census refuses instead of filing a cut layer as complete.
    """
    mod = _tool_module()
    cap = mod.EVENT_READ_CEILING          # the engine's default RESULTSET_SIZE
    true_layer = [{"id": f"ev-{i}", "eventKind": "meeting", "name": f"m{i}"}
                  for i in range(cap + 500)]

    def call(name, args):
        args = args or {}
        if name == "tortoise_get_entity" and args.get("type") == "events":
            # ⛔ LIMIT semantics AND the server's silent cap: never more than `cap` rows,
            # whatever `limit` was requested.
            return {"result": true_layer[:min(args["limit"], cap)]}
        if args.get("section") == "status":
            return {"counts": {"Event": len(true_layer)}}   # the TRUE population
        return {"result": []}

    reader = mod.MCPReader(call, None)
    with pytest.raises(C.CurationRefused, match=r"counts\.Event"):
        reader.read_events({"counts": {"Event": cap - 1}})   # the stale, pre-growth count


def test_a_layer_at_the_bound_refuses_because_the_server_caps_there():
    """"At the bound" and "capped at the bound" are the same bytes, so it refuses.

    This is the half an `EVENT_READ_CEILING + 1` request could not see: with the cap equal
    to the bound, a request one row above it is never filled, so a 10,000-row layer was
    accepted as whole even after it had grown to 10,001. The refusal is a FALSE BLOCK at
    exactly 10,000 rows and that is the correct trade: an unverifiable layer is refused,
    never filed as complete.
    """
    mod = _tool_module()
    reader = mod.MCPReader(_event_surface([], reported=mod.EVENT_READ_CEILING), None)
    with pytest.raises(C.CurationRefused, match=r"at or above"):
        reader.read_events({"counts": {"Event": mod.EVENT_READ_CEILING}})
