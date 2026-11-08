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

    def write_points(self, target_graph, points):
        self.calls.append((target_graph, list(points)))
        written = []
        for p in points:
            self.stored.setdefault(p["migrationKey"], p)
            written.append(p["migrationKey"])
        return {"written": written}


@pytest.fixture()
def ledger(tmp_path):
    return C.Ledger(tmp_path / "ledger.jsonl")


# ── idempotency ──────────────────────────────────────────────────────────


def test_second_run_writes_nothing(ledger):
    plan = C.build_plan(
        [row("a", content="claim one"), row("b", content="claim two")],
        source_graph="old", target_graph="new", packs={},
    )
    plan.decisions = [C.Decision(d.node_id, d.node_type, d.kind, C.CARRY, "test", d.pack, d.stable_key)
                      for d in plan.decisions]
    writer = CountingWriter()

    first = C.run_plan(plan, writer=writer, ledger=ledger, apply=True)
    assert first.written == 2
    assert len(writer.calls) == 1

    second = C.run_plan(plan, writer=writer, ledger=ledger, apply=True)
    assert second.written == 0
    assert second.skipped_known == 2
    assert len(writer.calls) == 1, "the second run must not reach the wire at all"


def test_digest_is_stable_across_runs(ledger):
    rows = [row("a", content="claim one"), row("b", content="claim two")]
    p1 = C.build_plan(rows, source_graph="old", target_graph="new", packs={})
    p2 = C.build_plan(rows, source_graph="old", target_graph="new", packs={})
    assert p1.digest() == p2.digest()


def test_duplicate_content_folds_onto_one_keeper():
    plan = C.build_plan(
        [row("a", content="same thing"), row("b", content="same thing")],
        source_graph="old", target_graph="new", packs={},
    )
    by_id = {d.node_id: d for d in plan.decisions}
    assert by_id["a"].disposition == C.UNDECIDABLE  # the keeper is still judged on merit
    assert by_id["b"].disposition == C.NOT_CARRIED
    assert "a" in by_id["b"].reason


# ── the switch and the guards ────────────────────────────────────────────


def test_dry_run_writes_nothing(ledger):
    plan = C.build_plan([row()], source_graph="old", target_graph="new", packs={})
    writer = CountingWriter()
    res = C.run_plan(plan, writer=writer, ledger=ledger, apply=False)
    assert res.written == 0 and not res.applied
    assert writer.calls == []
    assert "dry run" in (res.refused or "")


def test_explicit_off_switch_writes_nothing(ledger):
    plan = C.build_plan([row()], source_graph="old", target_graph="new", packs={})
    writer = CountingWriter()
    res = C.run_plan(plan, writer=writer, ledger=ledger, apply=True, off=True)
    assert res.off and not res.applied and writer.calls == []


def test_env_off_switch_writes_nothing(ledger, monkeypatch):
    monkeypatch.setenv(C.OFF_SWITCH_ENV, "1")
    plan = C.build_plan([row()], source_graph="old", target_graph="new", packs={})
    writer = CountingWriter()
    res = C.run_plan(plan, writer=writer, ledger=ledger, apply=True)
    assert res.off and writer.calls == []


def test_source_equals_target_refused(ledger):
    plan = C.build_plan([row()], source_graph="live-v4", target_graph="live-v4", packs={})
    with pytest.raises(C.CurationRefused):
        C.run_plan(plan, writer=CountingWriter(), ledger=ledger, apply=True)


def test_source_equals_target_refused_across_spellings():
    # namespace string vs graph id — the same graph, two origins
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("org_abc_g_2b7e4c9d1a6f8035", "g_2b7e4c9d1a6f8035")


def test_no_target_refused():
    with pytest.raises(C.CurationRefused):
        C.assert_distinct_graphs("old", "")


def test_writer_protocol_declares_no_destructive_verb():
    """Additivity is a property of the type: if someone adds a delete verb here,
    this test fails before the pass can destroy the only copy."""
    verbs = [m for m in dir(C.PlanWriter) if not m.startswith("_")]
    assert verbs == ["write_points"], verbs


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
    d = C.classify(row(node_type="operator", kind=None, content=""), {})
    assert d.disposition == C.CARRY and "reasoning" in d.reason


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


def test_bundle_carries_no_deletion():
    plan = C.build_plan([row("a", kind="decision", content="d")], source_graph="old", target_graph="new", packs={})
    body = json.dumps(plan.bundle())
    for verb in ("delete", "DELETE", "retract", "supersede"):
        assert verb not in body
