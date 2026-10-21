"""#8444 census, for `#7949` E1 and E3 — non-vacuous, fail-closed, report-only.

`#7949` E1 ("the `^the <X>` share of `:Object`s") and E3 ("every derived node
carries its version anchor — `:Object`/`:Event` included") had no home in a
run's own artifact: `METRIC_VALUES` has no object-composition member,
`provenance_accuracy` is a Point-level PRESENCE test that never reads
`sourceVersion`, and `snapshot_session` reads `:Point` only. `#8444` asked for
a report-only census in the runner; these tests pin it.

Every test names the mutation that makes it FAIL, and the ones that matter run
the census over a REAL capture (the offline extractor lane) rather than a
fixture, because the point is that the numbers come off the graph the write
path actually wrote.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.eval.write_path import runner, schema
from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user", "content": "I think the auth dead-end is the top issue. "
                                "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed. Evidence suggests the website "
                                     "config is the root cause."},
    {"role": "user", "content": "ok"},
]


@pytest.fixture()
def captured(tmp_path, monkeypatch):
    """(sdk, session_id, snapshot) — one real capture on the offline lane."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    db = tmp_path / "t.db"
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(db), event_log_path=str(events / "events.jsonl"))
    res = sdk.capture_session(CONV)
    assert res["ok"] is True and res["extracted"] >= 1, res
    snap = runner.snapshot_session(sdk, res["session_id"])
    yield sdk, res["session_id"], snap
    sdk.close()


def _point_ids(snap: dict) -> list[str]:
    """The session's memory layer: claims AND reified operator Points."""
    return [p["point_id"]
            for p in snap.get("points", []) + snap.get("operator_nodes", [])]


def test_the_census_counts_the_objects_the_capture_wrote(captured):
    """FAILS IF the census stops reading the graph (a query error would raise
    here, and `census_or_error` would return an ``error`` block instead of a
    count) or if `aboutObject` is no longer the edge it scopes Objects by."""
    sdk, session_id, snap = captured
    census = runner.graph_census(sdk, session_id, _point_ids(snap))

    assert "error" not in census, census
    objects = census["objects"]
    assert objects["total"] >= 1, (
        "the offline lane's capture minted an Object the census cannot see: "
        f"{census!r}")

    # Non-vacuity: the count agrees with an INDEPENDENT read of the same graph,
    # and the share is the arithmetic of the two numbers it reports.
    names = [row[1] or "" for row in sdk._get_proj().g.query(
        "MATCH (p:Point)-[:aboutObject]->(o:Object) WHERE p.id IN $pids "
        "RETURN DISTINCT ID(o), o.name",
        params={"pids": _point_ids(snap)}).result_set]
    assert objects["total"] == len(names)
    assert objects["the_share"] == pytest.approx(
        objects["the_prefixed"] / len(names))


def test_a_definite_description_is_counted_and_a_bare_name_is_not(captured):
    """FAILS IF the `^the <X>` predicate stops being case-insensitive or starts
    matching a name that merely CONTAINS "the" (`#7949` E1's instrument is
    `^the\\s+\\S`, anchored at the start)."""
    sdk, session_id, snap = captured
    g = sdk._get_proj().g
    oid_rows = g.query(
        "MATCH (p:Point)-[:aboutObject]->(o:Object) WHERE p.id IN $pids "
        "RETURN ID(o) LIMIT 1", params={"pids": _point_ids(snap)}).result_set
    assert oid_rows, "the capture minted no Object — guard failed"
    nid = oid_rows[0][0]

    def census_the_count(name: str) -> int:
        g.query("MATCH (o:Object) WHERE ID(o) = $nid SET o.name = $name",
                params={"nid": nid, "name": name})
        return runner.graph_census(
            sdk, session_id, _point_ids(snap))["objects"]["the_prefixed"]

    assert census_the_count("the rollout policy") >= 1
    assert census_the_count("The rollout policy") >= 1  # case-insensitive
    assert census_the_count("policy on the rollout") == 0  # not ANCHORED at start
    assert census_the_count("the") == 0  # no <X> after the article


