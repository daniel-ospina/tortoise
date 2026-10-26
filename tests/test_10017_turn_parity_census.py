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


def test_the_embedding_band_is_EXACT_literals_not_the_function_under_test():
    """Discriminating literals: one binary32 ULP apart must REFUSE.

    The sibling test builds its graph side with `tpc._f32`, and the comparison
    applies `tpc._f32` — so a mutation that WIDENS the band ("floating point,
    use a tolerance") stayed green, which is the one change that turns a
    different vector into parity on an irreversible delete. These cases carry
    literal values on both sides, so no helper can satisfy both:

        f32(0.1)              = 0.10000000149011612   (the graph's value)
        next f32 up           = 0.10000000894069672   (~6e-9 away)

    A tolerance of 1e-6 would call that second pair equal. Parity must not.
    """
    graph_vec = [0.10000000149011612]   # exactly f32(0.1)
    same_at_binary64 = [0.1]            # binary64 0.1 rounds to the same f32
    one_ulp_up = [0.10000000894069672]  # the NEXT binary32 value

    assert _census({"s1_t0": _graph_node("s1_t0", "s1", embedding=graph_vec)},
                   {"s1_t0": _record_row("s1_t0", embedding=same_at_binary64)}
                   )["verdict"] == "CLEAN"

    report = _census(
        {"s1_t0": _graph_node("s1_t0", "s1", embedding=graph_vec)},
        {"s1_t0": _record_row("s1_t0", embedding=one_ulp_up)})
    assert report["verdict"] == "REFUSED"
    problem = _problems(report, "s1")[0]
    assert problem["field"] == "embedding"
    assert "binary32" in problem["detail"]


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

def test_anomalies_refuse_the_census_and_are_reported():
    """Orphans, non-decimal suffixes, cross-wirings: never deleted, never hidden.

    An unclassifiable node is OUTSIDE the deterministic id space the sweep
    touches (`_capture_turn_ids` retains-but-cannot-place), so it is excluded
    from the purge set and REPORTED — and, because a shape-based sweep
    (`is_episodic AND pointKind='event'`) would delete it anyway, its presence
    REFUSES the census (exit 2). Excluding a node from the purge set is not the
    same as certifying it: `CLEAN` must mean "measured the whole population and
    it matched", never "measured part of it and the part matched". This test
    previously pinned `CLEAN` here — the arm was pinned in the WRONG
    DIRECTION, which is how a gate opens over a node nothing compared.
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
    # node, the orphan and the mismatched wiring are excluded and reported —
    # and their presence is what makes the verdict REFUSED.
    assert report["verdict"] == "REFUSED"
    assert report["refused_for"] == ["outside_population"]
    assert report["dry_run"]["would_delete_turn_nodes"] == 1
    assert report["dry_run"]["would_delete_ids"] == ["s2_t0"]
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
    assert report["population"]["outside_population"] == 4


def test_an_operator_flagged_node_is_never_counted_as_purgeable():
    """`is_operator` is an anomaly, not a parity field — say it in BOTH lines.

    The report used to print "1 operator_flagged (excluded from the purge set)"
    and "would delete 1 turn node(s)" for the same node, because
    `in_population` never consulted `is_operator`. One of the two had to be
    wrong, and the delete count is the one that costs a transcript.
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", is_operator=True)}
    rows = {"s1_t0": _record_row("s1_t0")}
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert report["dry_run"]["would_delete_turn_nodes"] == 0
    assert [a["id"] for a in report["anomalies"]["operator_flagged"]] == ["s1_t0"]


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


def test_a_zero_turn_graph_is_not_evidence(monkeypatch):
    """A drained graph and a wrong --graph-name are the same read.

    `FalkorProjection.from_uri` does not verify the graph exists, so a typo
    opens a fresh EMPTY graph and a `CLEAN`/exit 0 there would certify parity
    over a target the operator never reached — the mirror of the wrong-tenant
    record guard the docstring defends. The operator asserts the drainage with
    `--expect-empty` (the post-purge re-run, #10017's own last step).
    """
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {"nodes": {}})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {}, "non_turn_shaped": 0})
    assert tpc.main(_argv()) == 3
    assert tpc.main(_argv("--expect-empty")) == 0


