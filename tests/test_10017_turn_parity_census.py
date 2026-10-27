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
of. The STORE LEGS are pinned separately, without stores: `read_record_turns`
is driven through a STUBBED connection (its scoped door, its SQL column
contract and the refusal each failure raises) and `read_graph_turns` through a
fake `g` (paging, the aggregate cross-check, and the classification of every
row the store returns). An unpinned door is exactly how an unscoped read
(#10024) would certify vacuously while every core case stayed green.
"""

from __future__ import annotations

import ast
import sys
import types
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
    # The sampled turn's own fields. `content_head` is a 60-CHARACTER HEAD of
    # the transcript, emitted on purpose for the operator's eyeball comparison —
    # so this artifact is NOT content-free; what it never emits is the whole
    # transcript of any turn.
    assert session["sample"][0]["id"] == "s1_t0"
    assert session["sample"][0]["speaker"] == "user"
    assert session["sample"][0]["createdAt"] == "2026-10-25T00:00:00+00:00"


def test_the_sample_carries_a_60_CHARACTER_HEAD_not_the_transcript():
    """The exposure claim, PINNED — the previous assert could not hold it.

    It was `len(content_head) <= 60` against a fixture whose content is 20
    characters, so widening the slice to `[:600]` (a 10x-wider transcript head)
    or replacing it with `""` left the suite green: the property the comment
    above states was unpinned. The body here is longer than the slice, and the
    assertion is EXACT — the head, not a bound.
    """
    body = "[user] " + ("0123456789" * 30)  # 307 characters
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content=body)}
    rows = {"s1_t0": _record_row("s1_t0", content=body)}
    assert _census(nodes, rows)["verdict"] == "CLEAN"
    sample = _census(nodes, rows)["dry_run"]["per_session_sample"][0]["sample"][0]
    assert sample["content_head"] == body[:60]
    assert len(sample["content_head"]) == 60
    assert sample["content_head"] != body


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


def test_expect_empty_never_blesses_a_record_side_that_holds_nothing(
        monkeypatch, capsys):
    """The flag relaxes the GRAPH axis only, and it is the axis to get wrong.

    `--expect-empty` exists so a DRAINED target can certify after the purge. It
    is therefore the one flag a purge can be run under, and the one flag whose
    scope decides whether the gate protects anything on that run. A graph that
    still holds turn nodes while the record holds zero rows is the P0 shape —
    deleting on that reading loses every turn — and it must stay REFUSED (2)
    with the flag passed, not become a pass because the operator asserted an
    empty side for the OTHER axis.

    Measured on prod 2026-10-27 and the reason this pin exists: the record's
    every table held 0 rows while `/health` reported `record.armed=true,
    ok=true` (the counters reset on deploy, so they do not report volume), and
    a tenant at its capture ceiling writes nothing at all. "The record is
    armed" is not "the record holds the turns", so the purge run above is a
    reachable state, not a hypothetical.
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1"),
             "s1_t1": _graph_node("s1_t1", "s1")}
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {"nodes": nodes})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {}, "non_turn_shaped": 0})

    assert tpc.main(_argv("--expect-empty")) == 2
    out = capsys.readouterr()
    # NOT `"CLEAN" not in out`: the output legitimately contains the word as a
    # COUNT (`0 session(s) CLEAN`), so that assertion is a substring of both the
    # passing and the inverted reading and pins nothing. The verdict line is the
    # discriminating one, and the clean-session count is asserted explicitly.
    assert "verdict    : REFUSED" in out.out
    assert "0 session(s) CLEAN" in out.out
    assert "REFUSED  session s1" in out.out
    assert "would delete 0 turn node(s)" in out.out


def test_expect_empty_does_not_suppress_a_PARTIALLY_copied_session(
        monkeypatch):
    """One row of two is the half-copied state `--expect-empty` must not hide.

    A copy that stopped midway leaves the record holding SOME of a session's
    turns — the state a purge gate is most likely to meet in practice, and the
    one a coarse "the record has rows" check would pass.
    """
    nodes = {"s2_t0": _graph_node("s2_t0", "s2"),
             "s2_t1": _graph_node("s2_t1", "s2")}
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {"nodes": nodes})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {"s2_t0": _record_row("s2_t0")},
        "non_turn_shaped": 0})
    assert tpc.main(_argv("--expect-empty")) == 2