def test_a_blank_or_absent_anchor_is_counted_as_UNANCHORED(captured):
    """FAILS IF the anchored predicate stops following the WRITER's own rule.

    `projection/edges.py::_anchor_on_create` treats NULL **and** `''` as "not
    anchored" — `''` is the auto-created-Source placeholder and compares equal
    to a missing hash, i.e. a false current. A census that counted `''` as an
    anchor would report exactly the false green `#8501` shipped with.
    """
    sdk, session_id, snap = captured
    pids = _point_ids(snap)
    anchored = runner.graph_census(sdk, session_id, pids)["derivation_edges"]
    assert anchored["extractedFrom"]["total"] >= 1, anchored
    assert anchored["extractedFrom"]["anchored"] == \
        anchored["extractedFrom"]["total"], anchored  # #8501 is fixed on main

    g = sdk._get_proj().g
    g.query("MATCH (:Point)-[r:extractedFrom]->(:Source) SET r.sourceVersion = ''")
    blanked = runner.graph_census(sdk, session_id, pids)["derivation_edges"]
    assert blanked["extractedFrom"]["total"] == anchored["extractedFrom"]["total"]
    assert blanked["extractedFrom"]["anchored"] == 0, blanked

    g.query("MATCH (:Point)-[r:extractedFrom]->(:Source) SET r.sourceVersion = NULL")
    nulled = runner.graph_census(sdk, session_id, pids)["derivation_edges"]
    assert nulled["extractedFrom"]["anchored"] == 0, nulled

    # The `references` leg has its own copy of the predicate (it also excludes
    # containment targets), so pin its `''` arm too — one shared-looking rule
    # spelled twice is exactly where a false current survives.
    g.query("MATCH (:Source)-[r:references]->(:Event) SET r.sourceVersion = ''")
    refs = runner.graph_census(sdk, session_id, pids)["derivation_edges"]["references"]
    assert refs["total"] >= 1 and refs["anchored"] == 0, refs


def test_the_census_sees_the_unanchored_class_provenance_accuracy_cannot(captured):
    """The `#8444` argument, measured rather than asserted.

    `provenance_accuracy` is `bool(eventId) or bool(extractedFrom)` on the
    Point ROW — a presence test. Blank the anchor and it still reports
    provenance; the census reports 0 anchored. FAILS IF `provenance_present`
    ever becomes anchor-aware (then the two would agree and the gap this
    instrument exists for would be closed — delete this leg, do not weaken
    it), or if the census silently reads the row property instead of the edge.
    """
    sdk, session_id, snap = captured
    pids = _point_ids(snap)
    sdk._get_proj().g.query(
        "MATCH (:Point)-[r:extractedFrom]->(:Source) SET r.sourceVersion = NULL")
    census = runner.graph_census(sdk, session_id, pids)
    assert census["derivation_edges"]["extractedFrom"]["anchored"] == 0

    row = sdk._get_proj().g.query(
        f"MATCH (p:Point) WHERE p.id IN $pids RETURN {runner.MEMORY_ROW_COLUMNS}",
        params={"pids": pids}).result_set
    assert row, "the session has no memory rows — guard failed"
    points = [runner._row_to_point(r) for r in row]
    assert all(p["provenance_present"] for p in points), (
        "provenance_present is anchor-aware now — the census gap this leg "
        "pins no longer exists; update, do not weaken")


def test_an_object_reached_only_by_aboutObject_has_no_derivation_link(captured):
    """E3's `:Object` half, MEASURED (`#7949` E3, `#5199` option A).

    The capture's `:Object` is reached only by `(Point)-[:aboutObject]->`, so
    it carries no `extractedFrom`/`references` link to the artifact it was
    derived from — there is nothing on it to mark stale when the transcript is
    revised. That is a REPORTED number here, never an inferred pass: FAILS IF
    the write path starts linking Objects to their source (then E3's Object
    half has moved and this pin must be updated with the evidence, not
    relaxed).
    """
    sdk, session_id, snap = captured
    objects = runner.graph_census(sdk, session_id, _point_ids(snap))["objects"]
    assert objects["total"] >= 1, objects
    assert objects["with_derivation_link"] == 0, (
        "an :Object now carries a derivation link — `#7949` E3's Object half "
        "has moved; record the new number on the epic and update this pin")


def test_the_session_source_to_event_reference_anchor_is_counted(captured):
    """FAILS IF the `references` leg scopes by Point ids instead of the
    session's `:Source` — the `#5199` anchor lives on Source→Event, so a
    Point-scoped query would report a permanent 0/0 and read as \"nothing to
    anchor\"."""
    sdk, session_id, snap = captured
    refs = runner.graph_census(
        sdk, session_id, _point_ids(snap))["derivation_edges"]["references"]
    assert refs["total"] >= 1, refs
    assert refs["anchored"] == refs["total"], refs


