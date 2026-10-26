"""#10017 — the purge gate's own pins: parity rules, refusal arms, exit codes.

`tools/turn_parity_census.py` is the executable gate the destructive half of
#10000 (#10017) waits on. A gate that has only ever been observed passing is
decoration, so these tests pin the NEGATIVE arms first — a record missing a
turn row, a mismatched text, a lost vector each REFUSE — and then the pass
shape, the anomaly partition, and the exit-code contract.

The live end-to-end demonstration (real capture → real journal replay into a
throwaway record → census exit 0 → one row removed → exit 2) is a lane-run
artifact on throwaway stores, not a CI test; what CI can and must hold is the
comparison core and the CLI contract, which is everything the verdict is made
of. The store legs are thin: `read_graph_turns` / `read_record_turns` feed
`census()` the two dictionaries these tests build by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools.turn_parity_census as tpc

# ── fixtures: the two dictionaries the store legs produce ──────────────────

def _graph_node(tid: str, sid: str, **over) -> dict:
    node = {
        "id": tid,
        "content": f"[user] body of {tid}",
        "speaker": "user",
        "status": "draft",
        "createdAt": "2026-10-25T00:00:00+00:00",
        "pointKind": "event",
        "embedding": None,
        "sessions": {sid},
    }
    node.update(over)
    return node


def _record_row(tid: str, **over) -> dict:
    row = {
        "node_key": tid,
        "content": f"[user] body of {tid}",
        "speaker": "user",
        "status": "draft",
        "created_at": "2026-10-25T00:00:00+00:00",
        "point_kind": "event",
        "is_episodic": True,
        "embedding": None,
    }
    row.update(over)
    return row


def _stores(nodes: dict[str, dict], rows: dict[str, dict]) -> tuple[dict, dict]:
    return ({"nodes": nodes},
            {"rows_by_key": rows, "non_turn_shaped": 0})


def _census(nodes, rows):
    graph, record = _stores(nodes, rows)
    return tpc.census(graph, record, sample=5)


def _problems(report: dict, session: str) -> list[dict]:
    for v in report["parity"]["failed_sessions"]:
        if v["session"] == session:
            return v["problems"]
    return []


# ── the negative arms: a gate that cannot refuse is decoration ─────────────

def test_a_missing_record_row_refuses_and_names_the_key():
    """THE #10017 precondition: a graph turn the record does not hold."""
    nodes = {"s1_t0": _graph_node("s1_t0", "s1"),
             "s1_t1": _graph_node("s1_t1", "s1")}
    rows = {"s1_t0": _record_row("s1_t0")}  # s1_t1 deliberately absent
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert report["dry_run"]["would_delete_turn_nodes"] == 0
    assert report["dry_run"]["blocked_sessions"] == ["s1"]
    problems = _problems(report, "s1")
    assert problems == [{"node": "s1_t1", "field": "key",
                         "problem": "missing_in_record",
                         "graph": {"id": "s1_t1"}, "record": None}]


@pytest.mark.parametrize("graph_over,record_over,field", [
    ({"content": "[user] real text"}, {"content": "[user] TAMPERED"}, "text"),
    ({"speaker": "user"}, {"speaker": "assistant"}, "speaker"),
    ({"status": "promoted"}, {"status": "draft"}, "status"),
    ({"createdAt": "2026-10-25T00:00:00+00:00"},
     {"created_at": "2026-10-26T00:00:00+00:00"}, "createdAt"),
    ({"pointKind": "event"}, {"point_kind": "claim"}, "pointKind"),
])
def test_field_mismatches_refuse_and_name_the_field(graph_over, record_over,
                                                    field):
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", **graph_over)}
    rows = {"s1_t0": _record_row("s1_t0", **record_over)}
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    problems = _problems(report, "s1")
    assert problems and problems[0]["field"] == field
    assert problems[0]["problem"] == "field_mismatch"