def test_an_ALL_unidentifiable_graph_is_not_reported_as_an_EMPTY_graph(
        monkeypatch, capsys):
    """The population is "episodic nodes", not "keyable episodic nodes".

    Every `unidentifiable` node was invisible to the zero-node guard, so a
    graph whose episodic nodes ALL lacked a usable `t.id` exited 3 with "the
    graph holds ZERO is_episodic nodes" — factually wrong about the target,
    and it sends the operator at a wrong `--graph-name` instead of at the nodes
    the graph actually holds. Those nodes are `non_string_id` anomalies, which
    REFUSE the census: exit 2, with the ids listed and nothing certified.
    """
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {}, "unidentifiable": [{"id": "2", "id_type": "int"}]})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {}, "non_turn_shaped": 0})
    assert tpc.main(_argv()) == 2
    assert "ZERO is_episodic nodes" not in capsys.readouterr().err


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


def test_a_DRIVER_vector_type_is_READ_not_refused(monkeypatch):
    """A `vecf32` read-back is NOT contractually a `list` — accept what the
    repo's own sanitizer accepts, or the gate can never open.

    `tortoise/embeddings.py::stamp_journal_embedding` accepts ANY non-string
    iterable for the same value from the same store, and says why: "a graph
    read-back (`t.embedding` after a `vecf32` write, #5004 round-3) can arrive
    as a numpy array or a driver vector type". The graph leg accepted only
    `list`/`tuple`, so on such a driver EVERY vector-carrying turn raised
    `CensusError` → exit 3 for the whole graph: the census could never certify
    on exactly the graphs where vector parity is the risk, and the gate could
    never open. Refusing a container the store itself hands back is a
    MISCLASSIFICATION, not caution.
    """
    class _DriverVector:  # no list base, no `.shape`: iteration is the contract
        def __init__(self, values):
            self._values = values

        def __iter__(self):
            return iter(self._values)

        def __len__(self):
            return len(self._values)

    value = tpc._f32(0.1)
    rows = [[1, "s1_t0", "[user] body of s1_t0", "user", "draft",
             "2026-10-25T00:00:00+00:00", "event",
             _DriverVector([value]), False, ["s1"]]]
    _patch_proj(monkeypatch, _PagedG(rows))
    graph = tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert graph["nodes"]["s1_t0"]["embedding"] == [value]


@pytest.mark.parametrize("container", [{"a": 1.0}, 3.0])
def test_a_numeric_NON_sequence_is_still_REFUSED(monkeypatch, container):
    """Widening the acceptance to iterables must not widen it to anything.

    A `dict` is a container (not a sequence of numbers) and a float is not
    iterable; the sanitizer refuses both for the same reason, and so does the
    census — the refusal direction is preserved, never a silent `None`.
    """
    rows = [[1, "s1_t0", "[user] body of s1_t0", "user", "draft",
             "2026-10-25T00:00:00+00:00", "event", container, False,
             ["s1"]]]
    _patch_proj(monkeypatch, _PagedG(rows))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "not a sequence" in str(exc.value)


def test_a_container_whose_ITERATION_RAISES_REFUSES_it_does_not_crash(monkeypatch):
    """A pathological container is an unreadable side (exit 3), not a traceback.

    `list(embedding)` caught only `TypeError`, so a container whose `__iter__`
    raised anything else escaped as the exit-1 "usage/IO" code — a code the
    module docstring reserves for bad flags. `BaseException` still propagates,
    so a `KeyboardInterrupt` is not swallowed.
    """
    class _Exploding:
        def __iter__(self):
            raise RuntimeError("driver blew up mid-iteration")

    rows = [[1, "s1_t0", "[user] body of s1_t0", "user", "draft",
             "2026-10-25T00:00:00+00:00", "event", _Exploding(), False,
             ["s1"]]]
    _patch_proj(monkeypatch, _PagedG(rows))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "not a sequence" in str(exc.value)