def test_a_containment_link_is_excluded_from_the_reference_anchor_count(captured):
    """FAILS IF the `references` leg counts referential CONTAINMENT.

    A `(Source)-[:references]->(:Source)` link carries no `sourceVersion` **by
    design**, so counting one would turn a deliberately unanchored,
    non-derivation edge into an E3 failure — a false red. The excluded
    population is still reported, so it cannot vanish silently either.
    """
    sdk, session_id, snap = captured
    g = sdk._get_proj().g
    before = runner.graph_census(
        sdk, session_id, _point_ids(snap))["derivation_edges"]["references"]
    assert before["containment_excluded"] == 0, before

    g.query(
        "MATCH (s:Source {sessionId:$sid}) "
        "CREATE (s)-[:references]->(:Source {url:$url, sourceKind:'external'})",
        params={"sid": session_id, "url": f"session:{session_id}:external"})
    after = runner.graph_census(
        sdk, session_id, _point_ids(snap))["derivation_edges"]["references"]
    assert after["total"] == before["total"], (
        "a containment link entered the derivation population — an unanchored "
        "non-derivation edge would read as an E3 failure")
    assert after["anchored"] == after["total"]
    assert after["containment_excluded"] == 1, after


def test_a_graph_error_is_reported_never_zeroed(captured):
    """FAILS IF `census_or_error` swallows a graph failure into zeros — a
    measured empty session and an unreadable graph must not be the same
    artifact (`#8501`'s defect class one level up)."""

    class _Boom:
        def query(self, *_a, **_k):
            raise RuntimeError("socket closed")

    class _Sdk:
        def _get_proj(self):
            class _P:
                g = _Boom()
            return _P()

    census = runner.census_or_error(_Sdk(), "session-x", ["p1"])
    assert "error" in census and "socket closed" in census["error"], census
    assert "objects" not in census

    aggregate = runner.aggregate_graph_census({"session-x": census})
    assert aggregate["sessions_errored"] == ["session-x"]
    assert aggregate["objects"]["total"] == 0
    assert aggregate["objects"]["the_share"] is None  # never 0.0


def test_a_zero_denominator_reports_no_share_never_zero(captured):
    """FAILS IF an empty Object set reports `0.0` — which reads as \"the
    `^the <X>` class is gone\" when the truth is that there was nothing to
    divide by (`#7949` E1's fails-if is about the class moving, not about a
    session emitting nothing)."""
    sdk, session_id, _snap = captured
    census = runner.graph_census(sdk, session_id, [])
    assert census["objects"] == {"total": 0, "the_prefixed": 0,
                                 "the_share": None, "with_derivation_link": 0}
    assert runner.aggregate_graph_census({session_id: census})["objects"][
        "the_share"] is None


def test_the_census_is_report_only(captured):
    """FAILS IF the census enters the GATED surface.

    A `METRIC_VALUES` member would force a baseline re-bless on every existing
    receipt; a `metrics` entry would move a verdict. Neither may happen for a
    measurement added to make `#7949` E1/E3 answerable.
    """
    assert "graph_census" not in schema.METRIC_VALUES
    # The gated vocabulary must not ACQUIRE an object-composition or anchor
    # metric by accident: adding one is a decision that re-blesses every
    # baseline, so it must move this pin in the same commit.
    assert not any("object" in name or "anchor" in name
                   for name in schema.METRIC_VALUES)

    sdk, session_id, snap = captured
    census = runner.graph_census(sdk, session_id, _point_ids(snap))
    report = {
        "run_id": "r", "date": "2026-10-21", "run_status": "completed",
        "verdict": schema.VERDICT_PASS, "failure_origin": None,
        "commit": "c", "corpus_hash": "h", "judge_pin": "pin",
        "resolved_config": {}, "cost_usd": 0.0,
        "metrics": dict.fromkeys(schema.METRIC_VALUES, 0.0),
        "quote_spans_total": 0, "operator_audit": None, "semantic_judge": None,
        "session_results": [], "notes": [], "log": [],
    }
    # A report WITHOUT the census is byte-identical to the pre-#8444 shape.
    assert "graph_census" not in runner.build_receipt(report)
    report["graph_census"] = {
        "sessions": {session_id: census},
        "aggregate": runner.aggregate_graph_census({session_id: census}),
    }
    receipt = runner.build_receipt(report)
    assert receipt["graph_census"]["aggregate"]["objects"]["total"] >= 1
    assert runner.validate_receipt(receipt) == []
    # ... and it still does not enter `metrics`.
    assert "graph_census" not in receipt.get("metrics", {})