def test_a_lost_vector_refuses():
    """The graph holds a vector the record does not — the purge would lose it."""
    vec = [0.1, 0.2, 0.3]
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=vec)}
    rows = {"s1_t0": _record_row("s1_t0", embedding=None)}
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert _problems(report, "s1")[0]["field"] == "embedding"


def test_vector_length_mismatch_refuses():
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=[0.1, 0.2])}
    rows = {"s1_t0": _record_row("s1_t0", embedding=[0.1, 0.2, 0.3])}
    assert _census(nodes, rows)["verdict"] == "REFUSED"


def test_a_component_mismatch_refuses():
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=[0.1, 0.2])}
    rows = {"s1_t0": _record_row("s1_t0", embedding=[0.1, 0.9])}
    assert _census(nodes, rows)["verdict"] == "REFUSED"


# ── the pass shape ──────────────────────────────────────────────────────────

def test_exact_parity_passes_and_the_dry_run_counts():
    nodes = {f"s1_t{i}": _graph_node(f"s1_t{i}", "s1") for i in range(3)}
    rows = {f"s1_t{i}": _record_row(f"s1_t{i}") for i in range(3)}
    report = _census(nodes, rows)
    assert report["verdict"] == "CLEAN"
    assert report["dry_run"]["would_delete_turn_nodes"] == 3
    assert report["parity"]["failed_sessions"] == []
    session = report["dry_run"]["per_session_sample"][0]
    assert session["count"] == 3
    # Identifying fields, never the whole transcript.
    assert session["sample"][0]["id"] == "s1_t0"
    assert session["sample"][0]["speaker"] == "user"
    assert session["sample"][0]["createdAt"] == "2026-10-25T00:00:00+00:00"


def test_binary64_record_vector_matches_binary32_graph_vector():
    """The record's jsonb floats are binary64; the graph's vecf32 is binary32.

    Equality through binary32 rounding — the storage the graph itself applies
    — is parity, or every real capture would fail the census.
    """
    f32 = lambda v: tpc._f32(float(v))  # noqa: E731
    vec64 = [0.1, 0.2, 0.3]  # not exactly representable in binary32
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=[f32(v) for v in vec64])}
    rows = {"s1_t0": _record_row("s1_t0", embedding=vec64)}
    assert _census(nodes, rows)["verdict"] == "CLEAN"