def test_an_OVERLARGE_graph_component_REFUSES_it_does_not_crash(monkeypatch):
    """`float(v)` overflows for an int past binary64's range: refuse, do not crash.

    The record leg catches `OverflowError` (`_embedding_parity`); the graph leg
    caught only `TypeError`/`ValueError`, so a component it could not convert
    escaped as a traceback with exit 1 ("usage / IO error") instead of the
    `CensusError` → exit 3 that an unclassifiable side must produce.
    """
    rows = [[1, "s1_t0", "[user] body of s1_t0", "user", "draft",
             "2026-10-25T00:00:00+00:00", "event", [10 ** 400], False,
             ["s1"]]]
    _patch_proj(monkeypatch, _PagedG(rows))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "non-numeric" in str(exc.value)


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


# ── the population IS the recorded delete predicate (#10017) ────────────────
#
# #10017's delete set and completion test are `MATCH (t:Point) WHERE
# is_episodic = true` — NO kind filter. `is_episodic` is not co-extensive with
# "turn" (graph-scripts/backfill_is_episodic.py stamps it on every
# session-wired point, "capture turn + regex-extracted points"; create_point /
# update_point accept it on any kind), so a gate that read only
# `pointKind = 'event'` left a class that the purge WOULD delete in neither the
# comparison nor the anomaly partition. These cases pin the widened read.

def test_an_episodic_node_that_is_not_a_turn_refuses_and_is_named():
    """In the DELETE set, outside the turn shape: reported by id, and REFUSED."""
    nodes = {
        "s1_t0": _graph_node("s1_t0", "s1"),
        "ex1": _graph_node("ex1", "s1", pointKind="claim",
                           content="regex-extracted claim, not a turn"),
    }
    rows = {"s1_t0": _record_row("s1_t0")}
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert report["refused_for"] == ["outside_population"]
    # The purge SET is still the turn shape — nothing uncompared is counted.
    assert report["dry_run"]["would_delete_turn_nodes"] == 1
    assert report["dry_run"]["would_delete_ids"] == ["s1_t0"]
    assert ([a["id"] for a in report["anomalies"]["non_turn_point_kind"]]
            == ["ex1"])
    # ...and the read population is the delete set itself.
    assert report["population"]["episodic_graph_nodes"] == 2
    assert report["population"]["turn_shaped_nodes"] == 1


def test_the_graph_leg_reads_the_whole_delete_set_not_only_the_turn_shape(
        monkeypatch):
    """The QUERY must not re-narrow the population to `pointKind = 'event'`.

    A row for an episodic-but-not-turn node must come back from the store and
    reach `census()`; and the predicate itself is pinned, because a future
    edit that re-adds the kind filter restores the silent exclusion without
    reddening any classification test.
    """
    rows = [_turn_row(1, "s1_t0", "s1"),
            [2, "ex1", "claim text", "user", "draft",
             "2026-10-25T00:00:00+00:00", "claim", None, False, ["s1"]]]
    g = _PagedG(rows)
    _patch_proj(monkeypatch, g)
    graph = tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert sorted(graph["nodes"]) == ["ex1", "s1_t0"]
    assert graph["nodes"]["ex1"]["pointKind"] == "claim"
    assert "t.is_episodic = true" in tpc._GRAPH_EPISODIC_CYPHER
    assert "pointKind = " not in tpc._GRAPH_EPISODIC_CYPHER, (
        "the read is narrowed to the turn shape again — the delete set is "
        "`is_episodic = true` (#10017)")
    assert "pointKind" not in tpc._GRAPH_EPISODIC_COUNT_CYPHER


