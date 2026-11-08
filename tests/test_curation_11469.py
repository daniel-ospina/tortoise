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

import json

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
    """
    plan = C.build_plan(
        [row("a", content="", content_hash="H"),
         row("b", content="A durable claim", content_hash="H")],
        source_graph="old", target_graph="new", packs={},
    )
    by_id = {d.node_id: d for d in plan.decisions}
    assert by_id["a"].disposition == C.NOT_CARRIED          # empty content
    assert by_id["b"].disposition == C.NOT_CARRIED          # follows the keeper, not its own rule
    assert "a" in by_id["b"].reason


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


def test_a_dry_run_with_an_unproven_identity_plans_instead_of_refusing():
    """The guard is strict where a WRITE can happen; a plan-only run reports."""
    plan = C.build_plan([row("a", content="Let me look.")], source_graph="live-v4",
                        target_graph="new", packs={})
    res = C.run_plan(plan, writer=CountingWriter(), ledger=C.Ledger("/tmp/nonexistent-11469", target="new"),
                     apply=False, graph_rows=[{"namespace": "org_x_g_deadbeefdeadbeef"}])
    assert res.identity and "cannot resolve" in res.identity
    assert res.written == 0


def test_a_ledger_for_another_target_is_refused():
    plan = C.build_plan([row("a", content="Let me look.")], source_graph="old",
                        target_graph="new", packs={})
    with pytest.raises(C.CurationRefused):
        C.run_plan(plan, writer=CountingWriter(), ledger=C.Ledger("/tmp/nonexistent-11469b", target="A"),
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
    assert res.refused and "ERR_QUOTA" in res.refused
    assert len(led) == 1, "only the accepted batch is recorded"


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
    """Round-5 blocking finding: the bundle carried Sources but the writer only
    ever sent `points`, so a run reported success while writing none of them."""
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