def test_record_superset_passes_even_though_counts_differ():
    """The post-cutover append: record ⊇ graph per key is the purge criterion.

    A session captured before the cutover and appended to after it holds graph
    nodes for the old window and record rows for the whole stream; deleting
    the graph copies loses nothing, so the census passes and reports the
    count fact instead of refusing on it.
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1"),
             "s1_t1": _graph_node("s1_t1", "s1")}
    rows = {"s1_t0": _record_row("s1_t0"), "s1_t1": _record_row("s1_t1"),
            "s1_t2": _record_row("s1_t2")}  # appended post-cutover
    report = _census(nodes, rows)
    assert report["verdict"] == "CLEAN"
    assert report["dry_run"]["would_delete_turn_nodes"] == 2
    clean = report["parity"]["clean_sessions"]
    assert clean and clean[0]["counts_equal"] is False

def test_graph_without_vector_passes_when_record_holds_one():
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=None)}
    rows = {"s1_t0": _record_row("s1_t0", embedding=[0.1])}
    assert _census(nodes, rows)["verdict"] == "CLEAN"


# ── the population rule: nothing silently invisible ─────────────────────────

def test_anomalies_are_excluded_from_the_purge_set_and_reported():
    """Orphans, non-decimal suffixes, cross-wirings: never deleted, never hidden.

    An unclassifiable node is OUTSIDE the deterministic id space the sweep
    touches (`_capture_turn_ids` retains-but-cannot-place), so it is excluded
    from the purge set and REPORTED — the census does not guess an owner for
    it, and the purge executor handles the anomaly list separately.
    """
    nodes = {
        "s1_t0": _graph_node("s1_t0", "s1"),          # in population
        "s2_t0": _graph_node("s2_t0", "s2"),          # in population
        "orphan_t0": _graph_node("orphan_t0", "orphan", sessions=set()),
        "s1_t3x": _graph_node("s1_t3x", "s1"),        # suffix not all-decimal
        # wired to a session the id does not name — reported, never guessed
        "sec_t0_t3": _graph_node("sec_t0_t3", "sec"),
    }
    nodes["s1_t0"]["sessions"] = {"s1", "s1_t3x"}     # cross-wired shape
    rows = {"s1_t0": _record_row("s1_t0"), "s2_t0": _record_row("s2_t0")}
    report = _census(nodes, rows)
    # ONLY the unambiguous s2 node is a purge candidate; s1's cross-wired
    # node, the orphan and the mismatched wiring are excluded and reported,
    # never deleted.
    assert report["verdict"] == "CLEAN"
    assert report["dry_run"]["would_delete_turn_nodes"] == 1
    anomalies = report["anomalies"]
    assert any(a["id"] == "orphan_t0" for a in anomalies["orphan"])
    assert any(a["id"] == "s1_t3x" for a in anomalies["non_decimal_suffix"])
    assert any(a["id"] == "s1_t0" for a in anomalies["cross_wired"])
    mismatch = [a for a in anomalies["session_mismatch"]
                if a["id"] == "sec_t0_t3"]
    assert mismatch and mismatch[0]["wired_session"] == "sec"
    assert mismatch[0]["derived_session"] == "sec_t0"
    # ...and the population count still sees every turn-shaped node.
    assert report["population"]["turn_shaped_nodes"] == 5


def test_turn_shaped_nodes_are_counted_whatever_the_record_says():
    nodes = {"s1_t0": _graph_node("s1_t0", "s1")}
    report = _census(nodes, {})
    assert report["population"]["turn_shaped_nodes"] == 1
    assert report["population"]["sessions_with_graph_turns"] == 1


# ── the exit-code contract (a caller keys on it) ───────────────────────────

def _argv(*extra):
    return ["--uri", "docker://:@127.0.0.1:1/g", "--record-dsn",
            "postgresql://x/y", "--org-id", "o", "--graph-id", "g", *extra]


def test_usage_errors_exit_one_not_two(monkeypatch):
    """Exit 2 is REFUSED; a flag mistake must never read as a parity failure."""
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    # A missing DSN is a usage/configuration miss: exit 1, a plain return.
    assert tpc.main(["--uri", "docker://:@127.0.0.1:1/g"]) == 1
    # An invalid flag VALUE takes argparse's error() path — overridden to 1 so
    # "you typed --sample -1" is distinguishable from "the record is missing
    # turns" for any caller keying on the exit code.
    with pytest.raises(SystemExit) as exc:
        tpc.main(_argv("--sample", "-1"))
    assert exc.value.code == 1


def test_missing_tenant_refuses_to_guess(monkeypatch):
    monkeypatch.delenv("TORTOISE_RECORD_ORG_ID", raising=False)
    monkeypatch.delenv("TORTOISE_RECORD_GRAPH_ID", raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://x/y")
    assert tpc.main(["--uri", "docker://:@127.0.0.1:1/g"]) == 1


def test_unreadable_side_exits_three_never_zero(monkeypatch, tmp_path):
    """The P0 shape: an unreadable record must not certify parity vacuously."""
    def boom(*a, **k):
        raise tpc.CensusError("the record could not be opened")
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {"nodes": {}})
    monkeypatch.setattr(tpc, "read_record_turns", boom)
    assert tpc.main(_argv()) == 3


def test_main_maps_clean_to_zero_and_refused_to_two(monkeypatch, tmp_path):
    nodes = {"s1_t0": _graph_node("s1_t0", "s1")}
    graph = {"nodes": nodes}
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: graph)
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {}, "non_turn_shaped": 0})
    assert tpc.main(_argv("--json", str(tmp_path / "r.json"))) == 2

    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {"s1_t0": _record_row("s1_t0")},
        "non_turn_shaped": 0})
    assert tpc.main(_argv()) == 0