def test_a_node_with_no_usable_key_is_REPORTED_not_silently_dropped(
        monkeypatch):
    """The one shape that used to be invisible in BOTH `nodes` and `anomalies`.

    It was dropped with a bare `continue`, and the aggregate cross-check then
    reported it as `TRUNCATED (RESULTSET_SIZE) or torn` — a misleading
    diagnosis that sends the operator at the wrong problem. It must be its own
    reported class, and the count guard must not blame truncation for it.
    """
    rows = [_turn_row(1, "s1_t0", "s1"),
            [2, 7, "no string id", "user", "draft", None, "event", None,
             False, ["s1"]]]
    g = _PagedG(rows)  # its aggregate counts both rows
    _patch_proj(monkeypatch, g)
    graph = tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert graph["unidentifiable"] == [{"id": "2", "id_type": "int"}]
    report = tpc.census(graph, {"rows_by_key": {"s1_t0": _record_row("s1_t0")},
                                "non_turn_shaped": 0}, sample=5)
    assert report["verdict"] == "REFUSED"
    assert [a["id"] for a in report["anomalies"]["non_string_id"]] == ["2"]


def test_an_overlarge_record_component_REFUSES_it_does_not_crash():
    """A field-VALUE problem is exit 2, never the exit-1 "bad flags" crash.

    `struct.pack('<f', 1e300)` raises OverflowError, which used to escape both
    `_embedding_parity` and `census()` and surface as a traceback with exit 1 —
    a code the docstring defines as "usage / IO error".
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=[1.0])}
    rows = {"s1_t0": _record_row("s1_t0", embedding=[1e300])}
    report = _census(nodes, rows)  # must not raise
    assert report["verdict"] == "REFUSED"
    problem = _problems(report, "s1")[0]
    assert problem["field"] == "embedding"
    assert "binary32" in problem["detail"]


def test_the_population_counts_the_nodes_with_no_usable_key():
    """`episodic_graph_nodes` is labelled "(the delete set)" — so it must
    include the nodes whose `t.id` is not a string.

    Those nodes carry no dict key, so they live in `unidentifiable`; counting
    `len(nodes)` alone printed a population SMALLER than the delete set the
    line names (an all-unkeyable graph printed 0). They still refuse the census
    as `non_string_id`, so no verdict changes — the number just has to be about
    the population it says it is about.
    """
    graph = {"nodes": {"s1_t0": _graph_node("s1_t0", "s1")},
             "unidentifiable": [{"id": "2", "id_type": "int"},
                                {"id": "3", "id_type": "int"}]}
    report = tpc.census(
        graph,
        {"rows_by_key": {"s1_t0": _record_row("s1_t0")},
         "non_turn_shaped": 0}, sample=5)
    assert report["verdict"] == "REFUSED"
    assert report["population"]["episodic_graph_nodes"] == 3
    assert report["population"]["turn_shaped_nodes"] == 1


def test_a_refused_session_reports_the_turn_nodes_it_HELD_BACK():
    """`would_delete_turn_nodes` is CLEAN sessions only — the gate refuses what
    it could not verify — so a REFUSED run must also state what it is holding
    back, or "would delete 0" reads as "there is nothing to delete".
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1"),
             "s1_t1": _graph_node("s1_t1", "s1")}
    rows = {"s1_t0": _record_row("s1_t0")}  # s1_t1 absent -> s1 REFUSED
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert report["dry_run"]["would_delete_turn_nodes"] == 0
    assert report["dry_run"]["held_back_turn_nodes"] == 2
    assert report["dry_run"]["blocked_sessions"] == ["s1"]


def test_the_CLI_states_the_held_back_turn_nodes(capsys, monkeypatch):
    """The operator reads stdout, not the JSON: the held-back line must print."""
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {"s1_t0": _graph_node("s1_t0", "s1"),
                  "s1_t1": _graph_node("s1_t1", "s1")},
        "unidentifiable": []})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {"s1_t0": _record_row("s1_t0")},
        "non_turn_shaped": 0})
    assert tpc.main(_argv()) == 2
    out = capsys.readouterr().out
    assert "would delete 0 turn node(s)" in out
    assert "held back  : 2 turn node(s)" in out
    assert "NOT eligible" in out


def test_a_node_in_two_anomaly_classes_is_counted_ONCE():
    """`outside_population` must be a NODE count, not a classification count."""
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", is_operator=True,
                                  sessions=set())}
    report = _census(nodes, {})
    anomalies = report["anomalies"]
    assert [a["id"] for a in anomalies["orphan"]] == ["s1_t0"]
    assert [a["id"] for a in anomalies["operator_flagged"]] == ["s1_t0"]
    assert report["population"]["outside_population"] == 1