def test_the_aggregate_is_a_ratio_of_sums_not_a_mean_of_shares():
    """FAILS IF the aggregate averages per-session shares — a one-Object
    session would then weigh as much as a twenty-Object one, and `#7949` E1's
    corpus-level share would be wrong in exactly the direction that flatters a
    small session."""
    per_session = {
        "a": {"objects": {"total": 1, "the_prefixed": 1, "the_share": 1.0,
                          "with_derivation_link": 0},
              "derivation_edges": {"extractedFrom": {"total": 1, "anchored": 1},
                                   "references": {"total": 1, "anchored": 1}}},
        "b": {"objects": {"total": 3, "the_prefixed": 0, "the_share": 0.0,
                          "with_derivation_link": 0},
              "derivation_edges": {"extractedFrom": {"total": 0, "anchored": 0},
                                   "references": {"total": 1, "anchored": 0}}},
    }
    aggregate = runner.aggregate_graph_census(per_session)
    assert aggregate["objects"]["the_share"] == pytest.approx(0.25)  # not 0.5
    assert aggregate["derivation_edges"]["extractedFrom"] == {
        "total": 1, "anchored": 1}
    assert aggregate["derivation_edges"]["references"] == {
        "total": 2, "anchored": 1}
    assert aggregate["sessions_with_objects"] == 2
    assert aggregate["sessions_errored"] == []


def test_the_report_level_block_is_per_session_plus_one_aggregate(captured):
    """FAILS IF the run's census stops being keyed per session (a corpus-level
    number with no session behind it cannot say WHICH session moved E1), or if
    a session present in the snapshot is dropped from the block."""
    sdk, session_id, snap = captured
    block = runner.build_graph_census(sdk, {session_id: snap})
    assert list(block["sessions"]) == [session_id]
    assert block["aggregate"]["sessions"] == 1
    assert block["aggregate"]["objects"]["total"] == \
        block["sessions"][session_id]["objects"]["total"]
    assert runner.graph_census_notes(block) == []  # a clean read says nothing


def test_an_unreadable_session_names_itself_in_the_report_note(captured):
    """FAILS IF a failed graph read is absorbed into a note-less, zeroed
    aggregate — the receipt-level form of the "instrument that cannot see the
    writer" defect (`#4501`-class): a reader would take an unmeasured session
    for a measured empty one."""
    class _Boom:
        def query(self, *_a, **_k):
            raise RuntimeError("socket closed")

    class _Sdk:
        def _get_proj(self):
            class _P:
                g = _Boom()
            return _P()

    _sdk, session_id, snap = captured
    block = runner.build_graph_census(_Sdk(), {session_id: snap})
    assert "error" in block["sessions"][session_id]
    assert block["aggregate"]["sessions_errored"] == [session_id]
    notes = runner.graph_census_notes(block)
    assert len(notes) == 1 and session_id in notes[0]
    assert "never zeroed" in notes[0] and "socket closed" in notes[0]


# ── the retracted claim in the epic's own E1/E2 bundle ─────────────────────

_BUNDLE = (Path(__file__).resolve().parent.parent
           / "docs" / "evidence" / "7949-capstone" / "E1-E2.md")


def test_the_bundle_retracts_the_claim_that_a_graph_census_adds_nothing():
    """FAILS IF the retracted wording comes back, or the counterexample is lost.

    The bundle's limit 2 said a graph census "would catch no defect this one
    misses". `#8501` is that defect: every `extractedFrom` link carried a NULL
    `sourceVersion` (0/113) — an EDGE property, which the Layer-1 payload
    census structurally cannot see. The retraction is pinned here so the claim
    cannot be restored silently by a later reader who only reads the limits
    list, and the `#8501` numbers must stay quoted with it.
    """
    text = " ".join(_BUNDLE.read_text(encoding="utf-8").split())
    # Quoting a retracted sentence INSIDE its retraction is the convention; the
    # claim being live again is the failure. So the sentence may appear exactly
    # once, and only as a quotation named as retracted.
    retracted = "would add a docker dependency and catch no defect this one misses"
    assert text.count(retracted) == 1, (
        "the retracted graph-census claim appears more than once — one of them "
        "is no longer a quotation")
    at = text.index(retracted)
    window = text[max(0, at - 300):at + 300]
    assert "was a measured falsehood, retracted here" in window, (
        "the graph-census claim is live again: it is not named as retracted "
        "where it is quoted")
    assert "0/113" in text, "the retraction must carry the measured counterexample"
    assert "graph_census" in text, (
        "the bundle must name the instrument that closes the gap (#8444)")