def test_a_graph_read_row_of_the_wrong_shape_is_an_unreadable_side(
        monkeypatch):
    """An unclassifiable row must not be skipped into a smaller population."""
    class _G:
        def query(self, cypher, params=None):
            class _R:
                result_set = [["only", "a", "partial"]] if params else [[1]]
            return _R()

    class _Proj:
        g = _G()

        @classmethod
        def from_uri(cls, *a, **k):
            return cls()

    from tortoise.projection import FalkorProjection
    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _Proj()))
    with pytest.raises(tpc.CensusError):
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)


def test_a_non_sequence_graph_vector_refuses_rather_than_reading_as_absent(
        monkeypatch):
    """Polarity: in a gate, "cannot parse the vector" must REFUSE, not pass.

    The graph leg used to narrow an unexpected container to `None` ("no
    vector"), which `_embedding_parity` reads as the record holding a
    superset — a PASS. A gate may not pass on evidence it could not read.
    """
    class _G:
        def query(self, cypher, params=None):
            class _R:
                result_set = ([[1, "s1_t0", "c", "user", "draft", None,
                                "event", "not-a-vector", False, ["s1"]]]
                              if params else [[1]])
            return _R()

    class _Proj:
        g = _G()

    from tortoise.projection import FalkorProjection
    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _Proj()))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "not a sequence" in str(exc.value)


# ── the graph leg's TOTALITY (RESULTSET_SIZE is silent — #5062's class) ────

class _PagedG:
    """A FalkorDB stand-in: pages of the census query + the store's own count."""

    def __init__(self, rows: list[list], *, truncate_to: int | None = None):
        self._rows = rows
        self._truncate_to = truncate_to
        self.page_calls = 0

    def query(self, cypher, params=None):
        class _R:
            def __init__(self, rs):
                self.result_set = rs

        if params is None:  # the one-row aggregate
            return _R([[len(self._rows)]])
        self.page_calls += 1
        served = (self._rows if self._truncate_to is None
                  else self._rows[: self._truncate_to])
        page = [r for r in served
                if int(r[0]) >= params["cursor"]][: params["batch"]]
        return _R(page)


def _turn_row(inner_id: int, tid: str, sid: str) -> list:
    return [inner_id, tid, f"[user] body of {tid}", "user", "draft",
            "2026-10-25T00:00:00+00:00", "event", None, False, [sid]]


def _patch_proj(monkeypatch, g):
    from tortoise.projection import FalkorProjection

    class _Proj:
        def __init__(self, inner):
            self.g = inner

        @classmethod
        def from_uri(cls, *a, **k):
            return cls(g)

    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _Proj(g)))


def test_the_graph_leg_PAGES_and_collects_every_node(monkeypatch):
    """One unpaged RETURN sees at most RESULTSET_SIZE (10 000) — silently.

    The population this gate exists for is ~20 000 turn nodes, i.e. twice the
    cap, so a single query would compare half of it and report CLEAN over the
    rest. Pin that the leg takes MORE THAN ONE page (a page size small enough
    to force it) and that both pages reach the node map.
    """
    rows = [_turn_row(i, f"s1_t{i}", "s1") for i in range(1, 6)]
    g = _PagedG(rows)
    _patch_proj(monkeypatch, g)
    monkeypatch.setattr(tpc, "_GRAPH_PAGE_SIZE", 2)
    graph = tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert sorted(graph["nodes"]) == [f"s1_t{i}" for i in range(1, 6)]
    assert g.page_calls >= 3, "the leg did not page — a 20k population truncates"


def test_a_TRUNCATED_graph_read_refuses_instead_of_certifying(monkeypatch):
    """THE fail-open this guard exists for: a short read is silence, not an error.

    The fake serves 2 of 3 rows while its aggregate counts 3 — exactly what
    FalkorDB's RESULTSET_SIZE does to a real 20 000-node population. The leg
    must refuse (exit 3: an absence is NOT established), never hand `census()`
    a smaller population that then matches a smaller record and passes.
    """
    rows = [_turn_row(i, f"s1_t{i}", "s1") for i in range(1, 4)]
    g = _PagedG(rows, truncate_to=2)
    _patch_proj(monkeypatch, g)
    monkeypatch.setattr(tpc, "_GRAPH_PAGE_SIZE", 10)
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "TRUNCATED" in str(exc.value)
    assert "counts 3" in str(exc.value)