# ── the embedded leg honours --graph-name (a wrong-target read) ─────────────

def test_the_embedded_leg_honours_graph_name(monkeypatch):
    """`--db-path x.db --graph-name team_acme` must read team_acme, not the default.

    `FalkorProjection(path, graph_name=...)` accepts the keyword and the
    embedded store selects the graph by it; the leg used to drop it, so the run
    censused the default `tortoise` graph and still printed a verdict for the
    graph nobody asked about.
    """
    seen: dict = {}

    class _G:
        def query(self, cypher, params=None):
            class _R:
                result_set = [] if params else [[0]]
            return _R()

    class _Proj:
        def __init__(self, path=None, **kwargs):
            seen["path"] = path
            seen.update(kwargs)
            self.g = _G()

    import tortoise.projection as projection
    monkeypatch.setattr(projection, "FalkorProjection", _Proj)

    graph = tpc.read_graph_turns(None, "/tmp/x.db", "team_acme")
    assert graph["nodes"] == {}
    assert seen == {"path": "/tmp/x.db", "graph_name": "team_acme"}

    seen.clear()
    tpc.read_graph_turns(None, "/tmp/x.db", None)
    assert seen == {"path": "/tmp/x.db", "graph_name": "tortoise"}


# ── the record leg: its door, its SQL contract, and its refusals ───────────
#
# Nothing in the file above calls `read_record_turns`; these cases do, against
# a stubbed driver, so the arms the PR body claims are pinned (scoped door,
# exit 3 on an unreadable side) really are: deleting the `scope_connection`
# call, or a renamed `public.point` column, reds here.

def _stub_record(monkeypatch, *, columns=None, rows=(), refuse_scope=False):
    """A stubbed record leg: the door is a spy, the connection has no cursor.

    `_Conn.cursor()` raises, so a leg that reaches the connection directly
    (bypassing `scope_connection`) cannot pass — it must go through the door.
    """
    import tortoise.projection  # noqa: F401 - imported for its side effects
    from tortoise import record_projection, record_serve

    monkeypatch.setitem(sys.modules, "psycopg2", types.ModuleType("psycopg2"))
    calls: dict = {}

    class _Cursor:
        def __init__(self):
            self.description = [(c,) for c in (columns
                                               or _RECORD_COLUMNS_EXPECTED)]

        def execute(self, sql, params):
            calls["sql"], calls["params"] = sql, params

        def fetchall(self):
            return list(rows)

    class _Conn:
        def cursor(self):  # a bypass must not be a way through
            raise AssertionError("the record leg must use the SCOPE door")

        def close(self):
            calls["closed"] = True

    conn = _Conn()

    def _open(dsn):
        calls["dsn"] = dsn
        return conn

    def _scope(opened, org_id):
        calls["scope"] = (opened, org_id)
        if refuse_scope:
            raise RuntimeError("tenant scope refused (BYPASSRLS role)")
        return _Cursor()

    monkeypatch.setattr(record_serve, "open_record", _open)
    monkeypatch.setattr(record_projection, "scope_connection", _scope)
    return calls, conn


#: The record columns the census consumes, spelled out HERE rather than read
#: from the tool: a stub built from the tool's own constant cannot catch the
#: constant shrinking together with the query.
_RECORD_COLUMNS_EXPECTED = ("node_key", "content", "speaker", "status",
                           "created_at", "embedding", "point_kind",
                           "is_episodic")


def _record_row_tuple(tid: str, *, kind="event", content=None) -> tuple:
    return (tid, content or f"[user] body of {tid}", "user", "draft",
            "2026-10-25T00:00:00+00:00", None, kind, True)


def test_the_record_leg_goes_through_the_scoped_door(monkeypatch):
    """The #10024 shape: an unscoped read returns zero rows and would certify.

    Asserts the DOOR is the one used (it is called, with the tenant org, and
    the connection's own `cursor()` is never reached), that the tenant pair is
    bound as SQL parameters, and that the SQL selects every column the
    comparison and the classifier consume.
    """
    calls, conn = _stub_record(
        monkeypatch, rows=[_record_row_tuple("s1_t0"),
                           _record_row_tuple("claim-1", kind="claim")])
    out = tpc.read_record_turns("postgresql://x/y", "org-1", "graph-1")
    assert calls["scope"] == (conn, "org-1")
    assert calls["dsn"] == "postgresql://x/y"
    assert calls["sql"] == tpc._RECORD_TURNS_SQL
    assert calls["params"] == ("org-1", "graph-1")
    # The SQL really selects every column the comparison and the classifier
    # consume, and filters on the same predicate the graph leg reads.
    assert calls["sql"] == tpc._RECORD_TURNS_SQL
    assert tuple(tpc._RECORD_TURNS_COLUMNS) == _RECORD_COLUMNS_EXPECTED
    for column in _RECORD_COLUMNS_EXPECTED:
        assert column in calls["sql"], f"the SQL omits {column}"
    assert "FROM public.point" in calls["sql"]
    assert "is_episodic IS TRUE" in calls["sql"]
    assert calls["closed"] is True
    # The turn key is grouped; the non-turn episodic row is counted, not lost.
    assert sorted(out["rows_by_key"]) == ["s1_t0"]
    assert out["non_turn_shaped"] == 1


def test_the_executed_record_SQL_is_DERIVED_from_the_column_contract():
    """ONE declaration: the executed query is built from `_RECORD_TURNS_COLUMNS`.

    That tuple is documented as the single source of the SELECT list AND as the
    contract checked against the cursor's `description`. The module carried a
    second, hand-spelled `_RECORD_TURNS_SQL` BELOW the derived one, and Python
    keeps the LAST assignment — so the executing statement was the handwritten
    copy while the comment above it described an invariant the query did not
    use. The two were SQL-equivalent, so nothing red; the day the tuple gained
    or renamed a column the query would keep selecting the old set, and the
    moved-schema guard could not see it (the old column is still `description`'d
    — it is the census that stops naming it).
    """
    source = (Path(__file__).resolve().parent.parent
              / "tools" / "turn_parity_census.py").read_text(encoding="utf-8")
    bindings = [node for node in ast.parse(source).body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name)
                        and target.id == "_RECORD_TURNS_SQL"
                        for target in node.targets)]
    assert len(bindings) == 1, (
        f"_RECORD_TURNS_SQL is bound {len(bindings)} times at module level — "
        "a later binding shadows the one derived from _RECORD_TURNS_COLUMNS")
    assert tpc._RECORD_TURNS_SQL.split(" FROM ")[0] == (
        "SELECT " + ", ".join(tpc._RECORD_TURNS_COLUMNS))


def test_a_scope_refusal_is_an_unreadable_side_not_an_empty_record(monkeypatch):
    """A refused tenant scope must exit 3, never read as "the record is empty"."""
    _stub_record(monkeypatch, refuse_scope=True)
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_record_turns("postgresql://x/y", "org-1", "graph-1")
    assert "tenant scope refused" in str(exc.value)


def test_a_moved_record_column_refuses_rather_than_certifying_vacuously(
        monkeypatch):
    """A dropped `node_key` would leave every row unkeyed — an EMPTY comparison.

    The census would then compare a full graph against zero rows and report a
    verdict for a record it never read; the schema gap must be exit 3.
    """
    columns = tuple(c for c in _RECORD_COLUMNS_EXPECTED if c != "node_key")
    _stub_record(monkeypatch, columns=columns,
                 rows=[_record_row_tuple("s1_t0")])
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_record_turns("postgresql://x/y", "org-1", "graph-1")
    assert "node_key" in str(exc.value)


def test_a_record_leg_refusal_reaches_exit_three(monkeypatch):
    """End to end through `main`: the door's refusal is the exit CODE, not a 0."""
    _stub_record(monkeypatch, refuse_scope=True)
    monkeypatch.setattr(tpc, "read_graph_turns",
                        lambda *a, **k: {"nodes": {"s1_t0": _graph_node(
                            "s1_t0", "s1")}})
    assert tpc.main(_argv()) == 3
